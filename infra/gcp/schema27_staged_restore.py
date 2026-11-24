"""Restore an acknowledged, unpromoted mixed Source27 stage to closed Source70.

This is a distinct restore-only journal, never a continuation or freshness waiver
for the interrupted B release. Immutable old and staged revisions are retained.
Only service templates and traffic can be written. Raw templates and literal
environment values stay in RAM; durable records contain redacted projections.
An uncertain PATCH is never replayed and an absent ACK is never reconstructed.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import os
import re
import stat
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from infra.gcp import rolling_native as native
from infra.gcp import schema27_api_identity_operator as owned
from infra.gcp import schema27_mixed_app_successor as mixed

rolling, scope, identities = mixed.rolling, mixed.scope, mixed.identities
ROOT = Path(__file__).resolve().parents[2]
KIND = "hirewiz_schema27_interrupted_stage_restore_v1"
PROOF = "hirewiz_schema27_homogeneous_restore_verified_v1"
SOURCE70 = owned.APPLICATION_RELEASE
SOURCE3F7 = "3f7fd47738b336e4cbfdfdff2f6fdc13adca74c6"
B_FILE_SHA256 = "3ee4d69106719f3d9fb23d845b378645113456993dcc8a99d62b0d5a2cbc1fbc"
SERVICES = rolling.release.SERVICES
GENERATIONS = dict(zip(SERVICES, (387, 96, 49), strict=True))
STAGED_UIDS = dict(zip(SERVICES, (
    "e92ffcd3-3357-4ad5-8a0b-12e112fb0676", "01d33d3e-ecae-403f-a6cd-3abdd9582613",
    "7ae48e64-8f21-4b39-a94f-178f8392eff9"), strict=True))


def need(value, code):
    rolling.need(value, "schema27_staged_restore_" + code)


def source_state():
    result = owned.source_state()
    need(owned.ROOT == ROOT and Path(mixed.__file__).resolve().parents[2] == ROOT,
         "module_origin")
    result["files"]["infra/gcp/schema27_staged_restore.py"] = hashlib.sha256(
        Path(__file__).read_bytes()).hexdigest()
    return result


def _private_read(path, digest):
    path = Path(path)
    need(path.is_absolute() and path.resolve() == path and rolling.HASH.fullmatch(digest or ""),
         "private_file_pin")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        need(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid() and info.st_nlink == 1
             and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 524288,
             "private_file")
        raw = stream.read(524289)
    need(len(raw) == info.st_size and hashlib.sha256(raw).hexdigest() == digest, "file_changed")
    return raw


def _public_template(template):
    """No literal, literal hash, label, or annotation value enters evidence."""
    result = copy.deepcopy(template)
    result.pop("labels", None)
    result.pop("annotations", None)
    for container in result.get("containers", []):
        container.pop("labels", None)
        container.pop("annotations", None)
        for row in container.get("env", []):
            if "value" in row:
                row["value"] = "<inline-redacted>"
    return result


def _template(value, service):
    result = copy.deepcopy(value)
    if "revision" in result:
        result["revision"] = rolling.short_revision(result["revision"], service)
    return result


def _projection(value, service):
    return {"service_uid": value["uid"], "generation": int(value["generation"]),
            "etag": value["etag"], "template_public_sha256": rolling.sha(
                _public_template(_template(value["template"], service))),
            "traffic": rolling.pinned_traffic(service, value)}


@dataclass(frozen=True)
class StagePin:
    service: str
    started_sha256: str
    ack_sha256: str
    checked_sha256: str


@dataclass(frozen=True)
class ServicePin:
    service: str
    before: dict
    prior_revision_uid: str
    staged_revision_uid: str
    revision: str
    traffic: tuple[dict, ...]


@dataclass(frozen=True)
class RestorePlan:
    application: mixed.Plan
    original_plan_path: str
    original_plan_file_sha256: str
    stages: tuple[StagePin, ...]
    services: tuple[ServicePin, ...]
    nonce: str
    source: dict
    journal_location: dict
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def health_tag(self):
        return "restore70-" + self.nonce[:12]

    def pin(self, service):
        need(service in SERVICES, "service_scope")
        return next(pin for pin in self.services if pin.service == service)

    def validate(self):
        need(type(self) is RestorePlan and self.kind == KIND and type(self.application) is mixed.Plan,
             "plan_kind")
        self.application.validate()
        app = self.application
        need(app.prior_source.release == SOURCE70 and app.release == SOURCE3F7
             and self.original_plan_file_sha256 == B_FILE_SHA256
             and Path(self.original_plan_path).is_absolute()
             and re.fullmatch(r"[a-f0-9]{32}", self.nonce) and self.nonce != app.nonce,
             "exact_interrupted_release")
        need(tuple(pin.service for pin in self.stages) == SERVICES
             and all(type(pin) is StagePin and all(rolling.HASH.fullmatch(value) for value in
                     (pin.started_sha256, pin.ack_sha256, pin.checked_sha256)) for pin in self.stages),
             "stage_receipts")
        need(tuple(pin.service for pin in self.services) == SERVICES, "restore_service_scope")
        for pin in self.services:
            original = app.pin(pin.service)
            need(type(pin) is ServicePin and pin.before["service_uid"] == original.uid
                 and pin.before["generation"] == GENERATIONS[pin.service]
                 and pin.before["generation"] == original.generation + 1
                 and type(pin.before.get("etag")) is str and pin.before["etag"]
                 and rolling.HASH.fullmatch(pin.before.get("template_public_sha256", ""))
                 and pin.before["traffic"] == rolling._traffic_for(app, original, False)
                 and pin.revision == pin.service + "-restore-70-" + self.nonce[:12]
                 and pin.revision not in {original.prior_revision, app.revision(pin.service)}
                 and pin.staged_revision_uid == STAGED_UIDS[pin.service]
                 and all(re.fullmatch(r"[a-f0-9-]{36}", uid) for uid in
                         (pin.prior_revision_uid, pin.staged_revision_uid)), "service_pin")
            expected = tuple(sorted([row | {"revision": pin.revision} for row in pin.before["traffic"]]
                + [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": pin.revision,
                    "tag": self.health_tag}], key=rolling.canonical))
            need(pin.traffic == expected and rolling.pinned_traffic(pin.service,
                 {"trafficStatuses": list(pin.traffic)}, prior_revision=pin.revision) == list(pin.traffic),
                 "source70_all_aliases")
        need(type(self.source) is dict and set(self.source) == {"release", "tree", "files"}
             and all(re.fullmatch(r"[a-f0-9]{40}", self.source[key]) for key in ("release", "tree"))
             and type(self.source["files"]) is dict
             and "infra/gcp/schema27_staged_restore.py" in self.source["files"]
             and all(rolling.HASH.fullmatch(stamp) for stamp in self.source["files"].values()), "source_pin")
        need(type(self.journal_location) is dict and set(self.journal_location) == {"path", "device", "inode"}
             and Path(self.journal_location["path"]).is_absolute()
             and all(type(self.journal_location[key]) is int for key in ("device", "inode")), "journal_pin")


def load(raw, digest):
    need(type(raw) is bytes and len(raw) <= 524288 and hashlib.sha256(raw).hexdigest() == digest,
         "plan_file")
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {field.name for field in fields(RestorePlan)}, "plan_shape")
    child = rolling.canonical(value["application"])
    result = RestorePlan(**(value | {
        "application": mixed.load(child, hashlib.sha256(child).hexdigest()),
        "stages": tuple(StagePin(**pin) for pin in value["stages"]),
        "services": tuple(ServicePin(**(pin | {"traffic": tuple(pin["traffic"])})) for pin in value["services"])}))
    result.validate()
    need(rolling.canonical(asdict(result)) == rolling.canonical(value), "plan_canonical")
    return result


def _original(plan):
    raw = _private_read(plan.original_plan_path, plan.original_plan_file_sha256)
    app = mixed.load(raw, plan.original_plan_file_sha256)
    need(app == plan.application, "original_plan_changed")
    journal = rolling.Journal(Path(app.journal_location["path"]), app.custody())
    scope.verify_journal(app.custody(), journal)
    need(scope.journal_location(journal) == app.journal_location, "original_journal_changed")
    return journal


def _stage_pins(app, journal):
    scope.verify_journal(app.custody(), journal)
    need(journal.read(scope.MIGRATION_FENCE) is None
         and journal.read(mixed.app.historical.ROLLBACK_INTENT) is None, "foreign_intent")
    # Inspect filenames too: an incomplete or unbound intent must not disappear
    # just because it cannot be parsed as a complete original journal record.
    need(not any(path.name.startswith(("promote.", "rollback."))
                 for path in journal.directory.iterdir()), "promotion_or_rollback_started")
    result = []
    for pin in app.services:
        key = "stage." + pin.service
        started, ack, checked = (journal.read(key + suffix) for suffix in (".started", ".ack", ".checked"))
        need(all(type(row) is dict for row in (started, ack, checked)), "genuine_complete_stage_required")
        need(started.get("phase") == "stage" and started.get("mask") == "template,traffic"
             and started.get("service_uid") == pin.uid
             and started.get("expected_generation") == GENERATIONS[pin.service] == pin.generation + 1
             and started.get("replay_authorized") is False
             and started.get("traffic") == rolling._traffic_for(app, pin, False)
             and rolling.HASH.fullmatch(started.get("request_sha256", ""))
             and ack.get("request_sha256") == started["request_sha256"]
             and rolling.OPERATION.fullmatch(ack.get("operation", ""))
             and checked.get("service_uid") == pin.uid
             and checked.get("generation") == started["expected_generation"]
             and checked.get("revision") == app.revision(pin.service)
             and checked.get("native_metadata_reobserved") is True
             and checked.get("operation_ack_retained") is True
             and checked.get("replay_authorized") is False, "original_stage_receipt")
        result.append(StagePin(pin.service, rolling.sha(started), rolling.sha(ack), rolling.sha(checked)))
    return tuple(result)


def _revision(boundary, app, pin, name, expected_uid=None):
    value = boundary.revision(pin.service, name)
    need(type(value) is dict and value.get("name") == rolling.PARENT + "/services/" + pin.service
         + "/revisions/" + name and type(value.get("uid")) is str
         and (expected_uid is None or value["uid"] == expected_uid)
         and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                 for row in value.get("conditions", [])), "immutable_revision")
    need(value.get("serviceAccount") == app.identities.identity(pin.service).email, "revision_identity")
    return value


def _prior(boundary, app, pin, expected_uid=None):
    prior = _revision(boundary, app, pin, pin.prior_revision, expected_uid)
    # Historical comparisons occur only in RAM against existing B custody. No
    # new evidence contains a hash derived from an inline credential value.
    need(rolling.sha({key: prior.get(key) for key in pin.revision_keys}) == pin.revision_sha256,
         "retained_prior_configuration")
    template = {key: prior.get(key) for key in pin.revision_keys}
    if rolling.template_sha(template, pin.service) != pin.template_sha256:
        template["revision"] = pin.prior_revision
    need(rolling.template_sha(template, pin.service) == pin.template_sha256, "retained_prior_template")
    env = rolling.environment(template)
    need(env.get("APP_RELEASE") == {"name": "APP_RELEASE", "value": SOURCE70}
         and template["containers"][0]["image"].endswith("@" + app.prior_source.image_digest), "prior_source70")
    return prior, template


def _closed(app, template):
    env = rolling.environment(template)
    need(env.get("AUTO_DB_MIGRATE") == {"name": "AUTO_DB_MIGRATE", "value": "false"}
         and all(env.get(name, {"value": "false"}).get("value") == "false"
                 for name in app.disabled_flags()), "spending_or_migration_admission")


def _staged_template(app, pin, current, prior):
    baseline = current | {"template": prior, "generation": pin.generation, "observedGeneration": pin.generation,
        "trafficStatuses": list(pin.traffic), "latestReadyRevision": pin.prior_revision,
        "latestCreatedRevision": pin.prior_revision, "reconciling": False,
        "terminalCondition": {"state": "CONDITION_SUCCEEDED"}}
    return mixed.stage_body(app, pin, baseline)["template"]


def _custody(plan):
    plan.validate()
    need(source_state() == plan.source, "controller_source_changed")
    plan.application.identities.verify_custody()
    original = _original(plan)
    need(_stage_pins(plan.application, original) == plan.stages, "original_receipts_changed")
    return original


def _base_native(plan, boundary, original):
    app = plan.application
    app.identities.verify_principals(boundary.identity)
    boundary.verify_sources(app)
    for pin in app.services:
        ack = original.read("stage." + pin.service + ".ack")
        operation = boundary.operation(ack["operation"])
        need(operation.get("name") == ack["operation"] and operation.get("done") is True
             and "error" not in operation, "original_operation_unresolved")
        current = boundary.service(pin.service)
        rolling._ready(current, pin)
        policy = boundary.iam(pin.service)
        need(rolling.static_sha(current) == pin.static_sha256
             and rolling.sha(policy) == pin.iam_sha256, "static_or_iam_changed")
        rolling.worker_iam(pin.service, policy)


def capture(original_plan_path, original_sha256, boundary, *, nonce, journal_directory):
    """Read only in GCP. Bind the genuine all-staged B state, not an old lockfile."""
    need(type(boundary) is NativeBoundary, "exact_native_boundary")
    app = mixed.load(_private_read(original_plan_path, original_sha256), original_sha256)
    need(original_sha256 == B_FILE_SHA256 and app.release == SOURCE3F7
         and app.prior_source.release == SOURCE70, "exact_interrupted_release")
    original = rolling.Journal(Path(app.journal_location["path"]), app.custody())
    directory = Path(journal_directory)
    need(directory.exists() and not any(directory.iterdir()), "new_empty_journal_required")
    location = scope.journal_location(type("Location", (), {"directory": directory})())
    need(not directory.is_relative_to(original.directory) and not original.directory.is_relative_to(directory),
         "distinct_journal_required")
    with scope.lifecycle_lock(original), original.locked():
        stages, pins = _stage_pins(app, original), []
        for pin in app.services:
            current = boundary.service(pin.service)
            rolling._ready(current, pin)
            prior, template = _prior(boundary, app, pin)
            staged = _revision(boundary, app, pin, app.revision(pin.service), STAGED_UIDS[pin.service])
            expected = _staged_template(app, pin, current, template)
            _closed(app, current["template"])
            need(_template(current["template"], pin.service) == _template(expected, pin.service)
                 and int(current["generation"]) == GENERATIONS[pin.service]
                 and all(rolling.short_revision(current[key], pin.service) == app.revision(pin.service)
                         for key in ("latestCreatedRevision", "latestReadyRevision")), "actual_staged_preimage")
            revision = pin.service + "-restore-70-" + nonce[:12]
            need(boundary.revision(pin.service, revision) is None, "restore_revision_exists")
            before = _projection(current, pin.service)
            traffic = sorted([row | {"revision": revision} for row in before["traffic"]]
                + [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION", "revision": revision,
                    "tag": "restore70-" + nonce[:12]}], key=rolling.canonical)
            pins.append(ServicePin(pin.service, before, prior["uid"], staged["uid"], revision, tuple(traffic)))
        plan = RestorePlan(app, str(original_plan_path), original_sha256, stages, tuple(pins), nonce,
                           source_state(), location)
        plan.validate()
        _custody(plan)
        _base_native(plan, boundary, original)
        _verify_current(plan, boundary, None)
        return plan


def _restore_template(plan, boundary, pin):
    _, template = _prior(boundary, plan.application, plan.application.pin(pin.service), pin.prior_revision_uid)
    template = copy.deepcopy(template)
    template["revision"] = pin.revision
    env = rolling.environment(template)
    for name in (*plan.application.disabled_flags(), "AUTO_DB_MIGRATE"):
        env[name] = {"name": name, "value": "false"}
    template["containers"][0]["env"] = list(env.values())
    _closed(plan.application, template)
    return template


def _stage_traffic(plan, pin):
    return sorted(pin.before["traffic"] + [{"type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
        "revision": pin.revision, "tag": plan.health_tag}], key=rolling.canonical)


def _record(plan, journal, pin, phase):
    key = phase + "." + pin.service
    started, ack, checked = (journal.read(key + suffix) for suffix in (".started", ".ack", ".checked"))
    need(not journal.claimed(key + ".started") or started is not None, "incomplete_intent_reconcile_only")
    if started is not None:
        previous = pin.before
        if phase == "restore-route":
            previous = journal.read("restore-stage." + pin.service + ".checked")
            need(previous is not None and previous.get("health_verified") is True,
                 "prior_health_required")
        expected = {"service_uid": pin.before["service_uid"], "previous_generation": previous["generation"],
            "previous_etag": previous["etag"], "expected_generation": previous["generation"] + 1,
            "revision": pin.revision,
            "traffic": _stage_traffic(plan, pin) if phase == "restore-stage" else list(pin.traffic),
            "mask": "template,traffic" if phase == "restore-stage" else "traffic",
            "replay_authorized": False, "plan_sha256": plan.digest}
        need(started == expected, "restore_intent_changed")
        need(ack is not None, "missing_native_ack_reconcile_only")
        need(set(ack) == {"operation", "intent_sha256", "plan_sha256"}
             and ack["intent_sha256"] == rolling.sha(started)
             and rolling.OPERATION.fullmatch(ack["operation"]), "restore_ack_changed")
    else:
        need(ack is None and checked is None, "orphan_restore_receipt")
    return started, ack, checked


def _phase_projection(plan, boundary, pin, current, prior, staged):
    restored = _revision(boundary, plan.application, plan.application.pin(pin.service), pin.revision)
    expected = _restore_template(plan, boundary, pin)
    outputs = mixed.app.historical.maintenance.REVISION_READ_OUTPUTS
    actual = {key: value for key, value in restored.items() if key not in outputs | {"revision"}}
    need(actual == {key: value for key, value in expected.items() if key != "revision"},
         "restored_immutable_template")
    return _projection(current, pin.service) | {"revision": pin.revision,
        "revision_uid": restored["uid"], "prior_revision_uid": prior["uid"],
        "staged_revision_uid": staged["uid"]}


def _verify_current(plan, boundary, journal):
    """Join acknowledged partial progress; no staged3f7 is called serving70."""
    original = _custody(plan)
    _base_native(plan, boundary, original)
    result = {}
    for pin in plan.services:
        apppin = plan.application.pin(pin.service)
        current = boundary.service(pin.service)
        rolling._ready(current, apppin)
        prior, prior_template = _prior(boundary, plan.application, apppin, pin.prior_revision_uid)
        staged = _revision(boundary, plan.application, apppin, plan.application.revision(pin.service),
                           pin.staged_revision_uid)
        staged_expected = _staged_template(plan.application, apppin, current, prior_template)
        outputs = mixed.app.historical.maintenance.REVISION_READ_OUTPUTS
        staged_actual = {key: value for key, value in staged.items() if key not in outputs | {"revision"}}
        need(staged_actual == {key: value for key, value in staged_expected.items() if key != "revision"},
             "staged_revision_changed")
        _closed(plan.application, current["template"])
        stage = _record(plan, journal, pin, "restore-stage") if journal else (None, None, None)
        route = _record(plan, journal, pin, "restore-route") if journal else (None, None, None)
        phase = "restore-route" if route[0] else "restore-stage"
        started, ack, checked = route if route[0] else stage
        if started:
            operation = boundary.operation(ack["operation"])
            need(operation.get("name") == ack["operation"] and "error" not in operation,
                 "restore_operation_failed_reconcile_only")
            need(operation.get("done") is True, "restore_operation_pending")
            expected = _restore_template(plan, boundary, pin)
            expected_traffic = list(pin.traffic) if phase == "restore-route" else _stage_traffic(plan, pin)
            need(int(current["generation"]) == pin.before["generation"] + (2 if route[0] else 1)
                 and _template(current["template"], pin.service) == _template(expected, pin.service)
                 and rolling.pinned_traffic(pin.service, current) == expected_traffic
                 and all(rolling.short_revision(current[key], pin.service) == pin.revision
                         for key in ("latestCreatedRevision", "latestReadyRevision")), "restored_state_drift")
            result[pin.service] = _phase_projection(plan, boundary, pin, current, prior, staged)
            if checked:
                need(checked == result[pin.service] | {"health_verified": True, "operation_ack_retained": True,
                     "replay_authorized": False, "plan_sha256": plan.digest}, "checked_state_changed")
            # A route record preserves its genuine zero-traffic health predecessor.
            if route[0]:
                need(stage[2] is not None and stage[2].get("revision_uid") == result[pin.service]["revision_uid"]
                     and stage[2].get("generation") == pin.before["generation"] + 1
                     and stage[2].get("traffic") == _stage_traffic(plan, pin)
                     and stage[2].get("health_verified") is True, "stage_health_receipt_changed")
        else:
            need(_projection(current, pin.service) == pin.before
                 and _template(current["template"], pin.service) == _template(staged_expected, pin.service)
                 and boundary.revision(pin.service, pin.revision) is None, "unowned_staged_preimage")
            result[pin.service] = _projection(current, pin.service)
    return result


def execute(mode, plan, boundary, journal):
    """Stage closed70 at zero, health-check, then route all aliases to that image.

    Both writes have independent exclusive intent/ACK/LRO/readback custody. Every
    service continues serving Source70 throughout. A failed new revision leaves
    the old Source70 allocation untouched and the new action unreplayed.
    """
    need(mode in {"restore", "reconcile", "observe"} and type(plan) is RestorePlan
         and type(boundary) is NativeBoundary
         and type(journal) is owned.Journal and journal.plan_hash == plan.digest, "exact_owner")
    original = _custody(plan)
    with scope.lifecycle_lock(original), original.locked(), journal.locked():
        for pin in plan.services:
            for phase in ("restore-stage", "restore-route"):
                started, ack, _ = _record(plan, journal, pin, phase)
                if started:
                    operation = boundary.operation(ack["operation"])
                    need(operation.get("name") == ack["operation"] and "error" not in operation,
                         "restore_operation_failed_reconcile_only")
                    if operation.get("done") is not True:
                        return {"status": "same_native_operation_pending", "service": pin.service,
                                "phase": phase, "native_mutations": 0, "replay_authorized": False}
        _verify_current(plan, boundary, journal)
        mutations = 0
        for pin in plan.services:
            for phase in ("restore-stage", "restore-route"):
                key = phase + "." + pin.service
                started, ack, checked = _record(plan, journal, pin, phase)
                if started is None:
                    if mode != "restore":
                        continue
                    _verify_current(plan, boundary, journal)
                    current = boundary.service(pin.service)
                    previous = pin.before if phase == "restore-stage" else journal.read(
                        "restore-stage." + pin.service + ".checked")
                    need(previous is not None and _projection(current, pin.service) == {
                        key: previous[key] for key in pin.before}, "last_preimage_changed")
                    body = {"name": current["name"], "etag": current["etag"],
                            "traffic": _stage_traffic(plan, pin) if phase == "restore-stage" else list(pin.traffic)}
                    mask = "traffic"
                    if phase == "restore-stage":
                        body["template"] = _restore_template(plan, boundary, pin)
                        mask = "template,traffic"
                    else:
                        need(previous.get("health_verified") is True, "prior_health_required")
                    # Full native preimage stays in RAM; no request-body hash.
                    need(boundary.service(pin.service) == current, "last_preimage_changed")
                    started = {"service_uid": pin.before["service_uid"],
                        "previous_generation": previous["generation"], "previous_etag": previous["etag"],
                        "expected_generation": previous["generation"] + 1, "revision": pin.revision,
                        "traffic": body["traffic"], "mask": mask, "replay_authorized": False}
                    journal.record(key + ".started", started)
                    started = journal.read(key + ".started")
                    acknowledgement = boundary.patch(pin.service, body, mask)
                    need(type(acknowledgement) is dict and set(acknowledgement) == {"name"}
                         and rolling.OPERATION.fullmatch(acknowledgement.get("name", "")), "native_ack_unmapped")
                    journal.record(key + ".ack", {"operation": acknowledgement["name"],
                                                   "intent_sha256": rolling.sha(started)})
                    mutations += 1
                    operation = boundary.operation(acknowledgement["name"])
                    need(operation.get("name") == acknowledgement["name"] and "error" not in operation,
                         "restore_operation_failed_reconcile_only")
                    if operation.get("done") is not True:
                        return {"status": "same_native_operation_pending", "service": pin.service,
                                "phase": phase, "native_mutations": mutations, "replay_authorized": False}
                if checked is None and started is not None and mode != "observe":
                    projection = _verify_current(plan, boundary, journal)[pin.service]
                    boundary.health(plan.application, pin.service, pin.revision, SOURCE70, plan.health_tag)
                    need(_verify_current(plan, boundary, journal)[pin.service] == projection,
                         "health_preimage_changed")
                    journal.record(key + ".checked", projection | {"health_verified": True,
                        "operation_ack_retained": True, "replay_authorized": False})
                if journal.read(key + ".checked") is None:
                    # Reconciliation never starts the subsequent traffic phase.
                    break
        projection = _verify_current(plan, boundary, journal)
        complete = all(journal.read("restore-route." + pin.service + ".checked") for pin in plan.services)
        proof = {"kind": PROOF, "services": projection, "all_homogeneous_source70": complete,
                 "forward_release_authorized": False, "acceptance_freshness_authorized": False,
                 "payment_activation_authorized": False, "replay_authorized": False}
        if complete:
            if journal.read("complete") is None:
                need(mode != "observe", "completion_not_recorded")
                journal.record("complete", proof)
            need(journal.read("complete") == proof | {"plan_sha256": plan.digest}, "completion_changed")
        return proof | {"native_mutations": mutations}


class NativeBoundary(native.NativeRollingBoundary):
    """Normal SDK transport, fixed service scope; no SQL/IAM/secret/model writer."""
    def __init__(self):
        super().__init__(ROOT)
        self._verified_source_pair = None

    def identity(self, email):
        need(email in {rolling.RUNTIME, owned.NEW_API_EMAIL}, "identity_scope")
        return identities.read_identity(self.observer, email)

    def verify_sources(self, app):
        # Native immutable Git Build/image and CI provenance. No document proof
        # or expired spend policy is converted into present acceptance authority.
        pair = (app.prior_source, app.source)
        verified = self._verified_source_pair
        if verified is not None:
            need(pair == verified, "source_pair_changed")
            return
        for pin in pair:
            mixed.app.successor._native_ci(self.observer, pin.release, pin.ci_run_id)
            self.observer._verify_source_image_provenance(release=pin.release,
                image_digest=pin.image_digest, build_id=pin.build_id, ci_run_id=pin.ci_run_id)
        # Publish only the complete successful pair, never a partial/failed
        # proof. Process-local reuse changes no live-state or receipt guards.
        self._verified_source_pair = pair

    def close(self):
        if self._run is not None:
            self._run.stock.session.close()


class VerifiedRestore:
    """Typed historical custody plus a fresh, initial homogeneous native check.

    Maintenance may verify custody throughout. verify_current is for its initial
    preimage only; after its first own mutation it must use its phase guards.
    """
    def __init__(self, plan, journal):
        need(type(plan) is RestorePlan and type(journal) is owned.Journal, "verified_restore_kind")
        self.plan, self.journal = plan, journal
        self.application_plan, self.profile = plan.application, plan.application.identities
        self.verify_custody()

    def verify_custody(self):
        _custody(self.plan)
        self.journal.verify()
        need(self.journal.plan_hash == self.plan.digest, "verified_restore_owner")
        complete = self.journal.read("complete")
        need(complete is not None and complete.get("kind") == PROOF
             and complete.get("all_homogeneous_source70") is True, "restore_incomplete")
        for pin in self.plan.services:
            stage_started, stage_ack, stage_checked = _record(self.plan, self.journal, pin, "restore-stage")
            _, _, checked = _record(self.plan, self.journal, pin, "restore-route")
            need(stage_started is not None and stage_ack is not None and stage_checked is not None
                 and stage_checked.get("health_verified") is True
                 and stage_checked.get("operation_ack_retained") is True
                 and stage_checked.get("replay_authorized") is False
                 and stage_checked.get("service_uid") == pin.before["service_uid"]
                 and stage_checked.get("generation") == pin.before["generation"] + 1
                 and stage_checked.get("traffic") == _stage_traffic(self.plan, pin)
                 and checked is not None and checked.get("health_verified") is True
                 and checked.get("operation_ack_retained") is True
                 and checked.get("replay_authorized") is False
                 and checked.get("service_uid") == pin.before["service_uid"]
                 and checked.get("generation") == pin.before["generation"] + 2
                 and checked.get("traffic") == list(pin.traffic)
                 and checked.get("prior_revision_uid") == pin.prior_revision_uid
                 and checked.get("staged_revision_uid") == pin.staged_revision_uid
                 and checked.get("revision") == pin.revision
                 and stage_checked.get("revision_uid") == checked.get("revision_uid")
                 and stage_checked.get("template_public_sha256") == checked.get("template_public_sha256")
                 and complete["services"].get(pin.service) == {key: value for key, value in checked.items()
                     if key not in {"health_verified", "operation_ack_retained", "replay_authorized", "plan_sha256"}},
                 "restore_checked_receipt")
        expected = {"kind": PROOF, "services": complete["services"], "all_homogeneous_source70": True,
                    "forward_release_authorized": False, "acceptance_freshness_authorized": False,
                    "payment_activation_authorized": False, "replay_authorized": False,
                    "plan_sha256": self.plan.digest}
        need(complete == expected, "restore_proof_scope")
        return complete

    def verify_current(self, clients=None):
        self.verify_custody()
        boundary = NativeBoundary() if clients is None else clients
        need(type(boundary) is NativeBoundary, "exact_native_boundary")
        original = _original(self.plan)
        try:
            with scope.lifecycle_lock(original), original.locked(), self.journal.locked():
                result = _verify_current(self.plan, boundary, self.journal)
                need(result == self.journal.read("complete")["services"], "restore_current_changed")
                return result
        finally:
            if clients is None:
                boundary.close()


def load_verified_restore(*, plan_path, plan_sha256, journal_path, profile):
    plan = load(_private_read(plan_path, plan_sha256), plan_sha256)
    need(type(profile) is identities.Profile and profile == plan.application.identities, "profile_changed")
    return VerifiedRestore(plan, owned.Journal(Path(journal_path), plan))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "restore", "reconcile", "observe"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--original-plan", type=Path)
    parser.add_argument("--original-plan-sha256")
    parser.add_argument("--nonce")
    args = parser.parse_args()
    boundary = None
    try:
        boundary = NativeBoundary()
        if args.mode == "capture":
            plan = capture(args.original_plan, args.original_plan_sha256, boundary,
                           nonce=args.nonce, journal_directory=args.journal)
            owned.Journal(args.journal, plan, create=True)
            digest = mixed.app.bridge._write_plan(args.plan, plan)
            result = {"status": "restore_only_captured", "plan_sha256": digest, "native_mutations": 0}
        else:
            plan = load(_private_read(args.plan, args.plan_sha256), args.plan_sha256)
            result = execute(args.mode, plan, boundary, owned.Journal(args.journal, plan))
        print(rolling.canonical(result).decode())
        return 0
    except Exception as error:  # noqa: BLE001 - emit only the fixed nonsecret diagnostic projection.
        print(rolling.canonical(mixed.diagnostics.diagnostic(error, args.mode)).decode())
        return 1
    finally:
        if boundary is not None:
            boundary.close()


if __name__ == "__main__":
    raise SystemExit(main())
