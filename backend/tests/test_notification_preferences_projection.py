"""Synthetic native head values exercise SQL projection only, not ACK authority."""

from __future__ import annotations

import hashlib
import importlib.util
import socket
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.candidate_accounts.account_erasure import (
    erase_account_sql,
    erased_owner_rows_absent,
)
from backend.app.domains.notification_consent import projection
from backend.app.domains.notification_consent.models import NotificationPreference
from backend.app.domains.recovery.gcp_partitioned_contracts import DigestConsentHead, ScopedSubject
from backend.app.domains.recovery.store import GuardDenied
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)
EMAIL = "owner@example.invalid"
PASSWORD = "synthetic-encoded-hash"
SUBJECT = ScopedSubject(
    candidate_id=1,
    subject_uuid=UUID("11111111-1111-4111-8111-111111111111"),
    account_binding_id=UUID("22222222-2222-4222-8222-222222222222"),
    principal_sha256="a" * 64,
    auth_generation=1,
    credential_sha256=hashlib.sha256(PASSWORD.encode()).hexdigest(),
    enrollment_operation=uuid4(),
)


@pytest.fixture
def db(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("SQL projection controls forbid native/provider network IO")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as session:
        session.add_all(
            [
                core.User(id=1, email=EMAIL, password_hash=PASSWORD),
                core.User(id=2, email="other@example.invalid", password_hash="other"),
            ]
        )
        session.add(
            core.CandidatePasswordAccount(
                user_id=1,
                subject_uuid=str(SUBJECT.subject_uuid),
                account_binding_id=str(SUBJECT.account_binding_id),
                principal_sha256=SUBJECT.principal_sha256,
                credential_sha256=SUBJECT.credential_sha256,
                auth_generation=1,
                state="ACTIVE",
            )
        )
        session.commit()
        yield session
    engine.dispose()


def head(generation=1, enabled=True, **updates):
    value = dict(
        subject=SUBJECT,
        generation=generation,
        enabled=enabled,
        email_sha256=hashlib.sha256(EMAIL.encode()).hexdigest(),
        policy_version="weekly-v1",
        operation_id=uuid4(),
    )
    value.update(updates)
    return DigestConsentHead(**value)


def snapshot(db):
    result = projection.snapshot_owner(db, 1)
    db.rollback()  # Required before the caller's independent native operation.
    return result


def apply(db, value, *, now=NOW):
    revision, email = snapshot(db)
    return projection.apply_projection(db, revision, email, value, now=now)


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def test_original_head_projection_is_explicit_and_caller_owns_commit(db):
    assert db.query(NotificationPreference).count() == 0
    value = head()
    row = apply(db, value)
    assert row in db.new and db.query(NotificationPreference).count() == 0
    assert row.enabled and row.native_generation == 1
    assert row.native_head_sha256 == projection.head_sha256(value)
    assert row.recipient_sha256 == value.email_sha256
    assert row.next_due_at == datetime(2026, 11, 9, tzinfo=UTC)
    db.rollback()
    assert db.query(NotificationPreference).count() == 0
    apply(db, value)
    db.commit()
    assert db.query(NotificationPreference).count() == 1
    assert projection.projection_matches(db.get(NotificationPreference, 1), value)


@pytest.mark.parametrize("state", ["PENDING", "DELETE_DENIED", "deleted"])
def test_snapshot_refuses_unenrolled_denied_or_deleted_owner(db, state):
    if state == "deleted":
        db.query(core.User).filter_by(id=1).delete()
    else:
        db.query(core.CandidatePasswordAccount).filter_by(user_id=1).update({"state": state})
    db.commit()
    with pytest.raises(GuardDenied):
        projection.snapshot_owner(db, 1)


def test_snapshot_refuses_missing_mapping_or_inconsistent_password_hash(db):
    db.query(core.User).filter_by(id=1).update({"password_hash": "changed-without-projection"})
    db.commit()
    with pytest.raises(GuardDenied):
        projection.snapshot_owner(db, 1)
    db.rollback()
    db.query(core.CandidatePasswordAccount).filter_by(user_id=1).delete()
    db.commit()
    with pytest.raises(GuardDenied):
        projection.snapshot_owner(db, 1)


@pytest.mark.parametrize("kind", ["mailbox", "credentials", "closed"])
def test_sql_owner_change_during_native_io_cannot_store_old_ack(db, kind):
    revision, email = snapshot(db)
    if kind == "mailbox":
        db.query(core.User).filter_by(id=1).update({"email": "changed@example.invalid"})
    elif kind == "credentials":
        db.query(core.User).filter_by(id=1).update({"password_hash": "new-encoded-hash"})
        db.query(core.CandidatePasswordAccount).filter_by(user_id=1).update(
            {
                "auth_generation": 2,
                "credential_sha256": hashlib.sha256(b"new-encoded-hash").hexdigest(),
            }
        )
    else:
        db.query(core.CandidatePasswordAccount).filter_by(user_id=1).update(
            {"state": "DELETE_DENIED"}
        )
    db.commit()
    with pytest.raises(GuardDenied):
        projection.apply_projection(db, revision, email, head(), now=NOW)
    assert db.query(NotificationPreference).count() == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"email_sha256": "f" * 64},
        {"subject": SUBJECT.model_copy(update={"candidate_id": 2})},
        {"subject": SUBJECT.model_copy(update={"account_binding_id": uuid4()})},
        {"subject": SUBJECT.model_copy(update={"auth_generation": 2})},
    ],
)
def test_foreign_or_wrong_original_head_is_not_a_sql_projection(db, changes):
    with pytest.raises(GuardDenied):
        apply(db, head(**changes))
    assert db.query(NotificationPreference).count() == 0


