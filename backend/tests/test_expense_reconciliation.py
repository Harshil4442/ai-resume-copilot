"""Exact report-function tests on owned Core tables; no application imports."""
from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

STAMP = datetime(2026, 10, 12, 12, tzinfo=UTC)
OLD = STAMP.replace(month=9, day=20)


def report_function():
    path = Path(__file__).resolve().parents[1] / "scripts/audit_expense_policy.py"
    tree = ast.parse(path.read_bytes())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "ledger_report"]
    assert len(selected) == 1
    scope = {"datetime": datetime, "UTC": UTC}
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), "exec"), scope)
    return scope["ledger_report"]


def tables():
    metadata = MetaData()
    Table("payment_orders", metadata,
          Column("id", Integer, primary_key=True), Column("currency", String(3)),
          Column("gross_amount_minor", Integer), Column("refunded_amount_minor", Integer),
          Column("provider_fee_amount_minor", Integer), Column("customer_tax_amount_minor", Integer),
          Column("paid_at", DateTime()))
    Table("payment_refunds", metadata,
          Column("id", Integer, primary_key=True), Column("order_id", Integer),
          Column("currency", String(3)), Column("amount_minor", Integer), Column("status", String),
          Column("processed_at", DateTime()))
    Table("model_cost_liabilities", metadata,
          Column("id", Integer, primary_key=True), Column("cost_state", String),
          Column("reserved_cost_micros", Integer), Column("settled_cost_micros", Integer),
          Column("created_at", DateTime()))
    Table("service_credit_reservations", metadata,
          Column("id", Integer, primary_key=True), Column("operation", String),
          Column("unit_price", Integer), Column("reserved_amount", Integer),
          Column("committed_amount", Integer), Column("released_amount", Integer), Column("state", String),
          Column("created_at", DateTime(timezone=True)), Column("settled_at", DateTime(timezone=True)))
    return metadata


@pytest.fixture
def context():
    engine = create_engine("sqlite://")
    metadata = tables()
    metadata.create_all(engine)
    try:
        yield engine, metadata
    finally:
        engine.dispose()


def read(engine, stamp=STAMP):
    with Session(engine) as db:
        return report_function()(db, now=stamp)


def test_refunds_follow_processed_month_not_purchase_month(context):
    engine, metadata = context
    with engine.begin() as db:
        db.execute(metadata.tables["payment_orders"].insert(), [
            {"id": 1, "currency": "INR", "gross_amount_minor": 64900, "refunded_amount_minor": 500, "paid_at": OLD},
            {"id": 2, "currency": "INR", "gross_amount_minor": 109900, "refunded_amount_minor": 999, "paid_at": STAMP - timedelta(days=1)},
        ])
        db.execute(metadata.tables["payment_refunds"].insert(), [
            {"id": 1, "order_id": 1, "currency": "INR", "amount_minor": 500, "status": "processed", "processed_at": STAMP - timedelta(days=1)},
            {"id": 2, "order_id": 2, "currency": "INR", "amount_minor": 999, "status": "processed", "processed_at": STAMP + timedelta(days=1)},
            {"id": 3, "order_id": 1, "currency": "INR", "amount_minor": 70, "status": "pending", "processed_at": STAMP - timedelta(days=1)},
        ])
    result = read(engine)
    assert result["captured_gross_minor"] == 109900
    assert result["refunded_minor"] == 500 and result["processed_refund_count"] == 1
    assert result["orders_with_unknown_provider_fee"] == result["orders_with_unknown_customer_tax"] == 1
    assert result["actual_fixed_expenses_minor"] is None and result["reconciliation_required"] is True


def test_utc_start_and_exclusive_cutoff_and_currency_do_not_mix(context):
    engine, metadata = context
    start = STAMP.replace(day=1, hour=0)
    with engine.begin() as db:
        db.execute(metadata.tables["payment_orders"].insert(), [
            {"id": 1, "currency": "INR", "gross_amount_minor": 10, "paid_at": start},
            {"id": 2, "currency": "INR", "gross_amount_minor": 20, "paid_at": STAMP},
            {"id": 3, "currency": "INR", "gross_amount_minor": 40, "paid_at": start - timedelta(microseconds=1)},
            {"id": 4, "currency": "USD", "gross_amount_minor": 80, "paid_at": start},
        ])
        db.execute(metadata.tables["payment_refunds"].insert(), [
            {"id": 1, "currency": "INR", "amount_minor": 5, "status": "processed", "processed_at": start},
            {"id": 2, "currency": "INR", "amount_minor": 6, "status": "processed", "processed_at": STAMP},
            {"id": 3, "currency": "USD", "amount_minor": 7, "status": "processed", "processed_at": start},
            {"id": 4, "currency": "INR", "amount_minor": 8, "status": "processed", "processed_at": None},
        ])
    result = read(engine, STAMP.astimezone(timezone(timedelta(hours=5, minutes=30))))
    assert result["period_start"] == start.isoformat() and result["as_of"] == STAMP.isoformat()
    assert result["captured_gross_minor"] == 10 and result["refunded_minor"] == 5
    assert result["excluded_other_currency_capture_count"] == result["excluded_other_currency_refund_count"] == 1
    assert result["processed_refunds_without_timestamp"] == 1


