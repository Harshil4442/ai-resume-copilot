"""Prospective26 source/transport ports; never deployment or SQL acceptance."""
from __future__ import annotations

import copy
import importlib
import json
import os
import subprocess
import sys
from dataclasses import asdict, fields, replace
from pathlib import Path

import pytest
from test_schema25_app_rolling_release import AppFixture

from scripts import schema25_app_release_contract_v3 as contract

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
app = importlib.import_module("schema26_app_rolling_v3")
rolling = app.rolling


class V3Fixture(AppFixture):
    def __init__(self):
        super().__init__()
        pins = []
        for pin in self.plan.services:
            state = self.states[pin.service]
            template = state["template"]
            template["labels"]["hirewiz-source"] = app.PRIOR.release
            template["containers"][0]["image"] = self.plan.image.split("@")[0] + "@" + app.PRIOR.image_digest
            rolling.environment(template)["APP_RELEASE"]["value"] = app.PRIOR.release
            revision = {**copy.deepcopy(template), "name": state["latestReadyRevision"],
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}]}
            self.revisions[(pin.service, pin.prior_revision)] = revision
            pins.append(replace(pin, prior_release=app.PRIOR.release,
                template_sha256=rolling.template_sha(template, pin.service),
                revision_sha256=rolling.sha({key: revision.get(key) for key in pin.revision_keys})))
        values = {field.name: getattr(self.plan, field.name) for field in fields(app.historical.App25Plan)
            if field.name not in {"schema_contract", "database_schema", "prior_source", "services", "reachable_revisions"}}
        self.plan = app.App26V3Plan(**values, services=tuple(pins), reachable_revisions=tuple(
            app.historical.reachable_pin(pin.service, pin.prior_revision, self.revisions[(pin.service, pin.prior_revision)],
                prior_source=app.PRIOR) for pin in pins))
        self.targets = {pin.service: app.candidate_template(self.plan, pin, self.service(pin.service)) for pin in pins}
        self.plan.validate()


@pytest.fixture
def context(tmp_path):
    tmp_path.chmod(0o700)
    boundary = V3Fixture()
    return boundary, rolling.Journal(tmp_path, boundary.plan)


def test_complete_frozen_app_and_disjoint_schema26_source_is_required():
    prior = app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    assert contract.source_fingerprint(ROOT, prior)
    assert app.PRIOR.release == contract.PRIOR_RELEASE
    assert app.PRIOR.image_digest.startswith("sha256:df4618fa")


def test_standalone_source_entrypoint_uses_only_the_trusted_checkout(tmp_path):
    # -I removes pytest's repo-root injection and all PYTHONPATH authority.
    # A hostile CWD must not satisfy an accidentally missing package bootstrap.
    for name in ("infra.py", "scripts.py", "rolling_release.py"):
        (tmp_path / name).write_text("raise AssertionError('Untrusted CWD imported')\n")
    script = ROOT / "infra/gcp/schema26_app_rolling_v3.py"
    program = """
import runpy, socket, sys
from sqlalchemy.engine import Engine
def deny(*args, **kwargs):
    raise AssertionError('No database/network in source-only entrypoint')
for name in ('connect', 'connect_ex', 'bind', 'listen'):
    setattr(socket.socket, name, deny)
socket.getaddrinfo = socket.create_connection = Engine.connect = deny
sys.argv = [SCRIPT, 'source']
runpy.run_path(SCRIPT, run_name='__main__')
""".replace("SCRIPT", repr(str(script)))
    result = subprocess.run([sys.executable, "-I", "-B", "-"], cwd=tmp_path, input=program,
        text=True, capture_output=True, timeout=30, check=False,
        env={"PATH": os.defpath, "APP_ENV": "test", "DATABASE_URL": "sqlite://",
            "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(tmp_path)})
    assert result.returncode == 0, result.stderr[-2048:]
    assert not result.stderr
    assert json.loads(result.stdout) == {
        "source_sha256": contract.source_fingerprint(ROOT, app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)),
        "serving_prior_observed": False, "schema26_applied": False,
        "migration_authority": False, "rollout_authority": False,
    }


@pytest.mark.parametrize("name", ["backend/tests/test_schema25_app_rolling_release.py",
    "backend/tests/test_rolling_release.py", "backend/tests/test_document_worker_deployment.py",
    "frontend/app/api/browser-pairing/device/[operation]/route.ts",
    "browser-companion/extension/pairing-transport.js", "browser-companion/extension/pairing-runtime.js",
    "browser-companion/tests/pairing-readiness-fixture.js", "backend/tests/browser_paired_assistance_chrome_bridge.py",
    "browser-companion/scripts/package-reviewed.py", "browser-companion/package-lock.json",
    "frontend/components/BrowserAssistanceReview.tsx", "frontend/public/downloads/hirewiz-browser-companion-0.8.6.zip",
    "frontend/lib/cn.ts", "browser-companion/tests/browser.test.js",
    "browser-companion/tests/assistance-browser.test.js", "backend/tests/browser_submit_owned.py"])
def test_changed_rolling_proof_helper_cannot_reuse_a_previous_plan(monkeypatch, name):
    original = Path.read_bytes
    prior = app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE)
    baseline = contract.source_fingerprint(ROOT, prior)
    case = V3Fixture()
    plan = replace(case.plan, compatibility_sha256=baseline)
    native = object.__new__(app.NativeApp26Boundary)
    native.root = ROOT
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# unreviewed proof helper\n"
        if path == ROOT / name else original(path))
    assert contract.source_fingerprint(ROOT, prior) != baseline
    # No observer or command seam is installed: source drift must stop first.
    with pytest.raises(rolling.RollingDenied, match="compatibility_source_changed"):
        native._compatibility(plan)


