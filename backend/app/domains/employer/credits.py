"""Atomic prepaid service-credit reservation and settlement.

Amounts never derive from the browser or Premium status. The user lock is the
shared money authority; operation rows must be locked before settling twice.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ...models import User
from ..common import public_id, utcnow
from .config import PRICING_VERSION
from .models import ServiceCreditEvent, ServiceCreditReservation


def _event(db, user, reservation, kind, amount, reason):
    key = f"{reservation.id}:{kind}"
    db.add(ServiceCreditEvent(
        id=public_id("svc"), user_id=user.id, event_type=kind, amount=amount,
        balance_after=int(user.job_service_credits or 0), idempotency_key=key,
        source_type=reservation.operation, source_id=reservation.source_id,
        reason=reason[:240], created_at=utcnow(),
    ))


def reserve(db: Session, *, user_id: int, operation: str, source_id: str,
            unit_price: int, count: int) -> ServiceCreditReservation:
    user = db.query(User).filter(User.id == user_id).with_for_update().one()
    amount = unit_price * count
    balance = int(user.job_service_credits or 0)
    if amount < 0:
        raise ValueError("Reservation cannot be negative")
    if balance < amount:
        raise HTTPException(402, detail={
            "code": "insufficient_service_credits", "required": amount, "balance": balance,
            "message": f"This action needs {amount} service credits; {balance} are available.",
        })
    user.job_service_credits = balance - amount
    reservation = ServiceCreditReservation(
        id=public_id("resv"), user_id=user_id, operation=operation, source_id=source_id,
        unit_price=unit_price, requested_count=count, reserved_amount=amount,
        committed_amount=0, released_amount=0, state="reserved", pricing_version=PRICING_VERSION,
    )
    db.add(reservation)
    _event(db, user, reservation, "reserve", -amount, "Reserved prepaid credits for " + operation)
    return reservation


def settle(db: Session, reservation: ServiceCreditReservation, *, completed_count: int,
           reason: str) -> None:
    if reservation.state != "reserved":
        return
    if not 0 <= completed_count <= reservation.requested_count:
        raise ValueError("Completed count exceeds the reservation")
    user = db.query(User).filter(User.id == reservation.user_id).with_for_update().one()
    committed = completed_count * reservation.unit_price
    refund = reservation.reserved_amount - committed
    user.job_service_credits = int(user.job_service_credits or 0) + refund
    reservation.committed_amount = committed
    reservation.released_amount = refund
    reservation.state = "settled"
    reservation.settled_at = utcnow()
    _event(db, user, reservation, "commit", 0, reason)
    if refund:
        _event(db, user, reservation, "release", refund, "Unused reservation returned: " + reason)
