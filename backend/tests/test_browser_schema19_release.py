"""Owned local protocol/Git fixtures, never native deployment acceptance."""

from __future__ import annotations

import importlib
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_rolling_release import FixtureBoundary, _candidate_policy_plan

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
reviewed = importlib.import_module("browser_schema19_release")
rolling = reviewed.rolling


@pytest.fixture
def owned(tmp_path):
    boundary = FixtureBoundary()
    plan = reviewed.Schema19Plan(**asdict(boundary.plan))
    # asdict intentionally erases typed nested pins; the real loader restores
    # them from a public metadata-only JSON plan.
    plan = reviewed.load_plan(rolling.canonical(asdict(plan)))
    boundary.plan = plan
    tmp_path.chmod(0o700)
    return plan, boundary, rolling.Journal(tmp_path, plan)


def test_distinct19_plan_and_journal_cannot_be_loaded_or_adopted_by18(owned):
    plan, _, journal = owned
    plan.validate()
    raw = rolling.canonical(asdict(plan))
    assert reviewed.load_plan(raw) == plan
    with pytest.raises(TypeError):
        reviewed.native.load_plan(raw)
    old = rolling.RollingPlan(
        **{
            key: value
            for key, value in asdict(plan).items()
            if key not in {"schema_contract", "database_schema", "prior_sources"}
        }
    )
    assert rolling.sha(asdict(old)) != journal.plan_hash
    assert reviewed.contract.SCHEMA not in reviewed.release.CUTOVER_CHAIN
    assert reviewed.release.CHAIN[-1] == rolling.SCHEMA == "20261015_0018"


def test19_stage_pins_all_feature_flags_false_and_reuses_single_patch_journal(owned):
    plan, boundary, journal = owned
    result = rolling.advance(plan, boundary, journal)
    assert result["rolling_release_complete"] is True
    assert len(boundary.writes) == 6
    for _, body, mask in boundary.writes:
        if mask == "template,traffic":
            env = rolling.environment(body["template"])
            assert all(
                env.get(name, {"value": "false"}).get("value") == "false" for name in reviewed.OFF
            )
            assert all(
                env[name]["value"] == "false" for name in set(reviewed.OFF) - set(rolling.OFF)
            )
    rolling.reconcile(plan, boundary, journal)
    assert len(boundary.writes) == 6


def test19_candidate_policy_pair_roundtrip_retains_disabled_native_controls(owned):
    _plan, boundary, old_journal = owned
    plan = _candidate_policy_plan(boundary)
    boundary.plan = plan
    assert reviewed.load_plan(rolling.canonical(asdict(plan))) == plan
    journal = rolling.Journal(old_journal.directory, plan)
    assert rolling.advance(plan, boundary, journal)["rolling_release_complete"]
    for _, body, mask in boundary.writes:
        if mask == "template,traffic":
            env = rolling.environment(body["template"])
            assert all(env.get(flag, {"value": "false"})["value"] == "false" for flag in reviewed.OFF)
            assert env["HIREWIZ_EXPENSE_POLICY_JSON"]["valueSource"]["secretKeyRef"]["version"] == "2"
    assert all(next(row for row in pin.secrets if row.environment_name == "HIREWIZ_EXPENSE_POLICY_JSON").version == "1"
               for pin in plan.services)


@pytest.mark.parametrize("flag", reviewed.OFF)
def test_enabled19_feature_blocks_stage_before_journal_or_patch(owned, flag):
    plan, boundary, journal = owned
    pin = plan.services[0]
    current = boundary.states[pin.service]
    current["template"]["containers"][0]["env"].append({"name": flag, "value": "true"})
    pin = replace(pin, template_sha256=rolling.template_sha(current["template"], pin.service))
    with pytest.raises(rolling.RollingDenied):
        rolling.stage_body(plan, pin, current)
    assert boundary.writes == []
    assert journal.read("stage." + pin.service + ".started") is None


def test_lost19_patch_acknowledgement_reconciles_without_a_second_send(owned):
    plan, boundary, journal = owned
    boundary.lose_ack = True
    with pytest.raises(RuntimeError):
        rolling.advance(plan, boundary, journal)
    assert len(boundary.writes) == 1
    with pytest.raises(rolling.RollingDenied, match="reconcile"):
        rolling.advance(plan, boundary, journal)
    rolling.reconcile(plan, boundary, journal)
    assert len(boundary.writes) == 1


