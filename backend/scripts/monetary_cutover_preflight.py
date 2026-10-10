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

from psycopg import Connection as PsycopgConnection
from psycopg import ConnectionInfo
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import NullPool

try:
    from scripts import neon_direct_identity as neon_identity
except ModuleNotFoundError:  # Direct standalone script invocation.
    import neon_direct_identity as neon_identity

MAX_INPUT_BYTES = 131072
MAX_ROWS = 256
SCHEMA_ORDER = ("20261008_0009", "20261009_0010", "20261009_0011", "20261009_0012", "20261009_0013")
SCHEMAS = set(SCHEMA_ORDER)
FINANCIAL_SCHEMAS = SCHEMAS - {"20261008_0009"}
CANDIDATE_SCHEMAS = SCHEMAS - {"20261008_0009", "20261009_0010"}
EXTENSION_COLUMNS = {
    "20261009_0012": {("employer_postings", "preference_metadata"): ("json", False)},
    "20261009_0013": {
        ("payment_orders", "cost_policy_snapshot"): ("json", False),
        ("service_credit_reservations", "cost_policy_snapshot"): ("json", False),
    },
}
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
  (SELECT count(*) FROM pg_catalog.pg_class c
   WHERE c.relnamespace = :namespace AND c.relkind IN {RELATIONS}
    AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles target
      WHERE (target.oid = r.oid OR pg_catalog.pg_has_role(r.oid,target.oid,'SET'))
       AND (has_table_privilege(target.oid,c.oid,:privileges)
        OR has_any_column_privilege(target.oid,c.oid,:column_privileges)))) AS writes,
  EXISTS (SELECT 1 FROM pg_catalog.pg_roles target
   WHERE (target.oid = r.oid OR pg_catalog.pg_has_role(r.oid,target.oid,'SET'))
    AND has_schema_privilege(target.oid,:namespace,'CREATE')) AS schema_create,
  EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members membership
   WHERE membership.admin_option
    AND (membership.member = r.oid OR pg_catalog.pg_has_role(r.oid,membership.member,'MEMBER')))
    AS membership_administration
 FROM pg_catalog.pg_roles r
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
    expected_database_schema: Literal["20261008_0009", "20261009_0010", "20261009_0011", "20261009_0012", "20261009_0013"]
    candidate_schema: Literal["20261009_0010", "20261009_0011", "20261009_0012", "20261009_0013"]
    expected_system_identifier_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_database_oid: int = Field(gt=0)
    database_namespace: str = Field(pattern=r"^[a-z][a-z0-9_]{0,62}$")
    # Explicit aliases resolve role names in process, never into the report.
    role_env: dict[Literal["retired", "replacement_runtime", "replacement_migration"], str]
    # Expected identity only. Native issuer/legacy-DSN binding remains separate.
    retired_role_mode: Literal["disabled", "deleted"] = "disabled"
    retired_role_oid: int | None = Field(default=None, gt=0, le=4294967295)
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

    @model_validator(mode="after")
    def retired_role_identity(self) -> Inventory:
        if self.retired_role_mode == "deleted" and self.retired_role_oid is None:
            raise ValueError("Deleted role mode requires its pinned old OID")
        return self


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
    expense_policy_version: str | None = Field(default=None, min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:/-]+$")
    expense_funding: Literal["prepaid", "promotion", "legacy_premium"] | None = None
    expense_pool: Literal["prepaid", "promotion", "legacy_premium", "failed_work"] | None = None

    @model_validator(mode="after")
    def complete_expense_fields(self) -> LiabilityQuote:
        fields = (self.expense_policy_version, self.expense_funding, self.expense_pool)
        names = {"expense_policy_version", "expense_funding", "expense_pool"}
        present = names.intersection(self.model_fields_set)
        if present and (present != names or any(value is None for value in fields)):
            raise ValueError("Expense provenance must be complete or absent for a historical quote")
        if self.expense_pool is not None and self.expense_pool not in {self.expense_funding, "failed_work"}:
            raise ValueError("Expense pool must retain its funding class or failed-work classification")
        return self


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
    total = _count(connection, "SELECT count(*) FROM pg_catalog.pg_proc WHERE prosecdef")
    if total > MAX_ROWS:
        raise Denied("database_function_inventory_bound")
    routines = (
        connection.execute(
            text(
                "SELECT p.oid, p.proowner, p.prokind, n.oid AS namespace_oid, "
                "l.lanname, l.lanpltrusted FROM pg_catalog.pg_proc p "
                "LEFT JOIN pg_catalog.pg_namespace n ON n.oid = p.pronamespace "
                "LEFT JOIN pg_catalog.pg_language l ON l.oid = p.prolang "
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
                "AS known_writer_owner_count FROM pg_catalog.pg_roles r CROSS JOIN pg_catalog.pg_proc p "
                "WHERE p.prosecdef AND EXISTS (SELECT 1 FROM pg_catalog.pg_roles target "
                "WHERE (target.oid = r.oid OR pg_catalog.pg_has_role(r.oid,target.oid,'SET')) "
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


def _extension_shape(connection: Any, namespace_oid: int, version: str) -> None:
    """Inspect only column metadata; never load employer, payment or candidate values."""
    expected: dict[tuple[str, str], tuple[str, bool]] = {}
    for introduced, columns in EXTENSION_COLUMNS.items():
        if SCHEMA_ORDER.index(version) >= SCHEMA_ORDER.index(introduced):
            expected.update(columns)
    all_keys = {key for columns in EXTENSION_COLUMNS.values() for key in columns}
    rows = connection.execute(
        text(
            "SELECT c.relname, c.relkind, a.attname, "
            "pg_catalog.format_type(a.atttypid,a.atttypmod) AS column_type, a.attnotnull "
            "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_attribute a ON a.attrelid=c.oid "
            "WHERE c.relnamespace=:namespace AND c.relname = ANY(CAST(:tables AS text[])) "
            "AND a.attname = ANY(CAST(:columns AS text[])) "
            "AND a.attnum > 0 AND NOT a.attisdropped ORDER BY c.oid,a.attnum LIMIT 257"
        ),
        {
            "namespace": namespace_oid,
            "tables": sorted({table for table, _ in all_keys}),
            "columns": sorted({column for _, column in all_keys}),
        },
    ).mappings().all()
    if len(rows) > MAX_ROWS:
        raise Denied("database_extension_inventory_bound")
    found: dict[tuple[str, str], tuple[str, bool]] = {}
    for row in rows:
        if row["relkind"] != "r" or type(row["attnotnull"]) is not bool:
            raise Denied("database_extension_shape_mismatch")
        found[(str(row["relname"]), str(row["attname"]))] = (str(row["column_type"]), row["attnotnull"])
    if found != expected:
        raise Denied("database_extension_shape_mismatch")


# PostgreSQL17 native contracts: REL_17_11 pg_proc.dat and system_views.sql.
# Session identifiers precede HAS_PGSTAT_PERMISSIONS in pgstatfuncs.c.
PG17_ACTIVITY_NAMES = (
    "pid", "datid", "pid", "usesysid", "application_name", "state", "query",
    "wait_event_type", "wait_event", "xact_start", "query_start", "backend_start",
    "state_change", "client_addr", "client_hostname", "client_port", "backend_xid",
    "backend_xmin", "backend_type", "ssl", "sslversion", "sslcipher", "sslbits",
    "ssl_client_dn", "ssl_client_serial", "ssl_issuer_dn", "gss_auth", "gss_princ",
    "gss_enc", "gss_delegation", "leader_pid", "query_id",
)
PG17_ACTIVITY_TYPES = (
    23, 26, 23, 26, 25, 25, 25, 25, 25, 1184, 1184, 1184, 1184, 869, 25,
    23, 28, 28, 25, 16, 25, 25, 23, 25, 1700, 25, 16, 25, 16, 16, 23, 20,
)
PG17_ACTIVITY_VIEW_COLUMNS = (
    ("datid", 26), ("datname", 19), ("pid", 23), ("leader_pid", 23),
    ("usesysid", 26), ("usename", 19), ("application_name", 25),
    ("client_addr", 869), ("client_hostname", 25), ("client_port", 23),
    ("backend_start", 1184), ("xact_start", 1184), ("query_start", 1184),
    ("state_change", 1184), ("wait_event_type", 25), ("wait_event", 25),
    ("state", 25), ("backend_xid", 28), ("backend_xmin", 28),
    ("query_id", 20), ("query", 25), ("backend_type", 25),
)
PG17_ACTIVITY_VIEW = f"""
 SELECT s.datid, d.datname, s.pid, s.leader_pid, s.usesysid,
 u.rolname AS usename, s.application_name, s.client_addr, s.client_hostname,
 s.client_port, s.backend_start, s.xact_start, s.query_start, s.state_change,
 s.wait_event_type, s.wait_event, s.state, s.backend_xid, s.backend_xmin,
 s.query_id, s.query, s.backend_type
 FROM pg_stat_get_activity(NULL::integer) s({", ".join(PG17_ACTIVITY_NAMES[1:])})
 LEFT JOIN pg_database d ON s.datid = d.oid
 LEFT JOIN pg_authid u ON s.usesysid = u.oid;
"""


def _activity_view_tokens(definition: str) -> str:
    # PG deparser only changes whitespace/grouping parentheses here. Quoted
    # identifiers, extra predicates, expressions and dependencies remain distinct.
    return re.sub(r"[\s()]+", "", definition).lower()


def _native_session_ids(
    connection: Connection, database_oid: int,
    *, neon_binding: neon_identity.NeonDirectProxyBinding | None = None,
) -> tuple[list[tuple[int, int | None, int | None]], dict[str, Any]]:
    if neon_binding is not None:
        neon_identity.verify_transport(connection, neon_binding)
    # PQuser identifies the actual connection startup login. SQL session_user can
    # be changed by a privileged login; engine URL usernames can be overridden.
    # Read only these documented Connection.info fields, never DSN/password.
    try:
        driver = connection.connection.driver_connection
        if not isinstance(driver, PsycopgConnection):
            raise TypeError
        info = driver.info
        if not isinstance(info, ConnectionInfo):
            raise TypeError
        authenticated_user, protocol_pid, protocol_version = (
            info.user, info.backend_pid, info.server_version,
        )
    except Exception:
        raise Denied("database_observer_protocol_identity_unavailable") from None
    if (type(authenticated_user) is not str or not authenticated_user
        or len(authenticated_user) > 63 or "\x00" in authenticated_user
        or (neon_binding is None and (type(protocol_pid) is not int or protocol_pid <= 0))
        or (neon_binding is not None and not neon_identity.cancellation_key_shape(protocol_pid))
        or type(protocol_version) is not int or protocol_version <= 0):
        raise Denied("database_observer_protocol_identity_unavailable")
    version = str(connection.exec_driver_sql("SHOW server_version_num").scalar_one())
    if not re.fullmatch(r"17\d{4}", version):
        raise Denied("database_public_session_ids_version_unsupported")
    if protocol_version != int(version):
        raise Denied("database_observer_protocol_identity_mismatch")
    sql_user, sql_session_user = connection.execute(text("SELECT current_user,session_user")).one()
    if sql_user != sql_session_user:
        raise Denied("database_observer_authenticated_role_mismatch")
    if sql_user != authenticated_user:
        raise Denied("database_observer_protocol_identity_mismatch")
    functions = connection.execute(text(
        "SELECT p.oid,p.proname,p.pronamespace,p.proowner,p.prolang,p.prokind,"
        "p.prosecdef,p.proretset,p.proisstrict,p.provolatile,p.proparallel,"
        "p.prorettype,p.proargtypes::text,p.proallargtypes,p.proargmodes,"
        "p.proargnames,p.prosrc,p.probin,p.proconfig "
        "FROM pg_catalog.pg_proc p WHERE p.pronamespace=11 AND "
        "p.proname IN ('pg_stat_get_activity','pg_backend_pid') ORDER BY p.oid LIMIT 3"
    )).all()
    expected = [
        (2022, "pg_stat_get_activity", 11, 10, 12, "f", False, True, False, "s", "r",
         2249, "23", list(PG17_ACTIVITY_TYPES), ["i"] + ["o"] * 31,
         list(PG17_ACTIVITY_NAMES), "pg_stat_get_activity", None, None),
        (2026, "pg_backend_pid", 11, 10, 12, "f", False, False, True, "s", "r",
         23, "", None, None, None, "pg_backend_pid", None, None),
    ]
    if [tuple(row) for row in functions] != expected:
        raise Denied("database_native_session_function_identity_mismatch")
    view = connection.execute(text(
        "SELECT c.oid,c.relnamespace,c.relowner,c.relkind,c.relpersistence,"
        "c.relrowsecurity,c.relforcerowsecurity,c.reloptions,c.relhasrules,"
        "pg_catalog.pg_get_viewdef(c.oid,false) "
        "FROM pg_catalog.pg_class c WHERE c.relnamespace=11 "
        "AND c.relname='pg_stat_activity' LIMIT 2"
    )).all()
    if (len(view) != 1 or not 0 < int(view[0][0]) < 16384
        or tuple(view[0][1:9]) != (11, 10, "v", "p", False, False, None, True)
        or not isinstance(view[0][9], str) or len(view[0][9]) > 4096
        or _activity_view_tokens(view[0][9]) != _activity_view_tokens(PG17_ACTIVITY_VIEW)):
        raise Denied("database_native_session_view_identity_mismatch")
    columns = connection.execute(text(
        "SELECT a.attname,a.atttypid FROM pg_catalog.pg_attribute a "
        "WHERE a.attrelid=:view AND a.attnum>0 AND NOT a.attisdropped "
        "ORDER BY a.attnum LIMIT 23"
    ), {"view": int(view[0][0])}).all()
    if tuple(tuple(row) for row in columns) != PG17_ACTIVITY_VIEW_COLUMNS:
        raise Denied("database_native_session_view_shape_mismatch")
    own = connection.execute(text(
        "SELECT pg_catalog.pg_backend_pid(),r.oid "
        "FROM pg_catalog.pg_roles r WHERE r.rolname=:authenticated_user"
    ), {"authenticated_user": authenticated_user}).one()
    if neon_binding is None and own[0] != protocol_pid:
        raise Denied("database_observer_protocol_identity_mismatch")
    if neon_binding is not None:
        neon_identity.verify_native_target(connection, neon_binding, database_oid)
    rows = connection.execute(text(
        "SELECT pid,usesysid,datid FROM pg_catalog.pg_stat_activity ORDER BY pid LIMIT 257"
    )).all()
    if len(rows) > MAX_ROWS:
        raise Denied("database_session_or_writer_inventory_bound")
    sessions: list[tuple[int, int | None, int | None]] = []
    seen: set[int] = set()
    for pid, role_oid, db_oid in rows:
        if (type(pid) is not int or pid <= 0 or pid in seen
            or any(value is not None and (type(value) is not int or not 0 < value <= 4294967295)
                   for value in (role_oid, db_oid))):
            raise Denied("database_native_session_identifier_shape_mismatch")
        seen.add(pid)
        sessions.append((pid, role_oid, db_oid))
    if [row for row in sessions if row[0] == own[0]] != [(own[0], own[1], database_oid)]:
        raise Denied("database_observer_authenticated_session_mismatch")
    return [row for row in sessions if row[0] != own[0]], {
        "contract": "pg17_native_public_pid_role_database_ids",
        "server_version_num": int(version), "native_function_oid": 2022,
        "native_view_oid": int(view[0][0]), "authenticated_login_bound": True,
        "protocol_identity_bound": True,
        "requires_all_session_activity_details": False,
        **({"transport_contract": "neon_direct_proxy", "protocol_pid_is_cancellation_key": True,
            "native_own_session_bound": True} if neon_binding is not None else {}),
    }



def observe(
    engine: Engine,
    inventory: Inventory,
    roles: Mapping[str, str],
    operator_roles: tuple[str, ...] = (),
    *, neon_binding: neon_identity.NeonDirectProxyBinding | None = None,
) -> dict[str, Any]:
    if neon_binding is not None and (
        inventory.database_namespace != neon_identity.NAMESPACE
        or inventory.expected_database_oid != neon_identity.DATABASE_OID
        or inventory.expected_system_identifier_sha256 != neon_identity.CLUSTER
    ):
        raise Denied("neon_direct_inventory_target_mismatch")
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
        if neon_binding is not None:
            neon_identity.verify_transport(connection, neon_binding)
        with connection.begin():
            connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            connection.exec_driver_sql("SET LOCAL search_path = pg_catalog")
            connection.exec_driver_sql("SET LOCAL statement_timeout = '1000ms'")
            connection.exec_driver_sql("SET LOCAL lock_timeout = '1000ms'")
            connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout = '10000ms'")
            identifier = connection.execute(
                text("SELECT system_identifier::text FROM pg_catalog.pg_control_system()")
            ).scalar_one()
            identifier_hash = hashlib.sha256(str(identifier).encode()).hexdigest()
            database_oid = _count(
                connection, "SELECT oid FROM pg_catalog.pg_database WHERE datname = current_database()"
            )
            if inventory.retired_role_mode == "deleted" and (
                identifier_hash != inventory.expected_system_identifier_sha256
                or database_oid != inventory.expected_database_oid
            ):
                raise Denied("deleted_role_database_identity_mismatch")
            namespace = inventory.database_namespace
            # Bind both namespace and cluster/database identity; no search_path trust.
            schema_oid = connection.execute(
                text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname = :namespace"),
                {"namespace": namespace},
            ).scalar_one_or_none()
            if schema_oid is None:
                raise Denied("database_namespace_missing")
            table_rows = connection.execute(
                text(
                    f"SELECT relname, oid FROM pg_catalog.pg_class WHERE relnamespace = :namespace AND relkind IN {RELATIONS} ORDER BY oid LIMIT 257"
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
            if neon_binding is not None and version != "20261008_0009":
                raise Denied("neon_direct_inventory_schema_mismatch")
            # A candidate cannot silently adopt a newer database revision.
            if SCHEMA_ORDER.index(version) > SCHEMA_ORDER.index(inventory.candidate_schema):
                raise Denied("database_schema_unsupported")
            if version in FINANCIAL_SCHEMAS and "model_cost_liabilities" not in table_oids:
                raise Denied("database_liability_table_missing")
            if version == "20261008_0009" and "model_cost_liabilities" in table_oids:
                raise Denied("database_schema_shape_mismatch")
            if version in CANDIDATE_SCHEMAS:
                if not CANDIDATE_TABLES.issubset(table_oids):
                    raise Denied("database_candidate_projection_tables_missing")
                _candidate_projection_shape(connection, int(schema_oid))
            elif CANDIDATE_TABLES.intersection(table_oids):
                raise Denied("database_schema_shape_mismatch")
            _extension_shape(connection, int(schema_oid), version)
            sessions, session_contract = _native_session_ids(connection, database_oid, neon_binding=neon_binding)
            role_rows = connection.execute(
                text(
                    "SELECT oid, rolname, rolcanlogin, rolsuper, rolcreaterole, rolcreatedb, rolreplication, rolbypassrls FROM pg_catalog.pg_roles ORDER BY oid LIMIT 257"
                )
            ).all()
            if len(role_rows) > MAX_ROWS:
                raise Denied("database_role_inventory_bound")
            role_by_name = {str(row[1]): row for row in role_rows}
            required_names = {*roles.values(), *operator_roles}
            if inventory.retired_role_mode == "deleted":
                replacement_names = {
                    roles["replacement_runtime"], roles["replacement_migration"], *operator_roles
                }
                if roles["retired"] in replacement_names or any(
                    name in role_by_name and int(role_by_name[name][0]) == inventory.retired_role_oid
                    for name in replacement_names
                ):
                    raise Denied("retired_role_identity_class_overlap")
                if roles["retired"] in role_by_name or any(
                    int(row[0]) == inventory.retired_role_oid for row in role_rows
                ):
                    raise Denied("deleted_role_name_or_oid_present")
                required_names.remove(roles["retired"])
            elif inventory.retired_role_oid is not None and (
                roles["retired"] not in role_by_name
                or int(role_by_name[roles["retired"]][0]) != inventory.retired_role_oid
            ):
                raise Denied("retired_role_oid_mismatch")
            if not required_names.issubset(role_by_name):
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
                if alias == "retired" and inventory.retired_role_mode == "deleted":
                    observed_roles[alias] = {
                        "state": "absent",
                        "bound_oid": inventory.retired_role_oid,
                        "active_session_count": sum(
                            1 for _, role_oid, _ in sessions
                            if role_oid == inventory.retired_role_oid
                        ),
                    }
                    continue
                row = role_by_name[name]
                capability = capabilities[int(row[0])]
                role_sessions = sum(1 for _, role_oid, _ in sessions if role_oid == int(row[0]))
                observed_roles[alias] = {
                    "login_enabled": bool(row[2]),
                    "elevated": any(bool(item) for item in row[3:]),
                    "elevated_role_membership": bool(
                        connection.execute(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE (rolsuper OR rolcreaterole OR rolcreatedb OR rolreplication OR rolbypassrls) AND pg_catalog.pg_has_role(:role,oid,'MEMBER'))"
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
                connection, "SELECT oid FROM pg_catalog.pg_roles WHERE rolname = current_user"
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
            if len(writers) > MAX_ROWS:
                raise Denied("database_session_or_writer_inventory_bound")
            unrelated_sessions = sum(
                1
                for _, role_oid, _ in sessions
                if role_oid is not None
                and role_oid not in {int(role_by_name[name][0]) for name in known}
            )
            prepared_transactions = _count(
                connection,
                "SELECT count(*) FROM pg_catalog.pg_prepared_xacts WHERE database = current_database()",
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
                "session_inventory": session_contract,
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
    if inventory.retired_role_mode == "deleted":
        if (
            set(retired) != {"state", "bound_oid", "active_session_count"}
            or retired.get("state") != "absent"
            or type(retired.get("bound_oid")) is not int
            or retired.get("bound_oid") != inventory.retired_role_oid
            or type(retired.get("active_session_count")) is not int
            or retired.get("active_session_count", -1) < 0
        ):
            reasons.append("retired_database_role_not_fenced")
        elif retired["active_session_count"]:
            reasons.append("retired_database_sessions_present")
    else:
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
