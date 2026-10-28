"""Finite synthetic native contracts; no live pause, migration or provider proof."""
import copy
import importlib
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_rolling_release import FixtureBoundary

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
m = importlib.import_module("schema25_maintenance")


class Controls:
    def __init__(self):
        self.rows = {(kind, name): {"name": m.rolling.PARENT + m.RESOURCE_PATHS[kind][1] + name,
                     "state": "RUNNING" if kind == "queue" else "ENABLED", "config": {"private": "inert-secret"}}
                     for kind, names in m.RESOURCE_NAMES.items() for name in names}
        self.rows[("queue", m.QUEUES[-1])]["state"] = "PAUSED"
        self.writes, self.lose_ack = [], False

    def get(self, kind, name):
        return copy.deepcopy(self.rows[(kind, name)])

    def set_state(self, kind, name, state):
        self.writes.append((kind, name, state))
        self.rows[(kind, name)]["state"] = state
        if self.lose_ack:
            self.lose_ack = False
            raise RuntimeError("inert unknown POST")


def context(tmp_path):
    run, controls = FixtureBoundary(), Controls()
    for service, state in run.states.items():
        original = run.plan.pin(service).prior_revision
        for row in [state["template"], run.revisions[(service, original)]]:
            row["containers"][0]["env"].append({"name": "OPTIONAL_AI_GENERATION_ENABLED", "value": "true"})
    plan = m.capture(run, controls, "d" * 32)
    tmp_path.chmod(0o700)
    journal = m.rolling.Journal(tmp_path, plan)
    drains = []

    def drain():
        drains.append(True)

    return run, controls, plan, journal, drains, drain


def settling_revision_context(tmp_path):
    """Capture a real-shaped Revision before provider-owned status settles."""
    run, controls, _plan, _journal, drains, drain = context(tmp_path)
    service = m.SERVICES[0]
    original = run.plan.pin(service).prior_revision
    revision = run.revisions[(service, original)]
    revision.update({
        "service": service,
        "uid": "e1ad9c8d-4314-4d9d-a306-256d0c2fbadb",
        "generation": "1",
        "observedGeneration": "1",
        "createTime": "2026-10-28T22:44:04Z",
        "updateTime": "2026-10-28T22:44:04Z",
        "etag": "early-status-etag",
        "launchStage": "GA",
    })
    plan = m.capture(run, controls, "e" * 32)
    journal = m.rolling.Journal(tmp_path, plan)
    return (run, controls, plan, journal, drains, drain), revision


def apply(ctx, phase, **kwargs):
    run, controls, plan, journal, _drains, drain = ctx
    return m.execute(plan, run, controls, journal, phase, drain, **kwargs)


def close(ctx):
    for phase in ("pause-schedules", "close-api", "close-workers", "pause-queues"):
        assert apply(ctx, phase)["status"] == "maintenance_phase_observed"


def test_close_and_abort_restore_preserve_all_flags_images_config_and_original_states(tmp_path):
    ctx = context(tmp_path)
    run, controls, plan, journal, drains, _ = ctx
    originals = copy.deepcopy(run.states)
    original_resources = copy.deepcopy(controls.rows)
    assert m.load(m.rolling.canonical(asdict(plan))) == plan
    close(ctx)
    assert len(drains) == 2
    assert [s for s, _, _ in run.writes] == list(m.SERVICES)
    for pin in plan.services:
        state = run.states[pin.name]
        env = m.rolling.environment(state["template"])
        assert all(env[flag] == {"name": flag, "value": "false"} for flag in m.FLAGS)
        assert state["template"]["containers"][0]["image"] == pin.image
        assert env["RAZORPAY_KEY_SECRET"]["value"] == "synthetic-sensitive-literal"
    for phase in ("restore-services", "restore-queues", "restore-schedules"):
        apply(ctx, phase)
    assert [s for s, _, _ in run.writes[3:]] == list(reversed(m.SERVICES))
    assert controls.rows == original_resources
    assert not any(name == m.QUEUES[-1] for _, name, _ in controls.writes)
    for pin in plan.services:
        assert m.coordinator.template_hash(run.states[pin.name]["template"]) == m.coordinator.template_hash(originals[pin.name]["template"])
        assert m.rolling.pinned_traffic(pin.name, run.states[pin.name]) == list(pin.traffic)
    assert all("synthetic-sensitive" not in p.read_text() and "inert-secret" not in p.read_text() for p in journal.directory.glob("*.json"))


