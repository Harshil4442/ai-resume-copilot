"""Owned PG17 physical reads; not live Neon/TLS/source qualification.

The source port and expected physical constants are explicitly isolated to a
fresh loopback database. Catalog rows, actor/session identity, cluster ID and
schema observations are real. The production transport guard is never relaxed:
a separate genuine loopback-driver test must refuse before dialect SQL.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.pool import NullPool
from test_mail_budget_postgres import owned_pg_url as owned_pg_url
from test_neon_schema19_release_transport import Reader

from scripts import neon_direct_identity as neon


@pytest.fixture
def local_target(owned_pg_url, tmp_path, monkeypatch):
    admin = create_engine(owned_pg_url, isolation_level="AUTOCOMMIT", poolclass=NullPool)
    database = "neon27_" + uuid4().hex
    alternate = "n27_actor_" + uuid4().hex[:16]
    with admin.connect() as db:
        db.exec_driver_sql(f'CREATE DATABASE "{database}"')
        db.exec_driver_sql(f'CREATE ROLE "{alternate}" NOLOGIN')
    engine = create_engine(admin.url.set(database=database), poolclass=NullPool, hide_parameters=True)
    try:
        with engine.begin() as db:
            db.exec_driver_sql(f'GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "{alternate}"')
            row = db.execute(text("SELECT d.oid,d.datdba,r.rolname,r.oid,n.oid,n.nspowner,"
                "(SELECT system_identifier::text FROM pg_catalog.pg_control_system()) "
                "FROM pg_catalog.pg_database d JOIN pg_catalog.pg_roles r ON r.rolname=current_user "
                "JOIN pg_catalog.pg_namespace n ON n.nspname='public' WHERE d.datname=current_database()" )).one()
            db.exec_driver_sql("CREATE TABLE public.alembic_version(version_num VARCHAR(32) PRIMARY KEY)")
            db.execute(text("INSERT INTO public.alembic_version VALUES (:schema)"), {"schema": neon.SCHEMA26_RELEASE_SCHEMA})
        monkeypatch.setattr(neon, "DATABASE", database)
        monkeypatch.setattr(neon, "FRESH_DATABASE_OID", row[0])
        monkeypatch.setattr(neon, "FRESH_DATABASE_OWNER_OID", row[1])
        monkeypatch.setattr(neon, "FRESH_NAMESPACE_OWNER_OID", row[5])
        monkeypatch.setattr(neon, "FRESH_CLUSTER", hashlib.sha256(row[6].encode()).hexdigest())
        monkeypatch.setattr(neon, "FRESH_RELEASE_ACTORS", {"observer": (row[2], row[3])})
        receipt = {"status": "target_roles_created", "stage": "fresh_target_role_creation",
            "org_id": neon.ORGANIZATION, "project_id": neon.FRESH_NEON_PROJECT,
            "branch_id": neon.FRESH_BRANCH, "endpoint_id": neon.FRESH_ENDPOINT,
            "direct_host": neon.FRESH_DIRECT_HOST, "database": database,
            "commit_acknowledged": True, "production_switch": False, "global_provider_fence_proven": False,
            "summary": {"role_oids": {row[2]: row[3]}}}
        path = tmp_path / "owned-synthetic-target.json"
        path.write_text(json.dumps(receipt))
        monkeypatch.setattr(neon, "FRESH_CONTROL_RECEIPT", path)
        monkeypatch.setattr(neon, "FRESH_CONTROL_RECEIPT_SHA", hashlib.sha256(path.read_bytes()).hexdigest())
        reader = Reader()
        reader.value = reader.value.replace("/neondb?", f"/{database}?")
        before, _ = neon.collect_schema26_release_target(reader, reader.reference, alias="observer", database_owner_oid=row[1])
        after, _ = neon.collect_schema27_release_target(reader, reader.reference, alias="observer", database_owner_oid=row[1])
        yield engine, before, after, alternate
    finally:
        engine.dispose()
        with admin.connect() as db:
            db.exec_driver_sql(f'DROP DATABASE "{database}" WITH (FORCE)')
            db.exec_driver_sql(f'DROP ROLE "{alternate}"')
        admin.dispose()


def test_real_catalog26_then27_requires_distinct_original_binding(local_target):
    engine, before, after, _ = local_target
    with engine.begin() as db:
        neon.verify_native_target(db, before, neon.FRESH_DATABASE_OID)
        with pytest.raises(neon.NeonIdentityDenied, match="exact_schema_mismatch"):
            neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID)
        db.execute(text("UPDATE public.alembic_version SET version_num=:schema"), {"schema": neon.SCHEMA27_RELEASE_SCHEMA})
        neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID)
        with pytest.raises(neon.NeonIdentityDenied, match="exact_schema_mismatch"):
            neon.verify_native_target(db, before, neon.FRESH_DATABASE_OID)
        assert neon.bound_schema(before) == neon.SCHEMA26_RELEASE_SCHEMA
        assert neon.bound_schema(after) == neon.SCHEMA27_RELEASE_SCHEMA


def test_real_actor_and_session_cannot_be_replaced_under27_binding(local_target):
    engine, _, after, alternate = local_target
    with engine.begin() as db:
        db.execute(text("UPDATE public.alembic_version SET version_num=:schema"), {"schema": neon.SCHEMA27_RELEASE_SCHEMA})
        db.exec_driver_sql(f'SET LOCAL ROLE "{alternate}"')
        with pytest.raises(neon.NeonIdentityDenied, match="native_target_mismatch"):
            neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID)
        db.exec_driver_sql("RESET ROLE")
        neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID)
        with pytest.raises(neon.NeonIdentityDenied, match="native_target_mismatch"):
            neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID + 1)


def test_real_catalog_cluster_and_database_owner_drift_refuse(local_target, monkeypatch):
    engine, _, after, _ = local_target
    with engine.begin() as db:
        db.execute(text("UPDATE public.alembic_version SET version_num=:schema"), {"schema": neon.SCHEMA27_RELEASE_SCHEMA})
        with monkeypatch.context() as changed:
            changed.setattr(neon, "FRESH_CLUSTER", "0" * 64)
            with pytest.raises(neon.NeonIdentityDenied, match="native_target_mismatch"):
                neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID)
        # Compare actual ownership, not a replacement caller-provided schema label.
        db.exec_driver_sql("ALTER SCHEMA public OWNER TO CURRENT_USER")
        with pytest.raises(neon.NeonIdentityDenied, match="native_target_mismatch"):
            neon.verify_native_target(db, after, neon.FRESH_DATABASE_OID)


def test_actual_loopback_driver_is_closed_before_any_dialect_sql(local_target, monkeypatch):
    source, _, binding, _ = local_target
    engine = neon._TransientTarget(binding, source.url).engine()
    drivers, initialized, queries = [], [], []
    original = neon._verify_driver_transport

    def inspect(driver, exact_binding):
        drivers.append(driver)
        assert exact_binding is binding
        original(driver, exact_binding)

    monkeypatch.setattr(neon, "_verify_driver_transport", inspect)
    monkeypatch.setattr(engine.dialect, "initialize", lambda _: initialized.append(True))
    event.listen(engine, "before_cursor_execute", lambda *_: queries.append(True))
    try:
        with pytest.raises(neon.NeonIdentityDenied, match="connection_transport_refused"):
            engine.connect()
        assert len(drivers) == 1 and drivers[0].closed
        assert initialized == queries == []
    finally:
        engine.dispose()
