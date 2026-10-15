"""Actual native original-owned grants plus explicit independent activation ports.

This does not configure clients, project resources, keys or permissions. Normal
V3 denial/credential projection requires its separately reviewed protected port.
The bounded V2 cohort closer is deliberately not a normal lifecycle fallback.
"""
from __future__ import annotations

from typing import Protocol
from uuid import uuid4

from ..recovery.gcp_password_lifetime import (
    GcpPasswordLifetimeCoordinator,
    PasswordLifetimeOwnedAck,
)
from ..recovery.gcp_password_lifetime_contracts import (
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
    def check_account(self, revision: CandidateCredentialRevision) -> bool: ...
    def check_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...
    def revoke_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...
    def advance_credentials(self, context: CandidateWebSession, old: CandidateCredentialRevision,
                            new: CandidateCredentialRevision) -> bool: ...
    def delete_account(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool: ...


class NativeCandidateLifetimeRetention:
    def __init__(self, issuer: GcpPasswordLifetimeCoordinator, protected: ProtectedCandidateLifetimes):
        if type(issuer) is not GcpPasswordLifetimeCoordinator:
            raise GuardUnavailable("Candidate lifetime configuration is unavailable")
        self.issuer, self.protected = issuer, protected

    def enroll(self, revision: CandidateCredentialRevision) -> bool:
        if revision.auth_generation != 1:
            raise GuardUnavailable("Candidate enrollment requires a fresh generation")
        command = EnrollPasswordAccount(**revision.model_dump(exclude={"auth_generation"}),
                                       subject_registration_event_id=uuid4())
        execution = self.issuer.execute(self.issuer.allocate(command))
        return (execution.status.status == "COMMITTED" and execution.owned_ack is not None
                and self.protected.activate(execution.owned_ack, self.issuer) is True)

    def issue(self, revision: CandidateCredentialRevision, *, expires_at_ms: int) -> CandidateWebSession | None:
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
