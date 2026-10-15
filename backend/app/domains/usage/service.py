from __future__ import annotations

from sqlalchemy.orm import Session

from ... import models
from ..common import public_id, utcnow
from ..entitlements import lock_entitlement_owner


class InsufficientUnitsError(ValueError):
    def __init__(self, *, balance: int, required: int):
        self.balance = balance
        self.required = required
        super().__init__(f"This operation requires {required} analysis units; {balance} remain.")


def _event_exists(db: Session, user_id: int, idempotency_key: str) -> bool:
    return (
        db.query(models.UsageEvent.id)
        .filter(
            models.UsageEvent.user_id == user_id,
            models.UsageEvent.idempotency_key == idempotency_key,
        )
        .first()
        is not None
    )


def _append_event(
    db: Session,
    *,
    user_id: int,
    run_id: str,
    event_type: str,
    amount: int,
    balance_after: int,
    idempotency_key: str,
    reason: str,
) -> models.UsageEvent:
    event = models.UsageEvent(
        id=public_id("use"),
        user_id=user_id,
        analysis_run_id=run_id,
        event_type=event_type,
        amount=amount,
        balance_after=balance_after,
        idempotency_key=idempotency_key,
        source_type="analysis_run",
        source_id=run_id,
        actor="system",
        reason=reason,
        created_at=utcnow(),
    )
    db.add(event)
    return event


def reserve_run_usage(
    db: Session,
    *,
    user_id: int,
    run: models.AnalysisRun,
    units: int,
) -> None:
    if units < 0:
        raise ValueError("Reserved units cannot be negative")
    if run.usage_state != "pending":
        return

    user = lock_entitlement_owner(db, user_id)
    balance = int(user.ai_credits or 0)
    event_key = f"{run.id}:reserve"

    if user.is_premium_active() or units == 0:
        if run.model_cost_quote is not None and user.is_premium_active():
            run.model_cost_quote = dict(run.model_cost_quote) | {
                "expense_funding": "legacy_premium"
            }
        if not _event_exists(db, user_id, event_key):
            _append_event(
                db,
                user_id=user_id,
                run_id=run.id,
                event_type="waive",
                amount=0,
                balance_after=balance,
                idempotency_key=event_key,
                reason="Active Premium access" if units else "No-cost operation",
            )
        run.usage_state = "waived"
        return

    funding = funding_for_run(db, user_id=user_id, run=run, units=units)
    paid_units = min(units, current_analysis_funding(db, user_id))
    if run.model_cost_quote is not None:
        run.model_cost_quote = dict(run.model_cost_quote) | {
            "expense_funding": funding,
            "expense_paid_units": paid_units,
        }

    if balance < units:
        raise InsufficientUnitsError(balance=balance, required=units)

    user.ai_credits = balance - units
    _append_event(
        db,
        user_id=user_id,
        run_id=run.id,
        event_type="reserve",
        amount=-units,
        balance_after=user.ai_credits,
        idempotency_key=event_key,
        reason=f"Paid allocation {paid_units}; reserved for {run.operation}",
    )
    run.usage_state = "reserved"
    db.flush()


def _paid_allocation(reason: str | None) -> int:
    import re

    match = re.match(r"^Paid allocation (\d+); (?:reserved|released) for ", reason or "")
    return int(match[1]) if match else 0


def current_analysis_funding(db, user_id: int) -> int:
    from sqlalchemy import Integer, case, cast, func

    grants = int(
        db.query(func.coalesce(func.sum(models.UsageEvent.amount), 0))
        .join(
            models.PaymentOrder,
            models.PaymentOrder.public_id == models.UsageEvent.source_id,
        )
        .filter(
            models.UsageEvent.user_id == user_id,
            models.UsageEvent.source_type == "payment_order",
            models.PaymentOrder.entitlement_kind == "credit_bundle",
            models.PaymentOrder.cost_policy_snapshot["version"].as_string().is_not(None),
        )
        .scalar()
        or 0
    )
    reason = models.UsageEvent.reason
    semicolon = (
        func.instr(reason, ";")
        if db.get_bind().dialect.name == "sqlite"
        else func.strpos(reason, ";")
    )
    paid = case(
        (
            reason.like("Paid allocation %; % for %"),
            cast(func.substr(reason, 17, semicolon - 17), Integer),
        ),
        else_=0,
    )
    used = int(
        db.query(
            func.coalesce(
                func.sum(case((models.UsageEvent.event_type == "release", -paid), else_=paid)), 0
            )
        )
        .filter(
            models.UsageEvent.user_id == user_id,
            models.UsageEvent.source_type == "analysis_run",
            models.UsageEvent.event_type.in_(["reserve", "release"]),
        )
        .scalar()
        or 0
    )
    return max(0, grants - used)


