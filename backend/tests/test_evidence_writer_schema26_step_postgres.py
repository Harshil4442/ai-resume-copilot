"""Genuine supplied-transaction26 qualification on CI's owned PG17 only.

Reuse the existing per-test database/25 seed. Synthetic LOGIN/NOLOGIN roles and
real SQL grants follow the existing schema25 step fixture. This is SQL/role and
DDL evidence, never provider custody, owner authorization or production execution.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool
from test_evidence_writer_fence_postgres import pg25 as pg25  # noqa: F401
from test_schema25_app_compatibility_postgres import ROOT, release
from test_schema25_app_compatibility_postgres import app25_database as app25_database

from app.models import EntitlementLedger, ModelCostLiability, PaymentOrder
from scripts import evidence_writer_schema26_step as step

PASSWORD = "isolated-schema26-step-test-only"
FLAGS = {name: "false" for name in step.previous.DISABLED_FLAGS}


@pytest.fixture
def owned26(request):
    if (
        sys.platform != "linux"
        or os.getenv("CI") != "true"
        or os.getenv("GITHUB_ACTIONS") != "true"
        or not os.getenv("HIREWIZ_TEST_POSTGRES_URL")
        or os.getenv("HIREWIZ_APP25_TEST_ROOT")
    ):
        pytest.fail("Migration26 qualification requires the existing owned Linux CI PG17 fixture")
    source = request.getfixturevalue("pg25")
    assert source.url.host == "127.0.0.1" and source.url.port in {55433, 55434}
    assert re.fullmatch(r"app25_[a-f0-9]{32}", source.url.database)
    names = {
        key: "c26_" + uuid4().hex[:20] + "_" + key
        for key in ("bootstrap", "migrator", "runtime", "observer")
    }
    created, created_oids, engines = [], {}, []
    try:
        now = datetime.now(UTC)
        with Session(source) as db:
            order = PaymentOrder(
                public_id="m26-paid",
                user_id=1,
                sku="synthetic",
                catalog_version="synthetic",
                entitlement_kind="analysis_units",
                entitlement_quantity=3,
                billing_country_confirmed_at=now,
                gross_amount_minor=100,
                status="paid",
            )
            db.add(order)
            db.flush()
            db.add_all(
                [
                    EntitlementLedger(
                        user_id=1,
                        source_order_id=order.id,
                        source_provider="synthetic",
                        sku="synthetic",
                        entitlement_kind="analysis_units",
                        quantity=3,
                        starts_at=now,
                        status="active",
                    ),
                    ModelCostLiability(
                        id="m26-held",
                        financial_group_id="m26-synthetic",
                        attempt_number=1,
                        provider="synthetic",
                        model="synthetic",
                        endpoint_key="d" * 64,
                        currency="USD",
                        pricing_quote={"synthetic": True},
                        input_token_estimate=1,
                        reserved_cost_micros=12345,
                        cost_state="outcome_unknown",
                    ),
                ]
            )
            db.commit()
        with source.begin() as db:
            assert 170000 <= int(db.scalar(text("SHOW server_version_num"))) < 180000
            db.execute(
                text(
                    "INSERT INTO password_reset_tokens(id,user_id,token_sha256,email_sha256,"
                    "credential_sha256,legacy_epoch,status,created_at,expires_at) "
                    "VALUES('m26-reset',1,:sha,:sha,:sha,9,'PENDING',now(),now()+interval '15 minutes')"
                ),
                {"sha": "a" * 64},
            )
            for key, name in names.items():
                login = "NOLOGIN" if key == "migrator" else "LOGIN"
                db.exec_driver_sql(
                    f'CREATE ROLE "{name}" {login} NOINHERIT NOSUPERUSER NOCREATEDB '
                    f"NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD '{PASSWORD}'"
                )
                created.append(name)
                created_oids[name] = db.scalar(
                    text("SELECT oid FROM pg_roles WHERE rolname=:name"), {"name": name}
                )
            db.exec_driver_sql(
                f'GRANT "{names["migrator"]}" TO "{names["bootstrap"]}" '
                "WITH ADMIN FALSE, INHERIT FALSE, SET TRUE"
            )
            for name in names.values():
                db.exec_driver_sql(f'GRANT USAGE ON SCHEMA public TO "{name}"')
            # Model the existing native bootstrap owner's finite delegation
            # capability without data SELECT, table ownership or INHERIT.
            db.exec_driver_sql(
                f'GRANT CREATE ON SCHEMA public TO "{names["bootstrap"]}" WITH GRANT OPTION'
            )
            db.exec_driver_sql(
                "GRANT EXECUTE ON FUNCTION pg_catalog.pg_control_system() TO "
                f'"{names["bootstrap"]}"'
            )
            tables = (
                db.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace "
                        "AND relkind='r' ORDER BY relname LIMIT 257"
                    )
                )
                .scalars()
                .all()
            )
            assert 1 < len(tables) <= 256
            for table in tables:
                assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", table)
                db.exec_driver_sql(f'ALTER TABLE public."{table}" OWNER TO "{names["migrator"]}"')
            sequences = (
                db.execute(
                    text(
                        "SELECT relname FROM pg_class WHERE relnamespace='public'::regnamespace "
                        "AND relkind='S' ORDER BY relname LIMIT 257"
                    )
                )
                .scalars()
                .all()
            )
            assert len(sequences) <= 256
            for sequence in sequences:
                assert re.fullmatch(r"[a-z][a-z0-9_]{0,62}", sequence)
                db.exec_driver_sql(
                    f'ALTER SEQUENCE public."{sequence}" OWNER TO "{names["migrator"]}"'
                )
            db.exec_driver_sql(f'SET LOCAL ROLE "{names["migrator"]}"')
            db.exec_driver_sql(
                "GRANT SELECT ON ALL TABLES IN SCHEMA public TO "
                f'"{names["runtime"]}", "{names["observer"]}"'
            )
            db.exec_driver_sql(f'GRANT SELECT ON public.alembic_version TO "{names["bootstrap"]}"')
            # Bootstrap deliberately gets no blanket table SELECT/role inheritance.
            # The real step must enter the explicit owner role before data reads.
            erasable = (
                set(release.rolling.release.ERASABLE_TABLES)
                | set(step.previous.TABLES)
                | {
                    "candidate_google_enrollment_challenges",
                    "candidate_google_identities",
                    "password_reset_tokens",
                }
            )
            for table in tables:
                if table != "alembic_version":
                    db.exec_driver_sql(f'GRANT INSERT ON public."{table}" TO "{names["runtime"]}"')
                if table not in {"alembic_version", "candidate_identity_history"}:
                    db.exec_driver_sql(f'GRANT UPDATE ON public."{table}" TO "{names["runtime"]}"')
                if table in erasable:
                    db.exec_driver_sql(f'GRANT DELETE ON public."{table}" TO "{names["runtime"]}"')
            db.exec_driver_sql(
                f'GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO "{names["runtime"]}"'
            )
            db.exec_driver_sql("RESET ROLE")
            actual = db.execute(
                text(
                    "SELECT d.oid,d.datdba,n.oid,n.nspowner,"
                    "(SELECT system_identifier::text FROM pg_control_system()) FROM pg_database d "
                    "JOIN pg_namespace n ON n.nspname='public' WHERE d.datname=current_database()"
                )
            ).one()
            oids = dict(
                db.execute(
                    text(
                        "SELECT rolname,oid FROM pg_roles WHERE rolname=ANY(CAST(:names AS text[]))"
                    ),
                    {"names": list(names.values())},
                ).all()
            )
        identity = step.identity_step.MigrationIdentity(
            hashlib.sha256(actual[4].encode()).hexdigest(),
            actual[0],
            actual[1],
            "public",
            actual[2],
            actual[3],
            names["bootstrap"],
            oids[names["bootstrap"]],
            names["migrator"],
            oids[names["migrator"]],
            names["runtime"],
            oids[names["runtime"]],
            names["observer"],
            oids[names["observer"]],
        )
        for key in ("bootstrap", "runtime", "observer"):
            engine = create_engine(
                source.url.set(username=names[key], password=PASSWORD),
                poolclass=NullPool,
                hide_parameters=True,
                connect_args={
                    "connect_timeout": 5,
                    "options": "-csearch_path=public -clock_timeout=4000 -cstatement_timeout=15000",
                },
            )
            engines.append(engine)
        with source.connect() as db:
            acl = step.identity_step.schema_acl(db, identity)
            affected_acl = step.affected_acl(db, identity)
            assert (
                db.scalar(
                    text(
                        "SELECT count(*) FROM pg_namespace n, LATERAL aclexplode(n.nspacl) a "
                        "WHERE n.nspname='public' AND a.grantee=:bootstrap "
                        "AND a.privilege_type='CREATE' AND a.is_grantable"
                    ),
                    {"bootstrap": identity.bootstrap_oid},
                )
                == 1
            )
            assert (
                db.scalar(
                    text("SELECT has_table_privilege(:name,'public.users','SELECT')"),
                    {"name": names["bootstrap"]},
                )
                is False
            )
        yield SimpleNamespace(
            admin=source,
            bootstrap=engines[0],
            runtime=engines[1],
            observer=engines[2],
            identity=identity,
            names=names,
            acl=acl,
            affected_acl=affected_acl,
        )
    finally:
        for engine in engines:
            engine.dispose()
        if created:
            with source.begin() as db:
                assert db.scalar(text("SELECT current_database()")) == source.url.database
                assert re.fullmatch(r"app25_[a-f0-9]{32}", source.url.database)
                # This schema is inside the exact per-test disposable database.
                # Never REASSIGN/DROP objects in the shared service database.
                db.exec_driver_sql("DROP SCHEMA public CASCADE")
                for name in reversed(created):
                    oid = db.scalar(
                        text("SELECT oid FROM pg_roles WHERE rolname=:name"), {"name": name}
                    )
                    if oid is None:
                        continue  # A failed setup transaction may already have rolled back CREATE ROLE.
                    assert oid == created_oids[name], "Do not remove a replacement role"
                    db.exec_driver_sql(f'DROP OWNED BY "{name}"')
                    db.exec_driver_sql(f'DROP ROLE "{name}"')


def snapshot(engine, *, purpose=False):
    with engine.connect() as db:
        result = {}
        for name in (
            "users",
            "resumes",
            "evidence_items",
            "payment_orders",
            "entitlement_ledger",
            "model_cost_liabilities",
            "password_reset_tokens",
        ):
            projection = (
                "to_jsonb(r)-'purpose'"
                if purpose and name == "password_reset_tokens"
                else "to_jsonb(r)"
            )
            result[name] = (
                db.execute(text(f'SELECT {projection} FROM public."{name}" r ORDER BY id'))
                .scalars()
                .all()
            )
        return result


def assert_roles_and_acls(owned):
    with owned.admin.connect() as db:
        assert step.identity_step.schema_acl(db, owned.identity) == owned.acl
        assert step.affected_acl(db, owned.identity) == owned.affected_acl
        assert (
            db.scalar(
                text("SELECT rolcanlogin FROM pg_roles WHERE rolname=:name"),
                {"name": owned.names["migrator"]},
            )
            is False
        )
        assert (
            db.scalar(
                text("SELECT has_schema_privilege(:name,'public','CREATE')"),
                {"name": owned.names["migrator"]},
            )
            is False
        )
        assert (
            db.scalar(
                text("SELECT reserved_cost_micros FROM model_cost_liabilities WHERE id='m26-held'")
            )
            == 12345
        )


def test_native26_supplied_bootstrap_transaction_preserves_data_catalog_acl_and_holds(owned26):
    owned = owned26
    before = snapshot(owned.admin)
    commits, ddl_roles = [], []

    def commit(_db):
        commits.append(True)

    def observe(db, _cursor, statement, _parameters, _context, _many):
        if statement.startswith('CREATE FUNCTION "public".evidence_writer_fence'):
            ddl_roles.append(
                tuple(
                    db.execute(
                        text(
                            "SELECT current_user,session_user,"
                            "current_setting('transaction_isolation')"
                        )
                    ).one()
                )
            )

    event.listen(owned.bootstrap, "commit", commit)
    event.listen(owned.bootstrap, "before_cursor_execute", observe)
    try:
        with owned.bootstrap.connect() as db:
            with db.begin():
                result = step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
                assert db.in_transaction() and commits == [] and result["committed"] is False
        assert commits == [True]
    finally:
        event.remove(owned.bootstrap, "commit", commit)
        event.remove(owned.bootstrap, "before_cursor_execute", observe)
    assert ddl_roles == [(owned.names["migrator"], owned.names["bootstrap"], "read committed")]
    assert snapshot(owned.admin, purpose=True) == before
    with owned.observer.connect() as db, db.begin():
        db.exec_driver_sql("SET TRANSACTION READ ONLY")
        assert step.contract.observe(db)
        step.contract.verify_writer_permissions(
            db, runtime=owned.names["runtime"], migrator=owned.names["migrator"]
        )
    assert_roles_and_acls(owned)


def test_native26_failure_after_real_ddl_rolls_back_the_fence_grant_and_original_rows(owned26):
    owned = owned26
    before = snapshot(owned.admin)
    stopped = []
    error = RuntimeError("Synthetic owned stop after actual purpose-column DDL")

    def stop(db, _cursor, statement, _parameters, _context, _many):
        if "ALTER TABLE public.password_reset_tokens ADD COLUMN purpose" in statement:
            assert (
                db.scalar(
                    text("SELECT has_schema_privilege(:name,'public','CREATE')"),
                    {"name": owned.names["migrator"]},
                )
                is True
            )
            assert (
                db.scalar(
                    text("SELECT to_regprocedure('public.evidence_writer_fence()') IS NOT NULL")
                )
                is True
            )
            stopped.append(True)
            raise error

    event.listen(owned.bootstrap, "after_cursor_execute", stop)
    try:
        with pytest.raises(RuntimeError) as caught, owned.bootstrap.begin() as db:
            step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
        assert caught.value is error and stopped == [True]
    finally:
        event.remove(owned.bootstrap, "after_cursor_execute", stop)
    assert snapshot(owned.admin) == before
    with owned.admin.connect() as db:
        step.previous.verify_shape(db, owned.identity.namespace_oid)
        assert (
            db.scalar(text("SELECT to_regprocedure('public.evidence_writer_fence()') IS NULL"))
            is True
        )
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE "
                    "table_schema='public' AND table_name='password_reset_tokens' AND column_name='purpose'"
                )
            )
            == 0
        )
    assert_roles_and_acls(owned)


def test_native26_post_lock_cohort_includes_commit_between_first_select_and_lock(owned26):
    owned = owned26
    first_select, released, committed = Event(), Event(), Event()
    with owned.admin.connect() as db:
        before_digest = step.affected_digest(db, owned.identity, after=False)

    def writer():
        assert released.wait(10)
        with owned.runtime.begin() as db:
            db.execute(
                text(
                    "INSERT INTO evidence_items(id,user_id,resume_id,category,title,evidence_text,metrics,skills,"
                    "provenance,source_ref,approval_state,confidence,created_at,updated_at) SELECT "
                    "'evd_late',user_id,resume_id,category,title,'Actual admitted writer committed before AX lock.',"
                    "metrics,skills,provenance,source_ref,approval_state,confidence,created_at,updated_at "
                    "FROM evidence_items WHERE id='evd_bridge'"
                )
            )
        committed.set()

    def after(_db, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("SELECT d.oid,d.datdba"):
            first_select.set()

    def before(db, _cursor, statement, _parameters, _context, _many):
        if statement == 'LOCK TABLE "public"."evidence_items" IN ACCESS EXCLUSIVE MODE':
            assert first_select.is_set() and not released.is_set()
            assert db.scalar(text("SHOW transaction_isolation")) == "read committed"
            released.set()
            assert committed.wait(10)

    event.listen(owned.bootstrap, "after_cursor_execute", after)
    event.listen(owned.bootstrap, "before_cursor_execute", before)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(writer)
            with owned.bootstrap.begin() as db:
                result = step.upgrade_transaction(db, owned.identity, root=ROOT, flags=FLAGS)
            future.result(timeout=10)
    finally:
        event.remove(owned.bootstrap, "after_cursor_execute", after)
        event.remove(owned.bootstrap, "before_cursor_execute", before)
    with owned.admin.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM evidence_items WHERE id='evd_late'")) == 1
        assert result["affected_sha256"] == step.affected_digest(db, owned.identity, after=True)
        assert result["affected_sha256"] != before_digest
    assert_roles_and_acls(owned)
