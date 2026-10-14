import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from ..billing.catalog import CATALOG_VERSION, get_product, product_purchase_enabled, public_catalog
from ..billing.cost_policy import (
    CostPolicyUnavailable,
    assert_actual_variance,
    current_policy,
    service_prices,
)
from ..billing.purchase_quote import (
    BillingOrderStatusResponse,
    CheckoutOrderResponse,
    PurchaseQuoteAcceptance,
    order_quote,
    product_quote,
    purchase_request_id,
    validate_purchase_quote,
)
from ..billing.razorpay import RazorpayAdapter, RazorpayProviderError, RazorpaySettings
from ..billing.reconciliation import (
    OriginalOrder,
    ReconciliationResponse,
    ReconciliationUnavailable,
    fetch_reconciliation,
)
from ..billing.service import (
    WebhookValidationError,
    _process_capture,
    _process_refund,
    as_aware,
    process_razorpay_webhook,
)
from ..database import get_db
from ..models import EntitlementLedger, PaymentEvent, PaymentOrder, PaymentTransaction, User
from ..rate_limiter import limiter
from ..security import get_current_user

router = APIRouter(prefix="/billing", tags=["billing"])
public_router = APIRouter(prefix="/public/billing", tags=["public-billing"])
log = logging.getLogger("hirewiz.billing")

UNCERTAIN_ORDER_STATUSES = {"initializing", "creation_unknown", "create_failed"}
OPEN_ORDER_STATUSES = UNCERTAIN_ORDER_STATUSES | {"created", "client_confirmed", "payment_failed"}
MAX_WEBHOOK_BYTES = 1_000_000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, private"
    response.headers["Pragma"] = "no-cache"


class CreateOrderRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku: str = Field(min_length=1, max_length=64)
    billing_country: Literal["IN"]
    # Missing acceptance never authorizes payable checkout, including recovery.
    # Free status reads expose the original terms for another explicit review.
    accepted_quote: PurchaseQuoteAcceptance | None = Field(default=None, description="Required before payable checkout, including recovery. Free status reads expose the original terms for review.")
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$", description="Required for new orders; reuse this identity and the original accepted quote after an interrupted response.")
    order_reference: str | None = Field(default=None, min_length=5, max_length=120, description="An owned original order to resume without creating another order.")


class CheckoutResultRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    razorpay_payment_id: str = Field(min_length=5, max_length=120)
    razorpay_order_id: str = Field(min_length=5, max_length=120)
    razorpay_signature: str = Field(min_length=16, max_length=256)


def _settings() -> RazorpaySettings:
    return RazorpaySettings.from_env()


def _catalog_payload() -> dict:
    return public_catalog(checkout_enabled=_settings().checkout_enabled)


@public_router.get("/catalog")
def get_public_catalog(response: Response):
    # The response carries the emergency checkout-enable flag, so neither a
    # browser nor an intermediary may reuse a stale enabled response.
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return _catalog_payload()


