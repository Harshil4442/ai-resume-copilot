"""Actual native original-owned grants plus explicit independent activation ports.

This does not configure clients, project resources, keys or permissions. Normal
V3 denial/credential projection requires its separately reviewed protected port.
The bounded V2 cohort closer is deliberately not a normal lifecycle fallback.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from threading import Lock
from typing import Protocol
from uuid import uuid4
from weakref import WeakKeyDictionary

from ..recovery.contracts import canonical
from ..recovery.gcp_password_lifetime import (
    GcpPasswordLifetimeCoordinator,
    PasswordLifetimeOwnedAck,
)
from ..recovery.gcp_password_lifetime_contracts import (
    CreateVerifiedOAuthWebSession,
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
)
from ..recovery.password_reauth import CandidateWebSession
from ..recovery.store import GuardUnavailable
from .contracts import CandidateCredentialRevision


class ProtectedCandidateLifetimes(Protocol):
    def activate(self, ack: PasswordLifetimeOwnedAck, issuer: GcpPasswordLifetimeCoordinator) -> bool:
        """Consume the original ACK, commit exact protected activation, require own ACK."""
        ...

    def bind_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool: ...
    def check_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool: ...
    def check_account(self, revision: CandidateCredentialRevision) -> bool: ...
    def check_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...
    def revoke_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...
    def advance_credentials(self, context: CandidateWebSession, old: CandidateCredentialRevision,
                            new: CandidateCredentialRevision) -> bool:
        """Retain V3 denial/high-water first, then exact native projection; require both ACKs."""
        ...
    def delete_account(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...


class CandidateLifetimeRetention(Protocol):
    def enroll(self, revision: CandidateCredentialRevision) -> bool: ...
    def issue(self, revision: CandidateCredentialRevision, *, expires_at_ms: int) -> CandidateWebSession | None: ...
    def issue_oauth(self, revision: CandidateCredentialRevision, *, principal_sha256: str, verified_proof_sha256: str, expires_at_ms: int) -> CandidateWebSession | None: ...
    def bind_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool: ...
    def check_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool: ...
    def check_account(self, revision: CandidateCredentialRevision) -> bool: ...
    def check_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...
    def revoke_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...
    def advance_credentials(self, context: CandidateWebSession, old: CandidateCredentialRevision,
                            new: CandidateCredentialRevision) -> bool: ...
    def delete_account(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...


@dataclass(eq=False, frozen=True, repr=False)
class PendingPasswordLogin:
    """Opaque original pending result; not a session or authorization grant."""

    _raw: bytes
    _issuer: object


class ProtectedPasswordLoginFinalizer(Protocol):
    def finalize_password_login(self, context: CandidateWebSession,
                                revision: CandidateCredentialRevision, email_sha256: str) -> bool: ...


class PasswordLoginFinalizer(Protocol):
    def begin_password_login(self, revision: CandidateCredentialRevision, *, email_sha256: str,
                             expires_at_ms: int) -> PendingPasswordLogin | None: ...
    def finalize_password_login(self, pending: PendingPasswordLogin) -> CandidateWebSession: ...


class NativeCandidateLifetimeRetention:
    def __init__(self, issuer: GcpPasswordLifetimeCoordinator, protected: ProtectedCandidateLifetimes, *,
                 login_finalizer: ProtectedPasswordLoginFinalizer | None = None):
        if type(issuer) is not GcpPasswordLifetimeCoordinator:
            raise GuardUnavailable("Candidate lifetime configuration is unavailable")
        self.issuer, self.protected = issuer, protected
        self._login_finalizer = login_finalizer
        self._login_issuer, self._login_lock = object(), Lock()
        # Weak ownership retains no abandoned login after its caller releases it.
        # This stores pending canonical bytes, never a successful UID observation.
        self._pending_logins: WeakKeyDictionary[
            PendingPasswordLogin, tuple[bytes, ProtectedPasswordLoginFinalizer]
        ] = WeakKeyDictionary()

    def enroll(self, revision: CandidateCredentialRevision) -> bool:
        if revision.auth_generation != 1:
            raise GuardUnavailable("Candidate enrollment requires a fresh generation")
        command = EnrollPasswordAccount(**revision.model_dump(exclude={"auth_generation"}),
                                       subject_registration_event_id=uuid4())
        execution = self.issuer.execute(self.issuer.allocate(command))
        return (execution.status.status == "COMMITTED" and execution.owned_ack is not None
                and self.protected.activate(execution.owned_ack, self.issuer) is True)

    def bind_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool:
        return self.protected.bind_recovery_mailbox(revision, email_sha256)

    def check_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool:
        return self.protected.check_recovery_mailbox(revision, email_sha256)

    def issue(self, revision: CandidateCredentialRevision, *, expires_at_ms: int) -> CandidateWebSession | None:
        context = self._issue_password(revision, expires_at_ms=expires_at_ms)
        if context is None or self.protected.check_session(context, revision) is not True:
            return None
        return context

    def _issue_password(self, revision: CandidateCredentialRevision, *, expires_at_ms: int) -> CandidateWebSession | None:
        if self.protected.check_account(revision) is not True:
            raise GuardUnavailable("Candidate account lifetime is unavailable")
        execution = self.issuer.execute(self.issuer.allocate(CreateVerifiedPasswordWebSession(
            account_binding_id=revision.account_binding_id, session_id=uuid4(),
            expected_auth_generation=revision.auth_generation, credential_sha256=revision.credential_sha256,
            expires_at_ms=expires_at_ms)))
        if (execution.status.status != "COMMITTED" or execution.owned_ack is None
                or execution.context is None
                or self.protected.activate(execution.owned_ack, self.issuer) is not True):
            return None
        return execution.context

    def begin_password_login(self, revision: CandidateCredentialRevision, *, email_sha256: str,
                             expires_at_ms: int) -> PendingPasswordLogin | None:
        """Keep an activated original private until one fresh final transaction."""
        finalizer = self._login_finalizer
        if (finalizer is None or type(revision) is not CandidateCredentialRevision
                or type(email_sha256) is not str or re.fullmatch(r"[a-f0-9]{64}", email_sha256) is None):
            raise GuardUnavailable("Exact protected login finalization is unavailable")
        context = self._issue_password(revision, expires_at_ms=expires_at_ms)
        if context is None:
            return None
        if self._login_finalizer is not finalizer:
            raise GuardUnavailable("Protected login finalizer changed during issuance")
        raw = canonical({"revision": revision.model_dump(mode="json"),
                         "context": context.model_dump(mode="json"),
                         "email_sha256": email_sha256, "expires_at_ms": expires_at_ms}).encode()
        pending = PendingPasswordLogin(raw, self._login_issuer)
        with self._login_lock:
            self._pending_logins[pending] = raw, finalizer
        return pending

    def finalize_password_login(self, pending: PendingPasswordLogin) -> CandidateWebSession:
        """Consume before IO; an unknown Commit or failed proof cannot be retried."""
        with self._login_lock:
            owned = self._pending_logins.pop(pending, None) if type(pending) is PendingPasswordLogin else None
            if owned is None:
                raise GuardUnavailable("Original pending login is absent or consumed")
            raw, finalizer = owned
            if (pending._issuer is not self._login_issuer or pending._raw != raw
                    or self._login_finalizer is not finalizer):
                raise GuardUnavailable("Original pending login is absent or consumed")
        value = json.loads(raw)
        context = CandidateWebSession.model_validate(value["context"])
        revision = CandidateCredentialRevision.model_validate(value["revision"])
        if finalizer.finalize_password_login(context, revision, value["email_sha256"]) is not True:
            raise GuardUnavailable("Protected login finalization is unavailable")
        # The trusted IO port never owns the context exported to the caller.
        return CandidateWebSession.model_validate(json.loads(raw)["context"])

    def issue_oauth(self, revision: CandidateCredentialRevision, *, principal_sha256: str, verified_proof_sha256: str, expires_at_ms: int) -> CandidateWebSession | None:
        if principal_sha256 != revision.principal_sha256 or self.protected.check_account(revision) is not True:
            raise GuardUnavailable("Verified OAuth proof does not own the enrolled principal")
        execution = self.issuer.execute(self.issuer.allocate(CreateVerifiedOAuthWebSession(
            account_binding_id=revision.account_binding_id, session_id=uuid4(), expected_auth_generation=revision.auth_generation,
            credential_sha256=revision.credential_sha256, principal_sha256=principal_sha256,
            verified_proof_sha256=verified_proof_sha256, expires_at_ms=expires_at_ms)))
        if (execution.status.status != "COMMITTED" or execution.owned_ack is None or execution.context is None
                or execution.context.authentication_method != "google"
                or self.protected.activate(execution.owned_ack, self.issuer) is not True):
            return None
        if self.protected.check_session(execution.context, revision) is not True:
            return None
        return execution.context

    def check_account(self, revision: CandidateCredentialRevision) -> bool:
        return self.protected.check_account(revision)

    def check_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.protected.check_session(context, revision)

    def revoke_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.protected.revoke_session(context, revision)

    def advance_credentials(self, context: CandidateWebSession, old: CandidateCredentialRevision,
                            new: CandidateCredentialRevision) -> bool:
        return self.protected.advance_credentials(context, old, new)

    def delete_account(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.protected.delete_account(context, revision)
