"""Versioned API-only identity promotion, with an independent owned journal.

The frozen rehearsal is read-only predecessor evidence, never a replayable
action or a substituted source authority. No IAM, payment, SQL or model writer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from types import SimpleNamespace

from infra.gcp import schema27_api_identity_operator as rehearsal
from infra.gcp import schema27_api_identity_readback as readback
from infra.gcp import schema27_forward_diagnostics as diagnostics

# Import after rehearsal installs the standalone backend module path.
# isort: split
from scripts import schema26_app_successor as source

rolling, API = rehearsal.rolling, rehearsal.API
ROOT = Path(__file__).resolve().parents[2]
KIND = "hirewiz_schema27_api_identity_promotion_v1"
PHASES = ("promote", "rollback-template", "rollback-traffic")
REHEARSAL_CODE = "1bb94f7aba54614de21569c38eea95ff5cf67c9d11a6dd0b1c6de4cf6f480233"
REHEARSAL_READER = "835819ccc00fa88248919b2d864f3c1d9d9d6e18b37b24857e5a4a3ff829aa7d"
HANDOFF_HELPERS = ("infra/gcp/schema27_api_identity_promotion.py", "infra/gcp/schema27_mixed_identity_profile.py",
    "infra/gcp/schema27_mixed_document_proof.py", "infra/gcp/schema27_forward_diagnostics.py",
    "infra/gcp/document_source_release.py")


def need(value, reason):
    rolling.need(value, "schema27_identity_promotion_" + reason)


def controller_source():
    for module, relative in ((rehearsal, "infra/gcp/schema27_api_identity_operator.py"),
            (readback, "infra/gcp/schema27_api_identity_readback.py"),
            (diagnostics, "infra/gcp/schema27_forward_diagnostics.py"),
            (source, "backend/scripts/schema26_app_successor.py")):
        need(Path(module.__file__).resolve() == ROOT / relative, "controller_module_origin")
    need(hashlib.sha256(Path(rehearsal.__file__).read_bytes()).hexdigest() == REHEARSAL_CODE
         and hashlib.sha256(Path(readback.__file__).read_bytes()).hexdigest() == REHEARSAL_READER,
         "frozen_rehearsal_code_changed")
    release, stamp = source.controller_source(ROOT)
    return {"release": release, "sha256": stamp, "promotion_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "helpers_sha256": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in HANDOFF_HELPERS}}


@dataclass(frozen=True)
class Plan:
    predecessor: rehearsal.Plan
    predecessor_receipt: dict
    predecessor_records_sha256: str
    controller: dict
    journal_location: dict
    workers_release: str
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def transition(self):
        return self.predecessor.transition

    @property
    def before(self):
        return self.predecessor.before

    @property
    def rollback_revision(self):
        return self.predecessor.rollback_revision

    @property
    def handoff(self):
        return self.predecessor_receipt["observation"]

    @classmethod
    def from_dict(cls, value):
        need(type(value) is dict and set(value) == {field.name for field in fields(cls)}, "plan_shape")
        result = cls(**(value | {"predecessor": rehearsal.Plan.from_dict(value["predecessor"])}))
        result.validate()
        return result

    def validate(self):
        self.predecessor.validate()
        need(self.kind == KIND and readback._hash(self.predecessor_records_sha256)
             and self.predecessor.source["files"].get("infra/gcp/schema27_api_identity_operator.py") == REHEARSAL_CODE
             and self.predecessor.source["files"].get("infra/gcp/schema27_api_identity_readback.py") == REHEARSAL_READER,
             "frozen_predecessor")
        need(type(self.controller) is dict and set(self.controller) == {"release", "sha256", "promotion_sha256", "helpers_sha256"}
             and type(self.controller["release"]) is str and len(self.controller["release"]) == 40
             and all(c in "0123456789abcdef" for c in self.controller["release"])
             and readback._hash(self.controller["sha256"]) and readback._hash(self.controller["promotion_sha256"]),
             "controller_pin")
        need(type(self.controller["helpers_sha256"]) is dict and set(self.controller["helpers_sha256"]) == set(HANDOFF_HELPERS)
             and all(readback._hash(stamp) for stamp in self.controller["helpers_sha256"].values())
             and self.controller["helpers_sha256"][HANDOFF_HELPERS[0]] == self.controller["promotion_sha256"],
             "handoff_helper_pins")
        need(type(self.workers_release) is str and len(self.workers_release) == 40
             and all(c in "0123456789abcdef" for c in self.workers_release), "worker_source_pin")
        location = self.journal_location
        need(type(location) is dict and set(location) == {"path", "device", "inode"}
             and type(location["path"]) is str and Path(location["path"]).is_absolute()
             and all(type(location[key]) is int and location[key] >= 0 for key in ("device", "inode"))
             and location != self.predecessor.journal_location, "new_journal_pin")
        receipt = self.predecessor_receipt
        need(type(receipt) is dict and set(receipt) == {"observation", "retained", "operation_ack_retained",
             "request_sha256", "plan_sha256"} and receipt["plan_sha256"] == self.predecessor.digest
             and receipt["request_sha256"] == self.predecessor.stage_request_sha256
             and type(receipt["operation_ack_retained"]) is bool, "predecessor_receipt")
        readback.validate({"kind": readback.KIND, "plan": asdict(self.transition),
                           "before": self.before, "after": self.handoff})
        need(receipt["retained"] == self.before["services"][API]["revisions"]
             | self.handoff["services"][API]["revisions"], "predecessor_retention")

    def verify_predecessor(self):
        self.validate()
        original = rehearsal.Journal(Path(self.predecessor.journal_location["path"]), self.predecessor)
        with original.locked():
            need(not any(original.claimed(phase + ".started") for phase in rehearsal.PHASES[1:]),
                 "predecessor_rollback_started")
            need(rehearsal._verify_receipt(self.predecessor, original, "stage") == self.predecessor_receipt,
                 "predecessor_changed")
            records = {name: original.read(name) for name in ("stage.started", "stage.ack", "stage.checked")}
            need(rolling.sha(records) == self.predecessor_records_sha256, "predecessor_changed")

    def verify_source(self):
        self.verify_predecessor()
        need(self.controller == controller_source(), "controller_changed")


def _new_location(directory):
    import os
    import stat

    directory = Path(directory)
    parent = directory.parent.stat(follow_symlinks=False)
    need(directory.is_absolute() and directory.resolve() == directory and not directory.exists()
         and stat.S_ISDIR(parent.st_mode) and parent.st_uid == os.getuid()
         and stat.S_IMODE(parent.st_mode) == 0o700, "new_original_journal")
    directory.mkdir(mode=0o700)
    state = directory.stat(follow_symlinks=False)
    return {"path": str(directory), "device": state.st_dev, "inode": state.st_ino}


def capture(boundary, predecessor, *, journal_directory):
    stamp = controller_source()
    original = rehearsal.Journal(Path(predecessor.journal_location["path"]), predecessor)
    with original.locked():
        need(not any(original.claimed(phase + ".started") for phase in rehearsal.PHASES[1:]),
             "predecessor_rollback_started")
        receipt = rehearsal._verify_receipt(predecessor, original, "stage")
        records = {name: original.read(name) for name in ("stage.started", "stage.ack", "stage.checked")}
    observed, native = rehearsal._observe(boundary)
    need(rolling.environment(native[API]["service"]["template"]).get("APP_RELEASE") == {
        "name": "APP_RELEASE", "value": rehearsal.APPLICATION_RELEASE}, "fixed70_source_changed")
    need(observed == receipt["observation"], "handoff_changed")
    rehearsal._retained(predecessor, boundary, receipt["retained"])
    need(boundary.revision(API, predecessor.rollback_revision) is None, "rollback_revision_exists")
    workers_release = boundary.document_release()
    result = Plan(predecessor, receipt, rolling.sha(records), stamp, _new_location(journal_directory), workers_release)
    result.verify_source()
    return result


def traffic(plan, *, restored=False):
    rows = rehearsal._expected_traffic(plan, restored=restored)
    if not restored:
        rows = [row | {"revision": plan.transition.candidate_revision} for row in rows]
    return sorted(rows, key=rolling.canonical)


def _predecessor(plan, journal, phase):
    if phase == "promote":
        return plan.handoff, plan.predecessor_receipt["retained"]
    if phase == "rollback-template":
        if journal.claimed("promote.started"):
            checked = verify_receipt(plan, journal, "promote")
            return checked["observation"], checked["retained"]
        return plan.handoff, plan.predecessor_receipt["retained"]
    checked = verify_receipt(plan, journal, "rollback-template")
    return checked["observation"], checked["retained"]


def _validate(plan, phase, previous, observed, retained):
    rehearsal._shared(plan, observed)
    need(observed["callers"] == previous["callers"], "callers_changed")
    if phase != "promote":
        prior, target = previous["services"][API], observed["services"][API]
        baseline = plan.before["services"][API]
        need(set(target) == set(baseline) and target["uid"] == baseline["uid"]
             and target["static_sha256"] == baseline["static_sha256"]
             and target["generation"] == prior["generation"] + 1
             and target["ready"] == target["created"] == plan.rollback_revision
             and target["template"] == baseline["template"]
             and target["traffic"] == (prior["traffic"] if phase == "rollback-template" else traffic(plan, restored=True)),
             "rollback_changed")
        revision = target["revisions"][plan.rollback_revision]
        need(revision == baseline["template"] | {"uid": revision["uid"]}
             and (phase != "rollback-template" or all(row["uid"] != revision["uid"] for row in retained.values())),
             "rollback_revision_changed")
        expected = retained | {plan.rollback_revision: revision}
        need(target["revisions"] == {name: expected[name] for name in {
            plan.rollback_revision, *(row["revision"] for row in target["traffic"]) }}, "rollback_inventory")
        return {"status": "old_identity_equivalent_revision_verified"}
    old, new = previous["services"][API], observed["services"][API]
    need(new == old | {"generation": old["generation"] + 1, "traffic": traffic(plan),
         "revisions": {plan.transition.candidate_revision: retained[plan.transition.candidate_revision]}},
         "promotion_changed")
    return {"status": "dedicated_api_identity_promoted"}


def verify_receipt(plan, journal, phase):
    previous, retained = _predecessor(plan, journal, phase)
    started = journal.read(phase + ".started")
    need(started is not None and set(started) == {"kind", "phase", "request_sha256", "etag", "mask",
         "previous_generation", "service_uid", "authorization_sha256", "plan_sha256"} and started["kind"] == KIND
         and started["phase"] == phase and readback._hash(started["request_sha256"])
         and rehearsal._etag({"etag": started["etag"]}) == started["etag"]
         and started["mask"] == ("template,traffic" if phase == "rollback-template" else "traffic")
         and type(started["previous_generation"]) is int
         and started["previous_generation"] == previous["services"][API]["generation"]
         and started["service_uid"] == plan.transition.service_uid, "started_changed")
    if phase == "promote":
        from infra.gcp import schema27_mixed_document_proof as documents
        from infra.gcp import schema27_mixed_identity_profile as profiles

        proof = journal.read("promotion.document-authorization")
        need(proof is not None and proof["plan_sha256"] == plan.digest, "promotion_authorization_missing")
        payload = {key: value for key, value in proof.items() if key != "plan_sha256"}
        documents.validate_receipt(profiles.StagedProfile(plan), payload)
        need(started["authorization_sha256"] == rolling.sha(payload), "promotion_authorization_changed")
    else:
        need(started["authorization_sha256"] is None, "rollback_authorization_shape")
    ack = rehearsal._ack(plan, journal, phase, started)
    checked = journal.read(phase + ".checked")
    need(checked is not None and set(checked) == {"observation", "retained", "request_sha256",
         "operation_ack_retained", "plan_sha256"} and type(checked["operation_ack_retained"]) is bool
         and checked["operation_ack_retained"] == (ack is not None)
         and checked["request_sha256"] == started["request_sha256"], "receipt_changed")
    _validate(plan, phase, previous, checked["observation"], retained)
    need(checked["retained"] == retained | checked["observation"]["services"][API]["revisions"], "retention_changed")
    return checked


def reconcile(plan, boundary, journal):
    rehearsal._guard(plan, journal)
    with journal.locked():
        phases = [phase for phase in PHASES if journal.claimed(phase + ".started")]
        need(bool(phases), "no_started_action")
        phase = phases[-1]
        previous, retained = _predecessor(plan, journal, phase)
        started = journal.read(phase + ".started")
        need(started is not None, "partial_claim_held")
        ack = rehearsal._ack(plan, journal, phase, started)
        if ack is not None:
            operation = boundary.operation(ack["name"])
            need(type(operation) is dict and set(operation) == {"name", "done"}
                 and operation["name"] == ack["name"] and type(operation["done"]) is bool,
                 "same_native_operation_failed")
            if not operation["done"]:
                return {"status": "same_native_operation_pending", "phase": phase}
        observed, _ = rehearsal._observe(boundary)
        _validate(plan, phase, previous, observed, retained)
        actual = rehearsal._retained(plan, boundary, retained) | observed["services"][API]["revisions"]
        value = {"observation": observed, "retained": actual, "request_sha256": started["request_sha256"],
                 "operation_ack_retained": ack is not None}
        if journal.read(phase + ".checked") is None:
            journal.record(phase + ".checked", value)
        need(verify_receipt(plan, journal, phase) == value | {"plan_sha256": plan.digest}, "receipt_changed")
        return {"status": "api_identity_action_verified", "phase": phase,
                "operation_ack_retained": ack is not None}


def _action(plan, boundary, journal, phase):
    rehearsal._guard(plan, journal)
    with journal.locked():
        need(not journal.claimed(phase + ".started"), "started_reconcile_only")
        if phase == "promote":
            need(not any(journal.claimed(p + ".started") for p in PHASES[1:]), "rollback_started")
        previous, retained = _predecessor(plan, journal, phase)
        observed, native = rehearsal._observe(boundary)
        need(observed == previous, "preimage_changed")
        rehearsal._retained(plan, boundary, retained)
        current = native[API]["service"]
        need(rolling.environment(current["template"]).get("APP_RELEASE") == {
            "name": "APP_RELEASE", "value": rehearsal.APPLICATION_RELEASE}, "fixed70_source_changed")
        authorization_sha256 = None
        if phase == "promote":
            from infra.gcp import schema27_mixed_document_proof as documents
            from infra.gcp import schema27_mixed_identity_profile as profiles

            proof = boundary.document_authorization(plan)
            documents.validate_receipt(profiles.StagedProfile(plan), proof)
            authorization_sha256 = rolling.sha(proof)
            previous_proof = journal.read("promotion.document-authorization")
            if previous_proof is None:
                journal.record("promotion.document-authorization", proof)
            else:
                need(previous_proof == proof | {"plan_sha256": plan.digest}, "promotion_authorization_changed")
        body = {"name": current["name"], "etag": rehearsal._etag(current), "traffic": traffic(
            plan, restored=phase == "rollback-traffic") if phase != "rollback-template" else previous["services"][API]["traffic"]}
        if phase == "rollback-template":
            need(boundary.revision(API, plan.rollback_revision) is None, "rollback_revision_exists")
            body["template"] = current["template"] | {"serviceAccount": plan.transition.old_api.email,
                                                       "revision": plan.rollback_revision}
        mask = "template,traffic" if phase == "rollback-template" else "traffic"
        plan.verify_source()
        need(boundary.service(API) == current, "fresh_preimage_changed")
        journal.record(phase + ".started", {"kind": KIND, "phase": phase, "mask": mask,
            "request_sha256": rolling.sha(body), "etag": body["etag"],
            "previous_generation": int(current["generation"]), "service_uid": current["uid"],
            "authorization_sha256": authorization_sha256})
        ack = boundary.patch(body, mask)
        need(type(ack) is dict and set(ack) == {"name"} and type(ack["name"]) is str
             and rolling.OPERATION.fullmatch(ack["name"]), "native_ack_unverified")
        journal.record(phase + ".ack", ack | {"request_sha256": rolling.sha(body)})
    return reconcile(plan, boundary, journal)


def promote(plan, boundary, journal):
    return _action(plan, boundary, journal, "promote")


def rollback(plan, boundary, journal):
    for phase in PHASES[1:]:
        if journal.claimed(phase + ".started"):
            verify_receipt(plan, journal, phase)
        else:
            result = _action(plan, boundary, journal, phase)
            if result["status"] == "same_native_operation_pending":
                return result
    return reconcile(plan, boundary, journal)


class DocumentMetadataObserver:
    """Add only the two document metadata GET scopes to the existing observer."""
    def __init__(self, delegate):
        self._delegate = delegate
        self._document_reads = None

    def _cloud(self, *args):
        return self._delegate._cloud(*args)

    def _get(self, api, path, params=None):
        resources = (rolling.release.documents.RESOURCE, rolling.release.native_tex.RESOURCE)
        need(api == "run" and type(path) is str and params in (None, {})
             and any(path == resource or re.fullmatch(
                 re.escape(resource + "/revisions/" + resource.rsplit("/", 1)[-1] + "-")
                 + r"[a-z0-9-]{1,36}", path) for resource in resources), "document_metadata_route_refused")
        if self._document_reads is None:
            from infra.gcp import rolling_native

            self._document_reads = rolling_native.native.SDKGoogleReads(
                rolling_native.host_auth.SDK, rolling_native.host_auth.ACCOUNT)
        return self._document_reads.get("run", path, {})


class NativeBoundary(rehearsal.NativeBoundary):
    observer: DocumentMetadataObserver

    def __init__(self):
        super().__init__()
        self.observer = DocumentMetadataObserver(self.observer)

    def document_release(self):
        worker = diagnostics.ReadBudget().read("run_service_read", lambda: self.observer._get(
            "run", rolling.PARENT + "/services/hirewiz-document-inspector"))
        return worker["template"]["labels"]["hirewiz-source"]

    def document_authorization(self, plan):
        from infra.gcp import schema27_mixed_document_proof as documents
        from infra.gcp import schema27_mixed_identity_profile as profiles

        return documents.verify_document_profile(self.observer, SimpleNamespace(release=plan.workers_release),
                                                 profiles.StagedProfile(plan))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("capture", "promote", "rollback", "observe", "reconcile", "profile", "owner-profile"))
    parser.add_argument("--journal", type=Path, required=True)
    parser.add_argument("--plan-sha256")
    parser.add_argument("--rehearsal-journal", type=Path)
    parser.add_argument("--rehearsal-plan-sha256")
    parser.add_argument("--profile-output", type=Path)
    args = parser.parse_args()
    boundary = None
    try:
        need((args.profile_output is not None) == (args.action in {"profile", "owner-profile"}), "profile_arguments")
        if args.action == "capture":
            need(args.plan_sha256 is None and args.rehearsal_journal is not None
                 and readback._hash(args.rehearsal_plan_sha256), "capture_arguments")
            holder = object.__new__(rehearsal.Journal)
            holder.directory, holder.plan_hash = args.rehearsal_journal, args.rehearsal_plan_sha256
            predecessor = rehearsal.Plan.from_dict(holder.read("plan")["plan"])
            need(predecessor.digest == args.rehearsal_plan_sha256
                 and args.rehearsal_journal == Path(predecessor.journal_location["path"]), "predecessor_plan_changed")
            rehearsal.Journal(args.rehearsal_journal, predecessor).verify()
            controller_source()
            boundary = NativeBoundary()
            plan = capture(boundary, predecessor, journal_directory=args.journal)
            rehearsal.Journal(args.journal, plan, create=True)
            result = {"status": "identity_promotion_captured", "plan_sha256": plan.digest,
                      "native_mutations": 0, "native_approval": False}
        else:
            need(readback._hash(args.plan_sha256) and args.rehearsal_journal is None
                 and args.rehearsal_plan_sha256 is None, "review_arguments")
            holder = object.__new__(rehearsal.Journal)
            holder.directory, holder.plan_hash = args.journal, args.plan_sha256
            plan = Plan.from_dict(holder.read("plan")["plan"])
            need(plan.digest == args.plan_sha256, "reviewed_plan_changed")
            journal = rehearsal.Journal(args.journal, plan)
            rehearsal._guard(plan, journal)
            boundary = NativeBoundary()
            if args.action in {"observe", "profile", "owner-profile"}:
                phase = next((p for p in reversed(PHASES) if journal.claimed(p + ".started")), None)
                need(phase is not None, "no_started_action")
                expected = verify_receipt(plan, journal, phase)
                observed, _ = rehearsal._observe(boundary)
                need(observed == expected["observation"], "observed_changed")
                rehearsal._retained(plan, boundary, expected["retained"])
                result = {"status": "identity_promotion_observed", "native_mutations": 0}
                if args.action in {"profile", "owner-profile"}:
                    from infra.gcp import schema27_mixed_identity_profile as profiles

                    need(phase == "promote" and args.profile_output.is_absolute()
                         and args.profile_output.resolve() == args.profile_output, "profile_export_scope")
                    private_profile = profiles.from_promoted(plan, journal)
                    profile = profiles.public_export(private_profile) if args.action == "profile" else private_profile
                    raw = rolling.canonical(asdict(profile)) + b"\n"
                    fd = os.open(args.profile_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    result = {"status": "identity_maintenance_profile_exported" if args.action == "profile"
                              else "identity_private_owner_profile_exported", "native_mutations": 0,
                              "sha256": hashlib.sha256(raw).hexdigest(), "native_approval": False}
            else:
                result = {"promote": promote, "rollback": rollback, "reconcile": reconcile}[args.action](plan, boundary, journal)
        print(json.dumps(result))
        return 0
    except Exception as error:
        print(json.dumps(diagnostics.diagnostic(error, args.action) | {
            "next_action": "inspect_original_journals_reconcile"}))
        return 1
    finally:
        if boundary is not None:
            try:
                boundary.close()
            except Exception:
                pass


if __name__ == "__main__":
    from infra.gcp.schema27_api_identity_promotion import main as entrypoint

    raise SystemExit(entrypoint())
