"""Owned API-only zero-traffic identity rehearsal and functional rollback.

No promotion, IAM writer, payment binding, probe, SQL client or credential export.
All templates stay in RAM. A started PATCH is never sent again, including when
the native acknowledgement is lost. Reconciliation only reads that same action.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from pathlib import Path

from infra.gcp import schema27_api_identity_readback as readback

rolling = readback.rolling
API = readback.API
ROOT = Path(__file__).resolve().parents[2]
APPLICATION_RELEASE = "70a1ef77c19c7f00570bc159b3db514b38d112d0"
NEW_API_EMAIL = "hirewiz-api-20261117@ai-resume-parser-482412.iam.gserviceaccount.com"
KIND = "hirewiz_schema27_api_identity_operator_v1"
ROLLBACK_KIND = "hirewiz_schema27_api_identity_rollback_v1"
PHASES = ("stage", "rollback-template", "rollback-traffic")
MAX_BYTES = 524288
SOURCE_FILES = (
    "infra/gcp/schema27_api_identity_operator.py", "infra/gcp/schema27_api_identity_readback.py",
    "infra/gcp/rolling_native.py", "infra/gcp/rolling_release.py",
    "infra/gcp/document_application_context.py", "infra/gcp/schema27_continuity_scope.py",
)


def need(value, reason):
    rolling.need(value, "api_identity_operator_" + reason)


def source_state():
    """Bind the fixed executing checkout, never a caller-selected source root."""
    def git(*args):
        result = subprocess.run(["/usr/bin/git", "-C", str(ROOT), *args], capture_output=True,
                                check=True, timeout=10, env={"PATH": "/usr/bin:/bin"},
                                stdin=subprocess.DEVNULL)
        return result.stdout.decode().strip()
    need(not git("status", "--porcelain", "--untracked-files=all"), "source_not_clean")
    release, tree = git("rev-parse", "HEAD"), git("rev-parse", "HEAD^{tree}")
    need(re.fullmatch(r"[a-f0-9]{40}", release) and re.fullmatch(r"[a-f0-9]{40}", tree)
         and git("merge-base", APPLICATION_RELEASE, "HEAD") == APPLICATION_RELEASE, "source_ancestry")
    return {"release": release, "tree": tree, "files": {
        name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCE_FILES}}


def _epoch(now):
    if isinstance(now, datetime):
        need(now.tzinfo is not None and now.utcoffset() is not None, "capture_time")
        now = now.timestamp()
    value = time.time() if now is None else now
    need(type(value) in {int, float} and 0 < value < 100000000000, "capture_time")
    return int(value)


@dataclass(frozen=True)
class Plan:
    transition: readback.Transition
    rollback_revision: str
    before: dict
    api_etag: str
    stage_request_sha256: str
    source: dict
    captured_at: int
    journal_location: dict
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @classmethod
    def from_dict(cls, value):
        need(type(value) is dict and set(value) == {item.name for item in fields(cls)}, "plan_shape")
        plan = cls(**(value | {"transition": readback._transition(value["transition"])}))
        plan.validate()
        return plan

    def validate(self):
        self.transition.validate()
        need(self.kind == KIND and self.transition.new_api.email == NEW_API_EMAIL
             and rolling.short_revision(self.rollback_revision, API) == self.rollback_revision
             and self.rollback_revision not in {self.transition.old_revision, self.transition.candidate_revision}
             and type(self.api_etag) is str and re.fullmatch(r"[!-~]{1,256}", self.api_etag)
             and readback._hash(self.stage_request_sha256) and type(self.captured_at) is int
             and 0 < self.captured_at < 100000000000, "plan_pin")
        need(type(self.source) is dict and set(self.source) == {"release", "tree", "files"}
             and all(type(self.source[key]) is str and re.fullmatch(r"[a-f0-9]{40}", self.source[key])
                     for key in ("release", "tree"))
             and type(self.source["files"]) is dict and bool(self.source["files"])
             and all(type(key) is str and readback._hash(value)
                     for key, value in self.source["files"].items()), "source_pin")
        location = self.journal_location
        need(type(location) is dict and set(location) == {"path", "device", "inode"}
             and type(location["path"]) is str and Path(location["path"]).is_absolute()
             and all(type(location[key]) is int and location[key] >= 0 for key in ("device", "inode")),
             "journal_location_pin")
        need(set(self.before) == {"services", "identities", "callers"}
             and set(self.before["services"]) == set(readback.SERVICES)
             and set(self.before["identities"]) == {"old_api", "new_api"}
             and self.transition.old_api.matches(self.before["identities"]["old_api"])
             and self.transition.new_api.matches(self.before["identities"]["new_api"]), "identity_pin")
        readback._prior_api(self.transition, self.before["services"][API])
        for name, account in {**dict.fromkeys(readback.WORKERS, rolling.RUNTIME),
                              readback.COORDINATOR: readback.COORDINATOR_ACCOUNT}.items():
            service = self.before["services"][name]
            need(service["template"]["account"] == account and all(
                row["account"] == account for row in service["revisions"].values()), "other_identity_pin")
        # Validate the captured IAM shape without issuing or authorizing a grant.
        callers = json.loads(rolling.canonical(self.before["callers"]))
        for name in readback.DOCUMENTS:
            for row in callers[name]["bindings"]:
                if row["role"] == "roles/run.invoker" and "condition" not in row:
                    row["members"].append("serviceAccount:" + self.transition.new_api.email)
        readback._validate_callers(self.transition, self.before["callers"], callers)

    def verify_source(self):
        self.validate()
        need(self.source == source_state(), "source_changed")


def _etag(service):
    value = service.get("etag")
    need(type(value) is str and re.fullmatch(r"[!-~]{1,256}", value), "etag_unverified")
    return value


def _observe(boundary):
    services, native = {}, {}
    for name in readback.SERVICES:
        service = boundary.service(name)
        targets = {rolling.short_revision(service[key], name)
                   for key in ("latestReadyRevision", "latestCreatedRevision")}
        targets.update(row["revision"] for row in readback.application.allocations(name, service))
        revisions = {revision: boundary.revision(name, revision) for revision in sorted(targets)}
        services[name] = readback.project_service(name, service, revisions)
        native[name] = {"service": service, "revisions": revisions}
    identities = {key: boundary.identity(email) for key, email in {
        "old_api": rolling.RUNTIME, "new_api": NEW_API_EMAIL,
    }.items()}
    callers = {name: boundary.iam(name) for name in (*readback.SERVICES, *readback.DOCUMENTS)}
    return {"services": services, "identities": identities, "callers": callers}, native


def _prepare(plan, native):
    return readback.prepare_request({"kind": readback.PREPARE_KIND, "plan": asdict(plan.transition),
        "before": plan.before["services"][API], "etag": plan.api_etag}, native | {"candidate_lookup": {
            "name": native["service"]["name"] + "/revisions/" + plan.transition.candidate_revision,
            "status": 404}})


def capture(boundary, candidate_revision, rollback_revision, *, journal_directory, candidate_tag=None, now=None):
    """Read a pre-grant baseline. No account creation, IAM grant or PATCH occurs."""
    source = source_state()
    directory = Path(journal_directory)
    parent = directory.parent.stat(follow_symlinks=False)
    need(directory.is_absolute() and directory.resolve() == directory and not directory.exists()
         and stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
         and stat.S_IMODE(parent.st_mode) == 0o700, "new_original_journal")
    directory.mkdir(mode=0o700)
    status = directory.stat(follow_symlinks=False)
    location = {"path": str(directory), "device": status.st_dev, "inode": status.st_ino}
    before, native = _observe(boundary)
    api = native[API]["service"]
    need(rolling.environment(api["template"]).get("APP_RELEASE") == {
        "name": "APP_RELEASE", "value": APPLICATION_RELEASE}, "application_release")
    identities = before["identities"]
    transition = readback.Transition(
        readback.Identity(rolling.RUNTIME, identities["old_api"]["uid"]),
        readback.Identity(NEW_API_EMAIL, identities["new_api"]["uid"]), api["uid"],
        before["services"][API]["created"], candidate_revision, candidate_tag)
    transition.validate()
    for revision in (candidate_revision, rollback_revision):
        need(rolling.short_revision(revision, API) == revision
             and boundary.revision(API, revision) is None, "revision_not_absent")
    plan = Plan(transition, rollback_revision, before, _etag(api), "0" * 64, source, _epoch(now), location)
    plan.validate()
    body = _prepare(plan, native[API])
    plan = Plan(transition, rollback_revision, before, _etag(api), rolling.sha(body), source, plan.captured_at, location)
    plan.verify_source()
    return plan


class Journal:
    """Exclusive complete records, bound to the original private directory inode."""
    def __init__(self, directory, plan, *, create=False):
        self.directory, self.plan_hash = Path(directory), plan.digest
        need(self.directory.is_absolute() and self.directory.resolve() == self.directory, "journal_location")
        status = self.directory.stat(follow_symlinks=False)
        need(stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid()
             and stat.S_IMODE(status.st_mode) == 0o700, "journal_private")
        self.location = plan.journal_location
        need(self.location == {"path": str(self.directory), "device": status.st_dev, "inode": status.st_ino},
             "journal_custody")
        if create:
            need(not any(self.directory.iterdir()), "journal_not_empty")
            self.record("binding", self.location)
            self.record("plan", {"plan": asdict(plan)})
        self.verify()

    def verify(self):
        status = self.directory.stat(follow_symlinks=False)
        need(stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid()
             and stat.S_IMODE(status.st_mode) == 0o700
             and self.location == {"path": str(self.directory), "device": status.st_dev, "inode": status.st_ino}
             and self.read("binding") == self.location | {"plan_sha256": self.plan_hash}, "journal_custody")

    @contextmanager
    def locked(self):
        self.verify()
        fd = os.open(self.directory / "owner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            status = os.fstat(fd)
            need(stat.S_ISREG(status.st_mode) and status.st_uid == os.getuid()
                 and stat.S_IMODE(status.st_mode) == 0o600 and status.st_nlink == 1, "journal_lock")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.verify()
            yield
        finally:
            os.close(fd)

    def read(self, name):
        need(re.fullmatch(r"[a-z.-]{1,80}", name), "record_name")
        path = self.directory / (name + ".json")
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, "rb") as stream:
            status = os.fstat(stream.fileno())
            need(stat.S_ISREG(status.st_mode) and status.st_uid == os.getuid()
                 and stat.S_IMODE(status.st_mode) == 0o600 and status.st_nlink == 1
                 and 0 < status.st_size <= MAX_BYTES, "record_private")
            raw = stream.read(MAX_BYTES + 1)
        need(len(raw) == status.st_size, "record_bound")
        value = rolling.release._json(raw)
        need(type(value) is dict and value.get("plan_sha256") == self.plan_hash, "record_plan")
        return value

    def record(self, name, value):
        need(re.fullmatch(r"[a-z.-]{1,80}", name), "record_name")
        raw = rolling.canonical(value | {"plan_sha256": self.plan_hash}) + b"\n"
        need(len(raw) <= MAX_BYTES, "record_bound")
        # A durable exclusive claim survives a publication crash and forbids replay.
        claim = os.open(self.directory / (name + ".claim"), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fsync(claim)
        os.close(claim)
        self._sync()
        descriptor, temporary = tempfile.mkstemp(prefix=".record-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.directory / (name + ".json"))
            self._sync()
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def claimed(self, name):
        return (self.directory / (name + ".claim")).exists()

    def _sync(self):
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _guard(plan, journal):
    plan.verify_source()
    need(journal.plan_hash == plan.digest, "journal_plan")
    journal.verify()


def _shared(plan, observed):
    need(observed["identities"] == plan.before["identities"], "identity_changed")
    readback._validate_callers(plan.transition, plan.before["callers"], observed["callers"])
    for name in (*readback.WORKERS, readback.COORDINATOR):
        need(observed["services"][name] == plan.before["services"][name], "other_workload_changed")


def _retained(plan, boundary, expected):
    result = {}
    resource = rolling.PARENT + "/services/" + API
    for name, previous in expected.items():
        row = boundary.revision(API, name)
        need(type(row) is dict and row.get("name") == resource + "/revisions/" + name
             and row.get("service") in {API, resource}
             and any(item.get("type") == "Ready" and item.get("state") == "CONDITION_SUCCEEDED"
                     for item in row.get("conditions", [])), "retained_revision_unavailable")
        projected = readback._template(API, readback.application.template(row, revision=True)) | {"uid": row["uid"]}
        need(projected == previous, "retained_revision_changed")
        result[name] = projected
    return result


def _expected_traffic(plan, restored=False):
    _, traffic, _ = readback._prior_api(plan.transition, plan.before["services"][API])
    if restored:
        traffic = [row | {"revision": plan.rollback_revision} for row in traffic]
    elif plan.transition.candidate_tag is not None:
        traffic.append({"type": readback.REVISION_TRAFFIC, "revision": plan.transition.candidate_revision,
                        "tag": plan.transition.candidate_tag})
    return sorted(traffic, key=rolling.canonical)


def validate_rollback(plan, phase, before, after, retained):
    """Versioned explicit rollback shape; never reverse the original transition."""
    need(phase in {"rollback-template", "rollback-traffic"}, "rollback_phase")
    _shared(plan, after)
    need(after["callers"] == before["callers"], "rollback_callers_changed")
    prior, target = before["services"][API], after["services"][API]
    old = plan.before["services"][API]
    need(set(prior) == set(target) == set(old) and target["uid"] == old["uid"]
         and target["static_sha256"] == old["static_sha256"]
         and target["generation"] == prior["generation"] + 1
         and target["ready"] == target["created"] == plan.rollback_revision
         and target["template"] == old["template"]
         and target["traffic"] == _expected_traffic(plan, restored=phase == "rollback-traffic"),
         "rollback_shape")
    revision = target["revisions"][plan.rollback_revision]
    need(re.fullmatch(r"[a-f0-9-]{36}", revision["uid"])
         and revision == old["template"] | {"uid": revision["uid"]}
         and (phase != "rollback-template" or all(row["uid"] != revision["uid"] for row in retained.values())),
         "rollback_revision")
    expected = retained | {plan.rollback_revision: revision}
    need(target["revisions"] == {name: expected[name] for name in {
        plan.rollback_revision, *(row["revision"] for row in target["traffic"]) }}, "rollback_inventory")
    return {"kind": ROLLBACK_KIND, "status": "old_identity_equivalent_revision_verified"}


def _started(plan, journal, phase, previous):
    value = journal.read(phase + ".started")
    need(value is not None and set(value) == {"request_sha256", "mask", "etag", "previous_generation",
         "service_uid", "kind", "plan_sha256"} and readback._hash(value["request_sha256"])
         and value["mask"] == ("traffic" if phase == "rollback-traffic" else "template,traffic")
         and value["kind"] == (KIND if phase == "stage" else ROLLBACK_KIND)
         and value["service_uid"] == plan.transition.service_uid
         and type(value["previous_generation"]) is int
         and value["previous_generation"] == previous["services"][API]["generation"]
         and type(value["etag"]) is str and re.fullmatch(r"[!-~]{1,256}", value["etag"]), "started_changed")
    if phase == "stage":
        need(value["request_sha256"] == plan.stage_request_sha256 and value["etag"] == plan.api_etag,
             "started_request_changed")
    return value


def _ack(plan, journal, phase, started):
    value = journal.read(phase + ".ack")
    if value is not None:
        need(set(value) == {"name", "request_sha256", "plan_sha256"}
             and value["request_sha256"] == started["request_sha256"]
             and type(value["name"]) is str and rolling.OPERATION.fullmatch(value["name"]), "ack_changed")
    return value


def _verify_receipt(plan, journal, phase):
    """Revalidate saved metadata against the fixed plan before trusting any next phase."""
    previous, retained, result = plan.before, plan.before["services"][API]["revisions"], None
    for current in PHASES[:PHASES.index(phase) + 1]:
        started = _started(plan, journal, current, previous)
        ack = _ack(plan, journal, current, started)
        result = journal.read(current + ".checked")
        need(result is not None and set(result) == {"observation", "retained", "operation_ack_retained",
             "request_sha256", "plan_sha256"} and type(result["operation_ack_retained"]) is bool
             and result["operation_ack_retained"] == (ack is not None)
             and result["request_sha256"] == started["request_sha256"], "checked_receipt_changed")
        observation = result["observation"]
        if current == "stage":
            readback.validate({"kind": readback.KIND, "plan": asdict(plan.transition),
                               "before": plan.before, "after": observation})
        else:
            validate_rollback(plan, current, previous, observation, retained)
        retained = retained | observation["services"][API]["revisions"]
        need(result["retained"] == retained, "checked_retention_changed")
        previous = observation
    return result


def _readback(plan, boundary, journal, phase):
    previous = None if phase == "stage" else _verify_receipt(
        plan, journal, "stage" if phase == "rollback-template" else "rollback-template")
    started = _started(plan, journal, phase, plan.before if previous is None else previous["observation"])
    ack = _ack(plan, journal, phase, started)
    if ack is not None:
        operation = boundary.operation(ack["name"])
        need(type(operation) is dict and set(operation) == {"name", "done"}
             and operation.get("name") == ack["name"] and type(operation.get("done")) is bool,
             "same_native_operation_failed")
        if not operation["done"]:
            return {"status": "same_native_operation_pending", "phase": phase}
    observation, _ = _observe(boundary)
    _shared(plan, observation)
    if phase == "stage":
        readback.validate({"kind": readback.KIND, "plan": asdict(plan.transition),
                           "before": plan.before, "after": observation})
        expected = plan.before["services"][API]["revisions"]
    else:
        need(previous is not None, "rollback_predecessor_missing")
        assert previous is not None
        expected = previous["retained"]
        validate_rollback(plan, phase, previous["observation"], observation, expected)
    retained = _retained(plan, boundary, expected)
    retained.update(observation["services"][API]["revisions"])
    checked = journal.read(phase + ".checked")
    value = {"observation": observation, "retained": retained, "operation_ack_retained": ack is not None,
             "request_sha256": started["request_sha256"]}
    if checked is None:
        journal.record(phase + ".checked", value)
    else:
        need(checked == value | {"plan_sha256": plan.digest}, "checked_state_changed")
    _verify_receipt(plan, journal, phase)
    return {"status": "api_identity_action_verified", "phase": phase,
            "operation_ack_retained": ack is not None}


def _send(plan, boundary, journal, phase, body, mask, preimage):
    _guard(plan, journal)
    if phase == "stage":
        need(0 <= _epoch(None) - plan.captured_at < 21600, "capture_expired")
    need(not journal.claimed(phase + ".started") and journal.read(phase + ".started") is None,
         "started_reconcile_only")
    # Last fresh ETag and full service comparison closes the local preparation gap.
    need(boundary.service(API) == preimage, "fresh_preimage_changed")
    journal.record(phase + ".started", {"request_sha256": rolling.sha(body), "mask": mask,
        "etag": body["etag"], "previous_generation": int(preimage["generation"]),
        "service_uid": preimage["uid"], "kind": KIND if phase == "stage" else ROLLBACK_KIND})
    ack = boundary.patch(body, mask)
    need(type(ack) is dict and set(ack) == {"name"} and type(ack["name"]) is str
         and rolling.OPERATION.fullmatch(ack["name"]), "native_ack_unverified")
    journal.record(phase + ".ack", ack | {"request_sha256": rolling.sha(body)})
    return _readback(plan, boundary, journal, phase)


def stage(plan, boundary, journal):
    _guard(plan, journal)
    with journal.locked():
        need(0 <= _epoch(None) - plan.captured_at < 21600, "capture_expired")
        need(not journal.claimed("stage.started"), "started_reconcile_only")
        observation, native = _observe(boundary)
        _shared(plan, observation)
        need(observation["services"] == plan.before["services"], "stage_preimage_changed")
        need(boundary.revision(API, plan.transition.candidate_revision) is None
             and boundary.revision(API, plan.rollback_revision) is None, "revision_not_absent")
        body = _prepare(plan, native[API])
        need(rolling.sha(body) == plan.stage_request_sha256, "stage_request_changed")
        return _send(plan, boundary, journal, "stage", body, "template,traffic", native[API]["service"])


def rollback(plan, boundary, journal):
    _guard(plan, journal)
    with journal.locked():
        _verify_receipt(plan, journal, "stage")
        result = None
        for phase, predecessor in (("rollback-template", "stage"), ("rollback-traffic", "rollback-template")):
            if journal.claimed(phase + ".started"):
                need(journal.read(phase + ".checked") is not None, "started_reconcile_only")
                _verify_receipt(plan, journal, phase)
                continue
            previous = _verify_receipt(plan, journal, predecessor)
            need(previous is not None, "rollback_predecessor_missing")
            observation, native = _observe(boundary)
            _shared(plan, observation)
            need(observation == previous["observation"], "rollback_preimage_changed")
            _retained(plan, boundary, previous["retained"])
            api = native[API]["service"]
            body = {"name": api["name"], "etag": _etag(api), "traffic": _expected_traffic(
                plan, restored=phase == "rollback-traffic")}
            mask = "traffic"
            if phase == "rollback-template":
                need(boundary.revision(API, plan.rollback_revision) is None, "rollback_revision_not_absent")
                body["template"] = api["template"] | {"revision": plan.rollback_revision,
                                                       "serviceAccount": plan.transition.old_api.email}
                mask = "template,traffic"
            result = _send(plan, boundary, journal, phase, body, mask, api)
            if result["status"] == "same_native_operation_pending":
                return result
        if result is None:
            result = _readback(plan, boundary, journal, "rollback-traffic")
        return result


def reconcile(plan, boundary, journal):
    """Read only; never start another PATCH phase or reconstruct a native ACK."""
    _guard(plan, journal)
    with journal.locked():
        phases = [phase for phase in PHASES if journal.claimed(phase + ".started")]
        need(bool(phases), "no_started_action")
        return _readback(plan, boundary, journal, phases[-1])


def observe(plan, boundary, journal):
    """Fresh read-only validation of the latest owned operation and retained revisions."""
    _guard(plan, journal)
    with journal.locked():
        phases = [phase for phase in PHASES if journal.claimed(phase + ".started")]
        if phases:
            phase = phases[-1]
            # Observe may verify an existing receipt, but never creates one.
            previous = _verify_receipt(plan, journal, phase)
            observation, _ = _observe(boundary)
            need(observation == previous["observation"], "observed_state_changed")
            _retained(plan, boundary, previous["retained"])
            return {"status": "api_identity_observed", "phase": phase}
        observation, _ = _observe(boundary)
        need(observation == plan.before, "baseline_changed")
        return {"status": "api_identity_observed", "phase": "captured"}


class NativeBoundary:
    """Fixed stock normal-manager reads and one API-only PATCH; no IAM writer."""
    def __init__(self):
        from infra.gcp import rolling_native
        self.observer = rolling_native.fresh.FreshNativeBoundary(ROOT)

        class APIRun(rolling_native.NativeRun):
            SERVICES = readback.SERVICES

            def patch(self, service, body, mask):
                need(service == API, "api_only")
                return super().patch(service, body, mask)
        self.run = APIRun()

    def service(self, name):
        return self.run.service(name)

    def revision(self, name, revision):
        return self.run.revision(name, revision)

    def iam(self, name):
        need(name in (*readback.SERVICES, *readback.DOCUMENTS), "iam_read_scope")
        return self.observer._cloud("run", "services", "get-iam-policy", name,
                                    "--region", rolling.release.REGION)

    def identity(self, email):
        need(email in {rolling.RUNTIME, NEW_API_EMAIL}, "identity_read_scope")
        account = self.observer._cloud("iam", "service-accounts", "describe", email)
        keys = self.observer._cloud("iam", "service-accounts", "keys", "list", "--iam-account", email)
        return readback.project_identity(account, keys)

    def patch(self, body, mask):
        return self.run.patch(API, body, mask)

    def operation(self, name):
        return self.run.operation(name)

    def close(self):
        self.run.stock.session.close()


def _load(directory, digest):
    need(type(digest) is str and readback._hash(digest), "reviewed_plan_hash")
    # Read metadata before constructing any native client. Journal repeats all custody checks.
    holder = object.__new__(Journal)
    holder.directory, holder.plan_hash = directory, digest
    value = holder.read("plan")
    need(value is not None and set(value) == {"plan", "plan_sha256"}, "plan_record")
    plan = Plan.from_dict(value["plan"])
    need(plan.digest == digest, "reviewed_plan_changed")
    journal = Journal(directory, plan)
    _guard(plan, journal)
    return plan, journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "stage", "observe", "reconcile", "rollback"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--candidate-revision")
    parser.add_argument("--rollback-revision")
    parser.add_argument("--candidate-tag")
    args = parser.parse_args()
    boundary = None
    try:
        if args.action == "capture":
            need(args.candidate_revision is not None and args.rollback_revision is not None
                 and args.plan_sha256 is None, "capture_arguments")
            source_state()
            need(args.journal.is_absolute() and args.journal.resolve() == args.journal
                 and not args.journal.exists(), "new_original_journal")
            boundary = NativeBoundary()
            plan = capture(boundary, args.candidate_revision, args.rollback_revision,
                           journal_directory=args.journal, candidate_tag=args.candidate_tag)
            Journal(args.journal, plan, create=True)
            result = {"status": "api_identity_captured", "plan_sha256": plan.digest,
                      "native_mutations": 0, "native_approval": False}
        else:
            need(args.candidate_revision is None and args.rollback_revision is None and args.candidate_tag is None,
                 "review_arguments")
            plan, journal = _load(args.journal, args.plan_sha256)
            boundary = NativeBoundary()
            result = {"stage": stage, "observe": observe, "reconcile": reconcile,
                      "rollback": rollback}[args.action](plan, boundary, journal)
        print(json.dumps(result))
        return 0
    except Exception:
        print(json.dumps({"status": "api_identity_operator_refused", "next_action": "inspect_original_journal_reconcile",
                          "native_approval": False}))
        return 1
    finally:
        if boundary is not None:
            try:
                boundary.close()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
