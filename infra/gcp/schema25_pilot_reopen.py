"""Explicit, journaled same-image reopening of the previously running pilot.

This is separate from schema25 deployment. It may reopen only a finite subset of
non-payment, non-submission flags that were true in the exact pre-maintenance
revision. It never opens unified credits, free-core processing, checkout or final
application submission. All provider writes require a private captured plan.
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
from types import SimpleNamespace
from typing import Any, cast

import document_refresh_probe as document_probe
import rolling_native as native
import rolling_release as rolling
import schema25_coordinator_pause as coordinator
import schema25_maintenance as maintenance
import unified_credit_schema25_release as schema25

SERVICES = rolling.release.SERVICES
# Existing candidate-reviewed pilot routes only. Paid checkout and every native
# final-submission flag are deliberately absent. Individual flags are still
# explicitly requested, and a true pre-maintenance value is required.
PILOT_FLAGS = frozenset({
    "RESUME_DIRECT_UPLOAD_ENABLED",
    "EMPLOYER_DISCOVERY_ENABLED",
    "BROWSER_ASSISTANCE_ENABLED",
    "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED",
    "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED",
    "BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED",
    "BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED",
    "BROWSER_PAIRING_NATIVE_ENABLED",
    "CANDIDATE_INGRESS_NATIVE_ENABLED",
})
FORBIDDEN = frozenset(schema25.OFF) - PILOT_FLAGS
assert not PILOT_FLAGS.intersection({
    "RAZORPAY_CHECKOUT_ENABLED", "EMPLOYER_AUTO_SUBMIT_ENABLED",
    "BROWSER_ASSISTANCE_SUBMIT_ENABLED", "BROWSER_SUBMIT_NATIVE_ENABLED",
    *schema25.contract.DISABLED_FLAGS,
})


def need(value, reason):
    rolling.need(value, "schema25_pilot_" + reason)


@dataclass(frozen=True)
class PilotPin:
    service: str
    uid: str
    generation: int
    etag: str
    template_sha256: str
    template_keys: tuple[str, ...]
    static_sha256: str
    traffic: tuple[dict, ...]
    active_revision: str
    active_tag: str
    reopen_flags: tuple[str, ...]


@dataclass(frozen=True)
class PilotPlan:
    nonce: str
    deployment_sha256: str
    maintenance_sha256: str
    coordinator_sha256: str
    source_release: str
    image: str
    requested_flags: tuple[str, ...]
    services: tuple[PilotPin, ...]

    def validate(self):
        need(bool(re.fullmatch(r"[a-f0-9]{32}", self.nonce))
             and all(rolling.HASH.fullmatch(value) for value in (
                 self.deployment_sha256, self.maintenance_sha256, self.coordinator_sha256))
             and bool(re.fullmatch(r"[a-f0-9]{40}", self.source_release))
             and bool(re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", self.image))
             and self.requested_flags == tuple(sorted(set(self.requested_flags)))
             and bool(self.requested_flags)
             and set(self.requested_flags) <= PILOT_FLAGS
             and tuple(pin.service for pin in self.services) == SERVICES,
             "plan_invalid")
        need(any(pin.reopen_flags for pin in self.services), "no_prior_true_flags")
        for pin in self.services:
            need(bool(re.fullmatch(r"[a-f0-9-]{36}", pin.uid))
                 and type(pin.generation) is int and pin.generation > 0
                 and bool(pin.etag)
                 and all(rolling.HASH.fullmatch(value) for value in (
                     pin.template_sha256, pin.static_sha256))
                 and 1 <= len(pin.template_keys) <= 40
                 and len(set(pin.template_keys)) == len(pin.template_keys)
                 and "revision" not in pin.template_keys
                 and pin.reopen_flags == tuple(sorted(set(pin.reopen_flags)))
                 and set(pin.reopen_flags) <= set(self.requested_flags)
                 and bool(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", pin.active_tag))
                 and rolling.short_revision(pin.active_revision, pin.service) == pin.active_revision,
                 "service_pin_invalid")
            traffic = rolling.pinned_traffic(pin.service, {"trafficStatuses": list(pin.traffic)},
                                             prior_revision=pin.active_revision)
            need(traffic == list(pin.traffic), "traffic_pin_invalid")

    def revision(self, service):
        need(service in SERVICES, "service_refused")
        return service + "-p25-" + self.nonce[:12]


def _original_flags(run, maintenance_plan, service):
    pin = next(item for item in maintenance_plan.services if item.name == service)
    try:
        revision = maintenance.pinned_revision(
            pin, run, pin.prior_revision, dict(pin.revisions)[pin.prior_revision])
    except rolling.RollingDenied:
        raise rolling.RollingDenied("schema25_pilot_premaintenance_revision_changed") from None
    # The capture tied this revision to the original serving template, image,
    # source, account and traffic. Recheck its exact full template hash here.
    template = {key: revision.get(key) for key in pin.template_keys}
    need(coordinator.template_hash(template) == pin.template_sha256
         and revision["containers"][0].get("image") == pin.image
         and rolling.environment(revision).get("APP_RELEASE", {}).get("value") == pin.source,
         "premaintenance_template_changed")
    return rolling.environment(revision)


def _verify_reopen_flags(plan, maintenance_plan, run):
    """Recompute every requested value from the pinned pre-maintenance revision.

    The private reopen plan is an instruction, not evidence that a route used to
    be open. This check runs on every advance and reconcile before any PATCH.
    """
    need(rolling.sha(asdict(maintenance_plan)) == plan.maintenance_sha256,
         "maintenance_plan_changed")
    for pin in plan.services:
        prior = _original_flags(run, maintenance_plan, pin.service)
        need(all(prior.get(flag, {"name": flag, "value": "false"}).get("value")
                 in {"true", "false"} for flag in plan.requested_flags),
             "prior_flag_invalid")
        expected = tuple(flag for flag in plan.requested_flags
                         if prior.get(flag) == {"name": flag, "value": "true"})
        need(pin.reopen_flags == expected, "reopen_flags_not_prior_true")


def _restored(maintenance_plan, maintenance_journal, coordinator_plan, coordinator_journal,
              controls, coordinator_run):
    for pin in maintenance_plan.resources:
        name = pin.kind + "." + pin.name
        need(maintenance_journal.read(name + ".pause.checked") is not None
             and maintenance_journal.read(name + ".restore.checked") is not None,
             "resources_not_restored")
        observed = controls.get(pin.kind, pin.name)
        need(observed.get("state") == pin.state
             and maintenance.configuration(observed) == pin.configuration_sha256,
             "resource_changed")
    started = coordinator_journal.read("coordinator.restore.started")
    need(started is not None
         and coordinator_journal.read("coordinator.restore.checked") is not None,
         "coordinator_not_restored")
    coordinator.readback(coordinator_plan, coordinator_run, "restore", started)


def _deployed(deployment_plan, deployment_journal, boundary):
    schema25._release_phase(deployment_plan, boundary, deployment_journal)
    need(deployment_plan.database_schema == schema25.contract.SCHEMA,
         "schema25_required")
    for service in SERVICES:
        need(deployment_journal.read("rollback." + service + ".started") is None,
             "rollback_started")
        started = deployment_journal.read("promote." + service + ".started")
        need(started is not None
             and deployment_journal.read("promote." + service + ".checked") is not None,
             "deployment_not_complete")
        rolling._readback(deployment_plan, boundary, deployment_plan.pin(service), started)
    boundary.observer.verify_source_image(
        release=deployment_plan.release, image_digest=deployment_plan.image_digest,
        build_id=deployment_plan.build_id, ci_run_id=deployment_plan.ci_run_id)
    boundary.observer.verify_policy_configuration(deployment_plan.target_policies())


def _coordinator_revision(clients, plan, name):
    expected = dict(plan.prior_revisions)
    need(name in expected, "document_coordinator_prior_changed")
    reader = SimpleNamespace(revision=lambda service, short: clients.revision(
        document_probe.PARENT + "/services/" + service + "/revisions/" + short))
    try:
        return coordinator.pinned_revision(plan, reader, name, expected[name])
    except rolling.RollingDenied:
        raise rolling.RollingDenied("schema25_pilot_document_coordinator_prior_changed") from None


def _schema25_caller(clients, name, uid, image, source, latest, traffic,
                     template_sha, template_keys, pins, *, static_sha=None, coordinator_static=False,
                     coordinator_plan=None):
    """Read-only caller proof for an exact tagged schema25 serving revision.

    The scanner's generic no-tag validator remains unchanged. Schema25 leaves
    a rollout tag on API/worker traffic, while coordinator restore can leave
    its prior revision serving under a newer, identical restore template.
    """
    resource = document_probe.PARENT + "/services/" + name
    current = clients.service(resource)
    expected_created = resource + "/revisions/" + latest
    ready_revisions = {expected_created}
    if coordinator_plan is not None:
        need(coordinator_static and name == coordinator.SERVICE,
             "document_coordinator_scope_changed")
        paused = coordinator_plan.revision_name("pause")
        need(paused not in {row["revision"] for row in traffic},
             "document_coordinator_pause_serving")
        ready_revisions.add(resource + "/revisions/" + paused)
    need(current.get("name") == resource and current.get("uid") == uid
         and type(current.get("etag")) is str and bool(current["etag"])
         and str(current.get("generation")) == str(current.get("observedGeneration"))
         and current.get("reconciling", False) is False
         and current.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
         and current.get("latestReadyRevision") in ready_revisions
         and current.get("latestCreatedRevision") == expected_created
         and rolling.pinned_traffic(name, current) == traffic,
         "document_caller_serving_changed")
    if static_sha is not None:
        observed_static = (coordinator.static_hash(current) if coordinator_static
                           else rolling.static_sha(current))
        need(observed_static == static_sha, "document_caller_static_changed")
    template = current.get("template", {})
    observed_template = (coordinator.template_hash(template) if coordinator_static
                         else rolling.template_sha(template, name))
    need(observed_template == template_sha, "document_caller_template_changed")
    active = next(row["revision"] for row in traffic if row.get("percent") == 100)
    for revision_name in sorted({latest, active}):
        full = resource + "/revisions/" + revision_name
        revision = (_coordinator_revision(clients, coordinator_plan, revision_name)
                    if revision_name != latest and coordinator_plan is not None
                    else clients.revision(full))
        ready = revision.get("conditions", [])
        need(revision.get("name") == full
             and revision.get("service") in (resource, name)
             and type(revision.get("uid")) is str
             and document_probe.UUID.fullmatch(revision["uid"])
             and type(ready) is list
             and len([row for row in ready if row.get("type") == "Ready"
                      and row.get("state") == "CONDITION_SUCCEEDED"]) == 1,
             "document_caller_revision_changed")
        if revision_name == latest:
            revision_template = {key: revision.get(key) for key in template_keys}
            revision_template["revision"] = revision_name
            digest = (coordinator.template_hash(revision_template) if coordinator_static
                      else rolling.template_sha(revision_template, name))
            need(digest == template_sha, "document_caller_revision_changed")
        else:
            need(coordinator_plan is not None,
                 "document_caller_revision_changed")
        for row in (revision, template if revision_name == latest else {}):
            if not row:
                continue
            env = rolling.environment(row)
            need(row["containers"][0].get("image") == image
                 and env.get("APP_RELEASE", {}).get("value") == source
                 and all(env.get(key) == {"name": key, "value": value}
                         for key, value in pins.items()),
                 "document_caller_pins_changed")
    need(clients.service(resource) == current, "document_caller_serving_changed")


def verify_document_pins(plan, pilot_journal, coordinator_plan):
    """Require fresh native worker acceptance and exact schema25 caller pins."""
    clients = document_probe.Clients(read_only_release=True)
    try:
        worker = clients.service(document_probe.PARENT + "/services/hirewiz-document-inspector")
        image = worker.get("template", {}).get("containers", [{}])[0].get("image")
        need(type(image) is str and bool(document_probe.IMAGE.fullmatch(image)),
             "document_image_invalid")
        accepted = document_probe.verify_current_successor_for_release(image, worker)
        need(accepted.image == image, "document_acceptance_image_changed")
        pins = {"DOCUMENT_WORKER_URL": document_probe.WORKER_URL,
                "DOCUMENT_WORKER_IMAGE_DIGEST": image,
                "DOCUMENT_WORKER_POLICY_SHA256": accepted.policy_sha256,
                "DOCUMENT_WORKER_DOCX_RENDERER_VERSION": accepted.renderer_version,
                "DOCUMENT_WORKER_DOCX_FONT_MANIFEST_SHA256": accepted.font_manifest_sha256}
        for name in SERVICES[:2]:
            pin = next(item for item in plan.services if item.service == name)
            resource = document_probe.PARENT + "/services/" + name
            prior = clients.revision(resource + "/revisions/" + pin.active_revision)
            need(prior.get("name") == resource + "/revisions/" + pin.active_revision
                 and prior.get("service") in (resource, name)
                 and type(prior.get("uid")) is str
                 and document_probe.UUID.fullmatch(prior["uid"])
                 and len([row for row in prior.get("conditions", [])
                          if row.get("type") == "Ready"
                          and row.get("state") == "CONDITION_SUCCEEDED"]) == 1,
                 "document_candidate_revision_changed")
            original = {key: prior.get(key) for key in pin.template_keys}
            original["revision"] = pin.active_revision
            need(rolling.template_sha(original, name) == pin.template_sha256,
                 "document_candidate_revision_changed")
            reopened, reopened_traffic = desired(plan, pin, {"template": original}) if pin.reopen_flags else (None, None)
            candidate = (pin.active_revision, list(pin.traffic), pin.template_sha256)
            marker = "pilot." + name
            started = pilot_journal.read(marker + ".started")
            checked = pilot_journal.read(marker + ".checked")
            choices = [] if checked is not None else [candidate]
            if started is not None and reopened is not None and reopened_traffic is not None:
                choices.append((plan.revision(name), reopened_traffic,
                                rolling.template_sha(reopened, name)))
            need(bool(choices), "document_caller_state_changed")
            current = clients.service(resource)
            observed = (rolling.short_revision(current.get("latestReadyRevision", ""), name),
                        rolling.pinned_traffic(name, current))
            matching = [choice for choice in choices if choice[:2] == observed]
            need(len(matching) == 1, "document_caller_state_changed")
            latest, traffic, template_sha = matching[0]
            _schema25_caller(clients, name, pin.uid, plan.image, plan.source_release,
                             latest, traffic, template_sha, pin.template_keys, pins,
                             static_sha=pin.static_sha256)
        name = coordinator.SERVICE
        resource = document_probe.PARENT + "/services/" + name
        prior = _coordinator_revision(clients, coordinator_plan, coordinator_plan.prior_revision)
        original = {key: prior.get(key) for key in coordinator_plan.template_keys}
        need(coordinator.template_hash(original) == coordinator_plan.template_sha256,
             "document_coordinator_prior_changed")
        _schema25_caller(clients, name, coordinator_plan.uid, coordinator_plan.image,
                         coordinator_plan.source_release,
                         coordinator_plan.revision_name("restore"),
                         list(coordinator_plan.original_traffic),
                         coordinator_plan.template_sha256,
                         coordinator_plan.template_keys, pins,
                         static_sha=coordinator_plan.static_sha256,
                         coordinator_static=True,
                         coordinator_plan=coordinator_plan)
    finally:
        clients.close()


def capture(deployment_plan, deployment_journal, maintenance_plan, maintenance_journal,
            coordinator_plan, coordinator_journal, boundary, controls, coordinator_run,
            requested_flags, nonce):
    requested = tuple(sorted(set(requested_flags)))
    need(bool(requested) and set(requested) <= PILOT_FLAGS
         and len(requested) == len(requested_flags), "requested_flags_refused")
    _restored(maintenance_plan, maintenance_journal, coordinator_plan,
              coordinator_journal, controls, coordinator_run)
    _deployed(deployment_plan, deployment_journal, boundary)
    pins = []
    for service in SERVICES:
        current = boundary.service(service)
        source = deployment_plan.pin(service)
        prior = next(item for item in maintenance_plan.services if item.name == service)
        need(prior.uid == source.uid and prior.source == source.prior_release
             and prior.image == boundary.revision(service, source.prior_revision)["containers"][0]["image"],
             "premaintenance_deployment_mismatch")
        env, original = rolling.environment(current["template"]), _original_flags(
            boundary, maintenance_plan, service)
        need(current["template"]["containers"][0].get("image") == deployment_plan.image
             and env.get("APP_RELEASE", {}).get("value") == deployment_plan.release
             and all(env.get(flag, {"value": "false"}).get("value") == "false"
                     for flag in schema25.OFF), "candidate_not_closed")
        flags = tuple(flag for flag in requested
                      if original.get(flag) == {"name": flag, "value": "true"})
        for flag in requested:
            need(original.get(flag, {"name": flag, "value": "false"}).get("value")
                 in {"true", "false"}, "prior_flag_invalid")
        active = next(item for item in rolling.pinned_traffic(service, current)
                      if item.get("percent") == 100)
        need(active["revision"] == deployment_plan.revision(service),
             "candidate_not_serving")
        tag = active.get("tag", "pilot-" + nonce[:12])
        pins.append(PilotPin(service, current["uid"], int(current["generation"]),
            current["etag"], rolling.template_sha(current["template"], service),
            tuple(sorted(key for key in current["template"] if key != "revision")),
            rolling.static_sha(current), tuple(rolling.pinned_traffic(service, current)),
            active["revision"], tag, flags))
    need(all(flag in {value for pin in pins for value in pin.reopen_flags}
             for flag in requested), "requested_flag_was_not_running")
    plan = PilotPlan(nonce, rolling.sha(asdict(deployment_plan)),
        rolling.sha(asdict(maintenance_plan)), rolling.sha(asdict(coordinator_plan)),
        deployment_plan.release, deployment_plan.image, requested, tuple(pins))
    plan.validate()
    need(all(boundary.revision(pin.service, plan.revision(pin.service)) is None
             for pin in plan.services if pin.reopen_flags), "reopen_revision_exists")
    return plan


def desired(plan, pin, current):
    need(pin.reopen_flags, "empty_service_refused")
    template = copy.deepcopy(current["template"])
    env = rolling.environment(template)
    need(all(env.get(flag, {"value": "false"}).get("value") == "false"
             for flag in schema25.OFF), "candidate_not_closed")
    for flag in pin.reopen_flags:
        env[flag] = {"name": flag, "value": "true"}
    template["containers"][0]["env"] = list(env.values())
    template["revision"] = plan.revision(pin.service)
    traffic = copy.deepcopy(list(pin.traffic))
    for item in traffic:
        if item.get("percent") == 100:
            item["revision"] = plan.revision(pin.service)
            item["tag"] = pin.active_tag
    traffic = sorted(traffic, key=rolling.canonical)
    need(rolling.pinned_traffic(pin.service, {"trafficStatuses": traffic},
                                prior_revision=plan.revision(pin.service)) == traffic,
         "desired_traffic_invalid")
    return template, traffic


def _readback(plan, pin, run, started, health):
    current = run.service(pin.service)
    need(current.get("uid") == pin.uid
         and int(current.get("generation", -1)) == started["prior_generation"] + 1
         and rolling.static_sha(current) == pin.static_sha256
         and current["template"]["containers"][0].get("image") == plan.image
         and rolling.environment(current["template"]).get("APP_RELEASE", {}).get("value")
         == plan.source_release, "readback_changed")
    prior = run.revision(pin.service, pin.active_revision)
    need(prior is not None, "prior_revision_missing")
    prior_template = {key: prior.get(key) for key in pin.template_keys}
    prior_template["revision"] = pin.active_revision
    need(rolling.template_sha(prior_template, pin.service) == pin.template_sha256,
         "prior_revision_changed")
    template, traffic = desired(plan, pin, {"template": prior_template})
    need(rolling.template_sha(current["template"], pin.service)
         == rolling.template_sha(template, pin.service)
         and rolling.pinned_traffic(pin.service, current) == traffic
         and rolling.short_revision(current["latestReadyRevision"], pin.service)
         == plan.revision(pin.service), "readback_changed")
    revision = run.revision(pin.service, plan.revision(pin.service))
    need(revision is not None
         and any(row.get("type") == "Ready" and row.get("state") == "CONDITION_SUCCEEDED"
                 for row in revision.get("conditions", [])), "reopen_revision_not_ready")
    env = rolling.environment(revision)
    need(revision["containers"][0].get("image") == plan.image
         and all(env.get(flag) == {"name": flag, "value": "false"}
                 for flag in FORBIDDEN if flag in env), "forbidden_flag_opened")
    health(plan.source_release, pin.service, plan.revision(pin.service), pin.active_tag)
    return current


def advance(plan, journal, run, health, gate, maintenance_plan, *, reconcile=False):
    """Workers before API; never resend a PATCH after an uncertain outcome."""
    plan.validate()
    need(journal.plan_hash == rolling.sha(asdict(plan)), "journal_mismatch")
    with journal.locked():
        gate(not any(journal.read("pilot." + service + ".started")
                     for service in SERVICES))
        _verify_reopen_flags(plan, maintenance_plan, run)
        for service in (*SERVICES[1:], SERVICES[0]):
            pin = next(item for item in plan.services if item.service == service)
            if not pin.reopen_flags:
                continue
            marker = "pilot." + service
            started = journal.read(marker + ".started")
            ack = journal.read(marker + ".ack")
            if journal.read(marker + ".checked") is not None:
                need(started is not None, "checked_without_start")
                _readback(plan, pin, run, started, health)
                continue
            if started is None:
                if reconcile:
                    return {"status": "pilot_phase_partial", "resume_required": True,
                            "replay_authorized": False}
                current = run.service(service)
                need(current.get("uid") == pin.uid
                     and int(current.get("generation", -1)) == pin.generation
                     and current.get("etag") == pin.etag
                     and rolling.static_sha(current) == pin.static_sha256
                     and rolling.template_sha(current["template"], service)
                     == pin.template_sha256
                     and rolling.pinned_traffic(service, current) == list(pin.traffic)
                     and current["template"]["containers"][0].get("image") == plan.image
                     and rolling.environment(current["template"]).get("APP_RELEASE", {}).get("value")
                     == plan.source_release, "preimage_changed")
                template, traffic = desired(plan, pin, current)
                need(run.revision(service, plan.revision(service)) is None
                     and run.service(service) == current, "preimage_changed")
                body = {"name": current["name"], "etag": current["etag"],
                        "template": template, "traffic": traffic}
                journal.record(marker + ".started", {"prior_generation": pin.generation,
                    "request_sha256": rolling.sha(body), "replay_authorized": False})
                started = journal.read(marker + ".started")
                operation = run.patch(service, body, "template,traffic")
                need(rolling.OPERATION.fullmatch(operation.get("name", "")),
                     "unknown_acknowledgement")
                journal.record(marker + ".ack", {"operation": operation["name"]})
                ack = journal.read(marker + ".ack")
            elif ack is None:
                need(reconcile, "unknown_write_reconcile_only")
            if ack is not None:
                observed = run.operation(ack["operation"])
                need(observed.get("name") == ack["operation"] and "error" not in observed,
                     "operation_failed_reconcile_only")
                if observed.get("done") is not True:
                    return {"status": "pilot_operation_pending", "service": service,
                            "replay_authorized": False}
            current = _readback(plan, pin, run, started, health)
            journal.record(marker + ".checked", {
                "generation": int(current["generation"]),
                "revision": plan.revision(service), "replay_authorized": False})
    return {"status": "pilot_reopen_observed", "replay_authorized": False,
            "checkout_enabled": False, "final_submission_enabled": False}


def load(raw):
    value = rolling.release._json(raw)
    pins = []
    for item in value["services"]:
        item["traffic"] = tuple(item["traffic"])
        item["template_keys"] = tuple(item["template_keys"])
        item["reopen_flags"] = tuple(item["reopen_flags"])
        pins.append(PilotPin(**item))
    value["services"] = tuple(pins)
    value["requested_flags"] = tuple(value["requested_flags"])
    plan = PilotPlan(**value)
    plan.validate()
    return plan


def _pinned(path, sha, loader):
    return loader(schema25._read_pinned(path, sha))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "advance", "reconcile"))
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--nonce")
    parser.add_argument("--flags", help="Explicit comma-separated subset of the reviewed pilot flags")
    for name in ("deployment", "maintenance", "coordinator"):
        parser.add_argument("--" + name + "-plan", type=Path)
        parser.add_argument("--" + name + "-sha256")
        parser.add_argument("--" + name + "-journal", type=Path)
    args = parser.parse_args()
    if args.phase == "capture":
        need(args.nonce is not None and args.flags is not None
             and all(getattr(args, name + suffix) is not None
                     for name in ("deployment", "maintenance", "coordinator")
                     for suffix in ("_plan", "_sha256", "_journal"))
             and args.plan.is_absolute() and args.plan.parent.resolve() == args.plan.parent
             and args.plan.parent.stat().st_uid == os.getuid()
             and args.plan.parent.stat().st_mode & 0o077 == 0,
             "capture_arguments_invalid")
        deployment = _pinned(args.deployment_plan, args.deployment_sha256, schema25.load_plan)
        maintenance_plan = _pinned(args.maintenance_plan, args.maintenance_sha256,
                                   maintenance.load)
        coordinator_plan = _pinned(args.coordinator_plan, args.coordinator_sha256,
                                   coordinator.load_plan)
        deployment_journal = rolling.Journal(args.deployment_journal, deployment)
        maintenance_journal = rolling.Journal(args.maintenance_journal, maintenance_plan)
        coordinator_journal = rolling.Journal(args.coordinator_journal, coordinator_plan)
        boundary = schema25.NativeSchema25Boundary(Path(__file__).resolve().parents[2],
                                                    schema25.contract.SCHEMA,
                                                    deployment.prior_sources)
        plan = capture(deployment, deployment_journal, maintenance_plan,
            maintenance_journal, coordinator_plan, coordinator_journal, boundary,
            maintenance.NativeControls(), coordinator.NativeCoordinatorRun(),
            tuple(args.flags.split(",")), args.nonce)
        raw = rolling.canonical(asdict(plan)) + b"\n"
        fd = os.open(args.plan, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        print(json.dumps({"status": "pilot_reopen_plan_captured",
                          "plan_sha256": hashlib.sha256(raw).hexdigest(),
                          "provider_mutations": 0}))
        return
    plan = _pinned(args.plan, args.plan_sha256, load)
    need(args.journal is not None, "journal_required")
    need(all(getattr(args, name + suffix) is not None
             for name in ("deployment", "maintenance", "coordinator")
             for suffix in ("_plan", "_sha256", "_journal")),
         "reopen_proof_arguments_required")
    deployment = _pinned(args.deployment_plan, args.deployment_sha256, schema25.load_plan)
    maintenance_plan = _pinned(args.maintenance_plan, args.maintenance_sha256,
                               maintenance.load)
    coordinator_plan = _pinned(args.coordinator_plan, args.coordinator_sha256,
                               coordinator.load_plan)
    need(rolling.sha(asdict(deployment)) == plan.deployment_sha256
         and rolling.sha(asdict(maintenance_plan)) == plan.maintenance_sha256
         and rolling.sha(asdict(coordinator_plan)) == plan.coordinator_sha256,
         "reopen_proof_changed")
    deployment_journal = rolling.Journal(args.deployment_journal, deployment)
    maintenance_journal = rolling.Journal(args.maintenance_journal, maintenance_plan)
    coordinator_journal = rolling.Journal(args.coordinator_journal, coordinator_plan)
    journal = rolling.Journal(args.journal, plan)
    run = native.NativeRun()
    boundary = schema25.NativeSchema25Boundary(Path(__file__).resolve().parents[2],
                                                schema25.contract.SCHEMA)
    def health(release_id, service, revision, tag):
        boundary.observer._verify_http_health(cast(Any, SimpleNamespace(release=release_id)),
                                              service, revision, candidate_tag=tag)
    def gate(first):
        _restored(maintenance_plan, maintenance_journal, coordinator_plan,
                  coordinator_journal, maintenance.NativeControls(),
                  coordinator.NativeCoordinatorRun())
        need(all(rolling.sha(boundary.iam(service))
                 == deployment.pin(service).iam_sha256 for service in SERVICES),
             "service_iam_changed")
        if first:
            _deployed(deployment, deployment_journal, boundary)
        else:
            schema25._release_phase(deployment, boundary, deployment_journal)
            need(not any(deployment_journal.read("rollback." + service + ".started")
                         for service in SERVICES), "rollback_started")
            need(all(deployment_journal.read("promote." + service + ".checked") is not None
                     for service in SERVICES), "deployment_not_complete")
            boundary.observer.verify_source_image(
                release=deployment.release, image_digest=deployment.image_digest,
                build_id=deployment.build_id, ci_run_id=deployment.ci_run_id)
            boundary.observer.verify_policy_configuration(deployment.target_policies())
        if set(plan.requested_flags) & {
            "RESUME_DIRECT_UPLOAD_ENABLED", "BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED"
        }:
            verify_document_pins(plan, journal, coordinator_plan)
    print(json.dumps(advance(plan, journal, run, health, gate, maintenance_plan,
                             reconcile=args.phase == "reconcile")))


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        if isinstance(error, SystemExit):
            raise
        reason = "unclassified_failure"
        if type(error) is rolling.RollingDenied and len(error.args) == 1 \
                and re.fullmatch(r"schema25_pilot_[a-z_]{1,90}", str(error.args[0])):
            reason = error.args[0]
        print(json.dumps({"status": "pilot_reopen_stopped_reconcile_only",
                          "code": reason, "replay_authorized": False}))
        raise SystemExit(2) from None
