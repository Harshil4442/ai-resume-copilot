"""Application-only Source27 journal custody; no schema26 or migration authority."""
from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from infra.gcp import schema27_app_source as source27
from infra.gcp import schema27_continuity_bridge_plan as bridge
from infra.gcp import schema27_continuity_scope as scope

from scripts import notification_schema27_contract as contract

rolling, successor, historical = bridge.rolling, bridge.successor, bridge.historical
KIND = "hirewiz_schema27_app_successor_v1"
SCHEMA = contract.SCHEMA


def need(value, reason):
    rolling.need(value, "schema27_app_" + reason)


@dataclass(frozen=True, slots=True)
class App27Plan:
    source: successor.SourcePin
    prior_source: successor.SourcePin
    source_contract: source27.QualifiedSource
    prior_contract: source27.QualifiedSource
    controller_release: str
    controller_source_sha256: str
    controller_ci_run_id: int
    nonce: str
    database: scope.ContinuityDatabasePin
    runtime_uid: str
    workers_release: str
    financial_sha256: str
    retained_acl_sha256: str
    policies: rolling.PolicyPins
    services: tuple[rolling.ServicePin, ...]
    resources: tuple[historical.ResourcePin, ...]
    protected_liabilities: tuple[tuple[str, str, int], ...]
    reachable_revisions: tuple[historical.ReachablePin, ...]
    schema_contract: str = KIND
    database_schema: str = SCHEMA

    image = rolling.RollingPlan.image
    tag = rolling.RollingPlan.tag
    rollback_tag = rolling.RollingPlan.rollback_tag
    revision = rolling.RollingPlan.revision
    pin = rolling.RollingPlan.pin
    promoted_aliases = bridge.app26.App26V3Plan.promoted_aliases
    _validate_service = bridge.ContinuityBridgePlan._validate_service

    @property
    def release(self):
        return self.source.release

    @property
    def image_digest(self):
        return self.source.image_digest

    @property
    def runtime_secret(self):
        return self.database.runtime_reference

    @property
    def candidate_policies(self):
        return None

    @property
    def candidate_employer_cache(self):
        return None

    def target_policies(self):
        return self.policies

    def disabled_flags(self):
        return (*bridge.app26.CLOSED_FLAGS, "NOTIFICATION_DIGESTS_ENABLED", "LIFECYCLE_EMAILS_ENABLED")

    def validate(self):
        need(type(self) is App27Plan and self.schema_contract == KIND and self.database_schema == SCHEMA,
             "plan_kind")
        need(type(self.database) is scope.ContinuityDatabasePin and scope._nonce(self.nonce), "database_scope")
        self.database.validate()
        for source, qualified in ((self.source, self.source_contract), (self.prior_source, self.prior_contract)):
            need(type(source) is successor.SourcePin and type(qualified) is source27.QualifiedSource, "source_kind")
            source.validate()
            qualified.validate()
            need(source.release == qualified.release, "source_contract")
        need(self.release != self.prior_source.release
            and type(self.controller_release) is str and re.fullmatch(r"[a-f0-9]{40}", self.controller_release)
            and type(self.workers_release) is str and re.fullmatch(r"[a-f0-9]{40}", self.workers_release)
            and type(self.runtime_uid) is str and re.fullmatch(r"[1-9][0-9]{20}", self.runtime_uid)
            and all(scope._hash(value) for value in
                    (self.controller_source_sha256, self.financial_sha256, self.retained_acl_sha256))
            and type(self.controller_ci_run_id) is int and self.controller_ci_run_id > 0, "release_pins")
        bridge._validate_policies(self.policies)
        need(type(self.services) is tuple and all(type(pin) is rolling.ServicePin for pin in self.services)
            and tuple(pin.service for pin in self.services) == rolling.release.SERVICES, "service_scope")
        for pin in self.services:
            self._validate_service(pin, None)
        need(all(type(value) is tuple for value in
                 (self.resources, self.protected_liabilities, self.reachable_revisions))
            and all(type(pin) is historical.ResourcePin for pin in self.resources)
            and all(type(pin) is historical.ReachablePin for pin in self.reachable_revisions)
            and all(type(row) is tuple and len(row) == 3 for row in self.protected_liabilities), "retained_pin_types")
        historical.validate_extensions(self)

    def custody(self):
        self.validate()
        metadata = asdict(self)
        del metadata["database"]
        # Existing continuity custody binds the physical target and complete NEW
        # plan metadata. Its distinct kind/schema/descriptors prevent adoption of
        # any old bridge journal; this does not grant bridge or DDL authority.
        return scope.BridgeCustody(self.nonce, rolling.sha(metadata), self.database)


def load(raw: bytes, expected_sha256: str) -> App27Plan:
    need(type(raw) is bytes and 0 < len(raw) <= bridge.MAX_PLAN_BYTES and scope._hash(expected_sha256)
         and hashlib.sha256(raw).hexdigest() == expected_sha256, "private_plan_pin")
    try:
        value = rolling.release._json(raw)
        need(type(value) is dict and value.get("schema_contract") == KIND
             and set(value) == {field.name for field in fields(App27Plan)}, "plan_kind")
        canonical = rolling.canonical(value)
        for key in ("source", "prior_source"):
            value[key] = successor.SourcePin(**value[key])
        for key in ("source_contract", "prior_contract"):
            value[key] = source27.QualifiedSource(**value[key])
        for key in ("runtime_role", "coordinator_role", "observer_role", "migrator_role"):
            value["database"][key] = tuple(value["database"][key])
        value["database"] = scope.ContinuityDatabasePin(**value["database"])
        value["policies"] = rolling.PolicyPins(**value["policies"])
        value["services"] = tuple(rolling.ServicePin(**(pin | {
            "revision_keys": tuple(pin["revision_keys"]), "traffic": tuple(pin["traffic"]),
            "secrets": tuple(rolling.SecretPin(**secret) for secret in pin["secrets"]),
        })) for pin in value["services"])
        value["resources"] = tuple(historical.ResourcePin(**pin) for pin in value["resources"])
        value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
        value["reachable_revisions"] = tuple(historical.ReachablePin(**pin) for pin in value["reachable_revisions"])
        result = App27Plan(**value)
        need(rolling.canonical(asdict(result)) == canonical, "canonical_plan_shape")
        result.validate()
        return result
    except rolling.RollingDenied:
        raise
    except (rolling.release.ReleaseDenied, AttributeError, KeyError, TypeError, ValueError, RecursionError):
        raise rolling.RollingDenied("schema27_app_plan_unavailable") from None


def bind(plan: App27Plan, directory: Path) -> rolling.Journal:
    need(type(plan) is App27Plan, "plan_kind")
    journal = rolling.Journal(directory, plan.custody())
    scope.verify_journal(plan.custody(), journal)
    return journal
