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
from uuid import uuid4

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from ...models import CandidateLifetimeHistory, CandidatePasswordAccount, User, UserProfile
from ...security import hash_password, verify_password
from ..recovery.password_reauth import CandidateWebSession
from ..recovery.store import GuardDenied, GuardUnavailable
from .contracts import CandidateCredentialRevision, CandidateLoginResult
from .retention import CandidateLifetimeRetention

FAILURE = "Candidate password authentication failed"
UNAVAILABLE = "Candidate account lifetime is unavailable"
SESSION_MS = 86_400_000


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
                 now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000):
        if not isinstance(engine, Engine) or engine.hide_parameters is not True:
            raise GuardUnavailable(UNAVAILABLE)
        self.sessions = sessionmaker(bind=engine, expire_on_commit=False)
        self._dummy_hash = hash_password(secrets.token_urlsafe(32))
        self.retention, self.now_ms = retention, now_ms

    def register(self, *, email: str, password: str, policy_version: str) -> int:
        if not 10 <= len(password) <= 128:
            raise GuardDenied(FAILURE)
        encoded = hash_password(password)
        with self.sessions() as db:
            if db.query(User.id).filter_by(email=email.strip().lower()).first() is not None:
                raise GuardDenied("Account already exists; login cannot bootstrap a retained lifetime")
            from datetime import datetime
            accepted = datetime.now(UTC)
            user = User(email=email.strip().lower(), password_hash=encoded,
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
        if self.retention.enroll(revision) is not True or self.retention.check_account(revision) is not True:
            raise GuardUnavailable(UNAVAILABLE)
        with self.sessions() as db:
            row = db.query(CandidatePasswordAccount).filter_by(user_id=revision.candidate_id).with_for_update().one_or_none()
            user = db.query(User).filter_by(id=revision.candidate_id).with_for_update().one_or_none()
            if (row is None or user is None or row.state != "PENDING"
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
        return CandidateLoginResult(context=context, auth_generation=revision.auth_generation,
                                    expires_at_ms=now + SESSION_MS)

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


def production_candidate_accounts() -> CandidateAccountService:
    raise GuardUnavailable(UNAVAILABLE)
