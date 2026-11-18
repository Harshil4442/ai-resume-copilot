"""Synthetic native API/SQL ports; never Neon credentials or resource writes."""
from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from infra.gcp import schema27_provider_restore as restore

QA = "br-r03-test"
HELD = "br-r03-preserved"
ENDPOINT = "ep-r03-test"
HOST = ENDPOINT + ".c-8.us-east-2.aws.neon.tech"


@pytest.fixture
def plan():
    return restore.RestorePlan("a" * 32, "b" * 40, "c" * 40,
        tuple((name, "d" * 64) for name in restore.SOURCES), datetime.now(UTC).isoformat())


class Api:
    def __init__(self, plan):
        self.plan = plan
        self.writes = []
        self.unknown = None
        self.branches = [{"id": restore.PARENT, "project_id": restore.PROJECT, "name": "main",
                          "default": True, "protected": False}]
        self.endpoints = [{"id": restore.PRODUCTION_ENDPOINT, "project_id": restore.PROJECT,
                           "branch_id": restore.PARENT, "host": "production.invalid"}]

    def request(self, method, path, body=None):
        if method == "GET":
            if path == restore.PREFIX:
                return {"project": {"id": restore.PROJECT, "org_id": restore.manager.ORG,
                    "region_id": "aws-us-east-2", "pg_version": 17, "history_retention_seconds": 21600,
                    "owner": {"subscription_type": "free_v3", "branches_limit": 10}}}
            key = path.rsplit("/", 1)[1]
            return {key: copy.deepcopy(getattr(self, key))}
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == "POST" and path.endswith("/branches"):
            branch = body["branch"]
            self.branches.append({**copy.deepcopy(branch), "id": QA, "project_id": restore.PROJECT,
                                  "default": False, "created_at": datetime.now(UTC).isoformat()})
            self.endpoints.append({**copy.deepcopy(body["endpoints"][0]), "id": ENDPOINT,
                "project_id": restore.PROJECT, "branch_id": QA, "host": HOST, "suspend_timeout_seconds": 300})
            result = {"branch": self.branches[-1], "operations": [],
                      "connection_uris": [{"uri": "PRIVATE_MUST_NOT_BE_RECORDED"}]}
        elif method == "POST":
            assert path == restore.PREFIX + "/branches/" + QA + "/restore"
            self.branches.append({"id": HELD, "project_id": restore.PROJECT, "name": self.plan.held_name,
                "parent_id": restore.PARENT, "default": False, "protected": False,
                "created_at": datetime.now(UTC).isoformat()})
            branch = next(row for row in self.branches if row["id"] == QA)
            branch["parent_id"] = HELD
            result = {"branch": branch, "operations": []}
        else:
            kind, resource = path.split("/")[-2:]
            setattr(self, kind, [row for row in getattr(self, kind) if row["id"] != resource])
            result = {"operations": []}
        if self.unknown == method:
            raise restore.RestoreDenied("r03_synthetic_lost_response")
        return copy.deepcopy(result)


@dataclass(frozen=True)
class Snapshot:
    pin: dict
    checkpoint_lsn: str = "0/1234"
    checkpoint_utc: str = ""
    digest: str = "e" * 64

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


@dataclass(frozen=True)
class Marker:
    checkpoint_sha256: str

    @classmethod
    def from_dict(cls, value):
        return cls(**value)


@pytest.fixture
def ports(monkeypatch):
    calls = []
    pin = SimpleNamespace(from_dict=lambda value: SimpleNamespace(**value))
    def capture(actual):
        calls.append("capture")
        return Snapshot(vars(actual) if not hasattr(actual, "__dataclass_fields__") else restore.asdict(actual),
                        checkpoint_utc=datetime.now(UTC).isoformat())
    monkeypatch.setattr(restore.clone, "capture", capture)
    monkeypatch.setattr(restore.clone, "install_marker", lambda actual, baseline:
        (calls.append("marker"), Marker(baseline.digest))[1])
    monkeypatch.setattr(restore.clone, "verify_marker", lambda *_: True)
    monkeypatch.setattr(restore.clone, "compare_restore", lambda baseline, restored, marker:
        {"status": "owned_restore_verified", "unchanged_application_data": True})
    monkeypatch.setattr(restore.clone, "CloneSnapshot", Snapshot)
    monkeypatch.setattr(restore.clone, "CloneMarker", Marker)
    # Preserve the genuine pin constructor for initial Create and only replace its reader.
    monkeypatch.setattr(restore.clone.ClonePin, "from_dict", pin.from_dict)
    monkeypatch.setattr(restore, "preflight", lambda plan, api: restore.inventory(api))
    return calls