def test_search_application_quotes_and_prior_unknown_holds_stay_separate(context):
    engine, metadata = context
    with engine.begin() as db:
        db.execute(metadata.tables["service_credit_reservations"].insert(), [
            {"id": 1, "operation": "job_search", "unit_price": 1, "reserved_amount": 20, "committed_amount": 14, "released_amount": 6, "state": "settled", "created_at": OLD, "settled_at": STAMP - timedelta(days=1)},
            {"id": 2, "operation": "job_application", "unit_price": 5, "reserved_amount": 15, "committed_amount": 10, "released_amount": 5, "state": "settled", "created_at": OLD, "settled_at": STAMP - timedelta(days=1)},
            {"id": 3, "operation": "job_application", "unit_price": 20, "reserved_amount": 40, "committed_amount": 40, "released_amount": 0, "state": "settled", "created_at": STAMP - timedelta(days=1), "settled_at": STAMP - timedelta(hours=1)},
            {"id": 4, "operation": "job_application", "unit_price": 5, "reserved_amount": 25, "committed_amount": 0, "released_amount": 0, "state": "reserved", "created_at": OLD, "settled_at": None},
        ])
        db.execute(metadata.tables["model_cost_liabilities"].insert(), [
            {"id": 1, "cost_state": "outcome_unknown", "reserved_cost_micros": 1267, "settled_cost_micros": None, "created_at": OLD},
            {"id": 2, "cost_state": "settled", "reserved_cost_micros": 100, "settled_cost_micros": 17, "created_at": STAMP - timedelta(days=1)},
        ])
    result = read(engine)
    assert result["service_usage"]["job_search"] == {"settled_credits": 14, "released_credits": 6, "pending_or_unknown_held_credits": 0, "held_operation_count": 0, "settled_operation_count": 1, "charged_quantity": 14}
    assert result["service_usage"]["job_application"] == {"settled_credits": 50, "released_credits": 5, "pending_or_unknown_held_credits": 25, "held_operation_count": 1, "settled_operation_count": 2, "charged_quantity": 4}
    assert result["model_unresolved_held_micros"] == 1267 and result["model_known_settled_micros"] == 17


def test_service_reservations_remain_frozen_until_settlement_before_cutoff(context):
    engine, metadata = context
    with engine.begin() as db:
        db.execute(metadata.tables["service_credit_reservations"].insert(), [
            {"id": 1, "operation": "job_search", "unit_price": 1, "reserved_amount": 20, "committed_amount": 14, "released_amount": 6, "state": "settled", "created_at": OLD, "settled_at": STAMP + timedelta(days=1)},
            {"id": 2, "operation": "job_application", "unit_price": 5, "reserved_amount": 25, "committed_amount": 15, "released_amount": 10, "state": "settled", "created_at": OLD, "settled_at": STAMP},
            {"id": 3, "operation": "job_application", "unit_price": 20, "reserved_amount": 40, "committed_amount": 20, "released_amount": 20, "state": "settled", "created_at": STAMP - timedelta(days=1), "settled_at": STAMP + timedelta(seconds=1)},
            {"id": 4, "operation": "job_search", "unit_price": 1, "reserved_amount": 10, "committed_amount": 7, "released_amount": 3, "state": "settled", "created_at": OLD, "settled_at": STAMP - timedelta(seconds=1)},
            {"id": 5, "operation": "job_search", "unit_price": 1, "reserved_amount": 30, "committed_amount": 0, "released_amount": 0, "state": "reserved", "created_at": STAMP, "settled_at": None},
            {"id": 6, "operation": "job_application", "unit_price": 20, "reserved_amount": 80, "committed_amount": 0, "released_amount": 0, "state": "reserved", "created_at": STAMP + timedelta(seconds=1), "settled_at": None},
        ])
    result = read(engine)
    assert result["service_usage"]["job_search"] == {"settled_credits": 7, "released_credits": 3, "pending_or_unknown_held_credits": 20, "held_operation_count": 1, "settled_operation_count": 1, "charged_quantity": 7}
    assert result["service_usage"]["job_application"] == {"settled_credits": 0, "released_credits": 0, "pending_or_unknown_held_credits": 65, "held_operation_count": 2, "settled_operation_count": 0, "charged_quantity": 0}


def test_missing_current_ledger_is_an_error_not_zero(context):
    engine, _ = context
    with engine.begin() as db:
        db.execute(text("DROP TABLE service_credit_reservations"))
    with pytest.raises(SQLAlchemyError):
        read(engine)


def test_existing_transaction_and_naive_cutoff_refuse_without_reporting(context):
    engine, _ = context
    with Session(engine) as db:
        db.execute(text("SELECT 1"))
        with pytest.raises(ValueError, match="fresh read-only session"):
            report_function()(db, now=STAMP)
    with Session(engine) as db, pytest.raises(ValueError, match="aware UTC cutoff"):
        report_function()(db, now=STAMP.replace(tzinfo=None))


def test_empty_current_tables_are_valid_but_unknown_expenses_remain_unknown(context):
    result = read(context[0])
    assert result["captured_gross_minor"] == result["refunded_minor"] == 0
    assert result["service_usage"]["job_application"]["pending_or_unknown_held_credits"] == 0
    assert result["actual_fixed_expenses_minor"] is None
    assert result["profitability"].startswith("not determined")