def test_monotonic_generation_same_generation_conflict_and_idempotent_due(db):
    first = head()
    apply(db, first)
    db.commit()
    row = db.get(NotificationPreference, 1)
    row.next_due_at = NOW + timedelta(days=1)
    db.commit()
    original_due, original_updated = utc(row.next_due_at), utc(row.updated_at)
    again = apply(db, first, now=NOW + timedelta(hours=1))
    db.commit()
    assert utc(again.next_due_at) == original_due and utc(again.updated_at) == original_updated
    with pytest.raises(GuardDenied):
        apply(db, head(generation=1, enabled=False))
    db.rollback()
    second = head(generation=2, enabled=False)
    apply(db, second)
    db.commit()
    assert (
        not db.get(NotificationPreference, 1).enabled
        and db.get(NotificationPreference, 1).next_due_at is None
    )
    with pytest.raises(GuardDenied):
        apply(db, first)
    db.rollback()
    third = head(generation=3)
    apply(db, third)
    db.commit()
    assert (
        db.get(NotificationPreference, 1).enabled
        and db.get(NotificationPreference, 1).native_generation == 3
    )


def test_restored_or_reused_numeric_owner_projection_cannot_switch_lifetime(db):
    apply(db, head())
    db.commit()
    db.query(NotificationPreference).filter_by(user_id=1).update({"subject_uuid": str(uuid4())})
    db.commit()
    with pytest.raises(GuardDenied):
        apply(db, head(generation=2))


@pytest.mark.parametrize(
    "clock",
    [
        datetime(2026, 11, 2, tzinfo=UTC),
        datetime(2026, 11, 8, 23, 59, 59, tzinfo=UTC),
        NOW.astimezone(timezone(timedelta(hours=5, minutes=30))),
    ],
)
def test_weekly_due_is_next_monday_utc_within_seven_days(clock):
    due = projection.next_weekly_due(clock)
    assert due.weekday() == 0 and due.hour == due.minute == due.second == due.microsecond == 0
    assert due.tzinfo == UTC and timedelta(0) < due - clock <= timedelta(days=7)


def test_projection_lock_order_is_owner_then_user_account_preference(db, monkeypatch):
    from sqlalchemy.dialects import postgresql

    observed = []
    monkeypatch.setattr(
        projection, "lock_application_set", lambda session, owner: observed.append("owner")
    )

    def observe(state):
        sql = str(state.statement.compile(dialect=postgresql.dialect()))
        if "FOR UPDATE" in sql:
            table = (
                "notification_preferences"
                if "FROM notification_preferences" in sql
                else "candidate_password_accounts"
                if "FROM candidate_password_accounts" in sql
                else "users"
            )
            observed.append(table)

    revision, email = snapshot(db)
    observed.clear()  # Inspect the second lock acquisition after independent native IO.
    event.listen(db, "do_orm_execute", observe)
    try:
        projection.apply_projection(db, revision, email, head(), now=NOW)
    finally:
        event.remove(db, "do_orm_execute", observe)
    assert observed == ["owner", "users", "candidate_password_accounts", "notification_preferences"]