def test_complete_original_lifecycle_keeps_production_and_cleans_child_before_parent(plan, ports, tmp_path):
    owner, api = restore.Journal(tmp_path / "owned", plan, create=True), Api(plan)
    original = copy.deepcopy((api.branches, api.endpoints))
    with owner.lock():
        restore.create(owner, api)
        result = restore.restore(owner, api)
        restore.cleanup(owner, api)
    assert result["unchanged_application_data"] and ports == ["capture", "marker", "capture"]
    assert (api.branches, api.endpoints) == original
    assert [path for method, path, _ in api.writes if method == "DELETE"] == [
        restore.PREFIX + "/endpoints/" + ENDPOINT, restore.PREFIX + "/branches/" + QA,
        restore.PREFIX + "/branches/" + HELD]
    assert owner.read("cleanup.verified")["owned_resources_absent"]
    assert "PRIVATE_MUST_NOT_BE_RECORDED" not in "".join(path.read_text() for path in owner.path.glob("*.json"))


@pytest.mark.parametrize("phase", ["create", "restore"])
def test_persisted_unknown_POST_is_never_resent_and_is_reconciled_read_only(plan, ports, tmp_path, phase):
    owner, api = restore.Journal(tmp_path / "owned", plan, create=True), Api(plan)
    if phase == "restore":
        restore.create(owner, api)
    api.unknown = "POST"
    action = restore.create if phase == "create" else restore.restore
    with pytest.raises(restore.RestoreDenied, match="lost_response"):
        action(owner, api)
    sent = len(api.writes)
    report = restore.reconcile(owner, api)
    assert report["automatic_replay"] is False and len(api.writes) == sent
    with pytest.raises((restore.RestoreDenied, FileExistsError)):
        action(owner, api)
    assert len(api.writes) == sent
    api.unknown = None
    # Lost Create ACK permits cleanup-only native ownership, never restore/adoption.
    restore.cleanup(owner, api)
    assert restore.owned_resources(plan, owner, restore.inventory(api)) == ([], [])
    assert owner.read(phase + ".acknowledged") is None


def test_unknown_DELETE_never_resends_same_resource_and_absence_can_finish_cleanup(plan, ports, tmp_path):
    owner, api = restore.Journal(tmp_path / "owned", plan, create=True), Api(plan)
    restore.create(owner, api)
    api.unknown = "DELETE"
    with pytest.raises(restore.RestoreDenied, match="lost_response"):
        restore.cleanup(owner, api)
    first = list(api.writes)
    assert all(row["id"] != ENDPOINT for row in restore.inventory(api)["endpoints"])
    assert restore.reconcile(owner, api)["provider_mutations"] == 0
    api.unknown = None
    restore.cleanup(owner, api)
    assert api.writes[:len(first)] == first
    assert sum(path.endswith("/endpoints/" + ENDPOINT) for method, path, _ in api.writes if method == "DELETE") == 1


@pytest.mark.parametrize(("method", "path", "body"), [
    ("DELETE", restore.PREFIX + "/branches/" + restore.PARENT, None),
    ("DELETE", restore.PREFIX + "/endpoints/" + restore.PRODUCTION_ENDPOINT, None),
    ("POST", restore.PREFIX + "/branches/" + restore.PARENT + "/restore", {}),
    ("POST", "/projects/another-project/branches", {}),
    ("POST", restore.PREFIX + "/branches", {"branch": {"parent_id": "foreign"}}),
])
def test_native_adapter_denies_production_foreign_or_unbounded_mutations_before_manager(
    monkeypatch, method, path, body,
):
    monkeypatch.setattr(restore.manager, "_check_manager", lambda: pytest.fail("Must refuse before manager"))
    with pytest.raises(restore.RestoreDenied):
        restore.NativeNeon().request(method, path, body)


@pytest.mark.parametrize("drift", ["default", "old_id", "wrong_parent", "larger_compute", "second_endpoint"])
def test_cleanup_refuses_unowned_or_expanded_resources_without_DELETE(plan, ports, tmp_path, drift):
    owner, api = restore.Journal(tmp_path / "owned", plan, create=True), Api(plan)
    restore.create(owner, api)
    if drift == "default":
        api.branches[-1]["default"] = True
    elif drift == "old_id":
        api.branches[-1]["id"] = restore.PARENT
    elif drift == "wrong_parent":
        api.branches[-1]["parent_id"] = "br-foreign"
    elif drift == "larger_compute":
        api.endpoints[-1]["autoscaling_limit_max_cu"] = 2
    else:
        api.endpoints.append({**api.endpoints[-1], "id": "ep-extra"})
    sent = len(api.writes)
    with pytest.raises(restore.RestoreDenied):
        restore.cleanup(owner, api)
    assert len(api.writes) == sent


