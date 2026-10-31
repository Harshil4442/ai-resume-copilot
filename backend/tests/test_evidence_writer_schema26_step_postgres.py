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
from datetime import UTC, datetime, timedelta
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

from app.domains.browser_assistance.models import BrowserAssistanceDevice, BrowserAssistanceReview
from app.domains.employer.models import (
    EmployerApplication,
    EmployerPosting,
    EmployerSource,
    SealedApplicationArtifact,
)
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
            # Real FK-bound legacy4 row bytes and20-price metadata, not a grant,
            # reservation, paid submission or provider receipt.
            source_row = EmployerSource(
                id="m26-source",
                employer="Synthetic",
                platform="greenhouse",
                board_token="m26",
                careers_url="https://example.invalid/careers",
                verification_url="https://example.invalid/careers",
                verification_note="Synthetic SQL fixture",
            )
            db.add(source_row)
            db.flush()
            posting = EmployerPosting(
                id="m26-posting",
                source_id=source_row.id,
                external_id="m26-only",
                title="Synthetic",
                employer="Synthetic",
                description="Synthetic",
                canonical_url="https://example.invalid/job",
                apply_url="https://example.invalid/job",
                content_sha256="a" * 64,
            )
            db.add(posting)
            db.flush()
            artifact = SealedApplicationArtifact(
                id="m26-artifact",
                user_id=1,
                resume_id=1,
                sha256="b" * 64,
                filename="synthetic.txt",
                media_type="text/plain",
                size_bytes=1,
                content=b"x",
            )
            db.add(artifact)
            db.flush()
            application = EmployerApplication(
                id="m26-application",
                user_id=1,
                posting_id=posting.id,
                idempotency_key="m26-legacy4",
                input_fingerprint="c" * 64,
                resume_id=1,
                resume_choice="original",
                artifact_id=artifact.id,
                form={},
                answers={},
                consents={},
                package_digest="d" * 64,
                job_content_sha256="a" * 64,
                credit_cost=20,
                charged_credits=0,
                pricing_snapshot={"synthetic_legacy_protocol": 4, "credit_cost": 20},
            )
            db.add(application)
            db.flush()
            device = BrowserAssistanceDevice(
                id=str(uuid4()),
                user_id=1,
                extension_id="a" * 32,
                public_key={"synthetic_sql_fixture": True},
                key_sha256="e" * 64,
                state="active",
                created_at=now,
                pairing_expires_at=now + timedelta(minutes=5),
            )
            db.add(device)
            db.flush()
            db.add(
                BrowserAssistanceReview(
                    id=str(uuid4()),
                    user_id=1,
                    device_id=device.id,
                    application_id=application.id,
                    package_digest="d" * 64,
                    artifact_sha256="b" * 64,
                    context_digest="f" * 64,
                    review_digest="1" * 64,
                    payload={"synthetic_legacy_metadata": 20},
                    state="draft",
                    next_index=0,
                    review_protocol_version=4,
                    created_at=now,
                    expires_at=now + timedelta(minutes=5),
                )
            )
            db.flush()
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
            "browser_assistance_reviews",
            "employer_applications",
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


def prepared_sql_guard(owned, *, refuse=False):
    """Owned SQL qualification only; synthetic native port is NOT provider proof.

    Production cannot inject this port through NativeMaintenanceDdlGuard's
    constructor. The test bypasses only its native preflight, then executes its
    real one-use SQL freshness and final-retirement logic inside actual PG.
    """
    sys.path.insert(0, str(ROOT / "infra/gcp"))
    import schema26_app_rolling_v3 as release26
    import schema26_maintenance_guard as maintenance26

    baseline = release26.ClosedApiBaseline(
        *(["a" * 64] * 4),
        str(uuid4()),
        2,
        "synthetic-closed",
        "ai-resume-parser-m25-aaaaaaaaaaaa-close",
        str(uuid4()),
        "b" * 64,
        tuple((flag, "true") for flag in sorted(release26.historical.PILOT_FLAGS)),
    )
    revision = maintenance26.metadata.API + "/revisions/" + baseline.closed_revision
    traffic = [
        {
            "type": "TRAFFIC_TARGET_ALLOCATION_TYPE_REVISION",
            "revision": baseline.closed_revision,
            "percent": 100,
        }
    ]
    observation = {
        "service": {
            "name": maintenance26.metadata.API,
            "uid": baseline.api_service_uid,
            "generation": "2",
            "observedGeneration": "2",
            "etag": baseline.api_etag,
            "reconciling": False,
            "terminalCondition": {"type": "Ready", "state": "CONDITION_SUCCEEDED"},
            "latestCreatedRevision": revision,
            "latestReadyRevision": revision,
            "traffic": traffic,
            "trafficStatuses": traffic,
        },
        "revisions": [
            {
                "name": revision,
                "uid": baseline.closed_revision_uid,
                "service": "ai-resume-parser",
                "conditions": [{"type": "Ready", "state": "CONDITION_SUCCEEDED"}],
            }
        ],
        "elapsed_ms": 0,
        "resource_gets": 3,
        "physical_resource_gets": 3,
        "resource_write_sends": 0,
        "maintenance_admission_proved": False,
    }

    class NativePort:
        reads = 0
        closed = False

        def observe(self):
            self.reads += 1
            assert self.reads == 1
            if refuse:
                raise maintenance26.MaintenanceDenied("synthetic_final_metadata_refusal")
            return observation

        def close(self):
            self.closed = True

    result = object.__new__(maintenance26.NativeMaintenanceDdlGuard)
    result.started, result.closed, result.prepared = False, False, True
    result.baseline, result.owners, result.final_reader = baseline, (), NativePort()
    with owned.observer.connect() as db:
        rows, absence = maintenance26.sql_projection(db, ())
    result.sql_sha256 = maintenance26.fingerprint({"rows": rows, "absence": absence})
    result.retired_sha256 = maintenance26.retirement_fingerprint(observation, baseline)
    return result


