"""Transactional unified credit primitives.

Callers own commit/rollback and dispatch. These functions never call a payment or
model provider. A caller must reserve the complete monetary envelope in the SAME
transaction before dispatching a CreditReservation. Legacy balances are never read
as unified credits or silently converted here.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ...models import User
from ..common import public_id, utcnow
from .models import (
    CreditAccount,
    CreditAllocation,
    CreditCampaign,
    CreditEvent,
    CreditGrant,
    CreditPurchaseQuote,
    CreditReservation,
)
from .pricing import (
    EXECUTION_MINOR_PER_CREDIT,
    PASS_LIFETIME,
    PURCHASE_QUOTE_LIFETIME,
    RESTORATION_LIFETIME,
    CreditContractError,
    aware,
    public_catalog,
)
from .schemas import CreditLotView, CreditWalletView, PurchaseQuoteView


class InsufficientCredits(CreditContractError):
    def __init__(self, *, available: int, required: int):
        self.available, self.required = available, required
        super().__init__(f"This action needs {required} credits; {available} are available.")


def _stamp(now: datetime | None) -> datetime:
    return aware(now or utcnow())


def _count(value: int, *, positive: bool = False) -> int:
    if type(value) is not int or value < (1 if positive else 0) or value > 10**9:
        raise CreditContractError("Credit quantities must be bounded nonnegative integers.")
    return value


def _key(value: str, maximum: int = 160) -> str:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= maximum
        or re.search(r"[\s\x00-\x1f]", value)
    ):
        raise CreditContractError("A bounded identifier without whitespace is required.")
    return value


def _lock_owner(db: Session, user_id: int) -> None:
    from ..employer.admissions import lock_application_set

    lock_application_set(db, user_id)
    if db.query(User.id).filter_by(id=user_id).with_for_update().one_or_none() is None:
        raise CreditContractError("The credit owner does not exist.")


def _lock_account(db: Session, user_id: int) -> CreditAccount:
    _lock_owner(db, user_id)
    cached = db.get(CreditAccount, user_id)
    if cached is not None and cached in db.dirty:
        db.flush([cached])
    account = (
        db.query(CreditAccount)
        .filter_by(user_id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if account is None:
        account = CreditAccount(user_id=user_id, debt_credits=0)
        db.add(account)
        db.flush([account])
    return account


def configured_campaign(db: Session) -> CreditCampaign | None:
    rows = (
        db.query(CreditCampaign)
        .filter_by(enabled=True)
        .order_by(CreditCampaign.starts_at.desc())
        .limit(2)
        .all()
    )
    if len(rows) > 1:
        raise CreditContractError("Multiple enabled campaigns require pricing reconciliation.")
    return rows[0] if rows else None


def quote_response(quote: CreditPurchaseQuote, *, now: datetime | None = None) -> PurchaseQuoteView:
    return PurchaseQuoteView.model_validate(dict(quote.snapshot) | {"server_time": _stamp(now)})


def issue_purchase_quote(
    db: Session,
    *,
    user_id: int,
    sku: str,
    idempotency_key: str,
    checkout_enabled: bool,
    eligible_services: list[dict] | None = None,
    now: datetime | None = None,
) -> CreditPurchaseQuote:
    """Persist accepted-facing price terms; a retry never silently renews a quote."""
    stamp = _stamp(now)
    _key(idempotency_key)
    _lock_owner(db, user_id)
    prior = (
        db.query(CreditPurchaseQuote)
        .filter_by(user_id=user_id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if prior is not None:
        if prior.sku != sku:
            raise CreditContractError("This purchase request already belongs to a different pack.")
        return prior
    if not checkout_enabled:
        raise CreditContractError("Credit purchases are not enabled.")
    catalog = public_catalog(campaign=configured_campaign(db), now=stamp, checkout_enabled=True)
    product = next((item for item in catalog.products if item.sku == sku), None)
    if product is None:
        raise CreditContractError("This credit pack is not available.")
    identity = public_id("cq")
    payload = PurchaseQuoteView(
        eligible_services=eligible_services or [],
        quote_id=identity,
        sku=product.sku,
        name=product.name,
        catalog_version=catalog.catalog_version,
        terms_version=product.terms_version,
        amount_minor=product.amount_minor,
        credits=product.credits,
        created_at=stamp,
        expires_at=stamp + PURCHASE_QUOTE_LIFETIME,
        pass_expires_at=stamp + PASS_LIFETIME,
        server_time=stamp,
        campaign=product.campaign,
    ).model_dump(mode="json", exclude={"server_time"})
    quote = CreditPurchaseQuote(
        id=identity,
        user_id=user_id,
        sku=sku,
        idempotency_key=idempotency_key,
        snapshot=payload,
        created_at=stamp,
        expires_at=stamp + PURCHASE_QUOTE_LIFETIME,
        state="quoted",
    )
    db.add(quote)
    db.flush([quote])
    return quote


def accept_purchase_quote(
    db: Session, *, user_id: int, quote_id: str, order_id: str, now: datetime | None = None
) -> CreditPurchaseQuote:
    """Bind a valid quote to exactly one durable order before a provider request.

    Once accepted, later webhook fulfillment uses this immutable snapshot even
    after the campaign or quote expires. The caller must check checkout readiness.
    """
    stamp = _stamp(now)
    _key(order_id, 64)
    _lock_owner(db, user_id)
    quote = (
        db.query(CreditPurchaseQuote)
        .filter_by(id=quote_id, user_id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if quote is None:
        raise CreditContractError("Purchase quote not found.")
    if quote.state == "accepted":
        if quote.accepted_order_id != order_id:
            raise CreditContractError("This quote already belongs to another checkout.")
        return quote
    if not aware(quote.created_at) <= stamp < aware(quote.expires_at):
        raise CreditContractError("The purchase quote expired. Review a new quote.")
    # Validate the persisted public contract before accepting, without repricing.
    quote_response(quote, now=stamp)
    quote.state, quote.accepted_order_id, quote.accepted_at = "accepted", order_id, stamp
    db.flush([quote])
    return quote


def _balance(db: Session, user_id: int, account: CreditAccount, stamp: datetime) -> int:
    # Explicit flush matters: the production session has autoflush=False.
    db.flush()
    # Mutations expire stale lots first. During that expiry loop, include each
    # not-yet-recorded expiry so event balances reconcile with event amounts.
    available = (
        db.query(func.coalesce(func.sum(CreditGrant.remaining_credits), 0))
        .filter(
            CreditGrant.user_id == user_id,
        )
        .scalar()
    )
    return int(available or 0) - int(account.debt_credits)


def _event(
    db: Session,
    account: CreditAccount,
    *,
    key: str,
    kind: str,
    amount: int,
    source_type: str,
    source_id: str,
    stamp: datetime,
    origin_type: str | None = None,
    origin_id: str | None = None,
    details: dict[str, Any] | None = None,
) -> CreditEvent:
    event = CreditEvent(
        id=public_id("ce"),
        user_id=account.user_id,
        idempotency_key=key,
        event_type=kind,
        amount=amount,
        balance_after=_balance(db, account.user_id, account, stamp),
        source_type=source_type,
        source_id=source_id,
        origin_type=origin_type,
        origin_id=origin_id,
        details=details or {},
        created_at=stamp,
    )
    db.add(event)
    db.flush([event])
    return event


def _expire(db: Session, account: CreditAccount, stamp: datetime) -> None:
    grants = (
        db.query(CreditGrant)
        .filter(
            CreditGrant.user_id == account.user_id,
            CreditGrant.remaining_credits > 0,
            CreditGrant.expires_at <= stamp,
        )
        .order_by(CreditGrant.expires_at, CreditGrant.id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    for grant in grants:
        removed = grant.remaining_credits
        grant.remaining_credits = 0
        grant.expired_credits += removed
        _event(
            db,
            account,
            key=f"expire:{grant.id}",
            kind="expire",
            amount=-removed,
            source_type="credit_grant",
            source_id=grant.id,
            stamp=stamp,
            origin_type=grant.origin_type,
            origin_id=grant.origin_id,
        )


def expire_credits(db: Session, *, user_id: int, now: datetime | None = None) -> None:
    """Expire only unused balances; accepted reservations and originals survive."""
    _expire(db, _lock_account(db, user_id), _stamp(now))


def _grant(
    db: Session,
    account: CreditAccount,
    *,
    quantity: int,
    source_type: str,
    source_id: str,
    funding_kind: str,
    terms_snapshot: dict,
    expires_at: datetime | None,
    stamp: datetime,
    origin_type: str | None = None,
    origin_id: str | None = None,
    parent_grant_id: str | None = None,
) -> CreditGrant:
    _count(quantity, positive=True)
    _key(source_type, 40)
    _key(source_id)
    if funding_kind not in {"paid", "promotion", "legacy"}:
        raise CreditContractError("The grant needs an explicit funding classification.")
    expiry = aware(expires_at) if expires_at is not None else None
    origin_type, origin_id = origin_type or source_type, origin_id or source_id
    existing = (
        db.query(CreditGrant)
        .filter_by(user_id=account.user_id, source_type=source_type, source_id=source_id)
        .one_or_none()
    )
    if existing is not None:
        if (
            existing.quantity,
            existing.funding_kind,
            existing.terms_snapshot,
            existing.origin_type,
            existing.origin_id,
        ) != (quantity, funding_kind, terms_snapshot, origin_type, origin_id):
            raise CreditContractError("The grant source already has different immutable terms.")
        return existing
    if expiry is not None and expiry <= stamp:
        raise CreditContractError("A new credit grant must provide usable validity.")
    debt_offset = min(int(account.debt_credits), quantity)
    account.debt_credits -= debt_offset
    grant = CreditGrant(
        id=public_id("cg"),
        user_id=account.user_id,
        source_type=source_type,
        source_id=source_id,
        origin_type=origin_type,
        origin_id=origin_id,
        funding_kind=funding_kind,
        quantity=quantity,
        remaining_credits=quantity - debt_offset,
        expired_credits=0,
        issued_at=stamp,
        expires_at=expiry,
        terms_snapshot=copy.deepcopy(terms_snapshot),
        parent_grant_id=parent_grant_id,
    )
    db.add(grant)
    db.flush([grant])
    _event(
        db,
        account,
        key=f"grant:{grant.id}",
        kind="restore" if parent_grant_id else "grant",
        amount=quantity,
        source_type=source_type,
        source_id=source_id,
        origin_type=origin_type,
        origin_id=origin_id,
        stamp=stamp,
        details={"debt_offset": debt_offset},
    )
    return grant


def grant_credits(
    db: Session,
    *,
    user_id: int,
    quantity: int,
    source_type: str,
    source_id: str,
    funding_kind: str,
    terms_snapshot: dict,
    expires_at: datetime | None,
    now: datetime | None = None,
) -> CreditGrant:
    """Trusted fulfilled-payment or approved-migration boundary, not a public API."""
    stamp = _stamp(now)
    account = _lock_account(db, user_id)
    _expire(db, account, stamp)
    return _grant(
        db,
        account,
        quantity=quantity,
        source_type=source_type,
        source_id=source_id,
        funding_kind=funding_kind,
        terms_snapshot=terms_snapshot,
        expires_at=expires_at,
        stamp=stamp,
    )


def fulfill_purchase_quote(
    db: Session,
    *,
    user_id: int,
    quote_id: str,
    order_id: str,
    captured_at: datetime,
    now: datetime | None = None,
) -> CreditGrant:
    """Only call after independently verifying the captured amount and currency."""
    stamp, captured = _stamp(now), aware(captured_at)
    account = _lock_account(db, user_id)
    quote = (
        db.query(CreditPurchaseQuote)
        .filter_by(id=quote_id, user_id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if quote is None or quote.state != "accepted" or quote.accepted_order_id != order_id:
        raise CreditContractError("This payment has no matching accepted purchase quote.")
    view = quote_response(quote, now=stamp)
    if captured < aware(quote.created_at) or captured > stamp:
        raise CreditContractError("The verified payment timestamp is inconsistent.")
    # A delayed webhook cannot grant an already-expired pass or shorten the
    # displayed minimum. The actual favorable extension is shown on the receipt.
    expiry = max(view.pass_expires_at, captured + PASS_LIFETIME, stamp + PASS_LIFETIME)
    _expire(db, account, stamp)
    return _grant(
        db,
        account,
        quantity=view.credits,
        source_type="payment_order",
        source_id=order_id,
        funding_kind="paid",
        terms_snapshot=dict(quote.snapshot),
        expires_at=expiry,
        stamp=stamp,
    )


def wallet(db: Session, *, user_id: int, now: datetime | None = None) -> CreditWalletView:
    """Read-only view excludes expired unused lots without mutating historical events."""
    stamp = _stamp(now)
    account = db.get(CreditAccount, user_id)
    debt = int(account.debt_credits) if account is not None else 0
    lots = (
        db.query(CreditGrant)
        .filter(
            CreditGrant.user_id == user_id,
            CreditGrant.remaining_credits > 0,
            or_(CreditGrant.expires_at.is_(None), CreditGrant.expires_at > stamp),
        )
        .order_by(CreditGrant.expires_at.asc().nulls_last(), CreditGrant.issued_at, CreditGrant.id)
        .all()
    )
    reserved = int(
        db.query(func.coalesce(func.sum(CreditReservation.reserved_credits), 0))
        .filter_by(user_id=user_id, state="reserved")
        .scalar()
        or 0
    )
    return CreditWalletView(
        server_time=stamp,
        available_credits=max(0, sum(g.remaining_credits for g in lots) - debt),
        debt_credits=debt,
        reserved_credits=reserved,
        lots=[
            CreditLotView(
                grant_id=g.id,
                remaining_credits=g.remaining_credits,
                expires_at=aware(g.expires_at) if g.expires_at else None,
                funding_kind=g.funding_kind,
            )
            for g in lots
        ],
    )


def reserve_credits(
    db: Session,
    *,
    user_id: int,
    operation: str,
    source_id: str,
    idempotency_key: str,
    credits: int,
    input_fingerprint: str,
    quote_snapshot: dict,
    execution_allowance_minor: int,
    now: datetime | None = None,
) -> CreditReservation:
    """Credit half of atomic admission; caller supplies the money half + outbox.

    This function never dispatches work or implies that an allowance is funded.
    """
    _count(credits, positive=True)
    _count(execution_allowance_minor)
    _key(operation, 64)
    _key(source_id, 64)
    _key(idempotency_key)
    if not re.fullmatch(r"[a-f0-9]{64}", input_fingerprint):
        raise CreditContractError("An exact input fingerprint is required.")
    if execution_allowance_minor > credits * EXECUTION_MINOR_PER_CREDIT:
        raise CreditContractError("The operation exceeds the per-credit execution allowance.")
    stamp, account = _stamp(now), _lock_account(db, user_id)
    prior = (
        db.query(CreditReservation)
        .filter(
            CreditReservation.user_id == user_id,
            or_(
                CreditReservation.idempotency_key == idempotency_key,
                (CreditReservation.operation == operation)
                & (CreditReservation.source_id == source_id),
            ),
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    if prior:
        if len(prior) != 1:
            raise CreditContractError("The operation and request identities conflict.")
        row = prior[0]
        if (
            row.operation,
            row.source_id,
            row.reserved_credits,
            row.input_fingerprint,
            row.quote_snapshot,
            row.execution_allowance_minor,
        ) != (
            operation,
            source_id,
            credits,
            input_fingerprint,
            quote_snapshot,
            execution_allowance_minor,
        ):
            raise CreditContractError("This reservation already has different accepted terms.")
        return row
    _expire(db, account, stamp)
    available = max(0, _balance(db, user_id, account, stamp))
    if credits > available:
        raise InsufficientCredits(available=available, required=credits)
    row = CreditReservation(
        id=public_id("cr"),
        user_id=user_id,
        operation=operation,
        source_id=source_id,
        idempotency_key=idempotency_key,
        input_fingerprint=input_fingerprint,
        quote_snapshot=copy.deepcopy(quote_snapshot),
        execution_allowance_minor=execution_allowance_minor,
        reserved_credits=credits,
        committed_credits=0,
        released_credits=0,
        state="reserved",
        created_at=stamp,
    )
    db.add(row)
    db.flush([row])
    lots = (
        db.query(CreditGrant)
        .filter(
            CreditGrant.user_id == user_id,
            CreditGrant.remaining_credits > 0,
            or_(CreditGrant.expires_at.is_(None), CreditGrant.expires_at > stamp),
        )
        .order_by(CreditGrant.expires_at.asc().nulls_last(), CreditGrant.issued_at, CreditGrant.id)
        .with_for_update()
        .populate_existing()
        .all()
    )
    remaining = credits
    for index, grant in enumerate(lots):
        allocated = min(remaining, grant.remaining_credits)
        grant.remaining_credits -= allocated
        db.add(
            CreditAllocation(
                id=public_id("ca"),
                reservation_id=row.id,
                grant_id=grant.id,
                ordinal=index,
                quantity=allocated,
                committed_credits=0,
                released_credits=0,
            )
        )
        remaining -= allocated
        if remaining == 0:
            break
    if remaining:
        raise CreditContractError("Available credit lots require reconciliation.")
    _event(
        db,
        account,
        key=f"reserve:{row.id}",
        kind="reserve",
        amount=-credits,
        source_type="credit_reservation",
        source_id=row.id,
        stamp=stamp,
    )
    return row


def settle_reservation(
    db: Session,
    *,
    user_id: int,
    reservation_id: str,
    committed_credits: int,
    now: datetime | None = None,
) -> CreditReservation:
    """Commit delivered credits and restore the remainder once, without losing expiry."""
    _count(committed_credits)
    stamp, account = _stamp(now), _lock_account(db, user_id)
    row = (
        db.query(CreditReservation)
        .filter_by(id=reservation_id, user_id=user_id)
        .with_for_update()
        .populate_existing()
        .one_or_none()
    )
    if row is None:
        raise CreditContractError("Credit reservation not found.")
    if row.state == "settled":
        if row.committed_credits != committed_credits:
            raise CreditContractError("This reservation already has a different final outcome.")
        return row
    if committed_credits > row.reserved_credits:
        raise CreditContractError("The delivered amount exceeds the accepted reservation.")
    _expire(db, account, stamp)
    allocations = (
        db.query(CreditAllocation)
        .filter_by(reservation_id=row.id)
        .order_by(CreditAllocation.ordinal)
        .with_for_update()
        .populate_existing()
        .all()
    )
    if sum(a.quantity for a in allocations) != row.reserved_credits:
        raise CreditContractError("Credit allocation history requires reconciliation.")
    remaining = committed_credits
    for allocation in allocations:
        grant = (
            db.query(CreditGrant)
            .filter_by(id=allocation.grant_id, user_id=user_id)
            .with_for_update()
            .populate_existing()
            .one()
        )
        consumed = min(remaining, allocation.quantity)
        released = allocation.quantity - consumed
        allocation.committed_credits, allocation.released_credits = consumed, released
        remaining -= consumed
        if released:
            expiry = (
                max(aware(grant.expires_at), stamp + RESTORATION_LIFETIME)
                if grant.expires_at
                else None
            )
            _grant(
                db,
                account,
                quantity=released,
                source_type="reservation_release",
                source_id=allocation.id,
                funding_kind=grant.funding_kind,
                terms_snapshot=dict(grant.terms_snapshot),
                expires_at=expiry,
                stamp=stamp,
                origin_type=grant.origin_type,
                origin_id=grant.origin_id,
                parent_grant_id=grant.id,
            )
    row.committed_credits, row.released_credits = (
        committed_credits,
        row.reserved_credits - committed_credits,
    )
    row.state, row.settled_at = "settled", stamp
    _event(
        db,
        account,
        key=f"settle:{row.id}",
        kind="settle",
        amount=0,
        source_type="credit_reservation",
        source_id=row.id,
        stamp=stamp,
        details={"committed_credits": committed_credits, "released_credits": row.released_credits},
    )
    return row


def revoke_origin(
    db: Session,
    *,
    user_id: int,
    origin_type: str,
    origin_id: str,
    target_credits: int,
    now: datetime | None = None,
) -> int:
    """Apply cumulative verified cash-refund revocation, including restored lots.

    Already expired unused credits absorb revocation without creating debt. Spent
    or reserved refunded credits become debt; a later failure restoration pays
    that debt before providing usable credits. Provider expenses are unaffected.
    """
    _count(target_credits)
    stamp, account = _stamp(now), _lock_account(db, user_id)
    _expire(db, account, stamp)
    grants = (
        db.query(CreditGrant)
        .filter_by(user_id=user_id, origin_type=origin_type, origin_id=origin_id)
        .order_by(
            CreditGrant.expires_at.asc().nulls_last(),
            CreditGrant.issued_at,
            CreditGrant.id,
        )
        .with_for_update()
        .populate_existing()
        .all()
    )
    root = [g for g in grants if g.parent_grant_id is None]
    if len(root) != 1 or target_credits > root[0].quantity:
        raise CreditContractError("The refund does not match an original credit grant.")
    previous = (
        db.query(CreditEvent)
        .filter_by(
            user_id=user_id, origin_type=origin_type, origin_id=origin_id, event_type="revoke"
        )
        .all()
    )
    revoked = max((int(e.details["target_credits"]) for e in previous), default=0)
    if target_credits < revoked:
        raise CreditContractError("A verified refund cannot move backwards.")
    delta = target_credits - revoked
    if not delta:
        return 0
    remaining = delta
    for grant in grants:
        removed = min(remaining, grant.remaining_credits)
        grant.remaining_credits -= removed
        remaining -= removed
    expired_available = sum(g.expired_credits for g in grants) - sum(
        int(e.details.get("expired_offset", 0)) for e in previous
    )
    expired_offset = min(remaining, max(0, expired_available))
    remaining -= expired_offset
    account.debt_credits += remaining
    # An expiry already debited unused credits. Record its absorption in details
    # without debiting those same credits twice in the reconstructible ledger.
    _event(
        db,
        account,
        key=f"revoke:{root[0].id}:{target_credits}",
        kind="revoke",
        amount=-(delta - expired_offset),
        source_type="credit_grant",
        source_id=root[0].id,
        origin_type=origin_type,
        origin_id=origin_id,
        stamp=stamp,
        details={
            "target_credits": target_credits,
            "expired_offset": expired_offset,
            "debt_added": remaining,
        },
    )
    return delta
