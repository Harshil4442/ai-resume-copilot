"""One explicit schema18→19 transaction under an authenticated bootstrap session.

The production adapter supplies the fixed native target. This SQL layer never
acquires a retired credential, enables LOGIN, changes ownership or retries.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import browser_schema19_contract as contract
from scripts import monetary_cutover_preflight as preflight

BEFORE = "20261015_0018"
LOCK_ID = 721_994_372
NEW_TABLES = frozenset(
    {
        "candidate_google_enrollment_challenges",
        "candidate_google_identities",
        "candidate_identity_history",
    }
)


class Step19Denied(RuntimeError):
    """Only finite, nonsecret operation failures."""


def need(value: Any, code: str) -> None:
    if not value:
        raise Step19Denied(code)


@dataclass(frozen=True)
class MigrationIdentity:
    cluster_sha256: str
    database_oid: int
    database_owner_oid: int
    namespace: str
    namespace_oid: int
    namespace_owner_oid: int
    bootstrap: str
    bootstrap_oid: int
    migrator: str
    migrator_oid: int
    runtime: str
    runtime_oid: int
    observer: str
    observer_oid: int

    def validate(self) -> None:
        need(
            re.fullmatch(r"[a-f0-9]{64}", self.cluster_sha256) is not None,
            "schema19_cluster_pin_invalid",
        )
        need(
            all(
                type(value) is int and 0 < value < 2**32
                for value in (
                    self.database_oid,
                    self.database_owner_oid,
                    self.namespace_oid,
                    self.namespace_owner_oid,
                    self.bootstrap_oid,
                    self.migrator_oid,
                    self.runtime_oid,
                    self.observer_oid,
                )
            ),
            "schema19_oid_pin_invalid",
        )
        need(
            all(
                type(value) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", value)
                for value in (
                    self.namespace,
                    self.bootstrap,
                    self.migrator,
                    self.runtime,
                    self.observer,
                )
            ),
            "schema19_name_pin_invalid",
        )
        need(
            len({self.bootstrap_oid, self.migrator_oid, self.runtime_oid, self.observer_oid}) == 4,
            "schema19_actor_overlap",
        )


def marker(connection, identity: MigrationIdentity) -> str:
    values = list(
        connection.execute(
            text(f'SELECT version_num FROM "{identity.namespace}".alembic_version LIMIT 3')
        ).scalars()
    )
    need(len(values) == 1 and values[0] in {BEFORE, contract.SCHEMA}, "schema19_marker_refused")
    return values[0]


def verify_identity(connection, identity: MigrationIdentity) -> None:
    identity.validate()
    row = connection.execute(
        text(
            "SELECT d.oid,d.datdba,n.oid,n.nspowner,r.oid,current_user,session_user,"
            "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) FROM pg_catalog.pg_database d "
            "JOIN pg_catalog.pg_namespace n ON n.nspname=:namespace JOIN pg_catalog.pg_roles r ON r.rolname=session_user "
            "WHERE d.datname=current_database()"
        ),
        {"namespace": identity.namespace},
    ).one()
    need(
        tuple(row[:7])
        == (
            identity.database_oid,
            identity.database_owner_oid,
            identity.namespace_oid,
            identity.namespace_owner_oid,
            identity.bootstrap_oid,
            identity.bootstrap,
            identity.bootstrap,
        )
        and hashlib.sha256(str(row[7]).encode()).hexdigest() == identity.cluster_sha256,
        "schema19_native_identity_changed",
    )
    roles = connection.execute(
        text(
            "SELECT oid,rolname,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls "
            "FROM pg_catalog.pg_roles WHERE oid IN (:migration,:runtime,:observer) ORDER BY oid"
        ),
        {
            "migration": identity.migrator_oid,
            "runtime": identity.runtime_oid,
            "observer": identity.observer_oid,
        },
    ).all()
    expected = sorted(
        [
            (
                identity.migrator_oid,
                identity.migrator,
                False,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
            (
                identity.runtime_oid,
                identity.runtime,
                True,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
            (
                identity.observer_oid,
                identity.observer,
                True,
                False,
                False,
                False,
                False,
                False,
                False,
            ),
        ]
    )
    need([tuple(row) for row in roles] == expected, "schema19_serving_roles_changed")
    need(
        connection.execute(
            text(
                "SELECT pg_catalog.pg_has_role(:bootstrap,:migrator,'SET') AND "
                "pg_catalog.has_schema_privilege(:bootstrap,:namespace,'CREATE') AND NOT "
                "pg_catalog.has_schema_privilege(:migrator,:namespace,'CREATE')"
            ),
            {
                "bootstrap": identity.bootstrap_oid,
                "migrator": identity.migrator_oid,
                "namespace": identity.namespace_oid,
            },
        ).scalar_one()
        is True,
        "schema19_bootstrap_set_owner_or_prior_acl_refused",
    )
    need(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_auth_members WHERE member=:migrator AND admin_option"
            ),
            {"migrator": identity.migrator_oid},
        ).scalar_one()
        == 0,
        "schema19_migrator_admin_authority_refused",
    )


def schema_acl(connection, identity: MigrationIdentity) -> list[tuple]:
    return [
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT grantor,grantee,privilege_type,is_grantable "
                "FROM pg_catalog.pg_namespace n,LATERAL pg_catalog.aclexplode(coalesce(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) "
                "WHERE n.oid=:namespace ORDER BY grantor,grantee,privilege_type,is_grantable"
            ),
            {"namespace": identity.namespace_oid},
        ).all()
    ]


def effective_owner(connection, identity: MigrationIdentity) -> None:
    row = connection.execute(
        text(
            "SELECT current_user,session_user,(SELECT oid FROM pg_catalog.pg_roles WHERE rolname=current_user)"
        )
    ).one()
    need(
        tuple(row) == (identity.migrator, identity.bootstrap, identity.migrator_oid),
        "schema19_effective_owner_changed",
    )


def retained_digest(connection, identity: MigrationIdentity) -> str:
    """Bounded exact row multiset, excluding only the newly added protocol column."""
    tables = list(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_catalog.pg_class c WHERE c.relnamespace=:namespace "
                "AND c.relkind='r' ORDER BY c.relname LIMIT 257"
            ),
            {"namespace": identity.namespace_oid},
        ).scalars()
    )
    need(1 <= len(tables) <= 256, "schema19_retained_table_bound")
    digest, rows, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    for table in tables:
        if table == "alembic_version" or table in NEW_TABLES:
            continue
        need(
            re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table) is not None,
            "schema19_retained_table_name_refused",
        )
        digest.update(table.encode() + b"\0")
        body = (
            "to_jsonb(t)-'review_protocol_version'"
            if table == "browser_assistance_reviews"
            else "to_jsonb(t)"
        )
        query = f"SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(({body})::text,'UTF8')),'hex') AS digest FROM \"{identity.namespace}\".\"{table}\" AS t ORDER BY digest LIMIT 250001"
        for row in connection.execute(text(query)):
            rows += 1
            need(rows <= 250000 and time.monotonic() < deadline, "schema19_retained_rows_bound")
            digest.update(bytes.fromhex(row[0]))
        digest.update(b"\0")
    digest.update(str(rows).encode())
    return digest.hexdigest()


def drained(connection, identity: MigrationIdentity) -> None:
    ns = '"' + identity.namespace + '"'
    queries = [
        f"SELECT count(*) FROM {ns}.browser_assistance_reviews WHERE state IN ('approved','running')",
        f"SELECT count(*) FROM {ns}.browser_assistance_requests WHERE state NOT IN ('completed','filled','blocked','unknown')",
        "SELECT count(*) FROM pg_catalog.pg_prepared_xacts WHERE database=current_database()",
        f"SELECT count(*) FROM {ns}.model_call_events WHERE status NOT IN ('succeeded','failed','invalid_output')",
        f"SELECT count(*) FROM {ns}.analysis_runs WHERE status NOT IN ('succeeded','failed','cancelled')",
        contract.unresolved_application_sql(identity.namespace),
        f"SELECT count(*) FROM {ns}.employer_application_attempts WHERE state NOT IN ('confirmed','failed')",
        f"SELECT count(*) FROM {ns}.service_credit_reservations WHERE state <> 'settled' OR reserved_amount <> committed_amount + released_amount OR reserved_amount < 0 OR committed_amount < 0 OR released_amount < 0",
        f"SELECT count(*) FROM {ns}.analysis_runs WHERE model_cost_reserved_micros <> 0 OR model_cost_settled_micros < 0 OR model_cost_state NOT IN ('unquoted','active') OR (model_cost_state = 'unquoted' AND (model_cost_quote IS NOT NULL OR model_cost_settled_micros <> 0)) OR (model_cost_state = 'active' AND (model_cost_quote IS NULL OR model_cost_ceiling_micros IS NULL OR model_cost_ceiling_micros <= 0 OR model_cost_settled_micros > model_cost_ceiling_micros))",
        f"SELECT count(*) FROM {ns}.model_call_events WHERE cost_state NOT IN ('unavailable','settled') OR (cost_state = 'settled' AND (liability_id IS NULL OR settled_at IS NULL OR settled_cost_micros IS NULL OR settled_cost_micros < 0)) OR (cost_state = 'unavailable' AND (liability_id IS NOT NULL OR pricing_quote IS NOT NULL OR reserved_cost_micros IS NOT NULL OR settled_cost_micros IS NOT NULL OR settled_at IS NOT NULL OR token_estimate_provenance IS NOT NULL OR usage_provenance IS NOT NULL OR output_token_limit IS NOT NULL))",
    ]
    need(
        all(connection.execute(text(query)).scalar_one() == 0 for query in queries),
        "schema19_live_or_unresolved_work_refused",
    )
    need(
        preflight._liabilities(connection, identity.namespace).get("unresolved_count") == 0,
        "schema19_unresolved_model_liabilities",
    )


def upgrade_transaction(connection, identity: MigrationIdentity, *, root: Path) -> dict:
    """Caller owns the genuine session and explicit outer transaction/commit."""
    need(connection.in_transaction(), "schema19_external_transaction_required")
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE")
    connection.exec_driver_sql("SET LOCAL statement_timeout='300000ms'")
    connection.exec_driver_sql("SET LOCAL lock_timeout='5000ms'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10000ms'")
    verify_identity(connection, identity)
    need(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": LOCK_ID}
        ).scalar_one()
        is True,
        "schema19_migration_already_running",
    )
    need(marker(connection, identity) == BEFORE, "schema19_exact_18_required_never_replay")
    need(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=:namespace AND relname=ANY(CAST(:names AS text[]))"
            ),
            {"namespace": identity.namespace_oid, "names": sorted(NEW_TABLES)},
        ).scalar_one()
        == 0,
        "schema19_partial_google_schema_refused",
    )
    acl = schema_acl(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    effective_owner(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{identity.namespace}"')
    drained(connection, identity)
    retained = retained_digest(connection, identity)
    need(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=:ns AND relkind='r' AND relowner<>:owner"
            ),
            {"ns": identity.namespace_oid, "owner": identity.migrator_oid},
        ).scalar_one()
        == 0,
        "schema19_existing_table_owner_changed",
    )
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(
        f'GRANT CREATE ON SCHEMA "{identity.namespace}" TO "{identity.migrator}"'
    )
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    effective_owner(connection, identity)
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    need(
        connection.in_transaction() and marker(connection, identity) == contract.SCHEMA,
        "schema19_target_or_external_transaction_lost",
    )
    for table in sorted(NEW_TABLES):
        permissions = (
            "SELECT,INSERT"
            if table == "candidate_identity_history"
            else "SELECT,INSERT,UPDATE,DELETE"
        )
        connection.exec_driver_sql(
            f'GRANT {permissions} ON TABLE "{identity.namespace}"."{table}" TO "{identity.runtime}"'
        )
        connection.exec_driver_sql(
            f'GRANT SELECT ON TABLE "{identity.namespace}"."{table}" TO "{identity.observer}"'
        )
    contract.verify_shape(connection, identity.namespace_oid, identity.namespace)
    need(
        all(
            connection.execute(
                text(f'SELECT count(*) FROM "{identity.namespace}"."{table}"')
            ).scalar_one()
            == 0
            for table in NEW_TABLES
        ),
        "schema19_unreviewed_google_rows_created",
    )
    need(retained_digest(connection, identity) == retained, "schema19_retained_history_changed")
    drained(connection, identity)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(
        f'REVOKE CREATE ON SCHEMA "{identity.namespace}" FROM "{identity.migrator}"'
    )
    verify_identity(connection, identity)
    need(schema_acl(connection, identity) == acl, "schema19_prior_schema_acl_not_restored")
    return {
        "contract": contract.CONTRACT,
        "schema": contract.SCHEMA,
        "retained_sha256": retained,
        "schema_acl": acl,
        "migration_login_reenabled": False,
        "replay_authorized": False,
    }
