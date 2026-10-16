"""Schema18-only immutable rolling release; never a fresh cutover or migration.

The production CLI uses only the native adapter. Protocol fixtures prove ordering
and failure handling, never actual GCP, SQL, token, health or customer readiness.
Raw service templates may contain retained literals: keep them in RAM only.
"""
from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Protocol

import monetary_release as release

SCHEMA = "20261015_0018"
PARENT = f"projects/{release.PROJECT}/locations/{release.REGION}"
RUNTIME = f"hirewiz-runtime-20261014@{release.PROJECT}.iam.gserviceaccount.com"
OPERATION = re.compile(re.escape(PARENT) + r"/operations/[A-Za-z0-9._-]{1,200}\Z")
HASH = re.compile(r"[a-f0-9]{64}\Z")
REF = re.compile(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}\Z")
OFF = ("OPTIONAL_AI_GENERATION_ENABLED", "EMPLOYER_AUTO_SUBMIT_ENABLED",
       "RAZORPAY_CHECKOUT_ENABLED", "CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED",
       "BROWSER_ASSISTANCE_ENABLED", "RESUME_DIRECT_UPLOAD_ENABLED", "EMPLOYER_DISCOVERY_ENABLED")
OUTPUTS = frozenset({"name", "uid", "generation", "observedGeneration", "etag",
    "createTime", "updateTime", "creator", "lastModifier", "reconciling",
    "terminalCondition", "conditions", "latestReadyRevision", "latestCreatedRevision",
    "trafficStatuses", "template", "traffic"})


class RollingDenied(release.ReleaseDenied):
    """Fixed, nonsecret reasons only."""


def need(value: Any, reason: str = "rolling_native_state_unverified") -> None:
    if not value:
        raise RollingDenied(reason)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def short_revision(value: str, service: str) -> str:
    need(type(value) is str)
    prefix = PARENT + "/services/" + service + "/revisions/"
    result = value[len(prefix):] if value.startswith(prefix) else value
    need(re.fullmatch(re.escape(service) + r"-[a-z0-9-]{1,40}", result))
    return result


def environment(template: dict[str, Any]) -> dict[str, dict[str, Any]]:
    containers = template.get("containers")
    need(type(containers) is list and len(containers) == 1)
    rows = containers[0].get("env", [])
    need(type(rows) is list and len(rows) <= 256)
    result: dict[str, dict[str, Any]] = {}
    for row in rows:
        need(type(row) is dict and type(row.get("name")) is str
             and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", row["name"])
             and row["name"] not in result)
        result[row["name"]] = row
    return result


