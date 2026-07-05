import os
import logging
import requests
import uuid
import razorpay
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger("ai_resume_copilot.billing")

@router.get("/debug-env")
def debug_env():
    import os
    pid = os.getenv("PAYPAL_CLIENT_ID", "")
    paypal_keys = [k for k in os.environ.keys() if "PAYPAL" in k]
    razorpay_keys = [k for k in os.environ.keys() if "RAZORPAY" in k]
    return {
        "length": len(pid), 
        "value_starts": pid[:5], 
        "in_environ": "PAYPAL_CLIENT_ID" in os.environ, 
        "paypal_keys": paypal_keys,
        "razorpay_keys": razorpay_keys
    }


PAYPAL_CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
PAYPAL_CLIENT_SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")
PAYPAL_MODE = os.getenv("PAYPAL_MODE", "sandbox").lower()

RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")

razorpay_client = razorpay.Client(auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)) if RAZORPAY_KEY_ID else None

if PAYPAL_MODE == "live":
    PAYPAL_BASE_URL = "https://api-m.paypal.com"
else:
    PAYPAL_BASE_URL = "https://api-m.sandbox.paypal.com"

class PayPalCreateRequest(BaseModel):
    type: str  # "subscription" or "topup"
    currency: Optional[str] = "USD"
    credits: Optional[int] = 10  # for topup

class PayPalCaptureRequest(BaseModel):
    order_id: str
    type: str  # "subscription" or "topup"
    credits: Optional[int] = 10  # for topup

class RazorpayCreateRequest(BaseModel):
    type: str
    credits: Optional[int] = 10

class RazorpayVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str
    type: str
    credits: Optional[int] = 10

def _get_paypal_token() -> str:
    """Helper to request OAuth2 access token from PayPal REST API."""
    if not PAYPAL_CLIENT_ID or not PAYPAL_CLIENT_SECRET:
        raise ValueError("PayPal Client ID or Secret is not configured.")
        
    url = f"{PAYPAL_BASE_URL}/v1/oauth2/token"
    res = requests.post(
        url,
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
    """
    Creates a PayPal Checkout Order for Premium Subscriptions or Top-ups.
    Encodes metadata inside custom_id field.
    """
    # Check if we should use Mock Billing (when credentials are unset or fake)
    is_mock = not PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET.startswith("mock")
    if is_mock:
        has_key = "PAYPAL_CLIENT_ID" in os.environ
        mock_id = f"mock_order_{uuid.uuid4().hex[:8]}_haskey_{has_key}"
        log.info("Creating mock PayPal order: %s", mock_id)
        return {"order_id": mock_id}

    currency = (payload.currency or "USD").upper()
    
    if payload.type == "subscription":
        amount_value = "15.00"
        desc = "AI Resume CoPilot Premium Subscription (Monthly)"
        custom_id = f"user:{current_user.id}|type:subscription"
    elif payload.type == "topup":
        credits_to_add = payload.credits or 25
        amount_value = "3.99"
        desc = f"AI Resume CoPilot - {credits_to_add} Operation Credits"
        custom_id = f"user:{current_user.id}|type:topup|credits:{credits_to_add}"
    else:
        raise HTTPException(status_code=400, detail="Invalid billing type")

    try:
        token = _get_paypal_token()
        url = f"{PAYPAL_BASE_URL}/v2/checkout/orders"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        order_payload = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "description": desc,
                    "custom_id": custom_id,
                    "amount": {
                        "currency_code": currency,
                        "value": amount_value,
                    }
                }
            ],
            "application_context": {
                "shipping_preference": "NO_SHIPPING",
                "user_action": "PAY_NOW",
            }
        }
        
        res = requests.post(url, json=order_payload, headers=headers, timeout=15)
        res.raise_for_status()
        order_data = res.json()
        
        return {"order_id": order_data["id"]}
        
    except Exception as e:
        log.exception("PayPal order creation failed")
        raise HTTPException(
            status_code=500,
            detail=f"PayPal Integration Error: {str(e)}"
        )

