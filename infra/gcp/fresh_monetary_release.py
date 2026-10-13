"""Fresh destination release; old-cluster export and native fences stay separate.

The existing native fence is intentionally still unavailable. This module does
not grant cutover authority or activate production during preparation. Staging
and promotion are separate calls so the approved bootstrap operator can retire
the migrator between them; serving admission reobserves the actual final state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import monetary_release as release

from scripts.neon_execution_files import LinuxExecutionFiles


@dataclass(frozen=True)
class TransferPlan:
    dump: Path
    restore: Path
    dump_sha256: str
    restore_sha256: str
    workdir: Path

    def validate(self) -> None:
        if any(
            type(value) is not str or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in (self.dump_sha256, self.restore_sha256)
        ) or any(
            not isinstance(value, Path) or not value.is_absolute()
            for value in (self.dump, self.restore, self.workdir)
        ):
            raise release.ReleaseDenied("fresh_transfer_tools_or_workdir_invalid")


class FreshBoundary(release.Boundary, Protocol):
    def transfer_fresh(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        transfer: TransferPlan,
        fence: release.Fence,
    ) -> None: ...
    def grant_fresh_runtime(
        self, source: release.Plan, target: release.FreshTargetPlan, schema: str
    ) -> None: ...
    def verify_fresh_database_phase(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        schema: str,
        *,
        phase: str,
    ) -> None: ...
    def verify_fresh_finances(
        self, source: release.Plan, target: release.FreshTargetPlan, schema: str
    ) -> None: ...
    def migrate_fresh(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        expected: str,
        next_schema: str,
    ) -> None: ...
    def stage_fresh(
        self, source: release.Plan, target: release.FreshTargetPlan, service: str
    ) -> str: ...
    def verify_fresh_revision(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        service: str,
        revision: str,
    ) -> None: ...
    def verify_fresh_serving(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        service: str,
        revision: str,
    ) -> None: ...


def runtime_plan(source: release.Plan, target: release.FreshTargetPlan) -> release.Plan:
    """Only deployment metadata uses this copy; never source capture or backup."""
    source.validate()
    target.validate(source)
    return replace(
        source,
        runtime_secret=target.runtime_secret,
        migration_secret=target.migration_secret,
        observer_secret=target.observer_secret,
        database_owner_oid=16391,
    )


def _source_gate(
    source: release.Plan, boundary: FreshBoundary, clock: Any
) -> release.Fence:
    boundary.verify_identity_and_policy(source)
    boundary.verify_admission_closed()
    fence = release._fresh_fence(boundary, source, clock)
    boundary.verify_database(source, release.CUTOVER_CHAIN[0])
    return fence


def _target_gate(
    source: release.Plan,
    target: release.FreshTargetPlan,
    boundary: FreshBoundary,
    schema: str,
    phase: str,
) -> None:
    boundary.verify_fresh_database_phase(source, target, schema, phase=phase)
    boundary.verify_fresh_finances(source, target, schema)


def stage_fresh_release(
    source: release.Plan,
    target: release.FreshTargetPlan,
    transfer: TransferPlan,
    boundary: FreshBoundary,
    *,
    execute: bool,
    clock: Any = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """One forward transfer/migration/stage sequence; lost results stop it.

    No source-schema mutation, old-owner restore, automatic replay, finalizer or
    traffic promotion. An operator must reconcile any uncertain outcome before
    considering a different attempt. The owned transfer leaves its once marker.
    """
    if (
        type(source) is not release.Plan
        or type(target) is not release.FreshTargetPlan
        or type(transfer) is not TransferPlan
        or type(execute) is not bool
    ):
        raise release.ReleaseDenied("fresh_release_plan_shape_invalid")
    source.validate()
    target.validate(source)
    transfer.validate()
    fence = _source_gate(source, boundary, clock)
    if not execute:
        return {
            "release": source.release,
            "mutation_count": 0,
            "production_released": False,
            "final_native_transfer_required": True,
            "target_finalization_required": True,
        }
    boundary.create_backup(source, fence)
    boundary.verify_backup(source, fence)
    _source_gate(source, boundary, clock)
    boundary.transfer_fresh(source, target, transfer, fence)
    boundary.grant_fresh_runtime(source, target, release.CUTOVER_CHAIN[0])
    _target_gate(source, target, boundary, release.CUTOVER_CHAIN[0], "migration")
    for expected, next_schema in zip(
        release.CUTOVER_CHAIN[:-1], release.CUTOVER_CHAIN[1:], strict=True
    ):
        _source_gate(source, boundary, clock)
        _target_gate(source, target, boundary, expected, "migration")
        boundary.migrate_fresh(source, target, expected, next_schema)
        boundary.grant_fresh_runtime(source, target, next_schema)
        _target_gate(source, target, boundary, next_schema, "migration")
    revisions = {}
    for service in release.SERVICES:
        _source_gate(source, boundary, clock)
        _target_gate(source, target, boundary, release.CHAIN[-1], "migration")
        revisions[service] = boundary.stage_fresh(source, target, service)
        boundary.verify_fresh_revision(source, target, service, revisions[service])
    for service, revision in revisions.items():
        boundary.verify_fresh_revision(source, target, service, revision)
    return {
        "release": source.release,
        "schema": release.CHAIN[-1],
        "joined_revisions": revisions,
        "staging_complete": True,
        "traffic_promoted": False,
        "production_released": False,
        "target_finalization_required": True,
        "frontend_deployment_verified": False,
        "goal_complete": False,
    }


def promote_fresh_release(
    source: release.Plan,
    target: release.FreshTargetPlan,
    revisions: dict[str, str],
    boundary: FreshBoundary,
    *,
    clock: Any = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    """Native reread, not a stage receipt, authorizes each traffic mutation."""
    if type(source) is not release.Plan or type(target) is not release.FreshTargetPlan:
        raise release.ReleaseDenied("fresh_release_plan_shape_invalid")
    source.validate()
    target.validate(source)
    if (
        type(revisions) is not dict
        or set(revisions) != set(release.SERVICES)
        or any(
            type(revision) is not str
            or not 1 <= len(revision) <= 128
            or re.fullmatch(re.escape(service) + r"-[a-z0-9-]+", revision) is None
            for service, revision in revisions.items()
        )
    ):
        raise release.ReleaseDenied("fresh_joined_revision_identity_invalid")
    _source_gate(source, boundary, clock)
    _target_gate(source, target, boundary, release.CHAIN[-1], "serving")
    for service, revision in revisions.items():
        boundary.verify_fresh_revision(source, target, service, revision)
    for service in (*release.SERVICES[1:], release.SERVICES[0]):
        _source_gate(source, boundary, clock)
        _target_gate(source, target, boundary, release.CHAIN[-1], "serving")
        boundary.verify_fresh_revision(source, target, service, revisions[service])
        boundary.promote(service, revisions[service])
        boundary.verify_fresh_serving(source, target, service, revisions[service])
    _source_gate(source, boundary, clock)
    _target_gate(source, target, boundary, release.CHAIN[-1], "serving")
    return {
        "release": source.release,
        "schema": release.CHAIN[-1],
        "joined_revisions": revisions,
        "gcp_traffic_promoted": True,
        "production_released": False,
        "frontend_deployment_verified": False,
        "queue_admission": "closed",
        "checkout_enabled": False,
        "optional_generation": False,
        "automatic_submission": False,
        "goal_complete": False,
    }


def verify_financial_state(connection: Any, module: Any, schema: str) -> None:
    """Reuse the reviewed financial/schema predicates on a fresh native connection."""
    from sqlalchemy import text

    if schema not in release.CUTOVER_CHAIN:
        raise release.ReleaseDenied("fresh_financial_schema_invalid")
    tables = (
        connection.execute(
            text(
                "SELECT c.relname FROM pg_catalog.pg_class c WHERE c.relnamespace=2200 "
                "AND c.relkind IN ('r','p','v','m','f') ORDER BY c.oid LIMIT 257"
            )
        )
        .scalars()
        .all()
    )
    if (
        not 1 <= len(tables) <= 256
        or len(set(tables)) != len(tables)
        or not set(module.TABLES) | {"alembic_version"} <= set(tables)
    ):
        raise release.ReleaseDenied("fresh_financial_table_inventory_invalid")
    if (schema in module.FINANCIAL_SCHEMAS) != ("model_cost_liabilities" in tables):
        raise release.ReleaseDenied("fresh_financial_schema_shape_invalid")
    if schema in module.CANDIDATE_SCHEMAS:
        if not module.CANDIDATE_TABLES <= set(tables):
            raise release.ReleaseDenied("fresh_candidate_tables_missing")
        module._candidate_projection_shape(connection, 2200)
    elif module.CANDIDATE_TABLES & set(tables):
        raise release.ReleaseDenied("fresh_candidate_schema_shape_invalid")
    module._extension_shape(connection, 2200, schema)
    predicates = [
        "SELECT count(*) FROM pg_catalog.pg_prepared_xacts WHERE database=current_database()",
        "SELECT count(*) FROM public.model_call_events WHERE status NOT IN ('succeeded','failed','invalid_output')",
        "SELECT count(*) FROM public.analysis_runs WHERE status NOT IN ('succeeded','failed','cancelled')",
        "SELECT count(*) FROM public.employer_applications WHERE status NOT IN ('needs_action','ready','approved','manual_handoff','confirmed','failed','cancelled')",
        "SELECT count(*) FROM public.employer_application_attempts WHERE state NOT IN ('confirmed','failed')",
        "SELECT count(*) FROM public.service_credit_reservations WHERE state <> 'settled' OR reserved_amount <> committed_amount + released_amount OR reserved_amount < 0 OR committed_amount < 0 OR released_amount < 0",
    ]
    if schema in module.FINANCIAL_SCHEMAS:
        predicates.extend(
            [
                "SELECT count(*) FROM public.analysis_runs WHERE model_cost_reserved_micros <> 0 OR model_cost_settled_micros < 0 OR model_cost_state NOT IN ('unquoted','active') OR (model_cost_state = 'unquoted' AND (model_cost_quote IS NOT NULL OR model_cost_settled_micros <> 0)) OR (model_cost_state = 'active' AND (model_cost_quote IS NULL OR model_cost_ceiling_micros IS NULL OR model_cost_ceiling_micros <= 0 OR model_cost_settled_micros > model_cost_ceiling_micros))",
                "SELECT count(*) FROM public.model_call_events WHERE cost_state NOT IN ('unavailable','settled') OR (cost_state = 'settled' AND (liability_id IS NULL OR settled_at IS NULL OR settled_cost_micros IS NULL OR settled_cost_micros < 0)) OR (cost_state = 'unavailable' AND (liability_id IS NOT NULL OR pricing_quote IS NOT NULL OR reserved_cost_micros IS NOT NULL OR settled_cost_micros IS NOT NULL OR settled_at IS NOT NULL OR token_estimate_provenance IS NOT NULL OR usage_provenance IS NOT NULL OR output_token_limit IS NOT NULL))",
            ]
        )
        liabilities = module._liabilities(connection, "public")
        if (
            type(liabilities.get("unresolved_count")) is not int
            or liabilities["unresolved_count"] != 0
        ):
            raise release.ReleaseDenied("fresh_unresolved_model_liabilities")
    for query in predicates:
        count = connection.execute(text(query)).scalar_one()
        if type(count) is not int or count != 0:
            raise release.ReleaseDenied(
                "fresh_unresolved_financial_or_application_state"
            )


class FreshNativeBoundary(release.NativeBoundary):
    """Concrete destination actions; inherited native fence still refuses today."""

    def __init__(self, root: Path, *, linux_execution_files: LinuxExecutionFiles | None = None):
        super().__init__(root)
        if linux_execution_files is not None:
            if type(linux_execution_files) is not LinuxExecutionFiles:
                raise release.ReleaseDenied("fresh_linux_execution_files_invalid")
            try:
                linux_execution_files.validate()
            except Exception:
                raise release.ReleaseDenied("fresh_linux_execution_files_invalid") from None
        self.linux_execution_files = linux_execution_files

    def verify_database(self, plan: release.Plan, schema: str) -> None:
        if schema != release.CUTOVER_CHAIN[0]:
            raise release.ReleaseDenied("fresh_source_schema_must_remain_nine")
        module, (engine, inventory, binding, _), roles, operators = (
            self._database_context(plan, schema)
        )
        try:
            observed = module.observe(
                engine, inventory, roles, operators, neon_binding=binding
            )
            result = module.evaluate(
                inventory, plan.inventory_sha256, observed, datetime.now(UTC)
            )
            if result["database_gate_reasons"]:
                raise release.ReleaseDenied(
                    "fresh_source_retirement_or_obligations_unready"
                )
            for alias in ("replacement_runtime", "replacement_migration"):
                row = observed["roles"][alias]
                if (
                    row["login_enabled"]
                    or row["schema_create"]
                    or row["active_session_count"]
                    or row["elevated"]
                    or row["elevated_role_membership"]
                    or row["membership_administration"]
                    or row["writable_table_count"]
                    or row["executable_security_definer_count"]
                ):
                    raise release.ReleaseDenied(
                        "fresh_source_automated_writer_not_retired"
                    )
        finally:
            engine.dispose()

    def transfer_fresh(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        transfer: TransferPlan,
        fence: release.Fence,
    ) -> None:
        target.validate(source)
        transfer.validate()
        self.verify_database(source, release.CUTOVER_CHAIN[0])
        module, (engine, inventory, binding, origin), _, _ = self._database_context(
            source, release.CUTOVER_CHAIN[0]
        )
        engine.dispose()
        neon = module.neon_identity
        target_binding, destination = neon.collect_fresh_release_target(
            self,
            target.migration_secret,
            alias="replacement_migration",
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            expected_schema=release.CUTOVER_CHAIN[0],
        )
        library = self._module(
            "backend/scripts/database_transfer.py", "hirewiz_fresh_owned_transfer"
        )

        NativeBoundBase: Any = library.BoundDatabase

        class FreshDestination(NativeBoundBase):
            def connect(self) -> Any:
                # Reuse the library's genuine TLS/actor/cluster guard before the
                # fresh database/schema-owner admission. Do not query a marker:
                # this is the still-empty destination before restore.
                connection = super().connect()
                try:
                    connection.autocommit = True
                    row = connection.execute(
                        "SELECT d.datdba,n.oid,n.nspowner FROM pg_catalog.pg_database d "
                        "CROSS JOIN pg_catalog.pg_namespace n "
                        "WHERE d.datname=pg_catalog.current_database() AND n.nspname='public'"
                    ).fetchone()
                    if (
                        row is None
                        or len(row) != 3
                        or any(type(value) is not int for value in row)
                        or tuple(row)
                        != (
                            neon.FRESH_DATABASE_OWNER_OID,
                            2200,
                            neon.FRESH_NAMESPACE_OWNER_OID,
                        )
                    ):
                        raise release.ReleaseDenied(
                            "fresh_empty_destination_owner_identity_changed"
                        )
                    connection.autocommit = False
                    return connection
                except BaseException:
                    connection.close()
                    raise

        def bound(
            item: Any,
            identity_binding: Any,
            cluster: str,
            oid: int,
            *,
            fresh: bool = False,
        ) -> Any:
            env = item.dump_environment()
            if env["PGCHANNELBINDING"] != "prefer":
                raise release.ReleaseDenied("fresh_transfer_channel_policy_unsupported")
            identity = library.NativeDatabaseIdentity(
                identity_binding.host,
                5432,
                neon.DATABASE,
                cluster,
                oid,
                identity_binding.actor,
                identity_binding.actor_oid,
                env["PGSSLROOTCERT"],
            )
            database_class = FreshDestination if fresh else library.BoundDatabase
            return database_class(identity, env["PGPASSWORD"])

        old = bound(
            origin,
            binding,
            inventory.expected_system_identifier_sha256,
            inventory.expected_database_oid,
        )
        new = bound(
            destination,
            target_binding,
            neon.FRESH_CLUSTER,
            neon.FRESH_DATABASE_OID,
            fresh=True,
        )
        boundary = self

        class NativeCut:
            def verify_native_cut(
                self, source_identity: Any, target_identity: Any
            ) -> Any:
                if source_identity != old.identity or target_identity != new.identity:
                    raise release.ReleaseDenied("fresh_transfer_identity_changed")
                boundary.verify_database(source, release.CUTOVER_CHAIN[0])
                actual = release._fresh_fence(
                    boundary, source, lambda: datetime.now(UTC)
                )
                if actual.retired_since != fence.retired_since:
                    raise release.ReleaseDenied("fresh_transfer_writer_cut_changed")
                return library.NativeCutObservation(
                    actual.observed_at, actual.retired_since
                )

        tools = library.PostgresTools(
            transfer.dump,
            transfer.restore,
            transfer.dump_sha256,
            transfer.restore_sha256,
        )
        result = library.owned_transfer(
            old, new, tools, transfer.workdir, purpose="final", native_cut=NativeCut()
        )
        if (
            result.get("status") != "verified"
            or result.get("purpose") != "final"
            or result.get("schema") != release.CUTOVER_CHAIN[0]
            or result.get("native_cut_observed") is not True
            or result.get("attempt_started") is not True
            or result.get("cleanup") != "group_absent_anchor_reaped"
            or result.get("production_released") is not False
        ):
            raise release.ReleaseDenied(
                "fresh_final_transfer_not_verified_reconcile_only"
            )

    def _fresh_connection(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        schema: str,
        alias: str,
    ) -> tuple[Any, Any, Any]:
        if (
            alias not in {"observer", "replacement_migration"}
            or schema not in release.CUTOVER_CHAIN
        ):
            raise release.ReleaseDenied("fresh_native_connection_scope_invalid")
        target.validate(source)
        module, (engine, _, _, _), _, _ = self._database_context(
            source, release.CUTOVER_CHAIN[0]
        )
        engine.dispose()
        neon = module.neon_identity
        reference = (
            target.migration_secret
            if alias == "replacement_migration"
            else target.observer_secret
        )
        binding, observed = neon.collect_fresh_release_target(
            self,
            reference,
            alias=alias,
            database_owner_oid=neon.FRESH_DATABASE_OWNER_OID,
            expected_schema=schema,
        )
        return module, binding, observed.engine()

    def verify_fresh_finances(
        self, source: release.Plan, target: release.FreshTargetPlan, schema: str
    ) -> None:
        module, binding, engine = self._fresh_connection(
            source, target, schema, "observer"
        )
        try:
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                module.neon_identity.verify_transport(connection, binding)
                module.neon_identity.verify_native_target(
                    connection, binding, module.neon_identity.FRESH_DATABASE_OID
                )
                verify_financial_state(connection, module, schema)
        finally:
            engine.dispose()

    def grant_fresh_runtime(
        self, source: release.Plan, target: release.FreshTargetPlan, schema: str
    ) -> None:
        from sqlalchemy import text

        module, binding, engine = self._fresh_connection(
            source, target, schema, "replacement_migration"
        )
        try:
            with engine.connect() as connection, connection.begin():
                connection.exec_driver_sql("SET TRANSACTION READ WRITE")
                connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                connection.exec_driver_sql("SET LOCAL lock_timeout='1000ms'")
                neon = module.neon_identity
                neon.verify_transport(connection, binding)
                neon.verify_native_target(connection, binding, neon.FRESH_DATABASE_OID)
                rows = connection.execute(
                    text(
                        "SELECT c.relname,c.relkind,c.relowner FROM pg_catalog.pg_class c "
                        "WHERE c.relnamespace=2200 AND c.relkind IN ('r','p','S') ORDER BY c.oid LIMIT 513"
                    )
                ).all()
                if (
                    not rows
                    or len(rows) > 512
                    or sum(row[1] != "S" for row in rows) > 256
                    or sum(row[1] == "S" for row in rows) > 256
                    or any(
                        type(row[0]) is not str
                        or not 1 <= len(row[0]) <= 63
                        or row[1] not in {"r", "p", "S"}
                        or type(row[2]) is not int
                        or row[2]
                        != neon.FRESH_RELEASE_ACTORS["replacement_migration"][1]
                        for row in rows
                    )
                ):
                    raise release.ReleaseDenied("fresh_owned_grant_inventory_invalid")
                quote = connection.dialect.identifier_preparer.quote
                runtime = quote(neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0])
                observer = quote(neon.FRESH_RELEASE_ACTORS["observer"][0])
                for name, kind, _ in rows:
                    item = "public." + quote(name)
                    if kind == "S":
                        connection.exec_driver_sql(
                            f"GRANT USAGE ON SEQUENCE {item} TO {runtime}"
                        )
                        connection.exec_driver_sql(
                            f"GRANT SELECT ON SEQUENCE {item} TO {observer}"
                        )
                    else:
                        privileges = (
                            "SELECT"
                            if name == "alembic_version"
                            else "SELECT,INSERT,UPDATE"
                        )
                        if name in release.ERASABLE_TABLES:
                            privileges += ",DELETE"
                        connection.exec_driver_sql(
                            f"GRANT {privileges} ON TABLE {item} TO {runtime}"
                        )
                        connection.exec_driver_sql(
                            f"GRANT SELECT ON TABLE {item} TO {observer}"
                        )
                release.verify_runtime_permissions(
                    connection,
                    "public",
                    neon.FRESH_RELEASE_ACTORS["replacement_runtime"][0],
                    neon.FRESH_RELEASE_ACTORS["replacement_migration"][0],
                )
        finally:
            engine.dispose()

    def migrate_fresh(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        expected: str,
        next_schema: str,
    ) -> None:
        if (
            expected not in release.CUTOVER_CHAIN[:-1]
            or next_schema
            != release.CUTOVER_CHAIN[release.CUTOVER_CHAIN.index(expected) + 1]
        ):
            raise release.ReleaseDenied("fresh_migration_step_invalid")
        self.verify_fresh_database_phase(source, target, expected, phase="migration")
        module, binding, engine = self._fresh_connection(
            source, target, expected, "replacement_migration"
        )
        engine.dispose()
        neon = module.neon_identity
        job = "hirewiz-fresh-" + source.release[:12] + "-" + next_schema[-4:]
        env = {
            **release.SAFE_ENV,
            "APP_RELEASE": source.release,
            "SERVICE_ROLE": "migration",
            "DB_POOL_SIZE": "1",
            "DB_MAX_OVERFLOW": "0",
            "HIREWIZ_CUTOVER_CLUSTER_SHA256": neon.FRESH_CLUSTER,
            "HIREWIZ_CUTOVER_DATABASE_OID": str(neon.FRESH_DATABASE_OID),
            "HIREWIZ_CUTOVER_NAMESPACE": neon.NAMESPACE,
            "HIREWIZ_CUTOVER_MIGRATION_ROLE": binding.actor,
            "HIREWIZ_CUTOVER_MIGRATION_ROLE_OID": str(binding.actor_oid),
        }
        self._cloud(
            "run",
            "jobs",
            "deploy",
            job,
            "--region",
            release.REGION,
            "--image",
            source.image,
            "--service-account",
            source.migration_service_account,
            "--tasks",
            "1",
            "--parallelism",
            "1",
            "--max-retries",
            "0",
            "--task-timeout",
            "360s",
            "--cpu",
            "1",
            "--memory",
            "1Gi",
            "--set-env-vars",
            "^|^" + "|".join(f"{key}={value}" for key, value in env.items()),
            "--set-secrets",
            "DATABASE_URL=" + target.migration_secret,
            "--command",
            "python",
            "--args",
            f"scripts/monetary_schema_step.py,--expected,{expected},--target,{next_schema}",
        )
        execution = self._cloud(
            "run", "jobs", "execute", job, "--region", release.REGION, "--wait"
        )
        status = execution.get("status", {})
        if (
            status.get("succeededCount") != 1
            or not status.get("completionTime")
            or status.get("failedCount", 0)
        ):
            raise release.ReleaseDenied(
                "fresh_migration_outcome_unknown_reconcile_only"
            )

    def stage_fresh(
        self, source: release.Plan, target: release.FreshTargetPlan, service: str
    ) -> str:
        return self.stage(runtime_plan(source, target), service)

    def verify_fresh_revision(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        service: str,
        revision: str,
    ) -> None:
        self.verify_revision(runtime_plan(source, target), service, revision)

    def verify_fresh_serving(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        service: str,
        revision: str,
    ) -> None:
        self.verify_serving(runtime_plan(source, target), service, revision)

    def frontend_configuration(
        self, source: release.Plan, target: release.FreshTargetPlan, revision: str
    ) -> dict[str, Any]:
        """Observed GCP destination for separately reviewed Vercel staging."""
        self.verify_fresh_serving(source, target, release.SERVICES[0], revision)
        observed = self._cloud(
            "run",
            "services",
            "describe",
            release.SERVICES[0],
            "--region",
            release.REGION,
        )
        url = observed.get("status", {}).get("url")
        parsed = urlsplit(url) if type(url) is str else None
        if (
            parsed is None
            or parsed.scheme != "https"
            or not parsed.hostname
            or not parsed.hostname.endswith(".run.app")
            or parsed.username
            or parsed.password
            or parsed.port not in {None, 443}
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise release.ReleaseDenied("fresh_frontend_backend_destination_invalid")
        return {
            "environment": {"BACKEND_URL": url},
            "expected_backend_release": source.release,
            "database_secret_on_frontend": False,
            "frontend_deployment_verified": False,
        }
