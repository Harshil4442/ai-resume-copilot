"""Read-only schema12/13 metadata and detached financial observation; no cloud writes."""

from __future__ import annotations

import json

import pytest
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_monetary_cutover_preflight_postgres import (
    assert_refused,
    insert_liability,
    report,
    valid_quote,
)
from backend.tests.test_monetary_cutover_schema11_postgres import context as context
from sqlalchemy import event, text

from scripts import monetary_cutover_preflight as preflight


def latest_report(context, version):
    return report(context, expected_database_schema=version, candidate_schema=version)


@pytest.mark.parametrize("version", ["20261009_0012", "20261009_0013"])
def test_actual_extended_schema_is_not_cloud_or_funding_authority(context, version):
    _migrate(context[0], version)
    result = latest_report(context, version)
    assert result["database_gate_passed"] and result["cutover_ready"] is False
    assert result["observed_database"]["schema"] == version
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 0
    assert "read_only_database_observation_is_not_cutover_authority" in result["cutover_ready_reasons"]


@pytest.mark.parametrize(
    "database,candidate",
    [("20261009_0012", "20261009_0010"), ("20261009_0012", "20261009_0011"),
     ("20261009_0013", "20261009_0011"), ("20261009_0013", "20261009_0012")],
)
def test_older_candidate_cannot_adopt_new_schema_marker(context, database, candidate):
    _migrate(context[0], database)
    with pytest.raises(preflight.Denied, match="^database_schema_unsupported$"):
        report(context, expected_database_schema=database, candidate_schema=candidate)


@pytest.mark.parametrize(
    "version,alteration",
    [
        ("20261009_0012", "ALTER TABLE employer_postings DROP COLUMN preference_metadata"),
        ("20261009_0012", "ALTER TABLE employer_postings ALTER COLUMN preference_metadata TYPE jsonb"),
        ("20261009_0012", "ALTER TABLE employer_postings ALTER COLUMN preference_metadata SET NOT NULL"),
        ("20261009_0013", "ALTER TABLE payment_orders DROP COLUMN cost_policy_snapshot"),
        ("20261009_0013", "ALTER TABLE service_credit_reservations DROP COLUMN cost_policy_snapshot"),
        ("20261009_0013", "ALTER TABLE payment_orders ALTER COLUMN cost_policy_snapshot TYPE jsonb"),
        ("20261009_0013", "ALTER TABLE service_credit_reservations ALTER COLUMN cost_policy_snapshot SET NOT NULL"),
    ],
)
def test_marker_without_reviewed_nullable_json_columns_refuses(context, version, alteration):
    engine = context[0]
    _migrate(engine, version)
    with engine.begin() as connection:
        connection.execute(text(alteration))
    with pytest.raises(preflight.Denied, match="^database_extension_shape_mismatch$"):
        latest_report(context, version)


@pytest.mark.parametrize("version", ["20261009_0011", "20261009_0012"])
def test_new_columns_cannot_hide_behind_an_old_marker(context, version):
    engine = context[0]
    _migrate(engine, "20261009_0013")
    with engine.begin() as connection:
        connection.execute(text("UPDATE alembic_version SET version_num=:version"), {"version": version})
    with pytest.raises(preflight.Denied, match="^database_extension_shape_mismatch$"):
        latest_report(context, version)


@pytest.mark.parametrize("state", ["reserved", "outcome_unknown", "usage_unavailable"])
def test_new_schema_retains_historical_unknown_cost_after_erasure(context, state):
    engine = context[0]
    _migrate(engine, "20261009_0013")
    insert_liability(engine, state=state, settled=None)
    result = latest_report(context, "20261009_0013")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["unresolved_count"] == 1
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 1267
    with engine.connect() as connection:
        assert connection.execute(text("SELECT reserved_cost_micros FROM model_cost_liabilities")).scalar_one() == 1267


@pytest.mark.parametrize("funding,pool", [
    (None, None), ("prepaid", "prepaid"), ("promotion", "promotion"),
    ("legacy_premium", "legacy_premium"), ("prepaid", "failed_work"),
])
def test_old_and_complete_new_quotes_keep_independent_cost_math(context, funding, pool):
    engine = context[0]
    _migrate(engine, "20261009_0013")
    quote = valid_quote()
    if funding is not None:
        quote.update(expense_policy_version="estimate-reviewed-v1", expense_funding=funding, expense_pool=pool)
    insert_liability(engine, quote=json.dumps(quote))
    result = latest_report(context, "20261009_0013")
    assert result["database_gate_passed"] and result["cutover_ready"] is False
    assert result["observed_database"]["liabilities"]["settled_cost_micros"] == 17
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 0


@pytest.mark.parametrize("expense", [
    {"expense_policy_version": "reviewed-v1"},
    {"expense_funding": "prepaid"},
    {"expense_policy_version": None, "expense_funding": None, "expense_pool": None},
    {"expense_policy_version": "reviewed-v1", "expense_funding": "prepaid", "expense_pool": "promotion"},
    {"expense_policy_version": "reviewed-v1", "expense_funding": "unlimited", "expense_pool": "unlimited"},
    {"expense_policy_version": True, "expense_funding": "prepaid", "expense_pool": "prepaid"},
])
def test_malformed_expense_quote_cannot_release_a_financial_hold(context, expense):
    engine = context[0]
    _migrate(engine, "20261009_0013")
    insert_liability(engine, quote=json.dumps(valid_quote() | expense))
    result = latest_report(context, "20261009_0013")
    assert_refused(result, "unresolved_obligations")
    assert result["observed_database"]["liabilities"]["held_cost_micros"] == 1267


def test_column_inspection_never_reads_payment_or_preference_values(context):
    engine = context[0]
    _migrate(engine, "20261009_0013")
    statements = []

    def capture(connection, cursor, statement, parameters, execution_context, executemany):
        statements.append(statement)

    observer = engine.cutover_readonly_observer
    event.listen(observer, "before_cursor_execute", capture)
    try:
        result = latest_report(context, "20261009_0013")
    finally:
        event.remove(observer, "before_cursor_execute", capture)
    assert result["database_gate_passed"] and not result["cutover_ready"]
    assert not any("FROM payment_orders" in statement or "FROM employer_postings" in statement for statement in statements)
    assert any("pg_catalog.pg_attribute" in statement for statement in statements)
