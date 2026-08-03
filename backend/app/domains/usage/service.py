from __future__ import annotations

from sqlalchemy.orm import Session

from ... import models
from ..common import public_id, utcnow


class InsufficientUnitsError(ValueError):
    def __init__(self, *, balance: int, required: int):
        self.balance = balance
        self.required = required
        super().__init__(
            f"This operation requires {required} analysis units; {balance} remain."
        )


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

    user = (
        db.query(models.User)
        .filter(models.User.id == user_id)
        .with_for_update()
        .one()
    )
    balance = int(user.ai_credits or 0)
    event_key = f"{run.id}:reserve"

    if user.is_premium_active() or units == 0:
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
        reason=f"Reserved for {run.operation}",
    )
    run.usage_state = "reserved"


def commit_run_usage(db: Session, run: models.AnalysisRun) -> None:
    if run.usage_state in {"committed", "waived"}:
        if run.usage_state == "waived":
            run.committed_units = 0
        return
    if run.usage_state != "reserved":
        raise ValueError(f"Cannot commit usage in state {run.usage_state}")

    event_key = f"{run.id}:commit"
    user = db.query(models.User).filter(models.User.id == run.user_id).one()
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


def release_run_usage(db: Session, run: models.AnalysisRun, *, reason: str) -> None:
    if run.usage_state in {"released", "waived", "committed"}:
        return
    if run.usage_state == "pending":
        run.usage_state = "released"
        return
    if run.usage_state != "reserved":
        raise ValueError(f"Cannot release usage in state {run.usage_state}")

    event_key = f"{run.id}:release"
    user = (
        db.query(models.User)
        .filter(models.User.id == run.user_id)
        .with_for_update()
        .one()
    )
    if not _event_exists(db, run.user_id, event_key):
        user.ai_credits = int(user.ai_credits or 0) + int(run.estimated_units or 0)
        _append_event(
            db,
            user_id=run.user_id,
            run_id=run.id,
            event_type="release",
            amount=int(run.estimated_units or 0),
            balance_after=user.ai_credits,
            idempotency_key=event_key,
            reason=reason[:240],
        )
    run.usage_state = "released"
    run.committed_units = 0
