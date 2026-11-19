"""Owned Free-plan Neon restore rehearsal; never production restore authority.

Plan is provider-free. Run owns at most two temporary branches and one fixed
0.25-CU endpoint. Unknown writes are observed, never automatically resent.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import select
import signal
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]

from infra.gcp import schema27_continuity_bootstrap as manager  # noqa: E402
from infra.gcp import schema27_restore_clone as clone  # noqa: E402

PROJECT = "divine-flower-30944770"
PARENT = "br-small-surf-bdqqplk8"
PRODUCTION_ENDPOINT = "ep-holy-sound-bdqtltxz"
PREFIX = "/projects/" + PROJECT
MAX_BYTES = 1024 * 1024
SOURCES = (
    "infra/gcp/schema27_provider_restore.py", "infra/gcp/schema27_restore_clone.py",
    "infra/gcp/schema27_continuity_bootstrap.py", "infra/gcp/schema27_continuity_scope.py",
    "backend/scripts/notification_schema27_contract.py",
)


class RestoreDenied(RuntimeError):
    pass


def need(value: Any, code: str) -> None:
    if not value:
        raise RestoreDenied("r03_" + code)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def pairs(items):
    value = dict(items)
    need(len(value) == len(items), "duplicate_json_key")
    return value


def stamp(value: str) -> datetime:
    need(type(value) is str, "timestamp")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        need(result.tzinfo is not None, "timestamp")
        return result
    except ValueError:
        raise RestoreDenied("r03_timestamp") from None


def git(*args: str) -> str:
    result = subprocess.run(["/usr/bin/git", "-c", "core.fsmonitor=false", "-C", str(ROOT), *args],
        stdin=subprocess.DEVNULL, capture_output=True, timeout=15, check=False,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "GIT_CONFIG_NOSYSTEM": "1",
             "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_NO_REPLACE_OBJECTS": "1", "GIT_NO_LAZY_FETCH": "1"})
    need(result.returncode == 0 and len(result.stdout) <= MAX_BYTES, "source_unavailable")
    return result.stdout.decode().strip()


@dataclass(frozen=True)
class RestorePlan:
    nonce: str
    release: str
    tree: str
    source_sha256: tuple[tuple[str, str], ...]
    created_at: str
    request_provider_expiry: bool = False
    version: int = 1
    kind: str = "hirewiz_neon_schema27_isolated_restore_v1"

    @property
    def qa_name(self):
        return "hirewiz-r03-qa-" + self.nonce

    @property
    def held_name(self):
        return "hirewiz-r03-held-" + self.nonce

    def validate(self):
        need(type(self) is RestorePlan and type(self.version) is int and self.version == 1
             and self.kind == "hirewiz_neon_schema27_isolated_restore_v1"
             and type(self.request_provider_expiry) is bool
             and type(self.nonce) is str and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
             and all(type(value) is str and re.fullmatch(r"[a-f0-9]{40}", value)
                     for value in (self.release, self.tree)), "plan_scope")
        need(type(self.source_sha256) is tuple and tuple(name for name, _ in self.source_sha256) == SOURCES
             and all(type(value) is str and re.fullmatch(r"[a-f0-9]{64}", value)
                     for _, value in self.source_sha256), "plan_source")
        stamp(self.created_at)

    @classmethod
    def from_dict(cls, value):
        need(type(value) is dict and set(value) == {field.name for field in fields(cls)}, "plan_shape")
        data = dict(value)
        need(type(data["source_sha256"]) is list and all(type(row) is list and len(row) == 2
             for row in data["source_sha256"]), "plan_shape")
        data["source_sha256"] = tuple(tuple(row) for row in data["source_sha256"])
        result = cls(**data)
        result.validate()
        return result

    def verify_source(self):
        self.validate()
        need(git("rev-parse", "HEAD") == self.release and git("rev-parse", "HEAD^{tree}") == self.tree
             and not git("status", "--porcelain", "--untracked-files=all"), "source_changed")
        need(tuple((name, hashlib.sha256((ROOT / name).read_bytes()).hexdigest()) for name in SOURCES)
             == self.source_sha256, "source_changed")


def capture_plan(*, request_provider_expiry=False) -> RestorePlan:
    import secrets

    plan = RestorePlan(secrets.token_hex(16), git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}"),
        tuple((name, hashlib.sha256((ROOT / name).read_bytes()).hexdigest()) for name in SOURCES),
        datetime.now(UTC).isoformat(), request_provider_expiry=request_provider_expiry)
    plan.verify_source()
    return plan


def private_json(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= MAX_BYTES, "private_record")
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        def identity(row):
            return (row.st_dev, row.st_ino, row.st_mode, row.st_uid, row.st_nlink,
                    row.st_size, row.st_mtime_ns, row.st_ctime_ns)
        need(len(raw) == info.st_size and identity(after) == identity(info), "record_changed")
    return json.loads(raw, object_pairs_hook=pairs)


def write_new(path: Path, value: Any):
    raw = canonical(value) + b"\n"
    need(len(raw) <= MAX_BYTES, "record_bound")
    # The durable exclusive claim prevents replacement/replay. Publish complete
    # bytes atomically so the independent watchdog never sees half a record.
    claim = os.open(path.with_name(path.name + ".write-once"),
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    os.close(claim)
    need(not os.path.lexists(path), "record_already_exists")
    fd, temporary = tempfile.mkstemp(prefix=".r03-publish-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Journal:
    def __init__(self, path: Path, plan: RestorePlan, *, create=False):
        need(path.is_absolute() and path.resolve() == path, "journal_path")
        if create:
            path.mkdir(mode=0o700)
            write_new(path / "binding.json", asdict(plan))
        info = path.lstat()
        need(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and stat.S_IMODE(info.st_mode) == 0o700, "journal_private")
        need(private_json(path / "binding.json") == json.loads(canonical(asdict(plan))), "journal_binding")
        self.path, self.plan = path, plan

    def read(self, name):
        need(re.fullmatch(r"[a-z0-9_.-]{1,80}", name), "record_name")
        path = self.path / (name + ".json")
        return private_json(path) if os.path.lexists(path) else None

    def write(self, name, value):
        need(re.fullmatch(r"[a-z0-9_.-]{1,80}", name), "record_name")
        write_new(self.path / (name + ".json"), value)

    @contextmanager
    def lock(self):
        fd = os.open(self.path / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                 and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600,
                 "owner_lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield fd
        except BlockingIOError:
            raise RestoreDenied("r03_owner_active") from None
        finally:
            os.close(fd)


class NativeNeon:
    """Exact retained manager only. No key, endpoint or transport injection CLI."""
    def request(self, method, path, body=None):
        need(method in {"GET", "POST", "DELETE"} and path.startswith(PREFIX), "provider_route")
        tail = path[len(PREFIX):]
        allowed = (method == "GET" and re.fullmatch(
            r"(?:|/branches|/endpoints|/(?:branches|endpoints|operations)/[a-z0-9-]{1,80})", tail)
            or method == "POST" and (tail == "/branches"
                or re.fullmatch(r"/branches/br-[a-z0-9-]{1,60}/restore", tail))
            or method == "DELETE" and re.fullmatch(r"/(?:branches/br-|endpoints/ep-)[a-z0-9-]{1,60}", tail))
        need(allowed and (body is None) == (method != "POST"), "provider_route")
        if method != "GET":
            need(tail not in {"/branches/" + PARENT, "/branches/" + PARENT + "/restore",
                              "/endpoints/" + PRODUCTION_ENDPOINT}, "production_write_forbidden")
        if method == "POST" and tail == "/branches":
            need(type(body) is dict and set(body) == {"branch", "endpoints"}
                 and type(body["branch"]) is dict
                 and set(body["branch"]) in ({"parent_id", "name", "protected"},
                                            {"parent_id", "name", "protected", "expires_at"})
                 and body["branch"]["parent_id"] == PARENT and body["branch"]["protected"] is False
                 and type(body["branch"]["name"]) is str
                 and re.fullmatch(r"hirewiz-r03-qa-[a-f0-9]{32}", body["branch"]["name"])
                 and body["endpoints"] == [{"type": "read_write", "autoscaling_limit_min_cu": 0.25,
                     "autoscaling_limit_max_cu": 0.25}], "create_scope")
            if "expires_at" in body["branch"]:
                need(timedelta(0) < stamp(body["branch"]["expires_at"]) - datetime.now(UTC) <= timedelta(minutes=30),
                     "create_expiry")
        elif method == "POST":
            need(type(body) is dict and set(body) == {"source_branch_id", "source_lsn", "preserve_under_name"}
                 and body["source_branch_id"] == tail.split("/")[2]
                 and type(body["source_lsn"]) is str and re.fullmatch(r"[0-9A-F]+/[0-9A-F]+", body["source_lsn"])
                 and type(body["preserve_under_name"]) is str
                 and re.fullmatch(r"hirewiz-r03-held-[a-f0-9]{32}", body["preserve_under_name"]), "restore_scope")
        manager._check_manager()
        argv = [manager.NODE, str(manager.CLI_ENTRY), "--config-dir", str(manager.CLI_SESSION),
                "--profile", "DEFAULT", "--force-auth=false", "--no-analytics", "--no-color",
                "--api-host", "https://console.neon.tech/api/v2", "--oauth-host", "https://oauth2.neon.tech",
                "--client-id", "neonctl", "--context-file", str(manager.CLI_HOME / "unused-context.json"),
                "api", path, "--method", method, "--output", "json"]
        if body is not None:
            argv += ["--data", canonical(body).decode()]
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True, env={"HOME": str(Path.home()), "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
                "CI": "1", "NO_COLOR": "1", "NO_UPDATE_NOTIFIER": "1", "BROWSER": "/usr/bin/false"})
        try:
            raw, error = process.communicate(timeout=25)
        except BaseException:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate(timeout=3)
            raise RestoreDenied("r03_native_outcome_unknown") from None
        need(process.returncode == 0 and len(raw) <= MAX_BYTES and len(error) <= 65536,
             "native_outcome_unknown")
        value = json.loads(raw, object_pairs_hook=pairs) if raw.strip() else {}
        need(type(value) is dict, "native_response")
        return value


def inventory(api):
    result = {}
    for kind in ("branches", "endpoints"):
        value = api.request("GET", PREFIX + "/" + kind)
        rows = value.get(kind)
        pagination = value.get("pagination", {})
        need(type(rows) is list and len(rows) <= 100 and type(pagination) is dict
             and not pagination.get("next") and not pagination.get("cursor"), "incomplete_inventory")
        need(all(type(row) is dict and row.get("project_id") == PROJECT and type(row.get("id")) is str
                 for row in rows) and len({row["id"] for row in rows}) == len(rows), "inventory_scope")
        keys = ("id", "project_id", "name", "parent_id", "default", "protected", "created_at", "expires_at",
                "branch_id", "host", "type", "autoscaling_limit_min_cu", "autoscaling_limit_max_cu",
                "suspend_timeout_seconds", "current_state", "pending_state")
        result[kind] = [{key: row[key] for key in keys if key in row} for row in rows]
    return result


def operations(value):
    rows = value.get("operations")
    need(type(rows) is list and len(rows) <= 16 and all(type(row) is dict and row.get("project_id") == PROJECT
         and type(row.get("id")) is str and re.fullmatch(r"[a-f0-9-]{36}", row["id"]) for row in rows),
         "operation_receipt")
    return [{key: row[key] for key in ("id", "project_id", "branch_id", "endpoint_id", "action")
             if key in row} for row in rows]


def wait_operations(api, rows, deadline):
    for row in rows:
        while True:
            need(time.monotonic() < deadline, "operation_deadline_reconcile_only")
            value = api.request("GET", PREFIX + "/operations/" + row["id"])["operation"]
            need(value.get("id") == row["id"] and value.get("project_id") == PROJECT
                 and all(value.get(key) == expected for key, expected in row.items()), "operation_changed")
            state = value.get("status")
            if state in {"finished", "skipped"}:
                break
            need(state in {"running", "scheduling"}, "operation_failed_reconcile_only")
            time.sleep(min(2, max(0, deadline - time.monotonic())))


def once(owner, api, phase, method, path, body=None):
    need(owner.read(phase + ".started") is None, "write_already_started_reconcile_only")
    request = {"method": method, "path": path, "body": body}
    owner.write(phase + ".started", {"request": request, "request_sha256": digest(request),
        "started_at": datetime.now(UTC).isoformat(), "started_monotonic": time.monotonic(), "automatic_replay": False})
    value = api.request(method, path, body)
    # Persist metadata only, including all original operation handles, never URI/credentials.
    receipt = {"operations": operations(value)}
    if "branch" in value:
        receipt["branch"] = {key: value["branch"][key] for key in
            ("id", "project_id", "name", "parent_id", "created_at", "default", "protected", "expires_at")
            if key in value["branch"]}
    owner.write(phase + ".acknowledged", receipt)
    return receipt


def owned_resources(plan, owner, current):
    started, before = owner.read("create.started"), owner.read("before")
    if started is None:
        return [], []
    need(before is not None and started["request"]["path"] == PREFIX + "/branches"
         and started["request_sha256"] == digest(started["request"])
         and started["request"]["method"] == "POST"
         and started["request"]["body"]["branch"]["parent_id"] == PARENT
         and started["request"]["body"]["branch"]["name"] == plan.qa_name, "create_intent_changed")
    names = {plan.qa_name, plan.held_name}
    rows = [row for row in current["branches"] if row.get("name") in names]
    need(len(rows) <= 2 and len({row.get("name") for row in rows}) == len(rows), "owned_branch_ambiguity")
    old_ids = {row["id"] for row in before["branches"]}
    for row in rows:
        need(row["id"] not in old_ids and row["id"] != PARENT and row.get("default") is False
             and row.get("protected") is False and stamp(row["created_at"]) >= stamp(started["started_at"]) - timedelta(seconds=5),
             "owned_branch_identity")
        need(stamp(row["created_at"]) <= stamp(owner.read("execution")["started_at"]) + timedelta(minutes=30),
             "owned_branch_identity")
        need(row.get("parent_id") == PARENT or row.get("parent_id") in {candidate["id"] for candidate in rows},
             "owned_branch_parent")
    ids = {row["id"] for row in rows}
    endpoints = [row for row in current["endpoints"] if row.get("branch_id") in ids]
    need(len(endpoints) <= 1, "extra_endpoint_refused")
    for endpoint in endpoints:
        need(endpoint["id"] != PRODUCTION_ENDPOINT and endpoint["id"] not in {row["id"] for row in before["endpoints"]}
             and endpoint.get("type") == "read_write" and endpoint.get("autoscaling_limit_min_cu") == 0.25
             and endpoint.get("autoscaling_limit_max_cu") == 0.25
             and endpoint.get("suspend_timeout_seconds") in {0, 300}, "owned_endpoint_identity")
    return rows, endpoints


def preflight(plan, api):
    value = api.request("GET", PREFIX)["project"]
    need((value.get("id"), value.get("org_id"), value.get("region_id"), value.get("pg_version")) ==
         (PROJECT, manager.ORG, "aws-us-east-2", 17), "project_identity")
    need(value.get("owner", {}).get("subscription_type") == "free_v3"
         and value.get("history_retention_seconds") == 21600, "free_window_required")
    current = inventory(api)
    need(len(current["branches"]) + 2 <= value["owner"].get("branches_limit", 0)
         and not any(row.get("name") in {plan.qa_name, plan.held_name} for row in current["branches"]),
         "branch_quota_or_name")
    need(len([row for row in current["branches"] if row["id"] == PARENT and row.get("default") is True]) == 1
         and len([row for row in current["endpoints"] if row["id"] == PRODUCTION_ENDPOINT
                  and row.get("branch_id") == PARENT]) == 1, "production_identity")
    # Genuine production-only read-only identity/marker; never repoint this observer.
    from infra.gcp import schema27_continuity_catalog, schema27_continuity_scope

    from scripts import neon_continuity_identity, notification_schema27_contract

    password = schema27_continuity_scope._observer_password()
    database = schema27_continuity_scope.ContinuityDatabasePin(
        schema27_continuity_scope._commitment(plan.nonce, password))
    with schema27_continuity_catalog.authenticated_observer(
        database, plan.nonce, neon_continuity_identity.Purpose.AFTER_SCHEMA27,
    ) as connection:
        notification_schema27_contract.observe(connection, schema27_continuity_catalog._identity(database))
    return current


def create(owner, api):
    plan = owner.plan
    need(timedelta(0) <= datetime.now(UTC) - stamp(plan.created_at) <= timedelta(minutes=10), "plan_expired")
    owner.write("before", preflight(plan, api))
    now = datetime.now(UTC)
    owner.write("execution", {"started_at": now.isoformat(), "deadline": (now + timedelta(minutes=20)).isoformat(),
        "cleanup_deadline": (now + timedelta(minutes=25)).isoformat(), "owner_pid": os.getpid()})
    body: dict[str, Any] = {"branch": {"parent_id": PARENT, "name": plan.qa_name, "protected": False},
        "endpoints": [{"type": "read_write", "autoscaling_limit_min_cu": 0.25,
                       "autoscaling_limit_max_cu": 0.25}]}
    if plan.request_provider_expiry:
        body["branch"]["expires_at"] = (now + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ack = once(owner, api, "create", "POST", PREFIX + "/branches", body)
    wait_operations(api, ack["operations"], time.monotonic() + 180)
    branches, endpoints = owned_resources(plan, owner, inventory(api))
    need(len(branches) == len(endpoints) == 1 and branches[0]["id"] == ack.get("branch", {}).get("id")
         and branches[0].get("parent_id") == PARENT
         and (not plan.request_provider_expiry
              or stamp(branches[0].get("expires_at")) == stamp(body["branch"]["expires_at"])),
         "created_resource_mismatch")
    owner.write("create.verified", {"observed_at": datetime.now(UTC).isoformat(),
        "request_to_ready_seconds": round(time.monotonic() - owner.read("create.started")["started_monotonic"], 3)})
    pin = clone.ClonePin(PROJECT, branches[0]["id"], endpoints[0]["id"], endpoints[0]["host"], plan.nonce)
    owner.write("clone", asdict(pin))
    owner.write("expiry.observed", {"requested": plan.request_provider_expiry,
        "expires_at": branches[0].get("expires_at"), "cleanup_watchdog_required": True})
    baseline = clone.capture(pin)
    owner.write("baseline", asdict(baseline))
    owner.write("marker.started", {"checkpoint_sha256": baseline.digest, "automatic_replay": False})
    marker = clone.install_marker(pin, baseline)
    owner.write("marker.acknowledged", asdict(marker))


def restore(owner, api):
    need(owner.read("create.acknowledged") is not None, "create_ack_required")
    pin = clone.ClonePin.from_dict(owner.read("clone"))
    baseline = clone.CloneSnapshot.from_dict(owner.read("baseline"))
    marker = clone.CloneMarker.from_dict(owner.read("marker.acknowledged"))
    need(clone.verify_marker(pin, marker), "marker_unverified")
    deadline = stamp(owner.read("execution")["deadline"])
    need(datetime.now(UTC) < deadline and datetime.now(UTC) - stamp(baseline.checkpoint_utc) < timedelta(hours=6),
         "restore_window_expired")
    ack = once(owner, api, "restore", "POST", PREFIX + "/branches/" + pin.branch + "/restore",
        {"source_branch_id": pin.branch, "source_lsn": baseline.checkpoint_lsn,
         "preserve_under_name": owner.plan.held_name})
    need(ack.get("branch", {}).get("id") == pin.branch, "restore_target_changed")
    wait_operations(api, ack["operations"], time.monotonic() + min(180, (deadline - datetime.now(UTC)).total_seconds()))
    branches, endpoints = owned_resources(owner.plan, owner, inventory(api))
    need(len(branches) == 2 and len(endpoints) == 1 and endpoints[0]["id"] == pin.endpoint
         and endpoints[0]["branch_id"] == pin.branch, "restore_resources_changed")
    report = clone.compare_restore(baseline, clone.capture(pin), marker) | {
        "provider_restore_proven": True, "native_provider_restore_confirmed": True,
        "restore_operations": ack["operations"],
        "restore_requested_at": owner.read("restore.started")["started_at"],
        "restore_to_verified_seconds": round(time.monotonic() - owner.read("restore.started")["started_monotonic"], 3),
        "checkpoint_age_at_request_seconds": round((stamp(owner.read("restore.started")["started_at"])
                                                    - stamp(baseline.checkpoint_utc)).total_seconds(), 3),
    }
    owner.write("verified", report | {"verified_at": datetime.now(UTC).isoformat(),
        "production_promoted": False, "full_R03_qualified": False})
    return report


def cleanup(owner, api):
    branches, endpoints = owned_resources(owner.plan, owner, inventory(api))
    targets = [("delete_endpoint", "/endpoints/", row) for row in endpoints]
    remaining = {row["id"]: row for row in branches}
    while remaining:
        leaves = [row for key, row in remaining.items() if not any(other.get("parent_id") == key for other in remaining.values())]
        need(bool(leaves), "cleanup_parent_cycle")
        for row in leaves:
            phase = "delete_qa" if row.get("name") == owner.plan.qa_name else "delete_preserved"
            targets.append((phase, "/branches/", row))
            del remaining[row["id"]]
    for phase, route, row in targets:
        need(owner.read(phase + ".started") is None, "cleanup_unknown_reconcile_only")
        ack = once(owner, api, phase, "DELETE", PREFIX + route + row["id"])
        wait_operations(api, ack["operations"], time.monotonic() + 60)
    current = inventory(api)
    need(owned_resources(owner.plan, owner, current) == ([], []), "cleanup_absence_unverified")
    before = owner.read("before")
    if before is not None:
        for kind in ("branches", "endpoints"):
            for old in before[kind]:
                actual = next((row for row in current[kind] if row["id"] == old["id"]), None)
                keys = ("id", "project_id", "name", "parent_id", "default", "protected", "branch_id", "host",
                        "type", "autoscaling_limit_min_cu", "autoscaling_limit_max_cu", "suspend_timeout_seconds")
                need(actual is not None and all(actual.get(key) == old.get(key) for key in keys), "production_metadata_changed")
    if owner.read("cleanup.verified") is None:
        owner.write("cleanup.verified", {"owned_resources_absent": True, "production_targets_unchanged": True,
            "observed_at": datetime.now(UTC).isoformat(), "automatic_replay": False})


def reconcile(owner, api):
    current = inventory(api)
    branches, endpoints = owned_resources(owner.plan, owner, current)
    phases = ("create", "marker", "restore", "delete_endpoint", "delete_qa", "delete_preserved")
    observed = []
    for phase in phases:
        ack = owner.read(phase + ".acknowledged")
        for original in (ack or {}).get("operations", []):
            row = api.request("GET", PREFIX + "/operations/" + original["id"])["operation"]
            need(all(row.get(key) == value for key, value in original.items())
                 and row.get("status") in {"finished", "skipped", "running", "scheduling", "failed", "cancelling", "cancelled"},
                 "operation_changed")
            observed.append({"phase": phase, "id": original["id"], "status": row["status"]})
    return {"status": "read_only_reconciliation", "branches": branches, "endpoints": endpoints,
        "started": [phase for phase in phases if owner.read(phase + ".started") is not None],
        "unknown_request_phases": [phase for phase in phases if owner.read(phase + ".started") is not None
                                   and owner.read(phase + ".acknowledged") is None], "operations": observed,
        "automatic_replay": False, "provider_mutations": 0, "production_promoted": False}


def watchdog(owner, api, descriptor, *, parent_pid=None):
    expired = False
    while True:
        execution = owner.read("execution")
        remaining = 1200 if execution is None else (stamp(execution["deadline"]) - datetime.now(UTC)).total_seconds()
        ready, _, _ = select.select([descriptor], [], [], min(1, max(0, remaining)))
        if remaining <= 0 or ready and not os.read(descriptor, 1):
            expired = remaining <= 0
            break
    if expired and parent_pid is not None:
        # Only our actual still-parent can receive cancellation. It cannot be a
        # reused PID: reparenting changes getppid(), and EOF needs no signal.
        execution = owner.read("execution")
        if (type(parent_pid) is int and parent_pid > 1 and os.getppid() == parent_pid
                and execution is not None and execution.get("owner_pid") == parent_pid):
            owner.write("watchdog.cancelled-owner", {"parent_pid": parent_pid,
                "observed_at": datetime.now(UTC).isoformat(), "reason": "work_deadline"})
            try:
                os.kill(parent_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass  # Parent loss releases its lock; cleanup still must run.
    deadline = time.monotonic() + 300
    while True:
        try:
            with owner.lock():
                cleanup(owner, api)
            return
        except RestoreDenied as error:
            if str(error) != "r03_owner_active" or time.monotonic() >= deadline:
                raise
            time.sleep(1)


def run(plan_path, plan_sha, journal_path):
    raw = plan_path.read_bytes()
    need(hashlib.sha256(raw).hexdigest() == plan_sha, "plan_hash")
    plan = RestorePlan.from_dict(private_json(plan_path))
    plan.verify_source()
    owner = Journal(journal_path, plan, create=True)
    read_fd, write_fd = os.pipe()
    ready_fd, ready_writer = os.pipe()
    child = subprocess.Popen([sys.executable, "-B", "-m", "infra.gcp.schema27_provider_restore", "watchdog",
        "--plan", str(plan_path), "--plan-sha256", plan_sha, "--journal", str(journal_path),
        "--watchdog-fd", str(read_fd), "--watchdog-ready-fd", str(ready_writer)],
        cwd=ROOT, pass_fds=(read_fd, ready_writer), start_new_session=True,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.close(read_fd)
    os.close(ready_writer)
    previous_handler = signal.getsignal(signal.SIGTERM)

    def cancelled(_number, _frame):
        # Interrupts communicate/SQL through their existing finally blocks;
        # the native CLI's owned group is reaped before cleanup takes over.
        raise RestoreDenied("r03_work_deadline_cancelled")

    signal.signal(signal.SIGTERM, cancelled)
    try:
        ready, _, _ = select.select([ready_fd], [], [], 10)
        need(bool(ready) and os.read(ready_fd, 2) == b"R", "watchdog_not_ready")
        with owner.lock():
            try:
                create(owner, NativeNeon())
                result = restore(owner, NativeNeon())
            finally:
                cleanup(owner, NativeNeon())
        return result | {"cleanup_verified": True, "full_R03_qualified": False}
    finally:
        signal.signal(signal.SIGTERM, previous_handler)
        os.close(ready_fd)
        os.close(write_fd)
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            # Its bounded cleanup continues independently; never kill the safety owner.
            pass


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("plan", "run", "reconcile", "cleanup", "watchdog"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--watchdog-fd", type=int)
    parser.add_argument("--watchdog-ready-fd", type=int)
    parser.add_argument("--request-provider-expiry", action="store_true")
    args = parser.parse_args()
    if args.mode == "plan":
        write_new(args.plan, asdict(capture_plan(request_provider_expiry=args.request_provider_expiry)))
        return {"status": "provider_free_plan", "plan_sha256": hashlib.sha256(args.plan.read_bytes()).hexdigest(),
                "provider_calls": 0, "execution_performed": False}
    need(not args.request_provider_expiry and args.journal is not None and type(args.plan_sha256) is str
         and hashlib.sha256(args.plan.read_bytes()).hexdigest() == args.plan_sha256, "plan_arguments")
    if args.mode == "run":
        return run(args.plan, args.plan_sha256, args.journal)
    plan = RestorePlan.from_dict(private_json(args.plan))
    plan.verify_source()
    owner, api = Journal(args.journal, plan), NativeNeon()
    if args.mode == "reconcile":
        return reconcile(owner, api)
    if args.mode == "watchdog":
        need(type(args.watchdog_fd) is int and args.watchdog_fd > 2
             and type(args.watchdog_ready_fd) is int and args.watchdog_ready_fd > 2
             and args.watchdog_fd != args.watchdog_ready_fd, "watchdog_descriptor")
        os.write(args.watchdog_ready_fd, b"R")
        os.close(args.watchdog_ready_fd)
        parent_pid = os.getppid()
        try:
            watchdog(owner, api, args.watchdog_fd, parent_pid=parent_pid)
        except Exception:
            if owner.read("watchdog.failed") is None:
                owner.write("watchdog.failed", {"fixed_code": "r03_watchdog_cleanup_unverified",
                    "automatic_replay": False, "observed_at": datetime.now(UTC).isoformat()})
            raise
        finally:
            os.close(args.watchdog_fd)
        return {"status": "watchdog_finished"}
    with owner.lock():
        cleanup(owner, api)
    return {"status": "owned_resources_absent", "production_promoted": False}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except Exception as error:
        code = str(error) if type(error) is RestoreDenied and re.fullmatch(r"r03_[a-z_]+", str(error)) else "r03_unavailable_or_unknown"
        print(json.dumps({"status": "stopped_reconcile_only", "fixed_code": code,
            "automatic_replay": False, "full_R03_qualified": False}))
        raise SystemExit(2) from None
