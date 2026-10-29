"""Genuine credential lifecycle with fail-closed cross-resource state transitions.

All credential IO finishes before native/Storage IO; no SDK runs while SQL locks
are held. A new registration stays PENDING until the original enrollment AND
final protected activation ACK. Absent independent identities are never enrolled
by login. The SQL generation and exact salted-hash digest must both agree with
protected authority, so restoring an old hash cannot create a fresh session.
"""
from __future__ import annotations

import hashlib
import secrets
import time
from collections.abc import Callable
from datetime import UTC
from typing import Protocol
from uuid import UUID, uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ...billing.signup import NEW_ACCOUNT_LEGACY_ANALYSIS_UNITS, NEW_ACCOUNT_LEGACY_SERVICE_CREDITS
from ...models import (
    CandidateGoogleIdentity,
    CandidateLifetimeHistory,
    CandidatePasswordAccount,
    User,
    UserProfile,
)
from ...security import hash_password, verify_password
from ..recovery.password_reauth import CandidateWebSession
from ..recovery.store import GuardDenied, GuardUnavailable
from .contracts import CandidateCredentialRevision, CandidateLoginResult
from .google import GoogleBearerProof, GoogleIdentity
from .retention import CandidateLifetimeRetention

FAILURE = "Candidate password authentication failed"
UNAVAILABLE = "Candidate account lifetime is unavailable"
SESSION_MS = 86_400_000


class CandidateCookieLogout(Protocol):
    def deny_cookie_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision,
                            operation_id: UUID) -> bool: ...
    def confirm_deleted_cookie(self, context: CandidateWebSession, *, auth_generation: int) -> bool: ...
    def confirm_rotated_cookie(self, context: CandidateWebSession, revision: CandidateCredentialRevision,
                               *, auth_generation: int) -> bool: ...


def credential_digest(encoded: str) -> str:
    if type(encoded) is not str or not 0 < len(encoded.encode()) <= 1024:
        raise GuardDenied(FAILURE)
    return hashlib.sha256(encoded.encode()).hexdigest()


def revision_from_row(row: CandidatePasswordAccount, encoded: str) -> CandidateCredentialRevision:
    revision = CandidateCredentialRevision(candidate_id=row.user_id, subject_uuid=row.subject_uuid,
        account_binding_id=row.account_binding_id, principal_sha256=row.principal_sha256,
        auth_generation=row.auth_generation, credential_sha256=row.credential_sha256)
    if row.state != "ACTIVE" or credential_digest(encoded) != revision.credential_sha256:
        raise GuardDenied(FAILURE)
    return revision


def mapped_account(db: Session, candidate_id: int) -> CandidatePasswordAccount | None:
    return db.query(CandidatePasswordAccount).filter_by(user_id=candidate_id).first()


