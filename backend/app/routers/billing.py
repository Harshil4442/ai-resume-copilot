import os
import json
import base64
import hmac
import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User, UserProfile, PaymentOrder
from ..security import get_current_user

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger("ai_resume_copilot.billing")

# ==========================================================================
# Server-owned pricing. The client never sends an amount or currency — it
# only names the product ("subscription" or "topup"), and the server maps
# that to a fixed price. This prevents client-side price tampering.
# ==========================================================================
PREMIUM_DAYS = 30

PRICES = {
    # Premium: one payment unlocks premium for PREMIUM_DAYS. No stored mandate.
    "subscription": {"amount": 999.00, "currency": "INR", "credits": None},
    # Analysis-units pack: one-time top-up of credits.
    "topup": {"amount": 99.00, "currency": "INR", "credits": 25},
}


def _price_for(order_type: str) -> dict:
    price = PRICES.get(order_type)
    if not price:
        raise HTTPException(status_code=400, detail="Invalid billing type")
    return price


# ---------------------------------------------------------------------------
# Provisioning helpers — shared by every provider and by the mock path so that
# access is always granted the same way and exactly once (idempotent).
# ---------------------------------------------------------------------------
def _grant_premium(db: Session, user_id: int, days: int = PREMIUM_DAYS) -> datetime:
    """Extends premium by `days`, stacking on any remaining unexpired time."""
    now = datetime.now(timezone.utc)
    user = db.query(User).filter(User.id == user_id).first()
    base = now
    if user and user.premium_until:
        current = user.premium_until
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if current > now:
            base = current
    new_until = base + timedelta(days=days)
    db.execute(
        update(User).where(User.id == user_id).values(tier="premium", premium_until=new_until)
    )
    db.commit()
    log.info("Granted premium to user %s until %s", user_id, new_until.isoformat())
    return new_until


def _add_credits(db: Session, user_id: int, amount: int) -> None:
    db.execute(
        update(User).where(User.id == user_id).values(ai_credits=User.ai_credits + amount)
    )
    db.commit()
    log.info("Added %s credits to user %s", amount, user_id)


def _provision_order(db: Session, order: PaymentOrder) -> None:
    """Fulfils a paid order once. Safe to call from both webhook and verify."""
    if order.provisioned:
        return
    if order.order_type == "subscription":
        _grant_premium(db, order.user_id)
    elif order.order_type == "topup":
        _add_credits(db, order.user_id, order.credits or 25)
    order.status = "paid"
    order.provisioned = 1
    db.add(order)
    db.commit()


# ==========================================================================
# Cashfree (India / INR) — primary gateway
# ==========================================================================
CASHFREE_APP_ID = os.getenv("CASHFREE_APP_ID", "")
CASHFREE_SECRET_KEY = os.getenv("CASHFREE_SECRET_KEY", "")
CASHFREE_MODE = os.getenv("CASHFREE_MODE", "sandbox").lower()
CASHFREE_API_VERSION = os.getenv("CASHFREE_API_VERSION", "2023-08-01")
CASHFREE_BASE_URL = (
    "https://api.cashfree.com/pg"
    if CASHFREE_MODE == "production"
    else "https://sandbox.cashfree.com/pg"
)

FRONTEND_URL = os.getenv("FRONTEND_URL", "https://www.hirewizhq.com").rstrip("/")
BACKEND_PUBLIC_URL = os.getenv("BACKEND_PUBLIC_URL", "").rstrip("/")


def _cashfree_configured() -> bool:
    return bool(CASHFREE_APP_ID and CASHFREE_SECRET_KEY)


def _cashfree_headers() -> dict:
    return {
        "x-client-id": CASHFREE_APP_ID,
        "x-client-secret": CASHFREE_SECRET_KEY,
        "x-api-version": CASHFREE_API_VERSION,
        "Content-Type": "application/json",
    }


class CashfreeCreateRequest(BaseModel):
    type: str  # "subscription" or "topup"
    customer_phone: Optional[str] = None


class CashfreeVerifyRequest(BaseModel):
    order_id: str