def test_partial_inventory_and_changed_operation_cannot_authorize_progress(plan):
    api = Api(plan)
    api.request = lambda *_: {"branches": [], "pagination": {"next": "more"}}
    with pytest.raises(restore.RestoreDenied, match="incomplete_inventory"):
        restore.inventory(api)
    api.request = lambda *_: {"operation": {"id": "foreign", "project_id": restore.PROJECT, "status": "finished"}}
    with pytest.raises(restore.RestoreDenied, match="operation_changed"):
        restore.wait_operations(api, [{"id": "a" * 36, "project_id": restore.PROJECT}], float("inf"))


def test_journal_rejects_symlink_plan_drift_and_parallel_owner(plan, tmp_path):
    owner = restore.Journal(tmp_path / "owned", plan, create=True)
    with owner.lock(), pytest.raises(restore.RestoreDenied, match="owner_active"), owner.lock():
        pass
    outside = tmp_path / "outside"
    outside.write_text("{}")
    (owner.path / "spoof.json").symlink_to(outside)
    with pytest.raises(OSError):
        owner.read("spoof")
    data = json.loads(restore.canonical(restore.asdict(plan)))
    data["nonce"] = "f" * 32
    (owner.path / "binding.json").write_text(json.dumps(data))
    with pytest.raises(restore.RestoreDenied, match="journal_binding"):
        restore.Journal(owner.path, plan)


@pytest.mark.parametrize("parent_gone", [False, True])
def test_independent_watchdog_cleans_on_deadline_or_parent_pipe_EOF(plan, ports, tmp_path, parent_gone):
    owner, api = restore.Journal(tmp_path / "owned", plan, create=True), Api(plan)
    restore.create(owner, api)
    if not parent_gone:
        execution = owner.path / "execution.json"
        value = json.loads(execution.read_bytes())
        value["deadline"] = "2000-01-01T00:00:00+00:00"
        execution.write_text(json.dumps(value))
    reader, writer = os.pipe()
    try:
        if parent_gone:
            os.close(writer)
            writer = None
        restore.watchdog(owner, api, reader)
    finally:
        os.close(reader)
        if writer is not None:
            os.close(writer)
    assert restore.owned_resources(plan, owner, restore.inventory(api)) == ([], [])


@pytest.mark.parametrize("ready", [False, True])
def test_run_requires_independent_watchdog_ready_before_any_provider_write(
    plan, ports, tmp_path, monkeypatch, ready,
):
    api = Api(plan)
    plan_path = tmp_path / "plan.json"
    restore.write_new(plan_path, restore.asdict(plan))
    monkeypatch.setattr(restore.RestorePlan, "verify_source", lambda *_: None)
    monkeypatch.setattr(restore, "NativeNeon", lambda: api)

    class Child:
        def __init__(self, args, **kwargs):
            assert api.writes == [] and kwargs["start_new_session"] is True
            assert set(kwargs["pass_fds"]) == {int(args[args.index("--watchdog-fd") + 1]),
                int(args[args.index("--watchdog-ready-fd") + 1])}
            descriptor = int(args[args.index("--watchdog-ready-fd") + 1])
            os.write(descriptor, b"R" if ready else b"X")
        def wait(self, **_kwargs):
            return 0

    monkeypatch.setattr(restore.subprocess, "Popen", Child)
    if ready:
        result = restore.run(plan_path, restore.hashlib.sha256(plan_path.read_bytes()).hexdigest(), tmp_path / "owned")
        assert result["cleanup_verified"] and result["full_R03_qualified"] is False
        assert len(api.writes) == 5
    else:
        with pytest.raises(restore.RestoreDenied, match="watchdog_not_ready"):
            restore.run(plan_path, restore.hashlib.sha256(plan_path.read_bytes()).hexdigest(), tmp_path / "owned")
        assert api.writes == []


def test_optional_provider_TTL_never_changes_compute_or_cleanup_requirements(plan, ports, tmp_path):
    from dataclasses import replace

    for index, wanted in enumerate((False, True)):
        selected = replace(plan, request_provider_expiry=wanted)
        owner, api = restore.Journal(tmp_path / str(index), selected, create=True), Api(selected)
        restore.create(owner, api)
        body = owner.read("create.started")["request"]["body"]
        assert ("expires_at" in body["branch"]) is wanted
        assert body["endpoints"] == [{"type": "read_write", "autoscaling_limit_min_cu": 0.25,
            "autoscaling_limit_max_cu": 0.25}]
        assert owner.read("expiry.observed")["cleanup_watchdog_required"] is True
        restore.cleanup(owner, api)