def test_export_exposes_only_owner_projection_metadata_without_raw_head(db):
    value = head()
    apply(db, value)
    db.commit()
    exported = projection.export_preference(db, 1)
    assert (
        exported is not None and exported["projection_only"] is True and exported["enabled"] is True
    )
    assert set(exported) == {
        "projection_only",
        "enabled",
        "consent_generation",
        "policy_version",
        "next_due_at",
        "updated_at",
    }
    assert exported["next_due_at"].tzinfo == UTC
    assert projection.export_preference(db, 2) is None
    from backend.app.routers.auth import export_account

    response = export_account(db=db, current_user=db.get(core.User, 1))
    import json

    payload = json.loads(response.body)
    assert payload["notification_preferences"]["projection_only"] is True
    assert "native_head_sha256" not in payload["notification_preferences"]


def test_erasure_removes_projection_and_orphan_audit_detects_restored_row(db):
    apply(db, head())
    db.commit()
    erase_account_sql(db, 1)
    db.commit()
    assert db.get(NotificationPreference, 1) is None and erased_owner_rows_absent(db, 1)
    # Only an owned SQL corruption fixture: FK-off legacy restore, no native grants.
    db.add(
        NotificationPreference(
            user_id=1,
            subject_uuid=str(SUBJECT.subject_uuid),
            account_binding_id=str(SUBJECT.account_binding_id),
            principal_sha256="a" * 64,
            native_generation=1,
            native_head_sha256="b" * 64,
            recipient_sha256="c" * 64,
            policy_version="weekly-v1",
            enabled=False,
            next_due_at=None,
            operation_id=str(uuid4()),
            updated_at=NOW,
        )
    )
    db.commit()
    assert not erased_owner_rows_absent(db, 1)


def migration():
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/20261105_0027_notification_preferences.py"
    )
    spec = importlib.util.spec_from_file_location("owned_notification_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_adds_no_opt_in_or_backfill_and_has_cascade_index_and_refusing_downgrade():
    engine = create_engine("sqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    core.User.__table__.create(engine)
    module = migration()
    assert module.revision == "20261105_0027" and module.down_revision == "20261031_0026"
    with engine.begin() as connection:
        connection.execute(core.User.__table__.insert().values(id=1, email=EMAIL))
        with Operations.context(MigrationContext.configure(connection)):
            module.upgrade()
            assert connection.execute(select(NotificationPreference)).all() == []
            columns = {
                row["name"]: row
                for row in inspect(connection).get_columns("notification_preferences")
            }
            assert columns["enabled"]["default"] is None
            assert (
                inspect(connection).get_foreign_keys("notification_preferences")[0]["options"][
                    "ondelete"
                ]
                == "CASCADE"
            )
            assert any(
                row["name"] == "ix_notification_preferences_due"
                for row in inspect(connection).get_indexes("notification_preferences")
            )
            with pytest.raises(RuntimeError, match="native-retirement"):
                module.downgrade()
            connection.execute(
                NotificationPreference.__table__.insert().values(
                    user_id=1,
                    subject_uuid=str(SUBJECT.subject_uuid),
                    account_binding_id=str(SUBJECT.account_binding_id),
                    principal_sha256="a" * 64,
                    native_generation=1,
                    native_head_sha256="b" * 64,
                    recipient_sha256="c" * 64,
                    policy_version="weekly-v1",
                    enabled=False,
                    next_due_at=None,
                    operation_id=str(uuid4()),
                    updated_at=NOW,
                )
            )
            with pytest.raises(RuntimeError, match="native-retirement"):
                module.downgrade()
            connection.execute(core.User.__table__.delete().where(core.User.id == 1))
            assert connection.execute(select(NotificationPreference)).all() == []
    engine.dispose()
