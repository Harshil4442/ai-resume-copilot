"""Separate same-image coordinator pause/restore; no changes to the rolling plan.

The shared native transport still sends each armed PATCH at most once. The
existing private journal records metadata/hashes, never environment contents.
No provider action occurs on import. Pausing closes new scan and cleanup calls;
the migration's independent SQL lease/drain checks remain required.
"""

from __future__ import annotations

import copy
import re
from dataclasses import asdict, dataclass
from typing import Any

import rolling_native as native
import rolling_release as rolling

SERVICE = "hirewiz-resume-coordinator"
RESOURCE = rolling.PARENT + "/services/" + SERVICE
ACCOUNT = SERVICE + "@" + rolling.release.PROJECT + ".iam.gserviceaccount.com"
FLAG = "RESUME_UPLOAD_COORDINATOR_ENABLED"
REVISION_READ_OUTPUTS = frozenset({
    "name", "service", "uid", "generation", "observedGeneration", "createTime",
    "updateTime", "creator", "etag", "conditions", "scalingStatus",
    "launchStage", "logUri",
})


class NativeCoordinatorRun(native.NativeRun):
    """Reuse the stock one-send SDK adapter with a different fixed singleton scope."""

    SERVICES = (SERVICE,)  # Intentional narrower native write scope.


def need(value, code):
    rolling.need(value, "schema25_coordinator_" + code)


def template_hash(template):
    return rolling.sha({key: value for key, value in template.items() if key != "revision"})


def static_hash(service):
    return rolling.sha({key: value for key, value in service.items()
                        if key not in rolling.OUTPUTS | {"urls"}})


def ready(service):
    need(service.get("name") == RESOURCE and service.get("reconciling", False) is False
         and int(service.get("generation", -1)) == int(service.get("observedGeneration", -2))
         and service.get("terminalCondition", {}).get("state") == "CONDITION_SUCCEEDED"
         and type(service.get("etag")) is str and bool(service["etag"]), "service_not_ready")
    rolling.short_revision(service.get("latestCreatedRevision", ""), SERVICE)
    rolling.short_revision(service.get("latestReadyRevision", ""), SERVICE)
    template = service["template"]
    env = rolling.environment(template)
    image = template["containers"][0].get("image", "")
    source = env.get("APP_RELEASE", {}).get("value", "")
    need(template.get("serviceAccount") == ACCOUNT
         and env.get("SERVICE_ROLE", {}).get("value") == "resume-upload-coordinator"
         and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", image)
         and re.fullmatch(r"[a-f0-9]{40}", source), "source_or_identity_invalid")
    return image, source


def revision(reader, name):
    short = rolling.short_revision(name, SERVICE)
    row = reader.revision(SERVICE, short)
    need(row is not None and row.get("name") == RESOURCE + "/revisions/" + short
         and any(value.get("type") == "Ready" and value.get("state") == "CONDITION_SUCCEEDED"
                 for value in row.get("conditions", [])), "revision_not_ready")
    return row


def observe_closed(reader):
    """Freshly check the template and every reachable active/tagged revision."""
    service = reader.service(SERVICE)
    image, source = ready(service)
    rows = [service["template"]]
    traffic = rolling.pinned_traffic(SERVICE, service)
    targets = {row["revision"] for row in traffic}
    created = rolling.short_revision(service["latestCreatedRevision"], SERVICE)
    latest_ready = rolling.short_revision(service["latestReadyRevision"], SERVICE)
    need(latest_ready == created or latest_ready not in targets, "stale_ready_revision_serving")
    targets.add(created)
    for name in sorted(targets):
        rows.append(revision(reader, name))
    for row in rows:
        env = rolling.environment(row)
        need(env.get(FLAG) == {"name": FLAG, "value": "false"}
             and row.get("serviceAccount") == ACCOUNT
             and env.get("SERVICE_ROLE", {}).get("value") == "resume-upload-coordinator"
             and env.get("APP_RELEASE", {}).get("value") == source
             and row["containers"][0].get("image") == image, "admission_not_closed")
    need(reader.service(SERVICE) == service, "observation_changed")
    return {"service_uid": service["uid"], "generation": int(service["generation"]),
            "revision": service["latestCreatedRevision"], "admission": "closed",
            "inflight_invocations_proven_drained": False}


