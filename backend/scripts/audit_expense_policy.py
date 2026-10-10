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


def ledger_report(db) -> dict:
    from sqlalchemy import case, func, or_, text

    from app.models import ModelCostLiability, PaymentOrder

    now = datetime.now(UTC)
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SET TRANSACTION READ ONLY"))
    receipts = (
        db.query(
            func.coalesce(func.sum(PaymentOrder.gross_amount_minor), 0),
            func.coalesce(func.sum(PaymentOrder.refunded_amount_minor), 0),
            func.coalesce(func.sum(PaymentOrder.provider_fee_amount_minor), 0),
            func.coalesce(
                func.sum(case((PaymentOrder.provider_fee_amount_minor.is_(None), 1), else_=0)), 0
            ),
            func.coalesce(
                func.sum(case((PaymentOrder.customer_tax_amount_minor.is_(None), 1), else_=0)), 0
            ),
        )
        .filter(PaymentOrder.paid_at >= start)
        .one()
    )
    unresolved = ModelCostLiability.cost_state.notin_(["settled", "overrun"])
    held = func.coalesce(ModelCostLiability.reserved_cost_micros, 0)
    settled = func.coalesce(ModelCostLiability.settled_cost_micros, 0)
    maximum = (
        func.max(held, settled)
        if db.get_bind().dialect.name == "sqlite"
        else func.greatest(held, settled)
    )
    costs = (
        db.query(
            func.coalesce(func.sum(case((unresolved, 0), else_=settled)), 0),
            func.coalesce(func.sum(case((unresolved, maximum), else_=0)), 0),
            func.coalesce(
                func.sum(case((ModelCostLiability.cost_state == "overrun", 1), else_=0)), 0
            ),
        )
        .filter(or_(ModelCostLiability.created_at >= start, unresolved))
        .one()
    )
    return {
        "period_start": start.isoformat(),
        "captured_gross_minor": int(receipts[0]),
        "refunded_minor": int(receipts[1]),
        "provider_fee_known_minor": int(receipts[2]),
        "orders_with_unknown_provider_fee": int(receipts[3]),
        "orders_with_unknown_customer_tax": int(receipts[4]),
        "actual_fixed_expenses_minor": None,
        "model_known_settled_micros": int(costs[0]),
        "model_unresolved_held_micros": int(costs[1]),
        "model_overrun_count": int(costs[2]),
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
                "Ledger reconciliation unavailable; no account configuration or data was changed."
            ) from None
        finally:
            engine.dispose()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