def pinned_traffic(service: str, observed: dict[str, Any], *,
                   prior_revision: str | None = None) -> list[dict[str, Any]]:
    """Pin actual allocations; LATEST must not move traffic during staging."""
    rows = observed.get("trafficStatuses")
    need(type(rows) is list and 1 <= len(rows) <= 20)
    result, tags = [], set()
    for row in rows:
        need(type(row) is dict)
        percent, tag = row.get("percent", 0), row.get("tag")
        need(type(percent) is int and 0 <= percent <= 100)
        item = {"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                "revision": short_revision(row.get("revision", ""), service)}
        if percent:
            item["percent"] = percent
        if tag is not None:
            need(type(tag) is str and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", tag)
                 and tag not in tags)
            tags.add(tag)
            item["tag"] = tag
        need(percent or tag is not None)
        result.append(item)
    active = [row for row in result if row.get("percent", 0)]
    need(len(active) == 1 and active[0]["percent"] == 100)
    if prior_revision is not None:
        need(active[0]["revision"] == short_revision(prior_revision, service),
             "rolling_active_traffic_not_prior_revision")
    return sorted(result, key=canonical)


def static_sha(service: dict[str, Any]) -> str:
    return sha({key: value for key, value in service.items() if key not in OUTPUTS})


def template_sha(template: dict[str, Any], service: str) -> str:
    normalized = copy.deepcopy(template)
    if "revision" in normalized:
        normalized["revision"] = short_revision(normalized["revision"], service)
    return sha(normalized)


def worker_iam(service: str, policy: dict[str, Any]) -> None:
    """Retain the exact existing Tasks/Scheduler callers for the two workers."""
    need(service in release.SERVICES and type(policy) is dict)
    if service == release.SERVICES[0]:
        return
    expected = {"serviceAccount:hirewiz-tasks@" + release.PROJECT + ".iam.gserviceaccount.com"}
    if service == release.SERVICES[1]:
        expected.add("serviceAccount:hirewiz-scheduler@" + release.PROJECT + ".iam.gserviceaccount.com")
    bindings = policy.get("bindings", [])
    need(type(bindings) is list and len(bindings) == 1
         and set(bindings[0]) == {"role", "members"}
         and bindings[0]["role"] == "roles/run.invoker"
         and type(bindings[0]["members"]) is list
         and len(bindings[0]["members"]) == len(expected)
         and set(bindings[0]["members"]) == expected, "rolling_worker_invokers_unverified")


@dataclass(frozen=True)
class SecretPin:
    environment_name: str
    secret: str
    version: str


@dataclass(frozen=True)
class ServicePin:
    service: str
    uid: str
    generation: int
    prior_revision: str
    prior_release: str
    template_sha256: str
    static_sha256: str
    iam_sha256: str
    revision_sha256: str
    revision_keys: tuple[str, ...]
    traffic: tuple[dict[str, Any], ...]
    secrets: tuple[SecretPin, ...]


@dataclass(frozen=True)
class PolicyPins:
    expense_secret: str
    expense_sha256: str
    model_policy_secret: str
    model_policy_sha256: str
    model_key_secret: str
    model_primary: str
    model_api_base: str


@dataclass(frozen=True)
class RollingPlan:
    release: str
    image_digest: str
    build_id: str
    ci_run_id: int
    nonce: str
    runtime_uid: str
    runtime_secret: str
    observer_secret: str
    retired_migration_secret: str
    workers_release: str
    financial_sha256: str
    policies: PolicyPins
    services: tuple[ServicePin, ...]

    def disabled_flags(self) -> tuple[str, ...]:
        return OFF

    @property
    def image(self) -> str:
        return f"{release.REGION}-docker.pkg.dev/{release.PROJECT}/cloud-run-source-deploy/hirewiz@{self.image_digest}"

    @property
    def tag(self) -> str:
        return "rolling-" + self.nonce[:12]

    @property
    def rollback_tag(self) -> str:
        return "rolling-back-" + self.nonce[:12]

    def revision(self, service: str) -> str:
        return service + "-r-" + self.release[:12] + "-" + self.nonce[:8]

    def pin(self, service: str) -> ServicePin:
        need(service in release.SERVICES)
        return next(pin for pin in self.services if pin.service == service)

    def validate(self) -> None:
        need(release.CHAIN[-1] == SCHEMA, "rolling_schema18_only")
        need(all(type(value) is str for value in (self.release, self.image_digest, self.build_id,
            self.nonce, self.runtime_uid, self.workers_release, self.financial_sha256)))
        need(re.fullmatch(r"[a-f0-9]{40}", self.release)
             and re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest)
             and re.fullmatch(r"[a-f0-9-]{36}", self.build_id)
             and type(self.ci_run_id) is int and self.ci_run_id > 0
             and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
             and re.fullmatch(r"[1-9][0-9]{20}", self.runtime_uid)
             and re.fullmatch(r"[a-f0-9]{40}", self.workers_release)
             and HASH.fullmatch(self.financial_sha256), "rolling_plan_identity_invalid")
        for reference in (self.runtime_secret, self.observer_secret,
                          self.retired_migration_secret, self.policies.expense_secret,
                          self.policies.model_policy_secret, self.policies.model_key_secret):
            need(type(reference) is str and REF.fullmatch(reference), "rolling_numeric_secret_required")
        need(len({self.runtime_secret.split(":")[0], self.observer_secret.split(":")[0],
                  self.retired_migration_secret.split(":")[0]}) == 3)
        need(HASH.fullmatch(self.policies.expense_sha256)
             and HASH.fullmatch(self.policies.model_policy_sha256)
             and self.policies.model_api_base == release.MODEL_API_BASE
             and re.fullmatch(r"gemini-[A-Za-z0-9_.-]{1,113}", self.policies.model_primary))
        need(tuple(pin.service for pin in self.services) == release.SERVICES)
        for pin in self.services:
            need(re.fullmatch(r"[a-f0-9-]{36}", pin.uid)
                 and type(pin.generation) is int and pin.generation > 0
                 and short_revision(pin.prior_revision, pin.service) == pin.prior_revision
                 and re.fullmatch(r"[a-f0-9]{40}", pin.prior_release)
                 and pin.prior_revision != self.revision(pin.service))
            need(all(HASH.fullmatch(value) for value in (pin.template_sha256,
                pin.static_sha256, pin.iam_sha256, pin.revision_sha256)))
            need(1 <= len(pin.revision_keys) <= 40 and len(set(pin.revision_keys)) == len(pin.revision_keys))
            need(pinned_traffic(pin.service, {"trafficStatuses": list(pin.traffic)},
                                prior_revision=pin.prior_revision) == list(pin.traffic),
                 "rolling_pinned_traffic_invalid")
            names = set()
            for secret in pin.secrets:
                need(secret.environment_name not in names
                     and re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", secret.environment_name)
                     and re.fullmatch(r"[a-zA-Z0-9_-]{1,255}", secret.secret)
                     and re.fullmatch(r"[1-9][0-9]{0,18}", secret.version))
                names.add(secret.environment_name)
            database = [s for s in pin.secrets if s.environment_name == "DATABASE_URL"]
            need(len(database) == 1 and database[0].secret + ":" + database[0].version == self.runtime_secret)
            verify_policy_bindings(self.policies, pin)


