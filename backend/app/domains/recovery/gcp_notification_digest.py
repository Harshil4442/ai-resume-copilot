"""Genuine scoped digest consent and owned native send-attempt transactions.

This component never invokes a provider. The configured notifications adapter
owns callback ordering and never exports these process-owned acknowledgements.
Stored BEGIN/outcome rows alone cannot reconstruct a send or retry owner.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from uuid import UUID, uuid4

from ..candidate_accounts.contracts import CandidateCredentialRevision
from ..notification_consent.unsubscribe import VerifiedUnsubscribeProof
from ..notifications.mail_budget import ProviderResult, _result
from .contracts import fingerprint
from .gcp_buffer import BufferedTransaction
from .gcp_contracts import AmbiguousCommit
from .gcp_partitioned_contracts import (
    DigestConsentHead,
    DigestSendOutcome,
    PartitionedRecord,
    ScopedDigestConsentChange,
    ScopedDigestEmailDisable,
    ScopedDigestSendBegin,
    ScopedDigestSendOutcome,
    ScopedJournalIntent,
    ScopedSubject,
)
from .gcp_scoped_authority import ScopedCandidateAuthority, current_session, current_subject
from .password_reauth import CandidateWebSession
from .store import GuardDenied, GuardUnavailable

HEADS = "v3_digest_consent_heads"
HISTORY = "v3_digest_consent_history"
BEGINS = "v3_digest_send_begins"
OUTCOMES = "v3_digest_send_outcomes"


def iso_week(now_ms: int) -> str:
    if type(now_ms) is not int or not 0 < now_ms <= 253_402_300_799_999:
        raise GuardUnavailable("Digest current UTC clock is unavailable")
    year, week, _ = datetime.fromtimestamp(now_ms / 1000, UTC).isocalendar()
    return f"{year:04d}-W{week:02d}"


def consent_after(intent: ScopedJournalIntent) -> DigestConsentHead:
    command = intent.command
    if isinstance(command, ScopedDigestConsentChange):
        return DigestConsentHead(subject=command.subject, generation=command.generation,
            enabled=command.enabled, email_sha256=command.email_sha256,
            policy_version=command.policy_version, operation_id=intent.operation_id)
    if isinstance(command, ScopedDigestEmailDisable):
        return DigestConsentHead(subject=command.subject, generation=command.previous.generation + 1,
            enabled=False, email_sha256=command.previous.email_sha256,
            policy_version=command.previous.policy_version, operation_id=intent.operation_id)
    raise GuardUnavailable("Exact full digest consent command required")


def begin_key(command: ScopedDigestSendBegin) -> str:
    return f"{command.subject.subject_uuid}:{command.iso_week}:{command.ordinal}"


def _record(tx: BufferedTransaction, operation: UUID) -> tuple[PartitionedRecord, ScopedJournalIntent]:
    full = PartitionedRecord.model_validate(tx.get("v3_records", str(operation)))
    intent = ScopedJournalIntent.model_validate(full.intent)
    if full.reference.kind != "scoped_admission" or full.reference.operation_id != operation:
        raise GuardUnavailable("Digest provenance lacks its exact retained scoped record")
    return full, intent


def read_consent(tx: BufferedTransaction, subject: ScopedSubject) -> DigestConsentHead | None:
    key = str(subject.subject_uuid)
    raw = tx.get(HEADS, key)
    if raw is None:
        if tx.get(HISTORY, f"{key}:1") is not None:
            raise GuardUnavailable("Digest consent head was lost or rolled back")
        return None
    head = DigestConsentHead.model_validate(raw)
    _, intent = _record(tx, head.operation_id)
    if (head != consent_after(intent)
            or head.subject.model_dump(exclude={"auth_generation", "credential_sha256"})
                != subject.model_dump(exclude={"auth_generation", "credential_sha256"})
            or tx.get(HISTORY, f"{key}:{head.generation}") != head.model_dump(mode="json")
            or tx.get(HISTORY, f"{key}:{head.generation + 1}") is not None):
        raise GuardUnavailable("Digest consent lost its current generation or original ownership")
    return head


def _owner(tx: BufferedTransaction, revision: CandidateCredentialRevision, email_sha256: str,
           *, context: CandidateWebSession | None = None, now: int) -> tuple[ScopedSubject, object | None]:
    if type(revision) is not CandidateCredentialRevision:
        raise GuardDenied("Exact current candidate credential revision required")
    subject, session = (current_session(tx, context, now) if context is not None
                        else (current_subject(tx, revision.subject_uuid), None))
    if subject.model_dump(mode="json", exclude={"enrollment_operation"}) != revision.model_dump(mode="json"):
        raise GuardDenied("Digest credentials do not match the current protected subject")
    mailbox = {"version": 1, "subject_uuid": str(subject.subject_uuid),
        "candidate_id": subject.candidate_id, "account_binding_id": str(subject.account_binding_id),
        "principal_sha256": subject.principal_sha256, "email_sha256": email_sha256}
    if tx.get("v3_recovery_mailboxes", str(subject.subject_uuid)) != mailbox:
        raise GuardDenied("Digest recipient does not match the current protected mailbox")
    return subject, session


@dataclass(frozen=True)
class _DigestBeginAck:
    raw: bytes
    issuer: object


@dataclass(frozen=True)
class _DigestOutcomeAck:
    begin: _DigestBeginAck
    outcome: DigestSendOutcome
    issuer: object


class GcpDigestAuthority:
    def __init__(self, scoped: ScopedCandidateAuthority):
        if type(scoped) is not ScopedCandidateAuthority:
            raise GuardUnavailable("Actual configured scoped candidate authority required")
        self.scoped = scoped
        self._issuer, self._lock = object(), Lock()
        self._begins: dict[UUID, tuple[_DigestBeginAck, str]] = {}
        self._outcomes: dict[UUID, _DigestOutcomeAck] = {}
        self._windows: set[tuple[UUID, str]] = set()

    def _run(self, action):
        self.scoped._run(action)

    def read_current(self, revision: CandidateCredentialRevision, email_sha256: str) -> DigestConsentHead | None:
        result: list[DigestConsentHead | None] = []
        def read(tx):
            self.scoped.publication._root(tx, opened=True)
            subject, _ = _owner(tx, revision, email_sha256, now=self.scoped._now())
            head = read_consent(tx, subject)
            if head is not None and _record(tx, head.operation_id)[1].pin != self.scoped.publication.resource.pin.authority.target:
                raise GuardUnavailable("Digest consent provenance has another execution pin")
            result[:] = [head]
        self._run(read)
        return result[0]

    def change(self, context: CandidateWebSession, revision: CandidateCredentialRevision, *,
               expected_generation: int, enabled: bool, email_sha256: str,
               policy_version: str) -> DigestConsentHead:
        if type(expected_generation) is not int or not 0 <= expected_generation < 2**53 - 1:
            raise GuardDenied("Exact expected digest consent generation required")
        prepared: list[ScopedDigestConsentChange] = []
        def prepare(tx):
            self.scoped.publication._root(tx, opened=True)
            subject, session = _owner(tx, revision, email_sha256, context=context, now=self.scoped._now())
            previous = read_consent(tx, subject)
            if (previous.generation if previous else 0) != expected_generation:
                raise GuardDenied("Digest consent generation changed")
            prepared[:] = [ScopedDigestConsentChange(subject=subject, session=session, previous=previous,
                generation=expected_generation + 1, enabled=enabled,
                email_sha256=email_sha256, policy_version=policy_version)]
        self._run(prepare)
        now = self.scoped._now()
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=self.scoped.publication.resource.pin.authority.target,
            command=prepared[0], created_at_ms=now, deadline_ms=now + 60_000)
        def change(tx, fixed):
            command = fixed.command
            assert isinstance(command, ScopedDigestConsentChange)
            subject, session = _owner(tx, revision, email_sha256, context=context, now=self.scoped._now())
            if (subject != command.subject or session != command.session
                    or read_consent(tx, subject) != command.previous):
                raise GuardDenied("Digest consent cannot adopt changed native state")
            after = consent_after(fixed)
            tx.put(HISTORY, f"{subject.subject_uuid}:{after.generation}", after.model_dump(mode="json"), immutable=True)
            tx.put(HEADS, str(subject.subject_uuid), after.model_dump(mode="json"))
        if self.scoped._execute(intent, change) is None:
            raise GuardUnavailable("Digest consent Commit unknown")
        current = self.read_current(revision, email_sha256)
        if current != consent_after(intent):
            raise GuardUnavailable("Digest consent lost its original acknowledgement")
        return current

    def _disable_verified_email(self, revision: CandidateCredentialRevision,
                                proof: VerifiedUnsubscribeProof) -> DigestConsentHead:
        if type(proof) is not VerifiedUnsubscribeProof or type(revision) is not CandidateCredentialRevision:
            raise GuardDenied("Original verified unsubscribe proof and current credentials required")
        proof.require_current(self.scoped._now())
        if proof.identity.model_dump(mode="json") != revision.model_dump(mode="json",
                exclude={"auth_generation", "credential_sha256"}):
            raise GuardDenied("Unsubscribe proof does not own current native account")
        email_sha256 = proof.email_sha256
        previous = self.read_current(revision, email_sha256)
        if (previous is None or previous.generation != proof.expected_generation
                or previous.policy_version != proof.policy_version):
            raise GuardDenied("Email disable consent generation changed")
        prepared: list[ScopedDigestEmailDisable] = []
        def prepare(tx):
            self.scoped.publication._root(tx, opened=True)
            subject, _ = _owner(tx, revision, email_sha256, now=self.scoped._now())
            proof.require_current(self.scoped._now())
            if read_consent(tx, subject) != previous:
                raise GuardDenied("Email disable cannot adopt changed consent")
            prepared[:] = [ScopedDigestEmailDisable(subject=subject, previous=previous, proof_sha256=proof.proof_sha256)]
        self._run(prepare)
        now = self.scoped._now()
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=self.scoped.publication.resource.pin.authority.target,
            command=prepared[0], created_at_ms=now, deadline_ms=now + 60_000)
        def disable(tx, fixed):
            command = fixed.command
            assert isinstance(command, ScopedDigestEmailDisable)
            proof.require_current(self.scoped._now())
            subject, _ = _owner(tx, revision, email_sha256, now=self.scoped._now())
            if subject != command.subject or read_consent(tx, subject) != command.previous:
                raise GuardDenied("Email disable cannot retarget ownership or consent")
            after = consent_after(fixed)
            tx.put(HISTORY, f"{subject.subject_uuid}:{after.generation}", after.model_dump(mode="json"), immutable=True)
            tx.put(HEADS, str(subject.subject_uuid), after.model_dump(mode="json"))
        if self.scoped._execute(intent, disable) is None:
            raise GuardUnavailable("Email disable Commit unknown")
        current = self.read_current(revision, email_sha256)
        if current != consent_after(intent):
            raise GuardUnavailable("Email disable lost its original acknowledgement")
        return current

    def begin(self, revision: CandidateCredentialRevision, *, email_sha256: str,
              consent_generation: int, policy_version: str, message_sha256: str,
              previous: _DigestOutcomeAck | None = None,
              expected_week: str | None = None) -> _DigestBeginAck:
        if expected_week is not None and type(expected_week) is not str:
            raise GuardDenied("Exact queued digest week required")
        prior = None
        if previous is not None:
            if type(previous) is not _DigestOutcomeAck or previous.issuer is not self._issuer:
                raise GuardDenied("Original digest retry acknowledgement required")
            with self._lock:
                operation = ScopedJournalIntent.model_validate_json(previous.begin.raw).operation_id
                if (previous.issuer is not self._issuer
                        or self._outcomes.pop(operation, None) is not previous):
                    raise GuardDenied("Digest retry requires its original acknowledged outcome")
            prior = ScopedJournalIntent.model_validate_json(previous.begin.raw)
            if not isinstance(prior.command, ScopedDigestSendBegin):
                raise GuardDenied("Exact original digest attempt required")
        prepared: list[ScopedDigestSendBegin] = []
        def prepare(tx):
            self.scoped.publication._root(tx, opened=True)
            now = self.scoped._now()
            subject, _ = _owner(tx, revision, email_sha256, now=now)
            head = read_consent(tx, subject)
            if (head is None or not head.enabled
                    or head.subject.model_dump(exclude={"auth_generation", "credential_sha256"})
                        != subject.model_dump(exclude={"auth_generation", "credential_sha256"})
                    or head.generation != consent_generation or head.email_sha256 != email_sha256
                    or head.policy_version != policy_version):
                raise GuardDenied("Digest current consent is absent, stale or disabled")
            week = iso_week(now)
            if expected_week is not None and expected_week != week:
                raise GuardDenied("Queued digest week is not the current native UTC week")
            ordinal = prior.command.ordinal + 1 if prior else 1
            command = ScopedDigestSendBegin(subject=subject, consent=head, iso_week=week,
                ordinal=ordinal, message_sha256=message_sha256,
                previous_outcome=previous.outcome if previous else None)
            if prior is not None:
                assert previous is not None
                before = prior.command
                assert isinstance(before, ScopedDigestSendBegin)
                if (before.model_dump(exclude={"ordinal", "previous_outcome"})
                        != command.model_dump(exclude={"ordinal", "previous_outcome"})
                        or tx.get(OUTCOMES, str(prior.operation_id)) != previous.outcome.model_dump(mode="json")
                        or tx.get(BEGINS, begin_key(before)) != {"operation_id": str(prior.operation_id),
                            "intent_sha256": fingerprint(prior.model_dump(mode="json"))}):
                    raise GuardDenied("Digest retry cannot retarget its original weekly message")
            # Fresh SQL jobs/reconsent/epochs cannot acquire another weekly slot.
            for index in range(1, 4):
                key = f"{subject.subject_uuid}:{week}:{index}"
                existing = tx.get(BEGINS, key)
                if index >= ordinal and existing is not None:
                    raise GuardDenied("Digest weekly attempt already begun; no reconstructed owner")
            prepared[:] = [command]
        self._run(prepare)
        now = self.scoped._now()
        intent = ScopedJournalIntent(operation_id=uuid4(), pin=self.scoped.publication.resource.pin.authority.target,
            command=prepared[0], created_at_ms=now, deadline_ms=now + 60_000)
        def begin(tx, fixed):
            command = fixed.command
            assert isinstance(command, ScopedDigestSendBegin)
            subject, _ = _owner(tx, revision, email_sha256, now=self.scoped._now())
            if (iso_week(self.scoped._now()) != command.iso_week
                    or expected_week is not None and command.iso_week != expected_week
                    or subject != command.subject
                    or read_consent(tx, subject) != command.consent):
                raise GuardDenied("Digest BEGIN cannot adopt changed consent, credentials or week")
            if tx.get(BEGINS, begin_key(command)) is not None:
                raise GuardDenied("Digest weekly attempt already consumed")
            if command.ordinal > 1:
                assert previous is not None and prior is not None
                if tx.get(OUTCOMES, str(prior.operation_id)) != previous.outcome.model_dump(mode="json"):
                    raise GuardDenied("Digest retry lost its exact known outcome")
            tx.put(BEGINS, begin_key(command), {"operation_id": str(fixed.operation_id),
                "intent_sha256": fingerprint(fixed.model_dump(mode="json"))}, immutable=True)
        if prior is None:
            window = revision.subject_uuid, prepared[0].iso_week
            with self._lock:
                if window in self._windows:
                    raise GuardDenied("Digest live weekly chain is consumed or uncertain")
                self._windows.add(window)
        raw = self.scoped._execute(intent, begin)
        if raw is None:
            raise GuardUnavailable("Digest BEGIN Commit unknown; no callback")
        ack = _DigestBeginAck(raw, self._issuer)
        with self._lock:
            self._begins[intent.operation_id] = ack, "begun"
        return ack

    def claim(self, ack: _DigestBeginAck, revision: CandidateCredentialRevision, email_sha256: str) -> None:
        if type(ack) is not _DigestBeginAck or ack.issuer is not self._issuer:
            raise GuardDenied("Original digest BEGIN acknowledgement required")
        intent = ScopedJournalIntent.model_validate_json(ack.raw)
        with self._lock:
            if (type(ack) is not _DigestBeginAck or ack.issuer is not self._issuer
                    or self._begins.get(intent.operation_id) != (ack, "begun")
                    or self._begins[intent.operation_id][0] is not ack):
                raise GuardDenied("Original unconsumed digest BEGIN acknowledgement required")
            self._begins[intent.operation_id] = ack, "claimed"
        def check(tx):
            self.scoped.publication._root(tx, opened=True)
            command = intent.command
            assert isinstance(command, ScopedDigestSendBegin)
            subject, _ = _owner(tx, revision, email_sha256, now=self.scoped._now())
            if (not intent.created_at_ms <= self.scoped._now() < intent.deadline_ms
                    or iso_week(self.scoped._now()) != command.iso_week
                    or command.subject != subject or read_consent(tx, subject) != command.consent
                    or tx.get(BEGINS, begin_key(command)) != {"operation_id": str(intent.operation_id),
                        "intent_sha256": fingerprint(intent.model_dump(mode="json"))}
                    or _record(tx, intent.operation_id)[1] != intent):
                raise GuardDenied("Digest callback scope changed after BEGIN")
        self._run(check)

    def record_outcome(self, ack: _DigestBeginAck, result: ProviderResult) -> _DigestOutcomeAck:
        receipt = _result(result)
        if type(ack) is not _DigestBeginAck or ack.issuer is not self._issuer:
            raise GuardDenied("Original digest callback acknowledgement required")
        intent = ScopedJournalIntent.model_validate_json(ack.raw)
        with self._lock:
            if (type(ack) is not _DigestBeginAck or ack.issuer is not self._issuer
                    or self._begins.get(intent.operation_id) != (ack, "claimed")
                    or self._begins[intent.operation_id][0] is not ack):
                raise GuardDenied("Digest outcome requires its original claimed callback")
            self._begins[intent.operation_id] = ack, "finished"
        now, outcome_operation = self.scoped._now(), uuid4()
        outcome = DigestSendOutcome(record_operation=outcome_operation, operation_id=intent.operation_id,
            intent_sha256=fingerprint(intent.model_dump(mode="json")), outcome=receipt.outcome,
            receipt_sha256=receipt.receipt_sha256, status_code=receipt.status_code,
            recorded_at_ms=now)
        outcome_intent = ScopedJournalIntent(operation_id=outcome_operation,
            pin=self.scoped.publication.resource.pin.authority.target,
            command=ScopedDigestSendOutcome(subject=intent.command.subject,
                begin_intent=intent.model_dump(mode="json"), result=outcome),
            created_at_ms=now, deadline_ms=now + 60_000)
        def finish(tx, fixed):
            if fixed != outcome_intent:
                raise GuardDenied("Digest outcome lost its own exact closed operation")
            command = intent.command
            assert isinstance(command, ScopedDigestSendBegin)
            if (_record(tx, intent.operation_id)[1] != intent
                    or tx.get(BEGINS, begin_key(command)) != {"operation_id": str(intent.operation_id),
                        "intent_sha256": outcome.intent_sha256}
                    or tx.get(OUTCOMES, str(intent.operation_id)) is not None):
                raise GuardDenied("Digest outcome cannot adopt another attempt")
            tx.put(OUTCOMES, str(intent.operation_id), outcome.model_dump(mode="json"), immutable=True)
        try:
            if self.scoped._execute(outcome_intent, finish) is None:
                raise AmbiguousCommit("Digest outcome Commit unknown")
        except AmbiguousCommit:
            raise GuardUnavailable("Digest outcome Commit unknown; no retry owner") from None
        if self.scoped.publication.registry.read(OUTCOMES, str(intent.operation_id)) != outcome.model_dump(mode="json"):
            raise GuardUnavailable("Digest outcome lost its original acknowledgement")
        self.scoped.publication._fresh_open()
        owned = _DigestOutcomeAck(ack, outcome, self._issuer)
        with self._lock:
            self._outcomes[intent.operation_id] = owned
        return owned