def test_workers_keep_running_until_existing_work_and_coordinator_genuinely_drain(tmp_path):
    ctx = context(tmp_path)
    run, controls, plan, journal, _, _ = ctx
    apply(ctx, "pause-schedules")
    apply(ctx, "close-api")

    def not_drained():
        raise RuntimeError("existing accepted work or coordinator still active")

    with pytest.raises(RuntimeError, match="accepted work"):
        m.execute(plan, run, controls, journal, "close-workers", not_drained)
    assert len(run.writes) == 1
    assert all(m.rolling.environment(run.states[s]["template"])["OPTIONAL_AI_GENERATION_ENABLED"]["value"] == "true" for s in m.SERVICES[1:])
    assert all(controls.rows[("queue", q)]["state"] == "RUNNING" for q in m.QUEUES[:-1])


@pytest.mark.parametrize("phase", ["close-api", "close-workers", "pause-queues"])
def test_phase_order_cannot_bypass_producer_shutdown(tmp_path, phase):
    ctx = context(tmp_path)
    with pytest.raises(m.rolling.RollingDenied):
        apply(ctx, phase)
    assert ctx[0].writes == [] and ctx[1].writes == []


def test_unknown_scheduler_post_reconciles_without_any_new_post(tmp_path):
    ctx = context(tmp_path)
    controls = ctx[1]
    controls.lose_ack = True
    with pytest.raises(RuntimeError, match="unknown POST"):
        apply(ctx, "pause-schedules")
    assert len(controls.writes) == 1
    with pytest.raises(m.rolling.RollingDenied, match="started_reconcile_only"):
        apply(ctx, "pause-schedules")
    assert apply(ctx, "pause-schedules", reconcile=True)["status"] == "maintenance_phase_partial"
    assert len(controls.writes) == 1
    apply(ctx, "pause-schedules")
    assert len(controls.writes) == 3


def test_reconcile_never_begins_a_fresh_phase(tmp_path):
    ctx = context(tmp_path)
    assert apply(ctx, "pause-schedules", reconcile=True)["status"] == "maintenance_phase_partial"
    assert ctx[1].writes == [] and ctx[0].writes == []


@pytest.mark.parametrize("change", ["image", "unrelated_env", "generation", "tag"])
def test_conflicting_service_change_stops_before_patch(tmp_path, change):
    ctx = context(tmp_path)
    run = ctx[0]
    apply(ctx, "pause-schedules")
    state = run.states[m.SERVICES[0]]
    if change == "image":
        state["template"]["containers"][0]["image"] = state["template"]["containers"][0]["image"][:-64] + "e" * 64
    elif change == "unrelated_env":
        m.rolling.environment(state["template"])["RAZORPAY_KEY_SECRET"]["value"] = "new-inert-value"
    elif change == "generation":
        state["generation"] = state["observedGeneration"] = "43"
    else:
        state["trafficStatuses"][0]["tag"] = "concurrent"
    with pytest.raises(m.rolling.RollingDenied, match="preimage_changed"):
        apply(ctx, "close-api")
    assert run.writes == []


def test_partial_worker_pause_retains_first_action_and_does_not_repeat_it(tmp_path):
    ctx = context(tmp_path)
    run = ctx[0]
    apply(ctx, "pause-schedules")
    apply(ctx, "close-api")
    run.lose_ack = True
    with pytest.raises(RuntimeError):
        apply(ctx, "close-workers")
    assert len(run.writes) == 2
    assert apply(ctx, "close-workers", reconcile=True)["status"] == "maintenance_phase_partial"
    assert len(run.writes) == 2
    apply(ctx, "close-workers")
    assert len(run.writes) == 3


