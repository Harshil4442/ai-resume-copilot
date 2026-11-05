"""Bounded weekly delivery with native consent/cadence and detached mail funding.

Outbox rows and SQL schedules grant no permission. The complete outgoing message
is encrypted at rest and bound to the original native BEGIN before the first
possible provider call. A stale/unknown claim is never retried by a cold worker.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import Text, cast, func, or_
from sqlalchemy.orm import Session

from ...models import NotificationOutbox
from ...security import JWT_SECRET
from ..common import public_id, utcnow
from ..employer.admissions import lock_application_set
from ..notification_consent.models import NotificationPreference
from ..notification_consent.projection import (
    next_weekly_due,
    projection_matches,
    snapshot_owner,
)
from ..notification_consent.service import email_digest, unsubscribe_token
from ..recovery.store import GuardDenied, GuardUnavailable
from .digest_content import build_digest_content
from .mail_budget import guarded_send, reserve_messages
from .native import ConfiguredDigestNative
from .provider import ResendAttempt

TYPE = "weekly_digest"
PENDING, PROCESSING, UNKNOWN = "digest_pending", "digest_processing", "digest_unknown"
SENT, CANCELLED, FAILED = "digest_sent", "digest_cancelled", "digest_failed"
MAX_OWNERS = 20
MAX_CAPSULE_BYTES = 16 * 1024
_PURPOSE = b"hirewiz:weekly-digest-outbox:v1"


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _week(value: datetime) -> str:
    stamp = _utc(value).isocalendar()
    return f"{stamp.year:04d}-W{stamp.week:02d}"


def _configured() -> bool:
    return (os.getenv("NOTIFICATION_DIGESTS_ENABLED", "false") == "true"
        and os.getenv("LIFECYCLE_EMAILS_ENABLED", "false") == "true"
        and bool((os.getenv("RESEND_API_KEY") or "").strip())
        and bool((os.getenv("EMAIL_FROM") or "").strip()))


def _key() -> bytes:
    return hmac.new(JWT_SECRET.encode("utf-8"), _PURPOSE, hashlib.sha256).digest()


def _aad(item: NotificationOutbox, metadata: dict) -> bytes:
    return json.dumps([_PURPOSE.decode(), item.id, item.user_id, item.idempotency_key,
        item.recipient, metadata], sort_keys=True, separators=(",", ":")).encode()


def _seal(item: NotificationOutbox, metadata: dict, subject: str, text: str) -> dict:
    plain = json.dumps({"subject": subject, "text": text}, sort_keys=True,
                       separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if not 0 < len(plain) <= MAX_CAPSULE_BYTES:
        raise GuardUnavailable("Digest message is unavailable")
    nonce = os.urandom(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, plain, _aad(item, metadata))
    return {**metadata, "ciphertext": base64.urlsafe_b64encode(nonce + ciphertext).decode("ascii")}


def _open(item: NotificationOutbox, *, now: datetime) -> tuple[dict, str, str]:
    value = item.payload
    fields = {"version", "iso_week", "subject_uuid", "account_binding_id", "consent_generation",
              "policy_version", "recipient_sha256", "message_sha256", "expires_at_ms", "ciphertext"}
    if (item.notification_type != TYPE or type(value) is not dict or set(value) != fields
            or type(value["version"]) is not int or value["version"] != 1
            or type(value["consent_generation"]) is not int or value["consent_generation"] <= 0
            or type(value["expires_at_ms"]) is not int
            or int(_utc(now).timestamp() * 1000) >= value["expires_at_ms"]
            or value["iso_week"] != _week(now)
            or type(value["ciphertext"]) is not str or len(value["ciphertext"]) > 24 * 1024):
        raise GuardDenied("Digest message is unavailable or expired")
    metadata = {key: item for key, item in value.items() if key != "ciphertext"}
    try:
        raw = base64.b64decode(value["ciphertext"], altchars=b"-_", validate=True)
        plain = AESGCM(_key()).decrypt(raw[:12], raw[12:], _aad(item, metadata))
        if not 0 < len(plain) <= MAX_CAPSULE_BYTES:
            raise ValueError
        body = json.loads(plain)
        if type(body) is not dict or set(body) != {"subject", "text"} or any(type(v) is not str for v in body.values()):
            raise ValueError
        return metadata, body["subject"], body["text"]
    except Exception:
        raise GuardDenied("Digest message binding is unavailable") from None


def _attempt(item: NotificationOutbox, subject: str, text: str) -> ResendAttempt:
    return ResendAttempt(api_key=(os.getenv("RESEND_API_KEY") or "").strip(),
        sender=(os.getenv("EMAIL_FROM") or "").strip(), recipient=item.recipient,
        subject=subject, text=text, idempotency_key=item.idempotency_key)


def _defer(db: Session, user_id: int, generation: int, *, now: datetime, empty=False) -> None:
    lock_application_set(db, user_id)
    row = db.query(NotificationPreference).filter_by(user_id=user_id).with_for_update().one_or_none()
    if (row is not None and row.enabled and row.native_generation == generation
            and row.next_due_at is not None and _utc(row.next_due_at) <= now):
        row.next_due_at = next_weekly_due(now) if empty else now + timedelta(hours=1)
    db.commit()


def _queue_owner(db: Session, user_id: int, *, now: datetime) -> bool:
    revision, email = snapshot_owner(db, user_id)
    row = db.get(NotificationPreference, user_id, populate_existing=True)
    if row is None or not row.enabled or row.next_due_at is None or _utc(row.next_due_at) > now:
        db.rollback()
        return False
    generation = int(row.native_generation)
    # Empty digests need neither native nor provider calls.
    content = build_digest_content(db, user_id, now=now)
    db.rollback()
    if content is None:
        _defer(db, user_id, generation, now=now, empty=True)
        return False
    native = ConfiguredDigestNative()
    head = native.read_current(revision, email_digest(email))
    current, current_email = snapshot_owner(db, user_id)
    row = db.query(NotificationPreference).filter_by(user_id=user_id).populate_existing().with_for_update().one_or_none()
    if (current != revision or current_email != email or head is None or not head.enabled
            or not projection_matches(row, head) or row is None or row.next_due_at is None
            or _utc(row.next_due_at) > now):
        raise GuardDenied("Digest scheduling projection is not current")
    # Rebuild after network IO so removed owner roles cannot enter a new queue.
    content = build_digest_content(db, user_id, now=now)
    if content is None:
        row.next_due_at = next_weekly_due(now)
        db.commit()
        return False
    week = _week(now)
    key = f"weekly-digest:{revision.subject_uuid}:{week}"
    existing = db.query(NotificationOutbox).filter_by(idempotency_key=key).one_or_none()
    if existing is not None:
        row.next_due_at = next_weekly_due(now)
        db.commit()
        return False
    expiry = min(now + timedelta(hours=24), next_weekly_due(now))
    funded = reserve_messages(db, [key], category="lifecycle", expires_at=expiry)[0]
    expiry = min(expiry, _utc(funded.expires_at))
    if expiry <= now:
        raise GuardUnavailable("Original digest funding window expired")
    proof = unsubscribe_token(revision, head, now_ms=int(now.timestamp() * 1000))
    text = content.text + "\n\nYou enabled weekly role updates. To stop these emails, open:\n" \
        + "https://www.hirewizhq.com/notifications/unsubscribe#token=" + proof
    item = NotificationOutbox(id=public_id("ntf"), user_id=user_id, notification_type=TYPE,
        channel="email", recipient=email, payload={}, idempotency_key=key, status=PENDING,
        attempt_count=0, available_at=now, created_at=now, updated_at=now)
    attempt = _attempt(item, content.subject, text)
    metadata = {"version": 1, "iso_week": week, "subject_uuid": str(revision.subject_uuid),
        "account_binding_id": str(revision.account_binding_id), "consent_generation": head.generation,
        "policy_version": head.policy_version, "recipient_sha256": email_digest(email),
        "message_sha256": attempt.message_sha256, "expires_at_ms": int(expiry.timestamp() * 1000)}
    item.payload = _seal(item, metadata, content.subject, text)
    db.add(item)
    row.next_due_at = next_weekly_due(now)
    db.commit()
    return True


def _finish(db: Session, user_id: int, identity: str, status: str) -> None:
    lock_application_set(db, user_id)
    item = db.query(NotificationOutbox).filter_by(id=identity, user_id=user_id,
        notification_type=TYPE, status=PROCESSING).with_for_update().one_or_none()
    if item is not None:
        item.status, item.claimed_at, item.updated_at = status, None, utcnow()
        item.last_error = None if status == SENT else "DigestDeliveryUnavailable"
        if status == SENT:
            item.sent_at = utcnow()
        # Native cadence and detached accounting retain retry/unknown history;
        # personal content and opt-out capability are no longer needed here.
        item.payload = {}
    db.commit()


def _deliver(db: Session, identity: str) -> str:
    found = db.query(NotificationOutbox.user_id).filter_by(id=identity, notification_type=TYPE,
        status=PENDING).one_or_none()
    db.rollback()
    if found is None or type(found[0]) is not int:
        return CANCELLED
    user_id = found[0]
    try:
        revision, email = snapshot_owner(db, user_id)
        item = db.query(NotificationOutbox).filter_by(id=identity, user_id=user_id,
            notification_type=TYPE, status=PENDING).with_for_update().one_or_none()
        if item is None or _utc(item.available_at) > utcnow():
            db.rollback()
            return CANCELLED
        metadata, subject, text = _open(item, now=utcnow())
        row = db.get(NotificationPreference, user_id, populate_existing=True)
        if (email != item.recipient or metadata["recipient_sha256"] != email_digest(email)
                or metadata["subject_uuid"] != str(revision.subject_uuid)
                or metadata["account_binding_id"] != str(revision.account_binding_id)
                or row is None or not row.enabled or row.native_generation != metadata["consent_generation"]
                or row.recipient_sha256 != metadata["recipient_sha256"]):
            raise GuardDenied("Queued digest does not match the current owner")
        outgoing = NotificationOutbox(id=item.id, user_id=user_id, notification_type=TYPE,
            recipient=item.recipient, idempotency_key=item.idempotency_key, payload=dict(item.payload))
        attempt = _attempt(outgoing, subject, text)
        if attempt.message_sha256 != metadata["message_sha256"]:
            raise GuardDenied("Digest outgoing request changed")
        item.status, item.claimed_at, item.updated_at = PROCESSING, utcnow(), utcnow()
        item.attempt_count = int(item.attempt_count or 0) + 1
        db.commit()
    except Exception:
        db.rollback()
        # No callback was admitted. Cancel the exact still-pending row under the
        # owner fence; never turn a claim/commit ambiguity into a resend.
        lock_application_set(db, user_id)
        item = db.query(NotificationOutbox).filter_by(id=identity, user_id=user_id,
            notification_type=TYPE, status=PENDING).with_for_update().one_or_none()
        if item is not None:
            item.status, item.payload, item.updated_at = CANCELLED, {}, utcnow()
        db.commit()
        return CANCELLED if item is not None else UNKNOWN

    def callback():
        if not _configured():
            raise GuardUnavailable("Digest delivery paused")
        _open(outgoing, now=utcnow())  # Expiry/week rechecked after native IO.
        if attempt.message_sha256 != metadata["message_sha256"]:
            raise GuardDenied("Digest request binding changed")
        def provider():
            # Global funding may have waited after native admission. Recheck
            # current SQL identity/mailbox/projection immediately before HTTP,
            # then release the owner fence before the network operation.
            from ...database import SessionLocal
            with SessionLocal() as current_db:
                fresh, recipient = snapshot_owner(current_db, user_id)
                preference = current_db.get(NotificationPreference, user_id, populate_existing=True)
                valid = (fresh == revision and recipient == outgoing.recipient
                    and preference is not None and preference.enabled
                    and preference.native_generation == metadata["consent_generation"]
                    and preference.recipient_sha256 == metadata["recipient_sha256"])
                current_db.rollback()
            if not valid or not _configured():
                raise GuardDenied("Digest owner changed before provider admission")
            _open(outgoing, now=utcnow())
            return attempt()
        return guarded_send(outgoing.idempotency_key, provider, category="lifecycle",
            expires_at=datetime.fromtimestamp(metadata["expires_at_ms"] / 1000, UTC))

    try:
        native = ConfiguredDigestNative()
        result = native.execute_once(revision, email_sha256=email_digest(email),
            consent_generation=metadata["consent_generation"], policy_version=metadata["policy_version"],
            message_sha256=metadata["message_sha256"], expected_week=metadata["iso_week"],
            callback=callback, before_retry=attempt.before_retry)
        status = SENT if result.outcome == "sent" else FAILED if result.outcome in {"permanent", "known_retryable"} else UNKNOWN
    except Exception:
        status = UNKNOWN
    db.rollback()
    _finish(db, user_id, identity, status)
    return status


@dataclass(frozen=True)
class DigestDeliverySummary:
    configured: bool
    queued: int = 0
    claimed: int = 0
    sent: int = 0
    failed: int = 0


def clear_expired_digest_payloads(db: Session, *, now: datetime) -> int:
    """Drop personal snapshots after at most 24h even while sending is paused.

    Native cadence/outcomes and detached funding records stay intact. Unknown
    outbox metadata is retained; clearing its message never grants a retry.
    """
    stale = db.query(NotificationOutbox).filter(NotificationOutbox.notification_type == TYPE,
        NotificationOutbox.status == PROCESSING,
        or_(NotificationOutbox.claimed_at.is_(None), NotificationOutbox.claimed_at < now - timedelta(minutes=15))).update(
            {NotificationOutbox.status: UNKNOWN, NotificationOutbox.claimed_at: None,
             NotificationOutbox.payload: {}, NotificationOutbox.updated_at: now}, synchronize_session=False)
    expired = db.query(NotificationOutbox).filter(NotificationOutbox.notification_type == TYPE,
        NotificationOutbox.created_at <= now - timedelta(hours=24))
    cancelled = expired.filter(NotificationOutbox.status == PENDING).update(
        {NotificationOutbox.payload: {}, NotificationOutbox.status: CANCELLED,
         NotificationOutbox.updated_at: now}, synchronize_session=False)
    # Keep original terminal timestamps so ordinary retention can eventually
    # remove completed rows. Do not hydrate encrypted bodies during cleanup.
    cleared = expired.filter(NotificationOutbox.status != PENDING,
        func.length(cast(NotificationOutbox.payload, Text)) > 2).update(
            {NotificationOutbox.payload: {}, NotificationOutbox.updated_at: NotificationOutbox.updated_at},
            synchronize_session=False)
    return stale + cancelled + cleared


def deliver_pending_digests(db: Session, *, limit: int = MAX_OWNERS) -> DigestDeliverySummary:
    if not _configured():
        return DigestDeliverySummary(False)
    if type(limit) is not int or not 1 <= limit <= MAX_OWNERS:
        raise ValueError("Bounded digest batch required")
    now = utcnow()
    # An interrupted callback is unknown; no SQL claim restoration can replay it.
    db.query(NotificationOutbox).filter(NotificationOutbox.notification_type == TYPE,
        NotificationOutbox.status == PROCESSING,
        NotificationOutbox.claimed_at < now - timedelta(minutes=15)).update(
            {NotificationOutbox.status: UNKNOWN, NotificationOutbox.claimed_at: None,
             NotificationOutbox.payload: {}, NotificationOutbox.updated_at: now}, synchronize_session=False)
    db.commit()
    owners = [row[0] for row in db.query(NotificationPreference.user_id).filter(
        NotificationPreference.enabled.is_(True), NotificationPreference.next_due_at <= now).order_by(
        NotificationPreference.next_due_at.asc(), NotificationPreference.user_id.asc()).limit(limit).all()]
    db.rollback()
    queued = 0
    for user_id in owners:
        generation = db.query(NotificationPreference.native_generation).filter_by(user_id=user_id).scalar()
        db.rollback()
        try:
            queued += int(_queue_owner(db, user_id, now=now))
        except Exception:
            db.rollback()
            if type(generation) is int:
                _defer(db, user_id, generation, now=now)
    ids = [row[0] for row in db.query(NotificationOutbox.id).filter_by(notification_type=TYPE,
        status=PENDING).filter(NotificationOutbox.available_at <= now).order_by(
            NotificationOutbox.available_at.asc(), NotificationOutbox.id.asc()).limit(limit).all()]
    db.rollback()
    results = [_deliver(db, identity) for identity in ids]
    return DigestDeliverySummary(True, queued, len(ids), results.count(SENT), len(results) - results.count(SENT))
