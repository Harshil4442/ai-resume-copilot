"""Native rejected parent, coordinator-only continuation; no provider calls."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_native_admission_completion as op
from test_schema27_native_admission_closure import prepared as original_prepared  # noqa: F401


def private(path, value):
    path.write_text(json.dumps(value) + "\n")
    path.chmod(0o600)
    return op.file_sha(path)


@pytest.fixture
def prepared(original_prepared, tmp_path, monkeypatch):  # noqa: F811 - imported pytest fixture
    c = original_prepared

    def reject(service, body, mask):
        if service == op.COORDINATOR:
            raise RuntimeError("synthetic_definite400")
        return c.fixture.patch(service, body, mask)

    c.boundary.patch = reject
    with pytest.raises(RuntimeError, match="synthetic_definite400"):
        op.original.execute("close", c.plan, c.boundary, c.journal)
    oldpath = tmp_path / "old-closure.json"
    oldsha = op.restore.mixed.app.bridge._write_plan(oldpath, c.plan)
    rejection = {
        "read_at": "2026-11-24T13:46:11+00:00",
        "native_audit_entries": [
            {
                "insertId": "synthetic-audit",
                "logName": "projects/"
                + op.rolling.release.PROJECT
                + "/logs/cloudaudit.googleapis.com%2Factivity",
                "timestamp": "2026-11-24T13:43:04Z",
                "protoPayload": {
                    "methodName": "google.cloud.run.v2.Services.UpdateService",
                    "resourceName": op.rolling.PARENT + "/services/" + op.COORDINATOR,
                    "status": {
                        "code": 3,
                        "message": "traffic[].tag: traffic tag '"
                        + c.plan.tag
                        + "' and service name '"
                        + op.COORDINATOR
                        + "' together are too long. Combined traffic tag and service name cannot exceed 46 characters.",
                    },
                },
            }
        ],
        "service_patch_response_http_status": 400,
        "native_mutation_acknowledged": False,
        "original_journal_retained": True,
        "replay_authorized": False,
    }
    failure = {
        "observed_at": "2026-11-24T13:43:04.8+00:00",
        "diagnostic": {
            "http_status": 400,
            "exception_class": "HttpBadRequestError",
            "replay_authorized": False,
            "native_approval": False,
        },
        "automatic_replay": False,
    }
    rp, fp = tmp_path / "rejected.json", tmp_path / "failure.json"
    rsha, fsha = private(rp, rejection), private(fp, failure)
    parent = op.make_parent(
        root=tmp_path,
        plan_path=oldpath,
        plan_sha256=oldsha,
        journal_path=c.journal.directory,
        rejection_path=rp,
        rejection_sha256=rsha,
        failure_path=fp,
        failure_sha256=fsha,
    )
    realsha = op.file_sha
    monkeypatch.setattr(
        op, "file_sha", lambda p: "7" * 64 if str(p).endswith("/synthetic.py") else realsha(p)
    )
    monkeypatch.setattr(
        op.subprocess,
        "check_output",
        lambda cmd, **kw: (
            (c.plan.source["release"] + "\n").encode() if cmd[1] == "rev-parse" else b""
        ),
    )
    source = {"release": "a" * 40, "tree": "b" * 40, "files": {"synthetic-new.py": "c" * 64}}
    monkeypatch.setattr(op, "source", lambda: copy.deepcopy(source))
    monkeypatch.setattr(op, "historical_source", lambda v: op.need(v == source, "source_bytes"))
    boundary = object.__new__(op.NativeBoundary)
    for name in ("service", "revision", "patch", "operation", "health"):
        setattr(boundary, name, getattr(c.fixture, name))
    audit_reads = []
    boundary.verify_rejection = lambda value: audit_reads.append(value)
    directory = tmp_path / "completion"
    directory.mkdir(mode=0o700)
    plan = op.capture(parent, boundary, "e" * 32, directory)
    return SimpleNamespace(
        old=c,
        fixture=c.fixture,
        boundary=boundary,
        parent=parent,
        plan=plan,
        journal=op.owned.Journal(directory, plan, create=True),
        rejection=rejection,
        failure=failure,
        rejection_path=rp,
        failure_path=fp,
        audit_reads=audit_reads,
    )


def test_coordinator_only_composes_genuine_history(prepared):
    c = prepared
    before = copy.deepcopy(c.fixture.states)
    oldbytes = {p.name: p.read_bytes() for p in c.old.journal.directory.iterdir() if p.is_file()}
    assert c.audit_reads == [c.parent]
    result = op.execute("close", c.plan, c.boundary, c.journal)
    assert result["new_admission_closed"] and result["native_mutations"] == 2
    assert [x[0] for x in c.fixture.writes] == [op.API, op.API, op.COORDINATOR, op.COORDINATOR]
    assert [x[2] for x in c.fixture.writes[-2:]] == ["template,traffic", "traffic"]
    assert (
        c.fixture.states[op.API] == before[op.API]
        and c.fixture.states[op.ANALYSIS] == before[op.ANALYSIS]
    )
    assert len(c.plan.tag) + len(op.COORDINATOR) <= 46
    assert all(
        op.rolling.environment(c.fixture.states[op.COORDINATOR]["template"])[n]["value"] == "false"
        for n in op.original.FLAGS[op.COORDINATOR]
    )
    assert all(
        p.read_bytes() == oldbytes[p.name] for p in c.old.journal.directory.iterdir() if p.is_file()
    )
    assert not (c.old.journal.directory / "complete.json").exists()
    assert not (c.old.journal.directory / ("stage." + op.COORDINATOR + ".ack.json")).exists()
    reader = op.ClosedAdmissionCompletion(c.plan, c.journal)
    assert reader.restore_parent == c.old.plan.parent
    assert reader.parent_restore_plan_sha256 == c.old.plan.parent["plan_sha256"]
    assert set(reader.verify_current(c.boundary)) == {op.API, op.COORDINATOR}
    assert op.execute("close", c.plan, c.boundary, c.journal)["native_mutations"] == 0
    assert all(
        "synthetic-sensitive-literal" not in p.read_text()
        for p in c.journal.directory.glob("*.json")
    )


@pytest.mark.parametrize("length", [19, 20, 21])
def test_combined_tag_boundary(length):
    tag = "x" * length
    if len(op.COORDINATOR) + length <= 46:
        op.require_tag(op.COORDINATOR, tag)
    else:
        with pytest.raises(Exception, match="tag_length"):
            op.require_tag(op.COORDINATOR, tag)


@pytest.mark.parametrize(
    "change", ["code", "message", "method", "resource", "ack", "failure", "api_receipt"]
)
def test_parent_rejection_and_api_receipts_are_exact(prepared, change):
    c = prepared
    parent = copy.deepcopy(c.parent)
    v = copy.deepcopy(c.rejection)
    p = v["native_audit_entries"][0]["protoPayload"]
    if change == "code":
        p["status"]["code"] = 14
    elif change == "message":
        p["status"]["message"] = "different"
    elif change == "method":
        p["methodName"] = "google.cloud.run.v2.Services.DeleteService"
    elif change == "resource":
        p["resourceName"] = "foreign"
    elif change == "ack":
        v["native_mutation_acknowledged"] = True
    elif change == "failure":
        f = copy.deepcopy(c.failure)
        f["diagnostic"]["http_status"] = 503
        parent["failure_sha256"] = private(c.failure_path, f)
    elif change == "api_receipt":
        parent["api_route_sha256"] = "0" * 64
    if change not in {"failure", "api_receipt"}:
        parent["rejection_sha256"] = private(c.rejection_path, v)
    with pytest.raises(op.rolling.RollingDenied):
        op.read_parent(parent)
    assert len(c.fixture.writes) == 2


@pytest.mark.parametrize(
    "change", ["api", "analysis", "coordinator", "old_revision", "new_revision"]
)
def test_current_native_preimage_and_absence_guard(prepared, change):
    c = prepared
    if change in {"api", "analysis", "coordinator"}:
        name = {"api": op.API, "analysis": op.ANALYSIS, "coordinator": op.COORDINATOR}[change]
        c.fixture.states[name]["etag"] = "drift"
    else:
        old = next(p for p in c.old.plan.services if p.service == op.COORDINATOR)
        name = old.revision if change == "old_revision" else c.plan.coordinator.revision
        c.fixture.revisions[(op.COORDINATOR, name)] = {"uid": "unexpected"}
    with pytest.raises(op.rolling.RollingDenied):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 2


def test_lost_ack_no_replay(prepared):
    c = prepared
    c.fixture.lose_ack = True
    with pytest.raises(RuntimeError):
        op.execute("close", c.plan, c.boundary, c.journal)
    for mode in ("close", "observe", "reconcile"):
        with pytest.raises(Exception, match="missing_or_changed_native_ack"):
            op.execute(mode, c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 3


def test_pending_ack_read_only_reconciliation_then_explicit_route(prepared):
    c = prepared
    c.fixture.pending = True
    assert (
        op.execute("close", c.plan, c.boundary, c.journal)["status"]
        == "same_native_operation_pending"
    )
    assert len(c.fixture.writes) == 3
    for v in c.fixture.operations.values():
        v["done"] = True
    c.fixture.pending = False
    assert not op.execute("reconcile", c.plan, c.boundary, c.journal)["new_admission_closed"]
    assert len(c.fixture.writes) == 3
    assert op.execute("close", c.plan, c.boundary, c.journal)["new_admission_closed"]
    assert len(c.fixture.writes) == 4


def test_health_failure_does_not_route(prepared):
    c = prepared
    c.fixture.fail_health = True
    with pytest.raises(RuntimeError):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 3
    assert (
        c.fixture.states[op.COORDINATOR]["trafficStatuses"][0]["revision"]
        != c.plan.coordinator.revision
    )


@pytest.mark.parametrize("parent", [False, True])
def test_operator_pause_blocks_only_new_mutations(prepared, parent):
    c = prepared
    d = Path(c.parent["journal"]) if parent else c.journal.directory
    (d / "operator.pause-requested").touch(mode=0o600)
    with pytest.raises(Exception, match="pause_requested"):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 2
    assert not op.execute("observe", c.plan, c.boundary, c.journal)["new_admission_closed"]


def test_writer_refuses_api_and_analysis():
    b = object.__new__(op.NativeBoundary)
    for s in (op.API, op.ANALYSIS):
        with pytest.raises(Exception, match="coordinator_only"):
            b.patch(s, {}, "traffic")


@pytest.mark.parametrize(
    "flag,value",
    [("RESUME_DIRECT_UPLOAD_ENABLED", "false"), ("RESUME_UPLOAD_ADMISSION_ENABLED", "true")],
)
def test_original_api_admission_and_saved_read_flags_cannot_drift(prepared, flag, value):
    c = prepared
    env = op.rolling.environment(c.fixture.states[op.API]["template"])
    env[flag] = {"name": flag, "value": value}
    c.fixture.states[op.API]["template"]["containers"][0]["env"] = list(env.values())
    with pytest.raises(op.rolling.RollingDenied):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 2


@pytest.mark.parametrize("parent", [False, True])
def test_dangling_pause_marker_fails_closed(prepared, parent):
    c = prepared
    directory = Path(c.parent["journal"]) if parent else c.journal.directory
    (directory / "operator.pause-requested").symlink_to(directory / "missing-target")
    with pytest.raises(Exception, match="pause_requested"):
        op.execute("close", c.plan, c.boundary, c.journal)
    assert len(c.fixture.writes) == 2


def test_fresh_native_audit_exact_selected_read_only_projection(prepared):
    c = prepared
    b = object.__new__(op.NativeBoundary)
    calls = []
    selected = c.rejection["native_audit_entries"]

    def read(*args):
        calls.append(args)
        return json.dumps(selected).encode()

    b.observer = SimpleNamespace(_command=read)
    b.verify_rejection(c.parent)
    assert len(calls) == 1 and calls[0][:3] == ("gcloud", "logging", "read")
    assert "--limit=2" in calls[0] and any(
        v.startswith("--format=json(timestamp,insertId,") for v in calls[0]
    )
    b.observer = SimpleNamespace(_command=lambda *_: b"[]")
    with pytest.raises(Exception, match="fresh_native_rejection_required"):
        b.verify_rejection(c.parent)


@pytest.mark.parametrize(
    "message",
    [
        "private-token-and-request-body",
        "native_admission_completion_private\nprivate-token-and-request-body",
    ],
)
def test_cli_failure_never_emits_exception_text(tmp_path, monkeypatch, capsys, message):
    import sys

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "completion",
            "observe",
            "--plan",
            str(tmp_path / "plan"),
            "--journal",
            str(tmp_path / "journal"),
        ],
    )
    monkeypatch.setattr(op, "NativeBoundary", lambda: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(op.restore, "_private_read", lambda *_: b"{}")
    monkeypatch.setattr(op, "load", lambda *_: None)
    monkeypatch.setattr(op.owned, "Journal", lambda *_: None)

    def denied(*_):
        raise RuntimeError(message)

    monkeypatch.setattr(op, "execute", denied)
    assert op.main() == 1
    result = capsys.readouterr().out
    assert "private-token" not in result and "request-body" not in result
    assert "fixed_code" not in json.loads(result)
