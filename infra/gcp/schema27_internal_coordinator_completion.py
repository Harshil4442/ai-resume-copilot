"""Two internal health GETs and the one remaining coordinator traffic change.

Consumes the real b0 stage ACK without editing its incomplete journal. HTTP200
request logs prove internal reachability, not an observed JSON response body.
Scan closure is established separately by exact immutable source/env metadata.
The durable 98-call/802914-byte reserve covers additional Tasks/IAM health
transport and selected request-log responses, not the existing native source
readback operations covered by the parent deployment allowance.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, field, fields, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

from infra.gcp import schema27_native_admission_completion as prior

original, rolling, owned, restore = (
    prior.original,
    prior.rolling,
    prior.owned,
    prior.restore,
)
ROOT = Path(__file__).resolve().parents[2]
API, ANALYSIS, COORDINATOR = prior.API, prior.ANALYSIS, prior.COORDINATOR
PROJECT, NUMBER = rolling.release.PROJECT, "157225724590"
PARENT = rolling.PARENT
SCHEDULER = "hirewiz-resume-scheduler@" + PROJECT + ".iam.gserviceaccount.com"
AGENT = "service-" + NUMBER + "@gcp-sa-cloudtasks.iam.gserviceaccount.com"
KIND, PROOF = (
    "hirewiz_internal_coordinator_completion_v1",
    "hirewiz_internal_admission_closed_v1",
)
SOURCE_FILES = (
    *prior.SOURCE_FILES,
    "infra/gcp/schema27_internal_coordinator_completion.py",
)
PARENT_KEYS = {
    "root",
    "plan",
    "plan_sha256",
    "journal",
    "stage_intent_sha256",
    "stage_ack_sha256",
}
WORKER = "backend/app/resume_upload_worker.py"
ENABLED = "backend/app/services/resume_upload_coordinator.py"
COMMAND = {
    "command": ["python"],
    "args": [
        "-m",
        "uvicorn",
        "app.resume_upload_worker:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8080",
        "--no-access-log",
    ],
}
PERMISSIONS = (
    "cloudtasks.queues.create",
    "cloudtasks.queues.get",
    "cloudtasks.queues.delete",
    "cloudtasks.tasks.create",
)


def need(value, code):
    rolling.need(value, "internal_coordinator_" + code)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source():
    value = prior.source()
    value["files"][SOURCE_FILES[-1]] = sha(ROOT / SOURCE_FILES[-1])
    return value


def historical_source(pin):
    need(
        set(pin) == {"release", "tree", "files"}
        and set(pin["files"]) == set(SOURCE_FILES)
        and all(re.fullmatch(r"[a-f0-9]{40}", pin[k]) for k in ("release", "tree"))
        and all(rolling.HASH.fullmatch(v) for v in pin["files"].values()),
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
    for name, stamp in pin["files"].items():
        raw = subprocess.check_output(
            ["git", "show", pin["release"] + ":" + name], cwd=ROOT, timeout=10
        )
        need(
            hashlib.sha256(raw).hexdigest() == stamp and sha(ROOT / name) == stamp,
            "source_bytes",
        )


def make_parent(*, root, plan_path, plan_sha256, journal_path):
    directory = Path(journal_path)
    return {
        "root": str(root),
        "plan": str(plan_path),
        "plan_sha256": plan_sha256,
        "journal": str(directory),
        "stage_intent_sha256": sha(
            directory / ("stage." + COORDINATOR + ".started.json")
        ),
        "stage_ack_sha256": sha(directory / ("stage." + COORDINATOR + ".ack.json")),
    }


def read_parent(parent):
    need(
        set(parent) == PARENT_KEYS
        and all(
            rolling.HASH.fullmatch(v) for k, v in parent.items() if k.endswith("sha256")
        ),
        "parent_scope",
    )
    old = prior.load(
        restore._private_read(parent["plan"], parent["plan_sha256"]),
        parent["plan_sha256"],
    )
    prior.historical_source(old.source)
    prior.parent_binding(old)
    root = Path(parent["root"])
    need(root.is_absolute() and root.resolve() == root, "parent_root")
    need(
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=10)
        .decode()
        .strip()
        == old.source["release"]
        and not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            timeout=10,
        ),
        "parent_frozen",
    )
    need(
        all(sha(root / name) == stamp for name, stamp in old.source["files"].items()),
        "parent_bytes",
    )
    journal = owned.Journal(Path(parent["journal"]), old)
    started, ack, checked = original.record(old, journal, old.coordinator, "stage")
    need(
        started is not None
        and ack is not None
        and checked is None
        and sha(journal.directory / ("stage." + COORDINATOR + ".started.json"))
        == parent["stage_intent_sha256"]
        and sha(journal.directory / ("stage." + COORDINATOR + ".ack.json"))
        == parent["stage_ack_sha256"],
        "genuine_stage_ack",
    )
    for name in (
        "stage." + COORDINATOR + ".checked",
        "route." + COORDINATOR + ".started",
        "route." + COORDINATOR + ".ack",
        "route." + COORDINATOR + ".checked",
        "complete",
    ):
        need(
            journal.read(name) is None and not journal.claimed(name),
            "parent_unsent_route_required",
        )
    return old, journal


def health_source(old, metadata):
    container = metadata["template"]["containers"][0]
    need(
        {k: container.get(k, []) for k in COMMAND} == COMMAND,
        "deployed_health_entrypoint",
    )
    values = {
        name: subprocess.check_output(
            ["git", "show", old.coordinator.source + ":" + name], cwd=ROOT, timeout=10
        )
        for name in (WORKER, ENABLED)
    }
    worker, enabled = (ast.parse(values[n]) for n in (WORKER, ENABLED))
    handler = next(
        n for n in worker.body if isinstance(n, ast.FunctionDef) and n.name == "health"
    )
    expected = cast(
        ast.FunctionDef,
        ast.parse(
            'def health():\n return {"ok": True, "role": "resume-upload-coordinator", "scan_enabled": enabled("scan"), "cleanup_enabled": enabled("cleanup")}'
        ).body[0],
    )
    need(
        ast.dump(
            ast.Module(body=handler.body, type_ignores=[]), include_attributes=False
        )
        == ast.dump(
            ast.Module(body=expected.body, type_ignores=[]), include_attributes=False
        ),
        "health_handler_source",
    )
    need(
        len(handler.decorator_list) == 1
        and ast.unparse(handler.decorator_list[0]) == "app.get('/api/health')",
        "health_route_source",
    )
    function = next(
        n
        for n in enabled.body
        if isinstance(n, ast.FunctionDef) and n.name == "enabled"
    )
    expected_enabled = ast.parse("""def enabled(mode: str) -> bool:
 return (mode in {"scan", "cleanup"} and os.getenv("SERVICE_ROLE") == "resume-upload-coordinator" and os.getenv("RESUME_UPLOAD_COORDINATOR_ENABLED") == "true" and (mode == "cleanup" or (os.getenv("RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED") == "true" and os.getenv("RESUME_DIRECT_UPLOAD_ENABLED") == "true")))""").body[
        0
    ]
    need(
        ast.dump(function, include_attributes=False)
        == ast.dump(expected_enabled, include_attributes=False),
        "scan_guard_source",
    )
    return {
        "release": old.coordinator.source,
        "image": old.coordinator.image,
        "files": {n: hashlib.sha256(v).hexdigest() for n, v in values.items()},
        "entrypoint": COMMAND,
        "health_role": "resume-upload-coordinator",
        "scan_closed_by_exact_native_flags": True,
        "response_json_observed": False,
    }


@dataclass(frozen=True)
class Plan:
    parent: dict
    analysis: dict
    nonce: str
    target: str
    audience: str
    scheduler_uid: str
    source: dict
    health_source: dict
    journal_location: dict
    capture_transport_calls: int = 0
    capture_metadata_bytes: list = field(default_factory=list)
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def queue(self):
        return PARENT + "/queues/hirewiz-coor-health-" + self.nonce[:16]

    def task(self, phase):
        need(phase in {"before", "after"}, "probe_phase")
        return self.queue + "/tasks/" + phase + "-" + self.nonce

    def url(self, phase):
        return self.target + "/api/health?hirewiz_probe=" + self.nonce + "-" + phase

    def validate(self):
        need(
            type(self) is Plan
            and self.kind == KIND
            and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
            and set(self.parent) == PARENT_KEYS
            and all(re.fullmatch(r"[1-9][0-9]{20}", v) for v in (self.scheduler_uid,)),
            "plan_scope",
        )
        need(
            type(self.capture_transport_calls) is int
            and 0 <= self.capture_transport_calls <= 3,
            "capture_budget",
        )
        need(
            len(self.capture_metadata_bytes) in {0, 3}
            and all(
                type(v) is int and 0 < v <= 8192 for v in self.capture_metadata_bytes
            ),
            "capture_metadata_bound",
        )
        for url in (self.target, self.audience):
            parsed = urlsplit(url)
            need(
                parsed.scheme == "https"
                and parsed.hostname
                and parsed.hostname.endswith(".run.app")
                and not (
                    parsed.username
                    or parsed.password
                    or parsed.query
                    or parsed.fragment
                    or parsed.path
                )
                and parsed.port in {None, 443},
                "probe_origin",
            )


def load(raw, digest):
    need(hashlib.sha256(raw).hexdigest() == digest and len(raw) <= 131072, "plan_file")
    value = rolling.release._json(raw)
    need(set(value) == {f.name for f in fields(Plan)}, "plan_shape")
    plan = Plan(**value)
    plan.validate()
    return plan


def queue_body(plan):
    return {
        "name": plan.queue,
        "rateLimits": {"maxDispatchesPerSecond": 1, "maxConcurrentDispatches": 1},
        "retryConfig": {"maxAttempts": 1, "maxRetryDuration": "0s"},
        "stackdriverLoggingConfig": {"samplingRatio": 1},
    }


def task_body(plan, phase):
    return {
        "task": {
            "name": plan.task(phase),
            "dispatchDeadline": "15s",
            "httpRequest": {
                "httpMethod": "GET",
                "url": plan.url(phase),
                "oidcToken": {
                    "serviceAccountEmail": SCHEDULER,
                    "audience": plan.audience,
                },
            },
        }
    }


def pause(plan, journal):
    original.require_unpaused(journal)
    old, _ = read_parent(plan.parent)
    for directory in (Path(plan.parent["journal"]), Path(old.parent["journal"])):
        try:
            (directory / "operator.pause-requested").lstat()
        except FileNotFoundError:
            continue
        need(False, "parent_operator_pause_requested")


def current(plan, boundary, *, routed):
    plan.validate()
    historical_source(plan.source)
    old, old_journal = read_parent(plan.parent)
    old_api_plan, _, api = prior.parent_binding(old)
    api_pin = next(p for p in old_api_plan.services if p.service == API)
    need(
        prior.exact_changed(boundary, api_pin, old_api_plan, "route") == api,
        "api_changed",
    )
    need(
        original.projection(boundary.service(ANALYSIS), ANALYSIS) == plan.analysis,
        "analysis_changed",
    )
    stage = old_journal.read("stage." + COORDINATOR + ".ack")
    op = boundary.operation(stage["operation"])
    need(op.get("done") is True and "error" not in op, "stage_not_ready")
    coor = prior.exact_changed(
        boundary, old.coordinator, old, "route" if routed else "stage"
    )
    metadata = boundary.service(COORDINATOR)
    tagged = [
        r
        for r in metadata.get("trafficStatuses", [])
        if r.get("tag") == old.tag
        and rolling.short_revision(r.get("revision", ""), COORDINATOR)
        == old.coordinator.revision
    ]
    need(health_source(old, metadata) == plan.health_source, "health_source_changed")
    need(
        metadata.get("ingress") == "INGRESS_TRAFFIC_INTERNAL_ONLY"
        and len(tagged) == 1
        and tagged[0].get("uri", "").rstrip("/") == plan.target
        and metadata.get("uri", "").rstrip("/") == plan.audience,
        "internal_destination_changed",
    )
    return {API: api, COORDINATOR: coor}


def capture(parent, boundary, nonce, directory):
    need(type(boundary) is NativeBoundary, "exact_boundary")
    old, _ = read_parent(parent)
    value = boundary.service(COORDINATOR)
    tags = [
        r
        for r in value.get("trafficStatuses", [])
        if r.get("tag") == old.tag
        and rolling.short_revision(r.get("revision", ""), COORDINATOR)
        == old.coordinator.revision
    ]
    need(len(tags) == 1, "stage_tag_required")
    scheduler, agent = boundary.principals()
    boundary._capture_principals = (scheduler, agent)
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
        old.analysis,
        nonce,
        tags[0]["uri"].rstrip("/"),
        value["uri"].rstrip("/"),
        scheduler["uniqueId"],
        source(),
        health_source(old, value),
        restore.scope.journal_location(type("L", (), {"directory": directory})()),
        capture_transport_calls=0,
    )
    current(plan, boundary, routed=False)
    boundary.preflight(plan)
    need(
        boundary.tasks(plan).call("GET", plan.queue, absent=True) is None,
        "fresh_queue_required",
    )
    plan = replace(
        plan,
        capture_transport_calls=boundary.tasks(plan).attempts,
        capture_metadata_bytes=list(getattr(boundary, "_metadata_bytes", [])),
    )
    boundary._tasks.plan = plan
    return plan


def _suffix(index):
    need(0 <= index < 100, "budget_index")
    return chr(97 + index // 26) + chr(97 + index % 26)


def reserve(journal, prefix, limit, value):
    for index in range(limit):
        name = prefix + "." + _suffix(index)
        if journal.read(name) is None:
            need(not journal.claimed(name), "budget_claim_incomplete")
            journal.record(name, value)
            return
    need(False, "operator_budget_exhausted")


class TasksHttp:
    """Stock retained manager, fixed named resources; physical mutation send once."""

    def __init__(self, plan):
        import certifi

        self.plan = plan
        self.journal = None
        self.cleanup_mode = False
        self.attempts = plan.capture_transport_calls
        self.stock = restore.native._stock_http()
        self.session = self.stock.session
        self.session.trust_env = False
        self.session.max_redirects = 0
        self.session.verify = certifi.where()
        for adapter in self.session.adapters.values():
            adapter.max_retries = adapter.max_retries.__class__(
                total=0, connect=0, read=0, redirect=0, status=0
            )
        self.armed = None
        self.sent = 0
        send = self.session.send

        def guarded(request, **kwargs):
            if (
                request.url == "https://oauth2.googleapis.com/token"
                and request.method == "POST"
            ):
                pass
            else:
                need(
                    self.armed is not None
                    and (request.method, request.url, request.body) == self.armed,
                    "physical_request_not_armed",
                )
                need(self.sent == 0, "physical_send_replay_refused")
                self.sent += 1
            kwargs["allow_redirects"] = False
            response = send(request, **kwargs)
            if request.url != "https://oauth2.googleapis.com/token":
                try:
                    raw = response.raw.read(8193, decode_content=True)
                    need(type(raw) is bytes and len(raw) <= 8192, "response_bound")
                    response._content, response._content_consumed = raw, True
                except Exception:
                    response.close()
                    raise
            return response

        self.session.send = guarded

    def reserve_rpc(self, method, path, body):
        if self.journal is None:
            need(self.attempts < 3, "capture_rpc_budget")
        else:
            reserve(
                self.journal,
                "tasks-rpc",
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
        plan = self.plan
        plan.validate()
        origin = "https://cloudtasks.googleapis.com/v2/"
        allowed = (
            method == "GET"
            and path == plan.queue
            or method == "DELETE"
            and path == plan.queue
            or method == "POST"
            and path == PARENT + "/queues"
            and body == queue_body(plan)
            or method == "POST"
            and path == plan.queue + "/tasks"
            and body in [task_body(plan, p) for p in ("before", "after")]
        )
        if path == "projects/-/serviceAccounts/" + SCHEDULER + ":testIamPermissions":
            origin = "https://iam.googleapis.com/v1/"
            allowed = method == "POST" and body == {
                "permissions": ["iam.serviceAccounts.actAs"]
            }
        if path == "projects/" + PROJECT + ":testIamPermissions":
            origin = "https://cloudresourcemanager.googleapis.com/v1/"
            allowed = method == "POST" and body == {"permissions": list(PERMISSIONS)}
        need(allowed and self.armed is None, "request_scope")
        raw = None if body is None else rolling.canonical(body)
        self.reserve_rpc(method, path, body)
        self.armed = (method, origin + path, raw)
        self.sent = 0
        try:
            response = self.session.request(
                method,
                origin + path,
                data=raw,
                headers={"Content-Type": "application/json"},
                timeout=(5, 20),
                allow_redirects=False,
                stream=True,
            )
            try:
                if response.status_code == 404 and absent and method == "GET":
                    return None
                if method == "DELETE" and path == plan.queue:
                    need(
                        response.status_code in {200, 204}
                        and self.sent == 1
                        and response.content.strip() in {b"", b"{}"},
                        "native_delete_ack_unavailable_reconcile_only",
                    )
                    return {}
                need(
                    response.status_code in {200, 201} and self.sent == 1,
                    "native_response_unavailable_reconcile_only",
                )
                result = rolling.release._json(response.content)
                need(type(result) is dict, "native_response_shape")
                return result
            finally:
                response.close()
        finally:
            self.armed = None

    def close(self):
        self.session.close()


class RouteRun(prior.CompletionRun):
    def patch(self, service, body, mask):
        need(service == COORDINATOR and mask == "traffic", "only_unsent_route")
        return super().patch(service, body, mask)


class NativeBoundary(prior.NativeBoundary):
    def __init__(self):
        super().__init__()
        self._run: RouteRun | None = None
        self._tasks = None
        self._capture_principals = None
        self._metadata_bytes = []

    @property
    def run(self):
        if self._run is None:
            self._run = RouteRun()
        return self._run

    def patch(self, service, body, mask):
        need(service == COORDINATOR and mask == "traffic", "only_unsent_route")
        return self.run.patch(service, body, mask)

    def tasks(self, plan):
        if self._tasks is None:
            self._tasks = TasksHttp(plan)
        need(self._tasks.plan == plan, "tasks_plan_changed")
        return self._tasks

    def health_metadata(self, *args):
        need(len(self._metadata_bytes) < 4, "topology_read_count")
        value = self.observer._cloud(*args)
        size = len(rolling.canonical(value))
        need(0 < size <= 8192, "health_metadata_response_bound")
        self._metadata_bytes.append(size)
        return value

    def principals(self):
        scheduler = self.health_metadata(
            "iam", "service-accounts", "describe", SCHEDULER
        )
        # Google-managed service agents belong to the provider, not a normal
        # customer-owned ServiceAccount GET. Identity derives from projectNumber
        # and is verified by its authoritative existing project IAM binding.
        return scheduler, {
            "email": AGENT,
            "projectNumber": NUMBER,
            "google_managed": True,
        }

    def preflight(self, plan):
        if self._capture_principals is not None:
            scheduler, agent = self._capture_principals
            self._capture_principals = None
        else:
            self._metadata_bytes = []
            scheduler, agent = self.principals()
        need(
            scheduler.get("email") == SCHEDULER
            and scheduler.get("projectId") == PROJECT
            and scheduler.get("uniqueId") == plan.scheduler_uid
            and scheduler.get("disabled", False) is False,
            "scheduler_identity_changed",
        )
        need(
            agent
            == {
                "email": "service-"
                + NUMBER
                + "@gcp-sa-cloudtasks.iam.gserviceaccount.com",
                "projectNumber": NUMBER,
                "google_managed": True,
            },
            "managed_agent_identity",
        )
        policy = self.health_metadata(
            "run",
            "services",
            "get-iam-policy",
            COORDINATOR,
            "--region",
            rolling.release.REGION,
        )
        need(
            [
                b
                for b in policy.get("bindings", [])
                if b.get("role") == "roles/run.invoker"
            ]
            == [
                {
                    "role": "roles/run.invoker",
                    "members": ["serviceAccount:" + SCHEDULER],
                }
            ],
            "scheduler_not_invoker",
        )
        project = self.health_metadata("projects", "get-iam-policy", PROJECT)
        need(
            any(
                b.get("role") == "roles/cloudtasks.serviceAgent"
                and "serviceAccount:" + AGENT in b.get("members", [])
                and not b.get("condition")
                for b in project.get("bindings", [])
            ),
            "tasks_service_agent_role_required",
        )
        transport = self.tasks(plan)
        need(
            transport.call(
                "POST",
                "projects/-/serviceAccounts/" + SCHEDULER + ":testIamPermissions",
                {"permissions": ["iam.serviceAccounts.actAs"]},
            )
            == {"permissions": ["iam.serviceAccounts.actAs"]},
            "owner_actas_required",
        )
        result = transport.call(
            "POST",
            "projects/" + PROJECT + ":testIamPermissions",
            {"permissions": list(PERMISSIONS)},
        )
        need(
            set(result.get("permissions", [])) == set(PERMISSIONS),
            "owner_tasks_permissions_required",
        )

    def request_logs(self, plan, phase, created):
        old, _ = read_parent(plan.parent)
        query = (
            'resource.type="cloud_run_revision" AND resource.labels.service_name="'
            + COORDINATOR
            + '" AND resource.labels.revision_name="'
            + old.coordinator.revision
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
            "--project=" + PROJECT,
            "--account=harshilyogeshbhai886@gmail.com",
            "--billing-project=" + PROJECT,
            "--format=json(timestamp,insertId,logName,resource.type,resource.labels.project_id,resource.labels.location,resource.labels.service_name,resource.labels.revision_name,httpRequest.requestMethod,httpRequest.requestUrl,httpRequest.status,httpRequest.latency)",
            "--quiet",
        )
        need(type(raw) is bytes and len(raw) <= 8192, "health_log_response_bound")
        return rolling.release._json(raw)

    def close(self):
        if self._tasks is not None:
            self._tasks.close()
        super().close()


def _record(journal, name):
    started, ack = journal.read(name + ".started"), journal.read(name + ".ack")
    need(
        not journal.claimed(name + ".started") or started is not None,
        "incomplete_intent_reconcile_only",
    )
    if started is None:
        need(ack is None and not journal.claimed(name + ".ack"), "orphan_ack")
    else:
        need(
            ack is not None and ack.get("intent_sha256") == rolling.sha(started),
            "missing_ack_reconcile_only",
        )
    return started, ack


def _queue(value, plan):
    need(
        type(value) is dict
        and value.get("name") == plan.queue
        and value.get("state") == "RUNNING"
        and value.get("rateLimits", {}).get("maxDispatchesPerSecond") == 1
        and value.get("rateLimits", {}).get("maxConcurrentDispatches") == 1
        and value.get("retryConfig", {}).get("maxAttempts") == 1
        and value.get("retryConfig", {}).get("maxRetryDuration", "0s") == "0s"
        and value.get("httpTarget") in (None, {}),
        "owned_queue_changed",
    )


def _log_receipt(plan, phase, ack, entries, states, *, observed_at=None):
    if entries == []:
        return None
    old, _ = read_parent(plan.parent)
    need(type(entries) is list and len(entries) == 1, "probe_request_count_unexpected")
    entry = entries[0]
    resource = entry.get("resource", {})
    http = entry.get("httpRequest", {})
    need(
        resource
        == {
            "type": "cloud_run_revision",
            "labels": {
                "project_id": PROJECT,
                "location": rolling.release.REGION,
                "service_name": COORDINATOR,
                "revision_name": old.coordinator.revision,
            },
        }
        and set(entry)
        == {"timestamp", "insertId", "logName", "resource", "httpRequest"}
        and set(http) <= {"status", "requestMethod", "requestUrl", "latency"}
        and entry.get("logName")
        == "projects/" + PROJECT + "/logs/run.googleapis.com%2Frequests"
        and type(entry.get("insertId")) is str
        and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", entry["insertId"])
        and http.get("status") == 200
        and http.get("requestMethod") == "GET"
        and http.get("requestUrl") == plan.url(phase),
        "probe_http200_not_verified",
    )
    need(
        ack["name"] == plan.task(phase) and ack["dispatchDeadline"] == "15s",
        "probe_ack_identity",
    )
    need(
        type(http.get("latency")) is str
        and re.fullmatch(r"[0-9]{1,3}(?:\.[0-9]{1,9})?s", http["latency"])
        and float(http["latency"][:-1]) <= 270,
        "probe_latency_budget_exceeded",
    )
    created = datetime.fromisoformat(ack["createTime"].replace("Z", "+00:00"))
    stamp = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    now = datetime.now(UTC)
    observed = now if observed_at is None else datetime.fromisoformat(observed_at)
    need(
        created.tzinfo is not None
        and stamp.tzinfo is not None
        and observed.tzinfo is not None
        and created <= stamp <= observed <= now
        and (observed - stamp).total_seconds() <= 600,
        "probe_time_unverified",
    )
    return {
        "kind": "native_internal_request_log_health_v1",
        "phase": phase,
        "task": plan.task(phase),
        "task_create_ack_sha256": rolling.sha(ack),
        "request_log": entry,
        "native_states": states,
        "response_json_observed": False,
        "scan_disabled_evidence": plan.health_source,
        "observed_at": observed.isoformat(),
        "single_observed_request": True,
        "rare_duplicate_delivery_impossible": False,
    }


def checked_receipt(plan, journal, phase):
    ack = journal.read(phase + ".ack")
    receipt = journal.read(phase + ".checked")
    need(ack is not None and receipt is not None, "internal_health_required")
    expected = _log_receipt(
        plan,
        phase,
        ack,
        [receipt["request_log"]],
        receipt["native_states"],
        observed_at=receipt["observed_at"],
    )
    need(receipt == expected | {"plan_sha256": plan.digest}, "internal_health_custody")
    return receipt


def fresh_receipt(plan, boundary, journal, phase):
    receipt = checked_receipt(plan, journal, phase)
    ack = journal.read(phase + ".ack")
    entries = boundary.request_logs(plan, phase, ack["createTime"])
    need(entries == [receipt["request_log"]], "probe_duplicate_or_log_changed")
    _log_receipt(plan, phase, ack, entries, receipt["native_states"])
    return receipt


def record_scope(plan, journal, name):
    started, ack = _record(journal, name)
    if started is None:
        return None, None
    need(
        set(started)
        == {"target", "request", "body_sha256", "automatic_replay", "plan_sha256"}
        and started["target"] == name
        and started["automatic_replay"] is False
        and started["body_sha256"] == rolling.sha(started["request"]),
        "intent_scope",
    )
    request = started["request"]
    if name == "queue":
        need(
            request == queue_body(plan) and ack["name"] == plan.queue,
            "queue_intent_scope",
        )
    elif name in {"before", "after"}:
        need(
            request == task_body(plan, name)
            and set(ack)
            == {
                "name",
                "createTime",
                "dispatchDeadline",
                "intent_sha256",
                "plan_sha256",
            }
            and ack["name"] == plan.task(name)
            and ack["dispatchDeadline"] == "15s",
            "task_intent_scope",
        )
    elif name == "route":
        old, _ = read_parent(plan.parent)
        need(
            set(request) == {"name", "etag", "traffic"}
            and request["name"] == PARENT + "/services/" + COORDINATOR
            and type(request["etag"]) is str
            and request["etag"]
            and request["traffic"] == original.traffic(old, old.coordinator, "route")
            and set(ack) == {"operation", "intent_sha256", "plan_sha256"}
            and rolling.OPERATION.fullmatch(ack["operation"]),
            "route_intent_scope",
        )
    elif name == "delete":
        need(
            request == {"queue": plan.queue}
            and set(ack) == {"intent_sha256", "plan_sha256"},
            "delete_intent_scope",
        )
    else:
        need(False, "unknown_record")
    return started, ack


def _once(plan, journal, name, body, send, *, cleanup_only=False):
    if cleanup_only:
        need(name == "delete" and body == {"queue": plan.queue}, "cleanup_scope")
    else:
        pause(plan, journal)
    started = {
        "target": name,
        "request": body,
        "body_sha256": rolling.sha(body),
        "automatic_replay": False,
    }
    journal.record(name + ".started", started)
    started = journal.read(name + ".started")
    if not cleanup_only:
        pause(plan, journal)
    response = send()
    journal.record(name + ".ack", response | {"intent_sha256": rolling.sha(started)})


def advance(plan, boundary, journal, *, observe=False):
    need(
        type(boundary) is NativeBoundary
        and type(journal) is owned.Journal
        and journal.plan_hash == plan.digest,
        "exact_owner",
    )
    need(source() == plan.source, "mutator_source_changed")
    with journal.locked():
        boundary.tasks(plan).journal = journal
        reserve(
            journal,
            "invocation",
            40,
            {"observe": observe, "native_replay_authorized": False},
        )
        _, route_ack = record_scope(plan, journal, "route")
        if route_ack:
            operation = boundary.operation(route_ack["operation"])
            need("error" not in operation, "route_failed_reconcile_only")
            if operation.get("done") is not True:
                return {"status": "same_route_operation_pending", "native_mutations": 0}
        states = current(plan, boundary, routed=route_ack is not None)
        for name in ("queue", "before", "after", "delete"):
            record_scope(plan, journal, name)
        queue_started, _queue_ack = record_scope(plan, journal, "queue")
        if queue_started is None:
            if observe:
                return {"status": "queue_not_created", "native_mutations": 0}
            need(
                boundary.tasks(plan).call("GET", plan.queue, absent=True) is None,
                "queue_exists",
            )
            _once(
                plan,
                journal,
                "queue",
                queue_body(plan),
                lambda: boundary.tasks(plan).call(
                    "POST", PARENT + "/queues", queue_body(plan)
                ),
            )
            return {"status": "queue_created", "native_mutations": 1}
        if journal.read("delete.ack") is not None:
            need(
                boundary.tasks(plan).call("GET", plan.queue, absent=True) is None,
                "queue_cleanup_unverified",
            )
            need(
                journal.read("before.checked") is not None
                and journal.read("after.checked") is not None
                and route_ack is not None,
                "incomplete_health",
            )
            result = proof(
                plan,
                states,
                fresh_receipt(plan, boundary, journal, "before"),
                fresh_receipt(plan, boundary, journal, "after"),
                journal.read("topology.checked"),
            )
            if not observe and journal.read("complete") is None:
                journal.record("complete", result)
            need(
                journal.read("complete") == result | {"plan_sha256": plan.digest},
                "completion_scope",
            )
            return result | {"native_mutations": 0}
        _queue(boundary.tasks(plan).call("GET", plan.queue), plan)
        for phase in ("before", "after"):
            if phase == "after" and route_ack is None:
                before = fresh_receipt(plan, boundary, journal, "before")
                need(
                    before["native_states"] == states, "before_health_snapshot_changed"
                )
                if observe:
                    return {"status": "route_not_started", "native_mutations": 0}
                pause(plan, journal)
                fresh = current(plan, boundary, routed=False)
                need(fresh == states, "pre_route_changed")
                old, _ = read_parent(plan.parent)
                value = boundary.service(COORDINATOR)
                body = {
                    "name": value["name"],
                    "etag": value["etag"],
                    "traffic": original.traffic(old, old.coordinator, "route"),
                }
                for target in body["traffic"]:
                    if "tag" in target:
                        prior.require_tag(COORDINATOR, target["tag"])
                _once(
                    plan,
                    journal,
                    "route",
                    body,
                    lambda body=body: {
                        "operation": boundary.patch(COORDINATOR, body, "traffic")[
                            "name"
                        ]
                    },
                )
                return {"status": "route_acknowledged", "native_mutations": 1}
            started, ack = record_scope(plan, journal, phase)
            if started is None:
                if observe:
                    return {
                        "status": phase + "_probe_not_started",
                        "native_mutations": 0,
                    }
                if phase == "after":
                    fresh_receipt(plan, boundary, journal, "before")
                if phase == "after":
                    topology = journal.read("topology.checked")
                    if topology is None:
                        need(
                            journal.read("topology.started") is None
                            and not journal.claimed("topology.started"),
                            "topology_reconcile_only",
                        )
                        journal.record(
                            "topology.started", {"maximum_stock_metadata_reads": 3}
                        )
                        boundary.preflight(plan)
                        journal.record(
                            "topology.checked",
                            {
                                "metadata_response_bytes": list(
                                    getattr(boundary, "_metadata_bytes", [])
                                ),
                                "stock_metadata_parent_allowance": True,
                            },
                        )
                need(
                    current(plan, boundary, routed=route_ack is not None) == states,
                    "pre_probe_changed",
                )

                def create_task(phase=phase):
                    response = boundary.tasks(plan).call(
                        "POST", plan.queue + "/tasks", task_body(plan, phase)
                    )
                    need(
                        response.get("name") == plan.task(phase)
                        and type(response.get("createTime")) is str
                        and response.get("dispatchDeadline") == "15s",
                        "task_ack_identity",
                    )
                    return {
                        "name": response["name"],
                        "createTime": response["createTime"],
                        "dispatchDeadline": response["dispatchDeadline"],
                    }

                _once(plan, journal, phase, task_body(plan, phase), create_task)
                return {"status": phase + "_probe_created", "native_mutations": 1}
            checked = journal.read(phase + ".checked")
            if checked is None:
                entries = boundary.request_logs(plan, phase, ack["createTime"])
                receipt = _log_receipt(plan, phase, ack, entries, states)
                if receipt is None:
                    return {
                        "status": phase + "_probe_log_pending",
                        "native_mutations": 0,
                    }
                need(
                    current(plan, boundary, routed=route_ack is not None) == states,
                    "probe_preimage_changed",
                )
                if observe:
                    return {
                        "status": phase + "_probe_observed_unrecorded",
                        "native_mutations": 0,
                    }
                journal.record(phase + ".checked", receipt)
                return {
                    "status": phase + "_internal_health_verified",
                    "native_mutations": 0,
                }
            checked = checked_receipt(plan, journal, phase)
            need(
                checked["native_states"] == states
                if phase == "after" or route_ack is None
                else True,
                "health_snapshot_changed",
            )
        if observe:
            return {"status": "cleanup_not_started", "native_mutations": 0}
        fresh_receipt(plan, boundary, journal, "before")
        fresh_receipt(plan, boundary, journal, "after")
        _once(
            plan,
            journal,
            "delete",
            {"queue": plan.queue},
            lambda: boundary.tasks(plan).call("DELETE", plan.queue),
        )
        return {"status": "queue_delete_acknowledged", "native_mutations": 1}


def cleanup(plan, boundary, journal):
    """Delete only the genuinely ACK-owned ephemeral queue; never retry DELETE."""
    need(
        type(boundary) is NativeBoundary
        and type(journal) is owned.Journal
        and journal.plan_hash == plan.digest,
        "exact_owner",
    )
    need(source() == plan.source, "mutator_source_changed")
    read_parent(plan.parent)
    with journal.locked():
        boundary.tasks(plan).journal = journal
        boundary.tasks(plan).cleanup_mode = True
        _, ack = record_scope(plan, journal, "queue")
        need(ack is not None, "owned_queue_ack_required")
        started, deleted = record_scope(plan, journal, "delete")
        if started is None:
            _queue(boundary.tasks(plan).call("GET", plan.queue), plan)
            _once(
                plan,
                journal,
                "delete",
                {"queue": plan.queue},
                lambda: boundary.tasks(plan).call("DELETE", plan.queue),
                cleanup_only=True,
            )
            return {"status": "queue_delete_acknowledged", "native_mutations": 1}
        need(
            deleted is not None
            and boundary.tasks(plan).call("GET", plan.queue, absent=True) is None,
            "cleanup_unverified",
        )
        return {"status": "owned_queue_absent", "native_mutations": 0}


def proof(plan, states, before, after, topology):
    old, _ = read_parent(plan.parent)
    first, _, _ = prior.read_parent(old.parent)
    return {
        "kind": PROOF,
        "stock_topology_receipt_sha256": rolling.sha(topology),
        "stock_metadata_response_bytes": plan.capture_metadata_bytes
        + topology["metadata_response_bytes"],
        "health_control_response_limit_bytes": 98 * 8193,
        "health_receipt_sha256": {
            p: rolling.sha(v) for p, v in (("before", before), ("after", after))
        },
        "services": states,
        "analysis_unchanged": plan.analysis,
        "new_admission_closed": True,
        "parent_restore_plan_sha256": first.parent["plan_sha256"],
        "parent_restore_complete_sha256": first.parent["complete_sha256"],
        "stage_completion_plan_sha256": plan.parent["plan_sha256"],
        "genuine_stage_ack_sha256": plan.parent["stage_ack_sha256"],
        "saved_api_reads_preserved": True,
        "response_json_observed": False,
        "internal_HTTP200_logs_verified": True,
        "scan_disabled_evidence": plan.health_source,
        "owned_probe_queue_removed": True,
        "old_external_health_claimed": False,
        "document_processing_enabled": False,
        "payment_activation_authorized": False,
        "replay_authorized": False,
    }


class ClosedInternalAdmission:
    def __init__(self, plan, journal):
        need(type(plan) is Plan and type(journal) is owned.Journal, "closed_type")
        self.plan, self.journal = plan, journal
        self.verify_custody()

    @property
    def restore_parent(self):
        old, _ = read_parent(self.plan.parent)
        first, _, _ = prior.read_parent(old.parent)
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
        read_parent(self.plan.parent)
        self.journal.verify()
        old, _ = read_parent(self.plan.parent)
        need(
            health_source(old, {"template": {"containers": [COMMAND]}})
            == self.plan.health_source,
            "handler_manifest_changed",
        )
        for name in ("queue", "before", "route", "after", "delete"):
            started, ack = record_scope(self.plan, self.journal, name)
            need(started is not None and ack is not None, "incomplete_custody")
        before, after = (
            checked_receipt(self.plan, self.journal, p) for p in ("before", "after")
        )
        need(
            before["request_log"]["insertId"] != after["request_log"]["insertId"],
            "duplicate_probe_identity",
        )
        a, b = before["native_states"], after["native_states"]
        need(
            set(a) == set(b) == {API, COORDINATOR}
            and a[API] == b[API]
            and a[COORDINATOR]["generation"] == old.coordinator.before["generation"] + 1
            and b[COORDINATOR]["generation"] == old.coordinator.before["generation"] + 2
            and a[COORDINATOR]["traffic"]
            == original.traffic(old, old.coordinator, "stage")
            and b[COORDINATOR]["traffic"]
            == original.traffic(old, old.coordinator, "route")
            and {
                k: v
                for k, v in a[COORDINATOR].items()
                if k not in {"generation", "etag", "traffic"}
            }
            == {
                k: v
                for k, v in b[COORDINATOR].items()
                if k not in {"generation", "etag", "traffic"}
            },
            "health_snapshot_transition",
        )
        topology = self.journal.read("topology.checked")
        need(
            self.journal.read("topology.started") is not None
            and topology is not None
            and topology["stock_metadata_parent_allowance"] is True
            and len(topology["metadata_response_bytes"]) in {0, 3}
            and all(
                type(v) is int and 0 < v <= 8192
                for v in topology["metadata_response_bytes"]
            ),
            "topology_custody",
        )
        states = after["native_states"]
        complete = self.journal.read("complete")
        need(
            complete
            == proof(self.plan, states, before, after, topology)
            | {"plan_sha256": self.plan.digest},
            "complete_scope",
        )
        return complete

    def verify_current(self, boundary=None):
        complete = self.verify_custody()
        own = boundary is None
        boundary = NativeBoundary() if own else boundary
        try:
            states = current(self.plan, boundary, routed=True)
            need(states == complete["services"], "current_changed")
            return states
        finally:
            if own:
                boundary.close()


def load_closed_internal(*, plan_path, plan_sha256, journal_path):
    plan = load(restore._private_read(plan_path, plan_sha256), plan_sha256)
    return ClosedInternalAdmission(plan, owned.Journal(Path(journal_path), plan))


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
            parent = rolling.release._json(
                restore._private_read(args.parent, args.parent_sha256)
            )
            plan = capture(parent, boundary, args.nonce, args.journal)
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
    except Exception as error:  # noqa: BLE001 - finite secret-safe boundary diagnostics
        result = restore.mixed.diagnostics.diagnostic(error, "observe")
        if re.fullmatch(r"internal_coordinator_[a-z_]{1,90}", str(error)):
            result["fixed_code"] = str(error)
        print(rolling.canonical(result).decode())
        return 1
    finally:
        boundary.close()


if __name__ == "__main__":
    raise SystemExit(main())
