import os
import logging
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from pydantic import BaseModel
from sqlalchemy import update
from sqlalchemy.orm import Session
import stripe

from ..database import get_db
from ..models import User
from ..security import get_current_user

router = APIRouter(prefix="/billing", tags=["billing"])
log = logging.getLogger("ai_resume_copilot.billing")

stripe.api_key = os.getenv("STRIPE_API_KEY", "sk_test_51MockKeyForSaaSTransitionOnly")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "whsec_mock")

class CheckoutRequest(BaseModel):
    type: str  # "subscription" or "topup"
    currency: Optional[str] = "usd"
    credits: Optional[int] = 10  # for topup

@router.post("/checkout")
def create_checkout_session(
    payload: CheckoutRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Creates a Stripe Checkout Session for Premium Subscriptions or Credit Top-ups.
    Supports Credit/Debit Cards, UPI (for INR), and Wallet (GPay/ApplePay auto-enabled via Stripe).
    """
    frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
    frontend_url = frontend_url.rstrip("/")
    
    currency_lower = payload.currency.lower() if payload.currency else "usd"
    
    # Configure payment method types based on currency (UPI requires INR)
    payment_types = ["card"]
    if currency_lower == "inr":
        payment_types.append("upi")
        
    try:
        if payload.type == "subscription":
            # monthly subscription
            session_data = {
                "payment_method_types": payment_types,
                "mode": "subscription",
                "line_items": [
                    {
                        "price_data": {
                            "currency": currency_lower,
                            "product_data": {
                                "name": "AI Resume CoPilot Premium",
                                "description": "Unlimited resume parsing, job matching, learning strategy generation, and RAG chat.",
                            },
                            "unit_amount": 1900 if currency_lower == "usd" else 99900,  # $19/mo or ₹999/mo
                            "recurring": {"interval": "month"},
                        },
                        "quantity": 1,
                    }
                ],
                "metadata": {
                    "user_id": str(current_user.id),
                    "type": "subscription",
                },
                "success_url": f"{frontend_url}/dashboard?stripe=success",
                "cancel_url": f"{frontend_url}/dashboard?stripe=cancel",
            }
        elif payload.type == "topup":
            # one-time credit top-up
            credits_to_add = payload.credits or 10
            unit_price = 100 if currency_lower == "usd" else 5000  # $1.00 or ₹50 per credit
            
            session_data = {
                "payment_method_types": payment_types,
                "mode": "payment",
                "line_items": [
                    {
                        "price_data": {
                            "currency": currency_lower,
                            "product_data": {
                                "name": f"{credits_to_add} AI Operation Credits",
                                "description": "Top-up operations balance for parsing, matching, and bullet optimizations.",
                            },
                            "unit_amount": unit_price * credits_to_add,
                        },
                        "quantity": 1,
                    }
                ],
                "metadata": {
                    "user_id": str(current_user.id),
                    "type": "topup",
                    "credits": str(credits_to_add),
                },
                "success_url": f"{frontend_url}/dashboard?stripe=success",
                "cancel_url": f"{frontend_url}/dashboard?stripe=cancel",
            }
        else:
            raise HTTPException(status_code=400, detail="Invalid checkout type")

        session = stripe.checkout.Session.create(**session_data)
        return {"checkout_url": session.url}

    except Exception as e:
        log.exception("Stripe session creation failed")
        raise HTTPException(status_code=500, detail=f"Stripe Integration Error: {str(e)}")

@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    stripe_signature: Optional[str] = Header(None, alias="Stripe-Signature"),
    db: Session = Depends(get_db),
):
    """
    Verifies raw Stripe webhook calls and provisions or revokes commercial tiers and credits.
    """
    payload = await request.body()
    
    # Handle local testing or missing signature verification
    if not STRIPE_WEBHOOK_SECRET or STRIPE_WEBHOOK_SECRET == "whsec_mock":
        # Decode without verification for mock testing if configured
        import json
        try:
            event = json.loads(payload)
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid JSON payload")
    else:
        if not stripe_signature:
            raise HTTPException(status_code=400, detail="Missing Stripe-Signature header")
        try:
            event = stripe.Webhook.construct_event(
                payload, stripe_signature, STRIPE_WEBHOOK_SECRET
            )
        except stripe.error.SignatureVerificationError as e:
            log.warning("Stripe signature verification failed: %s", e)
            raise HTTPException(status_code=400, detail="Invalid signature")
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    event_type = event.get("type")
    data_obj = event.get("data", {}).get("object", {})

    log.info("Processing Stripe webhook event: %s", event_type)

    if event_type == "checkout.session.completed":
        metadata = data_obj.get("metadata", {})
        user_id_str = metadata.get("user_id")
        tx_type = metadata.get("type")
        
        if not user_id_str:
            log.warning("No user_id found in session metadata")
            return {"status": "ignored"}
            
        user_id = int(user_id_str)
        
        if tx_type == "subscription":
            customer_id = data_obj.get("customer")
            subscription_id = data_obj.get("subscription")
            
            log.info("Provisioning premium tier for user %d", user_id)
            db.execute(
                update(User)
                .where(User.id == user_id)
                .values(
                    tier="premium",
                    stripe_customer_id=customer_id,
                    stripe_subscription_id=subscription_id,
                )
            )
            db.commit()
            
        elif tx_type == "topup":
            credits_to_add = int(metadata.get("credits", "10"))
            log.info("Adding %d credits to user %d", credits_to_add, user_id)
            db.execute(
                update(User)
                .where(User.id == user_id)
                .values(ai_credits=User.ai_credits + credits_to_add)
            )
            db.commit()

    elif event_type == "customer.subscription.deleted":
        subscription_id = data_obj.get("id")
        if subscription_id:
            log.info("Revoking premium tier for subscription: %s", subscription_id)
            db.execute(
                update(User)
                .where(User.stripe_subscription_id == subscription_id)
                .values(tier="free", stripe_subscription_id=None)
            )
            db.commit()

    return {"status": "success"}
