"""Exact additive DDL and retained-data rehearsal on isolated local PostgreSQL.

This tests the DDL core, not production TLS/Neon identity or PG17 NOLOGIN roles.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from backend.tests.test_employer_admissions_postgres import _migrate, pg_engine
from sqlalchemy import text

from scripts import unified_credit_schema25_contract as contract
from scripts import unified_credit_schema25_step as step

assert pg_engine
ROOT = Path(__file__).resolve().parents[2]
FLAGS = {name: "false" for name in contract.DISABLED_FLAGS}


def identity(db):
    name = db.scalar(text("SELECT current_schema()"))
    oid = db.scalar(
        text("SELECT oid FROM pg_catalog.pg_namespace WHERE nspname=:name"), {"name": name}
    )
    return name, oid


def test_additive_core_preserves_existing_password_epochs_and_unknown_model_money(pg_engine):
    _migrate(pg_engine, contract.BEFORE)
    now = datetime.now(UTC)
    with pg_engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users(id,email,ai_credits,job_service_credits,credential_epoch) VALUES(42,'synthetic-schema25@example.test',17,13,9)"
            )
        )
        db.execute(
            text(
                "INSERT INTO password_reset_tokens(id,user_id,token_sha256,email_sha256,credential_sha256,legacy_epoch,status,created_at,expires_at) VALUES('synthetic-reset',42,:a,:b,:c,9,'PENDING',:now,:expiry)"
            ),
            {
                "a": "a" * 64,
                "b": "b" * 64,
                "c": "c" * 64,
                "now": now,
                "expiry": now + timedelta(minutes=15),
            },
        )
        db.execute(
            text(
                "INSERT INTO model_cost_liabilities(id,financial_group_id,attempt_number,provider,model,endpoint_key,currency,pricing_quote,input_token_estimate,reserved_cost_micros,cost_state,created_at) VALUES('synthetic-liability','synthetic-group',1,'google','synthetic-model',:endpoint,'USD','{}',100,12345,'outcome_unknown',:now)"
            ),
            {"endpoint": "d" * 64, "now": now},
        )
    with pg_engine.begin() as db:
        name, oid = identity(db)
        before = step.retained_digest(db, oid, name)
        result = step._additive_ddl(db, namespace_oid=oid, namespace=name, root=ROOT, flags=FLAGS)
        assert result["retained_sha256"] == before
        assert result["new_admission_enabled"] is False
    with pg_engine.connect() as db:
        name, oid = identity(db)
        assert step.retained_digest(db, oid, name) == before
        contract.verify_shape(db, oid, name)
        assert tuple(
            db.execute(
                text(
                    "SELECT ai_credits,job_service_credits,credential_epoch FROM users WHERE id=42"
                )
            ).one()
        ) == (17, 13, 9)
        assert tuple(
            db.execute(
                text(
                    "SELECT reserved_cost_micros,settled_cost_micros,cost_state FROM model_cost_liabilities"
                )
            ).one()
        ) == (12345, None, "outcome_unknown")
        assert db.scalar(text("SELECT count(*) FROM password_reset_tokens")) == 1
    with pg_engine.begin() as db, pytest.raises(contract.Schema25Denied, match="never_replay"):
        name, oid = identity(db)
        step._additive_ddl(db, namespace_oid=oid, namespace=name, root=ROOT, flags=FLAGS)


def test_additive_core_rolls_back_with_outer_transaction_and_requires_all_new_flags_off(pg_engine):
    _migrate(pg_engine, contract.BEFORE)
    for key in FLAGS:
        with pg_engine.begin() as db, pytest.raises(contract.Schema25Denied, match="remain_closed"):
            name, oid = identity(db)
            step._additive_ddl(
                db, namespace_oid=oid, namespace=name, root=ROOT, flags=FLAGS | {key: "true"}
            )

    class Rollback(Exception):
        pass

    with pytest.raises(Rollback), pg_engine.begin() as db:
        name, oid = identity(db)
        step._additive_ddl(db, namespace_oid=oid, namespace=name, root=ROOT, flags=FLAGS)
        raise Rollback()
    with pg_engine.connect() as db:
        name, oid = identity(db)
        contract.verify_before_shape(db, oid, name)


def test_catalog_refuses_modified_financial_constraint(pg_engine):
    _migrate(pg_engine, contract.SCHEMA)
    with pg_engine.begin() as db:
        name, oid = identity(db)
        contract.verify_shape(db, oid, name)
        db.execute(text("ALTER TABLE credit_accounts DROP CONSTRAINT ck_credit_account_debt"))
        with pytest.raises(contract.Schema25Denied, match="shape_mismatch"):
            contract.verify_shape(db, oid, name)


def test_catalog_fingerprint_is_identical_in_native_readonly_observer_path(pg_engine):
    _migrate(pg_engine, contract.SCHEMA)
    with pg_engine.connect() as db, db.begin():
        name, oid = identity(db)
        own_path = db.scalar(text("SELECT current_setting('search_path')"))
        expected = contract.catalog_digest(db, oid, name)
        assert db.scalar(text("SELECT current_setting('search_path')")) == own_path
    with pg_engine.connect() as db, db.begin():
        db.exec_driver_sql("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        db.exec_driver_sql("SET LOCAL search_path=pg_catalog")
        assert contract.catalog_digest(db, oid, name) == expected
        contract.verify_shape(db, oid, name)
        assert db.scalar(text("SELECT current_setting('search_path')")) == "pg_catalog"
        assert db.in_transaction()


def test_initial_native_release_refuses_new_credit_activity(pg_engine):
    _migrate(pg_engine, contract.SCHEMA)
    with pg_engine.begin() as db:
        name, _oid = identity(db)
        contract.verify_empty_new_tables(db, name)
        db.execute(text("INSERT INTO users(id,email) VALUES(42,'schema25-activity@example.test')"))
        db.execute(
            text("INSERT INTO credit_accounts(user_id, debt_credits,created_at) VALUES(42,0,now())")
        )
        with pytest.raises(contract.Schema25Denied, match="financial_activity"):
            contract.verify_empty_new_tables(db, name)
