"""API-only rehearsal over controlled metadata; no SDK/auth/SQL/provider calls."""
from __future__ import annotations

import copy
import json
import shutil
import subprocess
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest
from infra.gcp import schema27_api_identity_operator as op
from infra.gcp import schema27_api_identity_readback as readback
from test_schema27_api_identity_readback import native_service

API = readback.API
CANDIDATE = API + "-identity-rehearsal"
ROLLBACK = API + "-identity-return"
TAG = "identity-review"


class ControlledBoundary:
    """The local port records every requested mutation and preserves old revisions."""

    def __init__(self):
        self.services, self.revisions, self.calls, self.writes, self.operations = {}, {}, [], [], {}
        self.pending, self.lose_ack, self.persist_unknown, self.operation_error = False, False, True, False
        for index, name in enumerate(readback.SERVICES, 1):
            account = readback.COORDINATOR_ACCOUNT if name == readback.COORDINATOR else readback.rolling.RUNTIME
            service, revisions = native_service(name, index, account)
            for template in [service["template"], *revisions.values()]:
                for row in template["containers"][0]["env"]:
                    if row["name"] == "APP_RELEASE":
                        row["value"] = op.APPLICATION_RELEASE
            self.services[name], self.revisions[name] = service, revisions
        self.policies = {name: {"version": 1, "etag": "iam-before-" + name, "bindings": [
            {"role": "roles/run.invoker", "members": ["serviceAccount:" + readback.rolling.RUNTIME,
                "serviceAccount:" + readback.COORDINATOR_ACCOUNT]}]}
            for name in (*readback.SERVICES, *readback.DOCUMENTS)}
        self.identities = {
            readback.rolling.RUNTIME: {"email": readback.rolling.RUNTIME, "uid": "1" * 21,
                "enabled": True, "keyless": True},
            op.NEW_API_EMAIL: {"email": op.NEW_API_EMAIL, "uid": "109605218132077158800",
                "enabled": True, "keyless": True},
        }

    def service(self, name):
        self.calls.append(("service", name))
        return copy.deepcopy(self.services[name])

    def revision(self, name, revision):
        self.calls.append(("revision", name, revision))
        return copy.deepcopy(self.revisions[name].get(revision))

    def iam(self, name):
        self.calls.append(("iam", name))
        return copy.deepcopy(self.policies[name])

    def identity(self, email):
        self.calls.append(("identity", email))
        return copy.deepcopy(self.identities[email])

    def approve_document_invokers(self):
        # A separate external IAM action is modeled, never performed by the operator.
        for name in readback.DOCUMENTS:
            policy = self.policies[name]
            policy["etag"] = "iam-after-" + name
            policy["bindings"][0]["members"].append("serviceAccount:" + op.NEW_API_EMAIL)

    def patch(self, body, mask):
        assert body["name"] == readback.rolling.PARENT + "/services/" + API
        assert mask in {"template,traffic", "traffic"}
        assert set(body) == ({"name", "etag", "template", "traffic"}
                             if mask == "template,traffic" else {"name", "etag", "traffic"})
        assert body["etag"] == self.services[API]["etag"]
        self.writes.append((copy.deepcopy(body), mask))
        if not self.lose_ack or self.persist_unknown:
            current = self.services[API]
            if "template" in body:
                template = copy.deepcopy(body["template"])
                revision = template["revision"]
                assert revision not in self.revisions[API], "Immutable revisions cannot be overwritten"
                self.revisions[API][revision] = template | {
                    "name": current["name"] + "/revisions/" + revision,
                    "service": current["name"], "uid": str(UUID(int=1000 + len(self.writes))),
                    "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
                    "containerStatuses": [{"name": "app"}],
                }
                current["template"] = template
                current["latestCreatedRevision"] = current["latestReadyRevision"] = revision
            current["generation"] = str(int(current["generation"]) + 1)
            current["observedGeneration"] = current["generation"]
            current["etag"] = "etag-" + current["generation"]
            current["traffic"] = copy.deepcopy(body["traffic"])
            current["trafficStatuses"] = copy.deepcopy(body["traffic"])
        operation = readback.rolling.PARENT + "/operations/" + str(UUID(int=2000 + len(self.writes)))
        self.operations[operation] = {"name": operation, "done": not self.pending}
        if self.operation_error:
            self.operations[operation]["error"] = {"native_failure": True}
        if self.lose_ack:
            raise RuntimeError("Synthetic acknowledgement loss; no raw response may be persisted")
        return {"name": operation}

    def operation(self, name):
        self.calls.append(("operation", name))
        return copy.deepcopy(self.operations[name])