def test_native26_supplied_bootstrap_transaction_preserves_data_catalog_acl_and_holds(owned26):
    owned = owned26
    before = snapshot(owned.admin)
    maintenance_guard = prepared_sql_guard(owned)
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
                result = step.upgrade_transaction(
                    db, owned.identity, root=ROOT, flags=FLAGS, maintenance_guard=maintenance_guard
                )
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
    assert (
        maintenance_guard.final_reader.reads == 1
        and maintenance_guard.started
        and maintenance_guard.closed
    )
    assert_roles_and_acls(owned)


def test_native26_failure_after_real_ddl_rolls_back_the_fence_grant_and_original_rows(owned26):
    owned = owned26
    before = snapshot(owned.admin)
    maintenance_guard = prepared_sql_guard(owned)
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
            step.upgrade_transaction(
                db, owned.identity, root=ROOT, flags=FLAGS, maintenance_guard=maintenance_guard
            )
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
    assert (
        maintenance_guard.final_reader.reads == 1
        and maintenance_guard.started
        and maintenance_guard.closed
    )
    assert_roles_and_acls(owned)


def test_native26_post_lock_cohort_includes_commit_between_first_select_and_lock(owned26):
    owned = owned26
    first_select, released, committed = Event(), Event(), Event()
    maintenance_guard = prepared_sql_guard(owned)
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
                result = step.upgrade_transaction(
                    db, owned.identity, root=ROOT, flags=FLAGS, maintenance_guard=maintenance_guard
                )
            future.result(timeout=10)
    finally:
        event.remove(owned.bootstrap, "after_cursor_execute", after)
        event.remove(owned.bootstrap, "before_cursor_execute", before)
    with owned.admin.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM evidence_items WHERE id='evd_late'")) == 1
        assert result["affected_sha256"] == step.affected_digest(db, owned.identity, after=True)
        assert result["affected_sha256"] != before_digest
    assert (
        maintenance_guard.final_reader.reads == 1
        and maintenance_guard.started
        and maintenance_guard.closed
    )
    assert_roles_and_acls(owned)


def test_native26_final_seam_refusal_rolls_back_without_migration_ddl(owned26):
    owned = owned26
    before = snapshot(owned.admin)
    maintenance_guard = prepared_sql_guard(owned, refuse=True)
    migration_ddl = []

    def record(_db, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().startswith(("CREATE ", "ALTER ", "DROP ")):
            migration_ddl.append(statement.split()[0])

    event.listen(owned.bootstrap, "before_cursor_execute", record)
    try:
        with (
            pytest.raises(RuntimeError, match="synthetic_final_metadata_refusal"),
            owned.bootstrap.begin() as db,
        ):
            step.upgrade_transaction(
                db, owned.identity, root=ROOT, flags=FLAGS, maintenance_guard=maintenance_guard
            )
    finally:
        event.remove(owned.bootstrap, "before_cursor_execute", record)
    assert migration_ddl == []
    assert (
        maintenance_guard.final_reader.reads == 1
        and maintenance_guard.started
        and maintenance_guard.closed
    )
    assert snapshot(owned.admin) == before
    with owned.observer.connect() as db:
        assert (
            db.scalar(text("SELECT version_num FROM public.alembic_version"))
            == step.contract.BEFORE
        )
        assert db.scalar(text("SELECT to_regprocedure('public.evidence_writer_fence()')")) is None
        assert (
            db.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns WHERE table_schema='public' "
                    "AND table_name='password_reset_tokens' AND column_name='purpose'"
                )
            )
            == 0
        )
        assert (
            db.scalar(text("SELECT review_protocol_version FROM browser_assistance_reviews")) == 4
        )
        assert (
            db.scalar(
                text("SELECT credit_cost FROM employer_applications WHERE id='m26-application'")
            )
            == 20
        )
    assert_roles_and_acls(owned)
