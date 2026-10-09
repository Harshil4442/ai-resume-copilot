"""Read-only, bounded database gate; never a cloud cutover authorization."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.pool import NullPool

MAX_INPUT_BYTES = 131072
MAX_ROWS = 256
SCHEMAS = {"20261008_0009", "20261009_0010", "20261009_0011"}
FINANCIAL_SCHEMAS = {"20261009_0010", "20261009_0011"}
CANDIDATE_TABLES = {"candidate_password_accounts", "candidate_lifetime_history"}
CANDIDATE_COLUMNS = {
    "candidate_lifetime_history": {"registration_id": "character varying(36)"},
    "candidate_password_accounts": {
        "user_id": "integer",
        "subject_uuid": "character varying(36)",
        "account_binding_id": "character varying(36)",
        "principal_sha256": "character varying(64)",
        "credential_sha256": "character varying(64)",
        "auth_generation": "bigint",
        "state": "character varying(24)",
    },
}
TABLES = (
    "analysis_runs",
    "model_call_events",
    "employer_applications",
    "service_credit_reservations",
    "employer_application_attempts",
)
WRITE_PRIVILEGES = "INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER"
COLUMN_WRITE_PRIVILEGES = "INSERT,UPDATE,REFERENCES"
RELATIONS = "('r','p','v','f','m')"
USAGE_PROVENANCE = {
    "google": {"google-total-including-thoughts-v1", "google-explicit-thoughts-v1"},
    "openai": {"chat-completion-including-reasoning-v1"},
    "openai_compatible": {"chat-completion-including-reasoning-v1"},
}
# Include both inherited effective privileges and every SET ROLE target. ADMIN
# OPTION can enable a currently disabled SET path, so reject that capability
# conservatively even when its presently reachable target appears read-only.
ROLE_CAPABILITIES = f"""
 SELECT r.oid, r.rolname, r.rolcanlogin,
  (SELECT count(*) FROM pg_class c
   WHERE c.relnamespace = :namespace AND c.relkind IN {RELATIONS}
    AND EXISTS (SELECT 1 FROM pg_roles target
      WHERE (target.oid = r.oid OR pg_has_role(r.oid,target.oid,'SET'))
       AND (has_table_privilege(target.oid,c.oid,:privileges)
        OR has_any_column_privilege(target.oid,c.oid,:column_privileges)))) AS writes,
  EXISTS (SELECT 1 FROM pg_roles target
   WHERE (target.oid = r.oid OR pg_has_role(r.oid,target.oid,'SET'))
    AND has_schema_privilege(target.oid,:namespace,'CREATE')) AS schema_create,
  EXISTS (SELECT 1 FROM pg_auth_members membership
   WHERE membership.admin_option
    AND (membership.member = r.oid OR pg_has_role(r.oid,membership.member,'MEMBER')))
    AS membership_administration
 FROM pg_roles r
