"""One caller-owned schema19→20 transaction; no credential acquisition or replay."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import browser_schema19_step as previous
from scripts import native_resume_schema20_contract as contract
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
        "schema20_active_upload_or_cleanup_refused",
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
    contract.need(1 <= len(tables) <= 256, "schema20_retained_table_bound")
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    for table in tables:
        if table == "alembic_version":
            continue
        contract.need(
            type(table) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table),
            "schema20_retained_table_name_refused",
        )
        digest.update(table.encode() + b"\0")
        query = f"SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS digest FROM \"{identity.namespace}\".\"{table}\" AS t ORDER BY digest LIMIT 250001"
        for row in connection.execute(text(query)):
            count += 1
            contract.need(
                count <= 250000 and time.monotonic() < deadline, "schema20_retained_rows_bound"
            )
            digest.update(bytes.fromhex(row[0]))
        digest.update(b"\0")
    digest.update(str(count).encode())
    return digest.hexdigest()


def upgrade_transaction(connection, identity: MigrationIdentity, *, root: Path) -> dict:
    """Existing migration20 only, inside the original authenticated outer transaction."""
    contract.need(connection.in_transaction(), "schema20_external_transaction_required")
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
        "schema20_migration_already_running",
    )
    contract.need(marker(connection, identity) == BEFORE, "schema20_exact_19_required_never_replay")
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
        "schema20_existing_table_owner_changed",
    )
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    contract.need(
        connection.in_transaction() and marker(connection, identity) == contract.SCHEMA,
        "schema20_target_or_external_transaction_lost",
    )
    contract.verify_shape(connection, identity.namespace_oid, identity.namespace)
    contract.need(
        retained_digest(connection, identity) == retained, "schema20_retained_history_changed"
    )
    drained(connection, identity)
    connection.exec_driver_sql("RESET ROLE")
    verify_identity(connection, identity)
    contract.need(schema_acl(connection, identity) == acl, "schema20_schema_acl_changed")
    return {
        "contract": contract.CONTRACT,
        "schema": contract.SCHEMA,
        "retained_sha256": retained,
        "schema_acl": acl,
        "migration_login_reenabled": False,
        "replay_authorized": False,
    }