def test_pending_same_operation_is_polled_without_new_patch(tmp_path):
    ctx = context(tmp_path)
    run = ctx[0]
    apply(ctx, "pause-schedules")
    run.pause = True
    assert apply(ctx, "close-api")["status"] == "maintenance_operation_pending"
    assert apply(ctx, "close-api", reconcile=True)["status"] == "maintenance_operation_pending"
    assert len(run.writes) == 1
    next(iter(run.operations.values()))["done"] = True
    assert apply(ctx, "close-api", reconcile=True)["status"] == "maintenance_phase_observed"


def test_prior_revision_status_settles_after_capture_and_pending_close_reconciles(tmp_path):
    ctx, revision = settling_revision_context(tmp_path)
    run = ctx[0]
    apply(ctx, "pause-schedules")
    run.pause = True
    assert apply(ctx, "close-api")["status"] == "maintenance_operation_pending"
    revision["etag"] = "settled-status-etag"
    revision["updateTime"] = "2026-10-28T22:45:01Z"
    revision["scalingStatus"] = {"desiredMinInstanceCount": 0}
    revision["conditions"].append({"type": "RoutesReady", "state": "CONDITION_SUCCEEDED"})
    next(iter(run.operations.values()))["done"] = True
    assert apply(ctx, "close-api", reconcile=True)["status"] == "maintenance_phase_observed"
    assert len(run.writes) == 1
    assert ctx[3].read("service." + m.SERVICES[0] + ".close.checked") is not None


@pytest.mark.parametrize("drift", ["image", "source", "unknown_configuration"])
def test_prior_revision_real_configuration_drift_still_refuses_reconcile(tmp_path, drift):
    ctx, revision = settling_revision_context(tmp_path)
    run = ctx[0]
    apply(ctx, "pause-schedules")
    run.pause = True
    assert apply(ctx, "close-api")["status"] == "maintenance_operation_pending"
    if drift == "image":
        revision["containers"][0]["image"] = revision["containers"][0]["image"][:-64] + "a" * 64
    elif drift == "source":
        m.rolling.environment(revision)["APP_RELEASE"]["value"] = "a" * 40
    else:
        revision["encryptionKey"] = "other-key"
    next(iter(run.operations.values()))["done"] = True
    with pytest.raises(m.rolling.RollingDenied, match="prior_revision_changed"):
        apply(ctx, "close-api", reconcile=True)
    assert len(run.writes) == 1
    assert ctx[3].read("service." + m.SERVICES[0] + ".close.checked") is None


def test_restore_unknown_patch_reconciles_without_reverting_to_closed_state(tmp_path):
    ctx = context(tmp_path)
    run = ctx[0]
    close(ctx)
    run.lose_ack = True
    with pytest.raises(RuntimeError):
        apply(ctx, "restore-services")
    assert len(run.writes) == 4
    assert apply(ctx, "restore-services", reconcile=True)["status"] == "maintenance_phase_partial"
    assert len(run.writes) == 4
    apply(ctx, "restore-services")
    assert len(run.writes) == 6


def test_safe_abort_restores_only_actions_that_actually_started(tmp_path):
    ctx = context(tmp_path)
    apply(ctx, "pause-schedules")
    apply(ctx, "close-api")
    apply(ctx, "restore-services")
    apply(ctx, "restore-queues")
    apply(ctx, "restore-schedules")
    assert len(ctx[0].writes) == 2
    assert all(kind == "schedule" for kind, _, _ in ctx[1].writes)


def test_restore_will_not_overwrite_a_newly_deployed_candidate(tmp_path):
    ctx = context(tmp_path)
    close(ctx)
    state = ctx[0].states[m.SERVICES[0]]
    state["template"]["containers"][0]["image"] = state["template"]["containers"][0]["image"][:-64] + "e" * 64
    with pytest.raises(m.rolling.RollingDenied, match="readback_changed"):
        apply(ctx, "restore-services")
    assert len(ctx[0].writes) == 3


def test_resume_resources_requires_service_restore_first(tmp_path):
    ctx = context(tmp_path)
    close(ctx)
    with pytest.raises(m.rolling.RollingDenied, match="restore_services_first"):
        apply(ctx, "restore-queues")