@dataclass(frozen=True)
class CoordinatorPausePlan:
    nonce: str
    uid: str
    generation: int
    service_sha256: str
    static_sha256: str
    template_sha256: str
    template_keys: tuple[str, ...]
    source_release: str
    image: str
    original_flag: dict[str, str] | None
    original_traffic: tuple[dict[str, Any], ...]
    prior_revision: str
    prior_revisions: tuple[tuple[str, str], ...]

    def validate(self):
        need(re.fullmatch(r"[a-f0-9]{32}", self.nonce)
             and re.fullmatch(r"[a-f0-9-]{36}", self.uid)
             and type(self.generation) is int and self.generation > 0
             and all(rolling.HASH.fullmatch(value) for value in (
                 self.service_sha256, self.static_sha256, self.template_sha256))
             and re.fullmatch(r"[a-f0-9]{40}", self.source_release)
             and re.fullmatch(r"[A-Za-z0-9._/:-]+@sha256:[a-f0-9]{64}", self.image)
             and 1 <= len(self.template_keys) <= 40 and len(set(self.template_keys)) == len(self.template_keys)
             and self.original_flag in (None, {"name": FLAG, "value": "true"}, {"name": FLAG, "value": "false"}),
             "plan_invalid")
        need(1 <= len(self.prior_revisions) <= 21 and len(dict(self.prior_revisions)) == len(self.prior_revisions)
             and all(rolling.HASH.fullmatch(value) for _, value in self.prior_revisions), "plan_invalid")
        traffic = rolling.pinned_traffic(SERVICE, {"trafficStatuses": list(self.original_traffic)})
        need(traffic == list(self.original_traffic)
             and {item["revision"] for item in traffic} | {self.prior_revision} == set(dict(self.prior_revisions)),
             "plan_invalid")
        for name, _digest in self.prior_revisions:
            need(rolling.short_revision(name, SERVICE) == name, "plan_invalid")

    def revision_name(self, phase):
        need(phase in {"pause", "restore"}, "phase_invalid")
        return SERVICE + "-c25-" + self.nonce[:12] + "-" + phase


def capture(reader, nonce):
    service = reader.service(SERVICE)
    image, source = ready(service)
    traffic = rolling.pinned_traffic(SERVICE, service)
    prior = rolling.short_revision(service["latestCreatedRevision"], SERVICE)
    latest_ready = rolling.short_revision(service["latestReadyRevision"], SERVICE)
    need(latest_ready == prior or latest_ready not in {row["revision"] for row in traffic},
         "stale_ready_revision_serving")
    rows = {name: revision(reader, name) for name in {r["revision"] for r in traffic} | {prior}}
    # A newer idle revision must never replace the actually serving image during
    # a pause. Every reachable source is pinned and must match before any write.
    for row in rows.values():
        env = rolling.environment(row)
        need(row.get("serviceAccount") == ACCOUNT
             and env.get("SERVICE_ROLE", {}).get("value") == "resume-upload-coordinator"
             and env.get("APP_RELEASE", {}).get("value") == source
             and row["containers"][0].get("image") == image, "reachable_source_changed")
    keys = tuple(sorted(key for key in service["template"] if key != "revision"))
    need(all({key: row.get(key) for key in keys}
             == {key: service["template"][key] for key in keys} for row in rows.values()),
         "reachable_configuration_differs")
    need({key: rows[prior].get(key) for key in keys}
         == {key: service["template"][key] for key in keys}, "serving_template_changed")
    plan = CoordinatorPausePlan(nonce, service["uid"], int(service["generation"]), rolling.sha(service),
        static_hash(service), template_hash(service["template"]), keys, source, image,
        rolling.environment(service["template"]).get(FLAG), tuple(traffic), prior,
        tuple(sorted((name, rolling.sha(row)) for name, row in rows.items())))
    plan.validate()
    need(reader.service(SERVICE) == service, "observation_changed")
    return plan


def pinned_revision(plan, reader, name, expected):
    row = revision(reader, name)
    if rolling.sha(row) != expected:
        # Revision runtime configuration is immutable; provider status/etag can
        # settle after it becomes unserved. Accept only bounded output fields
        # with the exact independently captured source and template.
        env = rolling.environment(row)
        need(set(plan.template_keys) <= set(row)
             and set(row) <= set(plan.template_keys) | REVISION_READ_OUTPUTS
             and row.get("service") in (SERVICE, RESOURCE)
             and type(row.get("uid")) is str
             and re.fullmatch(r"[a-f0-9-]{36}", row["uid"])
             and str(row.get("generation")) == str(row.get("observedGeneration"))
             and row.get("serviceAccount") == ACCOUNT
             and row["containers"][0].get("image") == plan.image
             and env.get("SERVICE_ROLE", {}).get("value") == "resume-upload-coordinator"
             and env.get("APP_RELEASE", {}).get("value") == plan.source_release
             and env.get(FLAG) == plan.original_flag
             and template_hash({key: row[key] for key in plan.template_keys}) == plan.template_sha256,
             "prior_revision_changed")
    return row


