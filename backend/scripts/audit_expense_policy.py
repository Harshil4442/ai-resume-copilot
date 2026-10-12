"""Offline reviewed-expense audit and optional read-only ledger reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.billing.cost_policy import (  # noqa: E402
    ExpensePolicy,
    audit,
    ceil,
    net_receipts,
    usd_minor,
)


def forecasts(policy: ExpensePolicy) -> list[dict]:
    fixed = policy.monthly_platform_operations_minor + policy.monthly_marketing_operations_minor
    buffer = Fraction(10_000 + policy.contingency_bps, 10_000)
    per_ai = (
        usd_minor(policy, policy.analysis_unit_model_ceiling_micros)
        + policy.analysis_overhead_minor_per_unit
    )
    rows = []
    for pack in policy.packs:
        net = net_receipts(policy, pack.gross_minor - pack.max_discount_minor)
        variable = (pack.service_credits + pack.max_bonus_service_credits) * max(
            Fraction(policy.search_cost_minor, policy.service_search_credits),
            Fraction(policy.application_cost_minor, policy.service_apply_credits),
        ) + (pack.analysis_units + pack.max_bonus_analysis_units) * per_ai
        buffered = ceil(variable * buffer)
        fixed_buffered = ceil(fixed * buffer)
        contribution = net - buffered
        rows.append(
            {
                "sku": pack.sku,
                "break_even_paid_packs": ceil(Fraction(fixed_buffered, contribution))
                if contribution > 0
                else None,
                "monthly_forecasts": [
                    {
                        "paid_packs": count,
                        "estimated_after_buffer_contribution_minor": count * contribution
                        - fixed_buffered,
                    }
                    for count in (10, 50, 100)
                ],
                "basis": "estimated full mixed use; actual expenses and sales unknown",
            }
        )
    return rows


def ledger_report(db, *, now: datetime | None = None) -> dict:
    """UTC cash-period aggregates from a fresh session, without candidate bodies.

    Capture and refund dates are independent. Reserved service credits include
    pending and possible-sent unknown work; this report cannot classify either
    as a completed application or release them. Frozen reservations remain held
    until settlement before the cutoff. Model figures retain their
    attempt-created-month/current-snapshot basis.
    """
    from sqlalchemy import DateTime, bindparam, text

    stamp = now or datetime.now(UTC)
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ValueError("Ledger report requires an aware UTC cutoff")
    stamp = stamp.astimezone(UTC)
    start = stamp.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if db.in_transaction():
        raise ValueError("Ledger report requires a fresh read-only session")
    dialect = db.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise ValueError("Ledger report supports PostgreSQL or SQLite")
    if dialect == "postgresql":
        db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
        db.execute(text("SET LOCAL statement_timeout = '5000ms'"))
    bounds = {"start": start, "cutoff": stamp, "currency": "INR"}
    def query(sql):
        return db.execute(text(sql).bindparams(
            bindparam("start", type_=DateTime(timezone=True)),
            bindparam("cutoff", type_=DateTime(timezone=True)),
        ), bounds)
    receipts = query(
        "SELECT coalesce(sum(gross_amount_minor),0), "
        "coalesce(sum(provider_fee_amount_minor),0), "
        "coalesce(sum(CASE WHEN provider_fee_amount_minor IS NULL THEN 1 ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN customer_tax_amount_minor IS NULL THEN 1 ELSE 0 END),0) "
        "FROM payment_orders WHERE paid_at >= :start AND paid_at < :cutoff AND currency=:currency"
    ).one()
    refunds = query(
        "SELECT coalesce(sum(amount_minor),0),count(*) FROM payment_refunds "
        "WHERE status='processed' AND processed_at >= :start AND processed_at < :cutoff "
        "AND currency=:currency"
    ).one()
    exceptions = query(
        "SELECT "
        "(SELECT count(*) FROM payment_refunds WHERE status='processed' AND processed_at IS NULL), "
        "(SELECT count(*) FROM payment_orders WHERE paid_at >= :start AND paid_at < :cutoff AND currency<>:currency), "
        "(SELECT count(*) FROM payment_refunds WHERE status='processed' AND processed_at >= :start AND processed_at < :cutoff AND currency<>:currency)"
    ).one()
    maximum = "max" if dialect == "sqlite" else "greatest"
    costs = query(
        "SELECT coalesce(sum(CASE WHEN cost_state IN ('settled','overrun') "
        "THEN coalesce(settled_cost_micros,0) ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN cost_state NOT IN ('settled','overrun') THEN "
        + maximum + "(coalesce(reserved_cost_micros,0),coalesce(settled_cost_micros,0)) ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN cost_state='overrun' THEN 1 ELSE 0 END),0) "
        "FROM model_cost_liabilities WHERE created_at < :cutoff "
        "AND (created_at >= :start OR cost_state NOT IN ('settled','overrun'))"
    ).one()
    services = query(
        "SELECT operation, "
        "coalesce(sum(CASE WHEN state='settled' AND settled_at >= :start AND settled_at < :cutoff THEN committed_amount ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN state='settled' AND settled_at >= :start AND settled_at < :cutoff THEN released_amount ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN state='reserved' OR (state='settled' AND settled_at >= :cutoff) THEN reserved_amount ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN state='reserved' OR (state='settled' AND settled_at >= :cutoff) THEN 1 ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN state='settled' AND settled_at >= :start AND settled_at < :cutoff THEN 1 ELSE 0 END),0), "
        "coalesce(sum(CASE WHEN state='settled' AND settled_at >= :start AND settled_at < :cutoff AND unit_price > 0 "
        "THEN committed_amount / unit_price ELSE 0 END),0) "
        "FROM service_credit_reservations WHERE created_at < :cutoff "
        "AND operation IN ('job_search','job_application') "
        "AND (state='reserved' OR (state='settled' AND settled_at >= :start)) GROUP BY operation"
    ).all()
    usage = {operation: {"settled_credits": 0, "released_credits": 0,
                        "pending_or_unknown_held_credits": 0, "held_operation_count": 0,
                        "settled_operation_count": 0, "charged_quantity": 0}
             for operation in ("job_search", "job_application")}
    for operation, *amounts in services:
        usage[operation] = dict(zip(usage[operation], map(int, amounts), strict=True))
    return {
        "period_start": start.isoformat(),
        "as_of": stamp.isoformat(),
        "calendar_basis": "UTC; inclusive month start, exclusive as-of cutoff",
        "currency": "INR",
        "captured_gross_minor": int(receipts[0]),
        "refunded_minor": int(refunds[0]),
        "processed_refund_count": int(refunds[1]),
        "refund_basis": "processed refund ledger, independent of capture month",
        "processed_refunds_without_timestamp": int(exceptions[0]),
        "excluded_other_currency_capture_count": int(exceptions[1]),
        "excluded_other_currency_refund_count": int(exceptions[2]),
        "provider_fee_known_minor": int(receipts[1]),
        "orders_with_unknown_provider_fee": int(receipts[2]),
        "orders_with_unknown_customer_tax": int(receipts[3]),
        "actual_fixed_expenses_minor": None,
        "model_known_settled_micros": int(costs[0]),
        "model_unresolved_held_micros": int(costs[1]),
        "model_overrun_count": int(costs[2]),
        "model_cost_basis": "attempt-created month/current snapshot estimates and all prior unresolved holds; not historical as-of costs or invoices",
        "service_usage": usage,
        "service_usage_basis": "settlement-month credits/quantities at stored unit prices; pre-cutoff frozen reservations held until settlement before cutoff",
        "reconciliation_required": True,
        "profitability": "not determined; provider usage estimates and incomplete account expenses are not invoices",
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("policy_file", type=Path)
    parser.add_argument(
        "--ledger",
        action="store_true",
        help="Read DATABASE_URL in a read-only transaction; never creates schema",
    )
    args = parser.parse_args()
    policy = ExpensePolicy.model_validate_json(args.policy_file.read_text())
    result = {
        "review_status": policy.review_status,
        "provenance": policy.provenance,
        "audit": audit(policy),
        "forecasts": forecasts(policy),
    }
    if args.ledger:
        from sqlalchemy import create_engine
        from sqlalchemy.exc import SQLAlchemyError
        from sqlalchemy.orm import Session

        configured = os.getenv("DATABASE_URL")
        if not configured:
            parser.error("DATABASE_URL must be explicitly configured for read-only reconciliation")
        engine = create_engine(configured)
        try:
            with Session(engine) as db:
                result["ledger"] = ledger_report(db)
                db.rollback()
        except SQLAlchemyError:
            raise SystemExit(
                "Ledger reconciliation unavailable; current payment/refund, model-liability and service-reservation tables are required. No account configuration or data was changed."
            ) from None
        finally:
            engine.dispose()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
