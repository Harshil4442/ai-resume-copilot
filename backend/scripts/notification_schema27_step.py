"""One supplied26→27 additive SQL transaction; NOT a production entrypoint.

No credentials, native/cloud observer, source/CI/flag attestation, commit, retry,
reconciliation or rollout authority is created here. The later authenticated
controller must bind those independently and never replay an uncertain commit.
Caller flag dictionaries are refusal inputs, not native deployment evidence.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import browser_schema19_step as identity_step
from scripts import evidence_writer_schema26_step as current26_step
from scripts import notification_schema27_contract as contract

LOCK_ID = current26_step.LOCK_ID


def old_acls(connection, identity):
    rows = connection.execute(text("SELECT c.relname,c.relkind,c.relowner,coalesce(c.relacl::text,'') "
        "FROM pg_catalog.pg_class c WHERE c.relnamespace=:ns AND c.relkind IN ('r','p','S') "
        "AND c.relname<>:new ORDER BY c.relname LIMIT 257"),
        {"ns": identity.namespace_oid, "new": contract.TABLE}).all()
    contract.need(0 < len(rows) <= 256, "retained_acl_inventory_bound")
    return [tuple(row) for row in rows]


def upgrade_transaction(connection, identity, *, root: Path, flags: dict):
    contract.need(connection.dialect.name == "postgresql" and connection.in_transaction(), "supplied_pg_transaction_required")
    contract.need(type(identity) is identity_step.MigrationIdentity and identity.namespace == "public", "fixed_identity_required")
    contract.require_disabled(flags)
    source = contract.verify_source(root)
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
    connection.exec_driver_sql("SET LOCAL lock_timeout='4s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10s'")
    identity_step.verify_identity(connection, identity)
    contract.need(connection.execute(text("SELECT pg_try_advisory_xact_lock(:key)"),
                  {"key": LOCK_ID}).scalar_one() is True, "migration_already_running")
    contract.before.observe(connection, namespace_oid=identity.namespace_oid)
    contract.before.verify_writer_permissions(connection, runtime=identity.runtime, migrator=identity.migrator)
    contract.need(connection.execute(text("SELECT count(*) FROM pg_catalog.pg_class "
        "WHERE relnamespace=:ns AND relname=:name"), {"ns": identity.namespace_oid, "name": contract.TABLE}).scalar_one() == 0,
        "projection_must_be_absent")
    original_schema_acl = identity_step.schema_acl(connection, identity)
    retained_acls = old_acls(connection, identity)
    # Only the referenced owner table and Alembic marker need explicit locks.
    # Historical customer/financial rows may legitimately change elsewhere.
    connection.exec_driver_sql('LOCK TABLE "public"."users" IN SHARE ROW EXCLUSIVE MODE')
    connection.exec_driver_sql('LOCK TABLE "public"."alembic_version" IN ACCESS EXCLUSIVE MODE')
    contract.before.marker(connection)
    connection.exec_driver_sql('GRANT CREATE ON SCHEMA "public" TO "' + identity.migrator + '"')
    connection.exec_driver_sql('SET LOCAL ROLE "' + identity.migrator + '"')
    identity_step.effective_owner(connection, identity)
    connection.exec_driver_sql('SET LOCAL search_path TO "public"')
    configuration = Config(str(root / "backend/alembic.ini"))
    configuration.set_main_option("script_location", str(root / "backend/alembic"))
    configuration.attributes["connection"] = connection
    command.upgrade(configuration, contract.SCHEMA)
    contract.need(connection.in_transaction(), "supplied_transaction_lost")
    # Exact source-reviewed grants on the NEW table only; no sequence, role,
    # default privileges, LOGIN or existing customer table grants are changed.
    connection.exec_driver_sql('GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "public"."notification_preferences" TO "' + identity.runtime + '"')
    connection.exec_driver_sql('GRANT SELECT ON TABLE "public"."notification_preferences" TO "' + identity.observer + '"')
    contract.need(connection.execute(text('SELECT 1 FROM "public"."notification_preferences" LIMIT 1')).first() is None,
                  "projection_must_remain_empty")
    connection.exec_driver_sql("RESET ROLE")
    connection.exec_driver_sql('REVOKE CREATE ON SCHEMA "public" FROM "' + identity.migrator + '"')
    identity_step.verify_identity(connection, identity)
    signature = contract.observe(connection, identity)
    contract.need(identity_step.schema_acl(connection, identity) == original_schema_acl, "schema_acl_changed")
    contract.need(old_acls(connection, identity) == retained_acls, "retained_owner_or_acl_changed")
    contract.need(contract.verify_source(root) == source, "source_changed")
    return {"schema": contract.SCHEMA, "source_sha256": source, "catalog_sha256": signature,
        "retained_acl_sha256": hashlib.sha256(json.dumps(retained_acls, separators=(",", ":")).encode()).hexdigest(),
        "transaction_ready_to_commit": True, "committed": False,
        "replay_authorized": False, "native_attestation": False,
        "migration_login_reenabled": False, "new_admission_enabled": False}
