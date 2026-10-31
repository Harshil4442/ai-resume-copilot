from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

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
    from .mail_budget import LIFECYCLE_SCHEDULE_LIMIT, TTL, MailBudgetError, reserve_messages
    now = utcnow()
    if available_at is not None and (not isinstance(available_at, datetime) or available_at.utcoffset() is None):
        return None
    due = max(now, available_at.astimezone(UTC)) if available_at is not None else now
    if due > now + LIFECYCLE_SCHEDULE_LIMIT:
        return None
    try:
        funded = reserve_messages(db, [idempotency_key], category="lifecycle", expires_at=due + TTL["lifecycle"])[0]
    except MailBudgetError as error:
        if error.mail_outcome != "deferred":
            raise
        # This optional message has not been accepted. Keep the caller's paid
        # grant, generated result, registration or recovery transaction intact.
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
    if funded.expires_at <= due:
        return None  # An immutable older envelope cannot fund a new expired schedule.
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
        available_at=due,
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
    if item.notification_type in {"password_reset", "password_changed"}:
        from .password_reset import message
        return message(item)
    if item.notification_type == "privacy_erasure":
        from .privacy_erasure import message as erasure_message
        return erasure_message(item)
    if item.notification_type == "account_recovery_requested":
        return (
            "HireWiz account recovery requested",
            "A password recovery and browser account enrollment request was made for your HireWiz account. "
            f"If you did not request this, contact support: {app_url}/contact",
        )
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
    from .mail_budget import MailBudgetError, ProviderResult, guarded_send

    api_key = (os.getenv("RESEND_API_KEY") or "").strip()
    sender = (os.getenv("EMAIL_FROM") or "").strip()
    if not api_key or not sender:
        raise MailBudgetError("deferred")
    try:
        subject, text = _message(item)
    except Exception:
        raise MailBudgetError("permanent") from None
    body = {
        "from": sender,
        "to": [item.recipient],
        "subject": subject,
        "text": text,
        "html": f"<p>{html.escape(text).replace(chr(10), '<br>')}</p>",
    }
    def provider():
        with httpx.Client(timeout=httpx.Timeout(10.0, connect=5.0)) as client:
            response = client.post("https://api.resend.com/emails", headers={
                "Authorization": f"Bearer {api_key}", "Idempotency-Key": item.idempotency_key}, json=body)
        raw = response.content
        receipt = hashlib.sha256(raw).hexdigest()
        status = response.status_code
        outcome: Literal["sent", "unknown", "permanent", "known_retryable"] = "unknown"
        if 200 <= status < 300 and len(raw) <= 16384:
            try:
                value = json.loads(raw)
                if (type(value) is dict and set(value) == {"id"} and type(value["id"]) is str
                        and re.fullmatch(r"[A-Za-z0-9_-]{8,200}", value["id"]) is not None):
                    outcome = "sent"
            except (ValueError, TypeError, UnicodeError):
                pass
        elif status == 429:
            outcome = "known_retryable"
        elif 400 <= status < 500 and status != 408:
            outcome = "permanent"
        return ProviderResult(outcome, receipt, status)

    category = ("privacy_erasure" if item.notification_type == "privacy_erasure" else
        "password_reset" if item.notification_type in {"password_reset", "password_changed"} else "lifecycle")
    expiry = (item.payload or {}).get("expires_at_ms")
    expires_at = datetime.fromtimestamp(expiry / 1000, UTC) if type(expiry) is int else None
    guarded_send(item.idempotency_key, provider, category=category, expires_at=expires_at)


def _send_failure(item: models.NotificationOutbox, error: Exception) -> str:
    outcome = getattr(error, "mail_outcome", "unknown")
    item.last_error, item.claimed_at, item.updated_at = "MailDeliveryUnavailable", None, utcnow()
    if outcome == "deferred":
        item.status, item.attempt_count = "pending", max(0, int(item.attempt_count or 0) - 1)
        item.available_at = utcnow() + timedelta(minutes=1)
    elif outcome == "known_retryable" and int(item.attempt_count or 0) < 3:
        item.status, item.available_at = "pending", utcnow() + timedelta(minutes=2 ** item.attempt_count)
    elif outcome in {"permanent", "known_retryable"}:
        item.status = "failed"
    else:
        item.status = "mail_unknown"
    return item.status