def original_template(plan, reader):
    rows = {name: pinned_revision(plan, reader, name, expected)
            for name, expected in plan.prior_revisions}
    template = {key: copy.deepcopy(rows[plan.prior_revision].get(key)) for key in plan.template_keys}
    env = rolling.environment(template)
    need(template_hash(template) == plan.template_sha256
         and template["containers"][0].get("image") == plan.image
         and env.get("APP_RELEASE", {}).get("value") == plan.source_release
         and env.get(FLAG) == plan.original_flag, "prior_template_changed")
    return template


def desired(plan, reader, phase):
    template = original_template(plan, reader)
    if phase == "pause":
        env = rolling.environment(template)
        env[FLAG] = {"name": FLAG, "value": "false"}
        template["containers"][0]["env"] = list(env.values())
    template["revision"] = plan.revision_name(phase)
    traffic = copy.deepcopy(list(plan.original_traffic))
    if phase == "pause":
        for row in traffic:
            row["revision"] = plan.revision_name(phase)
    return template, sorted(traffic, key=rolling.canonical)


def readback(plan, reader, phase, started):
    current = reader.service(SERVICE)
    ready(current)
    template, traffic = desired(plan, reader, phase)
    need(current["uid"] == plan.uid and int(current["generation"]) == started["prior_generation"] + 1
         and static_hash(current) == plan.static_sha256
         and rolling.short_revision(current["latestCreatedRevision"], SERVICE) == plan.revision_name(phase)
         and rolling.short_revision(current["latestReadyRevision"], SERVICE) in (
             {plan.revision_name("pause"), plan.revision_name("restore")}
             if phase == "restore" else {plan.revision_name("pause")})
         and template_hash(current["template"]) == template_hash(template)
         and rolling.pinned_traffic(SERVICE, current) == traffic, "readback_changed")
    observed = revision(reader, plan.revision_name(phase))
    need({key: observed.get(key) for key in plan.template_keys}
         == {key: template.get(key) for key in plan.template_keys}, "readback_changed")
    if phase == "pause":
        observe_closed(reader)
    return current


def action(plan, reader, journal, phase, *, reconcile=False):
    """At most one physical write per phase; reconciliation never starts one."""
    plan.validate()
    need(phase in {"pause", "restore"} and type(journal) is rolling.Journal
         and journal.plan_hash == rolling.sha(asdict(plan)), "original_journal_required")
    marker = "coordinator." + phase
    with journal.locked():
        started, ack = journal.read(marker + ".started"), journal.read(marker + ".acknowledged")
        if phase == "pause":
            need(journal.read("coordinator.restore.started") is None, "restore_already_started")
        if started is None:
            need(not reconcile, "reconcile_never_starts_write")
            current = reader.service(SERVICE)
            ready(current)
            if phase == "pause":
                need(rolling.sha(current) == plan.service_sha256, "preimage_changed")
            else:
                paused = journal.read("coordinator.pause.started")
                need(paused is not None and journal.read("coordinator.pause.checked") is not None,
                     "pause_not_observed")
                readback(plan, reader, "pause", paused)
            template, traffic = desired(plan, reader, phase)
            need(reader.revision(SERVICE, plan.revision_name(phase)) is None, "revision_already_exists")
            need(reader.service(SERVICE) == current, "preimage_changed")
            body = {"name": RESOURCE, "etag": current["etag"], "template": template, "traffic": traffic}
            journal.record(marker + ".started", {"prior_generation": int(current["generation"]),
                "request_sha256": rolling.sha(body), "replay_authorized": False})
            started = journal.read(marker + ".started")
            operation = reader.patch(SERVICE, body, "template,traffic")
            need(rolling.OPERATION.fullmatch(operation.get("name", "")), "unknown_acknowledgement")
            journal.record(marker + ".acknowledged", {"operation": operation["name"]})
            ack = journal.read(marker + ".acknowledged")
        elif ack is None:
            need(reconcile, "unknown_write_reconcile_only")
        if ack is not None:
            observed = reader.operation(ack["operation"])
            need(observed.get("name") == ack["operation"] and "error" not in observed,
                 "operation_failed_reconcile_only")
            if observed.get("done") is not True:
                return {"status": "coordinator_operation_pending", "phase": phase, "replay_authorized": False}
        assert started is not None
        current = readback(plan, reader, phase, started)
        if journal.read(marker + ".checked") is None:
            journal.record(marker + ".checked", {"generation": int(current["generation"]),
                "revision": current["latestCreatedRevision"], "reconciled_without_ack": ack is None})
        return {"status": "coordinator_" + phase + "_observed", "phase": phase,
                "replay_authorized": False, "inflight_invocations_proven_drained": False}


def load_plan(raw):
    value = rolling.release._json(raw)
    value["template_keys"] = tuple(value["template_keys"])
    value["original_traffic"] = tuple(value["original_traffic"])
    value["prior_revisions"] = tuple(tuple(row) for row in value["prior_revisions"])
    plan = CoordinatorPausePlan(**value)
    plan.validate()
    return plan
