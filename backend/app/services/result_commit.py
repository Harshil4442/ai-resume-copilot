"""Explicit, short result-write boundary after all SDK/render work has ended."""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from ..domains.employer.admissions import lock_application_set
from .generation_budget import current_budget


class ResultOwnerGone(HTTPException):
    def __init__(self):
        super().__init__(status_code=410, detail="The account or operation is no longer available.")


def begin_result_commit(
    db: Session, user_id: int, run_id: str | None = None, *, allow_cancelled: bool = False,
) -> None:
    """Call before result row locks/mutations/flush; caller ends the transaction.

    Scalar fresh reads do not overwrite staged ORM attributes. no_autoflush
    prevents an innocent guard query from writing those attributes first.
    This is not a session hook: callers must never invoke SDK/render/retry work
    after entering this boundary until commit/rollback releases the guard.
    """
    budget = current_budget()
    effective_run_id = run_id or (budget.run_id if budget is not None else None)
    with db.no_autoflush:
        lock_application_set(db, user_id)
        if db.query(models.User.id).filter_by(id=user_id).first() is None:
            raise ResultOwnerGone()
        if effective_run_id is not None:
            state = db.query(models.AnalysisRun.status, models.AnalysisRun.cancel_requested).filter_by(
                id=effective_run_id, user_id=user_id,
            ).with_for_update().one_or_none()
            if state is None:
                raise ResultOwnerGone()
            if not allow_cancelled and (state.cancel_requested or state.status not in {"queued", "running"}):
                raise HTTPException(status_code=409, detail="The operation no longer accepts result writes.")
