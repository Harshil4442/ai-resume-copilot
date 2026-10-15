"""Disabled exact credential projection; no factory, recovery grants or cohort close.

One original server request retains its complete native cut in protected scope
before any native/SQL credential change. Unknown native outcomes remain denial,
never status-adopted completion. Frozen lifetime V2 plan/grant bytes are unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from threading import Lock
from uuid import UUID, uuid4

from pydantic import model_validator

from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedTransaction
from .gcp_contracts import JournalReceipt, OperationStatus
from .gcp_journal import _exact_bytes
from .gcp_pairing_attempts import GcsPairingAttempts
from .gcp_partitioned_contracts import (
    PartitionedContract,
    ScopedCredentialRevision,
    ScopedJournalIntent,
    ScopedPasswordReset,
    ScopedSession,
    ScopedSubject,
)
from .gcp_partitioned_publication import partitioned_intent
from .gcp_password_lifetime import GcpPasswordLifetimeCoordinator, _Snapshot
from .gcp_password_lifetime_contracts import LifetimeRecord, PasswordLifetimeEffects
from .gcp_scoped_authority import ScopedCandidateAuthority, ScopedDenialAck
from .password_reauth import CandidateWebSession, RetainedWebSession
from .store import GuardDenied, GuardUnavailable


def _json(value) -> dict:
    return value.model_dump(mode="json")


class ResetProjectionEvidence(PartitionedContract):
    intent: ScopedJournalIntent
    status: OperationStatus

    @model_validator(mode="after")
    def exact(self) -> ResetProjectionEvidence:
        if (not isinstance(self.intent.command, ScopedPasswordReset)
                or self.status.status != "COMMITTED" or self.status.journal is None
                or self.status.operation_id != self.intent.operation_id
                or self.status.intent_sha256 != fingerprint(_json(self.intent))):
            raise ValueError("Reset completion requires exact full original native projection evidence")
        return self


@dataclass(frozen=True)
class PasswordResetPlan:
    _raw: bytes = field(repr=False)
    _issuer: object = field(repr=False, compare=False)

    @property
    def intent(self) -> ScopedJournalIntent:
        return ScopedJournalIntent.model_validate_json(self._raw)


@dataclass(frozen=True)
class _OwnedReset:
    plan: PasswordResetPlan
    raw: bytes = field(repr=False)
    attempted: bool = False
    denial_claimed: bool = False


@dataclass(frozen=True)
class PasswordResetNativeAck:
    _raw: bytes = field(repr=False)
    _issuer: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class PasswordResetExecution:
    status: OperationStatus
    owned_ack: PasswordResetNativeAck | None = None


class GcpPasswordCredentialProjection:
    def __init__(self, lifetime: GcpPasswordLifetimeCoordinator, scoped: ScopedCandidateAuthority):
        if (type(lifetime) is not GcpPasswordLifetimeCoordinator or type(scoped) is not ScopedCandidateAuthority
                or lifetime.closed_denial is not None
                or lifetime.pin != scoped.publication.resource.pin.authority.target):
            raise GuardUnavailable("Exact OPEN native issuer and independently pinned scoped authority required")
        self.lifetime, self.scoped = lifetime, scoped
        self._issuer, self._lock = object(), Lock()
        self._plans: dict[UUID, _OwnedReset] = {}
        self._denials: dict[UUID, ScopedDenialAck] = {}
        self._acks: dict[UUID, PasswordResetNativeAck] = {}
        self._consumed: set[UUID] = set()

    def _clock(self) -> int:
        now = self.lifetime.now_ms()
        if type(now) is not int or not 0 < now <= 2**53 - 60_001:
            raise GuardUnavailable("Reset authority clock is unavailable")
        return now

    def _effects(self, tx: BufferedTransaction, subject: ScopedSubject, session: ScopedSession, *,
                 operation: UUID, request: UUID, event_id: UUID, now: int, new_digest: str) -> PasswordLifetimeEffects:
        snap = _Snapshot(tx)
        self.lifetime._control(snap)
        head = snap.get("head", "global")
        if (head is None or set(head) != {"sequence", "digest"}
                or type(head["sequence"]) is not int or not 0 <= head["sequence"] < 2**53 - 1
                or type(head["digest"]) is not str or len(head["digest"]) != 64
                or any(v not in "0123456789abcdef" for v in head["digest"])):
            raise GuardUnavailable("Exact reset native event cut is unavailable")
        clock = snap.get("pairing_clock", "observed")
        if (clock is None or set(clock) != {"now_ms"} or type(clock["now_ms"]) is not int
                or not 0 < clock["now_ms"] <= now):
            raise GuardUnavailable("Exact reset native monotonic clock is unavailable")
        binding, native = self.lifetime._binding(snap, subject.account_binding_id)
        if (binding.candidate_id != subject.candidate_id or binding.subject_uuid != subject.subject_uuid
                or binding.principal_sha256 != subject.principal_sha256
                or native.auth_generation != subject.auth_generation):
            raise GuardDenied("Reset native immutable account owner and old generation disagree")
        old = {"subject_uuid": str(subject.subject_uuid), "auth_generation": subject.auth_generation,
               "credential_sha256": subject.credential_sha256}
        if snap.get("password_credential_revisions", str(subject.subject_uuid)) != old:
            raise GuardDenied("Reset native old exact salted-hash commitment disagrees")
        actual = RetainedWebSession.model_validate(snap.get("pairing_web_sessions", str(session.session_id)))
        if (actual.subject_uuid != subject.subject_uuid or actual.session_id != session.session_id
                or actual.account_binding_id != subject.account_binding_id
                or actual.principal_sha256 != subject.principal_sha256
                or actual.auth_generation != subject.auth_generation or actual.active is not True
                or actual.expires_at_ms != session.expires_at_ms or now >= actual.expires_at_ms):
            raise GuardDenied("Reset requires its original current native password session")
        snap.event(str(actual.registration_event_id), "PASSWORD_WEB_SESSION_CREATED",
                   actual.model_dump(mode="json", exclude={"registration_event_id"}))
        projection = {"subject_uuid": str(subject.subject_uuid), "principal_sha256": subject.principal_sha256,
                      "auth_generation": subject.auth_generation, "active": True}
        if (snap.get("pairing_sessions", str(session.session_id)) != projection
                or snap.get("pairing_web_session_tombstones", str(session.session_id)) is not None):
            raise GuardDenied("Reset native session projection is absent or denied")
        snap.absent("password_lifetime_operations", str(operation))
        snap.absent("events", str(event_id))
        changes = [LifetimeRecord(namespace="subjects", key=str(subject.subject_uuid), value={
            **native.model_dump(mode="json"), "auth_generation": subject.auth_generation + 1}),
            LifetimeRecord(namespace="pairing_auth_high_water", key=str(subject.subject_uuid), value={
                "subject_uuid": str(subject.subject_uuid), "auth_generation": subject.auth_generation + 1,
                "principal_sha256": subject.principal_sha256}),
            LifetimeRecord(namespace="password_credential_revisions", key=str(subject.subject_uuid), value={
                **old, "auth_generation": subject.auth_generation + 1, "credential_sha256": new_digest}),
            LifetimeRecord(namespace="pairing_clock", key="observed", value={"now_ms": now})]
        payload = {"request_id": str(request), "subject_uuid": str(subject.subject_uuid),
            "account_binding_id": str(subject.account_binding_id), "candidate_id": subject.candidate_id,
            "principal_sha256": subject.principal_sha256, "old_auth_generation": subject.auth_generation,
            "old_credential_sha256": subject.credential_sha256, "auth_generation": subject.auth_generation + 1,
            "credential_sha256": new_digest}
        event = {"sequence": head["sequence"] + 1, "previous": head["digest"],
                 "kind": "PASSWORD_CREDENTIAL_REVISION_PROJECTED", "payload": payload, "event_id": str(event_id)}
        event["digest"] = fingerprint(event)
        changes += [LifetimeRecord(namespace="events", key=str(event_id), value=event),
                    LifetimeRecord(namespace="head", key="global", value={"sequence": event["sequence"], "digest": event["digest"]})]
        return PasswordLifetimeEffects(before=tuple(LifetimeRecord(namespace=n, key=k, value=v)
            for (n, k), v in sorted(snap.records.items())), after=tuple(changes), event=event)

    def allocate(self, context: CandidateWebSession, old: ScopedCredentialRevision,
                 new: ScopedCredentialRevision) -> PasswordResetPlan:
        if (type(old) is not ScopedCredentialRevision or type(new) is not ScopedCredentialRevision
                or old.auth_generation >= 2**53 - 1 or new.auth_generation != old.auth_generation + 1
                or new.model_dump(exclude={"auth_generation", "credential_sha256"})
                    != old.model_dump(exclude={"auth_generation", "credential_sha256"})):
            raise GuardDenied("Reset requires exact server ownership and old/new credential revisions")
        self.scoped.check_session(context, old)
        subject, session = self.scoped.resolve(context)
        now, operation, request, event_id = self._clock(), uuid4(), uuid4(), uuid4()
        effects: list[PasswordLifetimeEffects] = []
        def capture(tx: BufferedTransaction) -> None:
            effects[:] = [self._effects(tx, subject, session, operation=operation, request=request,
                event_id=event_id, now=now, new_digest=new.credential_sha256)]
        self.lifetime.registry.run(capture, before_attempt=lambda: self.lifetime.fence.check(self.lifetime.pin))
        self.scoped.check_session(context, old)
        command = ScopedPasswordReset(subject=subject, session=session, next_credential_sha256=new.credential_sha256,
            request_id=request, event_id=event_id, native_effects=effects[0])
        intent = ScopedJournalIntent(operation_id=operation, pin=self.lifetime.pin, command=command,
            created_at_ms=now, deadline_ms=now + 60_000)
        raw = canonical(_json(intent)).encode()
        partitioned_intent(raw, self.scoped.publication.resource)  # Full bounded envelope validation.
        plan = PasswordResetPlan(raw, self._issuer)
        with self._lock:
            self._plans[operation] = _OwnedReset(plan, bytes(raw))
        return plan

    def consume_denial_plan(self, plan: PasswordResetPlan) -> ScopedJournalIntent:
        if type(plan) is not PasswordResetPlan or plan._issuer is not self._issuer:
            raise GuardDenied("Original reset request plan required")
        intent = plan.intent
        with self._lock:
            owned = self._plans.get(intent.operation_id)
            if owned is None or owned.plan is not plan or owned.raw != plan._raw or not owned.attempted or owned.denial_claimed:
                raise GuardDenied("Reset request cannot be detached, adopted or armed twice")
            self._plans[intent.operation_id] = replace(owned, denial_claimed=True)
        if not intent.created_at_ms <= self._clock() < intent.deadline_ms:
            raise GuardDenied("Original reset request expired")
        return intent

    def _before(self, intent: ScopedJournalIntent, denial: ScopedDenialAck, receipt: JournalReceipt | None = None) -> None:
        if (not intent.created_at_ms <= self._clock() < intent.deadline_ms
                or self.scoped.verify_denial(denial) != intent):
            raise GuardUnavailable("Original reset denial/request fence is no longer current")
        self.lifetime.fence.check(self.lifetime.pin)
        if receipt is not None:
            ref, _ = partitioned_intent(canonical(_json(intent)).encode(), self.scoped.publication.resource)
            if (receipt.bucket != self.scoped.publication.resource.bucket or receipt.path != ref.path
                    or receipt.sha256 != ref.sha256):
                raise GuardUnavailable("Reset receipt cannot retarget full protected native plan")
            try:
                _exact_bytes(self.scoped.publication.bucket, ref.path, canonical(_json(intent)).encode(),
                    generation=receipt.generation, timeout=2.0)
            except Exception:
                raise GuardUnavailable("Exact retained reset native plan is unavailable") from None
        self.scoped.publication._fresh_open()

    def execute(self, plan: PasswordResetPlan) -> PasswordResetExecution:
        if type(plan) is not PasswordResetPlan or plan._issuer is not self._issuer:
            raise GuardDenied("Original reset request plan required")
        intent = plan.intent
        with self._lock:
            owned = self._plans.get(intent.operation_id)
            if owned is None or owned.plan is not plan or owned.raw != plan._raw:
                raise GuardDenied("Reset request plan cannot be detached or adopted")
            repeated = owned.attempted
            if not repeated:
                self._plans[intent.operation_id] = replace(owned, attempted=True)
        raw = owned.raw
        def before(denial: ScopedDenialAck, receipt: JournalReceipt | None = None) -> None:
            if plan._issuer is not self._issuer or plan._raw != raw or canonical(_json(intent)).encode() != raw:
                raise GuardDenied("Original coordinator-owned reset bytes changed")
            self._before(intent, denial, receipt)
            if plan._issuer is not self._issuer or plan._raw != raw or canonical(_json(intent)).encode() != raw:
                raise GuardDenied("Original coordinator-owned reset bytes changed")
        if repeated:
            return PasswordResetExecution(self.status(intent))
        denial = self.scoped.arm_password_reset(self, plan)
        if denial is None:
            return PasswordResetExecution(self._unknown(intent))
        self._denials[intent.operation_id] = denial
        try:
            before(denial)
            ref, _ = partitioned_intent(raw, self.scoped.publication.resource)
            generation = GcsPairingAttempts(self.scoped.publication.bucket)._fresh_create(ref.path, raw)
            if generation is None:
                return PasswordResetExecution(self._unknown(intent))
            receipt = JournalReceipt(bucket=self.scoped.publication.resource.bucket, path=ref.path,
                generation=generation, sha256=ref.sha256)
            before(denial, receipt)
            def project(tx: BufferedTransaction) -> None:
                fixed = ScopedJournalIntent.model_validate_json(raw)
                cmd = fixed.command
                assert isinstance(cmd, ScopedPasswordReset)
                effects = self._effects(tx, cmd.subject, cmd.session, operation=fixed.operation_id,
                    request=cmd.request_id, event_id=cmd.event_id, now=fixed.created_at_ms,
                    new_digest=cmd.next_credential_sha256)
                if canonical(_json(effects)) != canonical(_json(cmd.native_effects)):
                    raise GuardDenied("Reset full native cut changed; original plan cannot retarget")
                for record in effects.after:
                    if record.namespace not in {"events", "head"}:
                        assert record.value is not None
                        tx.put(record.namespace, record.key, record.value)
                event = tx.append(str(cmd.event_id), effects.event["kind"], effects.event["payload"])
                if event != effects.event:
                    raise GuardDenied("Reset full native event cut changed")
                tx.put("password_lifetime_effects", str(fixed.operation_id), _json(effects), immutable=True)
                tx.put("password_lifetime_operations", str(fixed.operation_id), {"intent_sha256": ref.sha256,
                    "journal": _json(receipt), "event_id": str(cmd.event_id)}, immutable=True)
                if not fixed.created_at_ms <= self._clock() < fixed.deadline_ms:
                    raise GuardDenied("Original reset native execution interval expired")
            self.lifetime.registry.run(project, before_attempt=lambda: before(denial, receipt))
            before(denial, receipt)
        except Exception:
            # No raw provider/native exception is a recovery grant or disclosure.
            return PasswordResetExecution(self._unknown(intent))
        status = OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                                 intent_sha256=ref.sha256, journal=receipt)
        evidence = ResetProjectionEvidence(intent=intent, status=status)
        ack = PasswordResetNativeAck(canonical(_json(evidence)).encode(), self._issuer)
        with self._lock:
            self._acks[intent.operation_id] = ack
        return PasswordResetExecution(status, ack)

    def status(self, intent: ScopedJournalIntent) -> OperationStatus:
        fixed = ScopedJournalIntent.model_validate_json(canonical(_json(intent)))
        cmd = fixed.command
        if not isinstance(cmd, ScopedPasswordReset) or fixed.pin != self.lifetime.pin:
            raise GuardDenied("Exact original reset request pin required")
        try:
            self.scoped.publication._fresh_open()
            self.lifetime.fence.check(self.lifetime.pin)
            values = self.lifetime.registry.read_many((("password_lifetime_operations", str(fixed.operation_id)),
                ("password_lifetime_effects", str(fixed.operation_id)), ("events", str(cmd.event_id)),
                ("head", "global"), ("control", "meta"), ("pairing_control", "current")))
            row = values["password_lifetime_operations", str(fixed.operation_id)]
            if (row is None or set(row) != {"intent_sha256", "journal", "event_id"}
                    or row["intent_sha256"] != fingerprint(_json(fixed)) or row["event_id"] != str(cmd.event_id)
                    or values["password_lifetime_effects", str(fixed.operation_id)] != _json(cmd.native_effects)
                    or values["events", str(cmd.event_id)] != cmd.native_effects.event):
                raise GuardUnavailable("Reset projection lacks full retained native evidence")
            head = values["head", "global"]
            if (head is None or set(head) != {"sequence", "digest"}
                    or type(head.get("sequence")) is not int
                    or type(head.get("digest")) is not str or len(head["digest"]) != 64
                    or any(v not in "0123456789abcdef" for v in head["digest"])
                    or not cmd.native_effects.event["sequence"] <= head["sequence"] <= 2**53 - 1
                    or (head["sequence"] == cmd.native_effects.event["sequence"]
                        and head.get("digest") != cmd.native_effects.event["digest"])):
                raise GuardUnavailable("Reset native event cut is missing or regressed")
            receipt = JournalReceipt.model_validate(row["journal"])
            ref, _ = partitioned_intent(canonical(_json(fixed)).encode(), self.scoped.publication.resource)
            if receipt.bucket != self.scoped.publication.resource.bucket or receipt.path != ref.path or receipt.sha256 != ref.sha256:
                raise GuardUnavailable("Reset status receipt has changed")
            _exact_bytes(self.scoped.publication.bucket, ref.path, canonical(_json(fixed)).encode(),
                generation=receipt.generation, timeout=2.0)
            def control(tx: BufferedTransaction) -> None:
                self.lifetime._control(_Snapshot(tx))
            self.lifetime.registry.run(control, before_attempt=lambda: self.lifetime.fence.check(self.lifetime.pin))
            self.scoped.publication._fresh_open()
            return OperationStatus(status="COMMITTED", operation_id=fixed.operation_id,
                intent_sha256=ref.sha256, journal=receipt)
        except Exception:
            return self._unknown(fixed)

    def consume_owned_ack(self, ack: PasswordResetNativeAck, denial: ScopedDenialAck) -> ResetProjectionEvidence:
        if type(ack) is not PasswordResetNativeAck or ack._issuer is not self._issuer:
            raise GuardDenied("Original owned native reset acknowledgement required")
        evidence = ResetProjectionEvidence.model_validate_json(ack._raw)
        op = evidence.intent.operation_id
        with self._lock:
            owned = self._plans.get(op)
            if (self._acks.get(op) is not ack or self._denials.get(op) is not denial or op in self._consumed
                    or owned is None or canonical(_json(evidence.intent)).encode() != owned.raw):
                raise GuardDenied("Reset native acknowledgement cannot be detached, replayed or adopted")
            self._consumed.add(op)
        self._before(evidence.intent, denial, evidence.status.journal)
        if self.status(evidence.intent) != evidence.status:
            raise GuardUnavailable("Reset native acknowledgement lost its complete retained evidence")
        cmd = evidence.intent.command
        assert isinstance(cmd, ScopedPasswordReset)
        def current(tx: BufferedTransaction) -> None:
            snap = _Snapshot(tx)
            self.lifetime._control(snap)
            snap.get("head", "global")
            binding, subject = self.lifetime._binding(snap, cmd.subject.account_binding_id)
            if (binding.subject_uuid != cmd.subject.subject_uuid or binding.candidate_id != cmd.subject.candidate_id
                    or binding.principal_sha256 != cmd.subject.principal_sha256
                    or subject.auth_generation != cmd.subject.auth_generation + 1
                    or snap.get("password_credential_revisions", str(cmd.subject.subject_uuid)) != {
                        "subject_uuid": str(cmd.subject.subject_uuid), "auth_generation": subject.auth_generation,
                        "credential_sha256": cmd.next_credential_sha256}):
                raise GuardUnavailable("Reset native current owner/generation/credential was restored or changed")
        self.lifetime.registry.run(current, before_attempt=lambda: self._before(evidence.intent, denial))
        self._before(evidence.intent, denial)
        return evidence

    def denial_for(self, ack: PasswordResetNativeAck) -> ScopedDenialAck:
        if type(ack) is not PasswordResetNativeAck or ack._issuer is not self._issuer:
            raise GuardDenied("Original native reset acknowledgement required")
        evidence = ResetProjectionEvidence.model_validate_json(ack._raw)
        with self._lock:
            if self._acks.get(evidence.intent.operation_id) is not ack:
                raise GuardDenied("Original native reset acknowledgement is absent")
            return self._denials[evidence.intent.operation_id]

    @staticmethod
    def _unknown(intent: ScopedJournalIntent) -> OperationStatus:
        return OperationStatus(status="UNKNOWN", operation_id=intent.operation_id, intent_sha256=fingerprint(_json(intent)))