@pytest.mark.parametrize("name", ["backend/app/domains/career/evidence_write_fence.py",
    "backend/app/domains/career/evidence_fence_definition.py", "backend/app/domains/credits/reconciliation_schema26.py",
    "backend/app/services/provider_input_guard.py"])
def test_changed_guard_or_successor_cannot_reuse_full_app_pin(monkeypatch, name):
    original = Path.read_bytes
    monkeypatch.setattr(Path, "read_bytes", lambda path: original(path) + b"\n# unreviewed code\n"
        if path == ROOT / name else original(path))
    with pytest.raises(Exception, match="source_changed|candidate_app_unqualified"):
        contract.source_fingerprint(ROOT, app.initial.prior_app_files(ROOT, contract.PRIOR_RELEASE))


def test_distinct_plan_never_adopts25_or_migration_authority(context):
    boundary, _ = context
    raw = rolling.canonical(asdict(boundary.plan))
    assert app.load(raw) == boundary.plan
    for loader in (app.historical.load, app.historical.load_v2, app.initial.load_plan):
        with pytest.raises(rolling.RollingDenied):
            loader(raw)
    old = asdict(boundary.plan) | {"schema_contract": app.historical.contract_v2.CONTRACT,
        "database_schema": "20261028_0025"}
    with pytest.raises(rolling.RollingDenied, match="distinct_plan_required"):
        app.load(rolling.canonical(old))
    assert not boundary.writes


def test_stage_advance_and_restricted_old_source_rollback_keep_original_journal(context):
    boundary, journal = context
    assert app.execute("stage", boundary.plan, boundary, journal)["status"] == "schema26_app_staged_zero_traffic"
    assert app.execute("advance", boundary.plan, boundary, journal)["status"] == "schema26_app_release_observed"
    result = app.execute("rollback", boundary.plan, boundary, journal)
    assert result["status"] == "schema26_app_prior_traffic_restored_writes_restricted"
    assert result["evidence_fence_required"] is True and result["old_source_evidence_writes_authorized"] is False
    assert result["migrations"] == result["resource_access_changes"] == 0
    writes = len(boundary.writes)
    assert app.execute("reconcile", boundary.plan, boundary, journal)["replay_authorized"] is False
    assert len(boundary.writes) == writes


@pytest.mark.parametrize("mode", ["pause", "lose_ack"])
def test_unknown_stage_never_replays_the_original_write(context, mode):
    boundary, journal = context
    setattr(boundary, mode, True)
    try:
        result = app.execute("stage", boundary.plan, boundary, journal)
        assert result["rolling_release_complete"] is False
    except RuntimeError:
        assert mode == "lose_ack"
    count = len(boundary.writes)
    if mode == "lose_ack":
        with pytest.raises(rolling.RollingDenied):
            app.execute("stage", boundary.plan, boundary, journal)
    else:
        result = app.execute("stage", boundary.plan, boundary, journal)
        assert result["status"] == "schema26_app_same_operation_pending"
    assert len(boundary.writes) == count


@pytest.mark.parametrize("flag", contract.STAGE5_FLAGS)
def test_stage5_activation_is_not_hidden_in_a_v3_template(context, flag):
    boundary, _ = context
    pin = boundary.plan.services[0]
    current = boundary.service(pin.service)
    current["template"]["containers"][0]["env"].append({"name": flag, "value": "true"})
    with pytest.raises(rolling.RollingDenied, match="stage5_activation_outside_release"):
        app.candidate_template(boundary.plan, pin, current)


