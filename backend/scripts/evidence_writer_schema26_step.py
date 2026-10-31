"""PROPOSAL: one caller-owned25→26 transaction, not a migration entrypoint.

The existing controlled native job must supply the authenticated fixed bootstrap
connection, current physical/source/CI/image/closed-admission proofs and its new
one-use intent. This SQL layer obtains no credential, sends no provider request,
commits nothing and never retries. Returned values are pre-commit observations.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import browser_schema19_step as identity_step
from scripts import evidence_writer_schema26_contract as contract
from scripts import schema25_app_release_contract as financial
from scripts import unified_credit_schema25_contract as previous
from scripts import unified_credit_schema25_step as previous_step

# Serialize with the original additive-migration transaction; no new lock service.
LOCK_ID = previous_step.LOCK_ID
TABLES = ("evidence_items", "password_reset_tokens")
MAX_ROWS = 250_000


class Migration26Denied(RuntimeError):
    pass


def need(value, reason):
    if not value:
        raise Migration26Denied("schema26_migration_" + reason)


def affected_acl(connection, identity):
    rows = [
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT relname,relowner,coalesce(relacl::text,'') FROM pg_catalog.pg_class "
                "WHERE relnamespace=:namespace AND relname=ANY(CAST(:tables AS text[])) "
                "AND relkind='r' ORDER BY relname LIMIT 3"
            ),
            {"namespace": identity.namespace_oid, "tables": list(TABLES)},
        )
    ]
    need(
        len(rows) == 2
        and tuple(row[0] for row in rows) == TABLES
        and all(row[1] == identity.migrator_oid for row in rows),
        "affected_owner_or_acl_unavailable",
    )
    return rows


def affected_digest(connection, identity, *, after: bool):
    """Only the two exclusively locked DDL tables; only row hashes leave SQL.

    Other pilot tables may legitimately change. For existing reset rows the only
    normalized field is the new purpose, separately required to be password_reset.
    This comparison is valid inside the locked transaction, not after commit.
    """
    previous.namespace_name(identity.namespace)
    need(identity.namespace == "public", "public_target_required")
    if after:
        need(
            connection.execute(
                text('SELECT count(*) FROM "public".password_reset_tokens WHERE purpose<>:purpose'),
                {"purpose": "password_reset"},
            ).scalar_one()
            == 0,
            "existing_reset_purpose_changed",
        )
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    for table in TABLES:
        projection = (
            "to_jsonb(t) - 'purpose'"
            if after and table == "password_reset_tokens"
            else "to_jsonb(t)"
        )
        query = f'''SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(({projection})::text,'UTF8')),'hex')
            FROM "public"."{table}" t ORDER BY 1 LIMIT 250001'''
        digest.update(table.encode() + b"\0")
        for row in connection.execute(text(query)):
            count += 1
            need(count <= MAX_ROWS and time.monotonic() < deadline, "affected_rows_bound")
            need(
                type(row[0]) is str and re.fullmatch(r"[a-f0-9]{64}", row[0]) is not None,
                "affected_hash_unavailable",
            )
            digest.update(bytes.fromhex(row[0]))
        digest.update(b"\0")
    digest.update(str(count).encode())
    return digest.hexdigest()


def upgrade_transaction(connection, identity, *, root: Path, flags: dict):
    """Use standard Alembic on this exact supplied transaction; no commit/replay."""
    need(
        connection.dialect.name == "postgresql" and connection.in_transaction(),
        "native_transaction_required",
    )
    need(
        type(identity) is identity_step.MigrationIdentity and identity.namespace == "public",
        "fixed_identity_required",
    )
    previous.require_disabled(flags)
    source = contract.verify_source(root)
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
    connection.exec_driver_sql("SET LOCAL lock_timeout='4s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10s'")
    identity_step.verify_identity(connection, identity)
    need(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_ID}
        ).scalar_one()
        is True,
        "already_running",
    )
    previous.verify_shape(connection, identity.namespace_oid, identity.namespace)
    acl = identity_step.schema_acl(connection, identity)
    table_acl = affected_acl(connection, identity)

    # Same temporary CREATE grant/revoke and effective-owner proof as schema25.
    # Never enable LOGIN, reuse a pool, grant runtime DDL or change ownership.
    connection.exec_driver_sql('GRANT CREATE ON SCHEMA "public" TO "' + identity.migrator + '"')
    connection.exec_driver_sql('SET LOCAL ROLE "' + identity.migrator + '"')
    identity_step.effective_owner(connection, identity)
    connection.exec_driver_sql('SET LOCAL search_path TO "public"')
    for table in TABLES:
        connection.exec_driver_sql(f'LOCK TABLE "public"."{table}" IN ACCESS EXCLUSIVE MODE')
    # READ COMMITTED takes a fresh statement snapshot after both AX locks.
    # Do not freeze a transaction snapshot in the earlier identity/catalog reads.
    financial.observe(connection, namespace_oid=identity.namespace_oid)
    liabilities = financial.liability_pins(connection)
    before = affected_digest(connection, identity, after=False)
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    need(connection.in_transaction(), "external_transaction_lost")
    need(affected_digest(connection, identity, after=True) == before, "affected_data_changed")
    contract.observe(connection, namespace_oid=identity.namespace_oid)
    financial.verify_liability_pins(connection, liabilities)
    previous_step.verify_new_permissions(connection, identity)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql('REVOKE CREATE ON SCHEMA "public" FROM "' + identity.migrator + '"')
    identity_step.verify_identity(connection, identity)
    contract.verify_writer_permissions(
        connection, runtime=identity.runtime, migrator=identity.migrator
    )
    need(identity_step.schema_acl(connection, identity) == acl, "schema_acl_changed")
    need(affected_acl(connection, identity) == table_acl, "affected_acl_changed")
    need(contract.verify_source(root) == source, "source_changed")
    return {
        "schema": contract.SCHEMA,
        "affected_sha256": before,
        "source_sha256": source,
        "schema_acl": acl,
        "transaction_ready_to_commit": True,
        "committed": False,
        "replay_authorized": False,
        "migration_login_reenabled": False,
        "new_admission_enabled": False,
    }