def test_changed_queue_configuration_is_never_overwritten(tmp_path):
    ctx = context(tmp_path)
    for phase in ("pause-schedules", "close-api", "close-workers"):
        apply(ctx, phase)
    ctx[1].rows[("queue", m.QUEUES[0])]["retryConfig"] = {"maxAttempts": 4}
    with pytest.raises(m.rolling.RollingDenied, match="configuration_changed"):
        apply(ctx, "pause-queues")
    assert not any(kind == "queue" for kind, _, _ in ctx[1].writes)


def test_zero_percent_tag_is_closed_and_restored_exactly(tmp_path):
    ctx = context(tmp_path)
    run, controls, _, journal, drains, drain = ctx
    state = run.states[m.SERVICES[0]]
    original = run.plan.pin(m.SERVICES[0]).prior_revision
    tagged = m.SERVICES[0] + "-tagged"
    revision = copy.deepcopy(run.revisions[(m.SERVICES[0], original)])
    revision["name"] = state["name"] + "/revisions/" + tagged
    run.revisions[(m.SERVICES[0], tagged)] = revision
    state["trafficStatuses"].append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": tagged, "tag": "review"})
    plan = m.capture(run, controls, "a" * 32)
    ctx = (run, controls, plan, m.rolling.Journal(journal.directory, plan), drains, drain)
    close(ctx)
    assert {r["revision"] for r in state["trafficStatuses"]} == {plan.revision(m.SERVICES[0], "close")}
    assert any(r.get("tag") == "review" and not r.get("percent") for r in state["trafficStatuses"])
    apply(ctx, "restore-services")
    assert m.rolling.pinned_traffic(m.SERVICES[0], state) == list(plan.services[0].traffic)


def test_unrelated_configuration_on_tagged_route_requires_separate_review(tmp_path):
    ctx = context(tmp_path)
    run = ctx[0]
    state = run.states[m.SERVICES[0]]
    original = run.plan.pin(m.SERVICES[0]).prior_revision
    tagged = m.SERVICES[0] + "-tagged"
    row = copy.deepcopy(run.revisions[(m.SERVICES[0], original)])
    row["maxInstanceRequestConcurrency"] = 99
    run.revisions[(m.SERVICES[0], tagged)] = row
    state["trafficStatuses"].append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": tagged, "tag": "different"})
    with pytest.raises(m.rolling.RollingDenied, match="reachable_configuration_differs"):
        m.capture(run, ctx[1], "f" * 32)


def test_native_drain_uses_real_coordinator_execution_queue_and_sql_checks(monkeypatch):
    calls = []
    boundary = SimpleNamespace(
        observer=SimpleNamespace(_cloud=lambda *args: calls.append(args) or []),
        _target=lambda *args: calls.append(("sql", *args)),
    )
    monkeypatch.setattr(m.schema25, "NativeSchema25Boundary", lambda root, schema: boundary)
    monkeypatch.setattr(m.coordinator, "NativeCoordinatorRun", lambda: "reader")
    monkeypatch.setattr(m.coordinator, "observe_closed", lambda reader: calls.append(("coordinator", reader)))
    m.NativeDrain(ROOT)()
    assert calls[0] == ("coordinator", "reader")
    assert sum(c[0] == "tasks" for c in calls) == 4
    assert calls[-1][0] == "sql"


@pytest.mark.parametrize("bad_kind", ["task", "execution", "coordinator", "sql"])
def test_native_drain_refuses_any_unfinished_boundary(monkeypatch, bad_kind):
    def cloud(*args):
        if bad_kind == "task" and args[0] == "tasks":
            return [{"name": "pending"}]
        if bad_kind == "execution" and args[0] == "run":
            return [{"status": {}}]
        return []

    def coordinator(_):
        if bad_kind == "coordinator":
            raise RuntimeError("coordinator still open")

    def sql(*_):
        if bad_kind == "sql":
            raise RuntimeError("SQL lease still active")

    monkeypatch.setattr(m.schema25, "NativeSchema25Boundary", lambda root, schema: SimpleNamespace(observer=SimpleNamespace(_cloud=cloud), _target=sql))
    monkeypatch.setattr(m.coordinator, "NativeCoordinatorRun", lambda: None)
    monkeypatch.setattr(m.coordinator, "observe_closed", coordinator)
    with pytest.raises((RuntimeError, m.rolling.RollingDenied)):
        m.NativeDrain(ROOT)()