def verify_policy_bindings(policies: PolicyPins, pin: ServicePin,
                           template: dict[str, Any] | None = None) -> None:
    """Bind each service to reviewed policy/model settings, using its source contract."""
    database = [row for row in pin.secrets if row.environment_name == "DATABASE_URL"]
    need(len(database) == 1, "rolling_service_database_binding_invalid")
    contract = SimpleNamespace(release=pin.prior_release,
        runtime_secret=database[0].secret + ":" + database[0].version, **asdict(policies))
    expected_env, expected_secrets, _ = release.runtime_environment(contract, pin.service)
    policy_names = {"HIREWIZ_EXPENSE_POLICY_JSON", *release.MODEL_ENV, *release.MODEL_ALIASES}
    literals = {name: value for name, value in expected_env.items() if name in policy_names}
    references = {name: value for name, value in expected_secrets.items() if name in policy_names}
    absent = policy_names - literals.keys() - references.keys()
    pinned = {row.environment_name: row.secret + ":" + row.version for row in pin.secrets}
    need(all(pinned.get(name) == reference for name, reference in references.items())
         and not (set(pinned) & (absent | literals.keys())),
         "rolling_service_policy_binding_invalid")
    if template is None:
        return
    env = environment(template)
    need(not (set(env) & absent), "rolling_service_model_fields_forbidden")
    for name, value in literals.items():
        need(env.get(name) == {"name": name, "value": value},
             "rolling_service_model_binding_invalid")
    for name, reference in references.items():
        secret, version = reference.split(":")
        need(env.get(name) == {"name": name, "valueSource": {
            "secretKeyRef": {"secret": secret, "version": version}}},
            "rolling_service_policy_binding_invalid")


class Boundary(Protocol):
    def verify_global(self, plan: RollingPlan) -> None: ...
    def service(self, service: str) -> dict[str, Any]: ...
    def iam(self, service: str) -> dict[str, Any]: ...
    def revision(self, service: str, name: str) -> dict[str, Any] | None: ...
    def patch(self, service: str, body: dict[str, Any], mask: str) -> dict[str, Any]: ...
    def operation(self, name: str) -> dict[str, Any]: ...
    def health(self, plan: RollingPlan, service: str, revision: str, release_id: str, tag: str) -> None: ...


