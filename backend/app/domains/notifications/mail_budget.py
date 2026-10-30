"""Detached provider-wide attempt envelopes; never an email/provider acceptance.

Three attempt units are reserved for each admitted message in its original UTC
funding day/month. Neither owner deletion, time rollover nor an unknown provider
outcome refunds that envelope. These controls govern this source only; provider-
wide enforcement still requires proof every historical sender is retired or fenced.
"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from ...models import AdminAuditEvent
from ..common import utcnow

ATTEMPTS = 3
DAILY_UNITS, MONTHLY_UNITS = 100, 1000
PRIVACY_DAILY, PRIVACY_MONTHLY = 20, 300
LOCK = 7349186502119
RESERVATION = "provider_mail_reserved_v1"
BEGIN = "provider_mail_attempt_begun_v1"
OUTCOME = "provider_mail_attempt_outcome_v1"
VERSION = "provider-mail-envelope-v1"
TTL = {"privacy_erasure": timedelta(minutes=10), "password_reset": timedelta(minutes=15),
    "lifecycle": timedelta(hours=24)}
LIFECYCLE_SCHEDULE_LIMIT = timedelta(hours=24)
MAX_TTL = {**TTL, "lifecycle": TTL["lifecycle"] + LIFECYCLE_SCHEDULE_LIMIT}


class MailBudgetError(RuntimeError):
    def __init__(self, outcome: Literal["unknown", "deferred", "permanent", "known_retryable"]):
        super().__init__("provider_mail_" + outcome)
        self.mail_outcome = outcome


@dataclass(frozen=True)
class ProviderResult:
    outcome: Literal["sent", "unknown", "permanent", "known_retryable"]
    receipt_sha256: str
    status_code: int | None = None


@dataclass(frozen=True)
class Reservation:
    message_sha256: str
    category: str
    expires_at: datetime


def _stamp(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _digest(value: str) -> str:
    # Stable across credential rotation: the same provider key must never gain
    # a fresh envelope because authentication custody changed.
    return hashlib.sha256(b"hirewiz:provider-mail-ledger:v1\0" + value.encode()).hexdigest()


def _id(kind: str, message: str, ordinal: int = 0) -> str:
    return "mail_" + _digest(f"{kind}:{message}:{ordinal}")[:59]


def _lock(db: Session) -> None:
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK})
    elif dialect != "sqlite":
        raise MailBudgetError("deferred")
    # SQLite may exercise inert local queue fixtures. guarded_send separately
    # requires PostgreSQL and cannot turn that fixture into provider authority.


def _find(db: Session, identity: str):
    pending = next((row for row in db.new if isinstance(row, AdminAuditEvent) and row.id == identity), None)
    return pending if pending is not None else db.get(AdminAuditEvent, identity)


def _record(db: Session, *, identity: str, action: str, message: str, value: dict, now: datetime):
    row = AdminAuditEvent(id=identity, actor_user_id=None, actor_email="provider-mail-ledger@system.invalid",
        action=action, target_type="provider_mail", target_id=message, reason="Bounded detached provider mail accounting",
        before_state={}, after_state=value, created_at=now)
    db.add(row)
    return row


def _reservation(row, message: str, category: str | None = None) -> Reservation:
    value = row.after_state
    if (row.action != RESERVATION or row.target_type != "provider_mail" or row.target_id != message
            or row.actor_user_id is not None or type(value) is not dict
            or set(value) != {"version", "message_sha256", "category", "units", "funding_day", "funding_month", "expires_at"}
            or value["version"] != VERSION or value["message_sha256"] != message or value["units"] != ATTEMPTS
            or value["category"] not in TTL or category is not None and value["category"] != category):
        raise MailBudgetError("unknown")
    created = _stamp(row.created_at)
    try:
        expires = datetime.fromisoformat(value["expires_at"])
    except (ValueError, TypeError):
        raise MailBudgetError("unknown") from None
    if (expires.tzinfo is None or value["funding_day"] != created.date().isoformat()
            or value["funding_month"] != created.strftime("%Y-%m")
            or not created < expires <= created + MAX_TTL[value["category"]]):
        raise MailBudgetError("unknown")
    return Reservation(message, value["category"], expires)


def reserve_messages(db: Session, keys: list[str], *, category: str,
                     expires_at: datetime | None = None, now: datetime | None = None) -> list[Reservation]:
    """Caller owns the transaction; all requested envelopes reserve or none do."""
    if (category not in TTL or type(keys) is not list or not 1 <= len(keys) <= 10
            or any(type(key) is not str or not 1 <= len(key) <= 200 for key in keys) or len(set(keys)) != len(keys)):
        raise MailBudgetError("permanent")
    _lock(db)
    # Lock contention can cross the funding period or an explicit expiry. Only
    # the deterministic test seam may supply a time from before acquisition.
    current = _stamp(now if now is not None else utcnow())
    # Lifecycle admission may reserve a bounded future schedule plus its normal
    # delivery window. Existing envelopes retain their original exact expiry.
    deadline = min(_stamp(expires_at), current + MAX_TTL[category]) if expires_at is not None else current + TTL[category]
    found, missing = {}, []
    for key in keys:
        message = _digest(key)
        row = _find(db, _id("reservation", message))
        if row is not None:
            found[message] = _reservation(row, message, category)
        else:
            missing.append(message)
    if not missing:
        return [found[_digest(key)] for key in keys]
    if deadline <= current:
        raise MailBudgetError("permanent")
    month = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = db.query(AdminAuditEvent).filter(AdminAuditEvent.action == RESERVATION,
        AdminAuditEvent.created_at >= month).limit(MONTHLY_UNITS + 1).all()
    pending = [row for row in db.new if isinstance(row, AdminAuditEvent) and row.action == RESERVATION
        and row not in rows and _stamp(row.created_at) >= month]
    rows += pending
    if len(rows) > MONTHLY_UNITS:
        raise MailBudgetError("unknown")
    day_units = month_units = privacy_day = privacy_month = 0
    for row in rows:
        reservation = _reservation(row, row.target_id)
        created = _stamp(row.created_at)
        if created > current:
            raise MailBudgetError("unknown")
        month_units += ATTEMPTS
        today = created.date() == current.date()
        day_units += ATTEMPTS if today else 0
        if reservation.category == "privacy_erasure":
            privacy_month += 1
            privacy_day += int(today)
    extra = len(missing)
    if (day_units + extra * ATTEMPTS > DAILY_UNITS or month_units + extra * ATTEMPTS > MONTHLY_UNITS
            or category == "privacy_erasure" and (privacy_day + extra > PRIVACY_DAILY or privacy_month + extra > PRIVACY_MONTHLY)):
        raise MailBudgetError("deferred")
    added = []
    for message in missing:
        value = {"version": VERSION, "message_sha256": message, "category": category, "units": ATTEMPTS,
            "funding_day": current.date().isoformat(), "funding_month": current.strftime("%Y-%m"),
            "expires_at": deadline.isoformat()}
        added.append(_record(db, identity=_id("reservation", message), action=RESERVATION, message=message, value=value, now=current))
        found[message] = Reservation(message, category, deadline)
    db.flush(added)
    return [found[_digest(key)] for key in keys]


def _result(value) -> ProviderResult:
    if (type(value) is not ProviderResult or value.outcome not in {"sent", "unknown", "permanent", "known_retryable"}
            or re.fullmatch(r"[a-f0-9]{64}", value.receipt_sha256) is None
            or value.status_code is not None and (type(value.status_code) is not int or not 100 <= value.status_code <= 599)):
        raise MailBudgetError("unknown")
    if (value.outcome == "sent" and (value.status_code is None or not 200 <= value.status_code < 300)
            or value.outcome == "known_retryable" and value.status_code != 429
            or value.outcome == "permanent" and (value.status_code is None or not 400 <= value.status_code < 500
                or value.status_code in {408, 429})):
        raise MailBudgetError("unknown")
    return value


def _attempt(db, reservation: Reservation, now: datetime):
    message = reservation.message_sha256
    for ordinal in range(1, ATTEMPTS + 1):
        begun = _find(db, _id("begin", message, ordinal))
        finished = _find(db, _id("outcome", message, ordinal))
        if begun is None:
            if finished is not None:
                raise MailBudgetError("unknown")
            if now >= reservation.expires_at:
                raise MailBudgetError("permanent")
            row = _record(db, identity=_id("begin", message, ordinal), action=BEGIN, message=message,
                value={"version": VERSION, "message_sha256": message, "ordinal": ordinal}, now=now)
            db.flush([row])
            return ordinal, None
        expected = {"version": VERSION, "message_sha256": message, "ordinal": ordinal}
        if (begun.action != BEGIN or begun.target_type != "provider_mail" or begun.actor_user_id is not None
                or begun.target_id != message or begun.after_state != expected or finished is None
                or finished.action != OUTCOME or finished.target_type != "provider_mail"
                or finished.actor_user_id is not None or finished.target_id != message):
            raise MailBudgetError("unknown")
        outcome = finished.after_state
        if type(outcome) is not dict or set(outcome) != {*expected, "outcome", "receipt_sha256", "status_code"}:
            raise MailBudgetError("unknown")
        if any(outcome[key] != value for key, value in expected.items()):
            raise MailBudgetError("unknown")
        receipt = _result(ProviderResult(outcome["outcome"], outcome["receipt_sha256"], outcome["status_code"]))
        if receipt.outcome == "sent":
            return ordinal, receipt
        if receipt.outcome != "known_retryable":
            raise MailBudgetError(receipt.outcome)
    raise MailBudgetError("permanent")


def guarded_send(key: str, callback: Callable[[], ProviderResult], *, category: str,
                 expires_at: datetime | None = None, session_factory=None) -> ProviderResult:
    """Exactly one callback outside SQL locks; an absent outcome never retries."""
    if session_factory is None:
        from ...database import SessionLocal
        session_factory = SessionLocal
    try:
        with session_factory() as db:
            if db.get_bind().dialect.name != "postgresql":
                raise MailBudgetError("deferred")
            reservation = reserve_messages(db, [key], category=category, expires_at=expires_at)[0]
            ordinal, completed = _attempt(db, reservation, _stamp(utcnow()))
            db.commit()  # Provider callback is forbidden before this acknowledgement.
    except MailBudgetError:
        raise
    except Exception:
        raise MailBudgetError("unknown") from None
    if completed is not None:
        return completed
    if _stamp(utcnow()) >= reservation.expires_at:
        # Begin is already detached and committed. Preserve its missing outcome
        # as unknown rather than inventing a provider receipt for an unsent call.
        raise MailBudgetError("unknown")
    try:
        receipt = _result(callback())
    except Exception:
        receipt = ProviderResult("unknown", hashlib.sha256(b"provider-mail-outcome-unknown").hexdigest())
    try:
        with session_factory() as db:
            _lock(db)
            retained = _find(db, _id("reservation", reservation.message_sha256))
            begun = _find(db, _id("begin", reservation.message_sha256, ordinal))
            if (retained is None or _reservation(retained, reservation.message_sha256, category) != reservation
                    or begun is None or begun.action != BEGIN or begun.actor_user_id is not None
                    or begun.target_type != "provider_mail" or begun.target_id != reservation.message_sha256
                    or begun.after_state != {"version": VERSION, "message_sha256": reservation.message_sha256, "ordinal": ordinal}):
                raise MailBudgetError("unknown")
            value = {"version": VERSION, "message_sha256": reservation.message_sha256, "ordinal": ordinal,
                "outcome": receipt.outcome, "receipt_sha256": receipt.receipt_sha256, "status_code": receipt.status_code}
            existing = _find(db, _id("outcome", reservation.message_sha256, ordinal))
            if existing is not None:
                if existing.action != OUTCOME or existing.after_state != value:
                    raise MailBudgetError("unknown")
            else:
                row = _record(db, identity=_id("outcome", reservation.message_sha256, ordinal), action=OUTCOME,
                    message=reservation.message_sha256, value=value, now=_stamp(utcnow()))
                db.flush([row])
            db.commit()
    except Exception:
        raise MailBudgetError("unknown") from None
    if receipt.outcome != "sent":
        raise MailBudgetError(receipt.outcome)
    return receipt
