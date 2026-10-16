"""Real undeployed19 additive identity schema and detached retention guard."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from test_employer_admissions_postgres import _migrate
from test_employer_admissions_postgres import pg_engine as pg_engine


def test_google_schema19_never_adopts_existing_users_and_roundtrips_when_unused(pg_engine):
    _migrate(pg_engine, "20261015_0018")
    with pg_engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,email,password_hash,ai_credits,job_service_credits) VALUES(91,'synthetic-legacy@example.test','',137,0)"
            )
        )
    _migrate(pg_engine, "20261015_0019")
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM candidate_google_identities")) == 0
        assert db.scalar(text("SELECT count(*) FROM candidate_google_enrollment_challenges")) == 0
        assert db.scalar(text("SELECT count(*) FROM candidate_identity_history")) == 0
        assert db.scalar(text("SELECT ai_credits FROM users WHERE id=91")) == 137
    _migrate(pg_engine, "20261015_0018", direction="downgrade")
    assert "candidate_google_identities" not in inspect(pg_engine).get_table_names()


def test_google_identity_uniqueness_and_detached_history_block_downgrade_after_erasure(pg_engine):
    _migrate(pg_engine, "20261015_0019")
    with pg_engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,email,password_hash,ai_credits,job_service_credits) VALUES(91,'synthetic-one@example.test','',50,0),(92,'synthetic-two@example.test','',50,0)"
            )
        )
        db.execute(
            text(
                "INSERT INTO candidate_google_identities(user_id,principal_sha256,enrollment_proof_sha256,state) VALUES(91,:principal,:proof,'ACTIVE')"
            ),
            {"principal": "a" * 64, "proof": "b" * 64},
        )
        db.execute(
            text(
                "INSERT INTO candidate_google_enrollment_challenges(id,user_id,nonce_sha256,principal_sha256,email_sha256,credential_sha256,expires_at_ms,used) VALUES(:id,91,:digest,:digest,:digest,:digest,1800000000000,true)"
            ),
            {"id": str(uuid4()), "digest": "a" * 64},
        )
        db.execute(
            text("INSERT INTO candidate_identity_history(registration_id) VALUES(:id)"),
            {"id": str(uuid4())},
        )
    with pytest.raises(IntegrityError) as error, pg_engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO candidate_google_identities(user_id,principal_sha256,enrollment_proof_sha256,state) VALUES(92,:principal,:proof,'ACTIVE')"
            ),
            {"principal": "a" * 64, "proof": "c" * 64},
        )
    assert error.value.orig.sqlstate == "23505"
    with pg_engine.begin() as db:
        db.execute(text("DELETE FROM users WHERE id=91"))
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT count(*) FROM candidate_google_identities")) == 0
        assert db.scalar(text("SELECT count(*) FROM candidate_google_enrollment_challenges")) == 0
        assert db.scalar(text("SELECT count(*) FROM candidate_identity_history")) == 1
    with pytest.raises(RuntimeError, match="Protected Google identity history must be retained"):
        _migrate(pg_engine, "20261015_0018", direction="downgrade")
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261015_0019"
    assert "review_protocol_version" in {
        column["name"] for column in inspect(pg_engine).get_columns("browser_assistance_reviews")
    }