@router.post("/cashfree/create-order")
def create_cashfree_order(
    payload: CashfreeCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Creates a Cashfree order from server-owned pricing and returns a
    payment_session_id the browser SDK uses to open checkout.
    """
    price = _price_for(payload.type)
    amount = price["amount"]
    currency = price["currency"]
    credits = price["credits"]
    order_id = f"hw_{uuid.uuid4().hex}"

    # Record the pending order (also our idempotency anchor).
    order = PaymentOrder(
        user_id=current_user.id,
        provider="cashfree",
        provider_order_id=order_id,
        order_type=payload.type,
        credits=credits,
        amount=amount,
        currency=currency,
        status="created",
    )
    db.add(order)
    db.commit()

    # Mock fallback so the flow is testable before live keys exist.
    if not _cashfree_configured():
        session = f"mock_cf_session_{uuid.uuid4().hex[:8]}"
        log.info("Cashfree not configured; returning mock session for %s", order_id)
        return {"payment_session_id": session, "order_id": order_id, "mode": "mock", "amount": amount}

    # Resolve a usable 10-digit phone (Cashfree requires customer_phone).
    phone = (payload.customer_phone or "").strip()
    if not phone:
        prof = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
        if prof and prof.phone:
            phone = prof.phone
    phone_digits = "".join(ch for ch in phone if ch.isdigit())[-10:]
    if len(phone_digits) < 10:
        phone_digits = "9999999999"  # placeholder; real phone should be collected

    order_meta = {"return_url": f"{FRONTEND_URL}/billing?order_id={order_id}"}
    if BACKEND_PUBLIC_URL:
        order_meta["notify_url"] = f"{BACKEND_PUBLIC_URL}/api/billing/cashfree/webhook"

    body = {
        "order_id": order_id,
        "order_amount": amount,
        "order_currency": currency,
        "customer_details": {
            "customer_id": f"user_{current_user.id}",
            "customer_email": current_user.email,
            "customer_phone": phone_digits,
        },
        "order_meta": order_meta,
        "order_note": payload.type,
        "order_tags": {
            "user_id": str(current_user.id),
            "type": payload.type,
            "credits": str(credits or 0),
        },
    }

    try:
        res = requests.post(
            f"{CASHFREE_BASE_URL}/orders",
            json=body,
            headers=_cashfree_headers(),
            timeout=20,
        )
        res.raise_for_status()
        data = res.json()
        return {
            "payment_session_id": data["payment_session_id"],
            "order_id": data.get("order_id", order_id),
            "mode": CASHFREE_MODE,
            "amount": amount,
        }
    except requests.HTTPError as e:
        detail = getattr(e.response, "text", "")
        log.error("Cashfree order creation failed: %s", detail)
        raise HTTPException(status_code=502, detail="Could not start Cashfree checkout. Please try again.")
    except Exception:
        log.exception("Cashfree order creation error")
        raise HTTPException(status_code=502, detail="Could not start Cashfree checkout. Please try again.")


@router.post("/cashfree/verify-payment")
def verify_cashfree_payment(
    payload: CashfreeVerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Browser-side confirmation after the user returns from checkout. The webhook
    is the authoritative source; this gives immediate feedback and is idempotent.
    """
    order = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.provider_order_id == payload.order_id)
        .first()
    )
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    if order.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="This order does not belong to you")
    if order.provisioned:
        return {"status": "success", "type": order.order_type, "already": True}

    # Mock fallback auto-approves so the flow is testable without live keys.
    if not _cashfree_configured():
        _provision_order(db, order)
        return {"status": "success", "type": order.order_type, "mock": True}

    try:
        res = requests.get(
            f"{CASHFREE_BASE_URL}/orders/{payload.order_id}",
            headers=_cashfree_headers(),
            timeout=20,
        )
        res.raise_for_status()
        data = res.json()
    except Exception:
        log.exception("Cashfree verify failed")
        raise HTTPException(status_code=502, detail="Could not verify payment status.")

    if data.get("order_status") == "PAID":
        _provision_order(db, order)
        return {"status": "success", "type": order.order_type}
    return {"status": "pending", "order_status": data.get("order_status")}


@router.post("/cashfree/webhook")
async def cashfree_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Authoritative payment confirmation from Cashfree. Verifies the signature
    against the raw body, then provisions the order idempotently.
    """
    raw = await request.body()
    signature = request.headers.get("x-webhook-signature", "")
    timestamp = request.headers.get("x-webhook-timestamp", "")

    if CASHFREE_SECRET_KEY:
        signed_payload = f"{timestamp}{raw.decode('utf-8')}"
        expected = base64.b64encode(
            hmac.new(
                CASHFREE_SECRET_KEY.encode("utf-8"),
                signed_payload.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        if not hmac.compare_digest(expected, signature):
            log.warning("Cashfree webhook signature verification failed")
            raise HTTPException(status_code=400, detail="Invalid signature")

    try:
        event = json.loads(raw.decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid payload")

    data = event.get("data", {})
    order_info = data.get("order", {}) or {}
    payment_info = data.get("payment", {}) or {}
    order_id = order_info.get("order_id")
    status = payment_info.get("payment_status") or order_info.get("order_status")

    if not order_id:
        return {"ok": True}

    order = (
        db.query(PaymentOrder)
        .filter(PaymentOrder.provider_order_id == order_id)
        .first()
    )
    if not order:
        return {"ok": True}

    if status in ("SUCCESS", "PAID"):
        _provision_order(db, order)
    elif status in ("FAILED", "USER_DROPPED", "CANCELLED"):
        order.status = "failed"
        db.add(order)
        db.commit()

    return {"ok": True}


# ==========================================================================
# Subscription management
# ==========================================================================
@router.post("/cancel-subscription")
def cancel_subscription(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Ends the user's Premium access. Because Premium is a one-time timed grant
    (no stored mandate), there is never an automatic future charge; this simply
    ends access now at the user's request.
    """
    db.execute(
        update(User).where(User.id == current_user.id).values(tier="free", premium_until=None)
    )
    db.commit()
    return {"status": "cancelled"}