@router.get("/config")
def get_billing_config(
    response: Response,
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    return _catalog_payload()


def _checkout_response(
    *,
    order: PaymentOrder,
    current_user: User,
    settings: RazorpaySettings,
) -> dict:
    if order.provider_mode != settings.mode or order.provider_key_id != settings.key_id:
        raise HTTPException(status_code=409, detail="This checkout attempt is no longer available.")
    if not order.provider_order_id:
        raise HTTPException(status_code=409, detail="Checkout initialization is still in progress.")
    quote = order_quote(order)
    description = (f"{order.entitlement_quantity} days of Premium access. One-time purchase; no automatic renewal."
                   if order.entitlement_kind == "premium_access" else
                   f"{quote.job_service_credits} job-service credits and {quote.analysis_units} AI analysis units. Finite prepaid balances; no subscription or unlimited usage.")
    return {
        "order_id": order.public_id,
        "provider": "razorpay",
        "provider_order_id": order.provider_order_id,
        "key_id": settings.key_id,
        "amount_minor": order.gross_amount_minor,
        "currency": order.currency,
        "name": "HireWiz",
        "description": description,
        "accepted_quote": quote.model_dump(),
        "prefill": {"email": current_user.email},
    }


def _order_status_required(order, message="Check this original checkout's payment status before continuing."):
    return HTTPException(409, {"code": "billing_order_requires_status", "order_id": order.public_id,
                               "message": message + " No new order was created."})


@router.post("/orders", response_model=CheckoutOrderResponse)
@limiter.limit("5/minute")
def create_order(
    payload: CreateOrderRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    user = db.query(User).filter(User.id == current_user.id).with_for_update().first()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    reference = payload.order_reference
    request_key = payload.idempotency_key
    identity = reference or (purchase_request_id(user.id, request_key) if request_key else None)
    original = db.query(PaymentOrder).filter(PaymentOrder.user_id == user.id,
        PaymentOrder.public_id == identity).with_for_update().first() if identity else None
    if reference and original is None:
        raise HTTPException(404, "Order not found.")
    if original is not None:
        if original.sku != payload.sku or payload.accepted_quote != order_quote(original):
            raise _order_status_required(original, "This request belongs to different original pack terms. Review the original order.")
        if original.status in UNCERTAIN_ORDER_STATUSES:
            raise _order_status_required(original, "Checkout creation has an unknown outcome. Contact billing support to reconcile this original attempt before another purchase.")
        if original.status != "created" or not original.provider_order_id:
            raise _order_status_required(original)
        settings = _settings()
        if not settings.checkout_enabled:
            raise HTTPException(503, "Checkout is not available yet.")
        if original.provider_mode != settings.mode or original.provider_key_id != settings.key_id:
            raise _order_status_required(original, "The original checkout configuration changed. Contact billing support before resuming this order.")
        return _checkout_response(order=original, current_user=user, settings=settings)
    product = get_product(payload.sku)
    if product is None:
        raise HTTPException(status_code=400, detail="This product is not available.")

    settings = _settings()
    if not settings.checkout_enabled:
        # Fail closed. Never reveal which key/approval/configuration item is
        # absent, and never fall back to a mock payment.
        raise HTTPException(status_code=503, detail="Checkout is not available yet.")
    if product.entitlement_kind == "premium_access" and user.is_premium_active():
        raise HTTPException(status_code=409, detail="Premium access is already active.")

    open_orders = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.user_id == user.id,
            PaymentOrder.status.in_(OPEN_ORDER_STATUSES),
            or_(PaymentOrder.status.in_(UNCERTAIN_ORDER_STATUSES), and_(
                PaymentOrder.sku == product.sku, PaymentOrder.provider_mode == settings.mode,
            )),
        )
        .order_by(PaymentOrder.created_at.desc())
        .with_for_update()
        .all()
    )
    abandoned = []
    for existing in open_orders:
        if existing.status in UNCERTAIN_ORDER_STATUSES:
            # Age, a changed pack/key/mode, and a missing response cannot prove
            # that the provider created no payable order. Keep the attempt held.
            raise _order_status_required(existing, "Checkout creation has an unknown outcome. Contact billing support to reconcile this original attempt before another purchase.")
        if existing.provider_order_id:
            # A standard provider order has no safe local cancel primitive and
            # an old Checkout popup can remain payable. Block instead of
            # exposing a second payable order after catalog or key changes.
            raise _order_status_required(existing, "An earlier checkout requires review before another purchase.")
        abandoned.append(existing)
    if not product_purchase_enabled(product, checkout_enabled=settings.checkout_enabled):
        raise HTTPException(status_code=503, detail="This service purchase is not available yet.")
    validate_purchase_quote(payload.accepted_quote, product_quote(product, CATALOG_VERSION))
    if not request_key:
        raise HTTPException(409, {"code": "billing_request_requires_review",
            "message": "Refresh the purchase review before creating checkout. A new order requires an accepted quote and request identity."})
    for existing in abandoned:
        existing.status = "abandoned"
        existing.active_attempt_key = None
    if abandoned:
        db.commit()

    try:
        authority = service_prices()["cost_policy_snapshot"]
        assert_actual_variance(db, current_policy())
    except CostPolicyUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    authority = authority | {"entitlements": {"job_service_credits": product.entitlement_quantity,
                                                "analysis_units": product.analysis_units}}
    public_id = purchase_request_id(user.id, request_key)
    receipt = f"hw_{public_id}"
    if len(receipt) > 40:
        raise HTTPException(status_code=500, detail="Could not initialize checkout.")
    order = PaymentOrder(
        public_id=public_id,
        user_id=user.id,
        provider="razorpay",
        provider_mode=settings.mode,
        provider_key_id=settings.key_id,
        sku=product.sku,
        catalog_version=CATALOG_VERSION,
        cost_policy_snapshot=authority,
        billing_type=product.billing_type,
        entitlement_kind=product.entitlement_kind,
        entitlement_quantity=product.entitlement_quantity,
        billing_country=payload.billing_country,
        billing_country_confirmed_at=_utcnow(),
        gross_amount_minor=product.amount_minor,
        refunded_amount_minor=0,
        currency=product.currency,
        status="initializing",
        active_attempt_key=f"{user.id}:{product.sku}:{settings.mode}",
    )
    db.add(order)
    try:
        db.commit()
        db.refresh(order)
    except IntegrityError:
        db.rollback()
        concurrent = (
            db.query(PaymentOrder)
            .filter(
                PaymentOrder.user_id == user.id,
                PaymentOrder.active_attempt_key == f"{user.id}:{product.sku}:{settings.mode}",
            )
            .first()
        )
        if concurrent:
            raise _order_status_required(concurrent) from None
        raise HTTPException(status_code=409, detail="Checkout initialization is in progress.")

    try:
        provider_result = RazorpayAdapter(settings).create_order(
            product=product,
            local_order_id=order.public_id,
            receipt=receipt,
        )
    except RazorpayProviderError:
        log.warning("Razorpay order creation outcome is unknown for local order %s", order.public_id)
        order.status = "creation_unknown"
        # The durable attempt key stays reserved until provider proof resolves it.
        db.add(order)
        db.commit()
        raise _order_status_required(order, "Checkout creation has an unknown outcome. Contact billing support to reconcile this original attempt before another purchase.") from None

    order.provider_order_id = provider_result["provider_order_id"]
    order.status = "created"
    db.add(order)
    try:
        db.commit()
        db.refresh(order)
    except IntegrityError:
        db.rollback()
        log.exception("Duplicate provider order identifier for local order %s", order.public_id)
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")
    return _checkout_response(
        order=order, current_user=user, settings=settings
    )