def deliver_pending_notifications(db: Session, *, limit: int = 50) -> DeliverySummary:
    configured = bool(
        lifecycle_email_enabled()
        and (os.getenv("RESEND_API_KEY") or "").strip()
        and (os.getenv("EMAIL_FROM") or "").strip()
    )
    from .password_reset import configured as reset_configured
    from .password_reset import deliver as deliver_reset
    from .privacy_erasure import deliver_pending as deliver_erasure
    privacy_sent = deliver_erasure(db, limit=limit)
    security_sent = security_retried = security_failed = security_claimed = 0
    if reset_configured():
        now = utcnow()
        # A stale send has unknown provider outcome, never automatic resend.
        db.query(models.NotificationOutbox).filter(
            models.NotificationOutbox.notification_type.in_(("password_reset", "password_changed")),
            models.NotificationOutbox.status == "processing",
            models.NotificationOutbox.claimed_at < now - timedelta(minutes=15),
        ).update({models.NotificationOutbox.status: "mail_unknown", models.NotificationOutbox.claimed_at: None}, synchronize_session=False)
        db.commit()
        reset_ids = [row[0] for row in db.query(models.NotificationOutbox.id).filter_by(status="pending").filter(
            models.NotificationOutbox.notification_type.in_(("password_reset", "password_changed")),
            models.NotificationOutbox.available_at <= now).order_by(models.NotificationOutbox.available_at.asc()).limit(limit).all()]
        for reset_id in reset_ids:
            if deliver_reset(db, reset_id):
                security_sent += 1
            else:
                item = db.get(models.NotificationOutbox, reset_id)
                if item is not None and item.status == "failed":
                    security_failed += 1
                elif item is not None and item.status == "pending":
                    security_retried += 1
            security_claimed += 1
        db.rollback()
    if not configured:
        return DeliverySummary(configured=reset_configured(), claimed=security_claimed,
            sent=security_sent + privacy_sent, retried=security_retried, failed=security_failed)

    now = utcnow()
    stale_claim = now - timedelta(minutes=15)
    db.query(models.NotificationOutbox).filter(
        models.NotificationOutbox.status == "processing",
        models.NotificationOutbox.notification_type != "privacy_erasure",
        models.NotificationOutbox.claimed_at < stale_claim,
    ).update(
        {
            models.NotificationOutbox.status: "mail_unknown",
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
                models.NotificationOutbox.notification_type.notin_(("password_reset", "password_changed", "privacy_erasure")),
                models.NotificationOutbox.available_at <= now,
            )
            .order_by(models.NotificationOutbox.available_at.asc())
            .limit(limit)
            .all()
        )
    ]
    sent, retried, failed = security_sent, security_retried, security_failed
    for item_id in item_ids:
        item = (
            db.query(models.NotificationOutbox)
            .filter(
                models.NotificationOutbox.id == item_id,
                models.NotificationOutbox.status == "pending",
                models.NotificationOutbox.notification_type.notin_(("password_reset", "password_changed", "privacy_erasure")),
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
        outgoing = models.NotificationOutbox(id=item.id, notification_type=item.notification_type,
            recipient=item.recipient, payload=dict(item.payload or {}), idempotency_key=item.idempotency_key)
        db.commit()
        try:
            _send(outgoing)
        except Exception as exc:
            db.rollback()
            item = db.query(models.NotificationOutbox).filter(models.NotificationOutbox.id == item_id).one()
            outcome = _send_failure(item, exc)
            if outcome == "failed":
                failed += 1
            elif outcome == "pending":
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
        claimed=len(item_ids) + security_claimed,
        sent=sent + privacy_sent,
        retried=retried,
        failed=failed,
    )
