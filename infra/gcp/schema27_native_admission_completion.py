"""Coordinator-only completion of an honestly rejected native admission closure.

The original API stage/route remain genuine history. A native audited HTTP400
and unchanged coordinator preimage permit a NEW short-tag operation, never an
ACK invention or replay of the original rejected request. No source/image/IAM,
credential, policy, model, checkout or database change is available here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime
from pathlib import Path

from infra.gcp import schema27_native_admission_closure as original

rolling, owned, restore = original.rolling, original.owned, original.restore
ROOT = Path(__file__).resolve().parents[2]
API, ANALYSIS, COORDINATOR = original.API, original.ANALYSIS, original.COORDINATOR
KIND, PROOF = "hirewiz_native_admission_completion_v1", "hirewiz_native_admission_completed_v1"
SOURCE_FILES = (*original.SOURCE_FILES, "infra/gcp/schema27_native_admission_completion.py")
PARENT_KEYS = {
    "root",
    "plan",
    "plan_sha256",
    "journal",
    "rejection",
    "rejection_sha256",
    "failure",
    "failure_sha256",
    "api_stage_sha256",
    "api_route_sha256",
    "rejected_intent_sha256",
}


def need(value, code):
    rolling.need(value, "native_admission_completion_" + code)


def require_tag(service, tag):
    need(
        type(tag) is str
        and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", tag)
        and len(service) + len(tag) <= 46,
        "tag_length",
    )


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source():
    value = original.source()
    value["files"][SOURCE_FILES[-1]] = file_sha(ROOT / SOURCE_FILES[-1])
    return value


def historical_source(pin):
    need(
        set(pin) == {"release", "tree", "files"}
        and set(pin["files"]) == set(SOURCE_FILES)
        and all(re.fullmatch(r"[a-f0-9]{40}", pin[k]) for k in ("release", "tree"))
        and all(rolling.HASH.fullmatch(v) for v in pin["files"].values()),
        "source_scope",
    )
    tree = (
        subprocess.check_output(
            ["git", "rev-parse", pin["release"] + "^{tree}"], cwd=ROOT, timeout=10
        )
        .decode()
        .strip()
    )
    need(tree == pin["tree"], "source_tree")
    for name, stamp in pin["files"].items():
        raw = subprocess.check_output(
            ["git", "show", pin["release"] + ":" + name], cwd=ROOT, timeout=10
        )
        need(
            hashlib.sha256(raw).hexdigest() == stamp and file_sha(ROOT / name) == stamp,
            "source_bytes",
        )


def make_parent(
    *,
    root,
    plan_path,
    plan_sha256,
    journal_path,
    rejection_path,
    rejection_sha256,
    failure_path,
    failure_sha256,
):
    """Local/read-only convenience; resulting spec still requires verification."""
    journal = Path(journal_path)
    return {
        "root": str(root),
        "plan": str(plan_path),
        "plan_sha256": plan_sha256,
        "journal": str(journal),
        "rejection": str(rejection_path),
        "rejection_sha256": rejection_sha256,
        "failure": str(failure_path),
        "failure_sha256": failure_sha256,
        "api_stage_sha256": file_sha(journal / ("stage." + API + ".checked.json")),
        "api_route_sha256": file_sha(journal / ("route." + API + ".checked.json")),
        "rejected_intent_sha256": file_sha(journal / ("stage." + COORDINATOR + ".started.json")),
    }


def checked_route(plan, journal, pin):
    checks = {}
    for phase in ("stage", "route"):
        started, ack, checked = original.record(plan, journal, pin, phase)
        need(
            started is not None
            and ack is not None
            and checked is not None
            and checked.get("health_verified") is True
            and checked.get("service_uid") == pin.before["service_uid"]
            and checked.get("generation")
            == pin.before["generation"] + (1 if phase == "stage" else 2)
            and checked.get("created") == checked.get("ready") == pin.revision
            and checked.get("traffic") == original.traffic(plan, pin, phase)
            and checked.get("prior_revision_uid") == pin.prior_uid
            and checked.get("static_public_sha256") == pin.before["static_public_sha256"],
            "checked_route_required",
        )
        checks[phase] = checked
    need(
        checks["stage"]["revision_uid"] == checks["route"]["revision_uid"]
        and checks["stage"]["template_public_sha256"] == checks["route"]["template_public_sha256"],
        "revision_changed",
    )
    return {k: v for k, v in checks["route"].items() if k not in {"health_verified", "plan_sha256"}}


def read_parent(parent):
    need(type(parent) is dict and set(parent) == PARENT_KEYS, "parent_scope")
    need(
        all(rolling.HASH.fullmatch(parent[k]) for k in PARENT_KEYS if k.endswith("sha256")),
        "parent_hashes",
    )
    root = Path(parent["root"])
    need(root.is_absolute() and root.resolve() == root, "parent_root")
    old = original.load(
        restore._private_read(parent["plan"], parent["plan_sha256"]), parent["plan_sha256"]
    )
    original.historical_source(old.source)
    need(
        subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, timeout=10).decode().strip()
        == old.source["release"]
        and not subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root, timeout=10
        ),
        "frozen_parent_changed",
    )
    need(
        all(file_sha(root / name) == stamp for name, stamp in old.source["files"].items()),
        "parent_bytes",
    )
    original.parent_read(old.parent)
    journal = owned.Journal(Path(parent["journal"]), old)
    api = next(p for p in old.services if p.service == API)
    coordinator = next(p for p in old.services if p.service == COORDINATOR)
    api_state = checked_route(old, journal, api)
    for phase in ("stage", "route"):
        need(
            file_sha(journal.directory / (phase + "." + API + ".checked.json"))
            == parent["api_" + phase + "_sha256"],
            "api_receipt_changed",
        )
    key = "stage." + COORDINATOR
    started = journal.read(key + ".started")
    expected = {
        "previous_generation": coordinator.before["generation"],
        "previous_etag": coordinator.before["etag"],
        "expected_generation": coordinator.before["generation"] + 1,
        "revision": coordinator.revision,
        "mask": "template,traffic",
        "traffic": original.traffic(old, coordinator, "stage"),
        "replay_authorized": False,
        "plan_sha256": old.digest,
    }
    need(
        journal.claimed(key + ".started")
        and started == expected
        and file_sha(journal.directory / (key + ".started.json"))
        == parent["rejected_intent_sha256"],
        "rejected_intent_changed",
    )
    for name in (
        key + ".ack",
        key + ".checked",
        "route." + COORDINATOR + ".started",
        "route." + COORDINATOR + ".ack",
        "route." + COORDINATOR + ".checked",
        "complete",
    ):
        need(journal.read(name) is None and not journal.claimed(name), "parent_not_rejected_only")
    rejection = rolling.release._json(
        restore._private_read(parent["rejection"], parent["rejection_sha256"])
    )
    need(
        set(rejection)
        == {
            "read_at",
            "native_audit_entries",
            "service_patch_response_http_status",
            "native_mutation_acknowledged",
            "original_journal_retained",
            "replay_authorized",
        }
        and rejection["service_patch_response_http_status"] == 400
        and rejection["native_mutation_acknowledged"] is False
        and rejection["original_journal_retained"] is True
        and rejection["replay_authorized"] is False
        and len(rejection["native_audit_entries"]) == 1,
        "native_rejection_required",
    )
    entry = rejection["native_audit_entries"][0]
    need(
        set(entry) == {"insertId", "timestamp", "logName", "protoPayload"}
        and type(entry["insertId"]) is str
        and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", entry["insertId"])
        and entry["logName"]
        == "projects/" + rolling.release.PROJECT + "/logs/cloudaudit.googleapis.com%2Factivity",
        "audit_identity",
    )
    payload = entry["protoPayload"]
    need(
        set(payload) == {"methodName", "resourceName", "status"}
        and payload["methodName"] == "google.cloud.run.v2.Services.UpdateService"
        and payload["resourceName"] == rolling.PARENT + "/services/" + COORDINATOR
        and payload["status"]
        == {
            "code": 3,
            "message": "traffic[].tag: traffic tag '"
            + old.tag
            + "' and service name '"
            + COORDINATOR
            + "' together are too long. Combined traffic tag and service name cannot exceed 46 characters.",
        }
        and len(old.tag) + len(COORDINATOR) > 46,
        "exact_tag_length_rejection",
    )
    failure = rolling.release._json(
        restore._private_read(parent["failure"], parent["failure_sha256"])
    )
    need(
        set(failure) == {"observed_at", "diagnostic", "automatic_replay"}
        and failure["automatic_replay"] is False
        and failure["diagnostic"].get("http_status") == 400
        and failure["diagnostic"].get("exception_class") == "HttpBadRequestError"
        and failure["diagnostic"].get("replay_authorized") is False
        and failure["diagnostic"].get("native_approval") is False,
        "definite_http400_required",
    )
    when = datetime.fromisoformat(entry["timestamp"].replace("Z", "+00:00"))
    observed = datetime.fromisoformat(failure["observed_at"])
    read_at = datetime.fromisoformat(rejection["read_at"])
    need(
        when.tzinfo is not None
        and observed.tzinfo is not None
        and read_at.tzinfo is not None
        and 0 <= (observed - when).total_seconds() <= 30
        and when <= read_at,
        "rejection_timing",
    )
    return old, journal, api_state


@dataclass(frozen=True)
class Plan:
    parent: dict
    coordinator: original.Pin
    analysis: dict
    nonce: str
    source: dict
    journal_location: dict
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def tag(self):
        return "nc-" + self.nonce[:12]

    def validate(self):
        require_tag(COORDINATOR, self.tag)
        need(
            type(self) is Plan
            and self.kind == KIND
            and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
            and type(self.coordinator) is original.Pin
            and self.coordinator.service == COORDINATOR
            and self.coordinator.revision == COORDINATOR + "-nc-" + self.nonce[:12]
            and len(self.tag) + len(COORDINATOR) <= 46
            and len(self.coordinator.revision) <= 63,
            "short_tag_scope",
        )
        need(type(self.parent) is dict and set(self.parent) == PARENT_KEYS, "parent_scope")


def load(raw, digest):
    need(hashlib.sha256(raw).hexdigest() == digest and len(raw) <= 131072, "plan_file")
    value = rolling.release._json(raw)
    need(type(value) is dict and set(value) == {f.name for f in fields(Plan)}, "plan_shape")
    pin = value["coordinator"]
    result = Plan(
        **(
            value
            | {
                "coordinator": original.Pin(
                    **(pin | {"template_keys": tuple(pin["template_keys"])})
                )
            }
        )
    )
    result.validate()
    need(rolling.canonical(asdict(result)) == rolling.canonical(value), "plan_canonical")
    return result


def parent_binding(plan):
    plan.validate()
    historical_source(plan.source)
    old, journal, api = read_parent(plan.parent)
    previous = next(p for p in old.services if p.service == COORDINATOR)
    need(
        plan.coordinator == replace(previous, revision=COORDINATOR + "-nc-" + plan.nonce[:12])
        and plan.analysis == old.analysis
        and plan.tag != old.tag,
        "completion_scope",
    )
    return old, journal, api


def exact_changed(boundary, pin, plan, phase):
    value = boundary.service(pin.service)
    original.ready(value, pin.service)
    expected = original.desired(boundary, pin)
    need(
        value["uid"] == pin.before["service_uid"]
        and int(value["generation"]) == pin.before["generation"] + (1 if phase == "stage" else 2)
        and original.projection(value, pin.service)["static_public_sha256"]
        == pin.before["static_public_sha256"]
        and original.normalize(value["template"], pin.service) == expected
        and rolling.pinned_traffic(pin.service, value) == original.traffic(plan, pin, phase)
        and rolling.short_revision(value["latestCreatedRevision"], pin.service) == pin.revision,
        "native_state_changed",
    )
    revision = boundary.revision(pin.service, pin.revision)
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
    return original.projection(value, pin.service) | {
        "revision_uid": revision["uid"],
        "prior_revision_uid": pin.prior_uid,
    }


def verify_current(plan, boundary, journal):
    old, old_journal, api = parent_binding(plan)
    original_api = next(p for p in old.services if p.service == API)
    need(exact_changed(boundary, original_api, old, "route") == api, "original_api_changed")
    need(
        original.projection(boundary.service(ANALYSIS), ANALYSIS) == plan.analysis,
        "analysis_changed",
    )
    old_coordinator = next(p for p in old.services if p.service == COORDINATOR)
    need(
        boundary.revision(COORDINATOR, old_coordinator.revision) is None, "rejected_revision_exists"
    )
    pin = plan.coordinator
    _, baseline = original.prior(boundary, pin)
    stage = original.record(plan, journal, pin, "stage") if journal else (None, None, None)
    route = original.record(plan, journal, pin, "route") if journal else (None, None, None)
    active = route if route[0] else stage
    if active[0] is None:
        current = boundary.service(COORDINATOR)
        original.ready(current, COORDINATOR)
        need(
            original.projection(current, COORDINATOR) == pin.before
            and original.normalize(current["template"], COORDINATOR) == baseline
            and boundary.revision(COORDINATOR, pin.revision) is None,
            "coordinator_no_effect_required",
        )
        coordinator = original.projection(current, COORDINATOR)
    else:
        assert active[1] is not None
        operation = boundary.operation(active[1]["operation"])
        need(operation.get("done") is True and "error" not in operation, "operation_not_complete")
        coordinator = exact_changed(boundary, pin, plan, "route" if route[0] else "stage")
        if active[2] is not None:
            need(
                active[2] == coordinator | {"health_verified": True, "plan_sha256": plan.digest},
                "checked_changed",
            )
    return {API: api, COORDINATOR: coordinator}


def capture(parent, boundary, nonce, journal_directory):
    need(type(boundary) is NativeBoundary, "exact_boundary")
    old, _, _ = read_parent(parent)
    boundary.verify_rejection(parent)
    previous = next(p for p in old.services if p.service == COORDINATOR)
    directory = Path(journal_directory)
    need(
        directory.is_absolute()
        and directory.resolve() == directory
        and directory.is_dir()
        and not any(directory.iterdir()),
        "new_journal_required",
    )
    plan = Plan(
        parent,
        replace(previous, revision=COORDINATOR + "-nc-" + nonce[:12]),
        old.analysis,
        nonce,
        source(),
        restore.scope.journal_location(type("L", (), {"directory": directory})()),
    )
    plan.validate()
    need(not any(r.get("tag") == plan.tag for r in previous.before["traffic"]), "new_tag_required")
    verify_current(plan, boundary, None)
    return plan


def proof(plan, states, complete):
    old, _, _ = parent_binding(plan)
    return {
        "kind": PROOF,
        "services": states,
        "analysis_unchanged": plan.analysis,
        "new_admission_closed": complete,
        "parent_restore_plan_sha256": old.parent["plan_sha256"],
        "parent_restore_complete_sha256": old.parent["complete_sha256"],
        "original_closure_plan_sha256": plan.parent["plan_sha256"],
        "original_api_route_checked_sha256": plan.parent["api_route_sha256"],
        "native_rejection_audit_sha256": plan.parent["rejection_sha256"],
        "required_flags": {
            API: {"RESUME_UPLOAD_ADMISSION_ENABLED": False, "RESUME_DIRECT_UPLOAD_ENABLED": True},
            COORDINATOR: {
                "RESUME_DIRECT_UPLOAD_ENABLED": False,
                "RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED": False,
            },
        },
        "saved_api_reads_preserved": complete,
        "original_rejected_patch_replayed": False,
        "document_processing_enabled": False,
        "payment_activation_authorized": False,
        "replay_authorized": False,
    }


def require_unpaused(plan, journal):
    original.require_unpaused(journal)
    try:
        (Path(plan.parent["journal"]) / "operator.pause-requested").lstat()
    except FileNotFoundError:
        return
    need(False, "parent_operator_pause_requested")


def execute(mode, plan, boundary, journal):
    need(
        mode in {"close", "reconcile", "observe"}
        and type(boundary) is NativeBoundary
        and type(journal) is owned.Journal
        and journal.plan_hash == plan.digest,
        "exact_owner",
    )
    need(source() == plan.source, "mutator_source_changed")
    with journal.locked():
        pin = plan.coordinator
        for phase in ("stage", "route"):
            started, ack, _ = original.record(plan, journal, pin, phase)
            if started:
                operation = boundary.operation(ack["operation"])
                need("error" not in operation, "operation_failed_reconcile_only")
                if operation.get("done") is not True:
                    return {
                        "status": "same_native_operation_pending",
                        "native_mutations": 0,
                        "service": COORDINATOR,
                        "phase": phase,
                    }
        verify_current(plan, boundary, journal)
        mutations = 0
        for phase in ("stage", "route"):
            key = phase + "." + COORDINATOR
            started, _, checked = original.record(plan, journal, pin, phase)
            if started is None:
                if mode != "close":
                    break
                require_unpaused(plan, journal)
                verify_current(plan, boundary, journal)
                before = boundary.service(COORDINATOR)
                previous = (
                    pin.before
                    if phase == "stage"
                    else journal.read("stage." + COORDINATOR + ".checked")
                )
                need(
                    previous is not None
                    and original.projection(before, COORDINATOR)
                    == {k: previous[k] for k in pin.before},
                    "prewrite_changed",
                )
                body = {
                    "name": before["name"],
                    "etag": before["etag"],
                    "traffic": original.traffic(plan, pin, phase),
                }
                mask = "traffic"
                if phase == "stage":
                    body["template"] = original.desired(boundary, pin)
                    mask = "template,traffic"
                for target in body["traffic"]:
                    if "tag" in target:
                        require_tag(COORDINATOR, target["tag"])
                need(boundary.service(COORDINATOR) == before, "prewrite_changed")
                started = {
                    "previous_generation": previous["generation"],
                    "previous_etag": previous["etag"],
                    "expected_generation": previous["generation"] + 1,
                    "revision": pin.revision,
                    "mask": mask,
                    "traffic": body["traffic"],
                    "replay_authorized": False,
                }
                require_unpaused(plan, journal)
                journal.record(key + ".started", started)
                started = journal.read(key + ".started")
                require_unpaused(plan, journal)
                ack = boundary.patch(COORDINATOR, body, mask)
                need(
                    set(ack) == {"name"} and rolling.OPERATION.fullmatch(ack["name"]),
                    "native_ack_unknown_reconcile_only",
                )
                journal.record(
                    key + ".ack", {"operation": ack["name"], "intent_sha256": rolling.sha(started)}
                )
                mutations += 1
                operation = boundary.operation(ack["name"])
                need("error" not in operation, "operation_failed_reconcile_only")
                if operation.get("done") is not True:
                    return {
                        "status": "same_native_operation_pending",
                        "native_mutations": mutations,
                        "service": COORDINATOR,
                        "phase": phase,
                    }
            if checked is None and mode != "observe":
                snapshot = verify_current(plan, boundary, journal)[COORDINATOR]
                boundary.health(plan, pin)
                need(
                    verify_current(plan, boundary, journal)[COORDINATOR] == snapshot,
                    "health_preimage_changed",
                )
                journal.record(key + ".checked", snapshot | {"health_verified": True})
            if journal.read(key + ".checked") is None:
                break
        states = verify_current(plan, boundary, journal)
        complete = journal.read("route." + COORDINATOR + ".checked") is not None
        result = proof(plan, states, complete)
        if complete:
            if journal.read("complete") is None:
                need(mode != "observe", "completion_not_recorded")
                journal.record("complete", result)
            need(
                journal.read("complete") == result | {"plan_sha256": plan.digest},
                "complete_changed",
            )
        return result | {"native_mutations": mutations}


class CompletionRun(original.AdmissionRun):
    def patch(self, service, body, mask):
        need(service == COORDINATOR, "coordinator_only")
        return super().patch(service, body, mask)


class NativeBoundary(original.NativeBoundary):
    def __init__(self):
        super().__init__()
        self._run: CompletionRun | None = None

    def verify_rejection(self, parent):
        selected = rolling.release._json(
            restore._private_read(parent["rejection"], parent["rejection_sha256"])
        )["native_audit_entries"]
        entry = selected[0]
        query = (
            'logName="'
            + entry["logName"]
            + '" AND insertId="'
            + entry["insertId"]
            + '" AND timestamp="'
            + entry["timestamp"]
            + '"'
        )
        raw = self.observer._command(
            "gcloud",
            "logging",
            "read",
            query,
            "--limit=2",
            "--project=" + rolling.release.PROJECT,
            "--billing-project=" + rolling.release.PROJECT,
            "--account=harshilyogeshbhai886@gmail.com",
            "--format=json(timestamp,insertId,logName,protoPayload.methodName,protoPayload.resourceName,protoPayload.status)",
            "--quiet",
        )
        need(rolling.release._json(raw) == selected, "fresh_native_rejection_required")

    @property
    def run(self):
        if self._run is None:
            self._run = CompletionRun()
        return self._run

    def patch(self, service, body, mask):
        need(service == COORDINATOR, "coordinator_only")
        return self.run.patch(service, body, mask)


class ClosedAdmissionCompletion:
    def __init__(self, plan, journal):
        need(type(plan) is Plan and type(journal) is owned.Journal, "closed_type")
        self.plan, self.journal = plan, journal
        self.verify_custody()

    @property
    def restore_parent(self):
        old, _, _ = read_parent(self.plan.parent)
        return old.parent

    @property
    def parent_restore_plan_sha256(self):
        return self.restore_parent["plan_sha256"]

    @property
    def parent_restore_complete_sha256(self):
        return self.restore_parent["complete_sha256"]

    def verify_custody(self):
        old, _, api = parent_binding(self.plan)
        self.journal.verify()
        need(self.journal.plan_hash == self.plan.digest, "owner_changed")
        coordinator = checked_route(self.plan, self.journal, self.plan.coordinator)
        states = {API: api, COORDINATOR: coordinator}
        complete = self.journal.read("complete")
        need(
            complete == proof(self.plan, states, True) | {"plan_sha256": self.plan.digest},
            "complete_scope",
        )
        return complete

    def verify_current(self, boundary=None):
        complete = self.verify_custody()
        own = boundary is None
        boundary = NativeBoundary() if own else boundary
        try:
            states = verify_current(self.plan, boundary, self.journal)
            need(states == complete["services"], "current_changed")
            return states
        finally:
            if own:
                boundary.close()


def load_completed(*, plan_path, plan_sha256, journal_path):
    plan = load(restore._private_read(plan_path, plan_sha256), plan_sha256)
    return ClosedAdmissionCompletion(plan, owned.Journal(Path(journal_path), plan))


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("mode", choices=("capture", "close", "reconcile", "observe"))
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
            need(
                args.parent is not None
                and args.parent_sha256
                and args.nonce
                and not args.plan.exists(),
                "capture_inputs",
            )
            parent = rolling.release._json(restore._private_read(args.parent, args.parent_sha256))
            plan = capture(parent, boundary, args.nonce, args.journal)
            owned.Journal(args.journal, plan, create=True)
            stamp = restore.mixed.app.bridge._write_plan(args.plan, plan)
            print(json.dumps({"status": "captured", "plan_sha256": stamp, "native_mutations": 0}))
        else:
            plan = load(restore._private_read(args.plan, args.plan_sha256), args.plan_sha256)
            result = execute(args.mode, plan, boundary, owned.Journal(args.journal, plan))
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:
        value = restore.mixed.diagnostics.diagnostic(error, "observe")
        if re.fullmatch(r"native_admission_completion_[a-z_]{1,90}", str(error)):
            value["fixed_code"] = str(error)
        print(json.dumps(value, sort_keys=True))
        return 1
    finally:
        boundary.close()


if __name__ == "__main__":
    raise SystemExit(main())