def funding_for_run(db, *, user_id, run, units):
    if run.usage_state == "waived" and units:
        return "legacy_premium"
    if run.usage_state in {"reserved", "committed", "released"}:
        receipt = (
            db.query(models.UsageEvent)
            .filter_by(
                user_id=user_id, source_type="analysis_run", source_id=run.id, event_type="reserve"
            )
            .first()
        )
        paid = _paid_allocation(receipt.reason if receipt else None)
    else:
        paid = min(units, current_analysis_funding(db, user_id))
    if run.model_cost_quote is not None:
        run.model_cost_quote = dict(run.model_cost_quote) | {"expense_paid_units": paid}
    return "prepaid" if units > 0 and paid >= units else "promotion"


def commit_run_usage(db: Session, run: models.AnalysisRun) -> None:
    if run.usage_state in {"committed", "waived"}:
        if run.usage_state == "waived":
            run.committed_units = 0
        return
    if run.usage_state != "reserved":
        raise ValueError(f"Cannot commit usage in state {run.usage_state}")

    event_key = f"{run.id}:commit"
    user = lock_entitlement_owner(db, run.user_id)
    if not _event_exists(db, run.user_id, event_key):
        _append_event(
            db,
            user_id=run.user_id,
            run_id=run.id,
            event_type="commit",
            amount=0,
            balance_after=int(user.ai_credits or 0),
            idempotency_key=event_key,
            reason=f"Completed {run.operation}",
        )
    run.usage_state = "committed"
    run.committed_units = run.estimated_units
    if run.model_cost_group_id:
        for liability in (
            db.query(models.ModelCostLiability)
            .filter_by(financial_group_id=run.model_cost_group_id)
            .all()
        ):
            quote = dict(liability.pricing_quote or {})
            if quote.get("expense_funding") == "prepaid":
                liability.pricing_quote = quote | {"expense_pool": "prepaid"}
    db.flush()


def release_run_usage(db: Session, run: models.AnalysisRun, *, reason: str) -> None:
    if run.model_cost_group_id and run.usage_state not in {"committed", "released"}:
        # Detached financial evidence carries no owner identifiers. A restored
        # product allowance cannot make the failed provider work disappear.
        for liability in (
            db.query(models.ModelCostLiability)
            .filter_by(financial_group_id=run.model_cost_group_id)
            .all()
        ):
            liability.pricing_quote = dict(liability.pricing_quote or {}) | {
                "expense_pool": "failed_work"
            }
    if run.usage_state in {"released", "waived", "committed"}:
        return
    if run.usage_state == "pending":
        run.usage_state = "released"
        return
    if run.usage_state != "reserved":
        raise ValueError(f"Cannot release usage in state {run.usage_state}")

    event_key = f"{run.id}:release"
    user = lock_entitlement_owner(db, run.user_id)
    if not _event_exists(db, run.user_id, event_key):
        receipt = (
            db.query(models.UsageEvent)
            .filter_by(
                user_id=run.user_id,
                source_type="analysis_run",
                source_id=run.id,
                event_type="reserve",
            )
            .first()
        )
        paid_units = _paid_allocation(receipt.reason if receipt else None)
        user.ai_credits = int(user.ai_credits or 0) + int(run.estimated_units or 0)
        _append_event(
            db,
            user_id=run.user_id,
            run_id=run.id,
            event_type="release",
            amount=int(run.estimated_units or 0),
            balance_after=user.ai_credits,
            idempotency_key=event_key,
            reason=f"Paid allocation {paid_units}; released for {run.operation}: {reason}"[:240],
        )
    run.usage_state = "released"
    run.committed_units = 0
    db.flush()