"""


class Denied(RuntimeError):
    """Only a fixed, nonsecret reason code may escape observation."""


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Consumer(Strict):
    # Opaque local labels, not resource names, URLs or credential material.
    label: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    kind: Literal["api", "analysis", "employer", "migration", "developer", "other"]
    credential: Literal["retired", "replacement_runtime", "replacement_migration", "none"]
    release: str = Field(pattern=r"^[a-f0-9]{40}$")
    state: Literal["fenced", "staged", "serving", "unknown"]
    generation_disabled: bool


class Inventory(Strict):
    format_version: Literal[1]
    reviewed_at: str
    scope: Literal["named_project_and_declared_external_consumers"]
    consumer_inventory_complete: bool
    shared_credentials_resolved: bool
    unknown_consumer_count: int = Field(ge=0, le=10000)
    provider_fencing_verified: bool
    queue_admission_closed: bool
    protected_backup_verified: bool
    serving_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    candidate_commit: str = Field(pattern=r"^[a-f0-9]{40}$")
    candidate_image: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    expected_database_schema: Literal["20261008_0009", "20261009_0010", "20261009_0011"]
    candidate_schema: Literal["20261009_0010", "20261009_0011"]
    expected_system_identifier_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_database_oid: int = Field(gt=0)
    database_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    # Explicit aliases resolve role names in process, never into the report.
    role_env: dict[Literal["retired", "replacement_runtime", "replacement_migration"], str]
    database_operator_role_env: list[str] = Field(max_length=16)
    consumers: list[Consumer] = Field(min_length=4, max_length=128)

    @field_validator("role_env")
    @classmethod
    def role_aliases(cls, value: dict[str, str]) -> dict[str, str]:
        if set(value) != {"retired", "replacement_runtime", "replacement_migration"}:
            raise ValueError("Three distinct credential classes are required")
        if len(set(value.values())) != 3 or any(
            not re.fullmatch(r"HIREWIZ_CUTOVER_ROLE_[A-Z_]{1,32}", item) for item in value.values()
        ):
            raise ValueError("Only distinct dedicated environment aliases are supported")
        return value


class LiabilityQuote(Strict):
    version: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/-]+$")
    input_rate_micros_per_million: int = Field(gt=0, le=10**12)
    output_rate_micros_per_million: int = Field(gt=0, le=10**12)
    max_input_tokens: int = Field(gt=0, le=1_000_000)
    max_output_tokens: int = Field(gt=0, le=1_000_000)
    output_limit_parameter: Literal["max_output_tokens", "max_completion_tokens", "max_tokens"]
    token_estimator: Literal["utf8-json-bytes-framing-v1"]
    operation_policy_version: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/-]+$"
    )
    admission_policy_version: str = Field(
        min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/-]+$"
    )
    authorized_ceiling_micros: int = Field(gt=0, le=10**12)


def _quoted_cost(quote: LiabilityQuote, input_tokens: int, output_tokens: int) -> int:
    return (
        input_tokens * quote.input_rate_micros_per_million
        + output_tokens * quote.output_rate_micros_per_million
        + 999_999
    ) // 1_000_000


def _known_settled(row: Mapping[str, Any]) -> bool:
    # PostgreSQL's 'settled' label alone never proves returned usage, quote
    # identity or the arithmetic. Invalid metadata remains an entire cost hold.
    try:
        quote = LiabilityQuote.model_validate(row["pricing_quote"])
        provider = row["provider"]
        if (
            provider not in USAGE_PROVENANCE
            or row["usage_provenance"] not in USAGE_PROVENANCE[provider]
        ):
            return False
        if (
            provider == "google"
            and quote.output_limit_parameter != "max_output_tokens"
            or provider == "openai"
            and quote.output_limit_parameter != "max_completion_tokens"
            or provider == "openai_compatible"
            and quote.output_limit_parameter == "max_output_tokens"
        ):
            return False
        tokens = (row["input_tokens"], row["output_tokens"], row["input_token_estimate"])
        if any(type(value) is not int or value < 0 or value > 2_147_483_647 for value in tokens):
            return False
        input_tokens, output_tokens, estimate = tokens
        if estimate <= 0:
            return False
        if (
            input_tokens > quote.max_input_tokens
            or output_tokens > quote.max_output_tokens
            or estimate > quote.max_input_tokens
        ):
            return False
        reserved, settled = row["reserved_cost_micros"], row["settled_cost_micros"]
        if type(reserved) is not int or type(settled) is not int or reserved <= 0 or settled < 0:
            return False
        return bool(
            row["cost_state"] == "settled"
            and row["currency"] == "USD"
            and row["settled_at"] is not None
            and row["created_at"] is not None
            and row["settled_at"] >= row["created_at"]
            and re.fullmatch(r"[a-f0-9]{64}", row["endpoint_key"])
            and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,120}", row["model"])
            and type(row["attempt_number"]) is int
            and 1 <= row["attempt_number"] <= 3
            and reserved == _quoted_cost(quote, estimate, quote.max_output_tokens)
            and settled == _quoted_cost(quote, input_tokens, output_tokens)
            and settled <= reserved <= quote.authorized_ceiling_micros
        )
    except (ValidationError, KeyError, ValueError, TypeError):
        return False


def _group_resolution(
    rows: list[Mapping[str, Any]], individually_resolved: list[bool]
) -> tuple[list[bool], int]:
    groups: dict[str, list[int]] = {}
    resolved = individually_resolved.copy()
    invalid_groups = 0
    for index, row in enumerate(rows):
        group = row["financial_group_id"]
        if not isinstance(group, str) or not re.fullmatch(r"[A-Za-z0-9_:-]{1,64}", group):
            resolved[index] = False
            invalid_groups += 1
            continue
        groups.setdefault(group, []).append(index)
    for indexes in groups.values():
        okay = all(individually_resolved[index] for index in indexes)
        if okay:
            ordered = sorted(indexes, key=lambda index: rows[index]["attempt_number"])
            attempts = [rows[index]["attempt_number"] for index in ordered]
            okay = attempts == list(range(1, len(attempts) + 1))
            previous: list[Mapping[str, Any]] = []
            for index in ordered:
                row = rows[index]
                if not okay:
                    break
                quote = LiabilityQuote.model_validate(row["pricing_quote"])
                if previous and row["created_at"] < previous[-1]["created_at"]:
                    okay = False
                    break
                # A settlement at the same timestamp does not prove its commit
                # preceded this admission. Preserve the entire earlier hold
                # until strictly earlier settlement is recorded.
                prior_exposure = sum(
                    prior["settled_cost_micros"]
                    if prior["settled_at"] < row["created_at"]
                    else prior["reserved_cost_micros"]
                    for prior in previous
                )
                cumulative_cost = (
                    sum(prior["settled_cost_micros"] for prior in previous)
                    + row["settled_cost_micros"]
                )
                if (
                    prior_exposure + row["reserved_cost_micros"] > quote.authorized_ceiling_micros
                    or cumulative_cost > quote.authorized_ceiling_micros
                ):
                    okay = False
                    break
                previous.append(row)
        if not okay:
            invalid_groups += 1
            for index in indexes:
                resolved[index] = False
    return resolved, invalid_groups


def _liabilities(connection: Any, quoted: str) -> dict[str, Any]:
    rows = (
        connection.execute(
            text(
                f"SELECT reserved_cost_micros, settled_cost_micros, input_tokens, output_tokens, "
                f"usage_provenance, cost_state, settled_at, currency, provider, model, attempt_number, "
                f"CASE WHEN octet_length(pricing_quote::text) <= 8192 THEN pricing_quote ELSE NULL END AS pricing_quote, "
                f"input_token_estimate, created_at, endpoint_key, financial_group_id FROM {quoted}.model_cost_liabilities "
                f"ORDER BY id LIMIT 257"
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > MAX_ROWS:
        raise Denied("database_liability_inventory_bound")
    individually_resolved = [_known_settled(row) for row in rows]
    resolved, invalid_groups = _group_resolution(rows, individually_resolved)
    return {
        "present": True,
        "row_count": len(rows),
        "unresolved_count": sum(not value for value in resolved),
        "invalid_group_count": invalid_groups,
        "recorded_individually_valid_settled_cost_micros": sum(
            row["settled_cost_micros"]
            for row, okay in zip(rows, individually_resolved, strict=True)
            if okay
        ),
        "held_cost_micros": (
            sum(
                row["reserved_cost_micros"]
                for row, okay in zip(rows, resolved, strict=True)
                if not okay
            )
            if all(row["reserved_cost_micros"] >= 0 for row in rows)
            else None
        ),
        "settled_cost_micros": sum(
            row["settled_cost_micros"] for row, okay in zip(rows, resolved, strict=True) if okay
        ),
        "amounts_are_invoice_proof": False,
    }


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise Denied("inventory_duplicate_key")
        result[key] = value
    return result


def load_inventory(path: Path, expected_hash: str, now: datetime) -> tuple[Inventory, str]:
    if not re.fullmatch(r"[a-f0-9]{64}", expected_hash):
        raise Denied("inventory_hash_invalid")
    with path.open("rb") as source:
        raw = source.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise Denied("inventory_size_bound")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != expected_hash:
        raise Denied("inventory_hash_mismatch")
    data = Inventory.model_validate(json.loads(raw, object_pairs_hook=_unique))
    reviewed = datetime.fromisoformat(data.reviewed_at)
    if reviewed.tzinfo is None or not now - timedelta(hours=24) <= reviewed <= now:
        raise Denied("inventory_expired_or_future")
    labels = [item.label for item in data.consumers]
    if len(set(labels)) != len(labels):
        raise Denied("inventory_duplicate_consumer")
    if not {"api", "analysis", "employer", "migration"}.issubset(
        item.kind for item in data.consumers
    ):
        raise Denied("inventory_required_consumer_missing")
    return data, digest


def _count(connection: Any, sql: str, parameters: dict[str, Any] | None = None) -> int:
    return int(connection.execute(text(sql), parameters or {}).scalar_one())


def _definer_capabilities(
    connection: Any, role_rows: list[Any], capabilities: Mapping[int, Mapping[str, Any]]
) -> tuple[dict[int, dict[str, int]], dict[str, Any]]:
    # Cross-namespace routines may write application tables. Inspect catalog
    # identity/authority only, never parse a routine body to certify no writes.
    total = _count(connection, "SELECT count(*) FROM pg_proc WHERE prosecdef")
    if total > MAX_ROWS:
        raise Denied("database_function_inventory_bound")
    routines = (
        connection.execute(
            text(
                "SELECT p.oid, p.proowner, p.prokind, n.oid AS namespace_oid, "
                "l.lanname, l.lanpltrusted FROM pg_proc p "
                "LEFT JOIN pg_namespace n ON n.oid = p.pronamespace "
                "LEFT JOIN pg_language l ON l.oid = p.prolang "
                "WHERE p.prosecdef ORDER BY p.oid LIMIT 257"
            )
        )
        .mappings()
        .all()
    )
    if len(routines) > MAX_ROWS:
        raise Denied("database_function_inventory_bound")
    if len(routines) != total:
        raise Denied("database_function_inventory_incomplete")
    role_by_oid = {int(row[0]): row for row in role_rows}
    if any(row["proowner"] not in role_by_oid or row["namespace_oid"] is None for row in routines):
        raise Denied("database_function_inventory_incomplete")
    if any(
        row["prokind"] not in {"f", "p"}
        or row["lanname"] not in {"sql", "plpgsql"}
        or row["lanpltrusted"] is not True
        for row in routines
    ):
        raise Denied("database_function_inventory_unsupported")
    owner_writers = [
        oid
        for oid, row in role_by_oid.items()
        if any(bool(item) for item in row[3:])
        or capabilities[oid]["writes"]
        or capabilities[oid]["schema_create"]
        or capabilities[oid]["membership_administration"]
    ]
    # has_function_privilege includes PUBLIC and inherited effective EXECUTE.
    # Check every SET target too, including non-inherited and transitive paths.
    # Every executable SECURITY DEFINER routine is unresolved authority even if
    # no known owner write privilege is visible: nesting/external effects and
    # arbitrary routine bodies cannot be proven safe by this bounded inventory.
    executable = (
        connection.execute(
            text(
                "SELECT r.oid, count(*) AS executable_count, "
                "count(*) FILTER (WHERE p.proowner = ANY(CAST(:owner_writers AS oid[]))) "
                "AS known_writer_owner_count FROM pg_roles r CROSS JOIN pg_proc p "
                "WHERE p.prosecdef AND EXISTS (SELECT 1 FROM pg_roles target "
                "WHERE (target.oid = r.oid OR pg_has_role(r.oid,target.oid,'SET')) "
                "AND has_function_privilege(target.oid,p.oid,'EXECUTE')) "
                "GROUP BY r.oid ORDER BY r.oid LIMIT 257"
            ),
            {"owner_writers": owner_writers},
        )
        .mappings()
        .all()
    )
    if len(executable) > MAX_ROWS or any(row["oid"] not in role_by_oid for row in executable):
        raise Denied("database_function_authority_inventory_incomplete")
    counts = {oid: {"executable_count": 0, "known_writer_owner_count": 0} for oid in role_by_oid}
    for row in executable:
        if not 0 <= row["known_writer_owner_count"] <= row["executable_count"] <= len(routines):
            raise Denied("database_function_authority_inventory_incomplete")
        counts[int(row["oid"])] = {
            "executable_count": int(row["executable_count"]),
            "known_writer_owner_count": int(row["known_writer_owner_count"]),
        }
    return counts, {
        "scope": "all_security_definer_routines_in_current_database",
        "routine_count": len(routines),
        "supported_metadata": "trusted_sql_or_plpgsql_functions_and_procedures",
        "body_read_or_audited": False,
        "executable_routine_write_effects_proven_absent": False,
        "classification": "every_executable_definer_is_unresolved_writer_authority",
    }


def _candidate_projection_shape(connection: Any, namespace_oid: int) -> None:
    # Catalog metadata only: no candidate row, credential or subject is loaded.
    # This certifies the narrow schema shape, not account/native authority.
    rows = connection.execute(
        text(
            "SELECT c.relname, c.relkind, a.attname, "
            "pg_catalog.format_type(a.atttypid,a.atttypmod) AS column_type, a.attnotnull "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
            "WHERE c.relnamespace=:namespace AND c.relname = ANY(CAST(:tables AS text[])) "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY c.oid,a.attnum LIMIT 257"
        ),
        {"namespace": namespace_oid, "tables": sorted(CANDIDATE_TABLES)},
    ).mappings().all()
    if len(rows) > MAX_ROWS:
        raise Denied("database_candidate_projection_inventory_bound")
    found: dict[str, dict[str, str]] = {}
    for row in rows:
        if row["relkind"] != "r" or row["attnotnull"] is not True:
            raise Denied("database_candidate_projection_shape_mismatch")
        found.setdefault(str(row["relname"]), {})[str(row["attname"])] = str(row["column_type"])
    if found != CANDIDATE_COLUMNS:
        raise Denied("database_candidate_projection_shape_mismatch")


def observe(
    engine: Engine,
    inventory: Inventory,
    roles: Mapping[str, str],
    operator_roles: tuple[str, ...] = (),
) -> dict[str, Any]:
    if (
        engine.dialect.name != "postgresql"
        or set(roles) != set(inventory.role_env)
        or len(set(roles.values())) != 3
    ):
        raise Denied("database_or_role_configuration_invalid")
    if any(
        not item or len(item) > 63 or "\x00" in item for item in (*roles.values(), *operator_roles)
    ):
        raise Denied("role_configuration_invalid")
    # A standalone fresh direct connection, never a shared Session or pooled tx.
    # psycopg connect timeout is supplied by the CLI; every SQL query is bounded.
    with engine.connect() as connection:
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '1000ms'")
            connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '10000ms'")
            identifier = connection.execute(
                text("SELECT system_identifier::text FROM pg_control_system()")
            ).scalar_one()
            identifier_hash = hashlib.sha256(str(identifier).encode()).hexdigest()
            database_oid = _count(
                connection, "SELECT oid FROM pg_database WHERE datname = current_database()"
            )
            namespace = inventory.database_namespace
            # Bind both namespace and cluster/database identity; no search_path trust.
            schema_oid = connection.execute(
                text("SELECT oid FROM pg_namespace WHERE nspname = :namespace"),
                {"namespace": namespace},
            ).scalar_one_or_none()
            if schema_oid is None:
                raise Denied("database_namespace_missing")
            table_rows = connection.execute(
                text(
                    f"SELECT relname, oid FROM pg_class WHERE relnamespace = :namespace AND relkind IN {RELATIONS} ORDER BY oid LIMIT 257"
                ),
                {"namespace": schema_oid},
            ).all()
            if len(table_rows) > MAX_ROWS:
                raise Denied("database_table_inventory_bound")
            table_oids = {str(row[0]): int(row[1]) for row in table_rows}
            if not set(TABLES).issubset(table_oids) or "alembic_version" not in table_oids:
                raise Denied("database_required_tables_missing")
            quoted = connection.dialect.identifier_preparer.quote(namespace)
            versions = list(
                connection.execute(
                    text(f"SELECT version_num FROM {quoted}.alembic_version LIMIT 3")
                ).scalars()
            )
            if len(versions) != 1 or versions[0] not in SCHEMAS:
                raise Denied("database_schema_unsupported")
            version = str(versions[0])
            # A schema0010 release cannot silently adopt a schema0011 database.
            if version == "20261009_0011" and inventory.candidate_schema != "20261009_0011":
                raise Denied("database_schema_unsupported")
            if version in FINANCIAL_SCHEMAS and "model_cost_liabilities" not in table_oids:
                raise Denied("database_liability_table_missing")
            if version == "20261008_0009" and "model_cost_liabilities" in table_oids:
                raise Denied("database_schema_shape_mismatch")
            if version == "20261009_0011":
                if not CANDIDATE_TABLES.issubset(table_oids):
                    raise Denied("database_candidate_projection_tables_missing")
                _candidate_projection_shape(connection, int(schema_oid))
            elif CANDIDATE_TABLES.intersection(table_oids):
                raise Denied("database_schema_shape_mismatch")
            # Visibility is required: otherwise another role's sessions may have NULL
            # activity fields. Counts alone cannot prove old executors absent.
            visibility = bool(
                connection.execute(
                    text(
                        "SELECT r.rolsuper OR pg_has_role(current_user, 'pg_read_all_stats', 'USAGE') FROM pg_roles r WHERE rolname = current_user"
                    )
                ).scalar_one()
            )
            if not visibility:
                raise Denied("database_session_inventory_visibility_missing")
            role_rows = connection.execute(
                text(
                    "SELECT oid, rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_roles ORDER BY oid LIMIT 257"
                )
            ).all()
            if len(role_rows) > MAX_ROWS:
                raise Denied("database_role_inventory_bound")
            role_by_name = {str(row[1]): row for row in role_rows}
            if not set((*roles.values(), *operator_roles)).issubset(role_by_name):
                raise Denied("database_expected_role_missing")

            parameters = {
                "namespace": schema_oid,
                "privileges": WRITE_PRIVILEGES,
                "column_privileges": COLUMN_WRITE_PRIVILEGES,
            }
            capability_rows = (
                connection.execute(
                    text(ROLE_CAPABILITIES + " ORDER BY r.oid LIMIT 257"), parameters
                )
                .mappings()
                .all()
            )
            if len(capability_rows) > MAX_ROWS:
                raise Denied("database_role_inventory_bound")
            capabilities = {int(row["oid"]): row for row in capability_rows}
            definer_counts, definer_inventory = _definer_capabilities(
                connection, role_rows, capabilities
            )
            observed_roles: dict[str, Any] = {}
            for alias, name in roles.items():
                row = role_by_name[name]
                capability = capabilities[int(row[0])]
                role_sessions = _count(
                    connection,
                    "SELECT count(*) FROM pg_stat_activity WHERE usesysid = :role AND pid <> pg_backend_pid()",
                    {"role": row[0]},
                )
                observed_roles[alias] = {
                    "login_enabled": bool(row[2]),
                    "elevated": any(bool(item) for item in row[3:]),
                    "elevated_role_membership": bool(
                        connection.execute(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls) AND pg_has_role(:role,oid,'MEMBER'))"
                            ),
                            {"role": name},
                        ).scalar_one()
                    ),
                    "writable_table_count": int(capability["writes"]),
                    "schema_create": bool(capability["schema_create"]),
                    "membership_administration": bool(capability["membership_administration"]),
                    "executable_security_definer_count": definer_counts[int(row[0])][
                        "executable_count"
                    ],
                    "executable_security_definer_known_writer_owner_count": definer_counts[
                        int(row[0])
                    ]["known_writer_owner_count"],
                    "active_session_count": role_sessions,
                }
            writers = [
                row
                for row in capability_rows
                if row["rolcanlogin"]
                and (
                    row["writes"]
                    or row["schema_create"]
                    or row["membership_administration"]
                    or definer_counts[int(row["oid"])]["executable_count"]
                )
            ]
            known = {roles["replacement_runtime"], roles["replacement_migration"], *operator_roles}
            current_oid = _count(
                connection, "SELECT oid FROM pg_roles WHERE rolname = current_user"
            )
            current = capabilities[current_oid]
            unexpected_writers = sum(1 for row in writers if str(row["rolname"]) not in known)
            # Operator classification cannot override a writable observer. The
            # observation transaction is read-only AND the observing identity
            # must have no direct/column/SET/admin/schema or executable-definer
            # authority. READ ONLY for this tx does not fence its later txs.
            own_writer = bool(
                current["writes"]
                or current["schema_create"]
                or current["membership_administration"]
                or definer_counts[current_oid]["executable_count"]
            )
            sessions = connection.execute(
                text(
                    "SELECT usesysid, datid FROM pg_stat_activity WHERE pid <> pg_backend_pid() AND usesysid IS NOT NULL ORDER BY pid LIMIT 257"
                )
            ).all()
            if len(sessions) > MAX_ROWS or len(writers) > MAX_ROWS:
                raise Denied("database_session_or_writer_inventory_bound")
            unrelated_sessions = sum(
                1
                for row in sessions
                if int(row[0]) not in {int(role_by_name[name][0]) for name in known}
            )
            prepared_transactions = _count(
                connection,
                "SELECT count(*) FROM pg_prepared_xacts WHERE database = current_database()",
            )
            legacy_events = _count(connection, f"SELECT count(*) FROM {quoted}.model_call_events")
            attempted_events = _count(
                connection,
                f"SELECT count(*) FROM {quoted}.model_call_events WHERE status NOT IN ('succeeded','failed','invalid_output')",
            )
            active_runs = _count(
                connection,
                f"SELECT count(*) FROM {quoted}.analysis_runs WHERE status NOT IN ('succeeded','failed','cancelled')",
            )
            uncertain_applications = _count(
                connection,
                f"SELECT count(*) FROM {quoted}.employer_applications WHERE status NOT IN ('needs_action','ready','approved','manual_handoff','confirmed','failed','cancelled')",
            )
            pending_application_attempts = _count(
                connection,
                f"SELECT count(*) FROM {quoted}.employer_application_attempts WHERE state NOT IN ('confirmed','failed')",
            )
            pending_credit_holds = _count(
                connection,
                f"SELECT count(*) FROM {quoted}.service_credit_reservations WHERE state <> 'settled' OR reserved_amount <> committed_amount + released_amount OR reserved_amount < 0 OR committed_amount < 0 OR released_amount < 0",
            )
            liability_summary: dict[str, Any] = {
                "present": False,
                "row_count": None,
                "unresolved_count": None,
                "held_cost_micros": None,
                "settled_cost_micros": None,
            }
            invalid_model_budget_runs = 0
            invalid_model_call_states = 0
            if version in FINANCIAL_SCHEMAS:
                invalid_model_budget_runs = _count(
                    connection,
                    f"SELECT count(*) FROM {quoted}.analysis_runs WHERE model_cost_reserved_micros <> 0 OR model_cost_settled_micros < 0 OR model_cost_state NOT IN ('unquoted','active') OR (model_cost_state = 'unquoted' AND (model_cost_quote IS NOT NULL OR model_cost_settled_micros <> 0)) OR (model_cost_state = 'active' AND (model_cost_quote IS NULL OR model_cost_ceiling_micros IS NULL OR model_cost_ceiling_micros <= 0 OR model_cost_settled_micros > model_cost_ceiling_micros))",
                )
                invalid_model_call_states = _count(
                    connection,
                    f"SELECT count(*) FROM {quoted}.model_call_events WHERE cost_state NOT IN ('unavailable','settled') OR (cost_state = 'settled' AND (liability_id IS NULL OR settled_at IS NULL OR settled_cost_micros IS NULL OR settled_cost_micros < 0)) OR (cost_state = 'unavailable' AND (liability_id IS NOT NULL OR pricing_quote IS NOT NULL OR reserved_cost_micros IS NOT NULL OR settled_cost_micros IS NOT NULL OR settled_at IS NOT NULL OR token_estimate_provenance IS NOT NULL OR usage_provenance IS NOT NULL OR output_token_limit IS NOT NULL))",
                )
                liability_summary = _liabilities(connection, quoted)
            return {
                "observation": "single_read_only_repeatable_read_snapshot",
                "system_identifier_sha256": identifier_hash,
                "database_oid": database_oid,
                "schema": version,
                "role_inventory_count": len(role_rows),
                "writer_login_role_count": len(writers),
                "unexpected_writer_login_role_count": unexpected_writers,
                "inspector_has_writer_authority": own_writer,
                "inspector_executable_security_definer_count": definer_counts[current_oid][
                    "executable_count"
                ],
                "inspector_executable_security_definer_known_writer_owner_count": definer_counts[
                    current_oid
                ]["known_writer_owner_count"],
                "security_definer_inventory": definer_inventory,
                "unclassified_session_count": unrelated_sessions,
                "roles": observed_roles,
                "pending_analysis_run_count": active_runs,
                "attempted_model_event_count": attempted_events,
                "legacy_model_event_count": legacy_events,
                "legacy_history_is_cost_proof": False,
                "uncertain_application_count": uncertain_applications,
                "pending_application_attempt_count": pending_application_attempts,
                "pending_service_credit_hold_count": pending_credit_holds,
                "declared_database_operator_count": len(operator_roles),
                "prepared_transaction_count": prepared_transactions,
                "invalid_or_held_model_budget_run_count": invalid_model_budget_runs,
                "invalid_model_call_financial_state_count": invalid_model_call_states,
                "liabilities": liability_summary,
            }


def evaluate(
    inventory: Inventory, digest: str, observed: dict[str, Any], now: datetime
) -> dict[str, Any]:
    reasons: list[str] = []
    if (
        observed["system_identifier_sha256"] != inventory.expected_system_identifier_sha256
        or observed["database_oid"] != inventory.expected_database_oid
    ):
        reasons.append("database_identity_mismatch")
    if observed["schema"] != inventory.expected_database_schema:
        reasons.append("database_schema_mismatch")
    if (
        not inventory.consumer_inventory_complete
        or not inventory.shared_credentials_resolved
        or inventory.unknown_consumer_count
    ):
        reasons.append("consumer_inventory_unresolved")
    if any(
        item.state == "unknown"
        or not item.generation_disabled
        or (item.credential == "retired" and item.state != "fenced")
        for item in inventory.consumers
    ):
        reasons.append("consumer_fencing_assertions_incomplete")
    if any(
        item.credential.startswith("replacement_") and item.release != inventory.candidate_commit
        for item in inventory.consumers
    ):
        reasons.append("candidate_consumer_release_mismatch")
    retired = observed["roles"]["retired"]
    if (
        retired["login_enabled"]
        or retired["elevated"]
        or retired["elevated_role_membership"]
        or retired["writable_table_count"]
        or retired["schema_create"]
        or retired["membership_administration"]
        or retired["executable_security_definer_count"]
    ):
        reasons.append("retired_database_role_not_fenced")
    if retired["active_session_count"]:
        reasons.append("retired_database_sessions_present")
    runtime = observed["roles"]["replacement_runtime"]
    if (
        runtime["elevated"]
        or runtime["elevated_role_membership"]
        or runtime["membership_administration"]
    ):
        reasons.append("replacement_runtime_role_elevated")
    if observed["inspector_has_writer_authority"]:
        reasons.append("database_observer_has_writer_authority")
    if observed["unexpected_writer_login_role_count"] or observed["unclassified_session_count"]:
        reasons.append("unclassified_database_writers_or_sessions")
    if (
        observed["pending_analysis_run_count"]
        or observed["attempted_model_event_count"]
        or observed["uncertain_application_count"]
        or observed["liabilities"]["unresolved_count"]
        or observed["pending_application_attempt_count"]
        or observed["pending_service_credit_hold_count"]
        or observed["prepared_transaction_count"]
        or observed["invalid_or_held_model_budget_run_count"]
        or observed["invalid_model_call_financial_state_count"]
    ):
        reasons.append("unresolved_obligations")
    return {
        "format_version": 1,
        "observed_at": now.isoformat(),
        "inventory_sha256": digest,
        "caller_release_pins_not_verified": {
            "serving_commit": inventory.serving_commit,
            "candidate_commit": inventory.candidate_commit,
            "candidate_image": inventory.candidate_image,
            "candidate_schema": inventory.candidate_schema,
        },
        "database_gate_passed": not reasons,
        "database_gate_reasons": reasons,
        "cutover_ready": False,
        "cutover_ready_reasons": [
            "read_only_database_observation_is_not_cutover_authority",
            "cloud_provider_fencing_backup_and_consumer_completeness_require_independent_verification",
        ],
        "observed_database": observed,
        "caller_assertions_not_verified": {
            "consumer_inventory_complete": inventory.consumer_inventory_complete,
            "shared_credentials_resolved": inventory.shared_credentials_resolved,
            "unknown_consumer_count": inventory.unknown_consumer_count,
            "provider_fencing_verified": inventory.provider_fencing_verified,
            "queue_admission_closed": inventory.queue_admission_closed,
            "protected_backup_verified": inventory.protected_backup_verified,
            "consumer_count": len(inventory.consumers),
        },
        "limits": [
            "A point-in-time SQL snapshot does not drain traffic, stop old workers, revoke provider keys, prevent later reconnects, or prove invoice reconciliation.",
            "No source/CI/image/cloud metadata is verified; release pins are caller assertions.",
            "No role names, session queries, URLs, candidate identifiers or credentials are included.",
            "Retained financial evidence is never changed; no migration, role change, termination, queue change or cloud call occurs.",
            "Every executable SECURITY DEFINER routine across this database is unresolved writer authority; no routine body or external side effect is certified read-only.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inventory", type=Path)
    parser.add_argument("--inventory-sha256", required=True)
    args = parser.parse_args()
    now = datetime.now(UTC)
    engine: Engine | None = None
    try:
        inventory, digest = load_inventory(args.inventory, args.inventory_sha256, now)
        roles = {str(alias): os.environ[name] for alias, name in inventory.role_env.items()}
        if len(set(inventory.database_operator_role_env)) != len(
            inventory.database_operator_role_env
        ) or any(
            not re.fullmatch(r"HIREWIZ_CUTOVER_OPERATOR_[A-Z_]{1,32}", name)
            for name in inventory.database_operator_role_env
        ):
            raise Denied("operator_role_alias_invalid")
        operator_roles = tuple(os.environ[name] for name in inventory.database_operator_role_env)
        if set(operator_roles) & set(roles.values()):
            raise Denied("operator_role_class_overlap")
        # Never use app.database/settings: that can load local files/create tables.
        url = os.environ["HIREWIZ_CUTOVER_DATABASE_URL"]
        engine = create_engine(
            url,
            poolclass=NullPool,
            echo=False,
            hide_parameters=True,
            connect_args={
                "connect_timeout": 5,
                "application_name": "hirewiz_readonly_cutover_preflight",
            },
        )
        result = evaluate(inventory, digest, observe(engine, inventory, roles, operator_roles), now)
        print(json.dumps(result, sort_keys=True, allow_nan=False))
        return 0 if result["database_gate_passed"] else 65
    except Denied as error:
        # Codes are internally selected; never stringify provider/database errors.
        print(
            json.dumps({"cutover_ready": False, "database_gate_passed": False, "error": str(error)})
        )
    except (ValidationError, OSError, ValueError, KeyError, TypeError):
        print(
            '{"cutover_ready":false,"database_gate_passed":false,"error":"invalid_input_or_environment"}'
        )
    except Exception:
        print(
            '{"cutover_ready":false,"database_gate_passed":false,"error":"database_observation_unavailable"}'
        )
    finally:
        if engine is not None:
            engine.dispose()
    return 65


if __name__ == "__main__":
    sys.exit(main())
