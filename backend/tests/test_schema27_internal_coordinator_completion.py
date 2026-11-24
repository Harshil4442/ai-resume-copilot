"""Owned synthetic internal GET/traffic boundaries; never provider authority."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
import test_schema27_native_admission_closure as closure_fixture
import test_schema27_native_admission_completion as stage_fixture
from infra.gcp import schema27_internal_coordinator_completion as op


class Tasks:
    def __init__(self):
        self.queue, self.lose = None, None
        self.calls, self.created = [], {}
        self.attempts = 0

    def call(self, method, path, body=None, *, absent=False):
        self.attempts += 1
        self.calls.append((method, path, copy.deepcopy(body)))
        if method == "GET":
            assert self.queue is not None or absent
            return copy.deepcopy(self.queue)
        if method == "DELETE":
            self.queue, response = None, {}
        elif path.endswith("/queues"):
            assert self.queue is None
            self.queue = copy.deepcopy(body) | {"state": "RUNNING"}
            response = self.queue
        else:
            task = body["task"]
            assert task["name"] not in self.created
            response = {"name": task["name"], "createTime": datetime.now(UTC).isoformat(),
                        "dispatchDeadline": task["dispatchDeadline"]}
            self.created[task["name"]] = copy.deepcopy(body)
        if self.lose == method + ":" + path:
            raise RuntimeError("synthetic_unknown_response")
        return copy.deepcopy(response)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    original_init, original_patch = closure_fixture.Fixture.__init__, closure_fixture.Fixture.patch

    def init(fixture):
        original_init(fixture)
        fixture.states[op.COORDINATOR]["ingress"] = "INGRESS_TRAFFIC_INTERNAL_ONLY"

    def patch(fixture, service, body, mask):
        response = original_patch(fixture, service, body, mask)
        for row in fixture.states[service]["trafficStatuses"]:
            if row.get("tag"):
                row["uri"] = "https://" + row["tag"] + "---" + service + ".run.app"
        return response

    monkeypatch.setattr(closure_fixture.Fixture, "__init__", init)
    monkeypatch.setattr(closure_fixture.Fixture, "patch", patch)
    original = closure_fixture.prepared.__wrapped__(tmp_path, monkeypatch)
    staged = stage_fixture.prepared.__wrapped__(original, tmp_path, monkeypatch)
    staged.fixture.fail_health = True
    with pytest.raises(RuntimeError, match="synthetic_health_failure"):
        op.prior.execute("close", staged.plan, staged.boundary, staged.journal)
    staged.fixture.fail_health = False
    assert staged.journal.read("stage." + op.COORDINATOR + ".ack") is not None
    assert staged.journal.read("stage." + op.COORDINATOR + ".checked") is None
    parent_path, frozen = tmp_path / "staged-parent.json", tmp_path / "staged-source"
    frozen.mkdir()
    digest = op.restore.mixed.app.bridge._write_plan(parent_path, staged.plan)
    parent = op.make_parent(root=frozen, plan_path=parent_path, plan_sha256=digest,
                           journal_path=staged.journal.directory)
    old_git, old_sha = op.subprocess.check_output, op.sha
    monkeypatch.setattr(op.subprocess, "check_output", lambda args, **kw:
        (staged.plan.source["release"] + "\n").encode()
        if Path(kw["cwd"]) == frozen and args[1] == "rev-parse" else old_git(args, **kw))
    monkeypatch.setattr(op, "sha", lambda p: "c" * 64
                        if str(p).endswith("/synthetic-new.py") else old_sha(p))
    source = {"release": "1" * 40, "tree": "2" * 40, "files": {"synthetic-internal.py": "3" * 64}}
    monkeypatch.setattr(op, "source", lambda: copy.deepcopy(source))
    monkeypatch.setattr(op, "historical_source", lambda value: op.need(value == source, "source_bytes"))
    monkeypatch.setattr(op, "health_source", lambda old, _metadata: {
        "release": old.coordinator.source, "image": old.coordinator.image,
        "files": {op.WORKER: "4" * 64, op.ENABLED: "5" * 64}, "entrypoint": op.COMMAND,
        "health_role": "resume-upload-coordinator", "scan_closed_by_exact_native_flags": True,
        "response_json_observed": False,
    })
    boundary = object.__new__(op.NativeBoundary)
    for name in ("service", "revision", "patch", "operation"):
        setattr(boundary, name, getattr(staged.fixture, name))
    transport, faults = Tasks(), {"missing": False}
    boundary._tasks = transport
    boundary.tasks = lambda _plan: transport
    boundary.principals = lambda: ({"uniqueId": "1" * 21}, {"uniqueId": "2" * 21})
    boundary.preflight = lambda _plan: None
    entries = {}
    def logs(plan, phase, created):
        if faults["missing"]:
            return []
        if phase not in entries:
            entries[phase] = request_log(plan, phase, staged.plan.coordinator.revision, created)
        return [copy.deepcopy(entries[phase])]
    boundary.request_logs = logs
    directory = tmp_path / "internal-health"
    directory.mkdir(mode=0o700)
    plan = op.capture(parent, boundary, "a" * 32, directory)
    return SimpleNamespace(staged=staged, plan=plan, boundary=boundary, transport=transport,
                           faults=faults, journal=op.owned.Journal(directory, plan, create=True))


def request_log(plan, phase, revision, created):
    stamp = max(datetime.fromisoformat(created.replace("Z", "+00:00")), datetime.now(UTC))
    return {"resource": {"type": "cloud_run_revision", "labels": {
        "project_id": op.PROJECT, "location": op.rolling.release.REGION,
        "service_name": op.COORDINATOR, "revision_name": revision}},
        "logName": "projects/" + op.PROJECT + "/logs/run.googleapis.com%2Frequests",
        "insertId": "synthetic-" + phase, "timestamp": stamp.isoformat(),
        "httpRequest": {"requestMethod": "GET", "requestUrl": plan.url(phase), "status": 200,
                        "latency": "0.01s"}}


def advance_until(c, status):
    for _ in range(12):
        value = op.advance(c.plan, c.boundary, c.journal)
        if value.get("status") == status or value.get("kind") == op.PROOF:
            return value
    pytest.fail("Expected bounded synthetic stage was not reached")


@pytest.mark.parametrize("phase", ["before", "after"])
def test_exact_GET_nonce_scheduler_audience_deadline_and_single_attempt_queue(prepared, phase):
    plan = prepared.plan
    assert op.task_body(plan, phase) == {"task": {"name": plan.task(phase), "dispatchDeadline": "15s",
        "httpRequest": {"httpMethod": "GET", "url": plan.url(phase), "oidcToken": {
            "serviceAccountEmail": "hirewiz-resume-scheduler@" + op.PROJECT + ".iam.gserviceaccount.com",
            "audience": plan.audience}}}}
    assert plan.url(phase).endswith("/api/health?hirewiz_probe=" + plan.nonce + "-" + phase)
    assert op.queue_body(plan)["retryConfig"] == {"maxAttempts": 1, "maxRetryDuration": "0s"}
    assert op.queue_body(plan)["rateLimits"] == {"maxDispatchesPerSecond": 1, "maxConcurrentDispatches": 1}


def test_two_GETs_only_unsent_route_no_old_stage_backfill_or_JSON_claim(prepared):
    c = prepared
    old = {p.name: p.read_bytes() for p in c.staged.journal.directory.iterdir() if p.is_file()}
    writes = len(c.staged.fixture.writes)
    result = advance_until(c, "complete")
    assert result["kind"] == op.PROOF and result["new_admission_closed"] is True
    creates = [body for method, path, body in c.transport.calls if method == "POST" and path.endswith("/tasks")]
    assert creates == [op.task_body(c.plan, "before"), op.task_body(c.plan, "after")]
    assert len(c.staged.fixture.writes) == writes + 1
    service, body, mask = c.staged.fixture.writes[-1]
    assert service == op.COORDINATOR and mask == "traffic" and "template" not in body
    assert result["response_json_observed"] is False and result["old_external_health_claimed"] is False
    assert c.staged.journal.read("stage." + op.COORDINATOR + ".checked") is None
    assert c.staged.journal.read("route." + op.COORDINATOR + ".started") is None
    assert all(p.read_bytes() == old[p.name] for p in c.staged.journal.directory.iterdir() if p.is_file())
    assert op.ClosedInternalAdmission(c.plan, c.journal).verify_current(c.boundary) == result["services"]
    mutations = len([call for call in c.transport.calls if call[0] != "GET"])
    assert op.advance(c.plan, c.boundary, c.journal)["native_mutations"] == 0
    assert len([call for call in c.transport.calls if call[0] != "GET"]) == mutations


@pytest.mark.parametrize("fault", ["status", "method", "url", "revision", "project", "service", "log", "duplicate", "old", "future"])
def test_log_proves_exact_request200_and_never_a_response_body(prepared, fault):
    c = prepared
    op.advance(c.plan, c.boundary, c.journal)
    op.advance(c.plan, c.boundary, c.journal)
    ack = c.journal.read("before.ack")
    row = request_log(c.plan, "before", c.staged.plan.coordinator.revision, ack["createTime"])
    if fault in {"status", "method", "url"}:
        key, value = {"status": ("status", 503), "method": ("requestMethod", "POST"),
                      "url": ("requestUrl", c.plan.url("after"))}[fault]
        row["httpRequest"][key] = value
    elif fault in {"revision", "project", "service"}:
        key = {"revision": "revision_name", "project": "project_id", "service": "service_name"}[fault]
        row["resource"]["labels"][key] = "foreign"
    elif fault == "log":
        row["logName"] = "projects/foreign/logs/requests"
    elif fault in {"old", "future"}:
        row["timestamp"] = (datetime.now(UTC) + timedelta(seconds=-3600 if fault == "old" else 10)).isoformat()
    entries = [row, copy.deepcopy(row)] if fault == "duplicate" else [row]
    with pytest.raises(op.rolling.RollingDenied):
        op._log_receipt(c.plan, "before", ack, entries, op.current(c.plan, c.boundary, routed=False))
    assert len(c.staged.fixture.writes) == 3


def test_late_log_never_recreates_task_or_routes(prepared):
    c = prepared
    op.advance(c.plan, c.boundary, c.journal)
    op.advance(c.plan, c.boundary, c.journal)
    c.faults["missing"] = True
    for _ in range(3):
        assert op.advance(c.plan, c.boundary, c.journal)["status"] == "before_probe_log_pending"
    assert len(c.transport.created) == 1 and len(c.staged.fixture.writes) == 3
    c.faults["missing"] = False
    assert op.advance(c.plan, c.boundary, c.journal)["status"] == "before_internal_health_verified"


@pytest.mark.parametrize("fault", ["queue", "before", "route"])
def test_unknown_ack_never_replays(prepared, fault):
    c = prepared
    if fault == "queue":
        c.transport.lose = "POST:" + op.PARENT + "/queues"
    else:
        op.advance(c.plan, c.boundary, c.journal)
        if fault == "before":
            c.transport.lose = "POST:" + c.plan.queue + "/tasks"
        else:
            advance_until(c, "before_internal_health_verified")
            c.staged.fixture.lose_ack = True
    with pytest.raises(RuntimeError, match="synthetic_(unknown_response|lost_ack)"):
        op.advance(c.plan, c.boundary, c.journal)
    count = len(c.transport.calls), len(c.staged.fixture.writes)
    with pytest.raises(op.rolling.RollingDenied, match="missing_ack_reconcile_only"):
        op.advance(c.plan, c.boundary, c.journal)
    assert count == (len(c.transport.calls), len(c.staged.fixture.writes))


def test_stored_foreign_revision_is_not_accepted_by_historical_reader(prepared):
    c = prepared
    advance_until(c, "complete")
    path = c.journal.directory / "before.checked.json"
    value = json.loads(path.read_bytes())
    value["request_log"]["resource"]["labels"]["revision_name"] = "foreign"
    path.write_text(json.dumps(value) + "\n")
    with pytest.raises(op.rolling.RollingDenied):
        op.ClosedInternalAdmission(c.plan, c.journal)


def test_forty_invocations_are_durable_and_refuse_a_restart(prepared):
    c = prepared
    for _ in range(40):
        assert op.advance(c.plan, c.boundary, c.journal, observe=True)["status"] == "queue_not_created"
        c.journal = op.owned.Journal(c.journal.directory, c.plan)
    attempts, writes = len(c.transport.calls), len(c.staged.fixture.writes)
    with pytest.raises(op.rolling.RollingDenied, match="operator_budget_exhausted"):
        op.advance(c.plan, c.boundary, c.journal, observe=True)
    assert (len(c.transport.calls), len(c.staged.fixture.writes)) == (attempts, writes)
    assert len(list(c.journal.directory.glob("invocation.*.json"))) == 40


class BoundedResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status
        self.raw, self.reads, self.closed = self, [], False

    def read(self, size, *, decode_content):
        assert decode_content is True
        self.reads.append(size)
        return self.payload[:size]

    @property
    def content(self):
        return self._content

    def close(self):
        self.closed = True


class FixedSession:
    def __init__(self, payload=b"{}", *, double=False, error=None, status=200):
        self.adapters, self.payload, self.status = {}, payload, status
        self.double, self.error, self.sends, self.responses = double, error, 0, []

    def send(self, request, **kwargs):
        assert kwargs["allow_redirects"] is False
        self.sends += 1
        if self.error:
            raise self.error
        response = BoundedResponse(self.payload, self.status)
        self.responses.append(response)
        return response

    def request(self, method, url, *, data, headers, timeout, allow_redirects, stream):
        assert timeout == (5, 20) and allow_redirects is False and stream is True
        assert headers == {"Content-Type": "application/json"}
        request = SimpleNamespace(method=method, url=url, body=data)
        response = self.send(request)
        if self.double:
            response.close()
            self.send(request)
        return response

    def close(self):
        pass


def real_bounded_transport(c, monkeypatch, **kwargs):
    session = FixedSession(**kwargs)
    monkeypatch.setattr(op.restore.native, "_stock_http", lambda: SimpleNamespace(session=session))
    transport = op.TasksHttp(c.plan)
    transport.journal = c.journal
    return transport, session


def test_transport_budget_reserves_three_cleanup_calls_and_8193_bytes(prepared, monkeypatch):
    c = prepared
    transport, session = real_bounded_transport(c, monkeypatch)
    normal = 95 - c.plan.capture_transport_calls
    for _ in range(normal):
        assert transport.call("GET", c.plan.queue) == {}
    with pytest.raises(op.rolling.RollingDenied, match="operator_budget_exhausted"):
        transport.call("GET", c.plan.queue)
    assert session.sends == normal
    retained = op.TasksHttp(c.plan)
    retained.journal, retained.cleanup_mode = op.owned.Journal(c.journal.directory, c.plan), True
    for _ in range(3):
        retained.reserve_rpc("DELETE", c.plan.queue, None)
    with pytest.raises(op.rolling.RollingDenied, match="operator_budget_exhausted"):
        retained.reserve_rpc("DELETE", c.plan.queue, None)
    rows = [json.loads(p.read_bytes()) for p in c.journal.directory.glob("tasks-rpc.*.json")]
    assert len(rows) + c.plan.capture_transport_calls == 98
    assert all(r["response_bytes_reserved"] == 8193 and r["uncertain_send_consumes_reservation"] is True for r in rows)
    assert (len(rows) + c.plan.capture_transport_calls) * 8193 == 802914
    assert all(response.reads == [8193] and response.closed for response in session.responses)


@pytest.mark.parametrize("fault", ["oversize", "double", "exception"])
def test_fixed_native_transport_rejects_overbound_replay_and_retains_uncertainty(prepared, monkeypatch, fault):
    c = prepared
    transport, session = real_bounded_transport(c, monkeypatch,
        payload=b"x" * 8193 if fault == "oversize" else b"{}",
        double=fault == "double", error=RuntimeError("private-provider-value") if fault == "exception" else None)
    expected = {"oversize": "response_bound", "double": "physical_send_replay_refused", "exception": "private-provider-value"}[fault]
    with pytest.raises(Exception, match=expected):
        transport.call("GET", c.plan.queue)
    assert session.sends == 1 and transport.armed is None
    rows = list(c.journal.directory.glob("tasks-rpc.*.json"))
    assert len(rows) == 1 and json.loads(rows[0].read_bytes())["uncertain_send_consumes_reservation"] is True
    assert all(response.closed for response in session.responses)


def test_exact_health_source_is_the_real_deployed_7a_Git_AST():
    release = "7a00839dc90a93caebb0b3b5f2e9e03c51e941e9"
    old = SimpleNamespace(coordinator=SimpleNamespace(source=release, image="sha256:" + "a" * 64))
    result = op.health_source(old, {"template": {"containers": [copy.deepcopy(op.COMMAND)]}})
    assert result["release"] == release and result["entrypoint"] == op.COMMAND
    assert result["scan_closed_by_exact_native_flags"] is True and result["response_json_observed"] is False
    assert set(result["files"]) == {op.WORKER, op.ENABLED}
    for name, digest in result["files"].items():
        raw = op.subprocess.check_output(["git", "show", release + ":" + name], cwd=op.ROOT, timeout=10)
        assert op.hashlib.sha256(raw).hexdigest() == digest


def test_genuine_health_source_rejects_a_changed_scan_guard(monkeypatch):
    release = "7a00839dc90a93caebb0b3b5f2e9e03c51e941e9"
    old = SimpleNamespace(coordinator=SimpleNamespace(source=release, image="sha256:" + "a" * 64))
    actual = op.subprocess.check_output
    def changed(args, **kw):
        raw = actual(args, **kw)
        return raw.replace(b'os.getenv("RESUME_DIRECT_UPLOAD_ENABLED")', b'os.getenv("RESUME_DIRECT_UPLOAD_ENABLED_OTHER")') if args[2].endswith(":" + op.ENABLED) else raw
    monkeypatch.setattr(op.subprocess, "check_output", changed)
    with pytest.raises(op.rolling.RollingDenied, match="scan_guard_source"):
        op.health_source(old, {"template": {"containers": [copy.deepcopy(op.COMMAND)]}})


def test_paused_cleanup_only_deletes_ack_owned_queue_once(prepared):
    c = prepared
    op.advance(c.plan, c.boundary, c.journal)
    (c.journal.directory / "operator.pause-requested").touch(mode=0o600)
    assert op.cleanup(c.plan, c.boundary, c.journal)["native_mutations"] == 1
    assert op.cleanup(c.plan, c.boundary, c.journal)["native_mutations"] == 0
    assert len([row for row in c.transport.calls if row[0] == "DELETE"]) == 1
    assert not c.transport.created and len(c.staged.fixture.writes) == 3


@pytest.mark.parametrize("error", ["private-token=https://private.example/secret", "internal_coordinator_secret=https://private.example/secret"])
def test_CLI_never_prints_poisoned_exception_text(prepared, monkeypatch, capsys, error):
    monkeypatch.setattr(op, "NativeBoundary", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(op.restore, "_private_read", lambda *_a: (_ for _ in ()).throw(RuntimeError(error)))
    monkeypatch.setattr("sys.argv", ["internal", "observe", "--plan", "/unused", "--plan-sha256", "a" * 64, "--journal", "/unused"])
    assert op.main() == 1
    output = capsys.readouterr().out
    assert error not in output and "private.example" not in output
    assert json.loads(output)["status"] == "schema27_forward_refused"


@pytest.mark.parametrize(("status", "body", "accepted"), [
    (204, b"", True), (200, b"{}", True), (200, b"", True),
    (201, b"{}", False), (200, b'{"unexpected":true}', False), (503, b"", False)])
def test_DELETE_has_only_native_Empty_ACK(prepared, monkeypatch, status, body, accepted):
    transport, session = real_bounded_transport(prepared, monkeypatch, payload=body, status=status)
    if accepted:
        assert transport.call("DELETE", prepared.plan.queue) == {}
    else:
        with pytest.raises(op.rolling.RollingDenied):
            transport.call("DELETE", prepared.plan.queue)
    assert session.sends == 1 and session.responses[0].closed and transport.armed is None
