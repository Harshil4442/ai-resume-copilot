"""Additive schema19→21 email-password release/migration ports.

No schema18/19/20 contract is changed. The existing one-shot journal, native
bootstrap identity, source provenance and rolling guards remain authoritative.
These callable ports do nothing at import time; only the original explicit
migration phase obtains its native bootstrap session.
"""

# ruff: noqa: E402 -- standalone native operator has explicit local module roots
from __future__ import annotations

import re
import sys
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Literal, cast

sys.path[:0] = [
    str(Path(__file__).resolve().parent),
    str(Path(__file__).resolve().parents[2] / "backend"),
]
import browser_schema19_release as previous
import fresh_cutover_docker as docker
import fresh_cutover_writes as writes
import rolling_native as native
import rolling_release as rolling
from browser_schema19_release import PriorSource, bootstrap_identity, verify_permissions

from scripts import email_password_schema21_contract as contract
from scripts import email_password_schema21_step as step

release = rolling.release
OFF = previous.OFF
CHAIN = (*previous.CHAIN, contract.INTERMEDIATE, contract.SCHEMA)


@dataclass(frozen=True)
class Schema21Plan(rolling.RollingPlan):
    schema_contract: Literal["hirewiz_email_password_schema21_v1"] = contract.CONTRACT
    database_schema: Literal["20261015_0019", "20261023_0021"] = contract.SCHEMA
    prior_sources: tuple[PriorSource, ...] = ()

    def validate(self) -> None:
        super().validate()
        rolling.need(
            type(self) is Schema21Plan
            and self.schema_contract == contract.CONTRACT
            and self.database_schema in {step.BEFORE, contract.SCHEMA},
            "schema21_release_contract_refused",
        )
        rolling.need(
            type(self.prior_sources) is tuple
            and len(self.prior_sources) <= 3
            and len({pin.release for pin in self.prior_sources}) == len(self.prior_sources),
            "schema21_prior_sources_invalid",
        )
        for pin in self.prior_sources:
            rolling.need(
                type(pin) is PriorSource
                and re.fullmatch(r"[a-f0-9]{40}", pin.release)
                and re.fullmatch(r"sha256:[a-f0-9]{64}", pin.image_digest)
                and re.fullmatch(r"[a-f0-9-]{36}", pin.build_id)
                and type(pin.ci_run_id) is int
                and pin.ci_run_id > 0
                and pin.release in {service.prior_release for service in self.services},
                "schema21_prior_source_pin_invalid",
            )

    def disabled_flags(self) -> tuple[str, ...]:
        return OFF


class Source21Boundary(previous.Source19Boundary):
    def verify_source_image(
        self, *, release: str, image_digest: str, build_id: str, ci_run_id: int
    ) -> None:
        tracked = rolling.release.source_guard.checked_files(self.root, release)
        rolling.need(
            rolling.release.source_guard.migration_chain(self.root, tracked, allow_literal_sql=True)
            == CHAIN,
            "schema21_exact_source_chain_required",
        )
        self._verify_source_image_provenance(
            release=release,
            image_digest=image_digest,
            build_id=build_id,
            ci_run_id=ci_run_id,
        )


