"""Finite same-image maintenance before schema25 capture; no deployment or DDL.

Close producers first; accepted work drains on the existing running workers.
Only then close workers and pause queues. The existing coordinator operator
remains separate. Restore is for a safe abort, never a rollback over new code.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import rolling_native as native
import rolling_release as rolling
import schema25_coordinator_pause as coordinator
import unified_credit_schema25_release as schema25

SERVICES = rolling.release.SERVICES
QUEUES = rolling.release.QUEUES
SCHEDULES = ("hirewiz-resume-cleanup", "hirewiz-maintenance", "hirewiz-resume-scan")
FLAGS = schema25.OFF
RESOURCE_NAMES = {"queue": QUEUES, "schedule": SCHEDULES}
RESOURCE_PATHS = {"queue": ("https://cloudtasks.googleapis.com/v2/", "/queues/"),
                  "schedule": ("https://cloudscheduler.googleapis.com/v1/", "/jobs/")}
RESOURCE_VOLATILE = {"state", "lastAttemptTime", "scheduleTime", "status", "userUpdateTime"}
# A Cloud Run Revision's serving template is immutable, but its observed
# status and etag can settle after the Service that names it becomes Ready.
# The captured full Revision digest is the fast path. If only that digest has
# drifted, accept a bounded live readback of the captured immutable template.
REVISION_READ_OUTPUTS = frozenset({
    "name", "service", "uid", "generation", "observedGeneration", "createTime",
    "updateTime", "creator", "etag", "conditions", "scalingStatus",
    "launchStage", "logUri",
})


def need(value, code):
    rolling.need(value, "schema25_maintenance_" + code)


def configuration(row):
    return rolling.sha({key: value for key, value in row.items() if key not in RESOURCE_VOLATILE})


def ready(row, service):
    need(service in SERVICES and row.get("name") == rolling.PARENT + "/services/" + service
         and row.get("reconciling", False) is False
         and str(row.get("generation")) == str(row.get("observedGeneration"))
         and row.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
         and row.get("latestReadyRevision") == row.get("latestCreatedRevision")
         and bool(row.get("etag")), "service_not_ready")
    env = rolling.environment(row["template"])
    image = row["template"]["containers"][0].get("image", "")
    source = env.get("APP_RELEASE", {}).get("value", "")
    need(row["template"].get("serviceAccount") == rolling.RUNTIME
         and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", image)
         and re.fullmatch(r"[a-f0-9]{40}", source)
         and env.get("SERVICE_ROLE", {}).get("value") == ("api" if service == SERVICES[0] else "worker"),
         "source_or_identity_changed")
    return source, image


@dataclass(frozen=True)
class ServicePin:
    name: str
    uid: str
    generation: int
    service_sha256: str
    static_sha256: str
    template_sha256: str
    template_keys: tuple[str, ...]
    source: str
    image: str
    traffic: tuple[dict, ...]
    prior_revision: str
    revisions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ResourcePin:
    kind: str
    name: str
    state: str
    configuration_sha256: str


@dataclass(frozen=True)
class MaintenancePlan:
    nonce: str
    services: tuple[ServicePin, ...]
    resources: tuple[ResourcePin, ...]

    def validate(self):
        need(re.fullmatch(r"[a-f0-9]{32}", self.nonce)
             and tuple(pin.name for pin in self.services) == SERVICES
             and tuple((pin.kind, pin.name) for pin in self.resources)
             == tuple((kind, name) for kind, names in RESOURCE_NAMES.items() for name in names), "plan_scope_invalid")
        for pin in self.services:
            need(re.fullmatch(r"[a-f0-9-]{36}", pin.uid) and type(pin.generation) is int and pin.generation > 0
                 and all(rolling.HASH.fullmatch(value) for value in (pin.service_sha256, pin.static_sha256, pin.template_sha256))
                 and re.fullmatch(r"[a-f0-9]{40}", pin.source)
                 and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", pin.image)
                 and 1 <= len(pin.template_keys) <= 40 and len(set(pin.template_keys)) == len(pin.template_keys)
                 and 1 <= len(pin.revisions) <= 21 and len(dict(pin.revisions)) == len(pin.revisions), "service_pin_invalid")
            traffic = rolling.pinned_traffic(pin.name, {"trafficStatuses": list(pin.traffic)})
            need(traffic == list(pin.traffic) and {pin.prior_revision, *[r["revision"] for r in traffic]} == set(dict(pin.revisions)), "traffic_pin_invalid")
            for name, digest in pin.revisions:
                need(rolling.short_revision(name, pin.name) == name and rolling.HASH.fullmatch(digest), "revision_pin_invalid")
        for resource in self.resources:
            need(resource.state in ({"RUNNING", "PAUSED"} if resource.kind == "queue" else {"ENABLED", "PAUSED"})
                 and rolling.HASH.fullmatch(resource.configuration_sha256), "resource_pin_invalid")

    def revision(self, service, phase):
        need(service in SERVICES and phase in {"close", "restore"}, "phase_invalid")
        return service + "-m25-" + self.nonce[:12] + "-" + phase


def capture(run, controls, nonce):
    pins = []
    for service in SERVICES:
        row = run.service(service)
        source, image = ready(row, service)
        traffic = rolling.pinned_traffic(service, row)
        prior = rolling.short_revision(row["latestReadyRevision"], service)
        keys = tuple(sorted(key for key in row["template"] if key != "revision"))
        revisions = {}
        for name in {prior, *[target["revision"] for target in traffic]}:
            revision = run.revision(service, name)
            need(revision is not None, "prior_revision_missing")
            env = rolling.environment(revision)
            need(env.get("APP_RELEASE", {}).get("value") == source
                 and revision["containers"][0].get("image") == image
                 and revision.get("serviceAccount") == rolling.RUNTIME, "reachable_source_changed")
            need({key: revision.get(key) for key in keys} == {key: row["template"][key] for key in keys},
                 "reachable_configuration_differs")
            revisions[name] = rolling.sha(revision)
        prior_row = run.revision(service, prior)
        need({key: prior_row.get(key) for key in keys} == {key: row["template"][key] for key in keys}, "serving_template_changed")
        need(run.service(service) == row, "capture_changed")
        pins.append(ServicePin(service, row["uid"], int(row["generation"]), rolling.sha(row), rolling.static_sha(row),
                               coordinator.template_hash(row["template"]), keys, source, image, tuple(traffic), prior,
                               tuple(sorted(revisions.items()))))
    resources = []
    for kind, names in RESOURCE_NAMES.items():
        for name in names:
            row = controls.get(kind, name)
            resources.append(ResourcePin(kind, name, row.get("state"), configuration(row)))
    plan = MaintenancePlan(nonce, tuple(pins), tuple(resources))
    plan.validate()
    return plan


def pinned_revision(pin, run, name, digest):
    row = run.revision(pin.name, name)
    need(row is not None, "prior_revision_changed")
    if rolling.sha(row) != digest:
        # Older captured plans retain a full Revision digest. Cloud Run may
        # update observed status/etag without changing the revision's runtime
        # configuration. In that case, use the independently captured serving
        # template hash, source, image and identity; never accept an unknown
        # configuration field or a different revision/service.
        need(set(pin.template_keys) <= set(row)
             and set(row) <= set(pin.template_keys) | REVISION_READ_OUTPUTS
             and row.get("name") == rolling.PARENT + "/services/" + pin.name + "/revisions/" + name
             and row.get("service") == pin.name
             and type(row.get("uid")) is str
             and re.fullmatch(r"[a-f0-9-]{36}", row["uid"])
             and str(row.get("generation")) == str(row.get("observedGeneration"))
             and type(row.get("conditions")) is list
             and any(type(condition) is dict and condition.get("type") == "Ready"
                     and condition.get("state") == "CONDITION_SUCCEEDED"
                     for condition in row["conditions"])
             and type(row.get("containers")) is list and len(row["containers"]) == 1
             and type(row["containers"][0]) is dict
             and row.get("serviceAccount") == rolling.RUNTIME
             and row["containers"][0].get("image") == pin.image
             and rolling.environment(row).get("APP_RELEASE", {}).get("value") == pin.source
             and coordinator.template_hash({key: row[key] for key in pin.template_keys})
                 == pin.template_sha256,
             "prior_revision_changed")
    return row


def desired(plan, pin, run, phase):
    rows = {}
    for name, digest in pin.revisions:
        rows[name] = pinned_revision(pin, run, name, digest)
    template = {key: copy.deepcopy(rows[pin.prior_revision].get(key)) for key in pin.template_keys}
    need(coordinator.template_hash(template) == pin.template_sha256, "prior_template_changed")
    if phase == "close":
        env = rolling.environment(template)
        for flag in FLAGS:
            env[flag] = {"name": flag, "value": "false"}
        template["containers"][0]["env"] = list(env.values())
    template["revision"] = plan.revision(pin.name, phase)
    traffic = copy.deepcopy(list(pin.traffic))
    if phase == "close":
        for target in traffic:
            target["revision"] = plan.revision(pin.name, phase)
    return template, sorted(traffic, key=rolling.canonical)


def service_readback(plan, pin, run, phase, started):
    row = run.service(pin.name)
    ready(row, pin.name)
    template, traffic = desired(plan, pin, run, phase)
    need(row["uid"] == pin.uid and int(row["generation"]) == started["prior_generation"] + 1
         and rolling.static_sha(row) == pin.static_sha256
         and rolling.short_revision(row["latestReadyRevision"], pin.name) == plan.revision(pin.name, phase)
         and coordinator.template_hash(row["template"]) == coordinator.template_hash(template)
         and rolling.pinned_traffic(pin.name, row) == traffic, "service_readback_changed")
    revision = run.revision(pin.name, plan.revision(pin.name, phase))
    need(revision is not None and {key: revision.get(key) for key in pin.template_keys}
         == {key: template.get(key) for key in pin.template_keys}, "revision_readback_changed")
    return row


def service_action(plan, pin, run, journal, phase, *, reconcile=False):
    marker = "service." + pin.name + "." + phase
    started, ack = journal.read(marker + ".started"), journal.read(marker + ".acknowledged")
    if journal.read(marker + ".checked") is not None:
        need(started is not None, "checked_without_started")
        service_readback(plan, pin, run, phase, started)
        return True
    if started is None:
        need(not reconcile, "reconcile_never_starts_write")
        current = run.service(pin.name)
        ready(current, pin.name)
        if phase == "close":
            need(rolling.sha(current) == pin.service_sha256, "service_preimage_changed")
        else:
            close = journal.read("service." + pin.name + ".close.started")
            need(close is not None and journal.read("service." + pin.name + ".close.checked") is not None, "close_not_observed")
            service_readback(plan, pin, run, "close", close)
        template, traffic = desired(plan, pin, run, phase)
        need(run.revision(pin.name, plan.revision(pin.name, phase)) is None and run.service(pin.name) == current, "service_preimage_changed")
        body = {"name": current["name"], "etag": current["etag"], "template": template, "traffic": traffic}
        journal.record(marker + ".started", {"prior_generation": int(current["generation"]), "request_sha256": rolling.sha(body), "replay_authorized": False})
        started = journal.read(marker + ".started")
        operation = run.patch(pin.name, body, "template,traffic")
        need(rolling.OPERATION.fullmatch(operation.get("name", "")), "unknown_acknowledgement")
        journal.record(marker + ".acknowledged", {"operation": operation["name"]})
        ack = journal.read(marker + ".acknowledged")
    elif ack is None:
        need(reconcile, "unknown_write_reconcile_only")
    if ack is not None:
        operation = run.operation(ack["operation"])
        need(operation.get("name") == ack["operation"] and "error" not in operation, "operation_failed_reconcile_only")
        if operation.get("done") is not True:
            return False
    row = service_readback(plan, pin, run, phase, started)
    if journal.read(marker + ".checked") is None:
        journal.record(marker + ".checked", {"generation": int(row["generation"]), "revision": row["latestReadyRevision"]})
    return True


def resource_action(pin, controls, journal, phase, *, reconcile=False):
    marker = pin.kind + "." + pin.name + "." + phase
    target = "PAUSED" if phase == "pause" else pin.state
    row = controls.get(pin.kind, pin.name)
    need(configuration(row) == pin.configuration_sha256, "resource_configuration_changed")
    started = journal.read(marker + ".started")
    if started is None:
        need(not reconcile, "reconcile_never_starts_write")
        if phase == "restore":
            need(journal.read(pin.kind + "." + pin.name + ".pause.checked") is not None, "pause_not_observed")
        need(row.get("state") == (pin.state if phase == "pause" else "PAUSED"), "resource_state_changed")
        journal.record(marker + ".started", {"target": target, "replay_authorized": False})
        if row.get("state") != target:
            controls.set_state(pin.kind, pin.name, target)
    else:
        need(reconcile, "resource_started_reconcile_only")
    row = controls.get(pin.kind, pin.name)
    need(configuration(row) == pin.configuration_sha256 and row.get("state") == target, "resource_outcome_unverified")
    if journal.read(marker + ".checked") is None:
        journal.record(marker + ".checked", {"state": target})


def checked_resources(plan, controls, journal, kind):
    for pin in plan.resources:
        if pin.kind == kind:
            need(journal.read(kind + "." + pin.name + ".pause.checked") is not None, "resources_not_paused")
            row = controls.get(kind, pin.name)
            need(row.get("state") == "PAUSED" and configuration(row) == pin.configuration_sha256, "resource_state_changed")


def checked_closed(plan, run, journal, services):
    for pin in plan.services:
        if pin.name in services:
            started = journal.read("service." + pin.name + ".close.started")
            need(started is not None and journal.read("service." + pin.name + ".close.checked") is not None, "services_not_closed")
            service_readback(plan, pin, run, "close", started)


def execute(plan, run, controls, journal, phase, drain, *, reconcile=False, deployed=None):
    """One bounded phase; partial progress is retained and explicitly resumed."""
    plan.validate()
    need(type(journal) is rolling.Journal and journal.plan_hash == rolling.sha(asdict(plan)), "original_journal_required")
    need(phase in {"pause-schedules", "close-api", "close-workers", "pause-queues", "restore-services", "restore-queues", "restore-schedules"}, "phase_invalid")
    need(deployed is None or phase in {"restore-queues", "restore-schedules"}, "deployment_resume_scope_invalid")
    with journal.locked():
        if deployed is not None:
            deployed()
        elif phase.startswith("restore-"):
            # Safe abort only. Every closed service must still have our exact
            # same-image closed configuration; never overwrite promoted code.
            for pin in plan.services:
                started = journal.read("service." + pin.name + ".close.started")
                restoring = journal.read("service." + pin.name + ".restore.started")
                if started is not None and restoring is None:
                    need(journal.read("service." + pin.name + ".close.checked") is not None, "partial_close_reconcile_required")
                    service_readback(plan, pin, run, "close", started)
        elif any(journal.read("service." + pin.name + ".restore.started") for pin in plan.services):
            need(False, "restore_already_started")
        if phase in {"pause-schedules", "pause-queues", "restore-schedules", "restore-queues"}:
            kind = "schedule" if phase.endswith("schedules") else "queue"
            mode = "restore" if phase.startswith("restore") else "pause"
            if phase == "pause-queues":
                checked_closed(plan, run, journal, SERVICES)
                checked_resources(plan, controls, journal, "schedule")
                drain()
            if mode == "restore" and deployed is None:
                for pin in plan.services:
                    if journal.read("service." + pin.name + ".close.started") is not None:
                        restored = journal.read("service." + pin.name + ".restore.started")
                        need(restored is not None and journal.read("service." + pin.name + ".restore.checked") is not None, "restore_services_first")
                        service_readback(plan, pin, run, "restore", restored)
            for pin in plan.resources:
                if pin.kind != kind:
                    continue
                marker = kind + "." + pin.name + "." + mode
                if mode == "restore" and journal.read(kind + "." + pin.name + ".pause.started") is None:
                    continue
                if journal.read(marker + ".checked") is not None:
                    expected = "PAUSED" if mode == "pause" else pin.state
                    row = controls.get(kind, pin.name)
                    need(row.get("state") == expected and configuration(row) == pin.configuration_sha256, "resource_state_changed")
                    continue
                if reconcile and journal.read(marker + ".started") is None:
                    return {"status": "maintenance_phase_partial", "phase": phase, "resume_required": True, "replay_authorized": False}
                resource_action(pin, controls, journal, mode, reconcile=reconcile and journal.read(marker + ".started") is not None)
        else:
            selected: tuple[str, ...] = SERVICES[:1] if phase == "close-api" else SERVICES[1:]
            mode = "close"
            if phase == "restore-services":
                selected, mode = tuple(reversed(SERVICES)), "restore"
            else:
                checked_resources(plan, controls, journal, "schedule")
            if phase == "close-workers":
                checked_closed(plan, run, journal, SERVICES[:1])
                drain()
            for pin in plan.services if mode == "close" else reversed(plan.services):
                if pin.name not in selected:
                    continue
                if mode == "restore" and journal.read("service." + pin.name + ".close.started") is None:
                    continue
                marker = "service." + pin.name + "." + mode
                if reconcile and journal.read(marker + ".started") is None:
                    return {"status": "maintenance_phase_partial", "phase": phase, "resume_required": True, "replay_authorized": False}
                if not service_action(plan, pin, run, journal, mode, reconcile=reconcile and journal.read(marker + ".started") is not None):
                    return {"status": "maintenance_operation_pending", "phase": phase, "replay_authorized": False}
        return {"status": "maintenance_phase_observed", "phase": phase, "replay_authorized": False, "migration_performed": False}


class NativeControls:
    """Fixed scheduler/queue state controls; one physical POST per armed call."""
    def __init__(self):
        self.stock = native.native.SDKGoogleReads(native.host_auth.SDK, native.host_auth.ACCOUNT)._http()
        self.session = self.stock.session
        self.session.trust_env, self.session.max_redirects = False, 0
        for adapter in self.session.adapters.values():
            adapter.max_retries = adapter.max_retries.__class__(total=0, connect=0, read=0, redirect=0, status=0)
        self.armed, self.sent = None, 0
        original = self.session.send

        def send(request, **kwargs):
            url = urlsplit(request.url)
            need(url.scheme == "https" and not url.username and not url.password and url.port is None and not url.fragment, "control_origin_refused")
            if url.hostname == "oauth2.googleapis.com":
                need(request.method == "POST" and url.path == "/token" and not url.query, "control_origin_refused")
            elif request.method == "POST":
                need(self.armed == request.url and self.sent == 0 and request.body in (b"{}", "{}"), "control_write_not_armed")
                self.sent += 1
            else:
                allowed = {self.url(kind, name) for kind, names in RESOURCE_NAMES.items() for name in names}
                query = parse_qs(url.query, strict_parsing=True)
                need(request.method == "GET" and request.url in allowed and not query, "control_read_refused")
            kwargs["allow_redirects"] = False
            return original(request, **kwargs)
        self.session.send = send

    @staticmethod
    def url(kind, name):
        need(kind in RESOURCE_NAMES and name in RESOURCE_NAMES[kind], "control_scope_refused")
        origin, category = RESOURCE_PATHS[kind]
        return origin + rolling.PARENT + category + name

    def request(self, method, url):
        response = self.session.request(method, url, data="{}" if method == "POST" else None,
                                        headers={"Content-Type": "application/json"}, timeout=10, allow_redirects=False)
        need(response.status_code == 200 and len(response.content) <= 131072, "control_response_unverified")
        value = json.loads(response.content)
        need(type(value) is dict, "control_response_unverified")
        return value

    def get(self, kind, name):
        value = self.request("GET", self.url(kind, name))
        need(value.get("name") == rolling.PARENT + RESOURCE_PATHS[kind][1] + name, "control_identity_changed")
        return value

    def set_state(self, kind, name, state):
        need(state in ({"RUNNING", "PAUSED"} if kind == "queue" else {"ENABLED", "PAUSED"}), "control_target_refused")
        url = self.url(kind, name) + (":pause" if state == "PAUSED" else ":resume")
        need(self.armed is None, "control_already_armed")
        self.armed, self.sent = url, 0
        try:
            self.request("POST", url)
        finally:
            self.armed = None


class NativeDrain:
    def __init__(self, root):
        self.boundary = schema25.NativeSchema25Boundary(root, schema25.contract.BEFORE)

    def __call__(self):
        coordinator.observe_closed(coordinator.NativeCoordinatorRun())
        observer = self.boundary.observer
        for queue in QUEUES:
            need(observer._cloud("tasks", "list", "--queue", queue, "--location", rolling.release.REGION, "--limit", "257") == [], "queue_not_empty")
        executions = observer._cloud("run", "jobs", "executions", "list", "--region", rolling.release.REGION, "--limit", "257")
        need(type(executions) is list and len(executions) <= 256
             and all(row.get("status", {}).get("completionTime") for row in executions), "job_execution_not_drained")
        self.boundary._target("hirewiz-fresh-runtime-db-20261013:2", "hirewiz-fresh-observer-db-20261013:2", "hirewiz-fresh-migrator-db-20261013:2")


class NativeDeployed:
    """Only an exact completed schema25 release can resume retained resources.

    No flag/image/traffic update. Reuse real rolling health/readback and the
    native schema25 collector; a saved success marker alone is insufficient.
    """
    def __init__(self, root, path, digest, directory):
        self.plan = schema25.load_plan(schema25._read_pinned(path, digest))
        self.journal = rolling.Journal(directory, self.plan)
        self.boundary = schema25.NativeSchema25Boundary(root, self.plan.database_schema, self.plan.prior_sources)

    def __call__(self):
        plan, boundary, journal = self.plan, self.boundary, self.journal
        schema25._release_phase(plan, boundary, journal)
        with journal.locked():
            need(not any(journal.read("rollback." + service + ".started") for service in SERVICES), "deployment_rollback_started")
            boundary.observer.verify_source_image(release=plan.release, image_digest=plan.image_digest,
                                                  build_id=plan.build_id, ci_run_id=plan.ci_run_id)
            boundary.observer.verify_policy_configuration(plan.target_policies())
            boundary.observed_disabled_flags(plan)
            for pin in plan.services:
                started = journal.read("promote." + pin.service + ".started")
                need(started is not None and journal.read("promote." + pin.service + ".checked") is not None,
                     "deployment_not_completed")
                assert started is not None
                rolling._readback(plan, boundary, pin, started)
            need(boundary._target(plan.runtime_secret, plan.observer_secret, plan.retired_migration_secret)
                 == plan.financial_sha256, "deployment_financial_state_changed")


def load(raw):
    value = rolling.release._json(raw)
    services = []
    for row in value["services"]:
        row["template_keys"], row["traffic"] = tuple(row["template_keys"]), tuple(row["traffic"])
        row["revisions"] = tuple(tuple(item) for item in row["revisions"])
        services.append(ServicePin(**row))
    plan = MaintenancePlan(value["nonce"], tuple(services), tuple(ResourcePin(**row) for row in value["resources"]))
    plan.validate()
    return plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "pause-schedules", "close-api", "close-workers", "pause-queues", "restore-services", "restore-queues", "restore-schedules"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--reconcile", action="store_true")
    parser.add_argument("--deployment-plan", type=Path)
    parser.add_argument("--deployment-plan-sha256")
    parser.add_argument("--deployment-journal", type=Path)
    args = parser.parse_args()
    run, controls = native.NativeRun(), NativeControls()
    if args.phase == "capture":
        need(not args.reconcile and args.plan.is_absolute() and args.plan.parent.resolve() == args.plan.parent
             and args.plan.parent.stat().st_uid == os.getuid() and args.plan.parent.stat().st_mode & 0o077 == 0, "private_directory_required")
        plan = capture(run, controls, args.nonce)
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as target:
            target.write(raw)
            target.flush()
            os.fsync(target.fileno())
        print(json.dumps({"status": "maintenance_plan_captured", "plan_sha256": hashlib.sha256(raw).hexdigest(), "provider_mutations": 0}))
        return
    plan = load(schema25._read_pinned(args.plan, args.plan_sha256))
    need(args.journal is not None, "journal_required")
    journal = rolling.Journal(args.journal, plan)
    deployment_args = (args.deployment_plan, args.deployment_plan_sha256, args.deployment_journal)
    need(all(value is None for value in deployment_args) or all(value is not None for value in deployment_args), "deployment_pins_incomplete")
    deployed = None if args.deployment_plan is None else NativeDeployed(args.root, *deployment_args)
    print(json.dumps(execute(plan, run, controls, journal, args.phase, NativeDrain(args.root), reconcile=args.reconcile, deployed=deployed)))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        code = "unclassified_failure"
        if type(error) is rolling.RollingDenied and len(error.args) == 1 and re.fullmatch(r"schema25_maintenance_[a-z_]{1,90}", str(error.args[0])):
            code = error.args[0]
        print(json.dumps({"status": "maintenance_stopped_reconcile_only", "code": code, "replay_authorized": False}))
        raise SystemExit(2) from None
