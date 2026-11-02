"""Fresh mailbox confirmation erases only an already denied, proved owner.

Generic native denial is not privacy intent. A purpose-bound single-use email
proof and explicit confirmation supply that new intent. Native ownership reads
finish before SQL locks; no login, reactivation, native POST/action or signing is
performed. The detached completion audit and SQL erasure commit atomically.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models import AdminAuditEvent, CandidatePasswordAccount, PasswordResetToken, User
from ...security import JWT_SECRET
from ..career.evidence_write_fence import require_installed
from ..common import public_id, utcnow
from ..notifications import privacy_erasure as mail
from . import deletion_provenance as native
from .account_erasure import erase_account_sql, lock_account_erasure

PURPOSE, VERSION = "privacy_erasure", "privacy-erasure-confirmation-schema26-v1"
ACTION = "privacy_erasure_completed_schema26_v1"
TTL, COOLDOWN = timedelta(minutes=10), timedelta(seconds=60)


class ErasureLinkInvalid(ValueError):
    pass


class ErasureUnavailable(RuntimeError):
    pass


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def lookup(token: str) -> str:
    return hmac.new(JWT_SECRET.encode(), b"hirewiz:privacy-erasure-lookup:v1\0" + token.encode(), hashlib.sha256).hexdigest()


def _mac(row, nonce):
    key = hmac.new(JWT_SECRET.encode(), b"hirewiz:privacy-erasure-proof:v1", hashlib.sha256).digest()
    binding = {"version": VERSION, "purpose": row.purpose, "id": row.id, "user_id": row.user_id,
        "email_sha256": row.email_sha256, "credential_sha256": row.credential_sha256,
        "native_revision_sha256": row.native_revision_sha256, "legacy_epoch": row.legacy_epoch,
        "expires_at": row.expires_at.replace(tzinfo=UTC).isoformat(timespec="microseconds")}
    return hmac.new(key, json.dumps(binding, sort_keys=True, separators=(",", ":")).encode() + b"\0" + nonce,
        hashlib.sha256).digest()[:16]


def issue_token(row):
    if row.purpose != PURPOSE:
        raise ErasureLinkInvalid()
    nonce = secrets.token_bytes(16)
    return base64.urlsafe_b64encode(nonce + _mac(row, nonce)).decode().rstrip("=")


def valid_token(row, token):
    try:
        raw = base64.b64decode(token + "=", altchars=b"-_", validate=True)
        return (row.purpose == PURPOSE and len(raw) == 32
            and base64.urlsafe_b64encode(raw).decode().rstrip("=") == token
            and hmac.compare_digest(row.token_sha256, lookup(token))
            and hmac.compare_digest(raw[16:], _mac(row, raw[:16])))
    except (ValueError, TypeError):
        return False


def _schema26(db):
    if db.get_bind().dialect.name != "postgresql":
        raise ErasureUnavailable()
    if list(db.execute(text("SELECT version_num FROM public.alembic_version")).scalars()) != ["20261031_0026"]:
        raise ErasureUnavailable()
    require_installed(db.connection(), "public")


def _binding(owner, account):
    revision = native.closed_revision(account, owner)
    return (owner.id, digest(owner.email), digest(owner.password_hash), int(owner.credential_epoch or 0), revision)


def _audit_id(token_hash):
    return "aud_erase26_" + token_hash[:52]


def _completed(db, token_hash, *, expected_owner=None):
    audit = db.get(AdminAuditEvent, _audit_id(token_hash), populate_existing=True)
    if audit is None:
        return None
    value = audit.after_state
    fields = {"version", "purpose", "token_sha256", "native_revision_sha256", "owner_id",
        "credential_sha256", "email_sha256", "legacy_epoch", "confirmed"}
    if (audit.action != ACTION or audit.target_type != PURPOSE or audit.actor_user_id is not None
            or type(value) is not dict or set(value) != fields or value["version"] != VERSION
            or value["purpose"] != PURPOSE or value["token_sha256"] != token_hash or value["confirmed"] is not True
            or type(value["owner_id"]) is not int or value["owner_id"] <= 0
            or audit.target_id != str(value["owner_id"])
            or type(value["legacy_epoch"]) is not int or not 0 <= value["legacy_epoch"] < 2**63
            or expected_owner is not None and value["owner_id"] != expected_owner
            or any(re.fullmatch(r"[a-f0-9]{64}", str(value[key])) is None for key in (
                "native_revision_sha256", "credential_sha256", "email_sha256"))):
        raise ErasureLinkInvalid()
    return value


def _no_live_owner(db, uid):
    from .account_erasure import erased_owner_rows_absent
    if not erased_owner_rows_absent(db, uid):
        raise ErasureUnavailable()


def request_erasure(db: Session, email: str, *, now: datetime | None = None, read_native=None):
    """Generic request: no token/outbox is accepted before its funded allowance."""
    if not mail.configured():
        raise ErasureUnavailable()
    fixed_now = now
    def clock():
        return fixed_now if fixed_now is not None else utcnow()
    _schema26(db)
    owner = db.query(User).filter_by(email=email.strip().lower()).one_or_none()
    account = db.get(CandidatePasswordAccount, owner.id) if owner is not None else None
    try:
        before = _binding(owner, account)
        recipient = owner.email
    except Exception:
        db.rollback()
        return None
    db.rollback()
    try:
        proof = (read_native or native.read_closed_ownership)(before[4], before[1])
    except Exception:
        return None  # Denied/missing/unknown accounts share the same public 202.
    if type(proof) is not str or re.fullmatch(r"[a-f0-9]{64}", proof) is None:
        return None
    issued_at = clock()  # Native observation has finished; no proof existed before this point.
    row = PasswordResetToken(id=public_id("ers"), purpose=PURPOSE, user_id=before[0], email_sha256=before[1],
        credential_sha256=before[2], legacy_epoch=before[3], native_revision_sha256=proof,
        status="PENDING", created_at=issued_at, expires_at=issued_at + TTL)
    token = issue_token(row)
    row.token_sha256 = lookup(token)
    owner = db.query(User).filter_by(id=before[0]).with_for_update().one_or_none()
    account = db.query(CandidatePasswordAccount).filter_by(user_id=before[0]).with_for_update().one_or_none()
    try:
        if _binding(owner, account) != before:
            raise ErasureLinkInvalid()
    except Exception:
        db.rollback()
        return None
    current = clock()  # Refresh after owner lock waits; never extend the issued TTL.
    if current >= row.expires_at.replace(tzinfo=UTC):
        db.rollback()
        return None
    last = db.query(PasswordResetToken).filter_by(user_id=owner.id).order_by(PasswordResetToken.created_at.desc()).first()
    if last is not None and current < last.created_at.replace(tzinfo=UTC) + COOLDOWN:
        db.rollback()
        return None
    from ..notifications.mail_budget import reserve_messages
    # Existing reset/generic acceptance takes owner locks before the shared
    # allowance lock. Keep that order and fund before accepting token/outbox.
    reserve_messages(db, ["privacy-erasure:" + row.id], category=PURPOSE, expires_at=row.expires_at)
    current = clock()
    if current >= row.expires_at.replace(tzinfo=UTC):
        db.rollback()
        return None
    for old in db.query(PasswordResetToken).filter_by(user_id=owner.id, purpose=PURPOSE, status="PENDING"):
        old.status, old.consumed_at = "INVALIDATED", current
    db.add(row)
    item = mail.enqueue(db, user_id=owner.id, recipient=recipient, proof_id=row.id, token=token, expires_at=row.expires_at)
    if clock() >= row.expires_at.replace(tzinfo=UTC):
        db.rollback()
        return None
    db.commit()
    return item.id


def confirm_erasure(db: Session, token: str, confirm_erasure: bool, *, now=None, read_native=None):
    if confirm_erasure is not True or type(token) is not str or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
        raise ErasureLinkInvalid()
    fixed_now, token_hash = now, lookup(token)
    def clock():
        return fixed_now if fixed_now is not None else utcnow()
    _schema26(db)
    done = _completed(db, token_hash)
    if done is not None:
        uid = done["owner_id"]
        db.rollback()
        lock_account_erasure(db, uid)
        if _completed(db, token_hash, expected_owner=uid) is None:
            raise ErasureUnavailable()
        _no_live_owner(db, uid)
        db.rollback()
        return
    row = db.query(PasswordResetToken).filter_by(token_sha256=token_hash, purpose=PURPOSE).one_or_none()
    if row is None or row.status != "PENDING" or row.consumed_at is not None or not valid_token(row, token):
        db.rollback()
        raise ErasureLinkInvalid()
    uid = row.user_id
    owner, account = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
    try:
        before = _binding(owner, account)
    except Exception:
        db.rollback()
        raise ErasureLinkInvalid() from None
    expected = (row.email_sha256, row.credential_sha256, row.legacy_epoch, row.native_revision_sha256)
    if before[1:4] != expected[:3] or clock() >= row.expires_at.replace(tzinfo=UTC):
        db.rollback()
        raise ErasureLinkInvalid()
    db.rollback()
    try:
        proof = (read_native or native.read_closed_ownership)(before[4], before[1])
    except Exception:
        raise ErasureUnavailable() from None
    if type(proof) is not str or re.fullmatch(r"[a-f0-9]{64}", proof) is None or proof != expected[3]:
        raise ErasureLinkInvalid()
    lock_account_erasure(db, uid)
    done = _completed(db, token_hash, expected_owner=uid)
    if done is not None:
        _no_live_owner(db, uid)
        db.rollback()
        return
    owner = db.query(User).filter_by(id=uid).with_for_update().one_or_none()
    account = db.query(CandidatePasswordAccount).filter_by(user_id=uid).with_for_update().one_or_none()
    row = db.query(PasswordResetToken).filter_by(token_sha256=token_hash, purpose=PURPOSE).with_for_update().one_or_none()
    checked_at = clock()  # The final authority check uses the clock after native and SQL lock IO.
    try:
        current = _binding(owner, account)
    except Exception:
        db.rollback()
        raise ErasureLinkInvalid() from None
    if (current != before or row is None or row.status != "PENDING" or row.consumed_at is not None
            or not valid_token(row, token) or checked_at >= row.expires_at.replace(tzinfo=UTC)
            or (row.email_sha256, row.credential_sha256, row.legacy_epoch, row.native_revision_sha256) != expected):
        db.rollback()
        raise ErasureLinkInvalid()
    completion = {"version": VERSION, "purpose": PURPOSE, "token_sha256": token_hash,
        "native_revision_sha256": proof, "owner_id": uid, "credential_sha256": expected[1],
        "email_sha256": expected[0], "legacy_epoch": expected[2], "confirmed": True}
    expiry = row.expires_at.replace(tzinfo=UTC)
    row.status, row.consumed_at = "COMPLETED", checked_at
    try:
        db.flush([row])
        erase_account_sql(db, uid)  # Also removes token/mail PII, including its User FK.
        db.add(AdminAuditEvent(id=_audit_id(token_hash), actor_user_id=None, actor_email="privacy-erasure-confirmation",
            action=ACTION, target_type=PURPOSE, target_id=str(uid), reason="Fresh purpose-bound mailbox proof and explicit erasure confirmation",
            before_state={}, after_state=completion, correlation_id=None, created_at=checked_at))
        if clock() >= expiry:
            raise ErasureLinkInvalid()
        db.commit()
    except ErasureLinkInvalid:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise ErasureUnavailable() from None  # Same token observes the exact audit on an unknown commit.
