"""Original journals/rolling/resource primitives over explicit inert ports only."""

from __future__ import annotations

import ast
import copy
import hashlib
import importlib
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from test_schema25_app_rolling_v3 import ClosedV3Fixture
from test_schema25_maintenance import Controls

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
m = importlib.import_module("schema26_schedule_resume")


@pytest.fixture
def ready(tmp_path):
    tmp_path.chmod(0o700)
    case = ClosedV3Fixture(tmp_path / "maintenance")
    controls = Controls()
    for pin in m.schedules(case.maintenance_plan):
        m.maintenance.resource_action(pin, controls, case.maintenance_journal, "pause")
    controls.writes.clear()
    resource_pins = tuple(
        replace(
            pin,
            fingerprint=m.rolling.sha(
                {
                    "state": "PAUSED",
                    "configuration": next(
                        p.configuration_sha256
                        for p in case.maintenance_plan.resources
                        if p.kind == "schedule" and p.name == pin.name
                    ),
                }
            ),
        )
        if pin.kind == "schedule"
        else pin
        for pin in case.plan.resources
    )
    case.plan = replace(case.plan, resources=resource_pins)
    directory = tmp_path / "app"
    directory.mkdir(mode=0o700)
    app_journal = m.rolling.Journal(directory, case.plan)
    assert m.app26.execute("advance", case.plan, case, app_journal)["rolling_release_complete"]
    native = object.__new__(m.app26.NativeApp26Boundary)
    events = []
    # Explicit source/SQL ports; not a provider receipt or native qualification.
    native._verify_global_before_resources = lambda plan: (
        events.append("source-prefix") or case.verify_global(plan)
    )
    native._verify_global_after_resources = lambda plan: events.append("sql26-suffix")
    native.service, native.revision, native.iam = case.service, case.revision, case.iam
    native.operation, native.health = case.operation, case.health
    native._run = None

    def resources(_release):
        return tuple(
            replace(
                pin,
                fingerprint=m.rolling.sha(
                    {
                        "state": controls.get("schedule", pin.name)["state"],
                        "configuration": m.maintenance.configuration(
                            controls.get("schedule", pin.name)
                        ),
                    }
                ),
            )
            if pin.kind == "schedule"
            else pin
            for pin in case.plan.resources
        )

    native._resources = resources
    boundary = object.__new__(m.NativeScheduleResume)
    boundary.plan, boundary.application = case.maintenance_plan, case.plan
    boundary.journal, boundary.application_journal = case.maintenance_journal, app_journal
    boundary.app, boundary.controls = native, controls
    boundary.case, boundary.events = case, events
    return boundary


def test_exact_three_state_only_resumes_keep_all_configuration_and_original_journals(ready):
    before = copy.deepcopy(ready.controls.rows)
    app_bytes = {p.name: p.read_bytes() for p in ready.application_journal.directory.glob("*.json")}
    result = m.execute(ready)
    assert result["status"] == "schema26_schedules_restored"
    assert ready.controls.writes == [
        ("schedule", name, "ENABLED") for name in m.maintenance.SCHEDULES
    ]
    assert (
        result["api_traffic_changes"]
        == result["queue_changes"]
        == result["credential_changes"]
        == 0
    )
    for key, row in ready.controls.rows.items():
        assert m.maintenance.configuration(row) == m.maintenance.configuration(before[key])
        assert row["state"] == ("ENABLED" if key[0] == "schedule" else before[key]["state"])
    assert {
        p.name: p.read_bytes() for p in ready.application_journal.directory.glob("*.json")
    } == app_bytes
    assert ready.journal.read(m.BINDING) == ready.binding()
    assert m.execute(ready, reconcile=True)["status"] == "schema26_schedules_restored"
    assert len(ready.controls.writes) == 3


@pytest.mark.parametrize(
    "fault",
    [
        "pause",
        "configuration",
        "state",
        "queue",
        "source",
        "sql",
        "health",
        "operation",
        "ack",
        "rollback",
    ],
)
def test_immutable_source_26_or_resource_drift_refuses_before_any_resume(ready, fault):
    if fault == "pause":
        (
            ready.journal.directory
            / ("schedule." + m.maintenance.SCHEDULES[0] + ".pause.checked.json")
        ).unlink()
    elif fault == "configuration":
        ready.controls.rows["schedule", m.maintenance.SCHEDULES[0]]["config"] = {"changed": True}
    elif fault == "state":
        ready.controls.rows["schedule", m.maintenance.SCHEDULES[0]]["state"] = "ENABLED"
    elif fault == "queue":
        original = ready.app._resources
        ready.app._resources = lambda value: (
            replace(original(value)[0], fingerprint="f" * 64),
            *original(value)[1:],
        )
    elif fault in {"source", "sql", "health"}:

        def fail(*_a, **_k):
            raise m.rolling.RollingDenied("synthetic reviewed boundary refusal")

        if fault == "source":
            ready.app._verify_global_before_resources = fail
        elif fault == "sql":
            ready.app._verify_global_after_resources = fail
        else:
            ready.app.health = fail
    elif fault == "operation":
        ready.app.operation = lambda name: {"name": name, "done": False}
    elif fault == "ack":
        (
            ready.application_journal.directory / ("promote." + m.app26.SERVICES[0] + ".ack.json")
        ).unlink()
    else:
        ready.application_journal.record(m.app26.historical.ROLLBACK_INTENT, {"synthetic": True})
    with pytest.raises(m.rolling.RollingDenied):
        m.execute(ready)
    assert ready.controls.writes == []
    assert ready.journal.read(m.BINDING) is None


