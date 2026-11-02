"""Purpose-bound erasure mail, isolated from both historical worker selectors."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
from datetime import UTC, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.orm import Session

from ...models import CandidatePasswordAccount, NotificationOutbox, PasswordResetToken, User
from ...security import JWT_SECRET
from ..common import public_id, utcnow
from .password_reset import configured as reset_mail_configured

PURPOSE = b"hirewiz:privacy-erasure-email:v1"
TYPE = "privacy_erasure"
PENDING, PROCESSING, UNKNOWN = "erasure_pending", "erasure_processing", "erasure_unknown"


def configured() -> bool:
    return os.getenv("PRIVACY_ERASURE_RECOVERY_ENABLED", "false") == "true" and reset_mail_configured()


def _key():
    return hmac.new(JWT_SECRET.encode(), PURPOSE, hashlib.sha256).digest()


def _aad(proof_id, recipient, expiry):
    return json.dumps([PURPOSE.decode(), proof_id, recipient, expiry], separators=(",", ":")).encode()


def enqueue(db, *, user_id, recipient, proof_id, token, expires_at):
    from .mail_budget import reserve_messages
    key = "privacy-erasure:" + proof_id
    reserve_messages(db, [key], category=TYPE, expires_at=expires_at)
    expiry = int(expires_at.replace(tzinfo=UTC).timestamp() * 1000)
    nonce, now = os.urandom(12), utcnow()
    encrypted = AESGCM(_key()).encrypt(nonce, token.encode(), _aad(proof_id, recipient, expiry))
    item = NotificationOutbox(id=public_id("ntf"), user_id=user_id, notification_type=TYPE, channel="email",
        recipient=recipient, payload={"purpose": TYPE, "proof_id": proof_id, "expires_at_ms": expiry,
            "ciphertext": base64.urlsafe_b64encode(nonce + encrypted).decode()}, idempotency_key=key,
        status=PENDING, attempt_count=0, available_at=now, created_at=now, updated_at=now)
    db.add(item)
    return item


def message(item):
    value = item.payload
    if (item.notification_type != TYPE or type(value) is not dict
            or set(value) != {"purpose", "proof_id", "expires_at_ms", "ciphertext"} or value["purpose"] != TYPE
            or type(value["expires_at_ms"]) is not int or int(utcnow().timestamp()*1000) >= value["expires_at_ms"]):
        raise ValueError("Privacy erasure delivery unavailable")
    raw = base64.b64decode(value["ciphertext"], altchars=b"-_", validate=True)
    token = AESGCM(_key()).decrypt(raw[:12], raw[12:], _aad(value["proof_id"], item.recipient, value["expires_at_ms"])).decode("ascii")
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise ValueError("Privacy erasure delivery unavailable")
    return ("Confirm deletion of your HireWiz data", "Someone requested deletion of the data remaining in your denied HireWiz account. "
        "This does not restore account access. To explicitly confirm deletion within 10 minutes, open:\n\n"
        "https://www.hirewizhq.com/privacy-erasure/confirm#token=" + token
        + "\n\nOpening the link does not delete data. If you did not request deletion, ignore this email.")


def deliver(db: Session, notification_id: str) -> bool:
    from ..candidate_accounts import email_erasure
    from .service import _send

    if not configured():
        return False
    item = db.query(NotificationOutbox).filter_by(id=notification_id, status=PENDING, notification_type=TYPE).with_for_update().one_or_none()
    if item is None:
        db.rollback()
        return False
    now = utcnow()
    if now < item.available_at.replace(tzinfo=UTC):
        db.rollback()
        return False
    row = db.get(PasswordResetToken, (item.payload or {}).get("proof_id"))
    owner = db.get(User, item.user_id)
    account = db.get(CandidatePasswordAccount, item.user_id)
    if (row is None or row.purpose != TYPE or row.status != "PENDING" or row.consumed_at is not None
            or owner is None or account is None or account.state != "DELETE_DENIED" or row.user_id != owner.id
            or owner.email != item.recipient or row.email_sha256 != email_erasure.digest(owner.email)
            or row.credential_sha256 != email_erasure.digest(owner.password_hash)
            or row.expires_at.replace(tzinfo=UTC) <= now or int(item.attempt_count or 0) >= 3):
        item.status, item.payload, item.updated_at = "erasure_cancelled", {}, now
        db.commit()
        return False
    item.status, item.claimed_at = PROCESSING, now
    item.attempt_count += 1
    outgoing = NotificationOutbox(id=item.id, notification_type=TYPE, recipient=item.recipient,
        payload=dict(item.payload), idempotency_key=item.idempotency_key)
    db.commit()
    try:
        _send(outgoing)
    except Exception as error:
        db.rollback()
        item = db.query(NotificationOutbox).filter_by(id=notification_id).with_for_update().one_or_none()
        if item is None:
            db.rollback()
            return False
        outcome = getattr(error, "mail_outcome", "unknown")
        item.status = UNKNOWN if outcome == "unknown" else ("erasure_failed" if outcome == "permanent" or item.attempt_count >= 3 else PENDING)
        if outcome == "deferred":
            item.status, item.attempt_count = PENDING, max(0, item.attempt_count-1)
        item.claimed_at, item.updated_at, item.last_error = None, utcnow(), "PrivacyMailUnavailable"
        item.available_at = utcnow() + timedelta(minutes=1)
        if item.status == "erasure_failed":
            item.payload = {}
        db.commit()
        return False
    item = db.query(NotificationOutbox).filter_by(id=notification_id).with_for_update().one_or_none()
    if item is None:
        db.rollback()
        return False
    item.status, item.payload, item.sent_at, item.claimed_at = "erasure_sent", {}, utcnow(), None
    item.updated_at, item.last_error = utcnow(), None
    db.commit()
    return True


def deliver_pending(db: Session, *, limit: int = 50) -> int:
    # A stale in-flight send has UNKNOWN provider outcome, never an automatic
    # retry. The detached central ledger also refuses a missing outcome receipt.
    now = utcnow()
    db.query(NotificationOutbox).filter(NotificationOutbox.notification_type == TYPE,
        NotificationOutbox.status == PROCESSING, NotificationOutbox.claimed_at < now-timedelta(minutes=2)).update(
            {NotificationOutbox.status: UNKNOWN, NotificationOutbox.claimed_at: None}, synchronize_session=False)
    db.commit()
    ids = [row[0] for row in db.query(NotificationOutbox.id).filter_by(notification_type=TYPE, status=PENDING).filter(
        NotificationOutbox.available_at <= now).order_by(NotificationOutbox.available_at).limit(limit).all()]
    db.rollback()
    return sum(deliver(db, identifier) for identifier in ids)
