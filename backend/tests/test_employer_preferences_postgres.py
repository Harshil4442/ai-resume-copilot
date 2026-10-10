"""Actual local PostgreSQL search alias/transaction and additive migration proof."""
import pytest
from backend.app.domains.employer import models
from backend.tests import test_employer_search_preferences as checks
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_context as pg_context
from backend.tests.test_employer_admissions_postgres import pg_engine as pg_engine
from sqlalchemy import inspect, text
from sqlalchemy.exc import ProgrammingError


def test_postgres_preferences_keep_exact_owner_idempotency_and_credit_provenance(pg_context, monkeypatch):
    checks.test_partial_country_and_salary_search_persists_exact_quote_billing_and_unknown(pg_context, monkeypatch)


def test_postgres_alias_branches_preserve_seniority_through_fts(pg_context):
    checks.test_qualifiers_survive_aliases_and_known_employment_changes_results(pg_context)


def test_actual_migration_0012_round_trip_keeps_old_postings_unknown(pg_context, pg_engine):
    factory, _, _ = pg_context
    with factory() as db:
        assert db.query(models.EmployerPosting).count() == 2
        assert all(row.preference_metadata is None for row in db.query(models.EmployerPosting))
    assert next(column for column in inspect(pg_engine).get_columns("employer_postings") if column["name"] == "preference_metadata")["nullable"]
    _migrate(pg_engine, "20261009_0011", "downgrade")
    assert "preference_metadata" not in {column["name"] for column in inspect(pg_engine).get_columns("employer_postings")}
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM employer_postings")).scalar() == 2
    _migrate(pg_engine, "20261009_0012")
    with factory() as db:
        assert all(row.preference_metadata is None for row in db.query(models.EmployerPosting))
    _migrate(pg_engine, "20261009_0011", "downgrade")
    with pytest.raises(ProgrammingError):  # Deployment must require the new column before schema12 model code.
        with factory() as db:
            db.query(models.EmployerPosting).all()