def _owned_order(db: Session, reference: str, user_id: int) -> PaymentOrder:
    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.user_id == user_id,
            PaymentOrder.provider == "razorpay",
            or_(
                PaymentOrder.public_id == reference,
                PaymentOrder.provider_order_id == reference,
            ),
        )
        .first()
    )
    if order is None:
        # A single 404 avoids disclosing whether another customer's order exists.
        raise HTTPException(status_code=404, detail="Order not found.")
    return order


@router.post("/orders/{order_reference}/checkout-result")
@limiter.limit("10/minute")
def record_checkout_result(
    order_reference: str,
    payload: CheckoutResultRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    order = _owned_order(db, order_reference, current_user.id)
    settings = _settings()
    if order.provider_mode != settings.mode or order.provider_key_id != settings.key_id:
        raise HTTPException(status_code=409, detail="This checkout attempt is no longer available.")
    if payload.razorpay_order_id != order.provider_order_id:
        raise HTTPException(status_code=400, detail="Invalid checkout confirmation.")
    if not payload.razorpay_payment_id.startswith("pay_"):
        raise HTTPException(status_code=400, detail="Invalid checkout confirmation.")

    if not RazorpayAdapter(settings).verify_checkout_signature(
        provider_order_id=payload.razorpay_order_id,
        provider_payment_id=payload.razorpay_payment_id,
        signature=payload.razorpay_signature,
    ):
        raise HTTPException(status_code=400, detail="Invalid checkout confirmation.")

    transaction = (
        db.query(PaymentTransaction)
        .filter(
            PaymentTransaction.provider == "razorpay",
            PaymentTransaction.provider_payment_id == payload.razorpay_payment_id,
        )
        .first()
    )
    if transaction and transaction.order_id != order.id:
        raise HTTPException(status_code=400, detail="Invalid checkout confirmation.")
    if transaction is None:
        db.add(
            PaymentTransaction(
                order_id=order.id,
                provider="razorpay",
                provider_payment_id=payload.razorpay_payment_id,
                entity_type="payment",
                status="client_confirmed",
                gross_amount_minor=order.gross_amount_minor,
                currency=order.currency,
            )
        )
    if order.status in {"created", "client_confirmed"}:
        order.status = "client_confirmed"
        order.client_confirmed_at = order.client_confirmed_at or _utcnow()
    db.add(order)
    db.commit()
    # Signature verification is evidence only. A signed browser result is not
    # proof of capture and cannot provision an entitlement.
    return {"accepted": True, "status": "pending", "fulfilled": False}


def _normalized_order_status(order: PaymentOrder) -> str:
    if order.status in {
        "initializing",
        "creation_unknown",
        "create_failed",
        "created",
        "client_confirmed",
        "paid_unfulfilled",
    }:
        return "pending"
    if order.status in {
        "payment_failed",
        "customer_deleted",
        "abandoned",
    }:
        return "failed"
    if order.status in {"paid", "partially_refunded"}:
        return "paid"
    if order.status == "refunded":
        return "refunded"
    return "failed"


def _order_status_payload(db: Session, order: PaymentOrder) -> dict:
    entitlement = (
        db.query(EntitlementLedger)
        .filter(
            EntitlementLedger.source_order_id == order.id,
            EntitlementLedger.status == "active",
        )
        .first()
    )
    fulfilled = bool(entitlement)
    if entitlement and entitlement.expires_at:
        expires_at = as_aware(entitlement.expires_at)
        fulfilled = bool(expires_at and expires_at > _utcnow())
    transaction = (
        db.query(PaymentTransaction)
        .filter(PaymentTransaction.order_id == order.id)
        .order_by(PaymentTransaction.id.desc())
        .first()
    )
    try:
        accepted_quote = order_quote(order).model_dump()
    except HTTPException:
        accepted_quote = None  # Status remains readable when original terms need support review.
    return {
        "order_id": order.public_id,
        "provider_order_id": order.provider_order_id,
        "checkout_available": bool(order.provider_order_id and order.status == "created"),
        "payment_reference": transaction.provider_payment_id if transaction else None,
        "sku": order.sku,
        "entitlement_kind": order.entitlement_kind,
        "entitlement_quantity": order.entitlement_quantity,
        "status": _normalized_order_status(order),
        "fulfilled": fulfilled,
        "amount_minor": order.gross_amount_minor,
        "refunded_amount_minor": int(order.refunded_amount_minor or 0),
        "currency": order.currency,
        "created_at": order.created_at,
        "paid_at": order.paid_at,
        "refunded_at": order.refunded_at,
        "accepted_quote": accepted_quote,
    }


@router.get("/recent-order", response_model=BillingOrderStatusResponse | None)
@limiter.limit("30/minute")
def get_recent_order(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict | None:
    _no_store(response)
    cutoff = _utcnow() - timedelta(hours=24)
    order = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.user_id == current_user.id,
            PaymentOrder.provider == "razorpay",
            PaymentOrder.created_at >= cutoff,
        )
        .order_by(PaymentOrder.created_at.desc())
        .first()
    )
    if order is None:
        return None
    return _order_status_payload(db, order)


