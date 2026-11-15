"""Reproduce the recovered bootstrap role's schema27 permissions on owned PG17."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_notification_schema27_step_postgres import FLAGS, ROOT
from test_notification_schema27_step_postgres import owned26 as owned26

from scripts import notification_schema27_contract as contract
from scripts import notification_schema27_step as step


def test_stock_schema27_needs_table_authority_before_and_after_owner_scope(owned26):
    source = owned26.identity
    database = owned26.engine.url.database
    bootstrap = "continuity_" + uuid4().hex[:12]
    with owned26.engine.begin() as admin:
        admin.exec_driver_sql(f'CREATE ROLE "{bootstrap}" LOGIN NOINHERIT')
        admin.exec_driver_sql(f'ALTER DATABASE "{database}" OWNER TO "{bootstrap}"')
        admin.exec_driver_sql(f'GRANT "{source.migrator}" TO "{bootstrap}" WITH INHERIT FALSE, SET TRUE, ADMIN FALSE')
        admin.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{bootstrap}"')
        oid = admin.scalar(text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:name"), {"name": bootstrap})
    identity = replace(source, bootstrap=bootstrap, bootstrap_oid=oid, database_owner_oid=oid)
    try:
        with owned26.engine.connect() as db:
            db.exec_driver_sql(f'SET SESSION AUTHORIZATION "{bootstrap}"')
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
                db.exec_driver_sql(f'SET LOCAL ROLE "{source.migrator}"')
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
                assert db.scalar(text("SELECT current_user")) == bootstrap
                transaction.rollback()
        with owned26.engine.connect() as admin:
            assert admin.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
            assert admin.scalar(text("SELECT count(*) FROM public.admin_audit_events")) == 0
    finally:
        with owned26.engine.begin() as admin:
            admin.exec_driver_sql(f'ALTER DATABASE "{database}" OWNER TO "{source.bootstrap}"')
            admin.exec_driver_sql(f'DROP OWNED BY "{bootstrap}"')
            admin.exec_driver_sql(f'DROP ROLE "{bootstrap}"')
