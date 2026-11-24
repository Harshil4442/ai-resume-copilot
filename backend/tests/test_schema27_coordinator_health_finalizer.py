"""Pure traffic reconciliation and owned one-GET continuation; no provider calls."""
from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_coordinator_health_finalizer as op

REVISION = "hirewiz-resume-coordinator-repaired"
ALIAS = "safe-nc-abcdefabcdef"


def split_traffic():
    return [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": REVISION, "percent": 100},
            {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": REVISION, "tag": ALIAS}]


def collapsed_traffic():
    return [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": REVISION, "percent": 100, "tag": ALIAS}]


def test_native_collapsed_row_has_exact_same_default_allocation_and_alias():
    expected = {"default": {"revision": REVISION, "percent": 100}, "aliases": {ALIAS: REVISION}}
    assert op.logical_traffic(split_traffic()) == expected
    assert op.logical_traffic(collapsed_traffic()) == expected
    assert op.logical_traffic(list(reversed(split_traffic()))) == expected
    # Inputs remain raw native evidence, rather than being rewritten in place.
    raw = collapsed_traffic()
    before = copy.deepcopy(raw)
    op.logical_traffic(raw)
    assert raw == before


@pytest.mark.parametrize("change", ["default_revision", "alias_revision", "alias_name", "remove_alias"])
def test_different_allocations_or_aliases_cannot_reconcile(change):
    changed = split_traffic()
    if change == "default_revision":
        changed[0]["revision"] = "hirewiz-resume-coordinator-foreign"
    elif change == "alias_revision":
        changed[1]["revision"] = "hirewiz-resume-coordinator-foreign"
    elif change == "alias_name":
        changed[1]["tag"] = "unreviewed-alias"
    else:
        changed.pop()
    assert op.logical_traffic(changed) != op.logical_traffic(collapsed_traffic())


@pytest.mark.parametrize("rows", [
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_LATEST", "revision": REVISION, "percent": 100}],
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": REVISION, "percent": 99}],
    split_traffic() + [copy.deepcopy(split_traffic()[1])],
    split_traffic() + [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": REVISION, "percent": 0}],
    [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": REVISION, "percent": 100, "unreviewed": True}],
    []])
def test_ambiguous_partial_or_unreviewed_traffic_is_refused(rows):
    with pytest.raises(op.rolling.RollingDenied):
        op.logical_traffic(rows)


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    import test_schema27_internal_coordinator_completion as fixture
    c = fixture.prepared.__wrapped__(tmp_path, monkeypatch)
    for _ in range(8):
        if op.old.advance(c.plan, c.boundary, c.journal)["status"] == "route_acknowledged":
            break
    else:
        pytest.fail("Synthetic retained route ACK not reached")
    assert op.old.cleanup(c.plan, c.boundary, c.journal)["native_mutations"] == 1
    assert op.old.cleanup(c.plan, c.boundary, c.journal)["native_mutations"] == 0
    for _ in range(2):
        op.old.reserve(c.journal, "tasks-rpc", 95, {"request_sha256": "4" * 64,
            "response_bytes_reserved": 8193, "uncertain_send_consumes_reservation": True})
    service = c.staged.fixture.states[op.COORDINATOR]
    for key in ("traffic", "trafficStatuses"):
        selected = next(r for r in service[key] if r.get("tag") == c.staged.plan.tag)
        service[key] = [copy.deepcopy(selected) | {"percent": 100}] + [
            copy.deepcopy(r) | {"percent": 0} for r in service[key] if r.get("tag") and r.get("tag") != c.staged.plan.tag]
    parent_path, frozen = tmp_path / "internal-plan.json", tmp_path / "retained-internal-source"
    frozen.mkdir()
    digest = op.restore.mixed.app.bridge._write_plan(parent_path, c.plan)
    parent = op.make_parent(root=frozen, plan_path=parent_path, plan_sha256=digest, journal_path=c.journal.directory)
    old_git, old_sha = op.subprocess.check_output, op.sha
    def git(args, **kw):
        if Path(kw["cwd"]) == frozen:
            if args[1:3] == ["rev-parse", "HEAD"]:
                return (c.plan.source["release"] + "\n").encode()
            if args[1] == "status":
                return b""
        return old_git(args, **kw)
    monkeypatch.setattr(op.subprocess, "check_output", git)
    monkeypatch.setattr(op, "sha", lambda p: "3" * 64 if Path(p) == frozen / "synthetic-internal.py" else old_sha(p))
    pin = {"release": "b" * 40, "tree": "c" * 40, "files": {"synthetic-final.py": "d" * 64}}
    monkeypatch.setattr(op, "source", lambda: copy.deepcopy(pin))
    monkeypatch.setattr(op, "historical_source", lambda value: op.need(value == pin, "source_bytes"))
    boundary = object.__new__(op.NativeBoundary)
    for name in ("service", "revision", "operation"):
        setattr(boundary, name, getattr(c.staged.fixture, name))
    transport = fixture.Tasks()
    transport.attempts = c.plan.capture_transport_calls + 2
    boundary._tasks = transport
    boundary.tasks = lambda _plan: transport
    boundary.preflight = lambda _plan: None
    boundary.old_queue_absent = lambda _plan: True
    entries = {}
    def logs(plan, phase, created):
        assert phase == "after"
        if phase not in entries:
            entries[phase] = fixture.request_log(plan, phase, c.staged.plan.coordinator.revision, created)
        return [copy.deepcopy(entries[phase])]
    boundary.request_logs = logs
    directory = tmp_path / "final-health"
    directory.mkdir(mode=0o700)
    plan = op.capture(parent, boundary, "f" * 32, directory)
    journal = op.owned.Journal(directory, plan, create=True)
    return SimpleNamespace(old=c, plan=plan, boundary=boundary, transport=transport, journal=journal,
                           json=json, fixture=fixture)


def complete(c):
    for _ in range(7):
        result = op.advance(c.plan, c.boundary, c.journal)
        if result.get("kind") == op.PROOF:
            return result
    pytest.fail("Bounded synthetic finalization did not complete")


def test_one_after_GET_no_PATCH_and_old_receipts_remain_immutable(prepared):
    c = prepared
    old = {p.name: p.read_bytes() for p in c.old.journal.directory.iterdir() if p.is_file()}
    writes = copy.deepcopy(c.old.staged.fixture.writes)
    result = complete(c)
    assert c.old.staged.fixture.writes == writes
    tasks = [body for method, path, body in c.transport.calls if method == "POST" and path.endswith("/tasks")]
    assert tasks == [op.old.task_body(c.plan, "after")]
    assert len([r for r in c.transport.calls if r[0] == "DELETE"]) == 1
    assert result["cumulative_health_gets"] == 2 and result["maximum_dispatches"] == 2
    assert result["raw_native_traffic_preserved"] is True and result["response_json_observed"] is False
    assert result["both_owned_probe_queues_removed"] is True
    assert all(p.read_bytes() == old[p.name] for p in c.old.journal.directory.iterdir() if p.is_file())
    assert c.old.journal.read("after.started") is None and c.old.journal.read("complete") is None
    reader = op.ClosedFinalAdmission(c.plan, c.journal)
    assert reader.verify_current(c.boundary) == result["services"]
    assert reader.parent_restore_plan_sha256 == result["parent_restore_plan_sha256"]
    assert reader.parent_restore_complete_sha256 == result["parent_restore_complete_sha256"]
    with pytest.raises(op.rolling.RollingDenied, match="mutation_forbidden"):
        c.boundary.patch(op.COORDINATOR, {}, "traffic")
    with pytest.raises(op.rolling.RollingDenied, match="mutation_forbidden"):
        object.__new__(op.ReadOnlyRun).patch(op.COORDINATOR, {}, "template")


def test_native_raw_traffic_mismatch_is_rejected_before_task_creation(prepared):
    c = prepared
    c.old.staged.fixture.states[op.COORDINATOR]["trafficStatuses"][0]["percent"] = 99
    with pytest.raises(op.rolling.RollingDenied):
        op.advance(c.plan, c.boundary, c.journal)
    assert not c.transport.created


@pytest.mark.parametrize("name", ["before.checked", "route.ack", "delete.ack"])
def test_retained_receipt_tampering_prevents_new_queue(prepared, name):
    c = prepared
    path = c.old.journal.directory / (name + ".json")
    data = c.json.loads(path.read_bytes())
    data["unreviewed"] = True
    path.write_text(c.json.dumps(data) + "\n")
    with pytest.raises(op.rolling.RollingDenied):
        op.advance(c.plan, c.boundary, c.journal)
    assert not any(row[0] == "POST" for row in c.transport.calls)


def test_invocation_and_rpc_carry_cannot_reset_between_owners(prepared, monkeypatch):
    c = prepared
    assert c.plan.previous_calls == c.old.plan.capture_transport_calls + 2
    assert c.plan.previous_invocations == 4
    assert c.plan.capture_transport_calls == c.plan.previous_calls + 1
    session = c.fixture.FixedSession()
    monkeypatch.setattr(op.restore.native, "_stock_http", lambda: SimpleNamespace(session=session))
    transport = op.TasksHttp(c.plan)
    transport.journal = c.journal
    assert transport.attempts == c.plan.capture_transport_calls
    for _ in range(95 - c.plan.capture_transport_calls):
        transport.reserve_rpc("LOG_READ", "fixed-synthetic-query", None)
    with pytest.raises(op.rolling.RollingDenied, match="operator_budget_exhausted"):
        transport.reserve_rpc("GET", c.plan.queue, None)
    transport.cleanup_mode = True
    for _ in range(3):
        transport.reserve_rpc("DELETE", c.plan.queue, None)
    with pytest.raises(op.rolling.RollingDenied, match="operator_budget_exhausted"):
        transport.reserve_rpc("DELETE", c.plan.queue, None)
    assert len(list(c.journal.directory.glob("health-control.*.json"))) + c.plan.capture_transport_calls == 98
    for _ in range(40 - c.plan.previous_invocations):
        assert op.advance(c.plan, c.boundary, c.journal, observe=True)["status"] == "queue_not_created"
        c.journal = op.owned.Journal(c.journal.directory, c.plan)
    with pytest.raises(op.rolling.RollingDenied, match="operator_budget_exhausted"):
        op.advance(c.plan, c.boundary, c.journal, observe=True)


def test_new_transport_forbids_before_probe_before_any_native_send(prepared, monkeypatch):
    c = prepared
    session = c.fixture.FixedSession()
    monkeypatch.setattr(op.restore.native, "_stock_http", lambda: SimpleNamespace(session=session))
    transport = op.TasksHttp(c.plan)
    transport.journal = c.journal
    with pytest.raises(op.rolling.RollingDenied, match="only_after_get"):
        transport.call("POST", c.plan.queue + "/tasks", op.old.task_body(c.plan, "before"))
    assert session.sends == 0


def test_real_stock_transport_constructor_carries_fourteen_previous_calls(prepared, monkeypatch):
    from dataclasses import replace
    c = prepared
    session = c.fixture.FixedSession()
    monkeypatch.setattr(op.restore.native, "_stock_http", lambda: SimpleNamespace(session=session))
    retained = replace(c.plan, previous_calls=14, capture_transport_calls=14)
    transport = op.TasksHttp(retained)
    assert transport.attempts == 14
    for _ in range(4):
        transport.reserve_rpc("GET", retained.queue, None)
    assert transport.attempts == 18
    with pytest.raises(op.rolling.RollingDenied, match="capture_rpc_budget"):
        transport.reserve_rpc("GET", retained.queue, None)
    assert session.sends == 0


@pytest.mark.parametrize("phase", ["queue", "after"])
def test_uncertain_new_queue_or_after_task_cannot_replay(prepared, phase):
    c = prepared
    if phase == "queue":
        c.transport.lose = "POST:" + op.old.PARENT + "/queues"
    else:
        op.advance(c.plan, c.boundary, c.journal)
        c.transport.lose = "POST:" + c.plan.queue + "/tasks"
    with pytest.raises(RuntimeError, match="synthetic_unknown_response"):
        op.advance(c.plan, c.boundary, c.journal)
    count = len(c.transport.calls)
    with pytest.raises(op.rolling.RollingDenied, match="missing_ack_reconcile_only"):
        op.advance(c.plan, c.boundary, c.journal)
    assert len(c.transport.calls) == count


def test_extra_old_budget_record_cannot_hide_outside_counted_range(prepared):
    c = prepared
    c.old.journal.record("tasks-rpc.zz", {"request_sha256": "5" * 64,
        "response_bytes_reserved": 8193, "uncertain_send_consumes_reservation": True})
    with pytest.raises(op.rolling.RollingDenied):
        op.advance(c.plan, c.boundary, c.journal)
    assert not c.transport.created
