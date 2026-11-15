"""Local shape and durable journal binding for the recovered Source27 bridge."""

from __future__ import annotations

import hashlib
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from infra.gcp import schema27_continuity_scope as scope

from scripts import schema26_app_successor as successor

rolling = scope.rolling
app26 = successor.app26
historical = app26.historical
DB_SCHEMA = successor.SCHEMA
MAX_PLAN_BYTES = 131072
HISTORICAL_DATABASE_SECRETS = ("hirewiz-fresh-observer-db-", "hirewiz-fresh-migrator-db-")


def _need(value, reason):
    rolling.need(value, "schema27_continuity_bridge_" + reason)


def _matches(pattern: str, value: str) -> bool:
    return type(value) is str and re.fullmatch(pattern, value) is not None


@dataclass(frozen=True, slots=True)
class ContinuityBridgePlan:
    source: successor.SourcePin
    prior_source: successor.SourcePin
    controller_release: str
    controller_source_sha256: str
    controller_ci_run_id: int
    nonce: str
    database: scope.ContinuityDatabasePin
    runtime_uid: str
    workers_release: str
    financial_sha256: str
    policies: rolling.PolicyPins
    services: tuple[rolling.ServicePin, ...]
    resources: tuple[historical.ResourcePin, ...]
    protected_liabilities: tuple[tuple[str, str, int], ...]
    reachable_revisions: tuple[historical.ReachablePin, ...]
    candidate_policies: rolling.PolicyPins | None = None
    candidate_employer_cache: rolling.EmployerCacheBinding | None = None
    schema_contract: str = scope.KIND
    database_schema: str = DB_SCHEMA

    image = rolling.RollingPlan.image
    tag = rolling.RollingPlan.tag
    rollback_tag = rolling.RollingPlan.rollback_tag
    revision = rolling.RollingPlan.revision
    pin = rolling.RollingPlan.pin
    target_policies = rolling.RollingPlan.target_policies
    promoted_aliases = app26.App26V3Plan.promoted_aliases

    @property
    def release(self) -> str:
        return self.source.release

    @property
    def image_digest(self) -> str:
        return self.source.image_digest

    @property
    def runtime_secret(self) -> str:
        return self.database.runtime_reference

    def disabled_flags(self) -> tuple[str, ...]:
        return app26.CLOSED_FLAGS

    def validate(self) -> None:
        _need(type(self) is ContinuityBridgePlan and self.schema_contract == scope.KIND
              and self.database_schema == DB_SCHEMA, "plan_kind")
        _need(type(self.database) is scope.ContinuityDatabasePin and scope._nonce(self.nonce),
              "database_scope")
        self.database.validate()
        _need(type(self.source) is successor.SourcePin and type(self.prior_source) is successor.SourcePin,
              "source_pins")
        self.source.validate()
        self.prior_source.validate()
        _need(self.release != self.prior_source.release
              and _matches(r"[a-f0-9]{40}", self.controller_release)
              and _matches(r"[a-f0-9]{40}", self.workers_release)
              and _matches(r"[1-9][0-9]{20}", self.runtime_uid)
              and all(scope._hash(value) for value in (self.controller_source_sha256, self.financial_sha256))
              and type(self.controller_ci_run_id) is int and self.controller_ci_run_id > 0, "release_pins")
        _validate_policies(self.policies)
        if self.candidate_policies is not None:
            candidate = self.candidate_policies
            _validate_policies(candidate)
            _need((candidate.model_key_secret, candidate.model_primary, candidate.model_api_base)
                  == (self.policies.model_key_secret, self.policies.model_primary, self.policies.model_api_base),
                  "candidate_policy_route_changed")
        cache = rolling.employer_cache_binding(self)
        _need(type(self.services) is tuple and all(type(pin) is rolling.ServicePin for pin in self.services)
              and tuple(pin.service for pin in self.services) == rolling.release.SERVICES, "service_scope")
        for pin in self.services:
            self._validate_service(pin, cache)
        _need(all(type(value) is tuple for value in
                  (self.resources, self.protected_liabilities, self.reachable_revisions))
              and all(type(pin) is historical.ResourcePin for pin in self.resources)
              and all(type(pin) is historical.ReachablePin for pin in self.reachable_revisions)
              and all(type(row) is tuple and len(row) == 3 for row in self.protected_liabilities),
              "retained_pin_types")
        historical.validate_extensions(self)
        if cache is not None:
            api, _, worker = self.services
            _need([(s.secret, s.version) for s in api.secrets if s.environment_name == "RATE_LIMIT_STORAGE_URL"]
                  == [(cache.secret, cache.version)]
                  and not any(s.environment_name == cache.environment_name for s in worker.secrets),
                  "existing_cache_reference")

    def _validate_service(self, pin: rolling.ServicePin, cache: rolling.EmployerCacheBinding | None) -> None:
        _need(_matches(r"[a-f0-9-]{36}", pin.uid) and type(pin.generation) is int and pin.generation > 0
              and rolling.short_revision(pin.prior_revision, pin.service) == pin.prior_revision
              and pin.prior_release == self.prior_source.release and pin.prior_revision != self.revision(pin.service),
              "service_revision")
        _need(all(scope._hash(value) for value in
                  (pin.template_sha256, pin.static_sha256, pin.iam_sha256, pin.revision_sha256)),
              "service_hashes")
        candidate_changed = self.candidate_policies is not None or (cache is not None and pin.service == cache.service)
        _need(scope._hash(pin.candidate_template_sha256) if candidate_changed else pin.candidate_template_sha256 is None,
              "candidate_template_pin")
        _need(type(pin.revision_keys) is tuple and 1 <= len(pin.revision_keys) <= 40
              and all(type(key) is str and 0 < len(key) <= 128 for key in pin.revision_keys)
              and len(set(pin.revision_keys)) == len(pin.revision_keys), "revision_keys")
        _need(type(pin.traffic) is tuple and all(type(row) is dict for row in pin.traffic)
              and rolling.pinned_traffic(pin.service, {"trafficStatuses": list(pin.traffic)},
                                         prior_revision=pin.prior_revision) == list(pin.traffic), "service_traffic")
        _need(type(pin.secrets) is tuple and all(type(secret) is rolling.SecretPin for secret in pin.secrets),
              "service_secret_shape")
        references = {}
        for secret in pin.secrets:
            _need(_matches(r"[A-Z][A-Z0-9_]{0,127}", secret.environment_name)
                  and _matches(r"[A-Za-z0-9_-]{1,255}", secret.secret)
                  and _matches(r"[1-9][0-9]{0,18}", secret.version)
                  and secret.environment_name not in references, "service_secret_reference")
            references[secret.environment_name] = f"{secret.secret}:{secret.version}"
        _need(references.get("DATABASE_URL") == self.database.runtime_reference, "service_runtime_database")
        _need(not any(ref.startswith(HISTORICAL_DATABASE_SECRETS) for ref in references.values()),
              "historical_database_reference")
        rolling.verify_policy_bindings(self.policies, pin)

    def custody(self) -> scope.BridgeCustody:
        self.validate()
        metadata = asdict(self)
        del metadata["database"]
        return scope.BridgeCustody(self.nonce, rolling.sha(metadata), self.database)


