"""Bounded SQL step for schema27 on the recovered Neon role model.

The release controller must independently establish transport, authority and
durable journal custody. This step uses the supplied transaction, never commits,
and exposes no native command, credential input or retry entry point.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from alembic.config import Config
from sqlalchemy import text

from alembic import command
from scripts import browser_schema19_step as identity_step
from scripts import notification_schema27_contract as contract
from scripts import notification_schema27_step as retained_step

KIND = "hirewiz_schema27_continuity_additive_v1"
ACTION = "schema27_continuity_additive_migration"
AUDIT_PREFIX = "schema27-continuity-"
AUDIT_REASON = "One-use additive continuity migration"
PROVIDER_ADMIN_GRANTOR = "cloud_admin"
PROVIDER_ADMIN_GRANTOR_OID = 10
HASH = re.compile(r"[a-f0-9]{64}")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,63}")


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class MigrationIntent:
    nonce: str
    actor_email: str
    plan_sha256: str
    target_identity_sha256: str
    bridge_receipts_sha256: str
    source_sha256: str
    catalog26_sha256: str
    retained_acl_sha256: str

    def snapshot(self) -> dict:
        return {
            "kind": KIND,
            "expected": contract.BEFORE,
            "target": contract.SCHEMA,
            "replay_authorized": False,
            **asdict(self),
        }

    def sha256(self) -> str:
        return _sha(self.snapshot())

    def validate(self, identity: identity_step.MigrationIdentity, journal_intent_sha256: str):
        contract.need(
            type(self.nonce) is str and re.fullmatch(r"[a-f0-9]{32}", self.nonce)
            and type(self.actor_email) is str and len(self.actor_email) <= 320 and EMAIL.fullmatch(self.actor_email)
            and all(type(value) is str and HASH.fullmatch(value) for value in (
                self.plan_sha256, self.target_identity_sha256, self.bridge_receipts_sha256,
                self.source_sha256, self.catalog26_sha256, self.retained_acl_sha256,
                journal_intent_sha256,
            )),
            "continuity_intent_invalid",
        )
        contract.need(
            self.target_identity_sha256 == _sha(asdict(identity)) and journal_intent_sha256 == self.sha256(),
            "continuity_intent_custody_mismatch",
        )


def _verify_bootstrap_scope(connection, identity: identity_step.MigrationIdentity) -> None:
    identity_step.verify_identity(connection, identity)
    memberships = connection.execute(text(
        "SELECT m.member,m.grantor,g.rolname,m.admin_option,m.inherit_option,m.set_option "
        "FROM pg_catalog.pg_auth_members m LEFT JOIN pg_catalog.pg_roles g ON g.oid=m.grantor "
        "WHERE m.roleid=:migrator ORDER BY m.member,m.grantor LIMIT 3"
    ), {"migrator": identity.migrator_oid}).all()
    # The provider's role-creation grant can administer membership only.
    # The bootstrap's explicit grant independently permits SET for this role.
    expected_memberships = [
        (identity.bootstrap_oid, PROVIDER_ADMIN_GRANTOR_OID, PROVIDER_ADMIN_GRANTOR, True, False, False),
        (identity.bootstrap_oid, identity.bootstrap_oid, identity.bootstrap, False, False, True),
    ]
    attributes = connection.execute(text(
        "SELECT b.rolcanlogin,b.rolsuper,pg_catalog.pg_has_role(b.oid,:migrator,'USAGE'),"
        "EXISTS(SELECT FROM pg_catalog.pg_auth_members WHERE member=:migrator) "
        "FROM pg_catalog.pg_roles b WHERE b.oid=:bootstrap"
    ), {"bootstrap": identity.bootstrap_oid, "migrator": identity.migrator_oid}).one()
    contract.need(
        identity.database_owner_oid == identity.bootstrap_oid
        and [tuple(row) for row in memberships] == expected_memberships
        and tuple(attributes) == (True, False, False, False),
        "continuity_bootstrap_membership_mismatch",
    )


def _verify_table_ownership(connection, identity: identity_step.MigrationIdentity) -> None:
    tables = connection.execute(text(
        "SELECT relname,relowner FROM pg_catalog.pg_class WHERE relnamespace=:namespace "
        "AND relkind='r' AND relname IN ('users','alembic_version','admin_audit_events') ORDER BY relname"
    ), {"namespace": identity.namespace_oid}).all()
    contract.need(
        [tuple(row) for row in tables] == [
            (name, identity.migrator_oid) for name in ("admin_audit_events", "alembic_version", "users")
        ],
        "continuity_migrator_table_ownership_mismatch",
    )


def receipt(intent: MigrationIntent, catalog_sha256: str) -> dict:
    return {
        "kind": KIND,
        "intent_sha256": intent.sha256(),
        "plan_sha256": intent.plan_sha256,
        "source_sha256": intent.source_sha256,
        "target_identity_sha256": intent.target_identity_sha256,
        "bridge_receipts_sha256": intent.bridge_receipts_sha256,
        "schema": contract.SCHEMA,
        "catalog_sha256": catalog_sha256,
        "retained_acl_sha256": intent.retained_acl_sha256,
        "replay_authorized": False,
    }


def _write_receipt(connection, intent: MigrationIntent, catalog_sha256: str) -> dict:
    value = receipt(intent, catalog_sha256)
    before = intent.snapshot()
    result = connection.execute(text(
        "INSERT INTO public.admin_audit_events "
        "(id,actor_user_id,actor_email,action,target_type,target_id,reason,before_state,after_state,"
        "correlation_id,created_at) "
        "VALUES (:id,NULL,:actor,:action,'database_schema',:target,:reason,CAST(:before AS json),"
        "CAST(:after AS json),:nonce,timezone('UTC',clock_timestamp())) "
        "RETURNING id,actor_user_id,actor_email,action,target_type,target_id,reason,before_state,after_state,correlation_id"
    ), {
        "id": AUDIT_PREFIX + intent.nonce,
        "actor": intent.actor_email,
        "action": ACTION,
        "target": contract.SCHEMA,
        "reason": AUDIT_REASON,
        "before": _canonical(before),
        "after": _canonical(value),
        "nonce": intent.nonce,
    }).one()
    contract.need(
        tuple(result) == (AUDIT_PREFIX + intent.nonce, None, intent.actor_email, ACTION,
                          "database_schema", contract.SCHEMA, AUDIT_REASON, before, value, intent.nonce),
        "continuity_detached_receipt_mismatch",
    )
    return value


def upgrade_transaction(
    connection, identity: identity_step.MigrationIdentity, intent: MigrationIntent,
    *, root: Path, flags: dict, journal_intent_sha256: str,
) -> dict:
    """Prepare additive DDL and its detached receipt within the owned transaction."""
    transaction = connection.get_transaction()
    contract.need(
        connection.dialect.name == "postgresql" and transaction is not None
        and not connection.in_nested_transaction(),
        "continuity_supplied_pg_transaction_required",
    )
    contract.need(
        type(identity) is identity_step.MigrationIdentity and identity.namespace == "public"
        and type(intent) is MigrationIntent,
        "continuity_fixed_intent_and_identity_required",
    )
    identity.validate()
    intent.validate(identity, journal_intent_sha256)
    contract.require_disabled(flags)
    contract.need(contract.verify_source(root) == intent.source_sha256, "continuity_source_changed")
    connection.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL READ COMMITTED READ WRITE")
    connection.exec_driver_sql("SET LOCAL lock_timeout='4s'")
    connection.exec_driver_sql("SET LOCAL statement_timeout='10s'")
    connection.exec_driver_sql("SET LOCAL idle_in_transaction_session_timeout='10s'")
    connection.exec_driver_sql('SET LOCAL search_path TO pg_catalog, "public", pg_temp')
    with connection.begin_nested() as scope:
        _verify_bootstrap_scope(connection, identity)
        _verify_table_ownership(connection, identity)
        contract.need(connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"),
                      {"key": retained_step.LOCK_ID}) is True, "migration_already_running")
        schema_acl = identity_step.schema_acl(connection, identity)
        migrator = f'"{identity.migrator}"'
        connection.exec_driver_sql(f'SET LOCAL ROLE {migrator}')
        identity_step.effective_owner(connection, identity)
        catalog26 = contract.before.observe(connection, namespace_oid=identity.namespace_oid)
        contract.before.verify_writer_permissions(connection, runtime=identity.runtime, migrator=identity.migrator)
        old_acls = retained_step.old_acls(connection, identity)
        contract.need(catalog26 == intent.catalog26_sha256 and _sha(old_acls) == intent.retained_acl_sha256,
                      "continuity_before_catalog_or_acl_changed")
        contract.need(connection.scalar(text(
            "SELECT pg_catalog.to_regclass('public.notification_preferences') IS NULL"
        )) is True, "projection_must_be_absent")
        contract.need(connection.scalar(text(
            "SELECT 1 FROM public.admin_audit_events WHERE id=:id"
        ), {"id": AUDIT_PREFIX + intent.nonce}) is None, "continuity_receipt_already_exists")
        connection.exec_driver_sql('LOCK TABLE "public"."users" IN SHARE ROW EXCLUSIVE MODE')
        connection.exec_driver_sql('LOCK TABLE "public"."alembic_version" IN ACCESS EXCLUSIVE MODE')
        contract.before.marker(connection)

        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql(f'GRANT CREATE ON SCHEMA "public" TO {migrator}')
        connection.exec_driver_sql(f'SET LOCAL ROLE {migrator}')
        identity_step.effective_owner(connection, identity)
        # Alembic's unqualified CREATE must target the fixed public schema.
        connection.exec_driver_sql('SET LOCAL search_path TO "public", pg_temp')
        configuration = Config(str(root / "backend/alembic.ini"))
        configuration.set_main_option("script_location", str(root / "backend/alembic"))
        configuration.attributes["connection"] = connection
        command.upgrade(configuration, contract.SCHEMA)
        contract.need(connection.get_transaction() is transaction and connection.get_nested_transaction() is scope,
                      "continuity_supplied_transaction_lost")
        connection.exec_driver_sql(
            'GRANT SELECT,INSERT,UPDATE,DELETE ON TABLE "public"."notification_preferences" '
            f'TO "{identity.runtime}"'
        )
        connection.exec_driver_sql(f'GRANT SELECT ON TABLE "public"."notification_preferences" TO "{identity.observer}"')

        connection.exec_driver_sql("RESET ROLE")
        connection.exec_driver_sql(f'REVOKE CREATE ON SCHEMA "public" FROM {migrator}')
        _verify_bootstrap_scope(connection, identity)
        contract.need(identity_step.schema_acl(connection, identity) == schema_acl, "schema_acl_changed")
        connection.exec_driver_sql(f'SET LOCAL ROLE {migrator}')
        identity_step.effective_owner(connection, identity)
        catalog27 = contract.observe(connection, identity)
        contract.need(connection.execute(text('SELECT 1 FROM "public"."notification_preferences" LIMIT 1')).first() is None,
                      "projection_must_remain_empty")
        contract.need(retained_step.old_acls(connection, identity) == old_acls, "retained_owner_or_acl_changed")
        receipt = _write_receipt(connection, intent, catalog27)
        connection.exec_driver_sql("RESET ROLE")
        contract.need(contract.verify_source(root) == intent.source_sha256, "continuity_source_changed")
    return {
        "schema": contract.SCHEMA,
        "catalog_sha256": catalog27,
        "retained_acl_sha256": intent.retained_acl_sha256,
        "receipt_sha256": _sha(receipt),
        "transaction_ready_to_commit": True,
        "committed": False,
        "replay_authorized": False,
        "native_attestation": False,
        "migration_login_reenabled": False,
        "new_admission_enabled": False,
    }
