"""Atomic prepaid service-credit reservation and settlement.

Amounts never derive from the browser or Premium status. The user lock is the
shared money authority; operation rows must be locked before settling twice.
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ..common import public_id, utcnow
from ..entitlements import lock_entitlement_owner
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
            unit_price: int, count: int, pricing_version: str = PRICING_VERSION,
            preparation: ServiceCreditReservation | None = None,
            record_event: bool = True) -> ServiceCreditReservation:
    from ...billing.cost_policy import (
        CostPolicyUnavailable,
        assert_actual_variance,
        current_policy,
        current_service_funding,
        service_expense_holds,
        snapshot,
    )
    user = lock_entitlement_owner(db, user_id)
    if preparation is not None and (preparation.user_id != user_id or preparation.operation != operation
            or preparation.source_id != source_id or preparation.state != "reserved"
            or preparation.reserved_amount != 0 or not (preparation.cost_policy_snapshot or {}).get("search_preparation")):
        raise ValueError("Preparation funding must be the server-owned pending reservation")
    try:
        policy = current_policy()
        assert_actual_variance(db, policy)
        authority = snapshot(policy)
        authority["paid_current_reserved_credits"] = min(unit_price * count, current_service_funding(db, user_id))
        authority["funding_order"] = "new-first-v1"
        authority.update(service_expense_holds(
            db, policy, operation=operation, unit_price=unit_price, count=count, user_id=user_id,
            exclude_reservation_id=preparation.id if preparation is not None else None,
        ))
    except CostPolicyUnavailable as exc:
        raise HTTPException(503, detail=str(exc)) from None
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
    reservation = preparation or ServiceCreditReservation(
        id=public_id("resv"), user_id=user_id, operation=operation, source_id=source_id,
        unit_price=unit_price, requested_count=count, reserved_amount=amount,
        committed_amount=0, released_amount=0, state="reserved", pricing_version=pricing_version,
        cost_policy_snapshot=authority,
    )
    if preparation is not None:
        reservation.unit_price = unit_price
        reservation.requested_count = count
        reservation.reserved_amount = amount
        reservation.pricing_version = pricing_version
        reservation.cost_policy_snapshot = authority
    db.add(reservation)
    if record_event:
        _event(db, user, reservation, "reserve", -amount, "Reserved prepaid credits for " + operation)
    db.flush()
    return reservation


def settle(db: Session, reservation: ServiceCreditReservation, *, completed_count: int,
           reason: str) -> None:
    if reservation.state != "reserved":
        return
    if not 0 <= completed_count <= reservation.requested_count:
        raise ValueError("Completed count exceeds the reservation")
    user = lock_entitlement_owner(db, reservation.user_id)
    committed = completed_count * reservation.unit_price
    refund = reservation.reserved_amount - committed
    user.job_service_credits = int(user.job_service_credits or 0) + refund
    reservation.committed_amount = committed
    reservation.released_amount = refund
    reservation.state = "settled"
    # Successful paid jobs release their provisional failure pool hold. Empty
    # or unsuccessful paid work retains cash exposure even as credits return.
    authority = dict(reservation.cost_policy_snapshot or {})
    paid_count = int(authority.get("paid_service_cost_count", 0))
    if paid_count:
        from fractions import Fraction
        from math import ceil
        remaining = max(0, paid_count - completed_count)
        authority["failed_service_hold_minor"] = ceil(Fraction(
            int(authority.get("failed_service_hold_minor", 0)) * remaining, paid_count,
        ))
        reservation.cost_policy_snapshot = authority
    reservation.settled_at = utcnow()
    _event(db, user, reservation, "commit", 0, reason)
    if refund:
        _event(db, user, reservation, "release", refund, "Unused reservation returned: " + reason)
    db.flush()