def _validate_policies(policies: rolling.PolicyPins) -> None:
    _need(type(policies) is rolling.PolicyPins
          and all(type(ref) is str and rolling.REF.fullmatch(ref)
                  and not ref.startswith(HISTORICAL_DATABASE_SECRETS) for ref in
                  (policies.expense_secret, policies.model_policy_secret, policies.model_key_secret))
          and all(scope._hash(value) for value in (policies.expense_sha256, policies.model_policy_sha256))
          and policies.model_api_base == rolling.release.MODEL_API_BASE
          and _matches(r"gemini-[A-Za-z0-9_.-]{1,113}", policies.model_primary), "policy_pins")


def load(raw: bytes, expected_sha256: str) -> ContinuityBridgePlan:
    _need(type(raw) is bytes and 0 < len(raw) <= MAX_PLAN_BYTES and scope._hash(expected_sha256)
          and hashlib.sha256(raw).hexdigest() == expected_sha256, "private_plan_pin")
    try:
        value = rolling.release._json(raw)
        _need(type(value) is dict and value.get("schema_contract") == scope.KIND
              and set(value) == {field.name for field in fields(ContinuityBridgePlan)}, "plan_kind")
        canonical = rolling.canonical(value)
        for field in ("source", "prior_source"):
            value[field] = successor.SourcePin(**value[field])
        database = value["database"]
        for field in ("runtime_role", "coordinator_role", "observer_role", "migrator_role"):
            database[field] = tuple(database[field])
        value["database"] = scope.ContinuityDatabasePin(**database)
        value["policies"] = rolling.PolicyPins(**value["policies"])
        if value["candidate_policies"] is not None:
            value["candidate_policies"] = rolling.PolicyPins(**value["candidate_policies"])
        if value["candidate_employer_cache"] is not None:
            value["candidate_employer_cache"] = rolling.EmployerCacheBinding(**value["candidate_employer_cache"])
        value["services"] = tuple(rolling.ServicePin(**(pin | {
            "revision_keys": tuple(pin["revision_keys"]),
            "traffic": tuple(pin["traffic"]),
            "secrets": tuple(rolling.SecretPin(**secret) for secret in pin["secrets"]),
        })) for pin in value["services"])
        value["resources"] = tuple(historical.ResourcePin(**pin) for pin in value["resources"])
        value["protected_liabilities"] = tuple(tuple(row) for row in value["protected_liabilities"])
        value["reachable_revisions"] = tuple(historical.ReachablePin(**pin) for pin in value["reachable_revisions"])
        result = ContinuityBridgePlan(**value)
        _need(rolling.canonical(asdict(result)) == canonical, "canonical_plan_shape")
        result.validate()
        return result
    except rolling.RollingDenied:
        raise
    except (rolling.release.ReleaseDenied, AttributeError, KeyError, StopIteration, TypeError, ValueError, RecursionError):
        raise rolling.RollingDenied("schema27_continuity_bridge_plan_unavailable") from None


def bind(plan: ContinuityBridgePlan, journal_directory: Path) -> rolling.Journal:
    """Require the already retained bridge metadata and database in one journal."""
    _need(type(plan) is ContinuityBridgePlan, "plan_kind")
    custody = plan.custody()
    journal = rolling.Journal(journal_directory, custody)
    scope.verify_journal(custody, journal)
    return journal