def test_records_publish_atomically_after_complete_private_write(tmp_path, monkeypatch):
    target = tmp_path / "execution.json"
    original = restore.os.replace
    observations = []

    def publish(temporary, destination):
        assert not target.exists()
        assert json.loads(restore.Path(temporary).read_bytes()) == {"whole": "record"}
        original(temporary, destination)
        observations.append(restore.private_json(target))

    monkeypatch.setattr(restore.os, "replace", publish)
    restore.write_new(target, {"whole": "record"})
    assert observations == [{"whole": "record"}]
    with pytest.raises(FileExistsError):
        restore.write_new(target, {"replace": "forbidden"})
    assert restore.private_json(target) == {"whole": "record"}


@pytest.mark.parametrize("actual_parent", [12345, 54321])
def test_watchdog_cancels_only_original_live_parent_before_cleanup(
    plan, tmp_path, monkeypatch, actual_parent,
):
    owner = restore.Journal(tmp_path / "owned", plan, create=True)
    owner.write("execution", {"deadline": "2000-01-01T00:00:00+00:00", "owner_pid": 12345})
    calls = []
    monkeypatch.setattr(restore.os, "getppid", lambda: actual_parent)
    monkeypatch.setattr(restore.os, "kill", lambda pid, number: calls.append(("cancel", pid, number)))
    monkeypatch.setattr(restore, "cleanup", lambda *_: calls.append("cleanup"))
    reader, writer = os.pipe()
    try:
        restore.watchdog(owner, object(), reader, parent_pid=12345)
    finally:
        os.close(reader)
        os.close(writer)
    expected = [("cancel", 12345, restore.signal.SIGTERM)] if actual_parent == 12345 else []
    assert calls == [*expected, "cleanup"]


def test_parent_exit_during_cancellation_still_reaches_cleanup(plan, tmp_path, monkeypatch):
    owner = restore.Journal(tmp_path / "owned", plan, create=True)
    owner.write("execution", {"deadline": "2000-01-01T00:00:00+00:00", "owner_pid": 12345})
    calls = []
    monkeypatch.setattr(restore.os, "getppid", lambda: 12345)
    monkeypatch.setattr(restore.os, "kill", lambda *_: (_ for _ in ()).throw(ProcessLookupError()))
    monkeypatch.setattr(restore, "cleanup", lambda *_: calls.append("cleanup"))
    reader, writer = os.pipe()
    try:
        restore.watchdog(owner, object(), reader, parent_pid=12345)
    finally:
        os.close(reader)
        os.close(writer)
    assert calls == ["cleanup"]


def test_actual_local_watchdog_cancels_its_owner_and_acquires_released_lock(tmp_path):
    import signal
    import subprocess
    import sys

    program = r'''
import json,os,signal,sys,time
from datetime import UTC,datetime
from pathlib import Path
root,path=map(Path,sys.argv[1:])
sys.path[:0]=[str(root),str(root/'backend')]
from infra.gcp import schema27_provider_restore as r
def no_network(event,args):
    assert event!='socket.__new__','No network permitted'
sys.addaudithook(no_network)
plan=r.RestorePlan('a'*32,'b'*40,'c'*40,tuple((n,'d'*64) for n in r.SOURCES),datetime.now(UTC).isoformat())
owner=r.Journal(path,plan,create=True)
owner.write('execution',{'deadline':'2000-01-01T00:00:00+00:00','owner_pid':os.getpid()})
class NoProvider:
    def request(self,method,path,body=None):
        assert method=='GET' and body is None
        return {path.rsplit('/',1)[1]:[]}
def cancelled(*_):raise r.RestoreDenied('r03_work_deadline_cancelled')
signal.signal(signal.SIGTERM,cancelled)
reader,writer=os.pipe()
try:
    with owner.lock() as inherited_lock:
        child=os.fork()
        if child==0:
            os.close(writer)
            os.close(inherited_lock)
            try:r.watchdog(owner,NoProvider(),reader,parent_pid=os.getppid())
            finally:os.close(reader)
            os._exit(0)
        os.close(reader)
        try:time.sleep(5)
        finally:owner.write('owner-finally',{'reached':True})
except r.RestoreDenied as error:
    assert str(error)=='r03_work_deadline_cancelled'
finally:os.close(writer)
pid,status=os.waitpid(child,0)
assert pid==child and os.waitstatus_to_exitcode(status)==0
assert owner.read('owner-finally')=={'reached':True}
assert owner.read('cleanup.verified')['owned_resources_absent'] is True
print(json.dumps({'cancelled':True,'owner_finally':True,'cleanup_lock_acquired':True,'provider_writes':0}))
'''
    process = subprocess.Popen([sys.executable, "-I", "-B", "-c", program, str(restore.ROOT), str(tmp_path / "owned")],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
    try:
        raw, error = process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=3)
        pytest.fail("Owned local watchdog exceeded its bound")
    assert process.returncode == 0, error.decode()
    assert json.loads(raw) == {"cancelled": True, "owner_finally": True,
                               "cleanup_lock_acquired": True, "provider_writes": 0}
