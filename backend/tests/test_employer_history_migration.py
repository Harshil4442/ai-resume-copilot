"""Real SQLite migration checks for retained paid rows and safe downgrade refusal."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


@pytest.fixture
def history_migration():
    source = Path(__file__).resolve().parents[1] / "alembic/versions/20261011_0015_employer_paid_history.py"
    spec = importlib.util.spec_from_file_location("history_migration_under_test", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def legacy_connection(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'paid-history.sqlite'}")
    try:
        with engine.begin() as connection:
            for statement in (
                "CREATE TABLE users(id INTEGER PRIMARY KEY)",
                "CREATE TABLE employer_postings(id VARCHAR(64) PRIMARY KEY)",
                "CREATE TABLE employer_searches(id VARCHAR(64) PRIMARY KEY,user_id INTEGER,items JSON)",
                "CREATE TABLE employer_job_deliveries(id VARCHAR(64) PRIMARY KEY,user_id INTEGER,"
                "search_id VARCHAR(64),posting_id VARCHAR(64),charged_credits INTEGER,created_at DATETIME,"
                "CONSTRAINT uq_original_delivery UNIQUE(user_id,posting_id),"
                "CONSTRAINT fk_original_search FOREIGN KEY(search_id) REFERENCES employer_searches(id))",
                "INSERT INTO users(id) VALUES(1)",
                "INSERT INTO employer_postings(id) VALUES('p1')",
            ):
                connection.execute(text(statement))
            connection.execute(
                text("INSERT INTO employer_searches(id,user_id,items) VALUES('s1',1,:items)"),
                {"items": json.dumps([{"unit_price": 5, "accepted_quote": "original", "text": "Unicode: résumé"}])},
            )
            connection.execute(text(
                "INSERT INTO employer_job_deliveries(id,user_id,search_id,posting_id,charged_credits,created_at) "
                "VALUES('d1',1,'s1','p1',5,'2026-10-10 01:02:03.123456')"
            ))
            yield connection
    finally:
        engine.dispose()


def retained_rows(connection):
    return (
        tuple(connection.execute(text("SELECT id,user_id,items FROM employer_searches")).one()),
        tuple(connection.execute(text(
            "SELECT id,user_id,search_id,posting_id,charged_credits,created_at FROM employer_job_deliveries"
        )).one()),
    )


def test_sqlite_history_migration_preserves_paid_rows_and_existing_constraints(history_migration, legacy_connection):
    connection = legacy_connection
    before = retained_rows(connection)
    with Operations.context(MigrationContext.configure(connection)):
        history_migration.upgrade()
    assert retained_rows(connection) == before
    assert connection.scalar(text("SELECT history_revision FROM employer_searches")) == 1
    assert connection.scalar(text("SELECT COUNT(*) FROM employer_paid_delivery_history")) == 0
    assert {"uq_original_delivery", "uq_employer_delivery_history_identity"} <= {
        row["name"] for row in inspect(connection).get_unique_constraints("employer_job_deliveries")
    }
    assert "fk_original_search" in {
        row["name"] for row in inspect(connection).get_foreign_keys("employer_job_deliveries")
    }
    with Operations.context(MigrationContext.configure(connection)):
        history_migration.downgrade()
    assert retained_rows(connection) == before
    assert "history_revision" not in {
        row["name"] for row in inspect(connection).get_columns("employer_searches")
    }


def test_sqlite_history_migration_refuses_populated_downgrade_without_data_loss(history_migration, legacy_connection):
    connection = legacy_connection
    with Operations.context(MigrationContext.configure(connection)):
        history_migration.upgrade()
    connection.execute(text(
        "INSERT INTO employer_paid_history_progress(user_id,state,created_at,updated_at) "
        "VALUES(1,'pending',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
    ))
    tables = set(inspect(connection).get_table_names())
    rows = retained_rows(connection)
    with pytest.raises(RuntimeError, match="retained paid-delivery history or resumable history work"):
        with Operations.context(MigrationContext.configure(connection)):
            history_migration.downgrade()
    assert set(inspect(connection).get_table_names()) == tables
    assert retained_rows(connection) == rows
    assert connection.scalar(text("SELECT COUNT(*) FROM employer_paid_history_progress")) == 1