def test_unknown_post_effect_is_only_reconciled_then_unsent_schedules_wait(ready):
    ready.controls.lose_ack = True
    with pytest.raises(RuntimeError):
        m.execute(ready)
    assert len(ready.controls.writes) == 1
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        m.execute(ready)
    result = m.execute(ready, reconcile=True)
    assert result["remaining_unsent"] is True and len(ready.controls.writes) == 1
    assert m.execute(ready)["status"] == "schema26_schedules_restored"
    assert len(ready.controls.writes) == 3


def test_unknown_post_without_observed_effect_never_resends(ready):
    def unavailable(kind, name, state):
        ready.controls.writes.append((kind, name, state))
        raise OSError("synthetic unknown before effect")

    ready.controls.set_state = unavailable
    with pytest.raises(OSError):
        m.execute(ready)
    with pytest.raises(m.rolling.RollingDenied, match="outcome_unverified"):
        m.execute(ready, reconcile=True)
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        m.execute(ready)
    assert len(ready.controls.writes) == 1


@pytest.mark.parametrize("point", ["binding", "checked"])
def test_local_ack_loss_retains_original_markers_and_never_repeats_an_effect(ready, point):
    original = ready.journal.record

    def unknown(name, value):
        original(name, value)
        if (point == "binding" and name == m.BINDING) or (
            point == "checked" and name.endswith(".restore.checked")
        ):
            raise OSError("synthetic local ACK loss")

    ready.journal.record = unknown
    with pytest.raises(OSError):
        m.execute(ready)
    ready.journal.record = original
    count = len(ready.controls.writes)
    m.execute(ready, reconcile=True)
    assert len(ready.controls.writes) == count
    m.execute(ready)
    assert len(ready.controls.writes) == 3


def test_existing_restore_cannot_be_rebound_to_different_completed_application(ready):
    ready.controls.lose_ack = True
    with pytest.raises(RuntimeError):
        m.execute(ready)
    before = list(ready.controls.writes)
    ready.application = replace(ready.application, compatibility_sha256="f" * 64)
    with pytest.raises(m.rolling.RollingDenied, match="binding_changed"):
        m.execute(ready, reconcile=True)
    assert ready.controls.writes == before


def test_fresh_reconcile_never_creates_binding_or_starts_work(ready):
    with pytest.raises(m.rolling.RollingDenied, match="binding_required"):
        m.execute(ready, reconcile=True)
    assert ready.journal.read(m.BINDING) is None and ready.controls.writes == []


def test_foreign_preexisting_stock_restore_cannot_be_adopted_without_bound_application(ready):
    ready.journal.record(
        "schedule." + m.maintenance.SCHEDULES[0] + ".restore.started",
        {"target": "ENABLED", "replay_authorized": False},
    )
    with pytest.raises(m.rolling.RollingDenied, match="binding_required"):
        m.execute(ready)
    assert ready.controls.writes == []


def test_fresh_global_checks_happen_before_each_send_and_stop_partial_progress(ready):
    original = ready.app._verify_global_after_resources

    def stop(plan):
        if ready.controls.writes:
            raise m.rolling.RollingDenied("synthetic newly changed SQL26")
        original(plan)

    ready.app._verify_global_after_resources = stop
    with pytest.raises(m.rolling.RollingDenied):
        m.execute(ready)
    assert len(ready.controls.writes) == 1
    assert ready.journal.read("schedule." + m.maintenance.SCHEDULES[1] + ".restore.started") is None


def test_both_original_journal_locks_exclude_parallel_controller(ready):
    with ready.application_journal.locked():
        with pytest.raises(BlockingIOError):
            m.execute(ready)
    with ready.journal.locked():
        with pytest.raises(BlockingIOError):
            m.execute(ready)
    assert ready.controls.writes == []


def test_original_v3_global_checks_are_ast_exact_when_tiny_factoring_is_inlined():
    tree = ast.parse((ROOT / "infra/gcp/schema26_app_rolling_v3.py").read_bytes())
    cls = next(
        n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "NativeApp26Boundary"
    )
    methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
    global_check = copy.deepcopy(methods["verify_global"])
    expanded = []
    for statement in global_check.body:
        if (
            isinstance(statement, ast.Expr)
            and isinstance(statement.value, ast.Call)
            and isinstance(statement.value.func, ast.Attribute)
            and statement.value.func.attr
            in {"_verify_global_before_resources", "_verify_global_after_resources"}
        ):
            expanded.extend(copy.deepcopy(methods[statement.value.func.attr].body))
        else:
            expanded.append(statement)
    global_check.body = expanded
    assert (
        hashlib.sha256(ast.dump(global_check, include_attributes=False).encode()).hexdigest()
        == "1a2a12d54e4e87fe68b8f4bb0c11405bb7edaa369bca2ec70b05026ccf999c22"
    )
