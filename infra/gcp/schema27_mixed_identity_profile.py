"""Explicit post27 identity profile; historical shared-account defaults stay exact.

An already verified promotion journal supplies identity custody, not application
release or worker acceptance. Each new controller still verifies its own source,
actual app images, native identities, document proof, database and policies.
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from infra.gcp import schema27_api_identity_operator as rehearsal
from infra.gcp import schema27_api_identity_promotion as promotion
from infra.gcp import schema27_api_identity_readback as readback
from infra.gcp import schema27_app_successor as app

rolling, API = rehearsal.rolling, rehearsal.API
KIND = "hirewiz_schema27_mixed_identity_profile_v1"
MAINTENANCE_KIND = "hirewiz_schema27_published_maintenance_identity_v1"
ROOT = Path(__file__).resolve().parents[2]
PUBLICATION = "infra/document-processing/reviewed-api-identity.json"


def need(value, reason):
    rolling.need(value, "schema27_mixed_identity_" + reason)


@dataclass(frozen=True)
class Profile:
    promotion: promotion.Plan
    receipt_sha256: str
    kind: str = KIND

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @property
    def transition(self):
        return self.promotion.transition

    @classmethod
    def from_dict(cls, value):
        need(type(value) is dict and set(value) == {field.name for field in fields(cls)}, "profile_shape")
        result = cls(**(value | {"promotion": promotion.Plan.from_dict(value["promotion"])}))
        result.validate()
        return result

    def validate(self):
        need(type(self) is Profile and self.kind == KIND and type(self.promotion) is promotion.Plan
             and readback._hash(self.receipt_sha256), "profile_kind")
        self.promotion.validate()

    def verify_custody(self):
        self.validate()
        # The later app controller has its own source. Check the unchanged
        # predecessor interpreter's code bytes, never relabel its old root/HEAD.
        need(hashlib.sha256(Path(promotion.__file__).read_bytes()).hexdigest()
             == self.promotion.controller["promotion_sha256"], "promotion_code_changed")
        need(all(hashlib.sha256((promotion.ROOT / name).read_bytes()).hexdigest() == stamp
                 for name, stamp in self.promotion.controller["helpers_sha256"].items()), "promotion_helper_changed")
        self.promotion.verify_predecessor()
        journal = rehearsal.Journal(Path(self.promotion.journal_location["path"]), self.promotion)
        with journal.locked():
            need(not any(journal.claimed(phase + ".started") for phase in promotion.PHASES[1:]),
                 "identity_rollback_started")
            receipt = promotion.verify_receipt(self.promotion, journal, "promote")
            need(rolling.sha(receipt) == self.receipt_sha256, "promotion_receipt_changed")
        return receipt

    def identity(self, service):
        need(service in readback.rolling.release.SERVICES, "service_scope")
        return self.transition.new_api if service == API else self.transition.old_api

    def verify_principals(self, reader):
        self.verify_custody()
        for identity in (self.transition.old_api, self.transition.new_api):
            need(identity.matches(reader(identity.email)), "principal_changed")

    def legacy_policy_component(self, name, actual):
        """Validate the full actual delta, then isolate the unchanged worker proof.

        This projection is used only inside historical worker configuration checks.
        No UID, native service, worker receipt, source or acceptance is rewritten.
        """
        need(name in readback.DOCUMENTS and type(actual) is dict, "document_scope")
        before = self.promotion.before["callers"]
        target = copy.deepcopy(self.promotion.handoff["callers"])
        target[name] = actual
        readback._validate_callers(self.transition, before, target)
        result = copy.deepcopy(actual)
        new = "serviceAccount:" + self.transition.new_api.email
        removed = 0
        for row in result["bindings"]:
            if row["role"] == "roles/run.invoker" and "condition" not in row and new in row["members"]:
                row["members"].remove(new)
                removed += 1
        need(removed == 1, "exact_document_caller_delta")
        return result


def from_promoted(plan, journal):
    # A -m entrypoint has a distinct __main__ class identity. Reparse the exact
    # versioned metadata, preserving its digest and every native/source pin.
    parsed = promotion.Plan.from_dict(asdict(plan))
    need(parsed.digest == plan.digest, "promotion_plan_changed")
    plan = parsed
    rehearsal._guard(plan, journal)
    need(not any(journal.claimed(phase + ".started") for phase in promotion.PHASES[1:]), "identity_rollback_started")
    result = Profile(plan, rolling.sha(promotion.verify_receipt(plan, journal, "promote")))
    result.verify_custody()
    return result


def policy_content(policy):
    need(type(policy) is dict and set(policy) <= {"version", "etag", "bindings"}, "document_policy_shape")
    rows = policy.get("bindings")
    need(type(rows) is list and all(type(row) is dict and type(row.get("members")) is list
         and all(type(member) is str for member in row["members"])
         and len(row["members"]) == len(set(row["members"])) for row in rows), "document_policy_shape")
    return {key: value for key, value in policy.items() if key not in {"etag", "bindings"}} | {
        "bindings": sorted([row | {"members": sorted(row["members"])} for row in rows], key=rolling.canonical)}


@dataclass(frozen=True)
class PublishedProfile:
    """Committed maintenance-only pins; no private journal or mutation ownership."""
    shared: readback.Identity
    api: readback.Identity
    service_uids: dict
    document_before_sha256: dict
    document_after_sha256: dict
    caller_delta: dict
    promotion_source: dict
    kind: str = MAINTENANCE_KIND
    native_approval: bool = False

    @property
    def digest(self):
        return rolling.sha(asdict(self))

    @classmethod
    def from_dict(cls, value):
        need(type(value) is dict and set(value) == {field.name for field in fields(cls)}, "published_profile_shape")
        result = cls(**(value | {"shared": readback.Identity(**value["shared"]), "api": readback.Identity(**value["api"])}))
        result.validate()
        return result

    def validate(self):
        import re

        need(type(self) is PublishedProfile and self.kind == MAINTENANCE_KIND and self.native_approval is False
             and self.shared.email == rolling.RUNTIME and self.api.email == rehearsal.NEW_API_EMAIL
             and self.shared.uid != self.api.uid and all(type(item.uid) is str and re.fullmatch(r"[1-9][0-9]{20}", item.uid)
                 for item in (self.shared, self.api)), "published_identity_pin")
        need(set(self.service_uids) == set(readback.SERVICES)
             and self.service_uids[API] == readback.application.UIDS[API]
             and all(type(uid) is str and re.fullmatch(r"[a-f0-9-]{36}", uid) for uid in self.service_uids.values()),
             "published_service_pins")
        for pins in (self.document_before_sha256, self.document_after_sha256):
            need(type(pins) is dict and set(pins) == set(readback.DOCUMENTS)
                 and all(readback._hash(stamp) for stamp in pins.values()), "published_policy_pins")
        expected = {name: {"role": "roles/run.invoker", "add_member": "serviceAccount:" + self.api.email,
                           "condition": None} for name in readback.DOCUMENTS}
        need(self.caller_delta == expected and type(self.promotion_source) is dict
             and set(self.promotion_source) == {"release", "promotion_code_sha256", "plan_sha256", "receipt_sha256"}
             and type(self.promotion_source["release"]) is str
             and re.fullmatch(r"[a-f0-9]{40}", self.promotion_source["release"])
             and all(readback._hash(self.promotion_source[key]) for key in
                     ("promotion_code_sha256", "plan_sha256", "receipt_sha256")), "published_source_delta")

    def verify_custody(self):
        self.validate()
        path = ROOT / PUBLICATION
        need(path.is_file() and not path.is_symlink() and path.resolve() == path
             and 0 < path.stat().st_size <= 16384, "published_committed_profile")
        raw = path.read_bytes()
        need(PublishedProfile.from_dict(rolling.release._json(raw)) == self, "published_profile_changed")
        from scripts import schema26_app_successor as source

        head = source._git(ROOT, "rev-parse", "HEAD").decode().strip()
        need(source._git(ROOT, "show", head + ":" + PUBLICATION) == raw, "published_uncommitted_profile")
        source._git(ROOT, "merge-base", "--is-ancestor", self.promotion_source["release"], head)
        need(hashlib.sha256(Path(promotion.__file__).read_bytes()).hexdigest()
             == self.promotion_source["promotion_code_sha256"], "published_promotion_code_changed")

    def identity(self, service):
        need(service in readback.rolling.release.SERVICES, "service_scope")
        return self.api if service == API else self.shared

    def verify_principals(self, reader):
        self.verify_custody()
        for identity in (self.shared, self.api):
            need(identity.matches(reader(identity.email)), "principal_changed")

    def legacy_policy_component(self, name, actual):
        self.validate()
        need(name in readback.DOCUMENTS and rolling.sha(policy_content(actual)) == self.document_after_sha256[name],
             "published_actual_policy_changed")
        result, removed = copy.deepcopy(actual), 0
        member = self.caller_delta[name]["add_member"]
        for row in result["bindings"]:
            if row["role"] == "roles/run.invoker" and "condition" not in row and member in row["members"]:
                need(row["members"].count(member) == 1, "published_duplicate_caller")
                row["members"].remove(member)
                removed += 1
        need(removed == 1 and rolling.sha(policy_content(result)) == self.document_before_sha256[name],
             "published_exact_caller_delta")
        return result


def public_export(profile):
    """Only safe IDs/digests/delta leave the verified private owner journal."""
    need(type(profile) is Profile, "private_owner_required")
    profile.verify_custody()
    plan = profile.promotion
    result = PublishedProfile(profile.transition.old_api, profile.transition.new_api,
        {name: value["uid"] for name, value in plan.before["services"].items()},
        {name: rolling.sha(policy_content(plan.before["callers"][name])) for name in readback.DOCUMENTS},
        {name: rolling.sha(policy_content(plan.handoff["callers"][name])) for name in readback.DOCUMENTS},
        {name: {"role": "roles/run.invoker", "add_member": "serviceAccount:" + profile.transition.new_api.email,
                "condition": None} for name in readback.DOCUMENTS},
        {"release": plan.controller["release"], "promotion_code_sha256": plan.controller["promotion_sha256"],
         "plan_sha256": plan.digest, "receipt_sha256": profile.receipt_sha256})
    result.validate()
    return result


def published():
    path = ROOT / PUBLICATION
    need(path.is_file() and path.stat().st_size <= 16384, "published_committed_profile")
    profile = PublishedProfile.from_dict(rolling.release._json(path.read_bytes()))
    profile.verify_custody()
    return profile


def read_identity(observer, email):
    need(email in {rolling.RUNTIME, rehearsal.NEW_API_EMAIL}, "principal_scope")
    account = observer._cloud("iam", "service-accounts", "describe", email)
    keys = observer._cloud("iam", "service-accounts", "keys", "list", "--iam-account", email)
    return readback.project_identity(account, keys)


@dataclass(frozen=True)
class StagedProfile:
    """Read-only authorization view before promotion; no promotion/publication authority."""
    promotion: promotion.Plan
    kind: str = "hirewiz_schema27_staged_identity_read_profile_v1"

    @property
    def digest(self):
        return rolling.sha({"kind": self.kind, "promotion_plan_sha256": self.promotion.digest})

    @property
    def transition(self):
        return self.promotion.transition

    def validate(self):
        need(type(self) is StagedProfile and type(self.promotion) is promotion.Plan
             and self.kind == "hirewiz_schema27_staged_identity_read_profile_v1", "staged_read_profile_kind")
        self.promotion.validate()

    def verify_custody(self):
        self.validate()
        self.promotion.verify_source()
        rehearsal.Journal(Path(self.promotion.journal_location["path"]), self.promotion).verify()

    def verify_principals(self, reader):
        self.verify_custody()
        for identity in (self.transition.old_api, self.transition.new_api):
            need(identity.matches(reader(identity.email)), "principal_changed")

    legacy_policy_component = Profile.legacy_policy_component


class MixedSourceBoundary(app.Source27Boundary):
    """Actual caller delta plus the unchanged full historical worker component."""
    def __init__(self, root, identity_profile):
        super().__init__(root)
        need(type(identity_profile) is Profile, "profile_kind")
        self.identity_profile = identity_profile

    def verify_document_configuration(self, source):
        from infra.gcp import schema27_mixed_document_proof

        result = schema27_mixed_document_proof.verify_document_profile(self, source, self.identity_profile)
        self.last_document_authorization = result
        return result