def test_new_privacy_mail_is_closed_without_disabling_the_authorized_password_reset(context):
    boundary, _ = context
    pin = boundary.plan.services[0]
    current = boundary.service(pin.service)
    app.candidate_template(boundary.plan, pin, current)
    current["template"]["containers"][0]["env"].append({"name": contract.PRIVACY_FLAG, "value": "true"})
    with pytest.raises(rolling.RollingDenied, match="privacy_mail_activation_outside_release"):
        app.candidate_template(boundary.plan, pin, current)


@pytest.mark.parametrize("wrong", ["prior_source", "ready_zero_traffic"])
def test_native_capture_requires_actual_e23_serving_not_prospective_pins(context, wrong):
    boundary, _ = context
    pin = boundary.plan.services[0]
    native = object.__new__(app.NativeApp26Boundary)
    native.service, native.revision, native.iam = boundary.service, boundary.revision, boundary.iam
    native._reference = lambda _name, version: version
    assert native._pin(pin.service).prior_release == contract.PRIOR_RELEASE
    if wrong == "prior_source":
        rolling.environment(boundary.states[pin.service]["template"])["APP_RELEASE"]["value"] = app.historical.PRIOR_V2.release
    else:
        boundary.states[pin.service]["latestCreatedRevision"] = rolling.PARENT + "/services/" + pin.service + "/revisions/" + pin.service + "-new-ready-zero"
    with pytest.raises(rolling.RollingDenied):
        native._pin(pin.service)
    assert not boundary.writes


class ClosedV3Fixture(V3Fixture):
    """Real stock close algorithm over synthetic ports, never native proof."""
    def __init__(self, directory):
        from test_schema25_maintenance import Controls

        super().__init__()
        for index, row in enumerate(self.revisions.values()):
            row["uid"] = f"10000000-0000-4000-8000-{index:012d}"
        self.maintenance_plan = app.maintenance.capture(self, Controls(), "e" * 32)
        directory.mkdir(mode=0o700)
        self.maintenance_journal = rolling.Journal(directory, self.maintenance_plan)
        assert app.maintenance.service_action(self.maintenance_plan, self.maintenance_plan.services[0],
            self, self.maintenance_journal, "close")
        self.baseline = app.capture_closed_api_baseline(self.maintenance_plan, self.maintenance_journal, self)
        native = object.__new__(app.NativeApp26Boundary)
        native.service, native.revision, native.iam = self.service, self.revision, self.iam
        native._reference = lambda _name, version: version
        api = native._pin(app.SERVICES[0], self.baseline)
        pins = (api, *self.plan.services[1:])
        self.plan = replace(self.plan, services=pins, maintenance_baseline=self.baseline,
            reachable_revisions=tuple(app.historical.reachable_pin(pin.service, row["revision"],
                self.revision(pin.service, row["revision"]), self.revision(pin.service, pin.prior_revision),
                prior_source=app.PRIOR) for pin in pins for row in pin.traffic))
        self.targets = {pin.service: app.candidate_template(self.plan, pin, self.service(pin.service)) for pin in pins}
        self.rollout_start = len(self.writes)

    def patch(self, service, body, mask):
        result = super().patch(service, body, mask)
        if "template" in body:
            name = rolling.short_revision(body["template"]["revision"], service)
            self.revisions[(service, name)]["uid"] = f"20000000-0000-4000-8000-{len(self.writes):012d}"
        return result

    def verify_global(self, plan):
        plan.validate()
        if plan.maintenance_baseline is not None:
            app.verify_closed_api_baseline(plan.maintenance_baseline, self.maintenance_plan,
                self.maintenance_journal, self)
        for pin in plan.services:
            template = self.states[pin.service]["template"]
            baseline = plan.maintenance_baseline if rolling.environment(template)["APP_RELEASE"]["value"] == app.PRIOR.release else None
            app.observed_flags(template, pin.service, baseline)
            app.historical.current_phase(plan, pin, self.states[pin.service], self.targets[pin.service])


@pytest.fixture
def closed_context(tmp_path):
    tmp_path.chmod(0o700)
    case = ClosedV3Fixture(tmp_path / "maintenance")
    rollout = tmp_path / "rollout"
    rollout.mkdir(mode=0o700)
    return case, rolling.Journal(rollout, case.plan)


