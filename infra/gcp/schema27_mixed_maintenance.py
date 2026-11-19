"""Opt-in current maintenance on the published dedicated-API identity profile.

Existing schedules, budgets, native Jobs and transition holds are unchanged.
No published profile is installed by this implementation. This module never
promotes an identity/app revision, changes IAM or activates a payment feature.
"""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from infra.gcp import schema27_mixed_identity_profile as identities

# Bootstrap the retained controller's fixed module path before its bare imports.
# isort: split
from infra.gcp import document_application_context as applications
from infra.gcp import document_refresh_probe as prior
from infra.gcp import schema27_mixed_document_proof as documents

rolling, need = identities.rolling, identities.need


def selected(context):
    return type(context) is Applications


class Probe:
    """Fixed forward readers; all unchanged binary protocol methods stay stock."""
    def __init__(self, profile):
        need(type(profile) is identities.PublishedProfile, "published_profile_required")
        self.identity_profile = profile

    def __getattr__(self, name):
        return getattr(prior, name)

    def Clients(self, *, read_only_release=False):
        return documents.Clients(self.identity_profile, read_only_release=read_only_release)

    def verify_current_successor(self, image, metadata):
        return documents.current_probe(self.identity_profile, image, metadata).verify_current_successor(image, metadata)

    def verify_caller_pins(self, clients, image, *, policy_sha256=None, caller_serving_validator=None):
        metadata = clients.service(prior.PARENT + "/services/hirewiz-document-inspector")
        return documents.current_probe(self.identity_profile, image, metadata).verify_caller_pins(
            clients, image, policy_sha256=policy_sha256, caller_serving_validator=caller_serving_validator)


class Applications(applications.Applications):
    def __init__(self, root, clients, get_json, uids, profile):
        need(type(profile) is identities.PublishedProfile, "published_profile_required")
        profile.verify_custody()
        super().__init__(root, clients, get_json, uids)
        self.identity_profile, self.probe = profile, Probe(profile)
        self.last_authorization = None
        need(all(uids[name] == profile.service_uids[name] for name in applications.CALLERS), "maintenance_service_uid")

    def account(self, name):
        return self.identity_profile.api.email if name == identities.API else applications.ACCOUNTS[name]

    def observe(self):
        self.identity_profile.verify_principals(self.clients.identity)
        return super().observe()

    def serving(self, clients, value, image, name, uid=None):
        # Preserve the existing source/CI/Build checks while selecting the exact
        # new API account. A staged mixed app/identity cannot start maintenance.
        need(uid is None or uid == self.uids[name], "maintenance_service_uid")
        release = rolling.environment(value["template"]).get("APP_RELEASE", {}).get("value")
        need(release in self.registry and self.registry[release].image == image
             and value["template"].get("labels", {}).get("hirewiz-source") == release,
             "maintenance_source_changed")
        self.provenance(self.registry[release])
        applications.ready(value, name)
        need(value["uid"] == self.uids[name] and value["template"].get("serviceAccount") == self.account(name),
             "maintenance_caller_identity")
        expected = applications.template(value["template"])
        traffic = applications.allocations(name, value)
        active = next(row["revision"] for row in traffic if row.get("percent") == 100)
        created = rolling.short_revision(value["latestCreatedRevision"], name)
        result = None
        for revision in sorted({created} | {row["revision"] for row in traffic}):
            row = clients.revision(prior.PARENT + "/services/" + name + "/revisions/" + revision)
            need(row.get("name") == prior.PARENT + "/services/" + name + "/revisions/" + revision
                 and row.get("service") in {name, prior.PARENT + "/services/" + name}
                 and type(row.get("uid")) is str and prior.UUID.fullmatch(row["uid"])
                 and any(condition.get("type") == "Ready" and condition.get("state") == "CONDITION_SUCCEEDED"
                         for condition in row.get("conditions", []))
                 and applications.template(row, revision=True) == expected,
                 "maintenance_transition_in_progress_or_unknown_alias")
            if revision == active:
                result = row
        need(result is not None, "maintenance_physical_revision")
        return result

    def before_refresh(self):
        # This read-only join precedes any new Build/Job. The existing native
        # scheduling and funding holds still decide whether execution may start.
        return self.observe()

    def prewrite_identity(self):
        clients = self.probe.Clients()
        try:
            self.identity_profile.verify_principals(clients.identity)
        finally:
            clients.close()

    def source_probe(self, build, *, root):
        return documents.SourceProbe(build, self.identity_profile, root=root)

    def verify_parent(self, image, current):
        clients = self.probe.Clients()
        try:
            metadata = clients.preflight()["service"]
            need(metadata["template"]["containers"][0]["image"] == image, "maintenance_parent_changed")
            probe = documents.current_probe(self.identity_profile, image, metadata)
            binary = probe.verify_current_successor(image, metadata)
            need(binary.policy_sha256 == current.policy and binary.source_sha256 == current.sources
                 and binary.contract_version == 5 and binary.required_case_count == 17,
                 "maintenance_parent_profile")
            return binary
        finally:
            clients.close()

    def authorization(self, binary):
        clients = self.probe.Clients()
        try:
            worker = clients.preflight()["service"]
            policies = {name: clients.document_policy(name) for name in identities.readback.DOCUMENTS}
            for name, policy in policies.items():
                self.identity_profile.legacy_policy_component(name, policy)
            latest = {name: clients.document_policy(name) for name in identities.readback.DOCUMENTS}
            need(latest == policies and clients.service(prior.PARENT + "/services/hirewiz-document-inspector") == worker,
                 "maintenance_authorization_changed")
            result = {"kind": documents.KIND, "identity_profile_sha256": self.identity_profile.digest,
                "actual_policies": copy.deepcopy(policies), "actual_policy_sha256": rolling.sha(policies),
                "actual_etags": {name: identities.rehearsal._etag(value) for name, value in policies.items()},
                "caller_authorization_verified": True,
                "binary_corpus_component": {"scope": "retained_document_binary_and_corpus", "acceptance": asdict(binary),
                                           "sha256": rolling.sha(asdict(binary))},
                "legacy_policy_components_sha256": {name: rolling.sha(self.identity_profile.legacy_policy_component(name, value))
                    for name, value in policies.items()}, "native_approval": False}
            self.last_authorization = documents.validate_receipt(self.identity_profile, result)
            return self.last_authorization
        finally:
            clients.close()


def main():
    import argparse
    import json

    from infra.gcp import schema27_forward_diagnostics as diagnostics

    from scripts import document_refresh as refresh

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("refresh",))
    parser.add_argument("--output-directory", type=Path, required=True)
    parser.add_argument("--publish-candidate", action="store_true")
    parser.add_argument("--prior-native-journal", type=Path)
    parser.add_argument("--prior-history-directory", type=Path)
    parser.add_argument("--consumed-history-bundle", type=Path)
    args = parser.parse_args()
    try:
        need(Path(refresh.__file__).resolve() == identities.ROOT / "backend/scripts/document_refresh.py",
             "maintenance_controller_origin")
        identities.published()
        result = refresh.refresh(args.output_directory, publish=args.publish_candidate, now=datetime.now(UTC),
            prior_journal=args.prior_native_journal, history=args.prior_history_directory,
            reviewed_application_context=True, published_api_identity=True,
            consumed_history_bundle=args.consumed_history_bundle)
        refresh.emit_status(result)
        return 0
    except Exception as error:
        print(json.dumps(diagnostics.diagnostic(error, "refresh")))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