@router.get("/orders/{order_reference}", response_model=BillingOrderStatusResponse)
@limiter.limit("60/minute")
def get_order_status(
    order_reference: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    order = _owned_order(db, order_reference, current_user.id)
    return _order_status_payload(db, order)


def _reconciliation_reply(db, order, resolution="unresolved", message=None):
    return {"resolution": resolution,
            "message": message or "The original attempt remains unresolved. Payment stays paused; contact billing support. No new order was created.",
            "order": _order_status_payload(db, order)}


@router.post("/orders/{order_reference}/reconcile", response_model=ReconciliationResponse)
@limiter.limit("3/minute")
def reconcile_order(
    order_reference: str,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Free explicit provider reads; never CREATE, capture, refund or open Checkout."""
    _no_store(response)
    owner_id = current_user.id
    order = _owned_order(db, order_reference, owner_id)
    order_reference = order.public_id
    terminal = {"paid", "partially_refunded", "refunded"}
    if order.status in terminal:
        return _reconciliation_reply(db, order, "settled", "The original payment outcome is recorded. Review its status.")
    if order.status == "paid_unfulfilled":
        return _reconciliation_reply(db, order, message="The original payment is recorded, but allowance fulfilment needs billing support. No new order was created.")
    try:
        original = OriginalOrder.from_order(order)
    except HTTPException:
        return _reconciliation_reply(db, order)
    settings = _settings()
    # End the read transaction before bounded network reads. Reacquire owner and
    # order locks, with refreshed rows, before any binding or accounting write.
    db.rollback()
    try:
        proof = fetch_reconciliation(original, settings)
    except ReconciliationUnavailable:
        return _reconciliation_reply(db, _owned_order(db, order_reference, owner_id))

    try:
        # Match settlement's order-before-owner lock order. NOWAIT also avoids
        # waiting on admission's owner lock while holding the original order.
        order = (db.query(PaymentOrder)
                 .filter(PaymentOrder.user_id == owner_id, PaymentOrder.public_id == order_reference)
                 .populate_existing().with_for_update(nowait=True).first())
        if order is None:
            raise HTTPException(404, "Order not found.")
        user = db.query(User).filter(User.id == owner_id).with_for_update(nowait=True).first()
        if user is None:
            raise HTTPException(404, "Order not found.")
    except OperationalError:
        db.rollback()
        return _reconciliation_reply(db, _owned_order(db, order_reference, owner_id))
    if order.status in terminal:
        return _reconciliation_reply(db, order, "settled", "The original payment outcome was already recorded. Review its status.")
    if order.status == "paid_unfulfilled":
        return _reconciliation_reply(db, order, message="The original payment is recorded, but allowance fulfilment needs billing support. No new order was created.")
    try:
        if OriginalOrder.from_order(order) != original:
            raise ReconciliationUnavailable("original_terms_changed")
        provider_id = proof.order["id"]
        bound = (db.query(PaymentOrder).filter(PaymentOrder.provider_order_id == provider_id)
                 .first())
        if (bound is not None and bound.id != order.id) or order.provider_order_id not in (None, provider_id):
            raise ReconciliationUnavailable("provider_order_already_bound")
        digest = proof.digest(original)
        event_id = "get:" + original.public_id + ":" + digest
        event = (db.query(PaymentEvent)
                 .filter(PaymentEvent.provider == "razorpay_api", PaymentEvent.provider_event_id == event_id)
                 .with_for_update().first())
        if event is not None and (event.order_id != order.id or event.payload_sha256 != digest):
            raise ReconciliationUnavailable("api_proof_ownership_mismatch")
        if event is None:
            event = PaymentEvent(provider="razorpay_api", provider_event_id=event_id,
                                 event_type="api.order_reconciliation", payload_sha256=digest,
                                 order_id=order.id, processing_status="received", provider_created_at=None)
            db.add(event)
        order.provider_order_id = provider_id
        db.add(order)
        db.flush()  # Existing unique provider-ID constraint protects a binding race.
        if proof.payment is not None:
            payload = {"payload": {"order": {"entity": proof.order}, "payment": {"entity": proof.payment}}}
            if proof.refunds:
                # Use actual refunded/captured payment and processed refund facts.
                # Never fabricate a historical payment.captured notification.
                for refund in proof.refunds:
                    payload["payload"]["refund"] = {"entity": refund}
                    if _process_refund(db, payload, event, original.provider_mode) != order.public_id:
                        raise ReconciliationUnavailable("unmapped_refund_proof")
            elif _process_capture(db, payload, event, "order.paid", original.provider_mode) != order.public_id:
                raise ReconciliationUnavailable("unmapped_capture_proof")
        elif proof.checkout_ready and order.status in UNCERTAIN_ORDER_STATUSES:
            order.status = "created"
        elif not proof.checkout_ready and order.status in OPEN_ORDER_STATUSES:
            # Positive attempted/authorized/failed-only proof still has no final
            # payment outcome. Keep Resume paused across later free reads.
            order.status = "creation_unknown"
        event.processing_status = "processed"
        event.processed_at = _utcnow()
        db.commit()
    except (ReconciliationUnavailable, WebhookValidationError, IntegrityError, OperationalError, HTTPException):
        db.rollback()
        return _reconciliation_reply(db, _owned_order(db, order_reference, owner_id))
    resolution = "settled" if order.status in terminal else "checkout_ready" if proof.checkout_ready and order.status == "created" else "unresolved"
    message = ("Original checkout recovered. Review its original allowances and confirm again before resuming."
               if resolution == "checkout_ready" else "The original payment outcome is recorded. Review its status."
               if resolution == "settled" else None)
    return _reconciliation_reply(db, order, resolution, message)


def _end_premium_access(db: Session, user: User) -> dict:
    now = _utcnow()
    entitlements = (
        db.query(EntitlementLedger)
        .filter(
            EntitlementLedger.user_id == user.id,
            EntitlementLedger.entitlement_kind == "premium_access",
            EntitlementLedger.status == "active",
        )
        .with_for_update()
        .all()
    )
    for entitlement in entitlements:
        entitlement.status = "ended_by_user"
        entitlement.revoked_at = now
    user.tier = "free"
    user.premium_until = None
    db.add(user)
    db.commit()
    return {"status": "ended", "auto_renews": False}


@router.post("/end-premium")
def end_premium(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    return _end_premium_access(db, current_user)


@router.post("/cancel-subscription", deprecated=True)
def deprecated_cancel_subscription(
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Temporary compatibility alias. There is no subscription or mandate.
    _no_store(response)
    response.headers["Deprecation"] = "true"
    return _end_premium_access(db, current_user)


@router.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    raw_body = await request.body()
    if len(raw_body) > MAX_WEBHOOK_BYTES:
        raise HTTPException(status_code=413, detail="Webhook payload is too large.")
    settings = _settings()
    if not settings.webhook_ready:
        # Missing verification configuration must never accept or process data.
        raise HTTPException(status_code=503, detail="Webhook is not configured.")

    signature = request.headers.get("x-razorpay-signature", "")
    event_id = request.headers.get("x-razorpay-event-id", "").strip()
    if not RazorpayAdapter(settings).verify_webhook_signature(
        raw_body=raw_body, signature=signature
    ):
        log.warning("Rejected Razorpay webhook with an invalid signature")
        raise HTTPException(status_code=400, detail="Invalid webhook signature.")
    if not event_id:
        raise HTTPException(status_code=400, detail="Missing webhook event identifier.")

    try:
        result = process_razorpay_webhook(
            db,
            raw_body=raw_body,
            provider_event_id=event_id,
            provider_mode=settings.mode,
        )
    except WebhookValidationError as exc:
        log.warning("Rejected Razorpay webhook event %s (%s)", event_id, exc.code)
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")
    if result.status in {"retryable_unknown_order", "retryable_unknown_payment"}:
        log.error("Razorpay webhook %s is unmatched and requires retry/reconciliation", event_id)
        raise HTTPException(status_code=503, detail="Webhook cannot be reconciled yet.")
    return {"ok": True, "status": result.status}
