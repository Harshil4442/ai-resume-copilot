"""Explicit expired-V5 maintenance after an owned homogeneous app restore.

An expired predecessor proves immutable binary/corpus custody, never processing
or reopening authority. A definitions-only child still needs the stock fresh
local validation, both native Jobs and all 17 cases before any serving mutation.
This controller uses the genuine private API-identity promotion receipt; it does
not manufacture the separately committed PublishedProfile.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from infra.gcp import document_source_candidate_v5 as v5
from infra.gcp import document_source_release as source
from infra.gcp import schema27_mixed_document_proof as documents
from infra.gcp import schema27_mixed_identity_profile as identities
from infra.gcp import schema27_mixed_maintenance as maintenance

need = identities.need


@dataclass(frozen=True)
class ExpiredV5Predecessor(source.ExpiredSourcePredecessor):
    """Read-only structural evidence; remains ineligible as NativeAcceptance."""

    def report(self):
        return super().report() | {"purpose": "expired_v5_structural_predecessor"}


def selected(context):
    return type(context) is Applications


def predecessor(profile, image, metadata):
    """Re-read the exact serving V5 Build, Jobs, execution, logs, IAM and pins."""
    need(type(profile) is identities.Profile, "expired_v5_private_profile_required")
    profile.verify_custody()
    probe = documents.current_probe(profile, image, metadata)
    need(probe._v5 and probe.CONTRACT_VERSION == 5 and probe.REQUIRED_CASE_COUNT == 17
         and probe.POLICY == v5.POLICY_SHA256, "expired_v5_parent_profile")
    # monitor=True retains every structural/native check while returning a
    # distinct non-acceptance value only after the three-day window has expired.
    observed = probe._verify_current_source(image, metadata, monitor=True)
    need(type(observed) is source.ExpiredSourcePredecessor
         and observed.contract_version == 5 and observed.required_case_count == 17
         and observed.policy_sha256 == v5.POLICY_SHA256 and observed.image == image,
         "expired_v5_structural_predecessor_required")
    return ExpiredV5Predecessor(**asdict(observed))


def historical_component(probe, clients):
    """Compare old custody without creating a fresh or expired NativeAcceptance.

    Native Build/Job/execution/log checks remain unchanged. This plain mapping
    can match the old journal, but cannot enter require_current_acceptance.
    """
    need(type(probe) is source.SourceProbe and probe._v5 and probe.CONTRACT_VERSION == 5
         and probe.REQUIRED_CASE_COUNT == 17 and probe.POLICY == v5.POLICY_SHA256,
         "expired_v5_historical_component_scope")
    job, execution, _inventory, value, stamp = probe._verified_evidence(clients)
    build = probe.build
    return {"image": build["immutable_image"], "build_id": build["build_id"],
        "build_resource": build["build_resource"], "source_archive_sha256": build["source_archive_sha256"],
        "refresh_dockerfile_sha256": build["refresh_dockerfile_sha256"], "source_sha256": build["source_sha256"],
        "job_name": job["name"], "job_uid": job["uid"], "execution_name": execution["name"],
        "execution_uid": execution["uid"],
        "probe_sha256": hashlib.sha256(probe._rendered(build["immutable_image"]).encode()).hexdigest(),
        "receipt_sha256": source.corpus.sha(value), "policy_sha256": probe.POLICY,
        "font_manifest_sha256": source.courier.FONT, "renderer_version": source.courier.RENDERER,
        "definition_expires_at": (stamp + timedelta(days=3)).isoformat(),
        "contract_version": 5, "required_case_count": 17}


def promote(build, native_journal, transition_journal, *, application_context, root=source.profile.ROOT):
    """New typed orchestration; the promotion owner's frozen helper is unchanged."""
    need(selected(application_context) and type(build) is dict and build.get("kind") == v5.DEFINITIONS_KIND
         and build.get("policy_sha256") == v5.POLICY_SHA256 and not transition_journal.exists(),
         "expired_v5_fresh_definitions_transition_required")
    application_context.restored.verify_custody()
    probe = application_context.source_probe(build, root=root)
    if native_journal.exists():
        accepted = probe.verify_successor(build["immutable_image"], build, native_journal)
    else:
        accepted = documents.run_candidate(probe, native_journal)
    source.transition.require_current_acceptance(accepted, build["immutable_image"], probe)
    # Use the same exclusive once marker and LRO controller. This new module is
    # outside the predecessor identity promotion's immutable helper manifest.
    source.prior.build_module().write_new(native_journal / "transition.started.json", {
        "build_identity_sha256": source.corpus.sha(build), "transition_journal": str(transition_journal),
        "automatic_replay": False, "unknown_outcome": "read_only_reconcile_exact_transition_only"})
    http = source.prior.build_module().native_http()
    try:
        result = source.transition.transition(build["immutable_image"], build, native_journal, transition_journal,
            http=http, probe=probe, application_context=application_context, recover_expired_source=True)
    finally:
        http.close()
    return result | {"source_commit": build["source_commit"], "renderer_contract_version": probe.CONTRACT_VERSION,
        "required_case_count": probe.REQUIRED_CASE_COUNT, "definition_expires_at": accepted.definition_expires_at,
        "prior_evidence_retained": True, "prior_jobs_retained": True}


class Probe(maintenance.Probe):
    def __init__(self, profile):
        need(type(profile) is identities.Profile, "expired_v5_private_profile_required")
        self.identity_profile = profile

    def verify_expired_current_source_predecessor(self, image, metadata):
        return predecessor(self.identity_profile, image, metadata)


