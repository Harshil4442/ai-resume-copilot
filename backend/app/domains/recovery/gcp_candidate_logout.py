"""Denial-only cookie logout recovery; never an ACK, activation or action grant.

The private web request UUID is retained as the full V3 operation identity before
an unknown outcome. A retry may observe only that exact immutable command and
pending denial. It never replays signing, replaces a command or manufactures an
original native acknowledgement. Existing scoped/native source stays unchanged.
"""
from __future__ import annotations

from uuid import UUID

from ..candidate_accounts.contracts import CandidateCredentialRevision
from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedTransaction
from .gcp_partitioned_contracts import (
    PartitionedRecord,
    ScopedCredentialRevision,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedSession,
    ScopedTombstone,
)
from .gcp_password_lifetime_contracts import CreateVerifiedPasswordWebSession
from .gcp_scoped_authority import (
    ScopedCandidateAuthority,
    _origin,
    current_subject,
)
from .password_reauth import CandidateWebSession
from .store import GuardDenied, GuardUnavailable


class GcpCandidateCookieLogout:
    def __init__(self, scoped: ScopedCandidateAuthority):
        if type(scoped) is not ScopedCandidateAuthority:
            raise GuardUnavailable("Actual protected scoped authority required")
        self.scoped = scoped

    def _owned_session(self, tx: BufferedTransaction, context: CandidateWebSession,
                       expected: ScopedCredentialRevision):
        """Denial-only ownership read; expiry can never grant a capability."""
        subject = current_subject(tx, expected.subject_uuid)
        session = ScopedSession.model_validate(tx.get("v3_sessions", str(context.session_id)))
        origin = _origin(tx, session.activation_operation).intent.command
        if (subject.model_dump(mode="json", exclude={"enrollment_operation"}) != expected.model_dump(mode="json")
                or context.candidate_id != subject.candidate_id or context.account_binding_id != subject.account_binding_id
                or context.account_binding_id != session.account_binding_id or context.session_id != session.session_id
                or session.subject_uuid != subject.subject_uuid or session.auth_generation != subject.auth_generation
                or session.principal_sha256 != subject.principal_sha256 or session.credential_sha256 != subject.credential_sha256
                or not isinstance(origin, CreateVerifiedPasswordWebSession)
                or origin.account_binding_id != context.account_binding_id or origin.session_id != context.session_id
                or origin.expected_auth_generation != expected.auth_generation or origin.credential_sha256 != expected.credential_sha256
                or origin.expires_at_ms != session.expires_at_ms):
            raise GuardDenied("Denial-only logout lacks exact original session/credential ownership")
        return subject, session

    def confirm_deleted_cookie(self, context: CandidateWebSession, *, auth_generation: int) -> bool:
        """An erased SQL account may clear only its original deletion cookie."""
        frozen = CandidateWebSession.model_validate_json(context.model_dump_json())
        confirmed: list[bool] = []
        def read(tx: BufferedTransaction) -> None:
            self.scoped.publication._root(tx, opened=True)
            owner = tx.get("v3_account_owners", str(frozen.account_binding_id))
            if owner is None or owner.get("candidate_id") != frozen.candidate_id:
                raise GuardDenied("Deleted cookie lacks immutable original ownership")
            raw = tx.get("v3_account_tombstones", str(frozen.account_binding_id))
            if raw is None:
                confirmed[:] = [False]
                return
            tombstone = ScopedTombstone.model_validate(raw)
            record = PartitionedRecord.model_validate(tx.get("v3_records", str(tombstone.operation_id)))
            intent = ScopedJournalIntent.model_validate(record.intent)
            command = intent.command
            if (record.reference.operation_id != intent.operation_id
                    or record.reference.partition != intent.partition or record.reference.kind != "scoped_admission"
                    or intent.operation_id != tombstone.operation_id
                    or intent.pin != self.scoped.publication.resource.pin.authority.target
                    or not isinstance(command, ScopedDenial) or command.scope != "subject"
                    or command.subject.candidate_id != frozen.candidate_id
                    or command.subject.account_binding_id != frozen.account_binding_id
                    or command.session.session_id != frozen.session_id
                    or command.subject.auth_generation != auth_generation
                    or tombstone.subject_uuid != command.subject.subject_uuid
                    or tombstone.account_binding_id != command.subject.account_binding_id
                    or tombstone.session_id is not None
                    or tx.get("v3_subject_tombstones", str(tombstone.subject_uuid)) != raw):
                raise GuardDenied("Deleted cookie does not match its exact original retained deletion")
            expected_owner = {"subject_uuid": str(command.subject.subject_uuid),
                "account_binding_id": str(frozen.account_binding_id), "candidate_id": frozen.candidate_id,
                "enrollment_operation": str(command.subject.enrollment_operation)}
            if owner != expected_owner or tx.get("v3_candidate_owners", str(frozen.candidate_id)) != owner:
                raise GuardUnavailable("Deleted cookie lost immutable enrollment ownership")
            enrollment = _origin(tx, command.subject.enrollment_operation).intent
            session = _origin(tx, command.session.activation_operation).intent
            from .gcp_password_lifetime_contracts import EnrollPasswordAccount
            if (not isinstance(enrollment.command, EnrollPasswordAccount)
                    or enrollment.command.subject_uuid != command.subject.subject_uuid
                    or enrollment.command.account_binding_id != frozen.account_binding_id
                    or enrollment.command.candidate_id != frozen.candidate_id
                    or enrollment.command.principal_sha256 != command.subject.principal_sha256
                    or not isinstance(session.command, CreateVerifiedPasswordWebSession)
                    or session.command.account_binding_id != frozen.account_binding_id
                    or session.command.session_id != frozen.session_id
                    or session.command.expected_auth_generation != auth_generation
                    or session.command.credential_sha256 != command.subject.credential_sha256):
                raise GuardUnavailable("Deleted cookie lacks genuine native enrollment/session provenance")
            confirmed[:] = [True]
        self.scoped._run(read)
        return confirmed[0]

    def deny_cookie_session(self, context: CandidateWebSession, revision: CandidateCredentialRevision,
                            operation_id: UUID) -> bool:
        if type(operation_id) is not UUID or type(revision) is not CandidateCredentialRevision:
            raise GuardDenied("Exact server logout request and credential revision required")
        fixed_context = CandidateWebSession.model_validate_json(context.model_dump_json())
        expected = ScopedCredentialRevision.model_validate_json(revision.model_dump_json())
        if (context.candidate_id != revision.candidate_id
                or context.account_binding_id != revision.account_binding_id):
            raise GuardDenied("Logout context does not own this credential revision")
        observed: list[bool] = []

        def retained(tx: BufferedTransaction) -> None:
            self.scoped.publication._root(tx, opened=True)
            full = tx.get("v3_records", str(operation_id))
            if full is None:
                observed[:] = [False]
                return
            record = PartitionedRecord.model_validate(full)
            intent = ScopedJournalIntent.model_validate(record.intent)
            command = intent.command
            if (intent.operation_id != operation_id
                    or record.reference.operation_id != operation_id or record.reference.partition != intent.partition
                    or record.reference.kind != "scoped_admission"
                    or intent.pin != self.scoped.publication.resource.pin.authority.target
                    or not isinstance(command, ScopedDenial) or command.scope != "session"
                    or command.subject.model_dump(mode="json", exclude={"enrollment_operation"}) != expected.model_dump(mode="json")
                    or command.session.session_id != fixed_context.session_id
                    or command.session.account_binding_id != fixed_context.account_binding_id
                    or command.subject.candidate_id != fixed_context.candidate_id):
                raise GuardDenied("Retained logout operation does not match its original owner")
            # current_subject verifies immutable enrollment, owner mappings and
            # generation provenance. It never adopts a mutable SQL identity.
            if current_subject(tx, expected.subject_uuid) != command.subject:
                raise GuardDenied("Retained logout subject changed")
            if tx.get("v3_sessions", str(fixed_context.session_id)) != command.session.model_dump(mode="json"):
                raise GuardUnavailable("Retained logout lost its immutable session")
            original = _origin(tx, command.session.activation_operation).intent.command
            if (not isinstance(original, CreateVerifiedPasswordWebSession)
                    or original.session_id != fixed_context.session_id
                    or original.account_binding_id != fixed_context.account_binding_id
                    or original.expected_auth_generation != expected.auth_generation
                    or original.credential_sha256 != expected.credential_sha256
                    or original.expires_at_ms != command.session.expires_at_ms):
                raise GuardUnavailable("Retained logout lost its genuine session issuance")
            pending = {"operation_id": str(operation_id), "intent_sha256": fingerprint(intent.model_dump(mode="json"))}
            if tx.get("v3_pending_session_denials", str(fixed_context.session_id)) != pending:
                raise GuardUnavailable("Logout lacks its exact retained pending denial")
            observed[:] = [True]

        self.scoped._run(retained)
        if observed[0]:
            return True  # Denial fact only; no original-owner ACK reconstruction.
        owned = []
        self.scoped._run(lambda tx: owned.append(self._owned_session(tx, fixed_context, expected)))
        subject, session = owned[0]
        now = self.scoped._now()
        intent = ScopedJournalIntent(operation_id=operation_id,
            pin=self.scoped.publication.resource.pin.authority.target,
            command=ScopedDenial(scope="session", subject=subject, session=session),
            created_at_ms=now, deadline_ms=now + 60_000)

        def arm(tx: BufferedTransaction, fixed: ScopedJournalIntent) -> None:
            original, active = self._owned_session(tx, fixed_context, expected)
            if (original != subject or active != session
                    or canonical(fixed.command.model_dump(mode="json")) != canonical(intent.command.model_dump(mode="json"))):
                raise GuardDenied("Logout cannot retarget a changed subject or session")
            if tx.get("v3_pending_session_denials", str(session.session_id)) is not None:
                raise GuardDenied("Logout cannot adopt a different pending denial")
            tx.put("v3_pending_session_denials", str(session.session_id),
                {"operation_id": str(operation_id), "intent_sha256": fingerprint(fixed.model_dump(mode="json"))},
                immutable=True)

        if self.scoped._execute(intent, arm) is None:
            return False  # Unknown initial commit never becomes a success here.
        # All scoped readers already deny this immutable pending scope. Cookie
        # completion is distinct from a final tombstone/projection acknowledgement.
        observed.clear()
        self.scoped._run(retained)
        return observed[0]
