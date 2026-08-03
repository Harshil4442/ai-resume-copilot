from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from ... import models
from ...database import get_db
from ...domains.common import public_id, utcnow
from ...security import get_current_user

router = APIRouter(prefix="/admin", tags=["support-admin"])


class UsageAdjustment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int = Field(gt=0)
    amount: int = Field(ge=-10_000, le=10_000)
    reason: str = Field(min_length=10, max_length=500)


def _admin_emails() -> set[str]:
    return {
        value.strip().lower()
        for value in (os.getenv("ADMIN_EMAILS") or "").split(",")
        if value.strip()
    }


def require_admin(current_user: models.User = Depends(get_current_user)) -> models.User:
    if str(current_user.email).strip().lower() not in _admin_emails():
        raise HTTPException(status_code=404, detail="Not found")
    return current_user


def _run_summary(run: models.AnalysisRun) -> dict[str, object]:
    return {
        "id": run.id,
        "user_id": run.user_id,
        "opportunity_id": run.opportunity_id,
        "operation": run.operation,
        "status": run.status,
        "usage_state": run.usage_state,
        "estimated_units": run.estimated_units,
        "committed_units": run.committed_units,
        "provider": run.provider,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "attempt_count": run.attempt_count,
        "error_code": run.error_code,
        "error_message": run.error_message,
        "created_at": run.created_at,
        "completed_at": run.completed_at,
        "input_purged_at": run.input_purged_at,
        "result_purged_at": run.result_purged_at,
    }


@router.get("/support-snapshot")
def support_snapshot(
    email: str = Query(min_length=3, max_length=320),
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    user = db.query(models.User).filter(models.User.email == email.strip().lower()).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    orders = (
        db.query(models.PaymentOrder)
        .filter(models.PaymentOrder.user_id == user.id)
        .order_by(models.PaymentOrder.created_at.desc())
        .limit(20)
        .all()
    )
    runs = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.user_id == user.id)
        .order_by(models.AnalysisRun.created_at.desc())
        .limit(20)
        .all()
    )
    usage = (
        db.query(models.UsageEvent)
        .filter(models.UsageEvent.user_id == user.id)
        .order_by(models.UsageEvent.created_at.desc())
        .limit(50)
        .all()
    )
    entitlements = (
        db.query(models.EntitlementLedger)
        .filter(models.EntitlementLedger.user_id == user.id)
        .order_by(models.EntitlementLedger.granted_at.desc())
        .all()
    )
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "tier": user.tier,
            "premium_active": user.is_premium_active(),
            "premium_until": user.premium_until,
            "analysis_units": user.ai_credits,
        },
        "orders": [
            {
                "reference": order.public_id,
                "provider": order.provider,
                "provider_order_id": order.provider_order_id,
                "sku": order.sku,
                "status": order.status,
                "amount_minor": order.gross_amount_minor,
                "currency": order.currency,
                "paid_at": order.paid_at,
                "refunded_at": order.refunded_at,
                "created_at": order.created_at,
            }
            for order in orders
        ],
        "entitlements": [
            {
                "id": item.id,
                "source_order_id": item.source_order_id,
                "kind": item.entitlement_kind,
                "quantity": item.quantity,
                "status": item.status,
                "starts_at": item.starts_at,
                "expires_at": item.expires_at,
            }
            for item in entitlements
        ],
        "runs": [_run_summary(run) for run in runs],
        "usage_events": [
            {
                "id": event.id,
                "event_type": event.event_type,
                "amount": event.amount,
                "balance_after": event.balance_after,
                "source_type": event.source_type,
                "source_id": event.source_id,
                "actor": event.actor,
                "reason": event.reason,
                "created_at": event.created_at,
            }
            for event in usage
        ],
    }


