"""Owned actual PostgreSQL migration, immutable mapping and downgrade retention."""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker
from test_employer_admissions_postgres import _migrate
from test_employer_admissions_postgres import pg_engine as pg_engine
from test_monetary_cutover_preflight_postgres import context as context

from app.models import CandidateLifetimeHistory, CandidatePasswordAccount, User


def test_candidate_schema_0011_is_additive_and_never_backfills_legacy_users(pg_engine):
    _migrate(pg_engine, "20261009_0010")
    with pg_engine.begin() as db:
        db.execute(text("INSERT INTO users(id,email,password_hash,ai_credits,job_service_credits) VALUES(91,'legacy@example.com','synthetic',50,0)"))
    _migrate(pg_engine, "head")
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261009_0011"
        assert db.scalar(text("SELECT COUNT(*) FROM candidate_password_accounts")) == 0
        assert db.scalar(text("SELECT COUNT(*) FROM candidate_lifetime_history")) == 0
        assert db.scalar(text("SELECT email FROM users WHERE id=91")) == "legacy@example.com"
    _migrate(pg_engine, "20261009_0010", direction="downgrade")
    assert "candidate_password_accounts" not in inspect(pg_engine).get_table_names()


@pytest.mark.parametrize("field", ["subject_uuid", "account_binding_id", "principal_sha256", "auth_generation"])
def test_sql_native_projection_cannot_change_owner_or_reduce_generation(pg_engine, field):
    _migrate(pg_engine, "head")
    sessions = sessionmaker(bind=pg_engine)
    with sessions() as db:
        db.add(User(id=1, email="candidate@example.com", password_hash="synthetic"))
        db.flush()
        db.add(CandidatePasswordAccount(user_id=1, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
            principal_sha256="a" * 64, credential_sha256="b" * 64, auth_generation=2, state="ACTIVE"))
        db.commit()
    with sessions() as db:
        row = db.get(CandidatePasswordAccount, 1)
        setattr(row, field, 1 if field == "auth_generation" else str(uuid4()))
        with pytest.raises(DBAPIError):
            db.commit()
        db.rollback()


def test_deleted_sql_account_keeps_schema_use_history_and_refuses_downgrade(pg_engine):
    _migrate(pg_engine, "head")
    sessions = sessionmaker(bind=pg_engine)
    with sessions() as db:
        db.add(User(id=1, email="candidate@example.com", password_hash="synthetic"))
        db.add(CandidateLifetimeHistory(registration_id=str(uuid4())))
        db.flush()
        db.add(CandidatePasswordAccount(user_id=1, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
            principal_sha256="a" * 64, credential_sha256="b" * 64, auth_generation=1, state="ACTIVE"))
        db.commit()
    with pg_engine.begin() as db:
        db.execute(text("DELETE FROM users WHERE id=1"))
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT COUNT(*) FROM candidate_password_accounts")) == 0
        assert db.scalar(text("SELECT COUNT(*) FROM candidate_lifetime_history")) == 1
    with pytest.raises(RuntimeError, match="Cannot discard candidate lifetime projections"):
        _migrate(pg_engine, "20261009_0010", direction="downgrade")
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261009_0011"


def test_monetary_preflight_honestly_refuses_unreviewed_schema_0011(context):
    from test_monetary_cutover_preflight_postgres import report

    from scripts import monetary_cutover_preflight as preflight
    _migrate(context[0], "head")
    with pytest.raises(preflight.Denied, match="database_schema_unsupported"):
        report(context, expected_database_schema="20261009_0010")