@pytest.fixture
def controlled(monkeypatch):
    state = {"release": op.APPLICATION_RELEASE, "tree": "c" * 40,
             "files": {"infra/gcp/schema27_api_identity_operator.py": "d" * 64}}
    monkeypatch.setattr(op, "source_state", lambda: copy.deepcopy(state))
    return ControlledBoundary(), state


def captured(controlled, tmp_path, *, tag=TAG):
    boundary, state = controlled
    plan = op.capture(boundary, CANDIDATE, ROLLBACK, candidate_tag=tag,
                      now=datetime.now(UTC), journal_directory=tmp_path / "original")
    journal = op.Journal(tmp_path / "original", plan, create=True)
    return plan, boundary, journal, state


def test_capture_and_json_roundtrip_keep_distinct_principals_and_no_raw_environment(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    assert plan.transition.old_api.uid != plan.transition.new_api.uid
    assert plan.transition.new_api.email == op.NEW_API_EMAIL
    assert plan.transition.candidate_tag == TAG
    assert plan.rollback_revision == ROLLBACK
    assert op.Plan.from_dict(json.loads(json.dumps(asdict(plan)))).digest == plan.digest
    assert boundary.writes == []
    encoded = json.dumps(asdict(plan))
    assert "synthetic-value-must-not-appear" not in encoded and "synthetic-db" not in encoded
    assert journal.read("stage.started") is None


@pytest.mark.parametrize("drift", ["release", "same_uid", "new_disabled", "new_has_keys",
                                  "candidate_exists", "rollback_exists", "checkout", "migrate"])
def test_capture_refuses_unreviewed_source_principals_revisions_or_flags(controlled, tmp_path, drift):
    boundary, _ = controlled
    if drift == "release":
        boundary.services[API]["template"]["containers"][0]["env"][0]["value"] = "f" * 40
    elif drift == "same_uid":
        boundary.identities[op.NEW_API_EMAIL]["uid"] = boundary.identities[readback.rolling.RUNTIME]["uid"]
    elif drift in {"new_disabled", "new_has_keys"}:
        boundary.identities[op.NEW_API_EMAIL]["enabled" if drift == "new_disabled" else "keyless"] = False
    elif drift in {"candidate_exists", "rollback_exists"}:
        boundary.revisions[API][CANDIDATE if drift == "candidate_exists" else ROLLBACK] = {"existing": True}
    else:
        setting = "RAZORPAY_CHECKOUT_ENABLED" if drift == "checkout" else "AUTO_DB_MIGRATE"
        for template in [boundary.services[API]["template"], *boundary.revisions[API].values()]:
            next(row for row in template["containers"][0]["env"] if row["name"] == setting)["value"] = "true"
    with pytest.raises(readback.rolling.RollingDenied):
        op.capture(boundary, CANDIDATE, ROLLBACK, candidate_tag=TAG,
                   journal_directory=tmp_path / "original")
    assert boundary.writes == []


@pytest.mark.parametrize("tag", [None, TAG])
def test_stage_is_api_only_zero_traffic_and_preserves_every_prior_revision(controlled, tmp_path, tag):
    plan, boundary, journal, _ = captured(controlled, tmp_path, tag=tag)
    before = copy.deepcopy(boundary.services)
    retained = copy.deepcopy(boundary.revisions)
    boundary.approve_document_invokers()
    op.stage(plan, boundary, journal)
    assert len(boundary.writes) == 1
    body, mask = boundary.writes[0]
    assert mask == "template,traffic" and body["template"]["serviceAccount"] == op.NEW_API_EMAIL
    assert body["template"]["revision"] == CANDIDATE
    assert {k: v for k, v in body["template"].items() if k not in {"serviceAccount", "revision"}} == {
        k: v for k, v in before[API]["template"].items() if k not in {"serviceAccount", "revision"}}
    candidate_traffic = [row for row in body["traffic"] if row["revision"] == CANDIDATE]
    assert candidate_traffic == ([] if tag is None else [
        {"type": readback.REVISION_TRAFFIC, "revision": CANDIDATE, "tag": tag}])
    assert all(row.get("percent", 0) == 0 for row in candidate_traffic)
    assert {name: value for name, value in boundary.services.items() if name != API} == {
        name: value for name, value in before.items() if name != API}
    for name, revisions in retained.items():
        assert all(boundary.revisions[name][key] == row for key, row in revisions.items())
    for _ in range(2):
        op.observe(plan, boundary, journal)
        op.reconcile(plan, boundary, journal)
    assert len(boundary.writes) == 1


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_stage_ack_never_replays_and_only_reconciles_observed_effect(controlled, tmp_path, persisted):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    boundary.lose_ack, boundary.persist_unknown = True, persisted
    with pytest.raises(RuntimeError, match="Synthetic acknowledgement loss"):
        op.stage(plan, boundary, journal)
    assert len(boundary.writes) == 1
    with pytest.raises(readback.rolling.RollingDenied, match="started_reconcile_only"):
        op.stage(plan, boundary, journal)
    assert len(boundary.writes) == 1
    if persisted:
        op.reconcile(plan, boundary, journal)
        assert journal.read("stage.checked")["operation_ack_retained"] is False
    else:
        with pytest.raises(readback.rolling.RollingDenied):
            op.reconcile(plan, boundary, journal)
        assert journal.read("stage.checked") is None
    assert len(boundary.writes) == 1


def test_known_pending_lro_keeps_original_handle_and_never_starts_rollback(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    boundary.pending = True
    result = op.stage(plan, boundary, journal)
    assert result["status"] == "same_native_operation_pending"
    assert len(boundary.writes) == 1
    with pytest.raises(readback.rolling.RollingDenied, match="checked_receipt_changed"):
        op.rollback(plan, boundary, journal)
    assert len(boundary.writes) == 1
    for operation in boundary.operations.values():
        operation["done"] = True
    op.reconcile(plan, boundary, journal)
    assert len(boundary.writes) == 1 and journal.read("stage.checked") is not None


@pytest.mark.parametrize("drift", ["worker", "coordinator", "worker_iam", "document_iam", "template", "etag"])
def test_prewrite_drift_never_widens_iam_or_sends_a_patch(controlled, tmp_path, drift):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    if drift in {"worker", "coordinator"}:
        name = readback.WORKERS[0] if drift == "worker" else readback.COORDINATOR
        boundary.services[name]["generation"] = "99"
    elif drift == "worker_iam":
        boundary.policies[readback.WORKERS[0]]["bindings"][0]["members"].append("serviceAccount:" + op.NEW_API_EMAIL)
    elif drift == "document_iam":
        boundary.policies[readback.DOCUMENTS[0]]["bindings"][0]["members"].append("allUsers")
    elif drift == "template":
        boundary.services[API]["template"]["containers"][0]["env"].append({"name": "UNREVIEWED", "value": "true"})
    else:
        boundary.services[API]["etag"] = "foreign-etag"
    with pytest.raises(readback.rolling.RollingDenied):
        op.stage(plan, boundary, journal)
    assert boundary.writes == []


def test_rollback_creates_old_identity_revision_then_moves_all_original_aliases(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    before, revisions = copy.deepcopy(boundary.services), copy.deepcopy(boundary.revisions)
    boundary.approve_document_invokers()
    op.stage(plan, boundary, journal)
    candidate = copy.deepcopy(boundary.revisions[API][CANDIDATE])
    op.rollback(plan, boundary, journal)
    assert [mask for _, mask in boundary.writes] == ["template,traffic", "template,traffic", "traffic"]
    rollback_template = boundary.writes[1][0]["template"]
    assert rollback_template["revision"] == ROLLBACK
    assert rollback_template["serviceAccount"] == readback.rolling.RUNTIME
    assert {k: v for k, v in rollback_template.items() if k != "revision"} == {
        k: v for k, v in before[API]["template"].items() if k != "revision"}
    final = boundary.services[API]
    assert final["latestReadyRevision"] == ROLLBACK
    assert all(row["revision"] == ROLLBACK for row in final["trafficStatuses"])
    assert {row.get("tag") for row in final["trafficStatuses"] if "tag" in row} == {
        row["tag"] for row in before[API]["trafficStatuses"] if "tag" in row}
    assert sum(row.get("percent", 0) for row in final["trafficStatuses"]) == 100
    assert boundary.revisions[API][CANDIDATE] == candidate
    assert all(boundary.revisions[API][key] == row for key, row in revisions[API].items())
    op.reconcile(plan, boundary, journal)
    op.observe(plan, boundary, journal)
    assert len(boundary.writes) == 3


def test_unknown_rollback_template_ack_reconciles_without_starting_traffic(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    op.stage(plan, boundary, journal)
    boundary.lose_ack = True
    with pytest.raises(RuntimeError, match="Synthetic acknowledgement loss"):
        op.rollback(plan, boundary, journal)
    assert len(boundary.writes) == 2
    assert journal.read("rollback-template.ack") is None
    assert journal.read("rollback-traffic.started") is None
    with pytest.raises(readback.rolling.RollingDenied, match="started_reconcile_only"):
        op.rollback(plan, boundary, journal)
    op.reconcile(plan, boundary, journal)
    assert journal.read("rollback-template.checked")["operation_ack_retained"] is False
    assert journal.read("rollback-traffic.started") is None
    assert len(boundary.writes) == 2
    boundary.lose_ack = False
    op.rollback(plan, boundary, journal)
    assert len(boundary.writes) == 3
    assert all(row["revision"] == ROLLBACK for row in boundary.services[API]["traffic"])


def test_pending_template_then_unknown_traffic_ack_never_replays_either_phase(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    op.stage(plan, boundary, journal)
    boundary.pending = True
    result = op.rollback(plan, boundary, journal)
    assert result == {"status": "same_native_operation_pending", "phase": "rollback-template"}
    assert len(boundary.writes) == 2 and journal.read("rollback-traffic.started") is None
    ack = journal.read("rollback-template.ack")
    assert ack is not None
    assert op.reconcile(plan, boundary, journal) == result
    assert journal.read("rollback-template.ack") == ack and len(boundary.writes) == 2
    boundary.operations[ack["name"]]["done"] = True
    op.reconcile(plan, boundary, journal)
    assert len(boundary.writes) == 2 and journal.read("rollback-traffic.started") is None
    boundary.pending, boundary.lose_ack = False, True
    with pytest.raises(RuntimeError, match="Synthetic acknowledgement loss"):
        op.rollback(plan, boundary, journal)
    assert len(boundary.writes) == 3 and journal.read("rollback-traffic.ack") is None
    with pytest.raises(readback.rolling.RollingDenied, match="started_reconcile_only"):
        op.rollback(plan, boundary, journal)
    op.reconcile(plan, boundary, journal)
    assert journal.read("rollback-traffic.checked")["operation_ack_retained"] is False
    op.observe(plan, boundary, journal)
    assert len(boundary.writes) == 3


def test_failed_same_native_operation_is_read_only_and_never_becomes_checked(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    boundary.operation_error = True
    with pytest.raises(readback.rolling.RollingDenied, match="same_native_operation_failed"):
        op.stage(plan, boundary, journal)
    ack = journal.read("stage.ack")
    for _ in range(2):
        with pytest.raises(readback.rolling.RollingDenied, match="same_native_operation_failed"):
            op.reconcile(plan, boundary, journal)
    assert journal.read("stage.ack") == ack
    assert journal.read("stage.checked") is None and len(boundary.writes) == 1


def test_rewritten_checked_observation_cannot_authorize_unreviewed_rollback_template(controlled, tmp_path):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    op.stage(plan, boundary, journal)
    for template in (boundary.services[API]["template"], boundary.revisions[API][CANDIDATE]):
        env = template["containers"][0]["env"]
        if not any(row["name"] == "UNREVIEWED" for row in env):
            env.append({"name": "UNREVIEWED", "value": "true"})
    checked = journal.read("stage.checked")
    checked["observation"]["services"][API] = readback.project_service(
        API, boundary.services[API], boundary.revisions[API])
    checked["retained"] = {}
    (journal.directory / "stage.checked.json").write_text(json.dumps(checked))
    boundary.calls.clear()
    for action in (op.rollback, op.observe):
        with pytest.raises(readback.rolling.RollingDenied, match="api_template_diff"):
            action(plan, boundary, journal)
    assert boundary.calls == [] and len(boundary.writes) == 1


@pytest.mark.parametrize("tamper", ["generation", "request_hash"])
def test_started_metadata_is_validated_against_fixed_plan_before_native_access(controlled, tmp_path, tamper):
    plan, boundary, journal, _ = captured(controlled, tmp_path)
    boundary.approve_document_invokers()
    op.stage(plan, boundary, journal)
    started = journal.read("stage.started")
    if tamper == "generation":
        started["previous_generation"] += 1
    else:
        started["request_sha256"] = "f" * 64
        # Matching rewritten ACK/checked hashes must not become authority.
        for phase in ("ack", "checked"):
            value = journal.read("stage." + phase)
            value["request_sha256"] = started["request_sha256"]
            (journal.directory / ("stage." + phase + ".json")).write_text(json.dumps(value))
    (journal.directory / "stage.started.json").write_text(json.dumps(started))
    boundary.calls.clear()
    for action in (op.rollback, op.observe, op.reconcile):
        with pytest.raises(readback.rolling.RollingDenied, match="started_(changed|request_changed)"):
            action(plan, boundary, journal)
    assert boundary.calls == [] and len(boundary.writes) == 1


def test_original_journal_copy_and_source_change_refuse_before_native_access(controlled, tmp_path):
    plan, boundary, journal, state = captured(controlled, tmp_path)
    copied = tmp_path / "copy"
    shutil.copytree(journal.directory, copied)
    with pytest.raises(readback.rolling.RollingDenied, match="journal_(custody|location)"):
        op.Journal(copied, plan)
    copied_status = copied.stat()
    binding = journal.read("binding") | {"path": str(copied), "device": copied_status.st_dev,
                                         "inode": copied_status.st_ino}
    (copied / "binding.json").write_text(json.dumps(binding))
    with pytest.raises(readback.rolling.RollingDenied, match="journal_(custody|location)"):
        op.Journal(copied, plan)
    assert plan.journal_location == journal.location
    boundary.calls.clear()
    state["tree"] = "e" * 40
    with pytest.raises(readback.rolling.RollingDenied, match="source_changed"):
        plan.verify_source()
    with pytest.raises(readback.rolling.RollingDenied, match="source_changed"):
        op.stage(plan, boundary, journal)
    assert boundary.calls == [] and boundary.writes == []


def test_cli_rejects_changed_source_before_constructing_native_client(controlled, tmp_path, monkeypatch, capsys):
    plan, boundary, journal, state = captured(controlled, tmp_path)
    state["tree"] = "e" * 40
    monkeypatch.setattr(op, "NativeBoundary", lambda: pytest.fail("native client before source guard"))
    monkeypatch.setattr(sys, "argv", ["identity-operator", "stage", "--journal", str(journal.directory),
                                     "--plan-sha256", plan.digest])
    assert op.main() == 1
    assert json.loads(capsys.readouterr().out)["status"] == "api_identity_operator_refused"
    assert boundary.writes == []


def test_source_state_uses_executing_checkout_and_refuses_dirty_source(tmp_path, monkeypatch):
    source, foreign = tmp_path / "source", tmp_path / "foreign"
    source.mkdir()
    foreign.mkdir()
    for name in op.SOURCE_FILES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("controlled source " + name)
    monkeypatch.setattr(op, "ROOT", source)
    monkeypatch.chdir(foreign)
    calls, dirty = [], []

    def git(command, **kwargs):
        calls.append(command)
        assert command[:3] == ["/usr/bin/git", "-C", str(source)]
        assert kwargs["env"] == {"PATH": "/usr/bin:/bin"}
        assert kwargs["stdin"] is subprocess.DEVNULL and kwargs["timeout"] == 10
        action = command[3:]
        if action[0] == "status":
            output = " M owned.py" if dirty else ""
        elif action == ["rev-parse", "HEAD^{tree}"]:
            output = "c" * 40
        else:
            output = op.APPLICATION_RELEASE
        return subprocess.CompletedProcess(command, 0, stdout=output.encode(), stderr=b"")

    monkeypatch.setattr(op.subprocess, "run", git)
    observed = op.source_state()
    assert observed["release"] == op.APPLICATION_RELEASE and observed["tree"] == "c" * 40
    assert set(observed["files"]) == set(op.SOURCE_FILES)
    assert all(len(value) == 64 for value in observed["files"].values())
    dirty.append(True)
    before = len(calls)
    with pytest.raises(readback.rolling.RollingDenied, match="source_not_clean"):
        op.source_state()
    assert len(calls) == before + 1


def test_cli_help_does_not_load_credentials_or_open_database_or_network():
    code = """
import builtins,runpy,socket,sys
import psycopg
original = builtins.__import__
def guarded(name,*args,**kwargs):
    if name.startswith(('google.auth','google.cloud','app.database')):
        raise AssertionError('credential/database import forbidden: ' + name)
    return original(name,*args,**kwargs)
builtins.__import__ = guarded
def forbidden(*args,**kwargs):
    raise AssertionError('network forbidden')
socket.socket.connect = forbidden
psycopg.connect = forbidden
sys.path.insert(0,sys.argv[1])
sys.argv = ['identity-operator', '--help']
try:
    runpy.run_module('infra.gcp.schema27_api_identity_operator',run_name='__main__')
except SystemExit as error:
    assert error.code == 0
assert 'app.database' not in sys.modules and 'app.models' not in sys.modules
assert 'infra.gcp.rolling_native' not in sys.modules and 'rolling_native' not in sys.modules
"""
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(root)],
                            capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode()
    assert all(option not in result.stdout for option in
               (b"--root", b"--dsn", b"--promote", b"--grant", b"--token", b"--project"))