@router.post("/paypal/capture-order")
def capture_paypal_order(
    payload: PayPalCaptureRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Captures the authorized PayPal order payment. On completion, provisions 
    premium tier privileges or credit packages.
    """
    order_id = payload.order_id
    
    # Handle mock local verification
    if order_id.startswith("mock_order_"):
        log.info("Processing mock capture verification for order: %s", order_id)
        if payload.type == "subscription":
            db.execute(
                update(User)
                .where(User.id == current_user.id)
                .values(
                    tier="premium",
                    stripe_customer_id="mock_paypal_cust",
                    stripe_subscription_id=f"mock_pp_sub_{order_id}",
                )
            )
            db.commit()
            return {"status": "success", "tier": "premium"}
        elif payload.type == "topup":
            credits_to_add = payload.credits or 25
            db.execute(
                update(User)
                .where(User.id == current_user.id)
                .values(ai_credits=User.ai_credits + credits_to_add)
            )
            db.commit()
            return {"status": "success", "added_credits": credits_to_add}
        else:
            raise HTTPException(status_code=400, detail="Invalid mock billing type")

    try:
        token = _get_paypal_token()
        url = f"{PAYPAL_BASE_URL}/v2/checkout/orders/{order_id}/capture"
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        
        res = requests.post(url, json={}, headers=headers, timeout=15)
        res.raise_for_status()
        capture_data = res.json()
        
        # Verify payment is COMPLETED
        if capture_data.get("status") != "COMPLETED":
            raise HTTPException(
                status_code=400,
                detail=f"Payment capture state: {capture_data.get('status')}"
            )
            
        # Parse purchase unit information
        purchase_unit = capture_data.get("purchase_units", [{}])[0]
        payments = purchase_unit.get("payments", {})
        captures = payments.get("captures", [{}])[0]
        
        custom_id = purchase_unit.get("custom_id", "")
        if not custom_id:
            custom_id = captures.get("custom_id", "")
            
        # Parse user meta claims
        user_id = current_user.id
        tx_type = payload.type
        credits_to_add = payload.credits or 25
        
        if custom_id:
            # e.g., user:1|type:subscription
            parts = dict(item.split(":") for item in custom_id.split("|") if ":" in item)
            if "user" in parts:
                user_id = int(parts["user"])
            if "type" in parts:
                tx_type = parts["type"]
            if "credits" in parts:
                credits_to_add = int(parts["credits"])

        if tx_type == "subscription":
            capture_id = captures.get("id", f"pp_cap_{order_id}")
            db.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    tier="premium",
                    stripe_customer_id=capture_data.get("payer", {}).get("payer_id"),
                    stripe_subscription_id=capture_id,
                )
            )
            db.commit()
            log.info("PayPal Subscription provisioned for User %d", user_id)
            return {"status": "success", "tier": "premium"}
            
        elif tx_type == "topup":
            db.execute(
                update(User)
                .where(User.id == user_id)
                .values(ai_credits=User.ai_credits + credits_to_add)
            )
            db.commit()
            log.info("PayPal Topup of %d credits provisioned for User %d", credits_to_add, user_id)
            return {"status": "success", "added_credits": credits_to_add}
            
    except Exception as e:
        log.exception("PayPal payment capture failed")
        raise HTTPException(
            status_code=500,
            detail=f"PayPal Capture Failure: {str(e)}"
        )

@router.post("/razorpay/create-order")
def create_razorpay_order(
    payload: RazorpayCreateRequest,
    current_user: User = Depends(get_current_user),
):
    if not razorpay_client:
        # Mock mode fallback
        mock_id = f"mock_rzp_{uuid.uuid4().hex[:8]}"
        return {"order_id": mock_id, "amount": 99900 if payload.type == "subscription" else 19900}

    if payload.type == "subscription":
        amount_paise = 99900  # ₹999.00
        receipt = f"rcpt_{current_user.id}_sub"
    elif payload.type == "topup":
        credits_to_add = payload.credits or 25
        # For simplicity, ₹99 flat for 25 credits.
        amount_paise = 9900 
        receipt = f"rcpt_{current_user.id}_top_{credits_to_add}"
    else:
        raise HTTPException(status_code=400, detail="Invalid billing type")

    try:
        order_data = razorpay_client.order.create({
            "amount": amount_paise,
            "currency": "INR",
            "receipt": receipt,
            "notes": {
                "user_id": current_user.id,
                "type": payload.type,
                "credits": payload.credits or 25
            }
        })
        return {"order_id": order_data["id"], "amount": amount_paise}
    except Exception as e:
        log.exception("Razorpay order creation failed")
        raise HTTPException(status_code=500, detail=f"Razorpay Error: {str(e)}")

@router.post("/razorpay/verify-payment")
def verify_razorpay_payment(
    payload: RazorpayVerifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Handle mock local verification
    if payload.razorpay_order_id.startswith("mock_rzp_"):
        if payload.type == "subscription":
            db.execute(
                update(User)
                .where(User.id == current_user.id)
                .values(tier="premium", stripe_subscription_id=f"mock_rzp_sub_{payload.razorpay_order_id}")
            )
            db.commit()
            return {"status": "success", "tier": "premium"}
        elif payload.type == "topup":
            db.execute(
                update(User)
                .where(User.id == current_user.id)
                .values(ai_credits=User.ai_credits + (payload.credits or 10))
            )
            db.commit()
            return {"status": "success", "added_credits": payload.credits or 10}

    if not razorpay_client:
        raise HTTPException(status_code=500, detail="Razorpay is not configured.")

    try:
        # Verify Signature
        razorpay_client.utility.verify_payment_signature({
            'razorpay_order_id': payload.razorpay_order_id,
            'razorpay_payment_id': payload.razorpay_payment_id,
            'razorpay_signature': payload.razorpay_signature
        })
        
        # Provision Access
        if payload.type == "subscription":
            db.execute(
                update(User)
                .where(User.id == current_user.id)
                .values(
                    tier="premium",
                    stripe_customer_id=payload.razorpay_payment_id,
                    stripe_subscription_id=payload.razorpay_order_id,
                )
            )
            db.commit()
            return {"status": "success", "tier": "premium"}
        elif payload.type == "topup":
            credits_to_add = payload.credits or 25
            db.execute(
                update(User)
                .where(User.id == current_user.id)
                .values(ai_credits=User.ai_credits + credits_to_add)
            )
            db.commit()
            return {"status": "success", "added_credits": credits_to_add}

    except razorpay.errors.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid payment signature")
    except Exception as e:
        log.exception("Razorpay payment verification failed")
        raise HTTPException(status_code=500, detail=str(e))
