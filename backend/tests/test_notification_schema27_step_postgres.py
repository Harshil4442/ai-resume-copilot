"""Actual owned localPG17 Alembic/catalog/roles; never native rollout authority."""
from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from test_mail_budget_postgres import owned_pg_url as owned_pg_url

from alembic import command
from app.models import ModelCostLiability
from scripts import browser_schema19_step as identity_step
from scripts import notification_schema27_contract as contract
from scripts import notification_schema27_step as step

ROOT = Path(__file__).resolve().parents[2]
FLAGS = dict.fromkeys(contract.DISABLED_FLAGS, "false")


@pytest.fixture
def owned26(owned_pg_url):
    admin = create_engine(owned_pg_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    suffix = uuid4().hex[:16]
    database = "schema27_" + suffix
    names = {kind: "c27_"+suffix+"_"+kind for kind in ("migrator", "runtime", "observer")}
    engine = None
    try:
        with admin.connect() as db:
            for kind, name in names.items():
                db.exec_driver_sql(f'CREATE ROLE "{name}" '+("NOLOGIN" if kind == "migrator" else "LOGIN")+
                                   " NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS")
            db.exec_driver_sql(f'CREATE DATABASE "{database}"')
        engine = create_engine(admin.url.set(database=database), poolclass=NullPool, hide_parameters=True)
        with engine.connect() as db, db.begin():
            bootstrap = db.scalar(text("SELECT session_user"))
            db.exec_driver_sql(f'GRANT USAGE,CREATE ON SCHEMA public TO "{names["migrator"]}"')
            db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{names["runtime"]}","{names["observer"]}"')
            db.exec_driver_sql(f'SET LOCAL ROLE "{names["migrator"]}"')
            config = Config(str(ROOT/"backend/alembic.ini"))
            config.set_main_option("script_location", str(ROOT/"backend/alembic"))
            config.attributes["connection"] = db
            command.upgrade(config, contract.BEFORE)
            db.exec_driver_sql("RESET ROLE")
            db.exec_driver_sql(f'REVOKE CREATE ON SCHEMA public FROM "{names["migrator"]}"')
        with engine.connect() as db, db.begin():
            row = db.execute(text("SELECT d.oid,d.datdba,n.oid,n.nspowner,r.oid,"
                "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
                "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_namespace n ON n.nspname='public' "
                "JOIN pg_catalog.pg_roles r ON r.rolname=session_user WHERE d.datname=current_database()" )).one()
            oids = {name: db.scalar(text("SELECT oid FROM pg_catalog.pg_roles WHERE rolname=:name"), {"name": name}) for name in names.values()}
            identity = identity_step.MigrationIdentity(hashlib.sha256(row[5].encode()).hexdigest(),
                row[0], row[1], "public", row[2], row[3], bootstrap, row[4],
                names["migrator"], oids[names["migrator"]], names["runtime"], oids[names["runtime"]],
                names["observer"], oids[names["observer"]])
            db.execute(text("INSERT INTO public.users(id,email,ai_credits,job_service_credits) VALUES(1,'owned@example.invalid',17,19)"))
        with Session(engine) as db:
            db.add(ModelCostLiability(id="owned-unknown", financial_group_id="owned-finance", attempt_number=1,
                provider="synthetic", model="synthetic", endpoint_key="a"*64, currency="USD", pricing_quote={"synthetic": True},
                input_token_estimate=1, reserved_cost_micros=23, cost_state="outcome_unknown", created_at=datetime.now(UTC)))
            db.commit()
        yield SimpleNamespace(engine=engine, identity=identity)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as db:
            db.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database}" WITH (FORCE)')
            for name in names.values():
                db.exec_driver_sql(f'DROP ROLE IF EXISTS "{name}"')
        admin.dispose()


def snapshots(db):
    return [tuple(row) for row in db.execute(text("SELECT ai_credits,job_service_credits FROM public.users WHERE id=1"))], \
        db.scalar(text("SELECT to_jsonb(t)::text FROM public.model_cost_liabilities t WHERE id='owned-unknown'"))


def migrate(c):
    with c.engine.connect() as db, db.begin():
        return step.upgrade_transaction(db, c.identity, root=ROOT, flags=FLAGS)


