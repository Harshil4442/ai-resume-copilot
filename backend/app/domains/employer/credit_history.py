"""Owner-scoped views of the existing service-credit ledger.

Reservation amounts are the settlement authority. A commit event's balance
delta is zero because the debit already happened at reservation time.
Persistence models are supplied by transport so this read boundary can be
verified without importing the application's complete model registry.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, func, or_
from sqlalchemy.orm import Session

_RESERVATION_FIELDS = (
    "id", "operation", "source_id", "unit_price", "requested_count", "reserved_amount",
    "committed_amount", "released_amount", "state", "pricing_version", "created_at", "settled_at",
)
_EVENT_FIELDS = (
    "id", "event_type", "amount", "balance_after", "source_type", "source_id", "reason", "created_at",
)
_OPERATIONS = {"job_search", "job_application"}


class ServiceCreditOperation(BaseModel):
    id: str
    operation: str
    source_id: str
    unit_price: int
    requested_count: int
    reserved_amount: int
    held_amount: int
    committed_amount: int
    released_amount: int
    state: str
    pricing_version: str
    created_at: datetime
    settled_at: datetime | None


class ServiceCreditEventView(BaseModel):
    id: str
    event_type: str
    amount: int
    balance_after: int
    source_type: str
    source_id: str
    reason: str
    created_at: datetime


class ServiceCreditHistory(BaseModel):
    balance: int
    reserved_credits: int
    reservations: list[ServiceCreditOperation]
    items: list[ServiceCreditEventView]
    operations: list[ServiceCreditOperation]
    next_cursor: str | None


def _invalid_cursor() -> HTTPException:
    return HTTPException(400, detail={
        "code": "invalid_credit_history_cursor",
        "message": "This service-history cursor is invalid. Refresh to start from the first page.",
    }, headers={"Cache-Control": "private, no-store"})


def _date(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat()


def reservation_response(row: Any) -> dict[str, Any]:
    fields = row._mapping
    result = {key: fields[key] for key in _RESERVATION_FIELDS}
    result["held_amount"] = max(0, fields["reserved_amount"] - fields["committed_amount"]
                                - fields["released_amount"]) if fields["state"] == "reserved" else 0
    result["created_at"] = _date(fields["created_at"])
    result["settled_at"] = _date(fields["settled_at"])
    return result


def history(db: Session, owner_id: int, *, balance: int, reservation_model: Any,
            event_model: Any, limit: int = 20, cursor: str | None = None,
            operation: str | None = None) -> dict[str, Any]:
    """Page operations by immutable creation order, retaining the old event view."""
    if type(limit) is not int or not 1 <= limit <= 200:
        raise HTTPException(400, "Service-credit history supports 1 to 200 entries per page.")
    if operation is not None and operation not in _OPERATIONS:
        raise HTTPException(400, "Choose job_search or job_application history.")
    reservation = reservation_model
    event = event_model
    with db.no_autoflush:
        query = db.query(*(getattr(reservation, field) for field in _RESERVATION_FIELDS)).filter(
            reservation.user_id == owner_id,
        )
        if operation is not None:
            query = query.filter(reservation.operation == operation)
        if cursor is not None:
            if not isinstance(cursor, str) or not 1 <= len(cursor) <= 64:
                raise _invalid_cursor()
            # The cursor identifies an owned boundary; another owner's ID and
            # an absent ID fail identically without exposing their records.
            boundary = query.filter(reservation.id == cursor).first()
            if boundary is None:
                raise _invalid_cursor()
            query = query.filter(or_(reservation.created_at < boundary.created_at, and_(
                reservation.created_at == boundary.created_at, reservation.id < boundary.id,
            )))
        rows = query.order_by(reservation.created_at.desc(), reservation.id.desc()).limit(limit + 1).all()
        held_query = db.query(*(getattr(reservation, field) for field in _RESERVATION_FIELDS)).filter(
            reservation.user_id == owner_id, reservation.state == "reserved",
        )
        held = held_query.order_by(reservation.created_at.desc(), reservation.id.desc()).limit(200).all()
        held_amount = db.query(func.coalesce(func.sum(
            reservation.reserved_amount - reservation.committed_amount - reservation.released_amount,
        ), 0)).filter(reservation.user_id == owner_id, reservation.state == "reserved").scalar()
        event_query = db.query(*(getattr(event, field) for field in _EVENT_FIELDS)).filter(event.user_id == owner_id)
        if operation is not None:
            event_query = event_query.filter(event.source_type == operation)
        events = event_query.order_by(event.created_at.desc(), event.id.desc()).limit(limit).all()
    operations = [reservation_response(row) for row in rows[:limit]]
    return {
        "balance": balance, "reserved_credits": int(held_amount),
        "reservations": [reservation_response(row) for row in held],
        "items": [{**dict(row._mapping), "created_at": _date(row.created_at)} for row in events],
        "operations": operations,
        "next_cursor": operations[-1]["id"] if len(rows) > limit else None,
    }