def test_scope_tampering_cannot_add_another_service_or_queue(tmp_path):
    plan = context(tmp_path)[2]
    with pytest.raises(m.rolling.RollingDenied, match="plan_scope_invalid"):
        replace(plan, resources=(*plan.resources, m.ResourcePin("queue", "unrelated", "PAUSED", "a"*64))).validate()
    with pytest.raises(m.rolling.RollingDenied, match="control_scope_refused"):
        m.NativeControls.url("queue", "unrelated")


def test_cli_capture_and_exact_hash_refusal(tmp_path, monkeypatch, capsys):
    ctx = context(tmp_path)
    run, controls = ctx[:2]
    monkeypatch.setattr(m.native, "NativeRun", lambda: run)
    monkeypatch.setattr(m, "NativeControls", lambda: controls)
    path = tmp_path / "plan.json"
    monkeypatch.setattr(sys, "argv", ["maintenance", "capture", "--plan", str(path), "--nonce", "a"*32])
    m.main()
    result = json.loads(capsys.readouterr().out)
    assert result["provider_mutations"] == 0 and run.writes == [] and controls.writes == []
    monkeypatch.setattr(sys, "argv", ["maintenance", "pause-schedules", "--plan", str(path), "--plan-sha256", "b"*64, "--journal", str(tmp_path)])
    with pytest.raises(m.rolling.RollingDenied, match="exact_plan_pin_required"):
        m.main()


class Session:
    def __init__(self):
        from urllib3.util import Retry
        self.adapters = {"https": SimpleNamespace(max_retries=Retry())}
        self.physical, self.repeat_post, self.bad_status = [], False, False
        self.trust_env, self.max_redirects = True, 30

    def send(self, request, **kwargs):
        from requests import Response
        self.physical.append((request.method, request.url, request.body, kwargs))
        response = Response()
        response.status_code = 500 if self.bad_status else 200
        name = request.url.split("/v2/", 1)[-1].split("/v1/", 1)[-1].split(":", 1)[0]
        response._content = json.dumps({"name": name, "state": "PAUSED"}).encode()
        return response

    def request(self, method, url, data=None, headers=None, **kwargs):
        from requests import Request
        request = Request(method, url, data=data, headers=headers).prepare()
        response = self.send(request, **kwargs)
        if method == "POST" and self.repeat_post:
            return self.send(request, **kwargs)
        return response


def native_controls(monkeypatch):
    session = Session()
    stock = SimpleNamespace(session=session)
    monkeypatch.setattr(m.native.native, "SDKGoogleReads", lambda *args: SimpleNamespace(_http=lambda: stock))
    return m.NativeControls(), session


def test_native_controls_fixed_endpoints_auth_and_no_redirects(monkeypatch):
    controls, session = native_controls(monkeypatch)
    assert controls.get("queue", m.QUEUES[0])["state"] == "PAUSED"
    controls.set_state("schedule", m.SCHEDULES[0], "PAUSED")
    assert len(session.physical) == 2
    assert session.physical[1][1] == "https://cloudscheduler.googleapis.com/v1/" + m.rolling.PARENT + "/jobs/" + m.SCHEDULES[0] + ":pause"
    assert all(row[3]["allow_redirects"] is False for row in session.physical)
    assert session.trust_env is False and session.max_redirects == 0
    assert all(adapter.max_retries.total == 0 for adapter in session.adapters.values())


def test_native_sdk_retry_cannot_send_a_second_pause(monkeypatch):
    controls, session = native_controls(monkeypatch)
    session.repeat_post = True
    with pytest.raises(m.rolling.RollingDenied, match="write_not_armed"):
        controls.set_state("queue", m.QUEUES[0], "PAUSED")
    assert len(session.physical) == 1 and controls.armed is None


@pytest.mark.parametrize("url", [
    "https://attacker.invalid/",
    "https://cloudtasks.googleapis.com/v2/projects/unrelated/locations/us-central1/queues/test",
    "https://cloudtasks.googleapis.com/v2/" + m.rolling.PARENT + "/queues/" + m.QUEUES[0] + "?pageToken=bad",
])
def test_native_control_transport_refuses_unlisted_reads(monkeypatch, url):
    controls, session = native_controls(monkeypatch)
    with pytest.raises(m.rolling.RollingDenied, match="read_refused"):
        controls.request("GET", url)
    assert session.physical == []


