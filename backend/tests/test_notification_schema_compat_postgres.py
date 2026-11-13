"""Owned PostgreSQL runtime compatibility, not full catalog/native qualification.

The predecessor fixture uses current ORM tables except the new projection and
the exact existing evidence fence. It executes the real additive27 migration;
its explicit marker is a runtime-boundary fixture, not a whole Alembic rehearsal.
Native privacy observations are controlled read-only ports, never provider IO.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from test_mail_budget_postgres import owned_pg_url as owned_pg_url

from app.database import Base
from app.domains.candidate_accounts import account_erasure, email_erasure
from app.domains.career.evidence_fence_definition import install
from app.domains.notification_consent.models import NotificationPreference
from app.domains.notification_consent.projection import export_preference
from app.domains.notification_consent.schema_compat import (
    FAILURE,
    PREDECESSOR,
    SUCCESSOR,
    projection_available,
)
from app.domains.notifications import mail_budget
from app.domains.notifications import privacy_erasure as privacy_mail
from app.domains.recovery.store import GuardUnavailable
from app.models import AdminAuditEvent, CandidatePasswordAccount, NotificationOutbox, User
from app.routers.auth import export_account

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
EMAIL = "schema-compat-owner@example.invalid"


@pytest.fixture
def engine(owned_pg_url):
    admin = create_engine(owned_pg_url, isolation_level="AUTOCOMMIT")
    name = "notification_compat_" + uuid4().hex
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{name}"'))
    current = create_engine(
        admin.url.set(database=name),
        hide_parameters=True,
        connect_args={"options": "-clock_timeout=5000 -cstatement_timeout=10000 -ctimezone=UTC"},
    )
    try:
        yield current
    finally:
        current.dispose()
        with admin.connect() as connection:
            connection.execute(text(f'DROP DATABASE "{name}"'))
        admin.dispose()


def marker(connection, version=PREDECESSOR):
    connection.execute(text("CREATE TABLE alembic_version(version_num VARCHAR(32) PRIMARY KEY)"))
    connection.execute(
        text("INSERT INTO alembic_version(version_num) VALUES (:version)"), {"version": version}
    )


def migration27(connection):
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261105_0027_notification_preferences.py"
    )
    spec = importlib.util.spec_from_file_location("owned_notification27_compat", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with Operations.context(MigrationContext.configure(connection)):
        module.upgrade()
    connection.execute(
        text("UPDATE alembic_version SET version_num=:version"), {"version": SUCCESSOR}
    )


def minimal(connection):
    User.__table__.create(connection)
    marker(connection)


def predecessor(connection):
    Base.metadata.create_all(
        connection,
        tables=[
            table
            for table in Base.metadata.sorted_tables
            if table.name != "notification_preferences"
        ],
    )
    marker(connection)
    install(connection, connection.scalar(text("SELECT current_schema()")))


def preference(uid, *, enabled=False):
    return NotificationPreference(
        user_id=uid,
        subject_uuid=str(uuid4()),
        account_binding_id=str(uuid4()),
        principal_sha256="a" * 64,
        native_generation=1,
        native_head_sha256="b" * 64,
        recipient_sha256="c" * 64,
        policy_version="weekly-digest-v1",
        enabled=enabled,
        next_due_at=NOW if enabled else None,
        operation_id=str(uuid4()),
        updated_at=NOW,
    )


def test_real_additive27_migration_changes_only_explicit_projection_availability(engine):
    with engine.begin() as connection:
        minimal(connection)
    with Session(engine) as db:
        assert projection_available(db) is False
    with engine.begin() as connection:
        migration27(connection)
    with Session(engine) as db:
        assert projection_available(db) is True
        assert projection_available(db, namespace="public") is True
        with pytest.raises(GuardUnavailable, match="^" + FAILURE + "$"):
            projection_available(db, namespace="another_namespace")


def test_owned_nonpublic_current_namespace_uses_its_own_marker_and_exact_table(engine):
    name = "digest_pipeline_" + uuid4().hex
    with engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{name}"'))
        connection.execute(text(f'SET LOCAL search_path="{name}"'))
        minimal(connection)
        migration27(connection)
        with Session(bind=connection) as db:
            assert projection_available(db) is True
            with pytest.raises(GuardUnavailable, match="^" + FAILURE + "$"):
                projection_available(db, namespace="public")


@pytest.mark.parametrize(
    "damage",
    [
        "unknown",
        "multiple",
        "empty",
        "missing_marker",
        "marker_view",
        "26_with_projection",
        "27_without_projection",
        "view",
        "unlogged",
        "rls",
        "force_rls",
        "partition",
        "inheritance",
    ],
)
def test_unsupported_marker_or_relation_shape_never_becomes_a_skip_or_available_cache(
    engine, damage
):
    with engine.begin() as connection:
        minimal(connection)
        if damage not in {
            "unknown",
            "multiple",
            "empty",
            "missing_marker",
            "marker_view",
            "27_without_projection",
        }:
            migration27(connection)
        statements = {
            "unknown": "UPDATE alembic_version SET version_num='20261106_0028'",
            "multiple": f"INSERT INTO alembic_version VALUES ('{SUCCESSOR}')",
            "empty": "DELETE FROM alembic_version",
            "missing_marker": "DROP TABLE alembic_version",
            "26_with_projection": f"UPDATE alembic_version SET version_num='{PREDECESSOR}'",
            "27_without_projection": f"UPDATE alembic_version SET version_num='{SUCCESSOR}'",
            "unlogged": "ALTER TABLE notification_preferences SET UNLOGGED",
            "rls": "ALTER TABLE notification_preferences ENABLE ROW LEVEL SECURITY",
            "force_rls": "ALTER TABLE notification_preferences FORCE ROW LEVEL SECURITY",
        }
        if damage in statements:
            connection.execute(text(statements[damage]))
        elif damage == "marker_view":
            connection.execute(text("DROP TABLE alembic_version"))
            connection.execute(
                text(
                    f"CREATE VIEW alembic_version AS SELECT '{PREDECESSOR}'::varchar AS version_num"
                )
            )
        else:
            connection.execute(text("DROP TABLE notification_preferences"))
            if damage == "view":
                connection.execute(
                    text("CREATE VIEW notification_preferences AS SELECT 1 AS user_id")
                )
            elif damage == "partition":
                connection.execute(
                    text("CREATE TABLE pref_parent(user_id INTEGER) PARTITION BY LIST(user_id)")
                )
                connection.execute(
                    text(
                        "CREATE TABLE notification_preferences PARTITION OF pref_parent FOR VALUES IN (1)"
                    )
                )
            else:
                connection.execute(text("CREATE TABLE pref_parent(user_id INTEGER)"))
                connection.execute(
                    text("CREATE TABLE notification_preferences() INHERITS(pref_parent)")
                )
    with Session(engine) as db:
        with pytest.raises(GuardUnavailable, match="^" + FAILURE + "$"):
            projection_available(db)


@pytest.mark.parametrize("successor", [False, True])
def test_temporary_shadow_cannot_replace_current_namespace_projection_proof(engine, successor):
    with engine.begin() as connection:
        minimal(connection)
        if successor:
            migration27(connection)
    with Session(engine) as db:
        db.execute(text("CREATE TEMP TABLE notification_preferences(user_id INTEGER)"))
        with pytest.raises(GuardUnavailable, match="^" + FAILURE + "$"):
            projection_available(db)


def test_schema26_existing_account_export_and_erasure_do_not_touch_an_absent_projection(engine):
    with engine.begin() as connection:
        predecessor(connection)
    statements = []

    def observed(_conn, _cursor, statement, _parameters, _context, _many):
        if (
            "FROM notification_preferences" in statement
            or "INTO notification_preferences" in statement
        ):
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", observed)
    with Session(engine) as db:
        owner = User(id=1, email=EMAIL, password_hash="synthetic-old-hash")
        db.add(owner)
        db.commit()
        assert export_preference(db, 1) is None
        response = export_account(db=db, current_user=owner)
        assert b'"notification_preferences":null' in response.body
        account_erasure.erase_account_sql(db, 1)
        db.commit()
        assert account_erasure.erased_owner_rows_absent(db, 1)
    assert statements == []


def test_schema27_export_and_erasure_include_only_exact_owner_projection(engine):
    with engine.begin() as connection:
        predecessor(connection)
        migration27(connection)
    with Session(engine) as db:
        db.add_all(
            [
                User(id=1, email=EMAIL, password_hash="synthetic"),
                User(id=2, email="other@example.invalid", password_hash="synthetic-other"),
            ]
        )
        db.flush()
        db.add_all([preference(1), preference(2)])
        db.commit()
        exported = export_preference(db, 1)
        assert exported["projection_only"] is True and exported["consent_generation"] == 1
        assert "native_head_sha256" not in exported
        account_erasure.erase_account_sql(db, 1)
        db.commit()
        assert account_erasure.erased_owner_rows_absent(db, 1)
        assert db.get(User, 2) is not None and db.get(NotificationPreference, 2) is not None


@pytest.fixture
def privacy_token(engine, monkeypatch):
    with engine.begin() as connection:
        predecessor(connection)
    with Session(engine) as db:
        owner = User(id=1, email=EMAIL, password_hash="synthetic-stored-hash", credential_epoch=7)
        db.add(owner)
        db.flush()
        db.add(
            CandidatePasswordAccount(
                user_id=1,
                subject_uuid=str(uuid4()),
                account_binding_id=str(uuid4()),
                principal_sha256="a" * 64,
                credential_sha256=email_erasure.digest(owner.password_hash),
                auth_generation=2,
                state="DELETE_DENIED",
            )
        )
        db.commit()
    monkeypatch.setattr(privacy_mail, "configured", lambda: True)
    monkeypatch.setattr(privacy_mail, "utcnow", lambda: NOW)
    monkeypatch.setattr(mail_budget, "utcnow", lambda: NOW)
    with Session(engine, autoflush=False) as db:
        identity = email_erasure.request_erasure(
            db, EMAIL, now=NOW, read_native=lambda *_: "b" * 64
        )
        item = db.get(NotificationOutbox, identity)
        token = privacy_mail.message(item)[1].split("#token=", 1)[1].split("\n", 1)[0]
    return token


@pytest.mark.parametrize("migrated", [False, True])
def test_inflight_schema26_privacy_token_completes_on_exact26_or27_with_original_audit(
    privacy_token, engine, migrated
):
    token = privacy_token
    if migrated:
        with engine.begin() as connection:
            migration27(connection)
        with Session(engine) as db:
            db.add(preference(1, enabled=True))
            db.commit()
    with Session(engine, autoflush=False) as db:
        email_erasure.confirm_erasure(db, token, True, now=NOW, read_native=lambda *_: "b" * 64)
        assert account_erasure.erased_owner_rows_absent(db, 1)
        row = db.query(AdminAuditEvent).filter_by(action=email_erasure.ACTION).one()
        assert row.after_state["version"] == "privacy-erasure-confirmation-schema26-v1"
        assert row.action == "privacy_erasure_completed_schema26_v1"
        email_erasure.confirm_erasure(
            db,
            token,
            True,
            now=NOW,
            read_native=lambda *_: pytest.fail("Original completed privacy intent is read-only"),
        )


@pytest.mark.parametrize("committed", [False, True])
def test_inflight_privacy_unknown_commit_after27_uses_same_token_and_completion(
    privacy_token, engine, committed
):
    with engine.begin() as connection:
        migration27(connection)
    with Session(engine, autoflush=False) as db:
        original = db.commit

        def unknown():
            if committed:
                original()
            raise RuntimeError("Synthetic SQL acknowledgment loss")

        db.commit = unknown
        with pytest.raises(email_erasure.ErasureUnavailable):
            email_erasure.confirm_erasure(
                db, privacy_token, True, now=NOW, read_native=lambda *_: "b" * 64
            )
    with Session(engine, autoflush=False) as db:
        assert (db.get(User, 1) is None) is committed
        email_erasure.confirm_erasure(
            db,
            privacy_token,
            True,
            now=NOW,
            read_native=(
                lambda *_: pytest.fail("Committed original outcome needs no new native read")
            )
            if committed
            else (lambda *_: "b" * 64),
        )
        assert db.query(AdminAuditEvent).filter_by(action=email_erasure.ACTION).count() == 1
        assert account_erasure.erased_owner_rows_absent(db, 1)


def test_privacy27_still_requires_exact_canonical_evidence_fence(engine):
    with engine.begin() as connection:
        predecessor(connection)
        migration27(connection)
        connection.execute(text("DROP TRIGGER evidence_writer_fence ON evidence_items"))
    with Session(engine) as db:
        assert projection_available(db) is True
        with pytest.raises(email_erasure.ErasureUnavailable):
            email_erasure._schema26(db)


def test_sqlite_keeps_its_existing_local_metadata_behavior_without_marker():
    local = create_engine("sqlite:///:memory:")
    try:
        with Session(local) as db:
            assert projection_available(db) is True
    finally:
        local.dispose()