@router.get("/analysis-runs/{run_id}")
def inspect_analysis_run(
    run_id: str,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    run = db.query(models.AnalysisRun).filter(models.AnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    calls = (
        db.query(models.ModelCallEvent)
        .filter(models.ModelCallEvent.analysis_run_id == run.id)
        .order_by(models.ModelCallEvent.created_at.asc())
        .all()
    )
    return {
        "run": _run_summary(run),
        "model_calls": [
            {
                "id": call.id,
                "provider": call.provider,
                "model": call.model,
                "prompt_version": call.prompt_version,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
                "estimated_cost_micros": call.estimated_cost_micros,
                "latency_ms": call.latency_ms,
                "status": call.status,
                "error_code": call.error_code,
                "created_at": call.created_at,
            }
            for call in calls
        ],
    }


@router.get("/payment-orders/{public_id}")
def inspect_payment_order(
    public_id: str,
    db: Session = Depends(get_db),
    _: models.User = Depends(require_admin),
):
    order = db.query(models.PaymentOrder).filter(models.PaymentOrder.public_id == public_id).first()
    if not order:
        raise HTTPException(status_code=404, detail="Payment order not found")
    transactions = db.query(models.PaymentTransaction).filter(
        models.PaymentTransaction.order_id == order.id
    ).all()
    refunds = db.query(models.PaymentRefund).filter(models.PaymentRefund.order_id == order.id).all()
    events = db.query(models.PaymentEvent).filter(models.PaymentEvent.order_id == order.id).all()
    return {
        "order": {
            "reference": order.public_id,
            "user_id": order.user_id,
            "provider": order.provider,
            "provider_order_id": order.provider_order_id,
            "sku": order.sku,
            "status": order.status,
            "amount_minor": order.gross_amount_minor,
            "refunded_amount_minor": order.refunded_amount_minor,
            "currency": order.currency,
            "paid_at": order.paid_at,
            "refunded_at": order.refunded_at,
            "created_at": order.created_at,
        },
        "transactions": [
            {
                "provider_payment_id": item.provider_payment_id,
                "status": item.status,
                "method": item.payment_method,
                "amount_minor": item.gross_amount_minor,
                "refunded_amount_minor": item.refunded_amount_minor,
                "captured_at": item.captured_at,
                "failed_at": item.failed_at,
            }
            for item in transactions
        ],
        "refunds": [
            {
                "provider_refund_id": item.provider_refund_id,
                "amount_minor": item.amount_minor,
                "status": item.status,
                "processed_at": item.processed_at,
            }
            for item in refunds
        ],
        "events": [
            {
                "provider_event_id": item.provider_event_id,
                "event_type": item.event_type,
                "processing_status": item.processing_status,
                "error_code": item.error_code,
                "received_at": item.received_at,
                "processed_at": item.processed_at,
            }
            for item in events
        ],
    }


@router.post("/usage-adjustments")
def adjust_usage(
    payload: UsageAdjustment,
    request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=160),
    db: Session = Depends(get_db),
    admin: models.User = Depends(require_admin),
):
    if payload.amount == 0:
        raise HTTPException(status_code=422, detail="Adjustment amount cannot be zero")
    user = (
        db.query(models.User)
        .filter(models.User.id == payload.user_id)
        .with_for_update()
        .first()
    )
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    event_key = f"admin:{idempotency_key}"
    existing = db.query(models.UsageEvent).filter(
        models.UsageEvent.user_id == user.id,
        models.UsageEvent.idempotency_key == event_key,
    ).first()
    if existing:
        return {
            "event_id": existing.id,
            "balance": existing.balance_after,
            "idempotent_replay": True,
        }

    before = int(user.ai_credits or 0)
    after = before + payload.amount
    if after < 0:
        raise HTTPException(status_code=409, detail="Adjustment would create a negative balance")
    user.ai_credits = after
    event = models.UsageEvent(
        id=public_id("use"),
        user_id=user.id,
        analysis_run_id=None,
        event_type="adjust",
        amount=payload.amount,
        balance_after=after,
        idempotency_key=event_key,
        source_type="support_adjustment",
        source_id=idempotency_key,
        actor=f"admin:{admin.id}",
        reason=payload.reason,
        created_at=utcnow(),
    )
    audit = models.AdminAuditEvent(
        id=public_id("aud"),
        actor_user_id=admin.id,
        actor_email=str(admin.email),
        action="usage_adjustment",
        target_type="user",
        target_id=str(user.id),
        reason=payload.reason,
        before_state={"analysis_units": before},
        after_state={"analysis_units": after, "usage_event_id": event.id},
        correlation_id=getattr(request.state, "correlation_id", None),
        created_at=utcnow(),
    )
    db.add(event)
    db.add(audit)
    db.commit()
    return {"event_id": event.id, "audit_id": audit.id, "balance": after, "idempotent_replay": False}