def test_native_error_cannot_be_recorded_as_known_pause_success(monkeypatch):
    controls, session = native_controls(monkeypatch)
    session.bad_status = True
    with pytest.raises(m.rolling.RollingDenied, match="response_unverified"):
        controls.set_state("queue", m.QUEUES[0], "PAUSED")
    assert len(session.physical) == 1 and controls.armed is None


def test_postdeployment_resource_restore_uses_proof_without_reverting_new_image(tmp_path):
    ctx = context(tmp_path)
    run, controls, plan, journal, _drains, drain = ctx
    close(ctx)
    state = run.states[m.SERVICES[0]]
    new_image = state["template"]["containers"][0]["image"][:-64] + "e" * 64
    state["template"]["containers"][0]["image"] = new_image
    checked = []

    def proof():
        checked.append("deployed proof")

    m.execute(plan, run, controls, journal, "restore-queues", drain, deployed=proof)
    assert checked == ["deployed proof"] and len(run.writes) == 3
    assert state["template"]["containers"][0]["image"] == new_image
    assert controls.rows[("queue", m.QUEUES[-1])]["state"] == "PAUSED"
    with pytest.raises(m.rolling.RollingDenied, match="resume_scope_invalid"):
        m.execute(plan, run, controls, journal, "restore-services", drain, deployed=proof)


def test_failed_postdeployment_proof_prevents_any_resource_resume(tmp_path):
    ctx = context(tmp_path)
    close(ctx)
    before = list(ctx[1].writes)

    def failed():
        raise RuntimeError("deployed evidence failed")

    with pytest.raises(RuntimeError, match="evidence failed"):
        m.execute(ctx[2], ctx[0], ctx[1], ctx[3], "restore-queues", ctx[5], deployed=failed)
    assert ctx[1].writes == before


def deployment_proof(tmp_path):
    fixture = FixtureBoundary()
    plan = m.schema25.load_plan(m.rolling.canonical({**asdict(fixture.plan),
        "schema_contract": m.schema25.contract.CONTRACT, "database_schema": m.schema25.contract.SCHEMA, "prior_sources": []}))
    tmp_path.chmod(0o700)
    journal = m.rolling.Journal(tmp_path, plan)
    m.rolling.advance(plan, fixture, journal)
    boundary = object.__new__(m.schema25.NativeSchema25Boundary)
    boundary.schema = plan.database_schema
    calls = []
    boundary.observer = SimpleNamespace(verify_source_image=lambda **kwargs: calls.append("source"),
                                        verify_policy_configuration=lambda policy: calls.append("policy"))
    boundary.observed_disabled_flags = lambda value: calls.append("flags")
    boundary._target = lambda *args: calls.append("sql") or plan.financial_sha256
    for name in ("service", "revision", "iam", "health"):
        setattr(boundary, name, getattr(fixture, name))
    proof = object.__new__(m.NativeDeployed)
    proof.plan, proof.journal, proof.boundary = plan, journal, boundary
    return proof, fixture, calls


def test_native_deployed_rechecks_source_flags_sql_and_actual_health(tmp_path):
    proof, fixture, calls = deployment_proof(tmp_path)
    before = len(fixture.health_checks)
    proof()
    assert calls == ["source", "policy", "flags", "sql"]
    assert len(fixture.health_checks) == before + 3


def test_native_deployed_rejects_saved_success_after_configuration_drift(tmp_path):
    proof, fixture, _calls = deployment_proof(tmp_path)
    fixture.states[m.SERVICES[0]]["template"]["maxInstanceRequestConcurrency"] = 19
    with pytest.raises(m.rolling.RollingDenied):
        proof()


def test_native_deployed_requires_all_three_completed_promotions(tmp_path):
    proof, _fixture, _calls = deployment_proof(tmp_path)
    (tmp_path / ("promote." + m.SERVICES[-1] + ".checked.json")).unlink()
    with pytest.raises(m.rolling.RollingDenied, match="deployment_not_completed"):
        proof()
