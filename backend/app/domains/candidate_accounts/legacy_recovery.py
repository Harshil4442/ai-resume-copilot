"""Explicit authoritative-Google recovery and first enrollment of a legacy account.

The ordinary enrollment password guard is unchanged. A distinct challenge purpose
authorizes this separate recovery operation; existing native accounts never enter it.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ...models import (
    CandidateGoogleEnrollmentChallenge,
    CandidateGoogleIdentity,
    CandidateIdentityHistory,
    CandidatePasswordAccount,
    User,
)
from ...security import hash_password
from ..notifications.service import enqueue_notification
from ..recovery.store import GuardDenied, GuardUnavailable
from .enrollment import _activate, _pending, _revision, digest
from .google import GoogleBearerProof, GoogleIdentity

if TYPE_CHECKING:
    from .service import CandidateAccountService

FAILURE = "Legacy account recovery could not be confirmed"
# 0xFF cannot begin a UTF-8 encoding. The ordinary enrollment digest hashes a
# UTF-8 string, so no caller-chosen ordinary nonce can alias this purpose tag.
PURPOSE = b"\xffhirewiz:legacy-google-recovery:v1\0"


class RecoveryAuthenticationFailed(GuardDenied):
    """A definite pre-commit ownership/challenge rejection."""


class RecoveryUnavailable(GuardDenied):
    """The exact legacy-only preimage is absent; never reset a native account."""


class RecoveryEmailProofRequired(GuardDenied):
    """Google does not authoritatively verify this mailbox for recovery."""


def recovery_digest(nonce: str) -> str:
    return hashlib.sha256(PURPOSE + nonce.encode()).hexdigest()


def recent(proof: GoogleBearerProof, now: int) -> None:
    if not now - 900_000 <= proof.authenticated_at_ms <= now + 5_000:
        raise RecoveryAuthenticationFailed(FAILURE)


def legacy_only(db: Session, candidate_id: int, user: User | None) -> None:
    if (user is None or not user.password_hash
            or db.get(CandidatePasswordAccount, candidate_id) is not None
            or db.get(CandidateGoogleIdentity, candidate_id) is not None):
        raise RecoveryUnavailable(FAILURE)


def google_recovery_challenge(service: CandidateAccountService, candidate_id: int,
                              proof: GoogleBearerProof) -> dict:
    now = service.now_ms()
    recent(proof, now)
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    if not client_id:
        raise GuardUnavailable(FAILURE)
    nonce, challenge_id = secrets.token_urlsafe(32), str(uuid4())
    with service.sessions() as db:
        user = db.query(User).filter_by(id=candidate_id).with_for_update().one_or_none()
        legacy_only(db, candidate_id, user)
        assert user is not None
        now = service.now_ms()
        recent(proof, now)
        for old in db.query(CandidateGoogleEnrollmentChallenge).filter_by(user_id=candidate_id, used=False):
            old.used = True
        db.add(CandidateGoogleEnrollmentChallenge(id=challenge_id, user_id=candidate_id,
            nonce_sha256=recovery_digest(nonce), principal_sha256=proof.principal_sha256,
            email_sha256=digest(user.email), credential_sha256=digest(user.password_hash),
            expires_at_ms=now + 300_000, used=False))
        db.commit()
    return {"challenge_id": challenge_id, "nonce": nonce, "client_id": client_id,
            "expires_at_ms": now + 300_000}


def recover_google(service: CandidateAccountService, candidate_id: int, proof: GoogleBearerProof, *,
                   challenge_id: str, nonce: str, identity: GoogleIdentity, password: str) -> int:
    now = service.now_ms()
    recent(proof, now)
    if (not 10 <= len(password) <= 128 or identity.principal_sha256 != proof.principal_sha256
            or not now - 120_000 <= identity.authenticated_at_ms <= now + 5_000
            or not now - 120_000 <= identity.issued_at_ms <= now + 5_000):
        raise RecoveryAuthenticationFailed(FAILURE)
    if not identity.verified_recovery_nonce(nonce):
        raise RecoveryEmailProofRequired(FAILURE)
    encoded = hash_password(password)
    with service.sessions() as db:
        user = db.query(User).filter_by(id=candidate_id).with_for_update().one_or_none()
        legacy_only(db, candidate_id, user)
        assert user is not None
        challenge = db.query(CandidateGoogleEnrollmentChallenge).filter_by(id=challenge_id).with_for_update().one_or_none()
        now = service.now_ms()
        recent(proof, now)
        if (challenge is None or challenge.user_id != candidate_id or challenge.used
                or not now < challenge.expires_at_ms or challenge.nonce_sha256 != recovery_digest(nonce)
                or not now - 120_000 <= identity.authenticated_at_ms <= now + 5_000
                or not now - 120_000 <= identity.issued_at_ms <= now + 5_000
                or challenge.principal_sha256 != identity.principal_sha256
                or challenge.email_sha256 != digest(user.email) or identity.email != user.email
                or user.email != user.email.strip().lower()
                or challenge.credential_sha256 != digest(user.password_hash)):
            raise RecoveryAuthenticationFailed(FAILURE)
        challenge.used = True
        user.password_hash = encoded
        row = _pending(db, user, encoded, identity.principal_sha256)
        db.add(CandidateGoogleIdentity(user_id=candidate_id, principal_sha256=identity.principal_sha256,
            enrollment_proof_sha256=identity.proof_sha256, state="PENDING"))
        db.add(CandidateIdentityHistory(registration_id=str(uuid4())))
        # Existing optional delivery only. The outbox receives no secret proof,
        # password, hash, credential revision or recovery URL.
        with db.no_autoflush:
            enqueue_notification(db, user_id=candidate_id, notification_type="account_recovery_requested",
                recipient=user.email, payload={}, idempotency_key=f"legacy-recovery:{challenge_id}")
        try:
            db.commit()  # The native PENDING row denies all old legacy JWTs.
        except IntegrityError:
            raise RecoveryUnavailable(FAILURE) from None
        revision = _revision(row)
    try:
        _activate(service, revision, google=True)
    except Exception:
        # Once SQL committed, even a later guard denial is an uncertain outcome.
        # Keep PENDING/original ACK custody; never report a retryable rejection.
        raise GuardUnavailable(FAILURE) from None
    return candidate_id