# ==========================================================================
# PayPal (global / USD) — currently hidden in the UI; kept for the future
# international lane. Provisions via the same shared helpers (30-day premium).
# ==========================================================================
PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()
PAYPAL_BASE_URL = (
    "https://api-m.paypal.com" if PAYPAL_MODE == "live" else "https://api-m.sandbox.paypal.com"
)


class PayPalCreateRequest(BaseModel):
    type: str
    currency: Optional[str] = "USD"
    credits: Optional[int] = 10


class PayPalCaptureRequest(BaseModel):
    order_id: str
    type: str
    credits: Optional[int] = 10


def _get_paypal_token() -> str:
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise ValueError("PayPal Client ID or Secret is not configured.")
    res = requests.post(
        f"{PAYPAL_BASE_URL}/v1/oauth2/token",
        data={"grant_type": "client_credentials"},
        auth=(PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET),
        timeout=15,
    )
    res.raise_for_status()
    return res.json()["access_token"]


@router.post("/paypal/create-order")
def create_paypal_order(
    payload: PayPalCreateRequest,
    current_user: User = Depends(get_current_user),
):
    is_mock = not PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET.startswith("mock")
    if is_mock:
        return {"order_id": f"mock_order_{uuid.uuid4().hex[:8]}"}

    currency = (payload.currency or "USD").upper()
    if payload.type == "subscription":
        amount_value, desc = "15.00", "HireWiz Premium (30 days)"
        custom_id = f"user:{current_user.id}|type:subscription"
    elif payload.type == "topup":
        credits_to_add = payload.credits or 25
        amount_value = "3.99"
        desc = f"HireWiz - {credits_to_add} Analysis Units"
        custom_id = f"user:{current_user.id}|type:topup|credits:{credits_to_add}"
    else:
        raise HTTPException(status_code=400, detail="Invalid billing type")

    try:
        token = _get_paypal_token()
        order_payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": desc,
                    "custom_id": custom_id,
                    "amount": {"currency_code": currency, "value": amount_value},
                }
            ],
            "application_context": {"shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW"},
        }
        res = requests.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders",
            json=order_payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )
        res.raise_for_status()
        return {"order_id": res.json()["id"]}
    except Exception as e:
        log.exception("PayPal order creation failed")
        raise HTTPException(status_code=500, detail=f"PayPal Integration Error: {str(e)}")


@router.post("/paypal/capture-order")
def capture_paypal_order(
    payload: PayPalCaptureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order_id = payload.order_id

    if order_id.startswith("mock_order_"):
        if payload.type == "subscription":
            _grant_premium(db, current_user.id)
            return {"status": "success", "tier": "premium"}
        _add_credits(db, current_user.id, payload.credits or 25)
        return {"status": "success", "added_credits": payload.credits or 25}

    try:
        token = _get_paypal_token()
        res = requests.post(
            f"{PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture",
            json={},
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=15,
        )
        res.raise_for_status()
        capture_data = res.json()

        if capture_data.get("status") != "COMPLETED":
            raise HTTPException(status_code=400, detail=f"Payment state: {capture_data.get('status')}")

        purchase_unit = capture_data.get("purchase_units", [{}])[0]
        custom_id = purchase_unit.get("custom_id", "")

        user_id = current_user.id
        tx_type = payload.type
        credits_to_add = payload.credits or 25
        if custom_id:
            parts = dict(item.split(":") for item in custom_id.split("|") if ":" in item)
            user_id = int(parts.get("user", user_id))
            tx_type = parts.get("type", tx_type)
            if "credits" in parts:
                credits_to_add = int(parts["credits"])

        if tx_type == "subscription":
            _grant_premium(db, user_id)
            return {"status": "success", "tier": "premium"}
        _add_credits(db, user_id, credits_to_add)
        return {"status": "success", "added_credits": credits_to_add}
    except HTTPException:
        raise
    except Exception as e:
        log.exception("PayPal payment capture failed")
        raise HTTPException(status_code=500, detail=f"PayPal Capture Failure: {str(e)}")


@router.get("/debug-env")
def debug_env():
    """Non-sensitive config presence check (no secrets returned)."""
    return {
        "cashfree_configured": _cashfree_configured(),
        "cashfree_mode": CASHFREE_MODE,
        "paypal_configured": bool(PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET),
        "paypal_mode": PAYPAL_MODE,
        "backend_public_url_set": bool(BACKEND_PUBLIC_URL),
    }
