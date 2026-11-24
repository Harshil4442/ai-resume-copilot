"""Read-only reconciliation of an ACKed route, then one internal health GET.

Cloud Run may coalesce the same revision's default and tag rows. Compare their
allocation/alias meaning without changing retained raw observations or traffic.
The previous incomplete journal remains immutable; its spend counters carry on.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from types import SimpleNamespace

from infra.gcp import schema27_internal_coordinator_completion as previous

old, prior, original = previous, previous.prior, previous.original
rolling, owned, restore = old.rolling, old.owned, old.restore
API, ANALYSIS, COORDINATOR = old.API, old.ANALYSIS, old.COORDINATOR
ROOT = Path(__file__).resolve().parents[2]
KIND, PROOF = (
    "hirewiz_coordinator_health_finalizer_v1",
    "hirewiz_native_admission_finalized_v1",
)
SOURCE_FILES = (*old.SOURCE_FILES, "infra/gcp/schema27_coordinator_health_finalizer.py")
PARENT_KEYS = {
    "root",
    "plan",
    "plan_sha256",
    "journal",
    "before_sha256",
    "route_ack_sha256",
    "delete_ack_sha256",
}


def need(value, code):
    rolling.need(value, "coordinator_finalizer_" + code)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source():
    pin = old.source()
    pin["files"][SOURCE_FILES[-1]] = sha(ROOT / SOURCE_FILES[-1])
    return pin


def historical_source(pin):
    need(
        set(pin) == {"release", "tree", "files"}
        and set(pin["files"]) == set(SOURCE_FILES),
        "source_scope",
    )
    need(
        subprocess.check_output(
            ["git", "rev-parse", pin["release"] + "^{tree}"], cwd=ROOT, timeout=10
        )
        .decode()
        .strip()
        == pin["tree"],
        "source_tree",
    )
    for name, digest in pin["files"].items():
        value = subprocess.check_output(
            ["git", "show", pin["release"] + ":" + name], cwd=ROOT, timeout=10
        )
        need(
            hashlib.sha256(value).hexdigest() == digest and sha(ROOT / name) == digest,
            "source_bytes",
        )


def logical_traffic(rows):
    need(type(rows) is list and 1 <= len(rows) <= 20, "traffic_shape")
    allocations = []
    aliases = {}
    for row in rows:
        need(
            type(row) is dict
            and set(row) <= {"type", "revision", "percent", "tag"}
            and row.get("type") == "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION"
            and type(row.get("revision")) is str
            and re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", row["revision"]),
            "traffic_row",
        )
        percent = row.get("percent", 0)
        tag = row.get("tag")
        need(
            type(percent) is int
            and 0 <= percent <= 100
            and (percent or tag is not None),
            "traffic_percent",
        )
        if percent:
            allocations.append({"revision": row["revision"], "percent": percent})
        if tag is not None:
            need(
                type(tag) is str
                and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", tag)
                and tag not in aliases,
                "traffic_alias",
            )
            aliases[tag] = row["revision"]
    need(
        len(allocations) == 1 and allocations[0]["percent"] == 100, "traffic_allocation"
    )
    return {"default": allocations[0], "aliases": dict(sorted(aliases.items()))}


def make_parent(*, root, plan_path, plan_sha256, journal_path):
    directory = Path(journal_path)
    return {
        "root": str(root),
        "plan": str(plan_path),
        "plan_sha256": plan_sha256,
        "journal": str(directory),
        "before_sha256": sha(directory / "before.checked.json"),
        "route_ack_sha256": sha(directory / "route.ack.json"),
        "delete_ack_sha256": sha(directory / "delete.ack.json"),
    }


def parent_binding(parent):
    need(
        set(parent) == PARENT_KEYS
        and all(
            rolling.HASH.fullmatch(v) for k, v in parent.items() if k.endswith("sha256")
        ),
        "parent_scope",
    )
    plan = old.load(
        restore._private_read(parent["plan"], parent["plan_sha256"]),
        parent["plan_sha256"],
    )
    old.historical_source(plan.source)
    old.read_parent(plan.parent)
    root = Path(parent["root"])
    need(root.is_absolute() and root.resolve() == root, "parent_root")
    need(
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=10)
        .decode()
        .strip()
        == plan.source["release"]
        and not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            timeout=10,
        ),
        "parent_frozen",
    )
    need(
        all(sha(root / name) == stamp for name, stamp in plan.source["files"].items()),
        "parent_bytes",
    )
    journal = owned.Journal(Path(parent["journal"]), plan)
    for name in ("queue", "before", "route", "delete"):
        started, ack = old.record_scope(plan, journal, name)
        need(started is not None and ack is not None, "parent_ack_required")
    before = old.checked_receipt(plan, journal, "before")
    for name in (
        "after.started",
        "after.ack",
        "after.checked",
        "topology.started",
        "topology.checked",
        "complete",
    ):
        need(
            journal.read(name) is None and not journal.claimed(name),
            "parent_incomplete_required",
        )
    need(
        sha(journal.directory / "before.checked.json") == parent["before_sha256"]
        and sha(journal.directory / "route.ack.json") == parent["route_ack_sha256"]
        and sha(journal.directory / "delete.ack.json") == parent["delete_ack_sha256"],
        "parent_receipt_hash",
    )
    calls: list[dict] = []
    invocations: list[dict] = []
    for index in range(100):
        name = "tasks-rpc." + old._suffix(index)
        record = journal.read(name)
        if record is None:
            need(not journal.claimed(name), "parent_uncertain_budget")
        else:
            need(
                index == len(calls) and record["response_bytes_reserved"] == 8193,
                "parent_counter_gap",
            )
            calls.append(record)
    for index in range(40):
        name = "invocation." + old._suffix(index)
        record = journal.read(name)
        if record is None:
            need(not journal.claimed(name), "parent_uncertain_invocation")
        else:
            need(index == len(invocations), "parent_invocation_gap")
            invocations.append(record)
    need(
        len(list(journal.directory.glob("tasks-rpc.*.json"))) == len(calls)
        and len(list(journal.directory.glob("tasks-rpc.*.claim"))) == len(calls)
        and len(list(journal.directory.glob("invocation.*.json"))) == len(invocations)
        and len(list(journal.directory.glob("invocation.*.claim"))) == len(invocations),
        "parent_extra_counters",
    )
    return (
        plan,
        journal,
        before,
        plan.capture_transport_calls + len(calls),
        len(invocations),
    )


@dataclass(frozen=True)
class Plan:
    parent: dict
    analysis: dict
    nonce: str
    target: str
    audience: str
    scheduler_uid: str
    previous_calls: int
    previous_invocations: int
    source: dict
    journal_location: dict
    capture_transport_calls: int
    metadata_bytes: list = field(default_factory=list)
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def queue(self):
        return old.PARENT + "/queues/hirewiz-coor-final-" + self.nonce[:16]

    def task(self, phase):
        need(phase in {"before", "after"}, "phase")
        return self.queue + "/tasks/" + phase + "-" + self.nonce

    def url(self, phase):
        return self.target + "/api/health?hirewiz_probe=" + self.nonce + "-" + phase

    def validate(self):
        need(
            type(self) is Plan
            and self.kind == KIND
            and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
            and set(self.parent) == PARENT_KEYS
            and re.fullmatch(r"[1-9][0-9]{20}", self.scheduler_uid)
            and 0 <= self.previous_calls <= self.capture_transport_calls <= 95
            and self.capture_transport_calls - self.previous_calls <= 4
            and 0 <= self.previous_invocations < 40,
            "plan_scope",
        )
        from urllib.parse import urlsplit

        for url in (self.target, self.audience):
            value = urlsplit(url)
            need(
                value.scheme == "https"
                and value.hostname
                and value.hostname.endswith(".run.app")
                and not (
                    value.username
                    or value.password
                    or value.path
                    or value.query
                    or value.fragment
                    or value.port
                ),
                "origin",
            )
        need(
            len(self.metadata_bytes) in {0, 3}
            and all(type(v) is int and 0 < v <= 8192 for v in self.metadata_bytes),
            "metadata_bytes",
        )


def load(raw, digest):
    need(hashlib.sha256(raw).hexdigest() == digest and len(raw) <= 131072, "plan_file")
    value = rolling.release._json(raw)
    need(set(value) == {f.name for f in fields(Plan)}, "plan_shape")
    result = Plan(**value)
    result.validate()
    return result


def current(plan, boundary):
    plan.validate()
    historical_source(plan.source)
    history, journal, _, calls, invocations = parent_binding(plan.parent)
    need(
        calls == plan.previous_calls
        and invocations == plan.previous_invocations
        and history.analysis == plan.analysis,
        "parent_counter_changed",
    )
    completed, _ = old.read_parent(history.parent)
    api_plan, _, api = prior.parent_binding(completed)
    api_pin = next(pin for pin in api_plan.services if pin.service == API)
    need(
        prior.exact_changed(boundary, api_pin, api_plan, "route") == api, "api_changed"
    )
    need(
        original.projection(boundary.service(ANALYSIS), ANALYSIS) == plan.analysis,
        "analysis_changed",
    )
    route = journal.read("route.ack")
    operation = boundary.operation(route["operation"])
    need(operation.get("done") is True and "error" not in operation, "route_not_ready")
    pin = completed.coordinator
    value = boundary.service(COORDINATOR)
    original.ready(value, COORDINATOR)
    expected = original.desired(boundary, pin)
    actual_traffic = rolling.pinned_traffic(COORDINATOR, value)
    need(
        value["uid"] == pin.before["service_uid"]
        and int(value["generation"]) == pin.before["generation"] + 2
        and original.projection(value, COORDINATOR)["static_public_sha256"]
        == pin.before["static_public_sha256"]
        and original.normalize(value["template"], COORDINATOR) == expected
        and logical_traffic(actual_traffic)
        == logical_traffic(original.traffic(completed, pin, "route"))
        and rolling.short_revision(value["latestCreatedRevision"], COORDINATOR)
        == pin.revision,
        "native_route_changed",
    )
    revision = boundary.revision(COORDINATOR, pin.revision)
    need(
        revision is not None
        and any(
            r.get("type") == "Ready" and r.get("state") == "CONDITION_SUCCEEDED"
            for r in revision.get("conditions", [])
        )
        and restore.identities.readback.application.template(revision, revision=True)
        == {k: v for k, v in expected.items() if k != "revision"},
        "immutable_revision_changed",
    )
    tags = [
        row
        for row in value.get("trafficStatuses", [])
        if row.get("tag") == completed.tag
    ]
    need(
        value.get("ingress") == "INGRESS_TRAFFIC_INTERNAL_ONLY"
        and len(tags) == 1
        and tags[0].get("uri", "").rstrip("/") == plan.target
        and value.get("uri", "").rstrip("/") == plan.audience
        and old.health_source(completed, value) == history.health_source,
        "internal_target_changed",
    )
    return {
        API: api,
        COORDINATOR: original.projection(value, COORDINATOR)
        | {"revision_uid": revision["uid"], "prior_revision_uid": pin.prior_uid},
    }


def pause(plan, journal):
    original.require_unpaused(journal)
    history, _, _, _, _ = parent_binding(plan.parent)
    for directory in (Path(plan.parent["journal"]), Path(history.parent["journal"])):
        try:
            (directory / "operator.pause-requested").lstat()
        except FileNotFoundError:
            continue
        need(False, "parent_pause_requested")


class ReadOnlyRun(previous.RouteRun):
    def patch(self, *args, **kwargs):
        need(False, "traffic_or_source_mutation_forbidden")


class TasksHttp(previous.TasksHttp):
    def reserve_rpc(self, method, path, body):
        if self.journal is None:
            need(self.attempts - self.plan.previous_calls < 4, "capture_rpc_budget")
        else:
            old.reserve(
                self.journal,
                "health-control",
                (98 if self.cleanup_mode else 95) - self.plan.capture_transport_calls,
                {
                    "request_sha256": rolling.sha(
                        {"method": method, "path": path, "body": body}
                    ),
                    "response_bytes_reserved": 8193,
                    "uncertain_send_consumes_reservation": True,
                },
            )
        self.attempts += 1

    def call(self, method, path, body=None, *, absent=False):
        if method == "POST" and path == self.plan.queue + "/tasks":
            need(body == old.task_body(self.plan, "after"), "only_after_get")
        return super().call(method, path, body, absent=absent)


class NativeBoundary(previous.NativeBoundary):
    def __init__(self):
        super().__init__()
        self._run: ReadOnlyRun | None = None
        self._tasks: TasksHttp | None = None

    @property
    def run(self):
        if self._run is None:
            self._run = ReadOnlyRun()
        return self._run

    def patch(self, *args, **kwargs):
        need(False, "traffic_or_source_mutation_forbidden")

    def tasks(self, plan):
        if self._tasks is None:
            self._tasks = TasksHttp(plan)
        need(self._tasks.plan == plan, "transport_plan_changed")
        return self._tasks

    def request_logs(self, plan, phase, created):
        need(phase == "after", "only_after_log")
        history, _, _, _, _ = parent_binding(plan.parent)
        completed, _ = old.read_parent(history.parent)
        query = (
            'resource.type="cloud_run_revision" AND resource.labels.service_name="'
            + COORDINATOR
            + '" AND resource.labels.revision_name="'
            + completed.coordinator.revision
            + '" AND httpRequest.requestMethod="GET" AND httpRequest.requestUrl="'
            + plan.url(phase)
            + '" AND timestamp>="'
            + created
            + '"'
        )
        self.tasks(plan).reserve_rpc("LOG_READ", query, None)
        raw = self.observer._command(
            "gcloud",
            "logging",
            "read",
            query,
            "--limit=2",
            "--project=" + old.PROJECT,
            "--account=harshilyogeshbhai886@gmail.com",
            "--billing-project=" + old.PROJECT,
            "--format=json(timestamp,insertId,logName,resource.type,resource.labels.project_id,resource.labels.location,resource.labels.service_name,resource.labels.revision_name,httpRequest.requestMethod,httpRequest.requestUrl,httpRequest.status,httpRequest.latency)",
            "--quiet",
        )
        need(type(raw) is bytes and len(raw) <= 8192, "health_log_response_bound")
        return rolling.release._json(raw)

    def old_queue_absent(self, plan):
        history, _, _, _, _ = parent_binding(plan.parent)

        class Reader(previous.TasksHttp):
            def call(reader, method, path, body=None, *, absent=False):
                need(
                    method == "GET"
                    and path == history.queue
                    and body is None
                    and absent,
                    "old_queue_read_only",
                )
                return super().call(method, path, body, absent=absent)

            def reserve_rpc(reader, method, path, body):
                self.tasks(plan).reserve_rpc(method, path, body)

        reader = Reader(history)
        try:
            return reader.call("GET", history.queue, absent=True) is None
        finally:
            reader.close()


def capture(parent, boundary, nonce, directory):
    need(type(boundary) is NativeBoundary, "exact_boundary")
    history, _, _, calls, invocations = parent_binding(parent)
    directory = Path(directory)
    need(
        directory.is_absolute()
        and directory.resolve() == directory
        and directory.is_dir()
        and not any(directory.iterdir()),
        "new_journal",
    )
    plan = Plan(
        parent,
        history.analysis,
        nonce,
        history.target,
        history.audience,
        history.scheduler_uid,
        calls,
        invocations,
        source(),
        restore.scope.journal_location(type("L", (), {"directory": directory})()),
        calls,
    )
    current(plan, boundary)
    boundary.preflight(plan)
    need(
        boundary.old_queue_absent(plan)
        and boundary.tasks(plan).call("GET", plan.queue, absent=True) is None,
        "queue_absence_required",
    )
    plan = replace(
        plan,
        capture_transport_calls=boundary.tasks(plan).attempts,
        metadata_bytes=list(getattr(boundary, "_metadata_bytes", [])),
    )
    boundary._tasks.plan = plan
    return plan


def log_receipt(plan, ack, entries, states, *, observed_at=None):
    history, _, _, _, _ = parent_binding(plan.parent)
    proxy = SimpleNamespace(
        parent=history.parent,
        task=plan.task,
        url=plan.url,
        health_source=history.health_source,
    )
    return old._log_receipt(
        proxy, "after", ack, entries, states, observed_at=observed_at
    )


def checked(plan, journal):
    ack = journal.read("after.ack")
    receipt = journal.read("after.checked")
    need(ack is not None and receipt is not None, "after_health_required")
    expected = log_receipt(
        plan,
        ack,
        [receipt["request_log"]],
        receipt["native_states"],
        observed_at=receipt["observed_at"],
    )
    need(receipt == expected | {"plan_sha256": plan.digest}, "after_receipt_changed")
    return receipt


def record(plan, journal, name):
    start, ack = old._record(journal, name)
    if start is None:
        return None, None
    need(
        start["target"] == name
        and start["automatic_replay"] is False
        and start["body_sha256"] == rolling.sha(start["request"]),
        "intent_changed",
    )
    body = start["request"]
    if name == "queue":
        need(body == old.queue_body(plan) and ack["name"] == plan.queue, "queue_intent")
    elif name == "after":
        need(
            body == old.task_body(plan, "after")
            and ack["name"] == plan.task("after")
            and ack["dispatchDeadline"] == "15s",
            "task_intent",
        )
    elif name == "delete":
        need(
            body == {"queue": plan.queue}
            and set(ack) == {"intent_sha256", "plan_sha256"},
            "delete_intent",
        )
    else:
        need(False, "unexpected_mutation")
    return start, ack


def once(plan, journal, name, body, send, *, cleanup_only=False):
    if cleanup_only:
        need(name == "delete" and body == {"queue": plan.queue}, "cleanup_scope")
    else:
        pause(plan, journal)
    journal.record(
        name + ".started",
        {
            "target": name,
            "request": body,
            "body_sha256": rolling.sha(body),
            "automatic_replay": False,
        },
    )
    if not cleanup_only:
        pause(plan, journal)
    response = send()
    journal.record(
        name + ".ack",
        response | {"intent_sha256": rolling.sha(journal.read(name + ".started"))},
    )


def proof(plan, states, receipt):
    history, _, _before, _, _ = parent_binding(plan.parent)
    completed, _ = old.read_parent(history.parent)
    first, _, _ = prior.read_parent(completed.parent)
    return {
        "kind": PROOF,
        "services": states,
        "analysis_unchanged": plan.analysis,
        "new_admission_closed": True,
        "parent_restore_plan_sha256": first.parent["plan_sha256"],
        "parent_restore_complete_sha256": first.parent["complete_sha256"],
        "retained_internal_plan_sha256": plan.parent["plan_sha256"],
        "retained_before_http200_sha256": plan.parent["before_sha256"],
        "retained_route_ack_sha256": plan.parent["route_ack_sha256"],
        "after_health_receipt_sha256": rolling.sha(receipt),
        "raw_native_traffic_preserved": True,
        "traffic_semantics": logical_traffic(states[COORDINATOR]["traffic"]),
        "cumulative_health_gets": 2,
        "maximum_global_control_calls": 98,
        "maximum_dispatches": 2,
        "maximum_global_response_reservation_bytes": 98 * 8193,
        "stock_metadata_response_bytes": history.capture_metadata_bytes
        + plan.metadata_bytes,
        "saved_api_reads_preserved": True,
        "response_json_observed": False,
        "internal_HTTP200_logs_verified": True,
        "scan_disabled_evidence": history.health_source,
        "both_owned_probe_queues_removed": True,
        "old_external_health_claimed": False,
        "document_processing_enabled": False,
        "payment_activation_authorized": False,
        "replay_authorized": False,
    }


def advance(plan, boundary, journal, *, observe=False):
    need(
        type(boundary) is NativeBoundary
        and type(journal) is owned.Journal
        and journal.plan_hash == plan.digest,
        "exact_owner",
    )
    need(source() == plan.source, "source_changed")
    with journal.locked():
        transport = boundary.tasks(plan)
        transport.journal = journal
        old.reserve(
            journal,
            "invocation",
            40 - plan.previous_invocations,
            {"observe": observe, "replay_authorized": False},
        )
        states = current(plan, boundary)
        for name in ("queue", "after", "delete"):
            record(plan, journal, name)
        if journal.read("queue.ack") is None:
            if observe:
                return {"status": "queue_not_created", "native_mutations": 0}
            need(transport.call("GET", plan.queue, absent=True) is None, "queue_exists")
            once(
                plan,
                journal,
                "queue",
                old.queue_body(plan),
                lambda: transport.call(
                    "POST", old.PARENT + "/queues", old.queue_body(plan)
                ),
            )
            return {"status": "queue_created", "native_mutations": 1}
        if journal.read("delete.ack") is not None:
            need(
                transport.call("GET", plan.queue, absent=True) is None,
                "cleanup_unverified",
            )
            receipt = checked(plan, journal)
            need(
                boundary.request_logs(
                    plan, "after", journal.read("after.ack")["createTime"]
                )
                == [receipt["request_log"]],
                "duplicate_or_changed_log",
            )
            result = proof(plan, states, receipt)
            if not observe and journal.read("complete") is None:
                journal.record("complete", result)
            need(
                journal.read("complete") == result | {"plan_sha256": plan.digest},
                "completion_changed",
            )
            return result | {"native_mutations": 0}
        old._queue(transport.call("GET", plan.queue), plan)
        if journal.read("after.ack") is None:
            if observe:
                return {"status": "after_probe_not_started", "native_mutations": 0}

            def create():
                response = transport.call(
                    "POST", plan.queue + "/tasks", old.task_body(plan, "after")
                )
                need(
                    response.get("name") == plan.task("after")
                    and type(response.get("createTime")) is str
                    and response.get("dispatchDeadline") == "15s",
                    "task_ack_identity",
                )
                return {
                    k: response[k] for k in ("name", "createTime", "dispatchDeadline")
                }

            once(plan, journal, "after", old.task_body(plan, "after"), create)
            return {"status": "after_probe_created", "native_mutations": 1}
        if journal.read("after.checked") is None:
            ack = journal.read("after.ack")
            entries = boundary.request_logs(plan, "after", ack["createTime"])
            receipt = log_receipt(plan, ack, entries, states)
            if receipt is None:
                return {"status": "after_probe_log_pending", "native_mutations": 0}
            need(current(plan, boundary) == states, "probe_snapshot_changed")
            if observe:
                return {
                    "status": "after_probe_observed_unrecorded",
                    "native_mutations": 0,
                }
            journal.record("after.checked", receipt)
            return {"status": "after_internal_health_verified", "native_mutations": 0}
        receipt = checked(plan, journal)
        need(receipt["native_states"] == states, "after_snapshot_changed")
        need(
            boundary.request_logs(
                plan, "after", journal.read("after.ack")["createTime"]
            )
            == [receipt["request_log"]],
            "duplicate_or_changed_log",
        )
        if observe:
            return {"status": "cleanup_not_started", "native_mutations": 0}
        once(
            plan,
            journal,
            "delete",
            {"queue": plan.queue},
            lambda: transport.call("DELETE", plan.queue),
        )
        return {"status": "queue_delete_acknowledged", "native_mutations": 1}


def cleanup(plan, boundary, journal):
    need(
        type(boundary) is NativeBoundary
        and type(journal) is owned.Journal
        and journal.plan_hash == plan.digest
        and source() == plan.source,
        "exact_cleanup_owner",
    )
    parent_binding(plan.parent)
    with journal.locked():
        transport = boundary.tasks(plan)
        transport.journal = journal
        transport.cleanup_mode = True
        _, ack = record(plan, journal, "queue")
        need(ack is not None, "owned_queue_required")
        start, deleted = record(plan, journal, "delete")
        if start is None:
            old._queue(transport.call("GET", plan.queue), plan)
            once(
                plan,
                journal,
                "delete",
                {"queue": plan.queue},
                lambda: transport.call("DELETE", plan.queue),
                cleanup_only=True,
            )
            return {"status": "queue_delete_acknowledged", "native_mutations": 1}
        need(
            deleted is not None
            and transport.call("GET", plan.queue, absent=True) is None,
            "cleanup_unverified",
        )
        return {"status": "owned_queue_absent", "native_mutations": 0}


class ClosedFinalAdmission:
    def __init__(self, plan, journal):
        need(type(plan) is Plan and type(journal) is owned.Journal, "closed_type")
        self.plan, self.journal = plan, journal
        self.verify_custody()

    @property
    def restore_parent(self):
        history, _, _, _, _ = parent_binding(self.plan.parent)
        completed, _ = old.read_parent(history.parent)
        first, _, _ = prior.read_parent(completed.parent)
        return first.parent

    @property
    def parent_restore_plan_sha256(self):
        return self.restore_parent["plan_sha256"]

    @property
    def parent_restore_complete_sha256(self):
        return self.restore_parent["complete_sha256"]

    def verify_custody(self):
        self.plan.validate()
        historical_source(self.plan.source)
        parent_binding(self.plan.parent)
        self.journal.verify()
        for name in ("queue", "after", "delete"):
            start, ack = record(self.plan, self.journal, name)
            need(start is not None and ack is not None, "incomplete")
        history, _, before, _, _ = parent_binding(self.plan.parent)
        completed, _ = old.read_parent(history.parent)
        receipt = checked(self.plan, self.journal)
        before_states, after_states = before["native_states"], receipt["native_states"]
        need(
            set(after_states) == {API, COORDINATOR}
            and after_states[API] == before_states[API]
            and after_states[COORDINATOR]["generation"]
            == before_states[COORDINATOR]["generation"] + 1
            and logical_traffic(after_states[COORDINATOR]["traffic"])
            == logical_traffic(
                original.traffic(completed, completed.coordinator, "route")
            )
            and {
                k: v
                for k, v in before_states[COORDINATOR].items()
                if k not in {"generation", "etag", "traffic"}
            }
            == {
                k: v
                for k, v in after_states[COORDINATOR].items()
                if k not in {"generation", "etag", "traffic"}
            },
            "historical_route_snapshot_changed",
        )
        complete = self.journal.read("complete")
        need(
            complete
            == proof(self.plan, receipt["native_states"], receipt)
            | {"plan_sha256": self.plan.digest},
            "complete_changed",
        )
        return complete

    def verify_current(self, boundary=None):
        complete = self.verify_custody()
        own = boundary is None
        boundary = NativeBoundary() if own else boundary
        try:
            value = current(self.plan, boundary)
            need(value == complete["services"], "current_changed")
            return value
        finally:
            if own:
                boundary.close()


def load_closed_final(*, plan_path, plan_sha256, journal_path):
    plan = load(restore._private_read(plan_path, plan_sha256), plan_sha256)
    return ClosedFinalAdmission(plan, owned.Journal(Path(journal_path), plan))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("capture", "advance", "observe", "cleanup"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--parent", type=Path)
    parser.add_argument("--parent-sha256")
    parser.add_argument("--nonce")
    args = parser.parse_args()
    boundary = NativeBoundary()
    try:
        if args.mode == "capture":
            spec = rolling.release._json(
                restore._private_read(args.parent, args.parent_sha256)
            )
            plan = capture(spec, boundary, args.nonce, args.journal)
            owned.Journal(args.journal, plan, create=True)
            result = {
                "status": "captured",
                "plan_sha256": restore.mixed.app.bridge._write_plan(args.plan, plan),
                "native_mutations": 0,
            }
        else:
            plan = load(
                restore._private_read(args.plan, args.plan_sha256), args.plan_sha256
            )
            journal = owned.Journal(args.journal, plan)
            result = (
                cleanup(plan, boundary, journal)
                if args.mode == "cleanup"
                else advance(plan, boundary, journal, observe=args.mode == "observe")
            )
        print(rolling.canonical(result).decode())
        return 0
    except Exception as error:  # noqa: BLE001 - fixed bounded provider diagnostics only
        result = restore.mixed.diagnostics.diagnostic(error, "observe")
        if re.fullmatch(r"coordinator_finalizer_[a-z_]{1,90}", str(error)):
            result["fixed_code"] = str(error)
        print(rolling.canonical(result).decode())
        return 1
    finally:
        boundary.close()


if __name__ == "__main__":
    raise SystemExit(main())
