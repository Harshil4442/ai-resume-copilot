"""Encrypted security-email delivery; independent of optional lifecycle emails."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from ...models import NotificationOutbox, PasswordResetToken, User
from ...security import JWT_SECRET
from ..common import public_id, utcnow

PURPOSE = b"hirewiz:password-reset-email:v1"


def _key() -> bytes:
    return hmac.new(JWT_SECRET.encode(), PURPOSE, hashlib.sha256).digest()


def _aad(reset_id: str, recipient: str, expiry: int) -> bytes:
    return json.dumps([reset_id, recipient, expiry], separators=(",", ":")).encode()


def configured() -> bool:
    return bool((os.getenv("RESEND_API_KEY") or "").strip() and (os.getenv("EMAIL_FROM") or "").strip())


def enqueue(db: Session, *, user_id: int, recipient: str, reset_id: str, token: str, expires_at: datetime) -> NotificationOutbox:
    expiry = int(expires_at.replace(tzinfo=UTC).timestamp() * 1000)
    nonce = os.urandom(12)
    encrypted = AESGCM(_key()).encrypt(nonce, token.encode(), _aad(reset_id, recipient, expiry))
    now = utcnow()
    item = NotificationOutbox(id=public_id("ntf"), user_id=user_id, notification_type="password_reset", channel="email",
        recipient=recipient, payload={"reset_id": reset_id, "expires_at_ms": expiry,
        "ciphertext": base64.urlsafe_b64encode(nonce + encrypted).decode()},
        idempotency_key="password-reset:" + reset_id, status="pending", attempt_count=0,
        available_at=now, created_at=now, updated_at=now)
    db.add(item)
    return item


def enqueue_changed(db: Session, *, user_id: int, recipient: str, reset_id: str) -> NotificationOutbox:
    now = utcnow()
    item = NotificationOutbox(id=public_id("ntf"), user_id=user_id, notification_type="password_changed", channel="email",
        recipient=recipient, payload={"reset_id": reset_id}, idempotency_key="password-changed:" + reset_id,
        status="pending", attempt_count=0, available_at=now, created_at=now, updated_at=now)
    db.add(item)
    return item


def message(item: NotificationOutbox) -> tuple[str, str]:
    if item.notification_type == "password_changed":
        return ("Your HireWiz password was changed", "Your HireWiz password was changed. If you did not make this change, "
            "request a new reset at https://www.hirewizhq.com/forgot-password and contact support immediately.")
    payload = item.payload
    if type(payload) is not dict or set(payload) != {"reset_id", "expires_at_ms", "ciphertext"}:
        raise ValueError("Password reset delivery unavailable")
    expiry = payload["expires_at_ms"]
    if type(expiry) is not int or int(utcnow().timestamp() * 1000) >= expiry:
        raise ValueError("Password reset delivery expired")
    raw = base64.b64decode(payload["ciphertext"], altchars=b"-_", validate=True)
    token = AESGCM(_key()).decrypt(raw[:12], raw[12:], _aad(payload["reset_id"], item.recipient, expiry)).decode("ascii")
    import re
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise ValueError("Password reset delivery unavailable")
    return ("Reset your HireWiz password", "Use this single-use link within 15 minutes to choose a new HireWiz password:\n\n"
        "https://www.hirewizhq.com/reset-password#token=" + token
        + "\n\nIf you did not request this, ignore this email. Your password has not changed.")


def deliver(db: Session, notification_id: str) -> bool:
    """Send only one reset item; provider idempotency is retained on retry."""
    from .service import _send

    if not configured():
        return False
    item = db.query(NotificationOutbox).filter_by(id=notification_id, status="pending").filter(NotificationOutbox.notification_type.in_(("password_reset", "password_changed"))).with_for_update().one_or_none()
    if item is None:
        db.rollback()
        return False
    now = utcnow()
    available = item.available_at.replace(tzinfo=UTC)
    if now < available:
        db.rollback()
        return False
    payload = item.payload or {}
    row = db.get(PasswordResetToken, payload.get("reset_id"))
    owner = db.get(User, item.user_id)
    if (row is None or owner is None or row.user_id != owner.id or row.status != ("COMPLETED" if item.notification_type == "password_changed" else "PENDING")
            or (item.notification_type == "password_reset" and row.expires_at.replace(tzinfo=UTC) <= now) or owner.email != item.recipient
            or hashlib.sha256(item.recipient.encode()).hexdigest() != row.email_sha256):
        item.status, item.payload, item.updated_at = "cancelled", {}, now
        db.commit()
        return False
    item.status, item.claimed_at = "processing", now
    item.attempt_count += 1
    outgoing = NotificationOutbox(id=item.id, notification_type=item.notification_type,
        recipient=item.recipient, payload=dict(item.payload), idempotency_key=item.idempotency_key)
    db.commit()  # Transient outgoing object cannot reopen an expired ORM transaction.
    try:
        _send(outgoing)
    except Exception as error:  # noqa: BLE001 -- never retain credential-bearing provider text.
        db.rollback()
        item = db.query(NotificationOutbox).filter_by(id=notification_id).with_for_update().one()
        item.last_error, item.claimed_at, item.updated_at = type(error).__name__, None, utcnow()
        if item.attempt_count >= 5:
            item.status, item.payload = "failed", {}
        else:
            item.status, item.available_at = "pending", utcnow() + timedelta(minutes=2 ** item.attempt_count)
        db.commit()
        return False
    item = db.query(NotificationOutbox).filter_by(id=notification_id).with_for_update().one()
    item.status, item.sent_at, item.claimed_at, item.last_error = "sent", utcnow(), None, None
    item.payload, item.updated_at = {}, utcnow()
    db.commit()
    return True
