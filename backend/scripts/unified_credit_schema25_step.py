"""Prepared additive21→25 transaction; no credentials, provider IO, CLI or replay.

Native release orchestration must still supply the current authenticated identity,
source/image provenance, freshly observed paused flags and one-shot journal.
"""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import email_password_schema21_step as previous
from scripts import unified_credit_schema25_contract as contract
from scripts.browser_schema19_step import MigrationIdentity

LOCK_ID = 721_994_375


def retained_digest(connection, namespace_oid, namespace):
    """Hash all existing21 values in PostgreSQL, including active reset metadata.

    Only row digests cross into Python; candidate data and credentials never do.
    New tables must be empty during migration; funded work cannot be erased.
    """
    contract.namespace_name(namespace)
    tables = (
        connection.execute(
            text(
                "SELECT relname FROM pg_catalog.pg_class WHERE relnamespace=:ns AND relkind='r' ORDER BY relname LIMIT 257"
            ),
            {"ns": namespace_oid},
        )
        .scalars()
        .all()
    )
    contract.need(1 <= len(tables) <= 256, "schema25_retained_table_bound")
    digest, count, deadline = hashlib.sha256(), 0, time.monotonic() + 30
    for table in tables:
        contract.need(
            re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table), "schema25_retained_table_name_refused"
        )
        if table == "alembic_version":
            continue
        if table in contract.TABLES:
            contract.need(
                connection.execute(text(f'SELECT 1 FROM "{namespace}"."{table}" LIMIT 1')).first()
                is None,
                "schema25_new_financial_activity_refused",
            )
            continue
        digest.update(table.encode() + b"\0")
        query = f'''SELECT encode(pg_catalog.sha256(pg_catalog.convert_to(to_jsonb(t)::text,'UTF8')),'hex') AS digest
            FROM "{namespace}"."{table}" t ORDER BY digest LIMIT 250001'''
        for row in connection.execute(text(query)):
            count += 1
            contract.need(
                count <= 250000 and time.monotonic() < deadline, "schema25_retained_rows_bound"
            )
            digest.update(bytes.fromhex(row[0]))
        digest.update(b"\0")
    digest.update(str(count).encode())
    return digest.hexdigest()


def _additive_ddl(connection, *, namespace_oid, namespace, root: Path, flags: dict):
    """Internal DDL core, tested locally; production entry is upgrade_transaction."""
    contract.need(connection.in_transaction(), "schema25_external_transaction_required")
    contract.require_disabled(flags)
    source = contract.verify_source(root)
    contract.verify_before_shape(connection, namespace_oid, namespace)
    retained = retained_digest(connection, namespace_oid, namespace)
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    contract.need(connection.in_transaction(), "schema25_external_transaction_lost")
    contract.verify_shape(connection, namespace_oid, namespace)
    contract.need(
        retained_digest(connection, namespace_oid, namespace) == retained,
        "schema25_retained_history_changed",
    )
    contract.need(contract.verify_source(root) == source, "schema25_migration_sources_changed")
    return {
        "contract": contract.CONTRACT,
        "schema": contract.SCHEMA,
        "retained_sha256": retained,
        "source_manifest_sha256": source,
        "replay_authorized": False,
        "new_admission_enabled": False,
    }


def verify_new_permissions(connection, identity: MigrationIdentity):
    rows = connection.execute(
        text(
            "SELECT c.relname,owner.rolname,x.grantor=c.relowner AS owner_grant,coalesce(grantee.rolname,'PUBLIC') AS grantee,x.privilege_type,x.is_grantable FROM pg_catalog.pg_class c JOIN pg_catalog.pg_roles owner ON owner.oid=c.relowner CROSS JOIN LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) x LEFT JOIN pg_catalog.pg_roles grantee ON grantee.oid=x.grantee WHERE c.relnamespace=:ns AND c.relname=ANY(CAST(:tables AS text[])) AND x.grantee<>c.relowner ORDER BY c.relname,grantee,privilege_type LIMIT 257"
        ),
        {"ns": identity.namespace_oid, "tables": list(contract.TABLES)},
    ).all()
    expected = {
        (table, identity.migrator, True, role, privilege, False)
        for table in contract.TABLES
        for role, privileges in (
            (identity.runtime, ("SELECT", "INSERT", "UPDATE", "DELETE")),
            (identity.observer, ("SELECT",)),
        )
        for privilege in privileges
    }
    contract.need(
        len(rows) == len(expected) and {tuple(row) for row in rows} == expected,
        "schema25_new_table_acl_mismatch",
    )


def upgrade_transaction(connection, identity: MigrationIdentity, *, root: Path, flags: dict):
    """Caller owns native bootstrap transaction; old financial uncertainty stays."""
    contract.need(connection.in_transaction(), "schema25_external_transaction_required")
    contract.require_disabled(flags)
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE READ WRITE")
    connection.exec_driver_sql("SET LOCAL statement_timeout='300000ms'")
    connection.exec_driver_sql("SET LOCAL lock_timeout='5000ms'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10000ms'")
    previous.verify_identity(connection, identity)
    contract.need(
        connection.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": LOCK_ID}
        ).scalar_one()
        is True,
        "schema25_migration_already_running",
    )
    contract.verify_before_shape(connection, identity.namespace_oid, identity.namespace)
    acl = previous.schema_acl(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    previous.effective_owner(connection, identity)
    connection.exec_driver_sql(f'SET LOCAL search_path TO "{identity.namespace}"')
    previous.drained(connection, identity)
    contract.need(
        connection.execute(
            text(
                "SELECT count(*) FROM pg_catalog.pg_class WHERE relnamespace=:ns AND relkind='r' AND relowner<>:owner"
            ),
            {"ns": identity.namespace_oid, "owner": identity.migrator_oid},
        ).scalar_one()
        == 0,
        "schema25_existing_table_owner_changed",
    )
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(
        f'GRANT CREATE ON SCHEMA "{identity.namespace}" TO "{identity.migrator}"'
    )
    connection.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
    previous.effective_owner(connection, identity)
    result = _additive_ddl(
        connection,
        namespace_oid=identity.namespace_oid,
        namespace=identity.namespace,
        root=root,
        flags=flags,
    )
    for table in contract.TABLES:
        connection.exec_driver_sql(
            f'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "{identity.namespace}"."{table}" TO "{identity.runtime}"'
        )
        connection.exec_driver_sql(
            f'GRANT SELECT ON TABLE "{identity.namespace}"."{table}" TO "{identity.observer}"'
        )
    verify_new_permissions(connection, identity)
    previous.drained(connection, identity)
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql(
        f'REVOKE CREATE ON SCHEMA "{identity.namespace}" FROM "{identity.migrator}"'
    )
    previous.verify_identity(connection, identity)
    contract.need(previous.schema_acl(connection, identity) == acl, "schema25_schema_acl_changed")
    return result | {"schema_acl": acl, "migration_login_reenabled": False}