def test19_source_admission_is_explicit_before_shared_native_image_provenance(monkeypatch):
    boundary = object.__new__(reviewed.Source19Boundary)
    boundary.root = ROOT
    calls = []
    monkeypatch.setattr(reviewed.release.source_guard, "checked_files", lambda *args: ["owned"])
    monkeypatch.setattr(
        boundary, "_verify_source_image_provenance", lambda **kwargs: calls.append(kwargs)
    )
    args = dict(release="a" * 40, image_digest="sha256:" + "b" * 64, build_id="c" * 36, ci_run_id=1)
    monkeypatch.setattr(
        reviewed.release.source_guard, "migration_chain", lambda *args, **kwargs: reviewed.CHAIN
    )
    boundary.verify_source_image(**args)
    assert calls == [args]
    with pytest.raises(reviewed.release.ReleaseDenied):
        reviewed.release.NativeBoundary.verify_source_image(boundary, **args)
    monkeypatch.setattr(
        reviewed.release.source_guard,
        "migration_chain",
        lambda *args, **kwargs: reviewed.release.CHAIN,
    )
    with pytest.raises(rolling.RollingDenied, match="exact_source"):
        boundary.verify_source_image(**args)
    assert calls == [args]


@pytest.mark.parametrize(
    "change", ["18", "wrong_contract", "untyped_prior", "wrong_native_factory"]
)
def test_invalid19_migration_binding_refuses_before_bootstrap_or_sql(owned, monkeypatch, change):
    plan, boundary, journal = owned
    plan = replace(plan, database_schema=reviewed.step.BEFORE)
    calls = []
    monkeypatch.setattr(
        reviewed.docker, "bootstrap_password", lambda *args: calls.append("forbidden")
    )
    if change == "18":
        plan = boundary.plan
    elif change == "wrong_contract":
        plan = replace(plan, schema_contract="unreviewed")
    elif change == "untyped_prior":
        plan = replace(plan, prior_sources=({"release": "a" * 40},))
    with pytest.raises((rolling.RollingDenied, AttributeError)):
        if change != "wrong_native_factory":
            plan.validate()
        reviewed.migration(plan, boundary, journal)
    assert calls == []


def test19_rollback_needs_independent_prior_image_source_and_actual_compatible_git_bytes(
    owned, tmp_path
):
    plan, fixture, _ = owned
    proof = reviewed.PriorSource(
        "b" * 40, "sha256:" + "d" * 64, "11111111-1111-4111-8111-111111111111", 123
    )
    calls = []
    boundary = SimpleNamespace(
        root=tmp_path,
        revision=fixture.revision,
        observer=SimpleNamespace(_verify_source_image_provenance=lambda **args: calls.append(args)),
    )
    with pytest.raises(rolling.RollingDenied, match="native_source_proof"):
        reviewed.verify_schema19_rollback_sources(plan, boundary)
    assert calls == []
    plan = replace(plan, prior_sources=(proof,))
    # A matching version label without actual native image identity is rejected.
    with pytest.raises(rolling.RollingDenied, match="native_image"):
        reviewed.verify_schema19_rollback_sources(plan, boundary)
    assert calls == []


