"""Explicit upgrade of an existing account after fresh independent owner proof.

SQL records a one-shot PENDING enrollment before native IO. A lost native ACK
cannot be adopted or replayed by another request, including a restored bearer.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy.exc import IntegrityError

from ...models import (
    CandidateGoogleEnrollmentChallenge,
    CandidateGoogleIdentity,
    CandidateIdentityHistory,
    CandidateLifetimeHistory,
    CandidatePasswordAccount,
    User,
)
from ...security import hash_password, verify_password
from ..recovery.store import GuardDenied, GuardUnavailable
from .contracts import CandidateCredentialRevision
from .google import GoogleBearerProof, GoogleIdentity

if TYPE_CHECKING:
    from .service import CandidateAccountService

FAILURE = "Candidate account enrollment failed"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def google_challenge(service: CandidateAccountService, candidate_id: int, proof: GoogleBearerProof) -> dict:
    now = service.now_ms()
    if not now - 900_000 <= proof.authenticated_at_ms <= now + 5_000:
        raise GuardDenied("Sign in with Google again before enrolling")
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    if not client_id:
        raise GuardUnavailable(FAILURE)
    nonce, challenge_id = secrets.token_urlsafe(32), str(uuid4())
    with service.sessions() as db:
        user = db.query(User).filter_by(id=candidate_id).with_for_update().one_or_none()
        if user is None or db.get(CandidatePasswordAccount, candidate_id) is not None:
            raise GuardDenied(FAILURE)
        # Superseding a challenge invalidates the old nonce before returning.
        for old in db.query(CandidateGoogleEnrollmentChallenge).filter_by(user_id=candidate_id, used=False):
            old.used = True
        db.add(CandidateGoogleEnrollmentChallenge(id=challenge_id, user_id=candidate_id,
            nonce_sha256=digest(nonce), principal_sha256=proof.principal_sha256,
            email_sha256=digest(user.email), credential_sha256=digest(user.password_hash),
            expires_at_ms=now + 300_000, used=False))
        db.commit()
    return {"challenge_id": challenge_id, "nonce": nonce, "client_id": client_id,
            "expires_at_ms": now + 300_000}


def enroll_google(service: CandidateAccountService, candidate_id: int, proof: GoogleBearerProof, *,
                  challenge_id: str, nonce: str, identity: GoogleIdentity, password: str,
                  current_password: str | None) -> int:
    if not 10 <= len(password) <= 128 or identity.principal_sha256 != proof.principal_sha256:
        raise GuardDenied(FAILURE)
    encoded = hash_password(password)
    with service.sessions() as db:
        user = db.query(User).filter_by(id=candidate_id).with_for_update().one_or_none()
        challenge = db.query(CandidateGoogleEnrollmentChallenge).filter_by(id=challenge_id).with_for_update().one_or_none()
        now = service.now_ms()
        if (user is None or challenge is None or challenge.user_id != candidate_id or challenge.used
                or not now < challenge.expires_at_ms or challenge.nonce_sha256 != digest(nonce)
                or challenge.principal_sha256 != identity.principal_sha256
                or challenge.email_sha256 != digest(user.email) or identity.email != user.email
                or challenge.credential_sha256 != digest(user.password_hash)
                or not now - 120_000 <= identity.authenticated_at_ms <= now + 5_000
                or db.get(CandidatePasswordAccount, candidate_id) is not None
                or (user.password_hash and (not current_password or not verify_password(current_password, user.password_hash)))):
            raise GuardDenied(FAILURE)
        challenge.used = True
        user.password_hash = encoded
        row = _pending(db, user, encoded, identity.principal_sha256)
        db.add(CandidateGoogleIdentity(user_id=candidate_id, principal_sha256=identity.principal_sha256,
                                      enrollment_proof_sha256=identity.proof_sha256, state="PENDING"))
        db.add(CandidateIdentityHistory(registration_id=str(uuid4())))
        try:
            db.commit()  # Unique principal + one original native attempt; no locks cross resource IO.
        except IntegrityError:
            raise GuardDenied(FAILURE) from None
        revision = _revision(row)
    _activate(service, revision, google=True, email=identity.email)
    return candidate_id


def enroll_password(service: CandidateAccountService, candidate_id: int, password: str) -> int:
    with service.sessions() as db:
        user = db.query(User).filter_by(id=candidate_id).with_for_update().one_or_none()
        if (user is None or db.get(CandidatePasswordAccount, candidate_id) is not None
                or not user.password_hash or not verify_password(password, user.password_hash)):
            raise GuardDenied(FAILURE)
        email = user.email  # Bound to the current-password-verified enrollment before native IO.
        revision = _revision(_pending(db, user, user.password_hash, digest(secrets.token_urlsafe(32))))
        db.commit()
    _activate(service, revision, google=False, email=email)
    return candidate_id


def _pending(db, user: User, encoded: str, principal: str) -> CandidatePasswordAccount:
    row = CandidatePasswordAccount(user_id=user.id, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
        principal_sha256=principal, credential_sha256=digest(encoded), auth_generation=1, state="PENDING")
    db.add(row)
    db.add(CandidateLifetimeHistory(registration_id=str(uuid4())))
    return row


def _revision(row: CandidatePasswordAccount) -> CandidateCredentialRevision:
    return CandidateCredentialRevision(candidate_id=row.user_id, subject_uuid=row.subject_uuid,
        account_binding_id=row.account_binding_id, principal_sha256=row.principal_sha256,
        auth_generation=row.auth_generation, credential_sha256=row.credential_sha256)


def _activate(service: CandidateAccountService, revision: CandidateCredentialRevision, *, google: bool, email: str) -> None:
    if (service.retention.enroll(revision) is not True or service.retention.check_account(revision) is not True
            or service.retention.bind_recovery_mailbox(revision, digest(email)) is not True):
        raise GuardUnavailable(FAILURE)
    with service.sessions() as db:
        user = db.query(User).filter_by(id=revision.candidate_id).with_for_update().one_or_none()
        row = db.query(CandidatePasswordAccount).filter_by(user_id=revision.candidate_id).with_for_update().one_or_none()
        identity = db.query(CandidateGoogleIdentity).filter_by(user_id=revision.candidate_id).with_for_update().one_or_none() if google else None
        if (user is None or user.email != email or row is None or row.state != "PENDING" or _revision(row) != revision
                or digest(user.password_hash) != revision.credential_sha256
                or (google and (identity is None or identity.state != "PENDING" or identity.principal_sha256 != revision.principal_sha256))):
            raise GuardUnavailable(FAILURE)
        row.state = "ACTIVE"
        if identity is not None:
            identity.state = "ACTIVE"
        db.commit()
