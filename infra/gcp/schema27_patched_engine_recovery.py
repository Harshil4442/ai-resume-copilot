"""Owned closed Source70 maintenance upgrading only the scanner engine.

The historical restore consumer is read only and proves its original interpreter
files/Git tree honestly. It never relabels the current controller as that source,
changes the restore mutator guard, or authorizes work through expired V5 proof.
"""
from __future__ import annotations

import fcntl
import hashlib
import os
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from infra.gcp import document_patched_engine_release as patched
from infra.gcp import document_source_patched_engine as engine
from infra.gcp import document_source_release as source
from infra.gcp import schema27_expired_v5_recovery as predecessor
from infra.gcp import schema27_mixed_identity_profile as identities
from infra.gcp import schema27_mixed_maintenance as maintenance
from infra.gcp import schema27_staged_restore as restoration

rolling, scope, need = identities.rolling, restoration.scope, identities.need
RESTORE_RELEASE = "5ce33265c700a817f5e8d41dae877588589ae351"
RESTORE_TREE = "2a6ebc2a026b1ef845a3a046da4004c1c2f23a74"
RESTORE_PLAN_FILE_SHA256 = "4e076ce33494ac478f16fdbd3ab384bbfc49ccf1102cc772f18d3b7344165668"


def caller_admission_closed(name, value):
    """Keep saved/status API access while refusing every new processing request."""
    if name not in source.transition.CALLERS:
        return False
    env = source.transition.environment(value["template"])
    required = {
        "ai-resume-parser": {"RESUME_UPLOAD_ADMISSION_ENABLED": "false",
            "RESUME_DIRECT_UPLOAD_ENABLED": "true", "OPTIONAL_AI_GENERATION_ENABLED": "false"},
        "hirewiz-analysis-worker": {"RESUME_DIRECT_UPLOAD_ENABLED": "false",
            "OPTIONAL_AI_GENERATION_ENABLED": "false"},
        "hirewiz-resume-coordinator": {"RESUME_DIRECT_UPLOAD_ENABLED": "false",
            "RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED": "false"},
    }[name]
    return all(env.get(key) == {"name": key, "value": flag} for key, flag in required.items())


def final_captured_flags(captured):
    result = {name: {key: {"name": key, "value": "false"}
                   for key in source.transition.PAUSE[name]} for name in source.transition.CALLERS}
    result[identities.API]["RESUME_DIRECT_UPLOAD_ENABLED"] = {
        "name": "RESUME_DIRECT_UPLOAD_ENABLED", "value": "true"}
    need(captured[identities.API]["RESUME_DIRECT_UPLOAD_ENABLED"] == result[identities.API]["RESUME_DIRECT_UPLOAD_ENABLED"],
         "patched_engine_saved_source_capability_changed")
    return result