def test_actual_git_same_revision_labels_cannot_replace_compatible_backend_bytes(owned, tmp_path):
    plan, fixture, _ = owned
    repo = tmp_path / "git"
    repo.mkdir()

    def git(*args):
        return (
            subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
            .stdout.decode()
            .strip()
        )

    git("init", "-q")
    git("config", "user.email", "owned@example.test")
    git("config", "user.name", "Owned fixture")
    files = {
        "backend/app/main.py": "# owned compatible runtime\n",
        "backend/scripts/browser_schema19_contract.py": "CONTRACT='hirewiz_browser_schema19_v1'\n",
        "backend/alembic/versions/20261015_0019_browser_typed_controls.py": "revision='20261015_0019'\ndown_revision='20261015_0018'\n",
    }
    for name, body in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    git("add", ".")
    git("commit", "-qm", "owned compatible19")
    previous = git("rev-parse", "HEAD")
    (repo / "README.md").write_text("Owned frontend/config-only release\n")
    git("add", ".")
    git("commit", "-qm", "owned later19")
    commit = git("rev-parse", "HEAD")
    proof = reviewed.PriorSource(previous, plan.image_digest, plan.build_id, plan.ci_run_id)
    pins = tuple(replace(pin, prior_release=previous) for pin in plan.services)
    plan = replace(plan, release=commit, services=pins, prior_sources=(proof,))
    calls = []

    def revision(service, name):
        result = fixture.revision(service, name)
        result["containers"][0]["image"] = plan.image
        return result

    boundary = SimpleNamespace(
        root=repo,
        revision=revision,
        observer=SimpleNamespace(_verify_source_image_provenance=lambda **args: calls.append(args)),
    )
    reviewed.verify_schema19_rollback_sources(plan, boundary)
    assert len(calls) == 1 and calls[0]["release"] == previous
    (repo / "backend/app/main.py").write_text("# incompatible changed backend\n")
    git("add", ".")
    git("commit", "-qm", "owned changed backend")
    plan = replace(plan, release=git("rev-parse", "HEAD"))
    with pytest.raises(rolling.RollingDenied, match="compatibility_unproved"):
        reviewed.verify_schema19_rollback_sources(plan, boundary)


def test_unknown_migration_commit_only_reconciles_and_fresh_global_denial_cannot_record_closed_success(
    owned, monkeypatch
):
    import sqlalchemy

    plan, _, _ = owned
    plan = replace(plan, database_schema=reviewed.step.BEFORE)
    journal = rolling.Journal(owned[2].directory, plan)
    boundary = reviewed.NativeSchema19Boundary(ROOT, reviewed.step.BEFORE)
    state = {"schema": reviewed.step.BEFORE, "ddl": 0, "deny_global": True, "native_phases": []}

    @contextmanager
    def transaction():
        yield

    class Connection:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def begin(self):
            return transaction()

        def exec_driver_sql(self, _sql):
            pass

    class Engine:
        def connect(self):
            return Connection()

        def dispose(self):
            pass

    def global_gate(self, current):
        state["native_phases"].append(current.database_schema)
        if current.database_schema == reviewed.contract.SCHEMA and state["deny_global"]:
            raise rolling.RollingDenied("owned_changed_queue_or_flag")

    monkeypatch.setattr(reviewed.NativeSchema19Boundary, "verify_global", global_gate)
    monkeypatch.setattr(
        reviewed.docker, "bootstrap_password", lambda _deadline: "owned-fixture-only"
    )
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(reviewed.step, "verify_identity", lambda *args: None)
    monkeypatch.setattr(reviewed.step, "marker", lambda *args: state["schema"])
    monkeypatch.setattr(reviewed.step, "retained_digest", lambda *args: "a" * 64)
    monkeypatch.setattr(reviewed.step, "schema_acl", lambda *args: [])
    monkeypatch.setattr(reviewed.step, "drained", lambda *args: None)
    monkeypatch.setattr(reviewed.contract, "verify_shape", lambda *args: None)
    monkeypatch.setattr(reviewed, "verify_permissions", lambda *args: None)

    def ddl(*args, **kwargs):
        state["ddl"] += 1
        state["schema"] = reviewed.contract.SCHEMA
        raise RuntimeError("owned lost commit acknowledgement")

    monkeypatch.setattr(reviewed.step, "upgrade_transaction", ddl)
    with pytest.raises(RuntimeError, match="lost commit"):
        reviewed.migration(plan, boundary, journal)
    assert journal.read("schema19-migration.started") is not None
    assert journal.read("schema19-migration.checked") is None
    with pytest.raises(rolling.RollingDenied, match="reconcile_only"):
        reviewed.migration(plan, boundary, journal)
    with pytest.raises(rolling.RollingDenied, match="changed_queue"):
        reviewed.migration(plan, boundary, journal, reconcile=True)
    assert journal.read("schema19-migration.checked") is None and state["ddl"] == 1
    state["deny_global"] = False
    result = reviewed.migration(plan, boundary, journal, reconcile=True)
    assert result["schema"] == reviewed.contract.SCHEMA and result["queue_admission"] == "closed"
    assert state["ddl"] == 1 and state["native_phases"][-2:] == [reviewed.contract.SCHEMA] * 2