def test_closed_baseline_load_roundtrip_retains_original_stock_lineage_without_raw_env(closed_context):
    case, _ = closed_context
    assert app.load(rolling.canonical(asdict(case.plan))) == case.plan
    assert app.capture_closed_api_baseline(case.maintenance_plan, case.maintenance_journal, case) == case.baseline
    raw = rolling.canonical(asdict(case.baseline))
    assert b"synthetic-sensitive" not in raw and b"DATABASE_URL" not in raw
    for loader in (app.historical.load, app.historical.load_v2, app.initial.load_plan):
        with pytest.raises(rolling.RollingDenied):
            loader(rolling.canonical(asdict(case.plan)))


def test_closed_baseline_targets_change_only_original_api_pilot_flags_and_qualified_source(closed_context):
    case, _ = closed_context
    api = case.plan.services[0]
    closed = case.service(api.service)
    target = app.candidate_template(case.plan, api, closed)
    expected = app.rolling.stage_body(case.plan, api, closed)["template"]
    env = rolling.environment(expected)
    for flag, value in case.baseline.original_pilot_flags:
        env[flag] = {"name": flag, "value": value}
    expected["containers"][0]["env"] = list(env.values())
    assert target == expected
    assert all(rolling.environment(target)[flag]["value"] == "false" for flag in app.CLOSED_FLAGS)
    assert all(rolling.environment(closed["template"])[flag]["value"] == "false" for flag in app.initial.OFF)
    for pin in case.plan.services[1:]:
        assert app.candidate_template(case.plan, pin, case.service(pin.service)) == case.targets[pin.service]
    with pytest.raises(rolling.RollingDenied, match="maintenance_plan_pin_changed"):
        replace(case.plan, release=app.PRIOR.release).validate()


@pytest.mark.parametrize("phase", ["stage", "advance"])
def test_closed_api_stage_sends_exact_reopened_target_and_journals_actual_request(closed_context, phase):
    case, journal = closed_context
    assert app.execute(phase, case.plan, case, journal)["status"] in {
        "schema26_app_staged_zero_traffic", "schema26_app_release_observed"}
    service, body, mask = case.writes[case.rollout_start]
    assert service == app.SERVICES[0] and mask == "template,traffic"
    assert body["template"] == case.targets[service]
    marker = journal.read("stage." + service + ".started")
    assert marker["request_sha256"] == rolling.sha(body)
    assert marker["template_sha256"] == rolling.template_sha(body["template"], service)
    assert journal.read("stage." + service + ".ack")["request_sha256"] == marker["request_sha256"]
    assert journal.read("stage." + service + ".checked")["operation_ack_retained"] is True
    count = len(case.writes)
    app.execute("reconcile", case.plan, case, journal)
    assert len(case.writes) == count


def test_closed_baseline_immutable_lineage_remains_verifiable_after_candidate_and_rollback(closed_context):
    case, journal = closed_context
    assert app.execute("advance", case.plan, case, journal)["rolling_release_complete"]
    app.verify_closed_api_baseline(case.baseline, case.maintenance_plan, case.maintenance_journal, case)
    result = app.execute("rollback", case.plan, case, journal)
    assert result["status"] == "schema26_app_prior_traffic_restored_writes_restricted"
    api = case.plan.services[0]
    active = next(row["revision"] for row in rolling.pinned_traffic(api.service, case.service(api.service)) if row.get("percent"))
    assert active == case.baseline.closed_revision
    assert all(rolling.environment(case.revision(api.service, active))[flag]["value"] == "false"
        for flag in app.initial.OFF)
    assert all(mask == "traffic" for _, _, mask in case.writes[-3:])
    assert not any(row["revision"] == case.maintenance_plan.services[0].prior_revision
        for row in rolling.pinned_traffic(api.service, case.service(api.service)))


def test_closed_api_stage_lost_ack_never_replays_and_only_reconciles_original_metadata(closed_context):
    case, journal = closed_context
    case.lose_ack = True
    with pytest.raises(RuntimeError):
        app.execute("stage", case.plan, case, journal)
    count = len(case.writes)
    with pytest.raises(rolling.RollingDenied, match="unknown_reconcile_only"):
        app.execute("stage", case.plan, case, journal)
    assert len(case.writes) == count
    app.execute("reconcile", case.plan, case, journal)
    assert len(case.writes) == count
    assert journal.read("stage." + app.SERVICES[0] + ".checked")["operation_ack_retained"] is False


@pytest.mark.parametrize("fault", ["missing_ack", "unknown_operation", "failed_operation", "restore", "worker_close",
    "closed_uid", "closed_template", "original_flags", "checked_generation"])
