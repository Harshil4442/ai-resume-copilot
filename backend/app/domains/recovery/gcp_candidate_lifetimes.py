"""Disabled exact lifecycle/V3 composition; normal credential projection absent."""

from __future__ import annotations

from ..candidate_accounts.contracts import CandidateCredentialRevision
from .gcp_partitioned_contracts import ScopedCredentialRevision
from .gcp_password_lifetime import GcpPasswordLifetimeCoordinator, PasswordLifetimeOwnedAck
from .gcp_scoped_authority import ScopedCandidateAuthority
from .password_reauth import CandidateWebSession
from .store import GuardDenied, GuardUnavailable


def _revision(value: CandidateCredentialRevision) -> ScopedCredentialRevision:
    if type(value) is not CandidateCredentialRevision:
        raise GuardDenied("Exact trusted candidate credential revision required")
    return ScopedCredentialRevision.model_validate_json(value.model_dump_json())


class GcpProtectedCandidateLifetimes:
    def __init__(self, scoped: ScopedCandidateAuthority):
        if type(scoped) is not ScopedCandidateAuthority:
            raise GuardUnavailable("Exact disabled scoped authority required")
        self.scoped = scoped

    def activate(self, ack: PasswordLifetimeOwnedAck, issuer: GcpPasswordLifetimeCoordinator) -> bool:
        self.scoped.activate(issuer, ack)
        return True

    def check_account(self, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.check_account(_revision(revision))

    def check_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.check_session(context, _revision(revision))

    def revoke_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.revoke_session(context, _revision(revision))

    def advance_credentials(self, context: CandidateWebSession, old: CandidateCredentialRevision,
                            new: CandidateCredentialRevision) -> bool:
        # Never falsely return a protected-only fence as complete native/SQL
        # credential advancement. No V2 cohort-close or arbitrary callback bridge.
        raise GuardUnavailable("Normal command-bound native credential projection is not implemented")

    def delete_account(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.delete_account(context, _revision(revision))
