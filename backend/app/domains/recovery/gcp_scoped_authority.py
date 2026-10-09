"""Disabled scoped lifetime component; no HTTP, signing, MayAct or product factory.

Protected tombstones and component BEGIN share one actual native transaction. A
BEGIN ACK proves only this component: remaining approval/disclosure/key checks
must compose in this same authority before any production action is possible.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock
from uuid import UUID, uuid4

from .contracts import Binding, canonical, fingerprint
from .gcp_buffer import BufferedTransaction
from .gcp_contracts import AmbiguousCommit
from .gcp_partitioned_contracts import (
    GenerationHistory,
    PartitionedRecord,
    ScopedBegin,
    ScopedCredentialRevision,
    ScopedDenial,
    ScopedJournalIntent,
    ScopedSession,
    ScopedSubject,
    ScopedTombstone,
)
from .gcp_partitioned_publication import PartitionedPublicationCoordinator, partitioned_intent
from .gcp_password_contracts import PasswordSigningMarker
from .gcp_password_lifetime import GcpPasswordLifetimeCoordinator, PasswordLifetimeOwnedAck
from .gcp_password_lifetime_contracts import (
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
    LifetimeGrantEvidence,
)
from .password_reauth import CandidateWebSession, PasswordAccountBinding, RetainedWebSession
from .store import GuardDenied, GuardUnavailable


@dataclass(frozen=True)
class ScopedDenialAck:
    _raw: bytes
    _issuer: object


@dataclass(frozen=True)
class ScopedBeginAck:
    """Opaque component acknowledgement; never an employer/action capability."""
    _raw: bytes
    _issuer: object


def _json(value) -> dict:
    return value.model_dump(mode="json")


def _origin(tx: BufferedTransaction, operation: UUID) -> LifetimeGrantEvidence:
    evidence = LifetimeGrantEvidence.model_validate(tx.get("v3_activations", str(operation)))
    full = PartitionedRecord.model_validate(tx.get("v3_records", str(operation)))
    if full.intent != _json(evidence.intent):
        raise GuardUnavailable("Protected activation lost its full original canonical intent")
    return evidence


def _pending(tx: BufferedTransaction, namespace: str, key: str, *, owner: UUID | None) -> None:
    pending = tx.get(namespace, key)
    if pending is None:
        return
    if owner is None or pending.get("operation_id") != str(owner):
        raise GuardDenied("Protected scoped denial is pending or already acknowledged")
    full = PartitionedRecord.model_validate(tx.get("v3_records", str(owner)))
    command = ScopedJournalIntent.model_validate(full.intent).command
    if (not isinstance(command, ScopedDenial)
            or pending != {"operation_id": str(owner), "intent_sha256": full.reference.sha256}):
        raise GuardUnavailable("Pending denial lost its exact full retained intent")


def current_subject(tx: BufferedTransaction, subject_id: UUID, *, pending_owner: UUID | None = None) -> ScopedSubject:
    subject = ScopedSubject.model_validate(tx.get("v3_subjects", str(subject_id)))
    evidence = _origin(tx, subject.enrollment_operation)
    command = evidence.intent.command
    if (not isinstance(command, EnrollPasswordAccount) or subject.subject_uuid != subject_id
            or command.subject_uuid != subject_id or command.account_binding_id != subject.account_binding_id
            or command.candidate_id != subject.candidate_id or command.principal_sha256 != subject.principal_sha256):
        raise GuardUnavailable("Protected subject lacks its original owned enrollment")
    owner = {"subject_uuid": str(subject_id), "account_binding_id": str(subject.account_binding_id),
        "candidate_id": subject.candidate_id, "enrollment_operation": str(subject.enrollment_operation)}
    if (tx.get("v3_account_owners", str(subject.account_binding_id)) != owner
            or tx.get("v3_candidate_owners", str(subject.candidate_id)) != owner):
        raise GuardUnavailable("Protected account ownership is missing or restored")
    history = GenerationHistory.model_validate(tx.get("v3_generation_history", f"{subject_id}:{subject.auth_generation}"))
    if (history.subject_uuid != subject_id or history.auth_generation != subject.auth_generation
            or history.credential_sha256 != subject.credential_sha256
            or tx.get("v3_generation_history", f"{subject_id}:{subject.auth_generation + 1}") is not None):
        raise GuardUnavailable("Protected credential generation is missing or rolled back")
    if subject.auth_generation == 1:
        if history.operation_id != subject.enrollment_operation or command.credential_sha256 != subject.credential_sha256:
            raise GuardUnavailable("Protected initial credential revision lost enrollment provenance")
    else:
        full = PartitionedRecord.model_validate(tx.get("v3_records", str(history.operation_id)))
        denial = ScopedJournalIntent.model_validate(full.intent).command
        if (not isinstance(denial, ScopedDenial) or denial.scope != "generation"
                or denial.subject.subject_uuid != subject_id
                or denial.subject.auth_generation != history.previous_generation
                or denial.next_credential_sha256 != subject.credential_sha256):
            raise GuardUnavailable("Protected generation lost its full retained denial provenance")
    if (tx.get("v3_subject_tombstones", str(subject_id)) is not None
            or tx.get("v3_account_tombstones", str(subject.account_binding_id)) is not None):
        raise GuardDenied("Protected account or subject has been permanently denied")
    _pending(tx, "v3_pending_subject_denials", f"{subject_id}:{subject.auth_generation}", owner=pending_owner)
    return subject


def current_session(tx: BufferedTransaction, context: CandidateWebSession, now: int, *,
                    pending_owner: UUID | None = None) -> tuple[ScopedSubject, ScopedSession]:
    session = ScopedSession.model_validate(tx.get("v3_sessions", str(context.session_id)))
    subject = current_subject(tx, session.subject_uuid, pending_owner=pending_owner)
    evidence = _origin(tx, session.activation_operation)
    command = evidence.intent.command
    if (not isinstance(command, CreateVerifiedPasswordWebSession)
            or command.session_id != session.session_id or command.account_binding_id != session.account_binding_id
            or command.expected_auth_generation != session.auth_generation
            or command.credential_sha256 != session.credential_sha256
            or command.expires_at_ms != session.expires_at_ms
            or context.candidate_id != subject.candidate_id
            or context.account_binding_id != session.account_binding_id
            or context.account_binding_id != subject.account_binding_id
            or context.session_id != session.session_id
            or session.auth_generation != subject.auth_generation
            or session.credential_sha256 != subject.credential_sha256
            or session.principal_sha256 != subject.principal_sha256
            or now >= session.expires_at_ms):
        raise GuardDenied("Protected current session and credential revision do not match")
    if tx.get("v3_session_tombstones", str(context.session_id)) is not None:
        raise GuardDenied("Protected session has been revoked")
    _pending(tx, "v3_pending_session_denials", str(context.session_id), owner=pending_owner)
    return subject, session


class ScopedCandidateAuthority:
    def __init__(self, publication: PartitionedPublicationCoordinator, *, now_ms: Callable[[], int]):
        if type(publication) is not PartitionedPublicationCoordinator or not callable(now_ms):
            raise TypeError("Exact disabled V3 coordinator and trusted clock required")
        self.publication, self.now_ms = publication, now_ms
        self._issuer, self._lock = object(), Lock()
        self._denials: dict[UUID, ScopedDenialAck] = {}
        self._begins: dict[UUID, ScopedBeginAck] = {}

    def _now(self) -> int:
        value = self.now_ms()
        if type(value) is not int or not 0 < value <= 2**53 - 1:
            raise GuardUnavailable("Strict current authority clock is unavailable")
        return value

    def _run(self, stage: Callable[[BufferedTransaction], None]) -> None:
        self.publication.registry.run(stage, before_attempt=self.publication._fresh_open)
        self.publication._fresh_open()

    def activate(self, issuer: GcpPasswordLifetimeCoordinator, ack: PasswordLifetimeOwnedAck) -> CandidateWebSession | None:
        if type(issuer) is not GcpPasswordLifetimeCoordinator or issuer.pin != self.publication.resource.pin.authority.target:
            raise GuardDenied("Exact original lifetime issuer and execution pin required")
        # Actual own-ACK/status/GCS IO is outside the protected transaction.
        evidence = issuer.consume_owned_ack(ack)
        fixed = canonical(_json(evidence)).encode()
        intent = evidence.intent
        now = self._now()
        if not intent.created_at_ms <= now < intent.deadline_ms:
            raise GuardDenied("Original lifetime activation acknowledgement is expired")
        reference, _ = partitioned_intent(canonical(_json(intent)).encode(), self.publication.resource)
        output: list[CandidateWebSession | None] = []
        def activate(tx: BufferedTransaction) -> None:
            self.publication._root(tx, opened=True)
            actual = LifetimeGrantEvidence.model_validate_json(fixed)
            full = PartitionedRecord.model_validate(tx.get("v3_records", str(intent.operation_id)))
            if full.reference != reference or full.intent != _json(actual.intent):
                raise GuardUnavailable("Owned activation requires its exact registered full canonical intent")
            if tx.get("v3_activations", str(intent.operation_id)) is not None:
                raise GuardDenied("An original activation acknowledgement cannot be adopted")
            command = actual.intent.command
            if isinstance(command, EnrollPasswordAccount):
                binding = PasswordAccountBinding.model_validate(next(r.value for r in actual.intent.effects.after
                    if r.namespace == "pairing_account_bindings" and r.key == str(command.account_binding_id)))
                if (binding.subject_uuid != command.subject_uuid or binding.candidate_id != command.candidate_id
                        or binding.principal_sha256 != command.principal_sha256):
                    raise GuardDenied("Owned enrollment effects disagree with the exact command")
                subject = ScopedSubject(subject_uuid=command.subject_uuid, account_binding_id=command.account_binding_id,
                    candidate_id=command.candidate_id, principal_sha256=command.principal_sha256, auth_generation=1,
                    credential_sha256=command.credential_sha256, enrollment_operation=intent.operation_id)
                owner = {"subject_uuid": str(subject.subject_uuid), "account_binding_id": str(subject.account_binding_id),
                    "candidate_id": subject.candidate_id, "enrollment_operation": str(intent.operation_id)}
                for namespace, key in (("v3_subjects", str(subject.subject_uuid)),
                        ("v3_account_owners", str(subject.account_binding_id)),
                        ("v3_candidate_owners", str(subject.candidate_id)),
                        ("v3_subject_tombstones", str(subject.subject_uuid)),
                        ("v3_account_tombstones", str(subject.account_binding_id))):
                    if tx.get(namespace, key) is not None:
                        raise GuardDenied("Fresh protected subject enrollment is absent or already denied")
                tx.put("v3_subjects", str(subject.subject_uuid), _json(subject))
                tx.put("v3_account_owners", str(subject.account_binding_id), owner, immutable=True)
                tx.put("v3_candidate_owners", str(subject.candidate_id), owner, immutable=True)
                history = GenerationHistory(subject_uuid=subject.subject_uuid, previous_generation=0,
                    auth_generation=1, operation_id=intent.operation_id, credential_sha256=subject.credential_sha256)
                tx.put("v3_generation_history", f"{subject.subject_uuid}:1", _json(history), immutable=True)
                output[:] = [None]
            elif isinstance(command, CreateVerifiedPasswordWebSession):
                subject = current_subject(tx, actual.intent.subject_uuid)
                retained = RetainedWebSession.model_validate(next(r.value for r in actual.intent.effects.after
                    if r.namespace == "pairing_web_sessions" and r.key == str(command.session_id)))
                if (retained.subject_uuid != subject.subject_uuid or retained.account_binding_id != subject.account_binding_id
                        or retained.principal_sha256 != subject.principal_sha256
                        or subject.auth_generation != command.expected_auth_generation
                        or subject.credential_sha256 != command.credential_sha256
                        or retained.auth_generation != subject.auth_generation
                        or retained.expires_at_ms != command.expires_at_ms or self._now() >= retained.expires_at_ms):
                    raise GuardDenied("Owned session credential and protected current generation disagree")
                if (tx.get("v3_sessions", str(command.session_id)) is not None
                        or tx.get("v3_session_tombstones", str(command.session_id)) is not None):
                    raise GuardDenied("Fresh protected session cannot adopt a prior or denied identity")
                session = ScopedSession(subject_uuid=subject.subject_uuid, account_binding_id=subject.account_binding_id,
                    session_id=command.session_id, auth_generation=subject.auth_generation,
                    principal_sha256=subject.principal_sha256, credential_sha256=subject.credential_sha256,
                    expires_at_ms=command.expires_at_ms, activation_operation=intent.operation_id)
                tx.put("v3_sessions", str(session.session_id), _json(session), immutable=True)
                output[:] = [CandidateWebSession(candidate_id=subject.candidate_id,
                    account_binding_id=subject.account_binding_id, session_id=session.session_id)]
            else:
                raise GuardDenied("Only genuine enrollment or verified session own-ACKs activate")
            tx.put("v3_activations", str(intent.operation_id), _json(actual), immutable=True)
        try:
            self._run(activate)
        except AmbiguousCommit:
            raise GuardUnavailable("Protected activation Commit unknown; original ACK consumed, no context") from None
        retained = self.publication.registry.read("v3_activations", str(intent.operation_id))
        if retained is None or canonical(retained).encode() != fixed:
            raise GuardUnavailable("Protected activation lacks its own exact full evidence")
        if output[0] is not None:
            self.resolve(output[0])
        return output[0]

    def resolve(self, context: CandidateWebSession) -> tuple[ScopedSubject, ScopedSession]:
        frozen = CandidateWebSession.model_validate_json(canonical(_json(context)))
        output: list[tuple[ScopedSubject, ScopedSession]] = []
        def read(tx: BufferedTransaction) -> None:
            self.publication._root(tx, opened=True)
            output[:] = [current_session(tx, frozen, self._now())]
        self._run(read)
        return output[0]

    def check_account(self, revision: ScopedCredentialRevision) -> bool:
        if type(revision) is not ScopedCredentialRevision:
            raise GuardDenied("Strict server-owned credential revision required")
        fixed = ScopedCredentialRevision.model_validate_json(canonical(_json(revision)))
        def check(tx: BufferedTransaction) -> None:
            self.publication._root(tx, opened=True)
            subject = current_subject(tx, fixed.subject_uuid)
            if subject.model_dump(mode="json", exclude={"enrollment_operation"}) != _json(fixed):
                raise GuardDenied("Protected current credential revision disagrees with SQL")
        self._run(check)
        return True

    def check_session(self, context: CandidateWebSession, revision: ScopedCredentialRevision) -> bool:
        if type(revision) is not ScopedCredentialRevision:
            raise GuardDenied("Strict server-owned credential revision required")
        subject, _ = self.resolve(context)
        if subject.model_dump(mode="json", exclude={"enrollment_operation"}) != _json(revision):
            raise GuardDenied("Protected session and SQL credential revision disagree")
        return True

    def revoke_session(self, context: CandidateWebSession, revision: ScopedCredentialRevision) -> bool:
        self.check_session(context, revision)
        ack = self.deny(context, scope="session")
        if ack is None:
            return False
        self.verify_denial(ack)
        return True

    def advance_credentials(self, context: CandidateWebSession, old: ScopedCredentialRevision,
                            new: ScopedCredentialRevision) -> ScopedDenialAck | None:
        self.check_session(context, old)
        if (type(new) is not ScopedCredentialRevision or new.auth_generation != old.auth_generation + 1
                or new.model_dump(exclude={"auth_generation", "credential_sha256"})
                != old.model_dump(exclude={"auth_generation", "credential_sha256"})):
            raise GuardDenied("Protected reset requires exact ownership and one new credential generation")
        ack = self.deny(context, scope="generation", next_credential_sha256=new.credential_sha256)
        if ack is not None:
            self.verify_denial(ack)
        # This is a protected DENIAL fence only. Actual native projection and
        # SQL credentials must NOT be updated or reported successful from it.
        return ack

    def delete_account(self, context: CandidateWebSession, revision: ScopedCredentialRevision) -> bool:
        self.check_session(context, revision)
        ack = self.deny(context, scope="subject")
        if ack is None:
            return False
        self.verify_denial(ack)
        return True

    def _execute(self, intent: ScopedJournalIntent, stage: Callable[[BufferedTransaction, ScopedJournalIntent], None]) -> bytes | None:
        raw = canonical(_json(intent)).encode()
        def commit(tx: BufferedTransaction) -> None:
            fixed = ScopedJournalIntent.model_validate_json(raw)
            if not fixed.created_at_ms <= self._now() < fixed.deadline_ms:
                raise GuardDenied("Scoped immutable admission plan expired")
            self.publication._append(tx, raw)
            stage(tx, fixed)
        try:
            self._run(commit)
        except AmbiguousCommit:
            return None
        full = PartitionedRecord.model_validate(self.publication.registry.read("v3_records", str(intent.operation_id)))
        if canonical(full.intent).encode() != raw:
            raise GuardUnavailable("Scoped own acknowledgement lost its full retained intent")
        self.publication._fresh_open()
        return raw

    def deny(self, context: CandidateWebSession, *, scope: str, next_credential_sha256: str | None = None) -> ScopedDenialAck | None:
        subject, session = self.resolve(context)
        now = self._now()
        command = ScopedDenial(scope=scope, subject=subject, session=session,
            next_credential_sha256=next_credential_sha256)
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=self.publication.resource.pin.authority.target,
            command=command, created_at_ms=now, deadline_ms=now + 60_000)
        namespace = "v3_pending_session_denials" if scope == "session" else "v3_pending_subject_denials"
        pending_key = str(session.session_id) if scope == "session" else f"{subject.subject_uuid}:{subject.auth_generation}"
        def arm(tx: BufferedTransaction, fixed: ScopedJournalIntent) -> None:
            assert isinstance(fixed.command, ScopedDenial)
            current, active = current_session(tx, context, self._now())
            if current != fixed.command.subject or active != fixed.command.session:
                raise GuardDenied("Scoped denial cannot retarget changed subject/session state")
            if tx.get(namespace, pending_key) is not None:
                raise GuardDenied("Scoped denial cannot adopt an existing pending operation")
            tx.put(namespace, pending_key, {"operation_id": str(fixed.operation_id),
                "intent_sha256": fingerprint(_json(fixed))}, immutable=True)
        raw = self._execute(intent, arm)
        if raw is None:
            return None
        def deny(tx: BufferedTransaction) -> None:
            self.publication._root(tx, opened=True)
            fixed = ScopedJournalIntent.model_validate_json(raw)
            assert isinstance(fixed.command, ScopedDenial)
            current, active = current_session(tx, context, self._now(), pending_owner=fixed.operation_id)
            if current != fixed.command.subject or active != fixed.command.session:
                raise GuardDenied("Scoped denial cannot retarget changed subject/session state")
            tombstone = ScopedTombstone(subject_uuid=current.subject_uuid, account_binding_id=current.account_binding_id,
                session_id=active.session_id if scope == "session" else None, operation_id=fixed.operation_id)
            if scope == "session":
                tx.put("v3_session_tombstones", str(active.session_id), _json(tombstone), immutable=True)
            elif scope == "subject":
                tx.put("v3_subject_tombstones", str(current.subject_uuid), _json(tombstone), immutable=True)
                tx.put("v3_account_tombstones", str(current.account_binding_id), _json(tombstone), immutable=True)
            else:
                assert fixed.command.next_credential_sha256 is not None
                generation = current.auth_generation + 1
                history = GenerationHistory(subject_uuid=current.subject_uuid, previous_generation=current.auth_generation,
                    auth_generation=generation, operation_id=fixed.operation_id,
                    credential_sha256=fixed.command.next_credential_sha256)
                tx.put("v3_generation_history", f"{current.subject_uuid}:{generation}", _json(history), immutable=True)
                updated = ScopedSubject.model_validate({**_json(current), "auth_generation": generation,
                    "credential_sha256": history.credential_sha256})
                tx.put("v3_subjects", str(current.subject_uuid), _json(updated))
        try:
            self._run(deny)
        except AmbiguousCommit:
            return None
        ack = ScopedDenialAck(raw, self._issuer)
        with self._lock:
            self._denials[intent.operation_id] = ack
        return ack

    def begin(self, context: CandidateWebSession, action: Binding | PasswordSigningMarker) -> ScopedBeginAck | None:
        subject, session = self.resolve(context)
        now = self._now()
        action = type(action).model_validate_json(canonical(_json(action)))
        target = self.publication.resource.pin.authority.target
        if isinstance(action, PasswordSigningMarker):
            if (action.pin != target or action.context != context or action.subject_uuid != subject.subject_uuid
                    or action.principal_sha256 != subject.principal_sha256 or action.auth_generation != subject.auth_generation
                    or not action.created_at_ms <= now < action.deadline_ms):
                raise GuardDenied("Protected signing BEGIN requires its exact current context and native pin")
            key = f"signing:{action.challenge_id}"
            deadline = min(action.deadline_ms, now + 60_000)
        elif isinstance(action, Binding):
            if (action.authority_id != target.authority_id or action.epoch_id != target.epoch_id
                    or action.subject_uuid != subject.subject_uuid or now >= action.deadline_ms):
                raise GuardDenied("Protected action BEGIN requires its exact subject, epoch and binding")
            key = f"action:{action.opening_claim_key}"
            deadline = min(action.deadline_ms, now + 60_000)
        else:
            raise GuardDenied("Full strict signing or action binding is required")
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=target, command=ScopedBegin(subject=subject,
            session=session, action=action), created_at_ms=now, deadline_ms=deadline)
        def begin(tx: BufferedTransaction, fixed: ScopedJournalIntent) -> None:
            assert isinstance(fixed.command, ScopedBegin)
            current, active = current_session(tx, context, self._now())
            if current != fixed.command.subject or active != fixed.command.session:
                raise GuardDenied("Protected BEGIN cannot retarget a changed subject/session")
            if tx.get("v3_begins", key) is not None:
                raise GuardDenied("Protected BEGIN is already consumed; no reconstructed owner")
            tx.put("v3_begins", key, {"operation_id": str(fixed.operation_id),
                "command_sha256": fingerprint(_json(fixed.command))}, immutable=True)
        raw = self._execute(intent, begin)
        if raw is None:
            return None
        # A denial immediately after the winning transaction withholds export.
        self.resolve(context)
        ack = ScopedBeginAck(raw, self._issuer)
        with self._lock:
            self._begins[intent.operation_id] = ack
        return ack

    def verify_denial(self, ack: ScopedDenialAck) -> ScopedJournalIntent:
        if type(ack) is not ScopedDenialAck or ack._issuer is not self._issuer:
            raise GuardDenied("Original scoped denial acknowledgement required")
        intent = ScopedJournalIntent.model_validate_json(ack._raw)
        with self._lock:
            if self._denials.get(intent.operation_id) is not ack:
                raise GuardDenied("Original scoped denial acknowledgement is absent")
        full = PartitionedRecord.model_validate(self.publication.registry.read("v3_records", str(intent.operation_id)))
        if canonical(full.intent).encode() != ack._raw or not isinstance(intent.command, ScopedDenial):
            raise GuardUnavailable("Scoped denial fence lost its exact retained full intent")
        self.publication._fresh_open()
        command = intent.command
        def verify(tx: BufferedTransaction) -> None:
            self.publication._root(tx, opened=True)
            assert isinstance(command, ScopedDenial)
            subject = command.subject
            tombstone = ScopedTombstone(subject_uuid=subject.subject_uuid, account_binding_id=subject.account_binding_id,
                session_id=command.session.session_id if command.scope == "session" else None,
                operation_id=intent.operation_id)
            if command.scope == "generation":
                current = current_subject(tx, subject.subject_uuid)
                if (current.auth_generation != subject.auth_generation + 1
                        or current.credential_sha256 != command.next_credential_sha256):
                    raise GuardUnavailable("Scoped credential fence lost its exact current generation")
            else:
                namespace = "v3_session_tombstones" if command.scope == "session" else "v3_subject_tombstones"
                key = str(command.session.session_id) if command.scope == "session" else str(subject.subject_uuid)
                if tx.get(namespace, key) != _json(tombstone):
                    raise GuardUnavailable("Scoped denial fence lost its own exact tombstone")
        self._run(verify)
        return intent