def test_closed_baseline_refuses_unknown_or_changed_original_lineage(closed_context, fault):
    case, _ = closed_context
    marker = "service." + app.SERVICES[0] + ".close"
    ack = case.maintenance_journal.read(marker + ".acknowledged")
    if fault == "missing_ack":
        (case.maintenance_journal.directory / (marker + ".acknowledged.json")).unlink()
    elif fault == "unknown_operation":
        case.operations[ack["operation"]]["done"] = False
    elif fault == "failed_operation":
        case.operations[ack["operation"]]["error"] = {"code": 9}
    elif fault in {"restore", "worker_close"}:
        name = "service." + (app.SERVICES[0] if fault == "restore" else app.SERVICES[1])
        case.maintenance_journal.record(name + (".restore.started" if fault == "restore" else ".close.started"), {})
    elif fault == "closed_uid":
        case.revisions[(app.SERVICES[0], case.baseline.closed_revision)]["uid"] = "30000000-0000-4000-8000-000000000001"
    elif fault == "closed_template":
        case.revisions[(app.SERVICES[0], case.baseline.closed_revision)]["maxInstanceRequestConcurrency"] = 99
    elif fault == "original_flags":
        pin = case.maintenance_plan.services[0]
        rolling.environment(case.revisions[(pin.name, pin.prior_revision)])["BROWSER_ASSISTANCE_ENABLED"]["value"] = "false"
    else:
        path = case.maintenance_journal.directory / (marker + ".checked.json")
        value = json.loads(path.read_text())
        value["generation"] += 1
        path.write_text(json.dumps(value))
        path.chmod(0o600)
    with pytest.raises(rolling.RollingDenied):
        app.verify_closed_api_baseline(case.baseline, case.maintenance_plan, case.maintenance_journal, case)


def test_closed_api_pin_refuses_live_preimage_or_alias_drift(closed_context):
    case, _ = closed_context
    native = object.__new__(app.NativeApp26Boundary)
    native.service, native.revision, native.iam = case.service, case.revision, case.iam
    native._reference = lambda _name, version: version
    case.states[app.SERVICES[0]]["etag"] = "unexpected-etag"
    with pytest.raises(rolling.RollingDenied, match="closed_preimage_changed"):
        native._pin(app.SERVICES[0], case.baseline)
    case.states[app.SERVICES[0]]["etag"] = case.baseline.api_etag
    case.states[app.SERVICES[0]]["trafficStatuses"].append({"tag": "old-open",
        "revision": case.maintenance_plan.services[0].prior_revision})
    with pytest.raises(rolling.RollingDenied, match="open_alias_changed"):
        native._pin(app.SERVICES[0], case.baseline)


@pytest.mark.parametrize("field", ["api_generation", "closed_template_sha256", "closed_revision", "original_pilot_flags"])
def test_loaded_closed_baseline_cannot_change_the_pinned_plan_or_expand_flags(closed_context, field):
    case, _ = closed_context
    value = asdict(case.plan)
    replacements = {"api_generation": True, "closed_template_sha256": "f" * 64,
        "closed_revision": app.SERVICES[0] + "-other", "original_pilot_flags": [("OPTIONAL_AI_GENERATION_ENABLED", "true")]}
    value["maintenance_baseline"][field] = replacements[field]
    with pytest.raises(rolling.RollingDenied):
        app.load(rolling.canonical(value))


def test_closed_capture_refuses_an_unrelated_worker_change(closed_context):
    case, _ = closed_context
    case.states[app.SERVICES[1]]["template"]["maxInstanceRequestConcurrency"] = 99
    with pytest.raises(rolling.RollingDenied, match="maintenance_worker_changed"):
        app.capture_closed_api_baseline(case.maintenance_plan, case.maintenance_journal, case)


def test_closed_api_pending_operation_and_rollback_intent_never_start_another_stage(closed_context):
    case, journal = closed_context
    case.pause = True
    assert app.execute("advance", case.plan, case, journal)["status"] == "schema26_app_same_operation_pending"
    count = len(case.writes)
    assert app.execute("advance", case.plan, case, journal)["status"] == "schema26_app_same_operation_pending"
    assert len(case.writes) == count
    journal.record(app.historical.ROLLBACK_INTENT, {"forward_continuation_authorized": False})
    with pytest.raises(rolling.RollingDenied, match="explicit_rollback_in_progress"):
        app.execute("advance", case.plan, case, journal)
    assert len(case.writes) == count


def test_native_closed_mode_requires_explicit_original_custody_before_any_client(monkeypatch):
    monkeypatch.setattr(app.historical.NativeApp25Boundary, "__init__",
        lambda *_a, **_k: pytest.fail("No native client without explicit custody"))
    with pytest.raises(rolling.RollingDenied, match="maintenance_custody_required"):
        app.NativeApp26Boundary(ROOT)
