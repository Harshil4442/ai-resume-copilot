"""Atomic free-processing admission. Callers commit before expensive work."""
from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from ... import models
from ..common import public_id
from ..credits import admission
from ..credits.finance import FundingUnavailable
from .models import FreeCoreRequest, FreeCoreReservation
from .policy import FreeCoreUnavailable, Operation, current_policy


def analysis_operation(operation: str, payload: dict) -> Operation | None:
    if operation == "job_match" and payload.get("mode", "basic") == "basic":
        return "basic_match"
    if operation == "interview_questions" and payload.get("mode", "curated") == "curated":
        return "curated_interview"
    if operation == "skill_roi":
        return "skill_roi"
    return None


def amount_expression(db):
    actual = func.coalesce(FreeCoreReservation.settled_minor, 0)
    maximum = func.max(FreeCoreReservation.reserved_minor, actual) if db.get_bind().dialect.name == "sqlite" else func.greatest(FreeCoreReservation.reserved_minor, actual)
    return case((FreeCoreReservation.state == "settled", actual), else_=maximum)


def expense_usage(db, *, start: datetime, owner: int | None = None, route: str | None = None) -> int:
    query = db.query(func.coalesce(func.sum(amount_expression(db)), 0)).filter(
        or_(FreeCoreReservation.state != "settled", FreeCoreReservation.created_at >= start))
    if owner is not None:
        query = query.filter(FreeCoreReservation.user_id == owner)
    if route is not None:
        query = query.filter(FreeCoreReservation.route_id == route)
    return int(query.scalar() or 0)


def admit(db: Session, *, user_id: int, operation: Operation, source_type: str,
          source_id: str, key: str, fingerprint: str, input_bytes: int,
          attempts: int, now: datetime | None = None) -> tuple[FreeCoreRequest, bool]:
    """Reserve complete infrastructure cost; no credit, model or provider mutation."""
    stamp = now or datetime.now(UTC)
    owner = db.query(models.User.id).filter_by(id=user_id).with_for_update().one_or_none()
    if owner is None:
        raise HTTPException(404, "Account not found")
    existing = db.query(FreeCoreRequest).filter_by(user_id=user_id, idempotency_key=key).one_or_none()
    if existing:
        if (existing.input_fingerprint != fingerprint or existing.source_type != source_type
                or existing.source_id != source_id):
            raise HTTPException(409, "This request key belongs to different inputs")
        return existing, False
    if type(input_bytes) is not int or input_bytes < 0 or type(attempts) is not int or attempts < 1:
        raise ValueError("A bounded complete processing envelope is required")
    try:
        policy = current_policy(now=stamp)
        envelope = policy.operations.get(operation)
        if envelope is None:
            raise FreeCoreUnavailable("This optional free service has no reviewed processing allowance yet. Saved results remain available.")
        if input_bytes > envelope.max_input_bytes or attempts > envelope.max_attempts:
            raise FreeCoreUnavailable("These inputs exceed the supported free-processing envelope. No content was shortened or credits charged.")
        if operation == "skill_roi":
            # Skill ROI reads the owned workspace rather than request text.
            # Refuse an unqualified large dataset; never silently sample it.
            opportunities = db.query(models.Opportunity.id).filter_by(user_id=user_id, archived_at=None).count()
            evidence = db.query(models.EvidenceItem.id).filter_by(user_id=user_id, approval_state="approved").count()
            if opportunities + evidence > envelope.max_source_records:
                raise FreeCoreUnavailable("The complete workspace exceeds the supported free-processing record limit. No records were omitted or credits charged.")
        admission.lock_money(db)
        if db.query(FreeCoreReservation.id).filter(
            FreeCoreReservation.route_id == envelope.route_id,
            FreeCoreReservation.policy_version == policy.version,
            FreeCoreReservation.settled_minor > FreeCoreReservation.reserved_minor,
        ).first():
            raise FreeCoreUnavailable("New processing on this route is paused while its actual expense overrun is reviewed.")
        start = stamp - timedelta(days=30)
        query = db.query(FreeCoreReservation).filter(FreeCoreReservation.created_at >= start)
        used = query.filter_by(user_id=user_id, operation=operation).count()
        if used >= envelope.per_account_30_days:
            raise HTTPException(429, detail={"code": "free_processing_account_limit",
                "message": f"The {envelope.per_account_30_days}-request free allowance for this service has been used in the last 30 days. Your saved results remain available."})
        if query.count() >= policy.global_30_day_operations:
            raise FreeCoreUnavailable("The funded free-processing capacity is fully allocated. Try later or continue using saved results.")
        if expense_usage(db, start=policy.valid_from) + envelope.reserved_minor > policy.free_allocation_minor:
            raise FreeCoreUnavailable("The funded free-processing allowance is fully allocated. Try later or continue using saved results.")
        admission.assert_headroom(db, user_id=user_id, route_id=envelope.route_id,
            required_minor=envelope.reserved_minor, policy=policy.financial_policy,
            budgets=policy.execution_budgets, now=stamp, funding_kind="shared")
    except (FreeCoreUnavailable, FundingUnavailable) as exc:
        raise HTTPException(503, detail={"code": "free_processing_unavailable", "message": str(exc)}) from None
    reservation = FreeCoreReservation(id=public_id("fcr"), user_id=user_id, operation=operation,
        route_id=envelope.route_id, policy_version=policy.version, funding_period_id=policy.funding_period_id,
        reserved_minor=envelope.reserved_minor, state="reserved", created_at=stamp)
    db.add(reservation)
    db.flush([reservation])
    request = FreeCoreRequest(id=public_id("fcq"), user_id=user_id, source_type=source_type,
        source_id=source_id, idempotency_key=key, input_fingerprint=fingerprint,
        reservation_id=reservation.id, state="admitted")
    db.add(request)
    db.flush([request])
    return request, True


def complete(db: Session, *, user_id: int, source_type: str, source_id: str, result_id: str) -> None:
    row = db.query(FreeCoreRequest).filter_by(user_id=user_id, source_type=source_type, source_id=source_id).with_for_update().one_or_none()
    if row is not None:
        if row.state == "completed" and row.result_id != result_id:
            raise ValueError("Free processing result was already bound")
        row.state, row.result_id = "completed", result_id
    # Completion is not proof of actual worker cost. The financial hold remains
    # until invoice/usage reconciliation, including when delivery fails.


def settle_measured_expense(db: Session, *, reservation_id: str, actual_minor: int,
                            receipt_sha256: str, now: datetime | None = None) -> None:
    """Internal reconciliation only, never inferred from a successful response."""
    if type(actual_minor) is not int or actual_minor < 0 or not re.fullmatch(r"[a-f0-9]{64}", receipt_sha256):
        raise ValueError("Measured expense and a retained receipt digest are required")
    admission.lock_money(db)
    row = db.query(FreeCoreReservation).filter_by(id=reservation_id).with_for_update().one()
    if row.state == "settled":
        if row.settled_minor != actual_minor or row.settlement_receipt_sha256 != receipt_sha256:
            raise ValueError("Expense reconciliation conflicts with the recorded receipt")
        return
    row.settled_minor, row.state = actual_minor, "settled"
    row.settlement_receipt_sha256 = receipt_sha256
    row.settled_at = now or datetime.now(UTC)