def test_additive27_exact_catalog_acl_and_old_rows_fence_survive(owned26):
    c = owned26
    with c.engine.connect() as db:
        before = snapshots(db)
        acl = step.old_acls(db, c.identity)
    result = migrate(c)
    assert result["transaction_ready_to_commit"] and result["committed"] is False
    assert result["native_attestation"] is result["new_admission_enabled"] is result["replay_authorized"] is False
    with c.engine.connect() as db, db.begin():
        assert snapshots(db) == before and step.old_acls(db, c.identity) == acl
        assert contract.observe(db, c.identity)
        with pytest.raises(contract.before.Schema26Denied, match="exact_marker"):
            contract.before.marker(db)
        assert db.scalar(text("SELECT count(*) FROM public.notification_preferences")) == 0
        assert db.scalar(text("SELECT has_schema_privilege(:role,'public','CREATE')"), {"role": c.identity.migrator}) is False
    with c.engine.connect() as db, db.begin():
        db.exec_driver_sql(f'SET LOCAL ROLE "{c.identity.observer}"')
        assert db.scalar(text("SELECT count(*) FROM public.notification_preferences")) == 0
        with pytest.raises(DBAPIError):
            db.exec_driver_sql("DELETE FROM public.notification_preferences")


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE public.shadow_projection () INHERITS (public.notification_preferences)",
    "GRANT {runtime} TO {observer} WITH INHERIT FALSE, SET TRUE",
    "ALTER ROLE {observer} CREATEROLE",
    "ALTER TABLE public.notification_preferences ADD COLUMN extra text",
    "ALTER TABLE public.notification_preferences ALTER COLUMN enabled SET DEFAULT true",
    "ALTER TABLE public.notification_preferences ENABLE ROW LEVEL SECURITY",
    "DROP INDEX public.ix_notification_preferences_due",
    "ALTER TABLE public.users DISABLE TRIGGER ALL",
    "GRANT SELECT ON public.notification_preferences TO PUBLIC",
    "GRANT UPDATE(enabled) ON public.notification_preferences TO {observer}",
    "GRANT {migrator} TO {runtime} WITH INHERIT FALSE, SET TRUE",
    "ALTER TABLE public.notification_preferences DROP CONSTRAINT ck_notification_pref_schedule; "
    "ALTER TABLE public.notification_preferences ADD CONSTRAINT ck_notification_pref_schedule "
    "CHECK (enabled AND (next_due_at IS NOT NULL OR NOT enabled) AND next_due_at IS NULL)",
])
def test_real_catalog_acl_or_schedule_drift_refuses_then_rollback_restores(owned26, ddl):
    c = owned26
    migrate(c)
    with c.engine.connect() as db:
        db.exec_driver_sql(ddl.format(observer='"'+c.identity.observer+'"', migrator='"'+c.identity.migrator+'"', runtime='"'+c.identity.runtime+'"'))
        with pytest.raises(contract.Schema27Denied):
            contract.observe(db, c.identity)
        db.rollback()
        assert contract.observe(db, c.identity)


def test_failure_after_actual27_ddl_rolls_back_table_grants_marker_and_keeps_rows(owned26, monkeypatch):
    c = owned26
    with c.engine.connect() as db:
        before = snapshots(db)
    original = contract.observe
    def refuse_after_ddl(db, identity):
        assert db.scalar(text("SELECT version_num FROM public.alembic_version")) == contract.SCHEMA
        raise RuntimeError("owned post-DDL refusal")
    monkeypatch.setattr(contract, "observe", refuse_after_ddl)
    with pytest.raises(RuntimeError, match="post-DDL"):
        migrate(c)
    monkeypatch.setattr(contract, "observe", original)
    with c.engine.connect() as db:
        assert snapshots(db) == before
        assert contract.before.marker(db) == contract.BEFORE
        assert db.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
        identity_step.verify_identity(db, c.identity)


def test_live_writer_fk_lock_timeout_is_atomic_no_migration_replay(owned26):
    c = owned26
    started, release = Event(), Event()
    def writer():
        with c.engine.connect() as db, db.begin():
            db.execute(text("UPDATE public.users SET ai_credits=ai_credits+1 WHERE id=1"))
            started.set()
            assert release.wait(10)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(writer)
        assert started.wait(2)
        try:
            with pytest.raises(DBAPIError):
                migrate(c)
        finally:
            release.set()
        future.result(timeout=3)
    with c.engine.connect() as db:
        assert contract.before.marker(db) == contract.BEFORE
        assert db.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
        assert db.scalar(text("SELECT ai_credits FROM public.users WHERE id=1")) == 18
    # A later caller-owned new transaction is explicit work, not an internal retry.
    assert migrate(c)["committed"] is False


def test_current26_migration_advisory_lock_blocks27_before_any_ddl(owned26):
    from scripts import evidence_writer_schema26_step as current26
    c = owned26
    with c.engine.connect() as blocker, blocker.begin():
        blocker.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": current26.LOCK_ID})
        with pytest.raises(contract.Schema27Denied, match="migration_already_running"):
            migrate(c)
        with c.engine.connect() as observer:
            assert contract.before.marker(observer) == contract.BEFORE
            assert observer.scalar(text("SELECT to_regclass('public.notification_preferences')")) is None
    # Releasing the current protocol interlock permits a new explicit attempt.
    assert migrate(c)["committed"] is False
