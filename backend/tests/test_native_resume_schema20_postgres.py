"""Real PG17 schema20 admission through an owned bootstrap/NOLOGIN owner.

Only the existing CI loopback fixture or a uniquely named disposable schema20
loopback database is admitted. Native OIDs/cluster/roles and every SQL check are
real; this makes no claim about production TLS, provider identity or authority.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from backend.tests import test_browser_schema19_step_postgres as old_fixture
from backend.tests.test_browser_schema19_step_postgres import (
    owned_step,  # noqa: F401 - genuine bootstrap, owner, retained-money and ACL fixture
)
from backend.tests.test_native_resume_schema20_contract import reviewed
from backend.tests.test_rolling_release import FixtureBoundary
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

from alembic import command
from scripts import native_resume_schema20_contract as contract
from scripts import native_resume_schema20_step as step

ROOT = Path(__file__).resolve().parents[2]


def configuration(connection):
    result = Config(str(ROOT / "backend/alembic.ini"))
    result.set_main_option("script_location", str(ROOT / "backend/alembic"))
    result.attributes["connection"] = connection
    return result


@pytest.fixture
def owned20(monkeypatch, request):
    configured = os.getenv("HIREWIZ_TEST_POSTGRES20_URL")
    if configured:
        url = make_url(configured)
        if (
            url.drivername != "postgresql+psycopg"
            or url.host != "127.0.0.1"
            or url.port != 55437
            or re.fullmatch(r"hirewiz_schema20_[a-f0-9]{12}", url.database or "") is None
            or url.username != "postgres"
            or url.query
        ):
            pytest.fail(
                "Schema20 tests accept only the explicitly owned disposable loopback database"
            )
        monkeypatch.setattr(old_fixture, "configured_url", lambda: url)
    # With no schema20 URL, the unchanged fixture admits only the existing
    # exact CI loopback URL, or skips. No transport/identity verifier is mocked.
    owned = request.getfixturevalue("owned_step")
    with owned.bootstrap.begin() as db:
        old_fixture.step.upgrade_transaction(db, owned.identity, root=ROOT)
    with owned.engine.begin() as db:
        db.execute(
            text("INSERT INTO candidate_identity_history(registration_id) VALUES(:id)"),
            {"id": str(uuid4())},
        )
        db.execute(
            text(
                "INSERT INTO candidate_google_enrollment_challenges "
                "(id,user_id,nonce_sha256,principal_sha256,email_sha256,credential_sha256,expires_at_ms,used) "
                "VALUES(:id,1,:sha,:sha,:sha,:sha,1800000000000,false)"
            ),
            {"id": str(uuid4()), "sha": "a" * 64},
        )
        db.execute(
            text(
                "INSERT INTO candidate_google_identities(user_id,principal_sha256,enrollment_proof_sha256,state) "
                "VALUES(1,:principal,:proof,'ACTIVE')"
            ),
            {"principal": "b" * 64, "proof": "c" * 64},
        )
        insert_upload(db, "pdf")
        db.execute(
            text(
                "INSERT INTO resume_upload_cleanups(id,upload_id,next_attempt_at,attempt_count,created_at) "
                "VALUES('owned20_cleanup','owned20_upload',now(),0,now())"
            )
        )
        owned.before_digest20 = step.retained_digest(db, owned.identity)
        owned.before_acl20 = step.schema_acl(db, owned.identity)
    return owned


def insert_upload(db, source_format):
    db.execute(
        text(
            "INSERT INTO resume_uploads "
            "(id,user_id,idempotency_sha256,request_sha256,source_format,media_type,size_bytes,source_sha256,"
            "quarantine_bucket,quarantine_name,clean_bucket,clean_name,enrich_skills,state,expires_at,"
            "upload_grant_expires_at,next_attempt_at,attempt_count,created_at) "
            "VALUES ('owned20_upload',1,:sha,:sha,:format,'application/octet-stream',16,:sha,"
            "'owned-quarantine','owned/source','owned-clean','owned/source',false,'cancelled',now(),now(),now(),0,now())"
        ),
        {"format": source_format, "sha": "d" * 64},
    )


def execute(owned):
    with owned.bootstrap.begin() as db:
        return step.upgrade_transaction(db, owned.identity, root=ROOT)


def assert_unchanged19(owned):
    with owned.engine.connect() as db:
        contract.verify_before_shape(db, owned.identity.namespace_oid, owned.namespace)
        assert step.retained_digest(db, owned.identity) == owned.before_digest20
        assert step.schema_acl(db, owned.identity) == owned.before_acl20
        assert (
            db.scalar(
                text("SELECT rolcanlogin FROM pg_roles WHERE oid=:oid"),
                {"oid": owned.identity.migrator_oid},
            )
            is False
        )
        assert (
            db.scalar(
                text("SELECT has_schema_privilege(:owner,:ns,'CREATE')"),
                {"owner": owned.names["migrator"], "ns": owned.namespace},
            )
            is False
        )
        old_fixture.permissions(owned, db)


def test_real20_transaction_exact_catalog_retains_all_rows_and_acl(owned20):
    owned = owned20
    observations, mutations = [], []

    def capture(db, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().startswith("ALTER TABLE"):
            observations.append(
                tuple(
                    db.execute(
                        text(
                            "SELECT current_user,session_user,current_setting('transaction_isolation'),"
                            "(SELECT rolcanlogin FROM pg_roles WHERE rolname=current_user),"
                            "has_schema_privilege(current_user,current_schema(),'CREATE')"
                        )
                    ).one()
                )
            )
        if sql.lstrip().startswith(("ALTER ", "GRANT ", "REVOKE ", "CREATE ", "DROP ")):
            mutations.append(sql)

    event.listen(owned.bootstrap, "before_cursor_execute", capture)
    try:
        result = execute(owned)
    finally:
        event.remove(owned.bootstrap, "before_cursor_execute", capture)
    assert result["schema"] == contract.SCHEMA
    assert result["retained_sha256"] == owned.before_digest20
    assert result["migration_login_reenabled"] is result["replay_authorized"] is False
    assert observations and all(
        row == (owned.names["migrator"], owned.names["bootstrap"], "serializable", False, False)
        for row in observations
    )
    assert len(mutations) == 2 and all(
        sql.lstrip().startswith("ALTER TABLE resume_uploads ") for sql in mutations
    )
    with owned.engine.connect() as db:
        assert step.retained_digest(db, owned.identity) == owned.before_digest20
        assert step.schema_acl(db, owned.identity) == owned.before_acl20
        old_fixture.permissions(owned, db)
        assert db.scalar(text("SELECT job_service_credits FROM users WHERE id=1")) == 100
        assert (
            db.scalar(
                text(
                    "SELECT gross_amount_minor FROM payment_orders WHERE public_id='schema19_owned_order'"
                )
            )
            == 39900
        )
        assert (
            db.scalar(
                text(
                    "SELECT committed_amount FROM service_credit_reservations WHERE id='schema19_owned_reservation'"
                )
            )
            == 2
        )
        assert (
            db.scalar(text("SELECT review_protocol_version FROM browser_assistance_reviews")) == 3
        )
        assert db.scalar(text("SELECT count(*) FROM candidate_google_identities")) == 1
        assert db.scalar(text("SELECT count(*) FROM resume_upload_cleanups")) == 1
    statements = []
    with owned.observer.connect() as db:
        db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")

        def observe(_db, _cursor, sql, _parameters, _context, _many):
            statements.append(sql)

        event.listen(db, "before_cursor_execute", observe)
        contract.verify_shape(db, owned.identity.namespace_oid, owned.namespace)
        event.remove(db, "before_cursor_execute", observe)
        assert db.scalar(text("SHOW transaction_read_only")) == "on"
    assert len(statements) == 10
    assert all(
        sql.startswith("SELECT ") and ("pg_catalog." in sql or "alembic_version" in sql)
        for sql in statements
    )
    with pytest.raises(contract.Schema20Denied, match="exact_19_required_never_replay"):
        execute(owned)
    with owned.engine.connect() as db:
        assert contract.marker(db, owned.namespace) == contract.SCHEMA
        assert step.retained_digest(db, owned.identity) == owned.before_digest20


def test_outer_rollback_preserves19_rows_constraints_and_acl(owned20):
    owned = owned20
    with owned.bootstrap.connect() as db:
        transaction = db.begin()
        assert step.upgrade_transaction(db, owned.identity, root=ROOT)["schema"] == contract.SCHEMA
        transaction.rollback()
    assert_unchanged19(owned)


def test_actual_ddl_failure_rolls_back_dropped_format_check_and_marker(owned20):
    owned, injected = owned20, []

    def fault(_db, _cursor, sql, parameters, _context, _many):
        if sql.lstrip().startswith(
            "ALTER TABLE resume_uploads ADD CONSTRAINT ck_resume_upload_format"
        ):
            injected.append(True)
            return (
                "ALTER TABLE resume_uploads ADD CONSTRAINT owned_invalid CHECK (owned_missing_column=1)",
                parameters,
            )
        return sql, parameters

    event.listen(owned.bootstrap, "before_cursor_execute", fault, retval=True)
    try:
        with pytest.raises(DBAPIError) as error:
            execute(owned)
        assert error.value.orig.sqlstate == "42703"
    finally:
        event.remove(owned.bootstrap, "before_cursor_execute", fault)
    assert injected == [True]
    assert_unchanged19(owned)


def test_actual_retained_row_change_after_ddl_rolls_back_everything(owned20):
    owned, changed = owned20, []

    def change(db, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().startswith(
            "ALTER TABLE resume_uploads ADD CONSTRAINT ck_resume_upload_format"
        ):
            changed.append(True)
            db.execute(text("UPDATE candidate_google_enrollment_challenges SET used=true"))

    event.listen(owned.bootstrap, "after_cursor_execute", change)
    try:
        with pytest.raises(contract.Schema20Denied, match="retained_history_changed"):
            execute(owned)
    finally:
        event.remove(owned.bootstrap, "after_cursor_execute", change)
    assert changed == [True]
    assert_unchanged19(owned)


@pytest.mark.parametrize("work", ["upload", "cleanup", "lock"])
def test_actual_unfinished_upload_cleanup_or_concurrent_migrator_refuses(owned20, work):
    owned = owned20
    with owned.engine.begin() as blocker:
        if work == "lock":
            blocker.execute(text("SELECT pg_advisory_xact_lock(:lock)"), {"lock": step.LOCK_ID})
        else:
            # Commit a live lease so the separate real bootstrap sees it.
            with owned.engine.begin() as db:
                table = "resume_uploads" if work == "upload" else "resume_upload_cleanups"
                db.execute(
                    text(
                        f"UPDATE {table} SET lease_token='owned-live-lease',lease_until=now()+interval '1 minute'"
                    )
                )
        with pytest.raises(
            contract.Schema20Denied,
            match="migration_already_running"
            if work == "lock"
            else "active_upload_or_cleanup_refused",
        ):
            execute(owned)
    if work != "lock":
        with owned.engine.begin() as db:
            db.execute(text(f"UPDATE {table} SET lease_token=NULL,lease_until=NULL"))
    assert_unchanged19(owned)


@pytest.mark.parametrize("retained", ["native", "detached"])
def test_direct_downgrade_and_operator_guard_refuse_native_or_unclassifiable_cleanup(
    owned20, retained
):
    owned = owned20
    execute(owned)
    with owned.engine.begin() as db:
        if retained == "native":
            db.execute(text("UPDATE resume_uploads SET source_format='texzip'"))
        else:
            db.execute(text("DELETE FROM resume_uploads"))
        before = step.retained_digest(db, owned.identity)
    with owned.engine.begin() as db:
        with pytest.raises(contract.Schema20Denied, match="downgrade_refused"):
            contract.require_schema19_downgrade_safe(db, owned.namespace)
        with pytest.raises(RuntimeError, match="cleanup history"):
            command.downgrade(configuration(db), contract.BEFORE)
        contract.verify_shape(db, owned.identity.namespace_oid, owned.namespace)
        assert step.retained_digest(db, owned.identity) == before
        assert db.scalar(text("SELECT count(*) FROM resume_upload_cleanups")) == 1


def test_proven_pdf_docx_cleanup_allows_non_lossy_downgrade_and_exact_constraint(owned20):
    owned = owned20
    execute(owned)
    for source_format in ("pdf", "docx", "tex", "texzip"):
        with owned.engine.connect() as db, db.begin() as transaction:
            db.execute(
                text("UPDATE resume_uploads SET source_format=:format"), {"format": source_format}
            )
            transaction.rollback()
    with pytest.raises(DBAPIError) as error, owned.engine.begin() as db:
        db.execute(text("UPDATE resume_uploads SET source_format='tex zip'"))
    assert error.value.orig.sqlstate == "23514"

    with owned.engine.begin() as db:
        db.execute(text("UPDATE resume_uploads SET source_format='docx'"))
        before = step.retained_digest(db, owned.identity)
        contract.require_schema19_downgrade_safe(db, owned.namespace)
        command.downgrade(configuration(db), contract.BEFORE)
        contract.verify_before_shape(db, owned.identity.namespace_oid, owned.namespace)
        assert step.retained_digest(db, owned.identity) == before
        assert step.schema_acl(db, owned.identity) == owned.before_acl20
        assert db.scalar(text("SELECT count(*) FROM resume_upload_cleanups")) == 1
    with pytest.raises(DBAPIError) as error, owned.engine.begin() as db:
        db.execute(text("UPDATE resume_uploads SET source_format='tex'"))
    assert error.value.orig.sqlstate == "23514"


def test_real_committed_unknown_ack_reconciles_without_replaying_ddl(
    owned20, tmp_path, monkeypatch
):
    """Only external transport/global gates are synthetic; commit/readback SQL is real."""
    import sqlalchemy

    owned = owned20
    raw = {
        **asdict(FixtureBoundary().plan),
        "schema_contract": contract.CONTRACT,
        "database_schema": contract.BEFORE,
        "prior_sources": [],
    }
    plan = reviewed.load_plan(reviewed.rolling.canonical(raw))
    tmp_path.chmod(0o700)
    journal = reviewed.rolling.Journal(tmp_path, plan)
    boundary = reviewed.NativeSchema20Boundary(ROOT, contract.BEFORE)
    state = {"transactions": 0, "ddl": 0, "global": []}
    native_shape = contract.verify_shape

    # Local SQL identity is fully verified. Only the production connection
    # factory, namespace and cloud pre/post gates are replaced by owned ports.
    monkeypatch.setattr(reviewed, "bootstrap_identity", lambda: owned.identity)
    monkeypatch.setattr(
        reviewed.docker, "bootstrap_password", lambda _deadline: old_fixture.PASSWORD
    )
    monkeypatch.setattr(
        reviewed.NativeSchema20Boundary,
        "verify_global",
        lambda _self, current: state["global"].append(current.database_schema),
    )
    monkeypatch.setattr(
        contract,
        "verify_shape",
        lambda db, oid, namespace="public": native_shape(db, oid, owned.namespace),
    )
    monkeypatch.setattr(
        reviewed, "verify_permissions", lambda db, *_args: old_fixture.permissions(owned, db)
    )

    class Engine:
        def connect(self):
            # Alembic must receive a genuine SQLAlchemy Connection. The sole
            # injected fault is raised after the real second COMMIT succeeds.
            connection = owned.bootstrap.connect()
            native_begin = connection.begin

            @contextmanager
            def begin():
                state["transactions"] += 1
                ordinal = state["transactions"]
                with native_begin():
                    yield
                if ordinal == 2:
                    raise RuntimeError("owned local committed acknowledgement lost")

            monkeypatch.setattr(connection, "begin", begin)
            return connection

        def dispose(self):
            pass

    def capture(_db, _cursor, sql, _parameters, _context, _many):
        if sql.lstrip().startswith(
            "ALTER TABLE resume_uploads ADD CONSTRAINT ck_resume_upload_format"
        ):
            state["ddl"] += 1

    monkeypatch.setattr(sqlalchemy, "create_engine", lambda *args, **kwargs: Engine())
    event.listen(owned.bootstrap, "before_cursor_execute", capture)
    try:
        with pytest.raises(RuntimeError, match="committed acknowledgement lost"):
            reviewed.migration(plan, boundary, journal)
        assert journal.read("schema20-migration.started") is not None
        assert journal.read("schema20-migration.checked") is None
        with pytest.raises(reviewed.rolling.RollingDenied, match="reconcile_only"):
            reviewed.migration(plan, boundary, journal)
        result = reviewed.migration(plan, boundary, journal, reconcile=True)
    finally:
        event.remove(owned.bootstrap, "before_cursor_execute", capture)
    assert result["schema"] == contract.SCHEMA and result["replay_authorized"] is False
    assert state["ddl"] == 1 and state["global"] == [contract.BEFORE, contract.SCHEMA]
    assert journal.read("schema20-migration.checked") is not None
    with owned.engine.connect() as db:
        native_shape(db, owned.identity.namespace_oid, owned.namespace)
        assert step.retained_digest(db, owned.identity) == owned.before_digest20
