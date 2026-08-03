from __future__ import annotations

import html
import os
from dataclasses import asdict, dataclass
from datetime import timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from ... import models
from ..common import public_id, utcnow


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def lifecycle_email_enabled() -> bool:
    return _env_bool("LIFECYCLE_EMAILS_ENABLED")


def enqueue_notification(
    db: Session,
    *,
    user_id: int | None,
    notification_type: str,
    recipient: str,
    payload: dict[str, Any],
    idempotency_key: str,
    available_at=None,
) -> models.NotificationOutbox | None:
    if not lifecycle_email_enabled():
        return None
    pending = next(
        (
            item
            for item in db.new
            if isinstance(item, models.NotificationOutbox)
            and item.idempotency_key == idempotency_key
        ),
        None,
    )
    if pending:
        return pending
    existing = (
        db.query(models.NotificationOutbox)
        .filter(models.NotificationOutbox.idempotency_key == idempotency_key)
        .first()
    )
    if existing:
        return existing
    now = utcnow()
    item = models.NotificationOutbox(
        id=public_id("ntf"),
        user_id=user_id,
        notification_type=notification_type,
        channel="email",
        recipient=recipient.strip().lower(),
        payload=payload,
        idempotency_key=idempotency_key,
        status="pending",
        attempt_count=0,
        available_at=available_at or now,
        created_at=now,
        updated_at=now,
    )
    db.add(item)
    return item


def queue_due_reminder_emails(db: Session, *, limit: int = 100) -> int:
    if not lifecycle_email_enabled():
        return 0
    now = utcnow()
    reminders = (
        db.query(models.Reminder, models.User)
        .join(models.User, models.User.id == models.Reminder.user_id)
        .filter(
            models.Reminder.status == "scheduled",
            models.Reminder.delivery_channel == "email",
            models.Reminder.sent_at.is_(None),
            models.Reminder.due_at <= now,
        )
        .order_by(models.Reminder.due_at.asc())
        .limit(limit)
        .all()
    )
    queued = 0
    for reminder, user in reminders:
        item = enqueue_notification(
            db,
            user_id=int(user.id),
            notification_type="reminder_due",
            recipient=str(user.email),
            payload={
                "reminder_id": reminder.id,
                "message": reminder.message,
                "opportunity_id": reminder.opportunity_id,
            },
            idempotency_key=f"reminder:{reminder.id}:email",
        )
        if item and item.status == "pending":
            queued += 1
    db.commit()
    return queued


@dataclass(frozen=True)
class DeliverySummary:
    configured: bool
    claimed: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _message(item: models.NotificationOutbox) -> tuple[str, str]:
    app_url = (os.getenv("FRONTEND_URL") or "https://www.hirewizhq.com").rstrip("/")
    payload = item.payload or {}
    if item.notification_type == "welcome":
        return (
            "Welcome to HireWiz",
            f"Your Career Workspace is ready. Add your resume to start building approved evidence: {app_url}/resume",
        )
    if item.notification_type == "onboarding_reminder":
        return (
            "Finish your first HireWiz workspace",
            f"Add a resume and target role to receive your first useful match: {app_url}/resume",
        )
    if item.notification_type == "analysis_completed":
        opportunity_id = payload.get("opportunity_id")
        destination = f"{app_url}/workspace/{opportunity_id}" if opportunity_id else f"{app_url}/dashboard"
        return (
            "Your HireWiz analysis is ready",
            f"Your {payload.get('operation', 'analysis').replace('_', ' ')} has completed. Review it here: {destination}",
        )
    if item.notification_type == "reminder_due":
        opportunity_id = payload.get("opportunity_id")
        destination = f"{app_url}/workspace/{opportunity_id}" if opportunity_id else f"{app_url}/dashboard"
        return (
            "HireWiz reminder",
            f"{payload.get('message', 'A Career Workspace reminder is due.')}\n\nOpen HireWiz: {destination}",
        )
    if item.notification_type == "payment_receipt":
        return (
            "Your HireWiz payment is confirmed",
            (
                f"Payment {payload.get('reference', '')} for {payload.get('amount_display', '')} "
                f"was confirmed. Review access and payment details: {app_url}/billing"
            ),
        )
    return ("HireWiz update", f"You have a HireWiz update: {app_url}/dashboard")


def _send(item: models.NotificationOutbox) -> None:
    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    sender = (os.getenv("EMAIL_FROM") or "").strip()
    if not api_key or not sender:
        raise RuntimeError("Email delivery is not configured")
    subject, text = _message(item)
    body = {
        "from": sender,
        "to": [item.recipient],
        "subject": subject,
        "text": text,
        "html": f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>",
    }
    with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
        response = client.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Idempotency-Key": item.idempotency_key,
            },
            json=body,
        )
    response.raise_for_status()


def deliver_pending_notifications(db: Session, *, limit: int = 50) -> DeliverySummary:
    configured = bool(
        lifecycle_email_enabled()
        and (os.getenv("RESEND_API_KEY") or "").strip()
        and (os.getenv("EMAIL_FROM") or "").strip()
    )
    if not configured:
        return DeliverySummary(configured=False)

    now = utcnow()
    stale_claim = now - timedelta(minutes=15)
    db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.status == "processing",
        models.NotificationOutbox.claimed_at < stale_claim,
    ).update(
        {
            models.NotificationOutbox.status: "pending",
            models.NotificationOutbox.claimed_at: None,
            models.NotificationOutbox.updated_at: now,
        },
        synchronize_session=False,
    )
    db.commit()

    item_ids = [
        row[0]
        for row in (
            db.query(models.NotificationOutbox.id)
            .filter(
                models.NotificationOutbox.status == "pending",
                models.NotificationOutbox.available_at <= now,
            )
            .order_by(models.NotificationOutbox.available_at.asc())
            .limit(limit)
            .all()
        )
    ]
    sent = retried = failed = 0
    for item_id in item_ids:
        item = (
            db.query(models.NotificationOutbox)
            .filter(
                models.NotificationOutbox.id == item_id,
                models.NotificationOutbox.status == "pending",
            )
            .with_for_update()
            .first()
        )
        if not item:
            db.rollback()
            continue
        item.status = "processing"
        item.claimed_at = utcnow()
        item.attempt_count = int(item.attempt_count or 0) + 1
        item.updated_at = utcnow()
        db.commit()
        try:
            _send(item)
        except Exception as exc:
            db.rollback()
            item = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.id == item_id).one()
            item.last_error = type(exc).__name__[:500]
            item.claimed_at = None
            item.updated_at = utcnow()
            if item.attempt_count >= 5:
                item.status = "failed"
                failed += 1
            else:
                item.status = "pending"
                item.available_at = utcnow() + timedelta(minutes=2 ** item.attempt_count)
                retried += 1
            db.commit()
            continue

        item = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.id == item_id).one()
        item.status = "sent"
        item.sent_at = utcnow()
        item.claimed_at = None
        item.last_error = None
        item.updated_at = utcnow()
        reminder_id = (item.payload or {}).get("reminder_id")
        if reminder_id:
            reminder = db.query(models.Reminder).filter(models.Reminder.id == reminder_id).first()
            if reminder:
                reminder.sent_at = item.sent_at
        db.commit()
        sent += 1
    return DeliverySummary(
        configured=True,
        claimed=len(item_ids),
        sent=sent,
        retried=retried,
        failed=failed,
    )