class NativeSchema21Boundary(native.NativeRollingBoundary):
    def __init__(
        self,
        root: Path,
        schema: str = contract.SCHEMA,
        prior_sources: tuple[PriorSource, ...] = (),
    ):
        rolling.need(schema in {step.BEFORE, contract.SCHEMA}, "schema21_native_phase_refused")
        super().__init__(root)
        self.observer = Source21Boundary(self.root)
        self.schema: Literal["20261015_0019", "20261023_0021"] = (
            "20261015_0019" if schema == step.BEFORE else "20261023_0021"
        )
        self.prior_sources = prior_sources

    def make_plan(self, *args, **kwargs):
        return Schema21Plan(
            *args,
            **kwargs,
            database_schema=self.schema,
            prior_sources=self.prior_sources,
        )

    def _target(self, runtime_secret, observer_secret, retired_migration_secret) -> str:
        module = self.observer._module(
            "backend/scripts/monetary_cutover_preflight.py",
            "hirewiz_schema21_database_observer",
        )
        neon = module.neon_identity
        before = self.observer.describe_release_secret(retired_migration_secret)
        name, version = retired_migration_secret.split(":")
        rolling.need(
            before.get("name") == f"projects/157225724590/secrets/{name}/versions/{version}"
            and before.get("state") == "DISABLED",
            "schema21_retired_migrator_secret_changed",
        )
        financial = None
        collector = (
            neon.collect_schema21_release_target
            if self.schema == contract.SCHEMA
            else neon.collect_schema19_release_target
        )
        for alias, reference in (
            ("observer", observer_secret),
            ("replacement_runtime", runtime_secret),
        ):
            binding, target = collector(
                self.observer,
                reference,
                alias=alias,
                database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
                expected_schema=self.schema,
            )
            engine = target.engine()
            try:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql(
                        "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                    )
                    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                    connection.exec_driver_sql("SET LOCAL TIME ZONE 'UTC'")
                    neon.verify_transport(connection, binding)
                    neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                    sessions, _ = module._native_session_ids(
                        connection, neon.FRESH_DATABASE_OID, neon_binding=binding
                    )
                    if alias == "observer":
                        release.verify_fresh_role_phase(
                            connection, neon.FRESH_RELEASE_ACTORS, "serving", sessions
                        )
                        # In-place additive release preserves coherent terminal
                        # uncertainty; it never settles unknown provider costs.
                        step.verify_financial_catalog(connection, module)
                        if self.schema == contract.SCHEMA:
                            contract.verify_shape(connection, 2200)
                        else:
                            contract.verify_before_shape(connection, 2200)
                        verify_permissions(
                            connection,
                            "public",
                            neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                            neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                            neon.FRESH_RELEASE_ACTORS["observer"][0],
                        )
                        if self.schema == contract.SCHEMA:
                            contract.verify_reset_permissions(connection, "public",
                                neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                                neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                                neon.FRESH_RELEASE_ACTORS["observer"][0])
                        step.drained(connection, bootstrap_identity())
                        financial = native.financial_digest(connection)
            finally:
                engine.dispose()
        rolling.need(
            financial is not None
            and self.observer.describe_release_secret(retired_migration_secret) == before,
            "schema21_native_target_or_retired_secret_changed",
        )
        return cast(str, financial)

    def verify_global(self, plan):
        rolling.need(
            type(plan) is Schema21Plan and plan.database_schema == self.schema,
            "schema21_native_plan_phase_mismatch",
        )
        super().verify_global(plan)
        for pin in plan.services:
            current = self.service(pin.service)
            prior = self.revision(pin.service, pin.prior_revision)
            rolling.need(prior is not None, "schema21_prior_revision_missing")
            for template in (current["template"], prior):
                rows = rolling.environment(template)
                rolling.need(
                    all(rows.get(name, {"value": "false"}).get("value") == "false" for name in OFF),
                    "schema21_all_candidate_admission_must_stay_closed",
                )


def load_plan(raw: bytes) -> Schema21Plan:
    value = release._json(raw)
    rolling.need(
        value.get("schema_contract") == contract.CONTRACT and "database_schema" in value,
        "schema21_distinct_plan_required",
    )
    value["policies"] = rolling.PolicyPins(**value["policies"])
    if value.get("candidate_policies") is not None:
        value["candidate_policies"] = rolling.PolicyPins(**value["candidate_policies"])
    services = []
    for item in value["services"]:
        item["secrets"] = tuple(rolling.SecretPin(**row) for row in item["secrets"])
        item["revision_keys"], item["traffic"] = (
            tuple(item["revision_keys"]),
            tuple(item["traffic"]),
        )
        services.append(rolling.ServicePin(**item))
    value["services"] = tuple(services)
    value["prior_sources"] = tuple(PriorSource(**row) for row in value["prior_sources"])
    plan = Schema21Plan(**value)
    plan.validate()
    return plan


def migration(
    plan: Schema21Plan,
    boundary: NativeSchema21Boundary,
    journal: rolling.Journal,
    *,
    reconcile: bool = False,
) -> dict:
    """A lost transaction acknowledgement is inspected; the DDL is never rerun."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import NullPool

    plan.validate()
    rolling.need(
        type(boundary) is NativeSchema21Boundary
        and type(plan) is Schema21Plan
        and plan.database_schema == step.BEFORE,
        "schema21_native_migration_contract_required",
    )
    rolling.need(
        type(journal) is rolling.Journal and journal.plan_hash == rolling.sha(asdict(plan)),
        "schema21_exact_original_plan_journal_required",
    )
    identity = bootstrap_identity()
    with journal.locked():
        started = journal.read("schema21-migration.started")
        checked = journal.read("schema21-migration.checked")
        rolling.need(checked is None, "schema21_migration_already_observed_never_replay")
        if started is not None:
            rolling.need(reconcile, "schema21_unknown_migration_reconcile_only")
        else:
            rolling.need(not reconcile, "schema21_reconcile_never_begins_migration")
            boundary.verify_global(plan)
        # This is the original normal Neon manager, not a Secret Manager read or
        # a revived migration credential. The BoundDatabase reauthenticates TLS,
        # cluster/role/database identity before the first protected SQL command.
        password = docker.bootstrap_password(time.monotonic() + 60)
        bootstrap = writes.transfer.BoundDatabase(
            writes.transfer.NativeDatabaseIdentity(
                writes.TARGET_HOST,
                5432,
                "neondb",
                writes.TARGET_CLUSTER,
                16395,
                identity.bootstrap,
                identity.bootstrap_oid,
                str(docker.CA),
            ),
            password,
        )
        engine = create_engine(
            "postgresql+psycopg://",
            creator=bootstrap.connect,
            poolclass=NullPool,
            echo=False,
            hide_parameters=True,
        )
        try:
            if started is None:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    step.verify_identity(connection, identity)
                    rolling.need(
                        step.marker(connection, identity) == step.BEFORE,
                        "schema21_before_schema_mismatch",
                    )
                    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                    before_hash = step.retained_digest(connection, identity)
                    connection.exec_driver_sql("RESET ROLE")
                    before_acl = step.schema_acl(connection, identity)
                journal.record(
                    "schema21-migration.started",
                    {
                        "expected": step.BEFORE,
                        "target": contract.SCHEMA,
                        "retained_sha256": before_hash,
                        "schema_acl": before_acl,
                        "replay_authorized": False,
                    },
                )
                started = journal.read("schema21-migration.started")
                with engine.connect() as connection, connection.begin():
                    result = step.upgrade_transaction(connection, identity, root=boundary.root)
                    rolling.need(
                        result["retained_sha256"] == before_hash
                        and result["schema_acl"] == before_acl,
                        "schema21_transaction_preimage_changed",
                    )
            assert started is not None
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                step.verify_identity(connection, identity)
                observed = step.marker(connection, identity)
                if observed == step.BEFORE:
                    return {
                        "status": "schema19_observed_reconcile_only",
                        "schema": observed,
                        "replay_authorized": False,
                    }
                contract.verify_shape(connection, identity.namespace_oid)
                rolling.need(
                    [list(row) for row in step.schema_acl(connection, identity)]
                    == started["schema_acl"],
                    "schema21_committed_acl_changed",
                )
                connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
                step.drained(connection, identity)
                rolling.need(
                    step.retained_digest(connection, identity) == started["retained_sha256"],
                    "schema21_committed_history_changed",
                )
                verify_permissions(
                    connection,
                    "public",
                    identity.runtime,
                    identity.migrator,
                    identity.observer,
                )
                contract.verify_reset_permissions(connection, "public", identity.runtime, identity.migrator, identity.observer)
            observed_boundary = NativeSchema21Boundary(
                boundary.root, contract.SCHEMA, plan.prior_sources
            )
            observed_boundary.verify_global(replace(plan, database_schema=contract.SCHEMA))
            journal.record(
                "schema21-migration.checked",
                {
                    "schema": contract.SCHEMA,
                    "retained_sha256": started["retained_sha256"],
                    "retired_migration_credential_revived": False,
                    "replay_authorized": False,
                },
            )
            return {
                "status": "schema21_committed_and_freshly_observed",
                "schema": contract.SCHEMA,
                "queue_admission": "closed",
                "replay_authorized": False,
                "goal_complete": False,
            }
        finally:
            engine.dispose()
