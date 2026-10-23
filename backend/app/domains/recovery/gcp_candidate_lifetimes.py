"""Disabled exact lifecycle/V3 composition; normal credential projection absent."""

from __future__ import annotations

import re

from ..candidate_accounts.contracts import CandidateCredentialRevision
from .gcp_partitioned_contracts import ScopedCredentialRevision
from .gcp_password_lifetime import GcpPasswordLifetimeCoordinator, PasswordLifetimeOwnedAck
from .gcp_password_reset import GcpPasswordCredentialProjection
from .gcp_scoped_authority import ScopedCandidateAuthority, current_subject
from .password_reauth import CandidateWebSession
from .store import GuardDenied, GuardUnavailable


def _revision(value: CandidateCredentialRevision) -> ScopedCredentialRevision:
    if type(value) is not CandidateCredentialRevision:
        raise GuardDenied("Exact trusted candidate credential revision required")
    return ScopedCredentialRevision.model_validate_json(value.model_dump_json())


class GcpProtectedCandidateLifetimes:
    def __init__(self, scoped: ScopedCandidateAuthority, *, reset: GcpPasswordCredentialProjection | None = None):
        if type(scoped) is not ScopedCandidateAuthority:
            raise GuardUnavailable("Exact disabled scoped authority required")
        if reset is not None and (type(reset) is not GcpPasswordCredentialProjection or reset.scoped is not scoped):
            raise GuardUnavailable("Exact original command-bound reset projector required")
        self.scoped, self.reset = scoped, reset

    def activate(self, ack: PasswordLifetimeOwnedAck, issuer: GcpPasswordLifetimeCoordinator) -> bool:
        self.scoped.activate(issuer, ack)
        return True

    def _mailbox(self, revision: CandidateCredentialRevision, email_sha256: str):
        expected = _revision(revision)
        if type(email_sha256) is not str or re.fullmatch(r"[a-f0-9]{64}", email_sha256) is None:
            raise GuardDenied("Exact recovery mailbox digest required")
        record = {"version": 1, "subject_uuid": str(expected.subject_uuid), "candidate_id": expected.candidate_id,
            "account_binding_id": str(expected.account_binding_id), "principal_sha256": expected.principal_sha256,
            "email_sha256": email_sha256}
        return expected, record

    def bind_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool:
        """Trusted post-credential-proof port; immutable within the original subject."""
        expected, record = self._mailbox(revision, email_sha256)
        def bind(tx):
            self.scoped.publication._root(tx, opened=True)
            subject = current_subject(tx, expected.subject_uuid)
            if subject.model_dump(mode="json", exclude={"enrollment_operation"}) != expected.model_dump(mode="json"):
                raise GuardDenied("Recovery mailbox does not own current protected credentials")
            tx.put("v3_recovery_mailboxes", str(expected.subject_uuid), record, immutable=True)
        self.scoped._run(bind)
        return True

    def check_recovery_mailbox(self, revision: CandidateCredentialRevision, email_sha256: str) -> bool:
        expected, record = self._mailbox(revision, email_sha256)
        result: list[bool] = []
        def check(tx):
            self.scoped.publication._root(tx, opened=True)
            subject = current_subject(tx, expected.subject_uuid)
            if subject.model_dump(mode="json", exclude={"enrollment_operation"}) != expected.model_dump(mode="json"):
                raise GuardDenied("Recovery mailbox does not own current protected credentials")
            result[:] = [tx.get("v3_recovery_mailboxes", str(expected.subject_uuid)) == record]
        self.scoped._run(check)
        return result[0]

    def check_account(self, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.check_account(_revision(revision))

    def check_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.check_session(context, _revision(revision))

    def revoke_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.revoke_session(context, _revision(revision))

    def advance_credentials(self, context: CandidateWebSession, old: CandidateCredentialRevision,
                            new: CandidateCredentialRevision) -> bool:
        if self.reset is None:
            raise GuardUnavailable("Normal command-bound native credential projection is not configured")
        plan = self.reset.allocate(context, _revision(old), _revision(new))
        execution = self.reset.execute(plan)
        if execution.status.status != "COMMITTED" or execution.owned_ack is None:
            raise GuardUnavailable("Original native reset projection has an unknown outcome")
        if self.scoped.complete_password_reset(self.reset, execution.owned_ack) is not True:
            raise GuardUnavailable("Original protected reset completion has an unknown outcome")
        return True

    def delete_account(self, context: CandidateWebSession, revision: CandidateCredentialRevision) -> bool:
        return self.scoped.delete_account(context, _revision(revision))
