from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from .. import models
from ..domains.common import payload_fingerprint, public_id, utcnow
from ..domains.usage import (
    InsufficientUnitsError,
    commit_run_usage,
    release_run_usage,
    reserve_run_usage,
)


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
        yield run
    except Exception as exc:
        db.rollback()
        current = db.get(models.AnalysisRun, run.id)
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
        current = db.get(models.AnalysisRun, run.id)
        if current:
            commit_run_usage(db, current)
            current.status = "succeeded"
            current.completed_at = utcnow()
            current.updated_at = current.completed_at
            db.commit()
