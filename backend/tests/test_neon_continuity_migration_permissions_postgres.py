"""Reproduce the recovered bootstrap role's schema27 permissions on owned PG17."""

from __future__ import annotations

from dataclasses import asdict, replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_notification_schema27_step_postgres import FLAGS, ROOT
from test_notification_schema27_step_postgres import owned26 as owned26

from scripts import notification_schema27_continuity_step as continuity
from scripts import notification_schema27_contract as contract
from scripts import notification_schema27_step as step


@pytest.fixture
def set_only_bootstrap(owned26, monkeypatch):
    source = owned26.identity
    database = owned26.engine.url.database
    bootstrap = "continuity_" + uuid4().hex[:12]
    with owned26.engine.begin() as admin:
        admin.exec_driver_sql(f'CREATE ROLE "{bootstrap}" LOGIN NOINHERIT')
        admin.exec_driver_sql(f'ALTER DATABASE "{database}" OWNER TO "{bootstrap}"')
        # Reproduce PostgreSQL's automatic role-creator grant and then use
        # that real ADMIN option to issue the separate explicit SET grant.
        admin.exec_driver_sql(f'GRANT "{source.migrator}" TO "{bootstrap}" WITH ADMIN TRUE, INHERIT FALSE, SET FALSE')
        admin.exec_driver_sql(f'SET LOCAL ROLE "{bootstrap}"')
        admin.exec_driver_sql(f'GRANT "{source.migrator}" TO "{bootstrap}" WITH ADMIN FALSE, INHERIT FALSE, SET TRUE')
        admin.exec_driver_sql("RESET ROLE")
        admin.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{bootstrap}"')
        oid = admin.scalar(text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:name"), {"name": bootstrap})
        grants = admin.execute(text(
            "SELECT m.grantor,g.rolname,m.admin_option,m.inherit_option,m.set_option "
            "FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles g ON g.oid=m.grantor "
            "WHERE m.member=:bootstrap AND m.roleid=:migrator ORDER BY m.grantor"
        ), {"bootstrap": oid, "migrator": source.migrator_oid}).all()
        assert grants == [(10, source.bootstrap, True, False, False), (oid, bootstrap, False, False, True)]
    monkeypatch.setattr(continuity, "PROVIDER_ADMIN_GRANTOR", source.bootstrap)
    identity = replace(source, bootstrap=bootstrap, bootstrap_oid=oid, database_owner_oid=oid)
    try:
        yield identity
    finally:
        with owned26.engine.begin() as admin:
            admin.exec_driver_sql(f'ALTER DATABASE "{database}" OWNER TO "{source.bootstrap}"')
            admin.exec_driver_sql(f'DROP OWNED BY "{bootstrap}"')
            admin.exec_driver_sql(f'DROP ROLE "{bootstrap}"')


def test_stock_schema27_needs_table_authority_before_and_after_owner_scope(owned26, set_only_bootstrap):
    identity = set_only_bootstrap
    with owned26.engine.connect() as db:
        db.exec_driver_sql(f'SET SESSION AUTHORIZATION "{identity.bootstrap}"')
        db.commit()
        with pytest.raises(DBAPIError) as denied:
            with db.begin():
                step.upgrade_transaction(db, identity, root=ROOT, flags=FLAGS)
        assert denied.value.orig.sqlstate == "42501"
        with db.begin() as transaction:
            assert db.exec_driver_sql(
                "SELECT has_table_privilege(current_user,'public.users','UPDATE'),"
                "has_table_privilege(current_user,'public.alembic_version','SELECT'),"
                "has_table_privilege(current_user,'public.admin_audit_events','INSERT')"
            ).one() == (False, False, False)
            db.exec_driver_sql(f'SET LOCAL ROLE "{identity.migrator}"')
            assert contract.before.marker(db) == contract.BEFORE
            db.exec_driver_sql("LOCK TABLE public.users IN SHARE ROW EXCLUSIVE MODE")
            db.exec_driver_sql("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
            audit_id = db.scalar(text(
                "INSERT INTO public.admin_audit_events "
                "(id,actor_email,action,target_type,target_id,reason,before_state,after_state,created_at) "
                "VALUES ('continuity-test','owned@example.invalid','owned-probe','schema','test',"
                "'owned PG17','{}','{}',clock_timestamp()) RETURNING id"
            ))
            assert audit_id == "continuity-test"
            db.exec_driver_sql("RESET ROLE")
            assert db.scalar(text("SELECT current_user")) == identity.bootstrap
            transaction.rollback()
    with owned26.engine.connect() as admin:
        assert admin.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
        assert admin.scalar(text("SELECT count(*) FROM public.admin_audit_events")) == 0


def test_continuity_role_migrates_and_audits_together_after_atomic_refusal(owned26, set_only_bootstrap, monkeypatch):
    identity = set_only_bootstrap
    with owned26.engine.connect() as db:
        before = step.old_acls(db, identity)
        intent = continuity.MigrationIntent(
            nonce="1" * 32,
            actor_email="owned@example.invalid",
            plan_sha256="2" * 64,
            target_identity_sha256=continuity._sha(asdict(identity)),
            bridge_receipts_sha256="3" * 64,
            source_sha256=contract.verify_source(ROOT),
            catalog26_sha256=contract.before.observe(db),
            retained_acl_sha256=continuity._sha(before),
        )

    def prepare(db, journal_hash):
        return continuity.upgrade_transaction(
            db, identity, intent, root=ROOT, flags=FLAGS, journal_intent_sha256=journal_hash,
        )

    write_receipt = continuity._write_receipt

    def fail_after_audit(db, item, catalog):
        write_receipt(db, item, catalog)
        assert db.scalar(text("SELECT current_user")) == identity.migrator
        assert contract.marker(db) == contract.SCHEMA
        raise RuntimeError("owned failure after DDL and receipt")

    with owned26.engine.connect() as db:
        db.exec_driver_sql(f'SET SESSION AUTHORIZATION "{identity.bootstrap}"')
        db.commit()
        with db.begin():
            with pytest.raises(contract.Schema27Denied, match="intent_custody_mismatch"):
                prepare(db, "0" * 64)
        with owned26.engine.begin() as admin:
            admin.exec_driver_sql(f'GRANT "{identity.migrator}" TO "{identity.bootstrap}" WITH SET TRUE')
        with db.begin():
            with pytest.raises(contract.Schema27Denied, match="bootstrap_membership_mismatch"):
                prepare(db, intent.sha256())
        with owned26.engine.begin() as admin:
            admin.exec_driver_sql(f'GRANT "{identity.migrator}" TO "{identity.bootstrap}" WITH SET FALSE')
        with owned26.engine.begin() as admin:
            admin.exec_driver_sql(f'SET LOCAL ROLE "{identity.bootstrap}"')
            admin.exec_driver_sql(f'GRANT "{identity.migrator}" TO "{identity.bootstrap}" WITH INHERIT TRUE')
        with db.begin():
            with pytest.raises(contract.Schema27Denied, match="bootstrap_membership_mismatch"):
                prepare(db, intent.sha256())
        with owned26.engine.begin() as admin:
            admin.exec_driver_sql(f'SET LOCAL ROLE "{identity.bootstrap}"')
            admin.exec_driver_sql(f'GRANT "{identity.migrator}" TO "{identity.bootstrap}" WITH INHERIT FALSE')
        with monkeypatch.context() as patch:
            patch.setattr(continuity, "_write_receipt", fail_after_audit)
            with db.begin():
                with pytest.raises(RuntimeError, match="owned failure after DDL"):
                    prepare(db, intent.sha256())
                assert db.scalar(text("SELECT current_user")) == identity.bootstrap
                assert db.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
        with owned26.engine.connect() as observer:
            assert contract.before.marker(observer) == contract.BEFORE
            assert observer.scalar(text("SELECT count(*) FROM public.admin_audit_events")) == 0
        with db.begin():
            result = prepare(db, intent.sha256())
            assert result["transaction_ready_to_commit"]
            assert result["committed"] is result["native_attestation"] is result["replay_authorized"] is False
            assert db.scalar(text("SELECT current_user")) == identity.bootstrap
            assert db.scalar(text("SELECT NOT has_schema_privilege(:owner,'public','CREATE')"),
                             {"owner": identity.migrator})
            with owned26.engine.connect() as observer:
                assert observer.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
                assert observer.scalar(text("SELECT count(*) FROM public.admin_audit_events")) == 0

    with owned26.engine.connect() as db:
        assert contract.observe(db, identity) == result["catalog_sha256"]
        assert step.old_acls(db, identity) == before
        assert db.scalar(text("SELECT ai_credits FROM public.users WHERE id=1")) == 17
        statement = text("SELECT actor_user_id,action,before_state,after_state FROM public.admin_audit_events")
        detached_user, action, saved_intent, saved_receipt = db.execute(statement).one()
        assert detached_user is None and action == continuity.ACTION
        assert saved_intent == intent.snapshot()
        assert continuity._sha(saved_receipt) == result["receipt_sha256"]
        assert saved_receipt["intent_sha256"] == intent.sha256()
