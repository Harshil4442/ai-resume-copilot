"""Native consent changes followed by a non-authoritative SQL schedule.

No native or provider call runs while the owner/row locks are held. Native ACK
loss is never repaired by accepting a SQL row or by retrying a preference write.
"""
from __future__ import annotations

import hashlib
import time
from datetime import UTC

from sqlalchemy.orm import Session

from ...security import JWT_SECRET
from ..candidate_accounts.contracts import CandidateCredentialRevision
from ..common import utcnow
from ..notifications.native import ConfiguredDigestNative
from ..recovery.gcp_partitioned_contracts import DigestConsentHead
from ..recovery.password_reauth import CandidateWebSession
from .models import NotificationPreference
from .projection import apply_projection, projection_matches, snapshot_owner
from .unsubscribe import UnsubscribeTokens

DIGEST_POLICY_VERSION = "weekly-digest-v1"


def email_digest(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def _view(head: DigestConsentHead | None, projection: NotificationPreference | None) -> dict:
    if head is None:
        return {"weekly_digest": False, "generation": 0, "policy_version": DIGEST_POLICY_VERSION,
                "next_digest_at": None, "projection_current": projection is None}
    current = projection_matches(projection, head)
    due = projection.next_due_at if projection is not None and current and head.enabled else None
    if due is not None and due.tzinfo is None:
        due = due.replace(tzinfo=UTC)
    return {"weekly_digest": head.enabled, "generation": head.generation,
        "policy_version": head.policy_version, "next_digest_at": due,
        "projection_current": current}


def read_preferences(db: Session, user_id: int) -> dict:
    native = ConfiguredDigestNative()  # Default-off refusal before new-schema SQL.
    revision, email = snapshot_owner(db, user_id)
    db.rollback()
    head = native.read_current(revision, email_digest(email))
    # Recheck the current owner after network IO; this still grants no send.
    current, current_email = snapshot_owner(db, user_id)
    if current != revision or current_email != email:
        from ..recovery.store import GuardDenied
        raise GuardDenied("Notification account changed")
    projection = db.get(NotificationPreference, user_id, populate_existing=True)
    result = _view(head, projection)
    db.rollback()
    return result


def change_preferences(db: Session, context: CandidateWebSession, *, expected_generation: int,
                       enabled: bool) -> dict:
    native = ConfiguredDigestNative()
    revision, email = snapshot_owner(db, context.candidate_id)
    db.rollback()
    head = native.change(context, revision, expected_generation=expected_generation,
        enabled=enabled, email_sha256=email_digest(email), policy_version=DIGEST_POLICY_VERSION)
    try:
        projection = apply_projection(db, revision, email, head, now=utcnow())
        result = _view(head, projection)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def unsubscribe(db: Session, token: str) -> dict:
    proof = UnsubscribeTokens(JWT_SECRET.encode("utf-8")).verify(token, now_ms=time.time_ns() // 1_000_000)
    native = ConfiguredDigestNative.for_unsubscribe()
    revision, email = snapshot_owner(db, proof.identity.candidate_id)
    db.rollback()
    if email_digest(email) != proof.email_sha256:
        from ..recovery.store import GuardDenied
        raise GuardDenied("Notification recipient changed")
    head = native.disable_verified_email(proof, revision)
    try:
        projection = apply_projection(db, revision, email, head, now=utcnow())
        result = _view(head, projection)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return result


def unsubscribe_token(revision: CandidateCredentialRevision, head: DigestConsentHead, *, now_ms: int) -> str:
    """Called only for a fresh native head, before its exact message is admitted."""
    from ..candidate_accounts.contracts import CandidateAccountIdentity
    identity = CandidateAccountIdentity.model_validate(revision.model_dump(
        exclude={"auth_generation", "credential_sha256"}))
    return UnsubscribeTokens(JWT_SECRET.encode("utf-8")).issue(identity,
        email_sha256=head.email_sha256, expected_generation=head.generation,
        policy_version=head.policy_version, now_ms=now_ms)
