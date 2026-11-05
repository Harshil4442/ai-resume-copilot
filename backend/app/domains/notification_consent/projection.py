"""SQL digest discovery/cache, populated only from Root's original native ACK.

These functions perform no native/provider IO and own no commit. A SQL projection
or a matching hash is never a send grant; callers must separately perform fresh
native account, mailbox, consent, cadence and mail-budget admission.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from ...models import CandidatePasswordAccount, User
from ..candidate_accounts.contracts import CandidateCredentialRevision
from ..candidate_accounts.service import revision_from_row
from ..employer.admissions import lock_application_set
from ..recovery.contracts import fingerprint
from ..recovery.gcp_partitioned_contracts import DigestConsentHead
from ..recovery.store import GuardDenied
from .models import NotificationPreference


def _owner_id(value: int) -> None:
    if type(value) is not int or not 0 < value < 2**31:
        raise GuardDenied("Exact SQL account owner is required")


def _snapshot_locked(db: Session, user_id: int) -> tuple[CandidateCredentialRevision, str]:
    # Caller holds the same advisory owner lock used by account erasure, before
    # taking any User/account/preference row lock. Never hold it across native IO.
    user = (
        db.query(User)
        .filter(User.id == user_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    account = (
        db.query(CandidatePasswordAccount)
        .filter_by(user_id=user_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    if user is None or account is None:
        raise GuardDenied("Current enrolled notification owner is unavailable")
    email = user.email
    if (
        type(email) is not str
        or not email
        or len(email.encode()) > 320
        or email != email.strip().lower()
    ):
        raise GuardDenied("Current notification mailbox is unavailable")
    try:
        revision = revision_from_row(account, user.password_hash)
    except (ValueError, TypeError):
        raise GuardDenied("Current notification credential revision is unavailable") from None
    return revision, email


def snapshot_owner(db: Session, user_id: int) -> tuple[CandidateCredentialRevision, str]:
    """Take a fresh SQL snapshot; caller MUST roll back before native IO."""
    _owner_id(user_id)
    lock_application_set(db, user_id)
    return _snapshot_locked(db, user_id)


def head_sha256(head: DigestConsentHead) -> str:
    if type(head) is not DigestConsentHead:
        raise GuardDenied("Exact native digest head is required")
    return fingerprint(head.model_dump(mode="json"))


def next_weekly_due(now: datetime) -> datetime:
    if type(now) is not datetime or now.utcoffset() is None:
        raise GuardDenied("An aware current projection clock is required")
    current = now.astimezone(UTC)
    monday = (current + timedelta(days=7 - current.weekday())).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return monday


def _metadata(head: DigestConsentHead) -> dict:
    return {
        "subject_uuid": str(head.subject.subject_uuid),
        "account_binding_id": str(head.subject.account_binding_id),
        "principal_sha256": head.subject.principal_sha256,
        "native_generation": head.generation,
        "native_head_sha256": head_sha256(head),
        "recipient_sha256": head.email_sha256,
        "policy_version": head.policy_version,
        "enabled": head.enabled,
        "operation_id": str(head.operation_id),
    }


def projection_matches(row: NotificationPreference | None, head: DigestConsentHead) -> bool:
    """Metadata comparison only, never native freshness, ACK custody or authority."""
    if (
        row is None
        or type(head) is not DigestConsentHead
        or row.user_id != head.subject.candidate_id
    ):
        return False
    return all(getattr(row, name) == value for name, value in _metadata(head).items())


def apply_projection(
    db: Session,
    revision: CandidateCredentialRevision,
    email: str,
    head: DigestConsentHead,
    *,
    now: datetime,
) -> NotificationPreference:
    """Store only the original-ACK head supplied by the trusted orchestration.

    A retained/read_current head is not an original ACK and MUST NOT be routed
    here as reconciliation. The native caller owns that custody distinction.
    """
    if (
        type(revision) is not CandidateCredentialRevision
        or type(head) is not DigestConsentHead
        or type(email) is not str
    ):
        raise GuardDenied("Exact captured revision and original native digest head are required")
    _owner_id(revision.candidate_id)
    next_due = next_weekly_due(now)
    lock_application_set(db, revision.candidate_id)
    current, current_email = _snapshot_locked(db, revision.candidate_id)
    if current != revision or current_email != email:
        raise GuardDenied("Notification owner changed while native consent was pending")
    if (
        head.subject.model_dump(mode="json", exclude={"enrollment_operation"})
        != revision.model_dump(mode="json")
        or head.email_sha256 != hashlib.sha256(email.encode()).hexdigest()
    ):
        raise GuardDenied("Original native digest head does not match current SQL owner/mailbox")
    row = (
        db.query(NotificationPreference)
        .filter_by(user_id=revision.candidate_id)
        .populate_existing()
        .with_for_update()
        .one_or_none()
    )
    expected = _metadata(head)
    if row is not None:
        if (
            row.subject_uuid != str(revision.subject_uuid)
            or row.account_binding_id != str(revision.account_binding_id)
            or row.principal_sha256 != revision.principal_sha256
            or type(row.native_generation) is not int
            or row.native_generation < 1
        ):
            raise GuardDenied("Notification projection belongs to another account lifetime")
        if head.generation < row.native_generation:
            raise GuardDenied("Notification consent projection cannot roll back")
        if head.generation == row.native_generation:
            if not projection_matches(row, head) or bool(row.enabled) != (
                row.next_due_at is not None
            ):
                raise GuardDenied(
                    "Same-generation notification projection disagrees with native consent"
                )
            return row  # Idempotency preserves the existing cadence and update time.
    else:
        row = NotificationPreference(user_id=revision.candidate_id)
        db.add(row)
    for name, value in expected.items():
        setattr(row, name, value)
    row.next_due_at = next_due if head.enabled else None
    row.updated_at = now.astimezone(UTC)
    return row


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.utcoffset() is None else value.astimezone(UTC)


def export_preference(db: Session, user_id: int) -> dict | None:
    """Owner export of cache metadata only; never adopt it into native state."""
    _owner_id(user_id)
    row = db.query(NotificationPreference).filter_by(user_id=user_id).one_or_none()
    if row is None:
        return None
    return {
        "projection_only": True,
        "enabled": bool(row.enabled),
        "consent_generation": row.native_generation,
        "policy_version": row.policy_version,
        "next_due_at": _utc(row.next_due_at),
        "updated_at": _utc(row.updated_at),
    }
