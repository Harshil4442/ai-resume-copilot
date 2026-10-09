from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session, sessionmaker

from .. import models
from ..domains.common import payload_fingerprint, public_id, utcnow
from ..domains.entitlements import lock_entitlement_owner
from ..domains.usage import (
    InsufficientUnitsError,
    commit_run_usage,
    release_run_usage,
    reserve_run_usage,
)
from .result_commit import ResultOwnerGone, begin_result_commit


class OptionalGenerationUnavailable(HTTPException):
    """Typed configuration refusal; unrelated provider errors are not swallowed."""

    def __init__(self, detail: str):
        super().__init__(status_code=503, detail=detail)


@contextmanager
def billable_operation(
    *,
    user_id: int,
    db: Session,
    operation: str,
    amount: int,
    input_payload: dict[str, Any] | None = None,
) -> Iterator[models.AnalysisRun]:
    """Give a synchronous legacy operation durable, failure-safe usage accounting.

    Callers must complete ownership and input validation before entering this
    context. A reservation is persisted before provider work begins, committed
    only after a successful return, and released after every raised exception.
    """
    if amount < 0:
        raise ValueError("analysis-unit reservation cannot be negative")

    from .generation_gate import check_generation_admission

    check_generation_admission(operation, input_payload)

    from ..domains.employer.admissions import lock_application_set

    lock_application_set(db, user_id)

    # Lock before inserting the run: its FK otherwise takes a key-share owner
    # lock, and two concurrent requests can deadlock upgrading to FOR UPDATE.
    lock_entitlement_owner(db, user_id)
    payload = input_payload or {}
    now = utcnow()
    run = models.AnalysisRun(
        id=public_id("run"),
        user_id=user_id,
        operation=operation,
        status="running",
        idempotency_key=public_id("legacy"),
        input_fingerprint=payload_fingerprint(payload),
        input_payload=payload,
        estimated_units=amount,
        committed_units=0,
        usage_state="pending",
        attempt_count=1,
        created_at=now,
        updated_at=now,
        started_at=now,
    )
    run_id = run.id
    # Mixed legacy operations can finish deterministically with no AI policy.
    # Their first actual provider attempt freezes a quote before any network.
    # Explicit generation freezes it before execution and product reservation.
    if payload.get("mode") == "enhanced" or operation in {
        "resume_tailor_legacy", "rewrite_bullets", "interview_questions_legacy",
    }:
        from .model_cost_policy import ModelCostUnavailable, freeze_run_quote

        try:
            freeze_run_quote(run)
        except ModelCostUnavailable as exc:
            db.rollback()
            raise OptionalGenerationUnavailable(str(exc)) from exc
    db.add(run)
    db.flush()
    try:
        reserve_run_usage(db, user_id=user_id, run=run, units=amount)
        db.commit()
    except InsufficientUnitsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                f"Operation requires {exc.required} analysis unit(s). "
                f"Your balance is {exc.balance}. Premium access has no unit deductions."
            ),
        ) from exc

    try:
        from .generation_budget import persistent_run_budget

        with persistent_run_budget(sessionmaker(bind=db.get_bind()), run_id):
            yield run
    except Exception as exc:
        db.rollback()
        try:
            begin_result_commit(db, user_id, run_id, allow_cancelled=True)
        except ResultOwnerGone:
            db.rollback()
            raise exc from None
        current = db.get(models.AnalysisRun, run_id, populate_existing=True)
        if current:
            release_run_usage(
                db,
                current,
                reason=f"Synchronous operation failed: {type(exc).__name__}",
            )
            current.status = "failed"
            current.error_code = type(exc).__name__
            current.error_message = str(exc)[:500]
            current.completed_at = utcnow()
            current.updated_at = current.completed_at
            db.commit()
        raise
    else:
        try:
            begin_result_commit(db, user_id, run_id, allow_cancelled=True)
        except ResultOwnerGone:
            db.rollback()
            return
        current = db.get(models.AnalysisRun, run_id, populate_existing=True)
        if current:
            if current.cancel_requested:
                release_run_usage(db, current, reason="Cancelled during synchronous execution")
                current.status = "cancelled"
                current.cancelled_at = utcnow()
            else:
                commit_run_usage(db, current)
                current.status = "succeeded"
            current.completed_at = utcnow()
            current.updated_at = current.completed_at
            db.commit()
