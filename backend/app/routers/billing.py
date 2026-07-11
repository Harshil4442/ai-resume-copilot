import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..billing.catalog import CATALOG_VERSION, CatalogProduct, get_product, public_catalog
from ..billing.razorpay import RazorpayAdapter, RazorpayProviderError, RazorpaySettings
from ..billing.service import WebhookValidationError, as_aware, process_razorpay_webhook
from ..database import get_db
from ..models import EntitlementLedger, PaymentOrder, PaymentTransaction, User
from ..rate_limiter import limiter
from ..security import get_current_user


router = APIRouter(prefix="/billing", tags=["billing"])
public_router = APIRouter(prefix="/public/billing", tags=["public-billing"])
log = logging.getLogger("hirewiz.billing")

OPEN_ORDER_STATUSES = {"initializing", "created", "client_confirmed", "payment_failed"}
OPEN_ORDER_REUSE_TTL = timedelta(minutes=30)
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
    product: CatalogProduct,
    current_user: User,
    settings: RazorpaySettings,
) -> dict:
    if order.provider_mode != settings.mode or order.provider_key_id != settings.key_id:
        raise HTTPException(status_code=409, detail="This checkout attempt is no longer available.")
    if not order.provider_order_id:
        raise HTTPException(status_code=409, detail="Checkout initialization is still in progress.")
    return {
        "order_id": order.public_id,
        "provider": "razorpay",
        "provider_order_id": order.provider_order_id,
        "key_id": settings.key_id,
        "amount_minor": order.gross_amount_minor,
        "currency": order.currency,
        "name": "HireWiz",
        "description": product.description,
        "prefill": {"email": current_user.email},
    }


@router.post("/orders")
@limiter.limit("5/minute")
def create_order(
    payload: CreateOrderRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _no_store(response)
    product = get_product(payload.sku)
    if product is None:
        raise HTTPException(status_code=400, detail="This product is not available.")

    settings = _settings()
    if not settings.checkout_enabled:
        # Fail closed. Never reveal which key/approval/configuration item is
        # absent, and never fall back to a mock payment.
        raise HTTPException(status_code=503, detail="Checkout is not available yet.")

    user = db.query(User).filter(User.id == current_user.id).with_for_update().first()
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if user.is_premium_active():
        raise HTTPException(status_code=409, detail="Premium access is already active.")

    open_orders = (
        db.query(PaymentOrder)
        .filter(
            PaymentOrder.user_id == user.id,
            PaymentOrder.sku == product.sku,
            PaymentOrder.provider_mode == settings.mode,
            PaymentOrder.status.in_(OPEN_ORDER_STATUSES),
        )
        .order_by(PaymentOrder.created_at.desc())
        .with_for_update()
        .all()
    )
    reuse_cutoff = _utcnow() - OPEN_ORDER_REUSE_TTL
    for existing in open_orders:
        created_at = as_aware(existing.created_at)
        if (
            existing.status == "initializing"
            and created_at
            and created_at >= reuse_cutoff
        ):
            # Another request owns the provider call for this unique active
            # attempt. Never abandon it while that call can still return: doing
            # so could expose two separately payable provider orders.
            raise HTTPException(
                status_code=409,
                detail="Checkout initialization is already in progress. Please retry shortly.",
            )
        reusable = bool(
            existing.provider_order_id
            and existing.catalog_version == CATALOG_VERSION
            and existing.gross_amount_minor == product.amount_minor
            and existing.currency == product.currency
            and existing.billing_type == product.billing_type
            and existing.provider_mode == settings.mode
            and existing.provider_key_id == settings.key_id
        )
        if reusable:
            return _checkout_response(
                order=existing, product=product, current_user=user, settings=settings
            )
        if existing.provider_order_id:
            # A standard provider order has no safe local cancel primitive and
            # an old Checkout popup can remain payable. Block instead of
            # exposing a second payable order after catalog or key changes.
            raise HTTPException(
                status_code=409,
                detail="An earlier checkout requires reconciliation before a new order can be created.",
            )
        existing.status = "abandoned"
        existing.active_attempt_key = None
    if open_orders:
        db.commit()

    public_id = f"ord_{uuid.uuid4().hex}"
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
        if concurrent and concurrent.provider_order_id:
            return _checkout_response(
                order=concurrent, product=product, current_user=user, settings=settings
            )
        raise HTTPException(status_code=409, detail="Checkout initialization is in progress.")

    try:
        provider_result = RazorpayAdapter(settings).create_order(
            product=product,
            local_order_id=order.public_id,
            receipt=receipt,
        )
    except RazorpayProviderError:
        log.exception("Razorpay order creation failed for local order %s", order.public_id)
        order.status = "create_failed"
        order.active_attempt_key = None
        db.add(order)
        db.commit()
        raise HTTPException(status_code=502, detail="Could not start checkout. Please try again.")

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
        order=order, product=product, current_user=user, settings=settings
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
        "created",
        "client_confirmed",
        "paid_unfulfilled",
    }:
        return "pending"
    if order.status in {
        "create_failed",
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
    return {
        "order_id": order.public_id,
        "provider_order_id": order.provider_order_id,
        "payment_reference": transaction.provider_payment_id if transaction else None,
        "sku": order.sku,
        "status": _normalized_order_status(order),
        "fulfilled": fulfilled,
        "amount_minor": order.gross_amount_minor,
        "refunded_amount_minor": int(order.refunded_amount_minor or 0),
        "currency": order.currency,
        "created_at": order.created_at,
        "paid_at": order.paid_at,
        "refunded_at": order.refunded_at,
    }


@router.get("/recent-order")
@limiter.limit("30/minute")
def get_recent_order(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
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
        raise HTTPException(status_code=404, detail="No recent order found.")
    return _order_status_payload(db, order)


@router.get("/orders/{order_reference}")
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