class Applications(maintenance.Applications):
    """Exact private-profile recovery context; construction requires owned restore."""

    def __init__(self, root, clients, get_json, uids, profile, restored):
        from infra.gcp import schema27_staged_restore as restoration

        need(type(profile) is identities.Profile and type(restored) is restoration.VerifiedRestore
             and restored.profile == profile, "expired_v5_owned_restore_required")
        profile.verify_custody()
        restored.verify_custody()
        restored.verify_current()
        maintenance.applications.Applications.__init__(self, root, clients, get_json, uids)
        self.identity_profile, self.probe, self.restored = profile, Probe(profile), restored
        self.last_authorization = None
        need(all(uids[name] == profile.promotion.before["services"][name]["uid"]
                 for name in (identities.API, "hirewiz-analysis-worker")), "maintenance_service_uid")
        pin = restored.application_plan.prior_source
        prior = maintenance.applications.Source(pin.release, pin.image_digest, pin.build_id, pin.ci_run_id)
        prior.validate()
        need(prior.release == "70a1ef77c19c7f00570bc159b3db514b38d112d0"
             and restored.application_plan.release == "3f7fd47738b336e4cbfdfdff2f6fdc13adca74c6",
             "expired_v5_restored_source_scope")
        need(prior.release not in self.registry or self.registry[prior.release] == prior,
             "expired_v5_restored_source_changed")
        self.registry[prior.release] = prior

    def account(self, name):
        return self.identity_profile.identity(name).email if name == identities.API else maintenance.applications.ACCOUNTS[name]

    def before_refresh(self):
        self.restored.verify_custody()
        self.restored.verify_current()
        return self.observe()

    def observe(self):
        self.restored.verify_custody()
        return super().observe()

    def prewrite_identity(self):
        self.restored.verify_custody()
        return super().prewrite_identity()

    def verify_parent(self, image, current):
        clients = self.probe.Clients()
        try:
            metadata = clients.preflight()["service"]
            need(metadata["template"]["containers"][0]["image"] == image, "maintenance_parent_changed")
            observed = self.probe.verify_expired_current_source_predecessor(image, metadata)
            need(observed.policy_sha256 == current.policy and observed.source_sha256 == current.sources
                 and observed.contract_version == 5 and observed.required_case_count == 17,
                 "maintenance_parent_profile")
            return observed
        finally:
            clients.close()


def run(directory, history, consumed_history_bundle, profile, restored):
    """Only the owned homogeneous restore may enter this explicit continuation."""
    from scripts import document_refresh as refresh

    need(Path(refresh.__file__).resolve() == identities.ROOT / "backend/scripts/document_refresh.py",
         "maintenance_controller_origin")
    need(directory.is_absolute() and not directory.exists() and directory.parent.is_dir()
         and directory.resolve() == directory, "expired_v5_fresh_output_required")
    required = refresh.infra("document_consumed_native").history.required(history)
    need(not required or consumed_history_bundle is not None and consumed_history_bundle.is_dir(),
         "expired_v5_consumed_history_required")
    if required:
        refresh.infra("document_consumed_native").history.load_bundle(consumed_history_bundle)
    directory.mkdir(mode=0o700)
    clients, http = Probe(profile).Clients(), None
    try:
        http = refresh.native_http()
        audit_module = refresh.infra("document_consumed_native")
        boundary = audit_module.NativeAudit(identities.ROOT, consumed_history_bundle if required else None,
            clients, http, Probe(profile), recover_expired_source=True)
        context = Applications(identities.ROOT, clients, boundary.get_json, audit_module.apps.UIDS, profile, restored)
        boundary.applications = context
        if required:
            disposition = audit_module.history.audit(consumed_history_bundle, boundary)
            refresh.write_new(directory / "consumed-history-audit.json", disposition.report())
        else:
            disposition = None
            original = boundary.verify_current_applications()
            boundary.verify_current_document(None)
            need(boundary.verify_current_applications() == original, "expired_v5_app_preimage_changed")
        result = refresh.refresh(directory, publish=True, now=datetime.now(UTC), history=history,
            application_context=context, consumed_history=disposition, recover_expired_source=True)
        refresh.write_new(directory / "result.json", result)
        refresh.emit_status(result)
        return result
    finally:
        clients.close()
        if http is not None:
            http.close()


def main():
    import argparse

    from infra.gcp import schema27_forward_diagnostics as diagnostics
    from infra.gcp import schema27_staged_restore as restoration

    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--identity-profile", type=Path, required=True)
    parser.add_argument("--identity-profile-sha256", required=True)
    parser.add_argument("--restore-plan", type=Path, required=True)
    parser.add_argument("--restore-plan-sha256", required=True)
    parser.add_argument("--restore-journal", type=Path, required=True)
    parser.add_argument("--prior-history-directory", type=Path, required=True)
    parser.add_argument("--consumed-history-bundle", type=Path)
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--publish-candidate", action="store_true", required=True)
    args = parser.parse_args()
    try:
        read = identities.app.bridge.bridge.app26.initial._read_pinned
        raw = read(args.identity_profile, args.identity_profile_sha256)
        profile = identities.Profile.from_dict(identities.rolling.release._json(raw))
        profile.verify_custody()
        restored = restoration.load_verified_restore(plan_path=args.restore_plan,
            plan_sha256=args.restore_plan_sha256, journal_path=args.restore_journal, profile=profile)
        result = run(args.output_directory, args.prior_history_directory, args.consumed_history_bundle, profile, restored)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as error:  # noqa: BLE001 - expose only the redacted fixed diagnostic.
        print(json.dumps(diagnostics.diagnostic(error, "refresh")))
        return 1


if __name__ == "__main__":
    # -m creates a second class identity. The entrypoint must construct the
    # canonical exact context used by every downstream type/authority guard.
    from infra.gcp import schema27_expired_v5_recovery as operator

    raise SystemExit(operator.main())
