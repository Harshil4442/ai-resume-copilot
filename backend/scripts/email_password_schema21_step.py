"""One caller-owned schema19→21 transaction; no credential acquisition or replay."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import email_password_schema21_contract as contract
from scripts import native_resume_schema20_step as previous
from scripts.browser_schema19_step import MigrationIdentity

BEFORE = contract.BEFORE
LOCK_ID = 721_994_373
verify_identity = previous.verify_identity
schema_acl = previous.schema_acl
effective_owner = previous.effective_owner


def marker(connection, identity: MigrationIdentity) -> str:
    return contract.marker(connection, identity.namespace)


def drained(connection, identity: MigrationIdentity) -> None:
    previous.drained(connection, identity)
    namespace = '"' + identity.namespace + '"'
    contract.need(
        all(
            connection.execute(text(query)).scalar_one() == 0
            for query in (
                f"SELECT count(*) FROM {namespace}.resume_uploads WHERE state IN ('awaiting_upload','queued','inspecting') OR lease_token IS NOT NULL OR lease_until IS NOT NULL",
                f"SELECT count(*) FROM {namespace}.resume_upload_cleanups WHERE lease_token IS NOT NULL OR lease_until IS NOT NULL",
            )
        ),
        "schema21_active_upload_or_cleanup_refused",
    )


def retained_digest(connection, identity: MigrationIdentity) -> str:
    """Retain every row/column, including schema19 identities and detached cleanup."""
    tables = list(
        connection.execute(
            text(
                "SELECT c.relname FROM pg_catalog.pg_class c WHERE c.relnamespace=:namespace "
                "AND c.relkind='r' ORDER BY c.relname LIMIT 257"
            ),
            {"namespace": identity.namespace_oid},
        ).scalars()
    )
    contract.need(1 <= len(tables) <= 256, "schema21_retained_table_bound")
    observed = marker(connection, identity)
    if observed == contract.SCHEMA:
        contract.need(
            connection.execute(text(f'SELECT count(*) FROM "{identity.namespace}".password_reset_tokens')).scalar_one() == 0
            and connection.execute(text(f'SELECT count(*) FROM "{identity.namespace}".users WHERE credential_epoch<>0 OR credential_epoch IS NULL')).scalar_one() == 0,
            "schema21_new_reset_state_changed",
        )
    else:
        contract.need("password_reset_tokens" not in tables, "schema21_before_reset_table_present")
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    for table in tables:
        if table == "alembic_version" or table == "password_reset_tokens" and observed == contract.SCHEMA:
            continue
        contract.need(
            type(table) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table),
            "schema21_retained_table_name_refused",
        )
        digest.update(table.encode() + b"\0")
        projection = "to_jsonb(t)-'credential_epoch'" if table == "users" and observed == contract.SCHEMA else "to_jsonb(t)"
        query = f"SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(({projection})::text,'UTF8')),'hex') AS digest FROM \"{identity.namespace}\".\"{table}\" AS t ORDER BY digest LIMIT 250001"
        for row in connection.execute(text(query)):
            count += 1
            contract.need(
                count <= 250000 and time.monotonic() < deadline, "schema21_retained_rows_bound"
            )
            digest.update(bytes.fromhex(row[0]))
        digest.update(b"\0")
    digest.update(str(count).encode())
    return digest.hexdigest()


def upgrade_transaction(connection, identity: MigrationIdentity, *, root: Path) -> dict:
    """Existing additive migrations20+21 only, inside the original authenticated outer transaction."""
    contract.need(connection.in_transaction(), "schema21_external_transaction_required")
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE")
    connection.exec_driver_sql("SET LOCAL statement_timeout='300000ms'")
    connection.exec_driver_sql("SET LOCAL lock_timeout='5000ms'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10000ms'")
    verify_identity(connection, identity)
    contract.need(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:lock)"), {"lock": LOCK_ID}
        ).scalar_one()
        is True,
        "schema21_migration_already_running",
    )
    contract.need(marker(connection, identity) == BEFORE, "schema21_exact_19_required_never_replay")
    contract.verify_before_shape(connection, identity.namespace_oid, identity.namespace)
    acl = schema_acl(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    effective_owner(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{identity.namespace}"')
    drained(connection, identity)
    retained = retained_digest(connection, identity)
    contract.need(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=:ns "
                "AND relkind='r' AND relowner<>:owner"
            ),
            {"ns": identity.namespace_oid, "owner": identity.migrator_oid},
        ).scalar_one()
        == 0,
        "schema21_existing_table_owner_changed",
    )
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(f'GRANT CREATE ON SCHEMA "{identity.namespace}" TO "{identity.migrator}"')
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    effective_owner(connection, identity)
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    contract.need(
        connection.in_transaction() and marker(connection, identity) == contract.SCHEMA,
        "schema21_target_or_external_transaction_lost",
    )
    connection.exec_driver_sql(f'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "{identity.namespace}".password_reset_tokens TO "{identity.runtime}"')
    connection.exec_driver_sql(f'GRANT SELECT ON TABLE "{identity.namespace}".password_reset_tokens TO "{identity.observer}"')
    contract.verify_shape(connection, identity.namespace_oid, identity.namespace)
    contract.verify_reset_permissions(connection, identity.namespace, identity.runtime, identity.migrator, identity.observer)
    contract.need(
        retained_digest(connection, identity) == retained, "schema21_retained_history_changed"
    )
    drained(connection, identity)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "{identity.namespace}" FROM "{identity.migrator}"')
    verify_identity(connection, identity)
    contract.need(schema_acl(connection, identity) == acl, "schema21_schema_acl_changed")
    return {
        "contract": contract.CONTRACT,
        "schema": contract.SCHEMA,
        "retained_sha256": retained,
        "schema_acl": acl,
        "migration_login_reenabled": False,
        "replay_authorized": False,
    }