class Journal:
    """Owned durable metadata only. A lost write acknowledgement never permits replay."""
    def __init__(self, directory: Path, plan: RollingPlan):
        need(directory.is_absolute() and directory.resolve() == directory
             and not directory.is_symlink() and directory.is_dir())
        status = directory.stat()
        need(status.st_uid == os.getuid() and stat.S_IMODE(status.st_mode) == 0o700)
        self.directory, self.plan_hash = directory, sha(asdict(plan))

    @contextmanager
    def locked(self) -> Iterator[None]:
        fd = os.open(self.directory / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            status = os.fstat(fd)
            need(stat.S_ISREG(status.st_mode) and status.st_uid == os.getuid()
                 and stat.S_IMODE(status.st_mode) == 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            yield
        finally:
            os.close(fd)

    def read(self, name: str) -> dict[str, Any] | None:
        path = self.directory / (name + ".json")
        if not path.exists():
            return None
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            status = os.fstat(stream.fileno())
            need(stat.S_ISREG(status.st_mode) and status.st_uid == os.getuid()
                 and stat.S_IMODE(status.st_mode) == 0o600 and status.st_size <= 131072)
            value = release._json(stream.read(131073))
        need(type(value) is dict and value.get("plan_sha256") == self.plan_hash)
        return value

    def record(self, name: str, value: dict[str, Any]) -> None:
        need(re.fullmatch(r"[a-z0-9.-]{1,100}", name))
        raw = canonical({**value, "plan_sha256": self.plan_hash}) + b"\n"
        need(len(raw) <= 131072)
        fd = os.open(self.directory / (name + ".json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _ready(value: dict[str, Any], pin: ServicePin) -> None:
    need(value.get("name") == PARENT + "/services/" + pin.service
         and value.get("uid") == pin.uid and type(value.get("etag")) is str
         and value.get("etag") and value.get("reconciling", False) is False
         and str(value.get("generation")) == str(value.get("observedGeneration"))
         and value.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED")


def _old_revision(boundary: Boundary, pin: ServicePin) -> None:
    observed = boundary.revision(pin.service, pin.prior_revision)
    need(observed is not None, "rolling_rollback_revision_missing")
    assert observed is not None
    need(sha({key: observed.get(key) for key in pin.revision_keys}) == pin.revision_sha256,
         "rolling_rollback_configuration_changed")
    need(any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
             for row in observed.get("conditions", [])))


def _traffic_for(plan: RollingPlan, pin: ServicePin, promote: bool, *, rollback: bool = False) -> list[dict[str, Any]]:
    rows = copy.deepcopy(list(pin.traffic))
    need(pinned_traffic(pin.service, {"trafficStatuses": rows},
                        prior_revision=pin.prior_revision) == rows,
         "rolling_pinned_traffic_invalid")
    need(not any(row.get("tag") in {plan.tag, plan.rollback_tag} for row in rows), "rolling_tag_collision")
    if promote:
        for row in rows:
            row.pop("percent", None)
        rows = [row for row in rows if row.get("tag")]
    rows.append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                 "revision": plan.revision(pin.service), "tag": plan.tag,
                 **({"percent": 100} if promote else {})})
    if rollback:
        rows.append({"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
                     "revision": pin.prior_revision, "tag": plan.rollback_tag})
    return sorted(rows, key=canonical)


def stage_body(plan: RollingPlan, pin: ServicePin, current: dict[str, Any]) -> dict[str, Any]:
    _ready(current, pin)
    need(int(current["generation"]) == pin.generation
         and template_sha(current["template"], pin.service) == pin.template_sha256
         and static_sha(current) == pin.static_sha256
         and pinned_traffic(pin.service, current, prior_revision=pin.prior_revision) == list(pin.traffic))
    template = copy.deepcopy(current["template"])
    verify_policy_bindings(plan.policies, pin, template)
    need(template.get("serviceAccount") == RUNTIME)
    env = environment(template)
    need(all(env.get(name, {"value": "false"}).get("value") == "false" for name in plan.disabled_flags()),
         "rolling_product_admission_not_closed")
    for name in set(plan.disabled_flags()) - set(OFF):
        env[name] = {"name": name, "value": "false"}
    need(env.get("SERVICE_ROLE", {}).get("value") == ("api" if pin.service == release.SERVICES[0] else "worker"))
    if pin.service != release.SERVICES[0]:
        expected_label = "analysis-worker" if pin.service == release.SERVICES[1] else "employer-worker"
        need(env.get("WORKER_LABEL", {"value": "analysis-worker"}).get("value") == expected_label)
    for secret in pin.secrets:
        row = env.get(secret.environment_name, {})
        ref = row.get("valueSource", {}).get("secretKeyRef", {})
        need(ref.get("secret") == secret.secret and ref.get("version") in {secret.version, "latest"})
        ref["version"] = secret.version
    expected_names = {s.environment_name for s in pin.secrets}
    need({name for name, row in env.items() if "valueSource" in row} == expected_names)
    env["APP_RELEASE"] = {"name": "APP_RELEASE", "value": plan.release}
    env["AUTO_DB_MIGRATE"] = {"name": "AUTO_DB_MIGRATE", "value": "false"}
    env["PGSSLROOTCERT"] = {"name": "PGSSLROOTCERT", "value": release.SAFE_ENV["PGSSLROOTCERT"]}
    template["revision"] = plan.revision(pin.service)
    template.setdefault("labels", {})["hirewiz-source"] = plan.release
    container = template["containers"][0]
    ports = container.get("ports", [])
    need(len(ports) == 1 and type(ports[0].get("containerPort")) is int
         and ports[0]["containerPort"] in {8080, 10000})
    container["image"], container["env"] = plan.image, list(env.values())
    module = "app.main:app" if pin.service == release.SERVICES[0] else "app.worker_main:app"
    container["command"] = ["python"]
    container["args"] = ["-m", "uvicorn", module, "--host", "0.0.0.0", "--port", str(ports[0]["containerPort"])]
    return {"name": current["name"], "etag": current["etag"], "template": template,
            "traffic": _traffic_for(plan, pin, False)}


def _readback(plan: RollingPlan, boundary: Boundary, pin: ServicePin, started: dict[str, Any]) -> dict[str, Any]:
    value = boundary.service(pin.service)
    _ready(value, pin)
    need(int(value["generation"]) == started["expected_generation"]
         and template_sha(value["template"], pin.service) == started["template_sha256"]
         and static_sha(value) == pin.static_sha256
         and sha(boundary.iam(pin.service)) == pin.iam_sha256
         and pinned_traffic(pin.service, value) == started["traffic"])
    need(short_revision(value["latestReadyRevision"], pin.service) == plan.revision(pin.service))
    _old_revision(boundary, pin)
    revision = boundary.revision(pin.service, plan.revision(pin.service))
    need(revision is not None)
    assert revision is not None
    keys = set(value["template"]) - {"revision"}
    need({key: revision.get(key) for key in keys} == {key: value["template"][key] for key in keys})
    need(any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
             for row in revision.get("conditions", [])))
    boundary.health(plan, pin.service, plan.revision(pin.service), plan.release, plan.tag)
    if started["phase"] == "rollback":
        boundary.health(plan, pin.service, pin.prior_revision, pin.prior_release, plan.rollback_tag)
    return {"service_uid": pin.uid, "revision": plan.revision(pin.service),
            "generation": int(value["generation"]), "native_metadata_reobserved": True}


def action(plan: RollingPlan, boundary: Boundary, journal: Journal, pin: ServicePin,
           phase: str, *, reconcile: bool = False) -> bool:
    """At most one PATCH. False means its same native LRO is still pending."""
    need(phase in {"stage", "promote", "rollback"})
    name = phase + "." + pin.service
    started, ack, checked = (journal.read(name + suffix) for suffix in (".started", ".ack", ".checked"))
    boundary.verify_global(plan)
    if checked is not None:
        need(started is not None)
        _readback(plan, boundary, pin, started)
        return True
    if started is None:
        need(not reconcile, "rolling_reconciliation_never_starts_a_mutation")
        current = boundary.service(pin.service)
        _ready(current, pin)
        need(sha(boundary.iam(pin.service)) == pin.iam_sha256)
        _old_revision(boundary, pin)
        if phase == "stage":
            need(boundary.revision(pin.service, plan.revision(pin.service)) is None,
                 "rolling_candidate_revision_already_exists")
            body, mask = stage_body(plan, pin, current), "template,traffic"
        else:
            previous = "promote" if phase == "rollback" and journal.read("promote." + pin.service + ".checked") else "stage"
            stage = journal.read(previous + "." + pin.service + ".started")
            need(stage is not None and journal.read(previous + "." + pin.service + ".checked") is not None)
            _readback(plan, boundary, pin, stage)
            body, mask = {"name": current["name"], "etag": current["etag"],
                          "traffic": _traffic_for(plan, pin, phase == "promote",
                                                  rollback=phase == "rollback")}, "traffic"
        template = body.get("template", current["template"])
        started = {"phase": phase, "request_sha256": sha(body), "mask": mask,
                   "template_sha256": template_sha(template, pin.service),
                   "traffic": body["traffic"], "expected_generation": int(current["generation"]) + 1,
                   "service_uid": pin.uid, "previous_etag": current["etag"],
                   "replay_authorized": False}
        # Compare a fresh native resource to the exact RAM request preimage before
        # persisting the once marker. Provider etag also fences the only PATCH.
        need(boundary.service(pin.service) == current, "rolling_native_preimage_changed")
        journal.record(name + ".started", started)
        operation = boundary.patch(pin.service, body, mask)
        op_name = operation.get("name")
        need(type(op_name) is str and OPERATION.fullmatch(op_name), "rolling_operation_ack_unmapped")
        ack = {"operation": op_name, "request_sha256": started["request_sha256"]}
        journal.record(name + ".ack", ack)
    if ack is None:
        need(reconcile, "rolling_unknown_mutation_reconcile_only")
        observation = _readback(plan, boundary, pin, started)
    else:
        need(ack.get("request_sha256") == started["request_sha256"]
             and OPERATION.fullmatch(ack.get("operation", "")))
        observed = boundary.operation(ack["operation"])
        need(observed.get("name") == ack["operation"] and "error" not in observed,
             "rolling_operation_failed_reconcile_only")
        if observed.get("done", False) is not True:
            return False
        observation = _readback(plan, boundary, pin, started)
    journal.record(name + ".checked", {**observation, "replay_authorized": False,
                                      "operation_ack_retained": ack is not None})
    return True


def advance(plan: RollingPlan, boundary: Boundary, journal: Journal) -> dict[str, Any]:
    plan.validate()
    with journal.locked():
        need(not any(journal.read("rollback." + service + ".started") for service in release.SERVICES),
             "rolling_explicit_rollback_in_progress")
        for phase, services in (("stage", release.SERVICES),
                                ("promote", (*release.SERVICES[1:], release.SERVICES[0]))):
            for service in services:
                pin = plan.pin(service)
                # A later invocation may arrive after an acknowledged promotion.
                # Reobserve that action instead of expecting the earlier staged
                # traffic configuration or issuing another PATCH.
                if phase == "stage" and journal.read("promote." + service + ".started"):
                    if not action(plan, boundary, journal, pin, "promote"):
                        return {"status": "same_native_operation_pending", "phase": "promote",
                                "service": service, "rolling_release_complete": False,
                                "replay_authorized": False}
                    continue
                # Before any promotion, all three staged revisions must pass a
                # fresh joined read. Already promoted participants use their
                # current expected traffic, never a persisted success flag alone.
                if phase == "promote":
                    for joined in plan.services:
                        kind = "promote" if journal.read("promote." + joined.service + ".checked") else "stage"
                        previous = journal.read(kind + "." + joined.service + ".started")
                        need(previous is not None)
                        _readback(plan, boundary, joined, previous)
                if not action(plan, boundary, journal, pin, phase):
                    return {"status": "same_native_operation_pending", "phase": phase,
                            "service": service, "rolling_release_complete": False, "replay_authorized": False}
        boundary.verify_global(plan)
        return {"status": "joined_schema18_release_observed", "schema": SCHEMA,
                "rolling_release_complete": True, "goal_complete": False,
                "queue_admission": "closed", "migrations": 0, "data_transfers": 0,
                "credential_retirements": 0, "source_owner_creations": 0}


def reconcile(plan: RollingPlan, boundary: Boundary, journal: Journal) -> dict[str, Any]:
    """GET/health/SQL-read only: observe existing actions, never begin the next one."""
    plan.validate()
    with journal.locked():
        for service in release.SERVICES:
            for phase in ("rollback", "promote", "stage"):
                name = phase + "." + service
                if journal.read(name + ".started") is not None:
                    if not action(plan, boundary, journal, plan.pin(service), phase, reconcile=True):
                        return {"status": "same_native_operation_pending", "phase": phase,
                                "service": service, "replay_authorized": False}
                    break
        return {"status": "existing_actions_reconciled_only", "native_mutations": 0,
                "rolling_release_complete": False, "replay_authorized": False}


def rollback(plan: RollingPlan, boundary: Boundary, journal: Journal) -> dict[str, Any]:
    """Explicit traffic-only rollback; preserve the new and every old revision."""
    plan.validate()
    with journal.locked():
        need(all(journal.read("stage." + service + ".checked") for service in release.SERVICES),
             "rolling_joined_stage_must_be_reconciled_first")
        for service in (release.SERVICES[0], *reversed(release.SERVICES[1:])):
            if not action(plan, boundary, journal, plan.pin(service), "rollback"):
                return {"status": "same_native_operation_pending", "phase": "rollback",
                        "service": service, "replay_authorized": False}
        boundary.verify_global(plan)
        return {"status": "prior_traffic_restored_schema18", "rolling_release_complete": False,
                "queue_admission": "closed", "replay_authorized": False, "goal_complete": False}
