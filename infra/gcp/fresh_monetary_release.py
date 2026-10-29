"""Fresh destination release; old-cluster export and native fences stay separate.

The concrete native fence is supplied by native_fresh_fence. This preparation
module does not establish native closure or activate production at import.
Staging and promotion are separate calls so the supported bootstrap operator
can retire the migrator between them; serving admission reobserves the actual
final state. Source controls are not an accepted customer cutover receipt.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import monetary_release as release

from scripts import database_transfer as transfer_protocol
from scripts.neon_execution_files import LinuxExecutionFiles

_TRANSFER_FIELDS = frozenset({
    "status", "phase", "attempt_started", "cleanup", "native_cut_observed",
    "table_count", "row_count", "sequence_count",
})
_TRANSFER_PARENT_PHASES = frozenset({
    "owned_transfer_launch_refused", "owned_transfer_deadline_reconcile_before_retry",
    "owned_transfer_cancelled_reconcile_before_retry",
    "owned_transfer_observation_unavailable_reconcile_before_retry",
    "owned_transfer_cleanup_unconfirmed",
})


def transfer_projection(value: Any) -> dict[str, Any]:
    """Fixed transfer diagnostics only; this never certifies a remote outcome."""
    if (type(value) is not dict or set(value) != _TRANSFER_FIELDS
            or type(value.get("status")) is not str
            or value["status"] not in {"verified", "refused", "reconcile_required", "cancelled"}
            or type(value.get("phase")) is not str
            or value["phase"] not in set(transfer_protocol._OWNER_PHASES) | _TRANSFER_PARENT_PHASES
            or not (value.get("attempt_started") is None or type(value["attempt_started"]) is bool)
            or type(value.get("cleanup")) is not str
            or value["cleanup"] not in {"not_launched", "group_absent_anchor_reaped", "anchor_reap_unconfirmed",
                                        "anchor_reaped_group_unconfirmed", "cleanup_deadline_unconfirmed"}
            or type(value.get("native_cut_observed")) is not bool
            or any(type(value.get(name)) is not int or not 0 <= value[name] <= maximum
                   for name, maximum in (("table_count", 256), ("row_count", 1_000_000), ("sequence_count", 256)))):
        raise ValueError("cutover_observability_projection_refused")
    return {name: value[name] for name in sorted(_TRANSFER_FIELDS)}


class FreshSourcePlan(release.Plan):
    """Explicit absent pre-cut slots; this is never a serving credential plan.

    Runtime/migration source slots are unused and must remain absent strings.
    Observer/model are empty only before their actual post-isolation issuance.
    Final runtime_plan produces the original complete Plan from native versions.
    """

    def validate(self) -> None:
        refs = (self.expense_secret, self.model_policy_secret)
        optional = (self.observer_secret, self.model_key_secret)
        if (
            self.runtime_secret != ""
            or self.migration_secret != ""
            or not re.fullmatch(r"[a-f0-9]{40}", self.release)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", self.image_digest)
            or not re.fullmatch(r"[a-f0-9-]{36}", self.build_id)
            or type(self.ci_run_id) is not int
            or self.ci_run_id <= 0
            or any(
                type(ref) is not str
                or not re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", ref)
                for ref in refs
            )
            or any(
                type(ref) is not str
                or ref
                and not re.fullmatch(r"[a-z][a-z0-9-]{2,62}:[1-9][0-9]{0,8}", ref)
                for ref in optional
            )
            or len({ref.split(":")[0] for ref in (*refs, *optional) if ref})
            != sum(bool(ref) for ref in (*refs, *optional))
            or not re.fullmatch(r"[a-f0-9]{64}", self.expense_sha256)
            or not re.fullmatch(r"[a-f0-9]{64}", self.model_policy_sha256)
            or not re.fullmatch(r"gemini-[A-Za-z0-9_.-]{1,113}", self.model_primary)
            or self.model_api_base != release.MODEL_API_BASE
            or type(self.database_owner_oid) is not int
            or not 0 < self.database_owner_oid <= 4294967295
            or not re.fullmatch(r"[a-f0-9]{64}", self.inventory_sha256)
            or self.runtime_service_account == self.migration_service_account
            or any(
                not re.fullmatch(
                    r"[a-z][a-z0-9-]{4,28}@" + release.PROJECT + r"\.iam\.gserviceaccount\.com",
                    value,
                )
                for value in (self.runtime_service_account, self.migration_service_account)
            )
            or not re.fullmatch(
                r"gs://"
                + release.PROJECT
                + r"-hirewiz-application-artifacts/releases/[a-zA-Z0-9_.-]+\.dump",
                self.backup_uri,
            )
        ):
            raise release.ReleaseDenied("fresh_source_plan_identity_invalid")


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
    def verify_document_preparation(self, source: release.Plan) -> dict[str, Any]: ...
    def verify_document_configuration(self, source: release.Plan) -> dict[str, Any]: ...
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
    if not source.observer_secret or not source.model_key_secret:
        raise release.ReleaseDenied("fresh_source_native_issuance_not_complete")
    values = {field: getattr(source, field) for field in release.Plan.__dataclass_fields__}
    values.update(
        runtime_secret=target.runtime_secret,
        migration_secret=target.migration_secret,
        observer_secret=target.observer_secret,
        database_owner_oid=16391,
    )
    result = release.Plan(**values)
    result.validate()
    return result


def _source_owner_closed(connection: Any, owner_oid: int, text: Any) -> None:
    row = connection.execute(
        text(
            "SELECT count(*)=1 AND bool_and(rolname='hirewiz_source_owner_20261014_r1' "
            "AND NOT rolcanlogin AND NOT rolinherit AND NOT rolsuper AND NOT rolcreatedb "
            "AND NOT rolcreaterole AND NOT rolreplication AND NOT rolbypassrls), "
            "(SELECT count(*) FROM pg_catalog.pg_auth_members "
            "WHERE roleid=:oid OR member=:oid OR grantor=:oid), "
            "(SELECT count(*) FROM pg_catalog.pg_stat_activity WHERE usesysid=:oid) "
            "FROM pg_catalog.pg_roles WHERE oid=:oid"
        ),
        {"oid": owner_oid},
    ).one()
    if tuple(row) != (True, 0, 0):
        raise release.ReleaseDenied("fresh_source_owned_role_not_closed")


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
        type(source) not in {release.Plan, FreshSourcePlan}
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
    boundary.verify_document_configuration(source)
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
    if (
        type(source) not in {release.Plan, FreshSourcePlan}
        or type(target) is not release.FreshTargetPlan
    ):
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
        module.unresolved_application_sql("public", schema),
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

    def _verify_model_policy(self, plan: release.Plan, expense: dict[str, Any]) -> None:
        if type(plan) is not FreshSourcePlan or plan.model_key_secret:
            return super()._verify_model_policy(plan, expense)
        plan.validate()
        raw = self._secret(plan.model_policy_secret)
        if hashlib.sha256(raw.encode()).hexdigest() != plan.model_policy_sha256:
            raise release.ReleaseDenied("model_policy_secret_hash_mismatch")
        authority = self._module(
            "backend/app/services/model_cost_policy.py", "hirewiz_release_model_policy"
        )
        try:
            policy = authority.ModelCostPolicy.model_validate(release._json(raw.encode()))
            if (
                policy.expense_funding is not None
                or policy.expense_policy_version is not None
                or policy.expense_paid_units is not None
            ):
                raise release.ReleaseDenied("model_policy_runtime_funding_not_configuration")
            limits = expense["operation_model_ceilings_micros"]
            if any(
                limit > limits.get(operation, 0)
                for operation, limit in policy.operation_limits_micros.items()
            ):
                raise release.ReleaseDenied("model_policy_exceeds_funded_operation_ceiling")
            models = tuple(
                dict.fromkeys((plan.model_primary, *release._gemini_fallbacks(self.root)))
            )
            now = datetime.now(UTC)
            for model in models:
                quotes = [
                    quote
                    for quote in policy.pricing_quotes
                    if (quote.provider, quote.model, quote.api_base)
                    == ("google", model, plan.model_api_base)
                ]
                if len(quotes) != 1 or not quotes[0].valid_from <= now < quotes[0].expires_at:
                    raise release.ReleaseDenied("exact_model_fallback_price_authority_unavailable")
        except release.ReleaseDenied:
            raise
        except (ValueError, TypeError, KeyError):
            raise release.ReleaseDenied("model_policy_invalid") from None

    def verify_database(self, plan: release.Plan, schema: str) -> None:
        if schema != release.CUTOVER_CHAIN[0]:
            raise release.ReleaseDenied("fresh_source_schema_must_remain_nine")
        module, (engine, inventory, binding, _), roles, operators = self._database_context(
            plan, schema
        )
        try:
            observed = module.observe(engine, inventory, roles, operators, neon_binding=binding)
            result = module.evaluate(inventory, plan.inventory_sha256, observed, datetime.now(UTC))
            if result["database_gate_reasons"]:
                raise release.ReleaseDenied("fresh_source_retirement_or_obligations_unready")
            if type(plan) is FreshSourcePlan:
                with engine.connect() as connection, connection.begin():
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    connection.exec_driver_sql("SET LOCAL search_path=pg_catalog")
                    connection.exec_driver_sql("SET LOCAL statement_timeout='1000ms'")
                    module.neon_identity.verify_transport(connection, binding)
                    module.neon_identity.verify_native_target(connection, binding, 16391)
                    _source_owner_closed(
                        connection, plan.database_owner_oid, module.neon_identity.text
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
                    raise release.ReleaseDenied("fresh_source_automated_writer_not_retired")
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
            type(result) is not dict
            or result.get("status") != "verified"
            or result.get("purpose") != "final"
            or result.get("schema") != release.CUTOVER_CHAIN[0]
            or result.get("native_cut_observed") is not True
            or result.get("attempt_started") is not True
            or result.get("cleanup") != "group_absent_anchor_reaped"
            or result.get("production_released") is not False
        ):
            refusal = release.ReleaseDenied(
                "fresh_final_transfer_not_verified_reconcile_only"
            )
            if type(result) is dict and _TRANSFER_FIELDS <= result.keys():
                try:
                    refusal.__dict__["transfer"] = transfer_projection(
                        {name: result[name] for name in _TRANSFER_FIELDS}
                    )
                except ValueError:
                    pass  # Invalid observations retain the original fixed refusal.
            raise refusal

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
        self.verify_document_configuration(source)
        return self.stage(runtime_plan(source, target), service)

    def verify_fresh_revision(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        service: str,
        revision: str,
    ) -> None:
        self.verify_document_configuration(source)
        self.verify_revision(runtime_plan(source, target), service, revision)

    def verify_fresh_serving(
        self,
        source: release.Plan,
        target: release.FreshTargetPlan,
        service: str,
        revision: str,
    ) -> None:
        self.verify_document_configuration(source)
        self.verify_serving(runtime_plan(source, target), service, revision)

    def verify_document_configuration(self, source: release.Plan) -> dict[str, Any]:
        """Require both private workers and exactly the natively bound callers."""
        return self._verify_document_workers(source, runtime_phase=True)

    def verify_document_preparation(self, source: release.Plan) -> dict[str, Any]:
        """Check ready isolated workers before retiring any current writer."""
        return self._verify_document_workers(source, runtime_phase=False)

    def _verify_document_workers(
        self, source: release.Plan, *, runtime_phase: bool,
        successor_identity: Callable[[str, dict[str, Any]], None] | None = None,
        qualified_source_revision: bool = False,
    ) -> dict[str, Any]:
        """Read current native configuration/IAM; no invocation or isolation claim.

        Actual calls must originate from the staged Cloud Run consumers through
        approved internal routing. A local identity token cannot prove ingress.
        The exact fresh boundary supplies the authenticated v2 metadata reader.
        """
        documents = release.documents
        if type(qualified_source_revision) is not bool:
            raise release.ReleaseDenied("fresh_document_source_revision_selector_invalid")
        if runtime_phase and source.runtime_service_account != documents.API_INVOKER:
            raise release.ReleaseDenied("fresh_document_runtime_identity_mismatch")
        reader = getattr(self, "_get", None)
        if not callable(reader):
            raise release.ReleaseDenied("fresh_document_native_metadata_reader_missing")
        try:
            service = reader("run", documents.RESOURCE)
            if service.get("uri") != documents.ACCEPTED_ORIGIN:
                raise release.ReleaseDenied("fresh_document_origin_mismatch")
            revision = reader("run", service["latestReadyRevision"])
            keys = self._cloud(
                "iam", "service-accounts", "keys", "list",
                "--iam-account", documents.SERVICE_ACCOUNT,
            )
            if type(keys) is not list:
                raise release.ReleaseDenied("fresh_document_key_inventory_invalid")
            snapshot = {
                "service": service,
                "revision": revision,
                "service_iam": self._cloud(
                    "run", "services", "get-iam-policy", documents.SERVICE,
                    "--region", release.REGION,
                ),
                "project_iam": self._cloud("projects", "get-iam-policy", release.PROJECT),
                "service_account": self._cloud(
                    "iam", "service-accounts", "describe", documents.SERVICE_ACCOUNT,
                ),
                "service_account_keys": {"keys": keys},
            }
            # The optional upload caller is a fixed native identity, never an
            # environment-supplied arbitrary invoker. Read its current IAM UID
            # and key inventory whenever it appears in the service policy.
            coordinator_phase = runtime_phase and any(
                "serviceAccount:" + documents.UPLOAD_COORDINATOR in binding.get("members", [])
                for binding in snapshot["service_iam"].get("bindings", [])
            )
            if coordinator_phase:
                snapshot["upload_coordinator_service_account"] = self._cloud(
                    "iam", "service-accounts", "describe", documents.UPLOAD_COORDINATOR,
                )
                coordinator_keys = self._cloud(
                    "iam", "service-accounts", "keys", "list",
                    "--iam-account", documents.UPLOAD_COORDINATOR,
                )
                if type(coordinator_keys) is not list:
                    raise release.ReleaseDenied("fresh_upload_coordinator_key_inventory_invalid")
                snapshot["upload_coordinator_service_account_keys"] = {"keys": coordinator_keys}
            _, maintenance = documents.service_bindings_without_maintenance(snapshot["service_iam"], documents.SERVICE)
            if maintenance:
                snapshot["maintenance_service_account"] = self._cloud(
                    "iam", "service-accounts", "describe", documents.MAINTENANCE_ACCOUNT)
                maintenance_keys = self._cloud("iam", "service-accounts", "keys", "list",
                                               "--iam-account", documents.MAINTENANCE_ACCOUNT)
                if type(maintenance_keys) is not list:
                    raise release.ReleaseDenied("fresh_document_maintenance_key_inventory_invalid")
                snapshot["maintenance_service_account_keys"] = {"keys": maintenance_keys}
                snapshot["maintenance_role"] = self._cloud(
                    "iam", "roles", "describe", documents.MAINTENANCE_ROLE.rsplit("/", 1)[-1])
            image = service.get("template", {}).get("containers", [{}])[0].get("image")
            if image != documents.ACCEPTED_IMAGE:
                # Normal release requires fresh Build/GCS/Job/log acceptance.
                # The explicit maintenance callback proves historical identity
                # only; the full snapshot, TeX and routing checks below remain.
                if successor_identity is None:
                    from document_refresh_probe import (
                        verify_current_successor_for_release,
                    )

                    accepted = verify_current_successor_for_release(image, service)
                    if accepted.image != image:
                        raise release.ReleaseDenied("fresh_document_successor_identity_mismatch")
                elif successor_identity(image, service) is not None:
                    # A maintenance-only historical identity callback must not
                    # hand a stale NativeAcceptance to any serving path.
                    raise release.ReleaseDenied("fresh_document_historical_serving_acceptance_refused")
            source_options = {}
            if qualified_source_revision:
                from document_source_candidate import POLICY_SHA256

                if any(row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256" and row.get("value") == POLICY_SHA256
                       for row in service.get("template", {}).get("containers", [{}])[0].get("env", [])):
                    source_options["source_revision"] = True
            if source_options and image == documents.ACCEPTED_IMAGE:
                # The fixed historical image cannot acquire the new source
                # identity merely by changing its policy environment literal.
                raise release.ReleaseDenied("fresh_document_source_revision_base_image_refused")
            report = documents.check_snapshot(
                snapshot, image, source.release, api_phase=runtime_phase,
                coordinator_phase=coordinator_phase,
                candidate_v3=any(row.get("name") == "DOCUMENT_WORKER_POLICY_SHA256"
                                 and row.get("value") == documents.CANDIDATE_POLICY_SHA256
                                 for row in service.get("template", {}).get("containers", [{}])[0].get("env", [])),
                **source_options,
            )
            tex = release.native_tex
            tex_origin = getattr(tex, "ACCEPTED_ORIGIN", "")
            tex_image = getattr(tex, "ACCEPTED_IMAGE", "")
            # The root binds only a natively accepted image and actual service
            # URI. Missing bindings refuse before any isolation/cutover writes.
            tex.api_environment(tex_origin, tex_image)
            tex_service = reader("run", tex.RESOURCE)
            if tex_service.get("uri") != tex_origin:
                raise release.ReleaseDenied("fresh_native_tex_origin_mismatch")
            tex_keys = self._cloud(
                "iam", "service-accounts", "keys", "list", "--iam-account", tex.SERVICE_ACCOUNT,
            )
            if type(tex_keys) is not list:
                raise release.ReleaseDenied("fresh_native_tex_key_inventory_invalid")
            tex_snapshot = {
                "service": tex_service,
                "revision": reader("run", tex_service["latestReadyRevision"]),
                "service_iam": self._cloud(
                    "run", "services", "get-iam-policy", tex.SERVICE, "--region", release.REGION,
                ),
                "project_iam": snapshot["project_iam"],
                "service_account": self._cloud(
                    "iam", "service-accounts", "describe", tex.SERVICE_ACCOUNT,
                ),
                "service_account_keys": {"keys": tex_keys},
            }
            tex_coordinator_phase = runtime_phase and any(
                "serviceAccount:" + documents.UPLOAD_COORDINATOR in binding.get("members", [])
                for binding in tex_snapshot["service_iam"].get("bindings", [])
            )
            if tex_coordinator_phase:
                # Native upload compilation also requires the exact already
                # admitted inspector caller; reuse its freshly read IAM facts.
                if not coordinator_phase:
                    raise release.ReleaseDenied("fresh_tex_coordinator_inspector_binding_missing")
                for name in ("upload_coordinator_service_account", "upload_coordinator_service_account_keys"):
                    tex_snapshot[name] = snapshot[name]
            tex_report = tex.check_snapshot(
                tex_snapshot, tex_image, source.release, runtime_phase=runtime_phase,
                coordinator_phase=tex_coordinator_phase,
            )
            routing = release.document_routing
            routing.verify_network(
                self._cloud("compute", "networks", "describe", routing.NETWORK),
                self._cloud("compute", "networks", "subnets", "describe", routing.SUBNET,
                            "--region", release.REGION),
                self._cloud("dns", "managed-zones", "describe", routing.DNS_ZONE),
                self._cloud("dns", "record-sets", "list", "--zone", routing.DNS_ZONE, "--limit", "5"),
                self._cloud("compute", "routes", "list", "--filter", "network=" + routing.NETWORK_URL,
                            "--limit", "65"),
            )
            return {**report, "native_tex": tex_report,
                    "native_authenticated_metadata_read": True}
        except release.ReleaseDenied:
            raise
        except Exception:
            raise release.ReleaseDenied("fresh_document_native_configuration_unverified") from None

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
