"""One-use email proof authorizes password change, never enrollment or sign-in."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ...models import CandidatePasswordAccount, PasswordResetToken, User
from ...security import JWT_SECRET, hash_password
from ..common import public_id, utcnow
from ..notifications import password_reset as delivery
from ..recovery.store import GuardDenied
from .service import production_candidate_accounts, revision_from_row

TTL = timedelta(minutes=15)
COOLDOWN = timedelta(seconds=60)


class ResetLinkInvalid(ValueError):
    """Absent, expired, used or changed ownership proof."""


class ResetUnavailable(RuntimeError):
    """No replay of a possibly committed native credential change."""


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _token_mac(row: PasswordResetToken, nonce: bytes) -> bytes:
    key = hmac.new(JWT_SECRET.encode(), b"hirewiz:password-reset-proof:v1", hashlib.sha256).digest()
    binding = {"id": row.id, "user_id": row.user_id, "email_sha256": row.email_sha256,
        "credential_sha256": row.credential_sha256, "native_revision_sha256": row.native_revision_sha256,
        "legacy_epoch": row.legacy_epoch,
        "expires_at": row.expires_at.replace(tzinfo=UTC).isoformat(timespec="microseconds")}
    raw = json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()
    return hmac.new(key, raw + b"\0" + nonce, hashlib.sha256).digest()[:16]


def _issue_token(row: PasswordResetToken) -> str:
    nonce = secrets.token_bytes(16)
    return base64.urlsafe_b64encode(nonce + _token_mac(row, nonce)).decode().rstrip("=")


def _valid_token(row: PasswordResetToken, token: str) -> bool:
    try:
        if row.purpose != "password_reset":
            return False
        raw = base64.b64decode(token + "=", altchars=b"-_", validate=True)
        return (len(raw) == 32 and base64.urlsafe_b64encode(raw).decode().rstrip("=") == token
            and hmac.compare_digest(raw[16:], _token_mac(row, raw[:16])))
    except (ValueError, TypeError):
        return False


def revision_digest(value) -> str:
    return digest(json.dumps(value.model_dump(mode="json"), sort_keys=True, separators=(",", ":")))


def _native(db: Session, owner: User) -> str | None:
    row = db.query(CandidatePasswordAccount).filter_by(user_id=owner.id).with_for_update().one_or_none()
    return revision_digest(revision_from_row(row, owner.password_hash)) if row is not None else None


def request_reset(db: Session, email: str, *, now: datetime | None = None) -> str | None:
    if not delivery.configured():
        raise ResetUnavailable()
    now = now or utcnow()
    owner = db.query(User).filter_by(email=email.strip().lower()).with_for_update().one_or_none()
    if owner is None:
        db.rollback()
        return None
    last = db.query(PasswordResetToken).filter_by(user_id=owner.id, purpose="password_reset").order_by(PasswordResetToken.created_at.desc()).first()
    if last is not None and now < last.created_at.replace(tzinfo=UTC) + COOLDOWN:
        db.rollback()
        return None
    try:
        native = _native(db, owner)
    except GuardDenied:
        db.rollback()
        return None  # A pending/deleting native account cannot be silently recovered.
    for old in db.query(PasswordResetToken).filter_by(user_id=owner.id, purpose="password_reset", status="PENDING"):
        old.status, old.consumed_at = "INVALIDATED", now
    row = PasswordResetToken(id=public_id("rst"), purpose="password_reset", user_id=owner.id,
        email_sha256=digest(owner.email), credential_sha256=digest(owner.password_hash), native_revision_sha256=native,
        legacy_epoch=int(owner.credential_epoch or 0), status="PENDING", created_at=now, expires_at=now + TTL)
    token = _issue_token(row)
    row.token_sha256 = digest(token)
    db.add(row)
    item = delivery.enqueue(db, user_id=owner.id, recipient=owner.email, reset_id=row.id, token=token, expires_at=row.expires_at)
    db.commit()
    return item.id


def confirm_reset(db: Session, token: str, new_password: str, *, now: datetime | None = None, accounts=None) -> None:
    if re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None or not 10 <= len(new_password) <= 128:
        raise ResetLinkInvalid()
    now = now or utcnow()
    # Resolve only ownership before locking in the same owner->proof order as issuance.
    owner_id = db.query(PasswordResetToken.user_id).filter_by(token_sha256=digest(token), purpose="password_reset").scalar()
    if owner_id is None:
        db.rollback()
        raise ResetLinkInvalid()
    owner = db.query(User).filter_by(id=owner_id).with_for_update().one_or_none()
    row = db.query(PasswordResetToken).filter_by(token_sha256=digest(token), purpose="password_reset").with_for_update().one_or_none()
    if (owner is None or row is None or row.status != "PENDING" or row.consumed_at is not None
            or now >= row.expires_at.replace(tzinfo=UTC) or row.email_sha256 != digest(owner.email)
            or row.credential_sha256 != digest(owner.password_hash) or row.legacy_epoch != int(owner.credential_epoch or 0)
            or not _valid_token(row, token)):
        db.rollback()
        raise ResetLinkInvalid()
    try:
        current = _native(db, owner)
    except GuardDenied:
        db.rollback()
        raise ResetLinkInvalid() from None
    if current != row.native_revision_sha256 or not 0 <= int(owner.credential_epoch or 0) < 2**63 - 1:
        db.rollback()
        raise ResetLinkInvalid()
    encoded = hash_password(new_password)
    row.status, row.consumed_at = "PROCESSING", now
    for other in db.query(PasswordResetToken).filter_by(user_id=owner.id, purpose="password_reset", status="PENDING"):
        if other.id != row.id:
            other.status, other.consumed_at = "INVALIDATED", now
    reset_id = row.id
    if current is None:
        owner.password_hash, owner.credential_epoch = encoded, int(owner.credential_epoch or 0) + 1
        row.status = "COMPLETED"
        delivery.enqueue_changed(db, user_id=owner.id, recipient=owner.email, reset_id=reset_id)
        db.commit()
        return
    db.commit()  # One-use claim precedes independent native IO; no locks cross it.
    try:
        (accounts or production_candidate_accounts()).reset_password_from_email(reset_id, encoded, token)
    except Exception:  # noqa: BLE001 -- uncertain native outcomes never replay or expose provider text.
        db.rollback()
        failed = db.query(PasswordResetToken).filter_by(id=reset_id, purpose="password_reset").with_for_update().one()
        failed.status = "UNAVAILABLE"
        db.commit()
        raise ResetUnavailable() from None