class CandidateAccountService:
    def __init__(self, engine: Engine, retention: CandidateLifetimeRetention, *,
                 now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
                 cookie_logout: CandidateCookieLogout | None = None):
        if not isinstance(engine, Engine) or engine.hide_parameters is not True:
            raise GuardUnavailable(UNAVAILABLE)
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._dummy_hash = hash_password(secrets.token_urlsafe(32))
        self.retention, self.now_ms = retention, now_ms
        self.cookie_logout = cookie_logout

    def google_challenge(self, candidate_id: int, proof: GoogleBearerProof) -> dict:
        from .enrollment import google_challenge
        return google_challenge(self, candidate_id, proof)

    def enroll_google(self, candidate_id: int, proof: GoogleBearerProof, **values) -> int:
        from .enrollment import enroll_google
        return enroll_google(self, candidate_id, proof, **values)

    def google_recovery_challenge(self, candidate_id: int, proof: GoogleBearerProof) -> dict:
        from .legacy_recovery import google_recovery_challenge
        return google_recovery_challenge(self, candidate_id, proof)

    def recover_google(self, candidate_id: int, proof: GoogleBearerProof, **values) -> int:
        from .legacy_recovery import recover_google
        return recover_google(self, candidate_id, proof, **values)

    def enroll_password(self, candidate_id: int, password: str) -> int:
        from .enrollment import enroll_password
        return enroll_password(self, candidate_id, password)

    def login_google(self, identity: GoogleIdentity) -> CandidateLoginResult:
        with self.sessions() as db:
            link = db.query(CandidateGoogleIdentity).filter_by(principal_sha256=identity.principal_sha256).one_or_none()
            if link is None or link.state != "ACTIVE":
                raise GuardDenied("Google account is not explicitly linked")
            user = db.query(User).filter_by(id=link.user_id).one_or_none()
            row = mapped_account(db, link.user_id)
            if user is None or row is None or user.email != identity.email:
                raise GuardDenied(FAILURE)
            revision = revision_from_row(row, user.password_hash)
        email = identity.email
        now = self.now_ms()
        if (not now - 120_000 <= identity.authenticated_at_ms <= now + 5_000
                or not now - 120_000 <= identity.issued_at_ms <= now + 5_000):
            raise GuardDenied(FAILURE)
        context = self.retention.issue_oauth(revision, principal_sha256=identity.principal_sha256,
            verified_proof_sha256=identity.proof_sha256, expires_at_ms=now + SESSION_MS)
        if context is None or self.retention.check_session(context, revision) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        self._bind_login_mailbox(revision, email)
        return CandidateLoginResult(context=context, auth_generation=revision.auth_generation,
                                    expires_at_ms=now + SESSION_MS)

    def registration_status(self, *, email: str, password: str) -> str:
        """Credential-verified status only; never adopt an enrollment ACK."""
        if type(password) is not str or not 0 < len(password.encode()) <= 1024:
            raise GuardDenied(FAILURE)
        with self.sessions() as db:
            user = db.query(User).filter_by(email=email.strip().lower()).one_or_none()
            valid = verify_password(password, user.password_hash if user is not None else self._dummy_hash)
            if user is None or not valid:
                raise GuardDenied(FAILURE)
            row = mapped_account(db, user.id)
            if row is None:
                return "LEGACY_ENROLLMENT_UNAVAILABLE"
            if row.state == "PENDING":
                return "PENDING_REVIEW_REQUIRED"
            revision = revision_from_row(row, user.password_hash)
        if self.retention.check_account(revision) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        return "ENROLLED"

    def register(self, *, email: str, password: str, policy_version: str) -> int:
        if not 10 <= len(password) <= 128:
            raise GuardDenied(FAILURE)
        email = email.strip().lower()  # Capture original verified registration input before native IO.
        encoded = hash_password(password)
        with self.sessions() as db:
            if db.query(User.id).filter_by(email=email.strip().lower()).first() is not None:
                raise GuardDenied("Account already exists; login cannot bootstrap a retained lifetime")
            from datetime import datetime
            accepted = datetime.now(UTC)
            user = User(email=email.strip().lower(), password_hash=encoded,
                ai_credits=NEW_ACCOUNT_LEGACY_ANALYSIS_UNITS,
                job_service_credits=NEW_ACCOUNT_LEGACY_SERVICE_CREDITS,
                terms_accepted_at=accepted, terms_version=policy_version, privacy_version=policy_version,
                age_confirmed_at=accepted)
            db.add(user)
            db.flush()
            account = CandidatePasswordAccount(user_id=user.id, subject_uuid=str(uuid4()),
                account_binding_id=str(uuid4()), principal_sha256=hashlib.sha256(secrets.token_bytes(32)).hexdigest(),
                credential_sha256=credential_digest(encoded), auth_generation=1, state="PENDING")
            db.add(account)
            db.add(CandidateLifetimeHistory(registration_id=str(uuid4())))
            db.add(UserProfile(user_id=user.id))
            db.commit()  # Genuine committed credential row; neither legacy nor candidate access is allowed yet.
            revision = CandidateCredentialRevision(candidate_id=user.id, subject_uuid=account.subject_uuid,
                account_binding_id=account.account_binding_id, principal_sha256=account.principal_sha256,
                auth_generation=1, credential_sha256=account.credential_sha256)
        if (self.retention.enroll(revision) is not True or self.retention.check_account(revision) is not True
                or self.retention.bind_recovery_mailbox(revision, credential_digest(email)) is not True):
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            row = db.query(CandidatePasswordAccount).filter_by(user_id=revision.candidate_id).with_for_update().one_or_none()
            user = db.query(User).filter_by(id=revision.candidate_id).with_for_update().one_or_none()
            if (row is None or user is None or row.state != "PENDING" or user.email != email
                    or row.subject_uuid != str(revision.subject_uuid)
                    or row.account_binding_id != str(revision.account_binding_id)
                    or row.principal_sha256 != revision.principal_sha256
                    or row.auth_generation != revision.auth_generation
                    or row.credential_sha256 != revision.credential_sha256
                    or credential_digest(user.password_hash) != revision.credential_sha256):
                raise GuardUnavailable(UNAVAILABLE)
            row.state = "ACTIVE"
            db.commit()
        return revision.candidate_id

    def login(self, *, email: str, password: str) -> CandidateLoginResult:
        email = email.strip().lower()
        if type(password) is not str or not 0 < len(password.encode()) <= 1024:
            raise GuardDenied(FAILURE)
        with self.sessions() as db:
            user = db.query(User).filter_by(email=email.strip().lower()).one_or_none()
            row = mapped_account(db, user.id) if user is not None else None
            valid_password = verify_password(password, user.password_hash if user is not None else self._dummy_hash)
            if user is None or row is None or not valid_password:
                raise GuardDenied(FAILURE)
            revision = revision_from_row(row, user.password_hash)
        now = self.now_ms()
        if type(now) is not int or not 0 < now < 2**53 - SESSION_MS:
            raise GuardUnavailable(UNAVAILABLE)
        context = self.retention.issue(revision, expires_at_ms=now + SESSION_MS)
        if context is None or context.candidate_id != revision.candidate_id or context.account_binding_id != revision.account_binding_id:
            raise GuardUnavailable(UNAVAILABLE)
        # A concurrent password reset/deletion must be independently denied. No SQL-held external IO.
        if self.retention.check_session(context, revision) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        self._bind_login_mailbox(revision, email)
        return CandidateLoginResult(context=context, auth_generation=revision.auth_generation,
                                    expires_at_ms=now + SESSION_MS)

    def _bind_login_mailbox(self, revision: CandidateCredentialRevision, email: str) -> None:
        if self.retention.bind_recovery_mailbox(revision, credential_digest(email)) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            user, row = db.get(User, revision.candidate_id), mapped_account(db, revision.candidate_id)
            if (user is None or row is None or user.email != email
                    or revision_from_row(row, user.password_hash) != revision):
                raise GuardUnavailable(UNAVAILABLE)

    def _current(self, context: CandidateWebSession, *, auth_generation: int | None = None) -> CandidateCredentialRevision:
        with self.sessions() as db:
            row = mapped_account(db, context.candidate_id)
            user = db.query(User).filter_by(id=context.candidate_id).one_or_none()
            if row is None or user is None:
                raise GuardDenied(FAILURE)
            revision = revision_from_row(row, user.password_hash)
        if (context.account_binding_id != revision.account_binding_id
                or (auth_generation is not None and revision.auth_generation != auth_generation)
                or self.retention.check_session(context, revision) is not True):
            raise GuardDenied(FAILURE)
        return revision

    def validate(self, context: CandidateWebSession, *, auth_generation: int) -> None:
        self._current(context, auth_generation=auth_generation)

    def logout(self, context: CandidateWebSession) -> None:
        revision = self._current(context)
        if self.retention.revoke_session(context, revision) is not True:
            raise GuardUnavailable(UNAVAILABLE)

    def logout_cookie(self, context: CandidateWebSession, *, auth_generation: int, operation_id: UUID) -> None:
        """Only clears a cookie after an exact retained denial; never grants."""
        if self.cookie_logout is None:
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            row = mapped_account(db, context.candidate_id)
            user = db.query(User).filter_by(id=context.candidate_id).one_or_none()
            if row is None or user is None or row.state == "DELETE_DENIED":
                revision = None
            else:
                revision = revision_from_row(row, user.password_hash)
        if revision is None:
            if self.cookie_logout.confirm_deleted_cookie(context, auth_generation=auth_generation) is not True:
                raise GuardUnavailable(UNAVAILABLE)
            return
        if type(auth_generation) is int and 0 < auth_generation < revision.auth_generation:
            if (context.candidate_id != revision.candidate_id or context.account_binding_id != revision.account_binding_id
                    or self.retention.check_account(revision) is not True
                    or self.cookie_logout.confirm_rotated_cookie(context, revision, auth_generation=auth_generation) is not True):
                raise GuardUnavailable(UNAVAILABLE)
            return  # Denial only: no old session is authenticated, issued or activated.
        if (context.account_binding_id != revision.account_binding_id
                or auth_generation != revision.auth_generation
                or self.cookie_logout.deny_cookie_session(context, revision, operation_id) is not True):
            raise GuardUnavailable(UNAVAILABLE)

    def change_password(self, context: CandidateWebSession, *, current_password: str, new_password: str) -> None:
        old = self._current(context)
        if not 0 < len(current_password) <= 128 or not 10 <= len(new_password) <= 128 or old.auth_generation >= 2**53 - 1:
            raise GuardDenied(FAILURE)
        with self.sessions() as db:
            user = db.query(User).filter_by(id=context.candidate_id).one_or_none()
            if user is None or not verify_password(current_password, user.password_hash):
                raise GuardDenied(FAILURE)
            if credential_digest(user.password_hash) != old.credential_sha256:
                raise GuardDenied(FAILURE)
        encoded = hash_password(new_password)
        new = old.model_copy(update={"auth_generation": old.auth_generation + 1,
                                     "credential_sha256": credential_digest(encoded)})
        if self.retention.advance_credentials(context, old, new) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            row = db.query(CandidatePasswordAccount).filter_by(user_id=context.candidate_id).with_for_update().one_or_none()
            user = db.query(User).filter_by(id=context.candidate_id).with_for_update().one_or_none()
            if row is None or user is None or revision_from_row(row, user.password_hash) != old:
                raise GuardUnavailable(UNAVAILABLE)
            row.auth_generation, row.credential_sha256 = new.auth_generation, new.credential_sha256
            user.password_hash = encoded
            db.commit()

    def reset_password_from_email(self, reset_id: str, encoded: str, token: str) -> None:
        """Consume an already claimed mailbox proof through existing native ports."""
        from ...models import PasswordResetToken
        from .email_reset import _valid_token, digest, revision_digest
        from .enrollment import _revision

        def binding(proof):
            return (proof.id, proof.user_id, proof.token_sha256, proof.email_sha256,
                proof.credential_sha256, proof.native_revision_sha256, proof.legacy_epoch,
                proof.expires_at, proof.consumed_at)

        with self.sessions() as db:
            proof = db.query(PasswordResetToken).filter_by(id=reset_id).with_for_update().one_or_none()
            # The original mailbox token is reauthenticated after the SQL claim
            # boundary, before deriving any owner or issuing native authority.
            if (proof is None or proof.status != "PROCESSING" or proof.consumed_at is None
                    or proof.token_sha256 != digest(token) or not _valid_token(proof, token)):
                raise GuardDenied(FAILURE)
            verified = binding(proof)
            user = db.get(User, proof.user_id)
            row = mapped_account(db, proof.user_id)
            if user is None or row is None:
                raise GuardDenied(FAILURE)
            old = revision_from_row(row, user.password_hash)
            if (revision_digest(old) != proof.native_revision_sha256
                    or credential_digest(user.email) != proof.email_sha256
                    or int(user.credential_epoch or 0) != proof.legacy_epoch
                    or credential_digest(user.password_hash) != proof.credential_sha256
                    or old.auth_generation >= 2**53 - 1):
                raise GuardDenied(FAILURE)
        if self.retention.check_recovery_mailbox(old, proof.email_sha256) is not True:
            raise GuardDenied("Recovery mailbox is not independently bound; verified sign-in or support required")
        context = self.retention.issue(old, expires_at_ms=self.now_ms() + 60_000)
        if context is None or self.retention.check_session(context, old) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        new = old.model_copy(update={"auth_generation": old.auth_generation + 1,
                                     "credential_sha256": credential_digest(encoded)})
        with self.sessions() as db:
            user = db.query(User).filter_by(id=old.candidate_id).with_for_update().one()
            proof = db.query(PasswordResetToken).filter_by(id=reset_id).with_for_update().one()
            row = db.query(CandidatePasswordAccount).filter_by(user_id=old.candidate_id).with_for_update().one()
            if (proof.status != "PROCESSING" or proof.user_id != old.candidate_id
                    or binding(proof) != verified or not _valid_token(proof, token)
                    or revision_from_row(row, user.password_hash) != old
                    or credential_digest(user.email) != proof.email_sha256
                    or int(user.credential_epoch or 0) != proof.legacy_epoch):
                raise GuardDenied(FAILURE)
            user.password_hash, row.state = encoded, "RESET_PENDING"
            db.commit()  # Deny all sign-in/session use before the native credential cut.
        if self.retention.advance_credentials(context, old, new) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            user = db.query(User).filter_by(id=old.candidate_id).with_for_update().one()
            proof = db.query(PasswordResetToken).filter_by(id=reset_id).with_for_update().one()
            row = db.query(CandidatePasswordAccount).filter_by(user_id=old.candidate_id).with_for_update().one()
            if (proof.status != "PROCESSING" or proof.user_id != old.candidate_id
                    or binding(proof) != verified or not _valid_token(proof, token)
                    or row.state != "RESET_PENDING" or _revision(row) != old
                    or credential_digest(user.email) != proof.email_sha256
                    or int(user.credential_epoch or 0) != proof.legacy_epoch or user.password_hash != encoded):
                raise GuardUnavailable(UNAVAILABLE)
            row.auth_generation, row.credential_sha256, row.state = new.auth_generation, new.credential_sha256, "ACTIVE"
            proof.status = "COMPLETED"
            from ..notifications.password_reset import enqueue_changed
            enqueue_changed(db, user_id=user.id, recipient=user.email, reset_id=reset_id)
            db.commit()

    def prepare_delete(self, context: CandidateWebSession) -> None:
        revision = self._current(context)
        if self.retention.delete_account(context, revision) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            row = db.query(CandidatePasswordAccount).filter_by(user_id=context.candidate_id).with_for_update().one_or_none()
            if row is None:
                raise GuardUnavailable(UNAVAILABLE)
            row.state = "DELETE_DENIED"
            db.commit()  # Existing account-data deletion must run only after this independent denial.


def production_candidate_accounts():
    from .production import production_candidate_accounts as configured
    return configured()