class HistoricalRestore:
    """Historical completed restore custody; no mutation entry or freshness waiver."""
    def __init__(self, plan, journal, *, closed):
        from infra.gcp import schema27_native_admission_closure as admission
        from infra.gcp import schema27_native_admission_completion as completion

        need(type(closed) in {admission.ClosedAdmission, completion.ClosedAdmissionCompletion},
             "patched_engine_closed_admission_kind")
        self.closed = closed
        need(type(plan) is restoration.RestorePlan and type(journal) is restoration.owned.Journal,
             "patched_engine_restore_kind")
        self.plan, self.journal = plan, journal
        self.application_plan, self.profile = plan.application, plan.application.identities
        self.verify_custody()

    def _historical_custody(self):
        plan = self.plan
        plan.validate()
        plan.application.identities.verify_custody()
        expected_files = {*restoration.owned.SOURCE_FILES, "infra/gcp/schema27_staged_restore.py"}
        need(set(plan.source["files"]) == expected_files and plan.source["release"] == RESTORE_RELEASE
             and plan.source["tree"] == RESTORE_TREE, "patched_engine_restore_source_inventory")
        git = restoration.mixed.app.successor._git
        need(git(identities.ROOT, "rev-parse", plan.source["release"] + "^{tree}").decode().strip()
             == plan.source["tree"], "patched_engine_restore_historical_tree")
        for name, stamp in plan.source["files"].items():
            path = identities.ROOT / name
            need(path.is_file() and not path.is_symlink() and path.resolve() == path
                 and hashlib.sha256(path.read_bytes()).hexdigest() == stamp
                 and hashlib.sha256(git(identities.ROOT, "show", plan.source["release"] + ":" + name)).hexdigest() == stamp,
                 "patched_engine_restore_helper_changed")
        original = restoration._original(plan)
        need(restoration._stage_pins(plan.application, original) == plan.stages,
             "patched_engine_restore_original_changed")
        return original

    def verify_custody(self):
        self._historical_custody()
        self.journal.verify()
        need(self.journal.plan_hash == self.plan.digest, "verified_restore_owner")
        complete = self.journal.read("complete")
        need(complete is not None and complete.get("kind") == restoration.PROOF
             and complete.get("all_homogeneous_source70") is True, "restore_incomplete")
        for pin in self.plan.services:
            stage_started, stage_ack, stage_checked = restoration._record(self.plan, self.journal, pin, "restore-stage")
            _, _, checked = restoration._record(self.plan, self.journal, pin, "restore-route")
            need(stage_started is not None and stage_ack is not None and stage_checked is not None
                 and stage_checked.get("health_verified") is True
                 and stage_checked.get("operation_ack_retained") is True
                 and stage_checked.get("replay_authorized") is False
                 and stage_checked.get("service_uid") == pin.before["service_uid"]
                 and stage_checked.get("generation") == pin.before["generation"] + 1
                 and stage_checked.get("traffic") == restoration._stage_traffic(self.plan, pin)
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
        expected = {"kind": restoration.PROOF, "services": complete["services"], "all_homogeneous_source70": True,
                    "forward_release_authorized": False, "acceptance_freshness_authorized": False,
                    "payment_activation_authorized": False, "replay_authorized": False,
                    "plan_sha256": self.plan.digest}
        need(complete == expected, "restore_proof_scope")
        # The original completed restore is custody only. This separately owned
        # follow-on proves closure of API admission without disabling saved reads.
        self.closed.verify_custody()
        from infra.gcp import schema27_native_admission_completion as completion

        parent = (self.closed.restore_parent if type(self.closed) is completion.ClosedAdmissionCompletion
                  else self.closed.plan.parent)
        raw = restoration._private_read(parent["plan"], self.closed.parent_restore_plan_sha256)
        need(self.closed.parent_restore_plan_sha256 == RESTORE_PLAN_FILE_SHA256
             and restoration.load(raw, self.closed.parent_restore_plan_sha256) == self.plan
             and Path(parent["journal"]) == self.journal.directory
             and hashlib.sha256((self.journal.directory / "complete.json").read_bytes()).hexdigest()
                 == self.closed.parent_restore_complete_sha256,
             "patched_engine_admission_restore_parent")
        return complete


    def verify_current(self):
        """Fresh closed preimage before this controller creates any mutation."""
        self.verify_custody()
        return self.closed.verify_current()


def load_historical_restore(*, plan_path, plan_sha256, journal_path, profile, closed):
    need(plan_sha256 == RESTORE_PLAN_FILE_SHA256, "patched_engine_exact_restore_plan")
    plan = restoration.load(restoration._private_read(plan_path, plan_sha256), plan_sha256)
    need(type(profile) is identities.Profile and plan.application.identities == profile,
         "patched_engine_restore_profile")
    return HistoricalRestore(plan, restoration.owned.Journal(Path(journal_path), plan), closed=closed)


class ParentProbe(maintenance.Probe):
    def __init__(self, profile):
        need(type(profile) is identities.Profile, "patched_engine_private_profile")
        self.identity_profile = profile

    def Clients(self, *, read_only_release=False):
        return patched.Clients(self.identity_profile, read_only_release=read_only_release)

    def policy_for_serving(self, metadata, image):
        containers = metadata.get("template", {}).get("containers", [])
        need(type(containers) is list and len(containers) == 1 and containers[0].get("image") == image,
             "patched_engine_parent_image")
        rows = [row for row in containers[0].get("env", []) if row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256"]
        need(len(rows) == 1 and rows[0].get("value") in {patched.v5.POLICY_SHA256, engine.POLICY_SHA256},
             "patched_engine_parent_policy")
        return rows[0]["value"]

    def verify_expired_current_source_predecessor(self, image, metadata):
        return patched.historical_predecessor(self.identity_profile, image, metadata)

    def verify_current_successor(self, image, metadata):
        return patched.current_probe(self.identity_profile, image, metadata).verify_current_successor(image, metadata)

    def verify_caller_pins(self, clients, image, *, policy_sha256=None, caller_serving_validator=None):
        metadata = clients.service(source.prior.PARENT + "/services/hirewiz-document-inspector")
        if policy_sha256 == patched.v5.POLICY_SHA256:
            original = source.current_probe(image, metadata)
            probe = patched.HistoricalV5Probe(original.build, self.identity_profile, root=original.root, serving_read=True)
        else:
            probe = patched.current_probe(self.identity_profile, image, metadata)
        return probe.verify_caller_pins(clients, image, policy_sha256=policy_sha256,
                                       caller_serving_validator=caller_serving_validator)


class Applications(predecessor.Applications):
    def __init__(self, root, clients, get_json, uids, profile, restored):
        need(type(profile) is identities.Profile and type(restored) is HistoricalRestore
             and restored.profile == profile, "patched_engine_owned_restore")
        profile.verify_custody()
        restored.verify_current()
        maintenance.applications.Applications.__init__(self, root, clients, get_json, uids)
        self.identity_profile, self.probe, self.restored = profile, ParentProbe(profile), restored
        closed = restored.closed
        self.closed_preimage = closed.verify_custody()["services"] | {
            "hirewiz-analysis-worker": closed.plan.analysis}
        self.last_authorization = None
        need(all(uids[name] == profile.promotion.before["services"][name]["uid"]
                 for name in (identities.API, "hirewiz-analysis-worker")), "patched_engine_service_uid")
        pin = restored.application_plan.prior_source
        old = maintenance.applications.Source(pin.release, pin.image_digest, pin.build_id, pin.ci_run_id)
        old.validate()
        need(old.release == restoration.SOURCE70 and restored.application_plan.release == restoration.SOURCE3F7,
             "patched_engine_restored_source")
        need(old.release not in self.registry or self.registry[old.release] == old, "patched_engine_restored_source")
        self.registry[old.release] = old

    def require_active(self):
        require_active(self.stage_directory)

    def require_closed_preimage(self, name, value):
        from infra.gcp import schema27_native_admission_closure as admission

        need(name in source.transition.CALLERS and name in self.closed_preimage,
             "patched_engine_preimage_scope")
        observed = admission.projection(value, name)
        need(all(self.closed_preimage[name].get(key) == item for key, item in observed.items()),
             "patched_engine_closed_preimage_changed")

    def source_probe(self, build, *, root):
        return patched.Probe(build, self.identity_profile, root=root)

    def verify_engine_parent(self, current):
        need(type(current) is engine.Source, "patched_engine_source_kind")
        self.restored.verify_custody()
        clients = self.probe.Clients()
        try:
            metadata = clients.preflight()["service"]
            image = metadata["template"]["containers"][0]["image"]
            return self.probe.verify_expired_current_source_predecessor(image, metadata)
        finally:
            clients.close()


def selected(context):
    return type(context) is Applications


def promote(build, native_journal, transition_journal, *, application_context, root=engine.ROOT):
    need(selected(application_context) and type(build) is dict and build.get("kind") == engine.KIND
         and build.get("policy_sha256") == engine.POLICY_SHA256 and not transition_journal.exists(),
         "patched_engine_transition_scope")
    application_context.restored.verify_custody()
    probe = application_context.source_probe(build, root=root)
    need(native_journal.is_dir(), "patched_engine_full_native_proof_required")
    clients = probe.clients()
    try:
        for phase in ("inventory", "corpus"):
            need(patched._phase_read(native_journal / (phase + ".verified.json"))
                 == patched.phase_receipt(probe, clients, native_journal, phase),
                 "patched_engine_full_native_phase_changed")
    finally:
        clients.close()
    accepted = probe.verify_successor(build["immutable_image"], build, native_journal)
    source.transition.require_current_acceptance(accepted, build["immutable_image"], probe)
    application_context.require_active()
    source.prior.build_module().write_new(native_journal / "transition.started.json", {
        "build_identity_sha256": source.corpus.sha(build), "transition_journal": str(transition_journal),
        "automatic_replay": False, "unknown_outcome": "read_only_reconcile_exact_transition_only"})
    http = source.prior.build_module().native_http()
    try:
        result = source.transition.transition(build["immutable_image"], build, native_journal, transition_journal,
            http=http, probe=probe, application_context=application_context, recover_expired_source=True)
    finally:
        http.close()
    return result | {"source_commit": build["source_commit"], "engine_version": engine.ENGINE_VERSION,
        "renderer_contract_version": 5, "required_case_count": 17,
        "definition_expires_at": accepted.definition_expires_at, "prior_evidence_retained": True}


STAGES = ("build", "inventory", "corpus", "promote")


def require_active(directory):
    need(type(directory) is Path or isinstance(directory, Path), "patched_engine_stage_root")
    need(not os.path.lexists(directory / "operator.pause-requested"), "patched_engine_operator_paused")


@contextmanager
def stage_lock(directory):
    from infra.gcp import document_refresh_recovery as custody

    build = source.prior.build_module()
    custody.private(directory, build)
    lock = directory / "stage.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        need(os.fstat(fd).st_uid == os.getuid() and os.fstat(fd).st_nlink == 1
             and os.fstat(fd).st_mode & 0o777 == 0o600, "patched_engine_stage_lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def owner_context(directory, application_context):
    state = directory.lstat()
    closure = application_context.restored.closed
    closure.verify_custody()
    return {"output_root": str(directory), "output_device": state.st_dev, "output_inode": state.st_ino,
        "controller_root": str(identities.ROOT),
        "identity_profile_sha256": application_context.identity_profile.digest,
        "restore_plan_file_sha256": RESTORE_PLAN_FILE_SHA256,
        "admission_closure_plan_sha256": closure.plan.digest,
        "admission_closure_journal": str(closure.journal.directory),
        "admission_closure_journal_device": closure.journal.directory.lstat().st_dev,
        "admission_closure_journal_inode": closure.journal.directory.lstat().st_ino,
        "combined_execution_authorized": False, "automatic_replay": False}


def build_owner_scope(directory, current, nonce, archive_sha256, application_context):
    return owner_context(directory, application_context) | {
        "kind": "hirewiz_patched_engine_build_owner_v1", "source_identity": current.identity,
        "nonce": nonce, "source_archive_sha256": archive_sha256}


def stage_scope(directory, build, application_context):
    current = engine.committed(build.get("source_commit"), root=identities.ROOT, clean=True)
    need(current.identity == {key: build[key] for key in current.identity}, "patched_engine_frozen_build_source")
    return owner_context(directory, application_context) | {
        "kind": "hirewiz_patched_engine_staged_candidate_v1", "source_commit": current.commit,
        "build_identity_sha256": source.corpus.sha(build), "nonce": build["nonce"]}


def local_once(path, value):
    if os.path.lexists(path):
        need(patched._phase_read(path) == value, "patched_engine_existing_local_record_changed")
    else:
        source.prior.build_module().write_new(path, value)


def record_build(value, directory, *, application_context, read_only_finalization=False):
    """Stop after Build/local proof; native image-size/cost review is external."""
    need(selected(application_context) and application_context.stage_directory == directory,
         "patched_engine_stage_root")
    need(type(read_only_finalization) is bool, "patched_engine_finalization_scope")
    if not read_only_finalization:
        application_context.require_active()
    build = value["build_identity"]
    local_once(directory / "candidate-build.json", build)
    local_once(directory / "stage-owner.json", stage_scope(directory, build, application_context))
    result = {"completed_stage": "build", "next_stage_required": "inventory",
        "immutable_image": build["immutable_image"], "build_identity_sha256": source.corpus.sha(build),
        "native_image_size_and_cost_review_required": True, "next_stage_started": False,
        "native_acceptance": False, "promotion_performed": False}
    local_once(directory / "build.completed.json", result)
    return result


def load_staged_build(directory, application_context):
    build = patched._phase_read(directory / "candidate-build.json")
    need(patched._phase_read(directory / "stage-owner.json") == stage_scope(directory, build, application_context),
         "patched_engine_owned_stage_scope_changed")
    completed = patched._phase_read(directory / "build.completed.json")
    need(completed.get("completed_stage") == "build" and completed.get("next_stage_started") is False
         and completed.get("immutable_image") == build["immutable_image"]
         and completed.get("build_identity_sha256") == source.corpus.sha(build), "patched_engine_build_stage_incomplete")
    return build


def finalize_build(directory, *, application_context):
    """Fill local records from the exact acknowledged Build; no native writes."""
    from scripts import document_refresh as refresh

    from infra.gcp import document_cloud_build_qa as qa
    from infra.gcp.document_refresh_auth import ReleaseReadOnlyHttp

    need(selected(application_context) and application_context.stage_directory == directory
         and not (directory / "native-proof").exists() and not (directory / "transition").exists(),
         "patched_engine_finalization_scope")
    identity = patched._phase_read(directory / "native-build-identity.json")
    current = engine.committed(identity.get("source_commit"), root=identities.ROOT, clean=True)
    need(current.identity == {key: identity[key] for key in current.identity}, "patched_engine_frozen_build_source")
    nonce = identity["nonce"]
    need(patched._phase_read(directory / "build-owner.json") == build_owner_scope(
        directory, current, nonce, identity["source_archive_sha256"], application_context),
        "patched_engine_finalization_owner_changed")
    need(patched._phase_read(directory / "build-started.json") == {"nonce": nonce,
        "recipe_sha256": identity["refresh_dockerfile_sha256"], "source_archive_sha256": identity["source_archive_sha256"],
        "automatic_replay": False, "unknown_outcome": "reconcile_exact_native_object_and_build_only"},
        "patched_engine_finalization_intent_changed")
    uploaded = {"bucket": identity["source_bucket"], "object": identity["source_object"], "generation": identity["source_generation"]}
    need(patched._phase_read(directory / "source-upload-acknowledged.json") == uploaded,
         "patched_engine_finalization_upload_ack_changed")
    request = refresh.full_source_build_request(nonce, uploaded, source_revision=True,
        source_commit=current.commit, source_patched_engine=True)
    need(patched._phase_read(directory / "build-create-started.json") == {"nonce": nonce,
        "request_sha256": source.corpus.sha(request), "automatic_replay": False},
        "patched_engine_finalization_build_intent_changed")
    ack = patched._phase_read(directory / "build-acknowledged.json")
    need(type(ack) is dict and set(ack) == {"nonce", "build_id", "operation_name", "automatic_replay"}
         and ack["nonce"] == nonce and ack["build_id"] == identity["build_id"]
         and type(ack["operation_name"]) is str and 0 < len(ack["operation_name"]) <= 512
         and ack["automatic_replay"] is False, "patched_engine_finalization_build_ack_changed")
    # This function's verifier/transport are explicitly read only. A missing ACK
    # can never be manufactured, even if a matching Build can be discovered.
    actual = refresh.verify_current_build_release(identity["build_id"], nonce, identity["immutable_image"])
    need(actual == identity, "patched_engine_finalization_native_identity_changed")
    http = ReleaseReadOnlyHttp()
    try:
        proof, native_log = qa.read_native(http, identity, refresh.native_json, root=identities.ROOT)
    finally:
        http.close()
    refresh.proof_contract(proof, refresh.accepted(), current.sources, now=datetime.now(UTC),
        image=identity["immutable_image"], build_identity=identity, source_revision=True, source_patched_engine=True)
    local_once(directory / "local-native-proof.json", proof)
    local_once(directory / "native-build-qa-log.json", native_log)
    full_identity = identity | {"local_native_proof_sha256": hashlib.sha256(
        (directory / "local-native-proof.json").read_bytes()).hexdigest()}
    return record_build({"build_identity": full_identity}, directory,
        application_context=application_context, read_only_finalization=True)


def run(directory, history, consumed_history_bundle, profile, restored, *, stage=None,
        operator_review=None, operator_review_sha256=None, finalize=False):
    """Exactly one explicit stage per invocation; provider cost review sits between."""
    from scripts import document_refresh as refresh

    need(stage in STAGES and type(finalize) is bool and (not finalize or stage == "build"), "patched_engine_explicit_stage_required")
    need(Path(refresh.__file__).resolve() == identities.ROOT / "backend/scripts/document_refresh.py",
         "patched_engine_controller_origin")
    need(directory.is_absolute() and directory.parent.is_dir() and directory.resolve() == directory,
         "patched_engine_fresh_output_required")
    need((operator_review is None) == (operator_review_sha256 is None), "patched_engine_review_reference")
    if stage == "build" and not finalize:
        need(not directory.exists(), "patched_engine_fresh_output_required")
        directory.mkdir(mode=0o700)
    with stage_lock(directory):
        if not finalize:
            require_active(directory)
            need(not (directory / (stage + ".completed.json")).exists(), "patched_engine_existing_stage_reconcile_only")
        audit_module = refresh.infra("document_consumed_native")
        clients, http = ParentProbe(profile).Clients(read_only_release=finalize), None
        try:
            if finalize:
                from infra.gcp.document_refresh_auth import ReleaseReadOnlyHttp

                http = ReleaseReadOnlyHttp()
            else:
                http = refresh.native_http()
            audit = audit_module.NativeAudit(identities.ROOT, consumed_history_bundle,
                clients, http, ParentProbe(profile), recover_expired_source=True)
            context = Applications(identities.ROOT, clients, audit.get_json, audit_module.apps.UIDS, profile, restored)
            context.stage_directory = directory
            audit.applications = context
            if finalize:
                return finalize_build(directory, application_context=context)
            if operator_review is not None:
                restoration._private_read(operator_review, operator_review_sha256)
                refresh.write_new(directory / (stage + ".operator-review.json"), {
                    "path": str(operator_review), "sha256": operator_review_sha256,
                    "scope": "operator_supplied_reference_only", "provider_approval_asserted": False})
            if stage == "build":
                required = audit_module.history.required(history)
                need(not required or consumed_history_bundle is not None and consumed_history_bundle.is_dir(),
                     "patched_engine_consumed_history_required")
                if required:
                    audit_module.history.load_bundle(consumed_history_bundle)
                    disposition = audit_module.history.audit(consumed_history_bundle, audit)
                    refresh.write_new(directory / "consumed-history-audit.json", disposition.report())
                else:
                    disposition = None
                    original = audit.verify_current_applications()
                    audit.verify_current_document(None)
                    need(audit.verify_current_applications() == original, "patched_engine_app_preimage_changed")
                return refresh.refresh(directory, publish=True, now=datetime.now(UTC), history=history,
                    application_context=context, consumed_history=disposition, recover_expired_source=True,
                    full_source=True, source_revision=True, source_patched_engine=True)
            build = load_staged_build(directory, context)
            target = context.source_probe(build, root=identities.ROOT)
            context.before_refresh()
            if stage in {"inventory", "corpus"}:
                if stage == "corpus":
                    previous = patched._phase_read(directory / "inventory.completed.json")
                    need(previous.get("completed_stage") == "inventory" and previous.get("next_stage_started") is False
                         and previous.get("build_identity_sha256") == source.corpus.sha(build),
                         "patched_engine_inventory_stage_incomplete")
                native = patched.run_candidate(target, directory / "native-proof", stage=stage,
                    require_active=context.require_active)
                result = {"completed_stage": stage, "next_stage_required": "corpus" if stage == "inventory" else "promote",
                    "build_identity_sha256": source.corpus.sha(build), "native_phase": native,
                    "operator_cost_review_required": stage == "inventory", "next_stage_started": False,
                    "promotion_performed": False}
            else:
                previous = patched._phase_read(directory / "corpus.completed.json")
                need(previous.get("completed_stage") == "corpus" and previous.get("next_stage_started") is False
                     and previous.get("build_identity_sha256") == source.corpus.sha(build),
                     "patched_engine_corpus_stage_incomplete")
                result = promote(build, directory / "native-proof", directory / "transition", application_context=context)
                result.update(completed_stage="promote", next_stage_required="separate_application_release",
                              next_stage_started=False)
            refresh.write_new(directory / (stage + ".completed.json"), result)
            return result
        finally:
            clients.close()
            if http is not None:
                http.close()


def main():
    import argparse
    import json

    from infra.gcp import schema27_forward_diagnostics as diagnostics

    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("stage", choices=STAGES)
    parser.add_argument("--identity-profile", type=Path, required=True)
    parser.add_argument("--identity-profile-sha256", required=True)
    parser.add_argument("--restore-plan", type=Path, required=True)
    parser.add_argument("--restore-plan-sha256", required=True)
    parser.add_argument("--restore-journal", type=Path, required=True)
    parser.add_argument("--admission-closure-plan", type=Path, required=True)
    parser.add_argument("--admission-closure-plan-sha256", required=True)
    parser.add_argument("--admission-closure-journal", type=Path, required=True)
    parser.add_argument("--admission-closure-completed", action="store_true",
        help="Use the separately owned coordinator completion proof after a definite original rejection.")
    parser.add_argument("--prior-history-directory", type=Path, required=True)
    parser.add_argument("--consumed-history-bundle", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--publish-candidate", action="store_true", required=True)
    parser.add_argument("--operator-review-file", type=Path)
    parser.add_argument("--operator-review-file-sha256")
    parser.add_argument("--finalize-build", action="store_true", help="Read-only native continuation of this output root; never submit another Build.")
    args = parser.parse_args()
    try:
        read = identities.app.bridge.bridge.app26.initial._read_pinned
        profile = identities.Profile.from_dict(rolling.release._json(read(
            args.identity_profile, args.identity_profile_sha256)))
        profile.verify_custody()
        from infra.gcp import schema27_native_admission_closure as admission

        if args.admission_closure_completed:
            from infra.gcp import schema27_native_admission_completion as completion

            closed = completion.load_completed(plan_path=args.admission_closure_plan,
                plan_sha256=args.admission_closure_plan_sha256, journal_path=args.admission_closure_journal)
        else:
            closed = admission.load_closed(plan_path=args.admission_closure_plan,
                plan_sha256=args.admission_closure_plan_sha256, journal_path=args.admission_closure_journal)
        restored = load_historical_restore(plan_path=args.restore_plan, plan_sha256=args.restore_plan_sha256,
            journal_path=args.restore_journal, profile=profile, closed=closed)
        result = run(args.output_directory, args.prior_history_directory, args.consumed_history_bundle, profile, restored,
            stage=args.stage, operator_review=args.operator_review_file, operator_review_sha256=args.operator_review_file_sha256,
            finalize=args.finalize_build)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - emit only a fixed redacted diagnostic.
        print(json.dumps(diagnostics.diagnostic(error, "refresh")))
        return 1


if __name__ == "__main__":
    from infra.gcp import schema27_patched_engine_recovery as operator

    raise SystemExit(operator.main())
