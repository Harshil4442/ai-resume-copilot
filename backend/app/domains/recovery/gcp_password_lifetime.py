"""Restricted injected lifetime writer; no SQL/browser bootstrap or runtime factory.

Every plan fixes complete effects and the global event cut before protected IO.
Contention requires a fresh explicit plan, never retargets a protected plan.
"""
from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from threading import Lock
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from .contracts import Revocation, canonical, fingerprint
from .gcp_buffer import BufferedRegistry, BufferedTransaction
from .gcp_contracts import AmbiguousCommit, JournalReceipt, OperationStatus, RegistryPin
from .gcp_journal import Fence, UnavailableFence
from .gcp_pairing import _checked_event, pairing_control_record
from .gcp_password_lifetime_contracts import (
    AdvancePasswordAuthGeneration,
    BindPasswordAccount,
    CreatePasswordWebSession,
    DeletePasswordSubject,
    LifetimeRecord,
    PasswordLifetimeCommand,
    PasswordLifetimeEffects,
    PasswordLifetimeIntent,
    RegisteredSubject,
    RevokePasswordWebSession,
    TombstonePasswordAccount,
)
from .gcp_password_lifetime_journal import (
    PasswordLifetimeJournal,
    UnavailablePasswordLifetimeJournal,
)
from .gcp_service import control_record
from .pairing_contracts import JS_SAFE_MAX
from .password_reauth import CandidateWebSession, PasswordAccountBinding, RetainedWebSession
from .store import GuardDenied, GuardUnavailable

_COMMAND = TypeAdapter(PasswordLifetimeCommand)
_SESSION_MS = 86_400_000
_COMMAND_TYPES = (BindPasswordAccount, CreatePasswordWebSession, RevokePasswordWebSession,
                  AdvancePasswordAuthGeneration, TombstonePasswordAccount, DeletePasswordSubject)


def _same(left: dict | None, right: dict | None) -> bool:
    return (left is None and right is None) or (left is not None and right is not None
            and canonical(left) == canonical(right))


@dataclass(frozen=True)
class PasswordLifetimePlan:
    intent: PasswordLifetimeIntent
    _issuer: object = field(repr=False, compare=False)


@dataclass(frozen=True)
class _OwnedLifetimePlan:
    export: PasswordLifetimePlan = field(repr=False, compare=False)
    raw: bytes = field(repr=False)
    sha256: str
    attempted: bool = False


def _intent_raw(intent: PasswordLifetimeIntent) -> bytes:
    try:
        raw = canonical(intent.model_dump(mode="json")).encode()
        if not 0 < len(raw) <= 65_536:
            raise ValueError("Intent byte bound")
        return raw
    except (ValueError, TypeError, UnicodeError):
        raise GuardDenied("Lifetime intent is not exact bounded canonical data") from None


def _intent_copy(raw: bytes) -> PasswordLifetimeIntent:
    try:
        copied = PasswordLifetimeIntent.model_validate_json(raw)
        if _intent_raw(copied) != raw:
            raise ValueError("Intent canonical mismatch")
        return copied
    except (ValidationError, ValueError, TypeError):
        raise GuardDenied("Lifetime intent is not an exact typed snapshot") from None


@dataclass(frozen=True)
class PasswordLifetimeExecution:
    status: OperationStatus
    context: CandidateWebSession | None = None


class _Snapshot:
    def __init__(self, tx: BufferedTransaction):
        self.tx = tx
        self.records: dict[tuple[str, str], dict | None] = {}

    def get(self, namespace: str, key: str) -> dict | None:
        value = self.tx.get(namespace, key)
        self.records[namespace, key] = value
        return value

    def absent(self, namespace: str, key: str) -> None:
        if self.get(namespace, key) is not None:
            raise GuardDenied("Lifetime identity has already been retained")

    def event(self, event_id: str, kind: str, payload: dict) -> dict:
        event = _checked_event(self.get("events", event_id))
        head = self.records.get(("head", "global"))
        if (head is None or event["sequence"] > head["sequence"]
                or (event["sequence"] == head["sequence"] and event["digest"] != head["digest"])):
            raise GuardDenied("Registration evidence is ahead of the retained event cut")
        if event["event_id"] != event_id or event["kind"] != kind or not _same(event["payload"], payload):
            raise GuardDenied("Independent lifetime registration provenance disagrees")
        return event


class GcpPasswordLifetimeCoordinator:
    """Server-created commands only; no arbitrary callbacks or namespace grants."""

    def __init__(self, registry: BufferedRegistry, pin: RegistryPin, *, epoch_generation: int,
                 journal: PasswordLifetimeJournal | None = None, fence: Fence | None = None,
                 now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000):
        if registry.rpc.database_resource != pin.database:
            raise GuardDenied("Lifetime writer database pin disagrees")
        pairing_control_record(pin, epoch_generation)
        self.registry, self.pin, self.epoch_generation = registry, pin, epoch_generation
        self.journal, self.fence = journal or UnavailablePasswordLifetimeJournal(), fence or UnavailableFence()
        self.now_ms = now_ms
        self._issuer = object()
        self._issued: dict[UUID, _OwnedLifetimePlan] = {}
        self._lock = Lock()

    def _fresh(self, intent: PasswordLifetimeIntent) -> None:
        now = self.now_ms()
        if (type(now) is not int or intent.pin != self.pin or intent.epoch_generation != self.epoch_generation
                or not intent.created_at_ms <= now < intent.deadline_ms):
            raise GuardDenied("Lifetime plan is outside its exact pinned interval")

    def _checked(self, raw: bytes, *, owned: _OwnedLifetimePlan | None = None,
                 source: PasswordLifetimeIntent | None = None) -> None:
        if owned is not None and (owned.raw != raw or hashlib.sha256(raw).hexdigest() != owned.sha256
                or _intent_raw(owned.export.intent) != raw):
            raise GuardDenied("Issued lifetime bytes changed during execution")
        if source is not None and _intent_raw(source) != raw:
            raise GuardDenied("Lifetime status input changed during verification")

    def _fenced(self, raw: bytes, *, owned: _OwnedLifetimePlan | None = None,
                source: PasswordLifetimeIntent | None = None) -> None:
        self._checked(raw, owned=owned, source=source)
        self.fence.check(self.pin.model_copy(deep=True))
        self._checked(raw, owned=owned, source=source)

    def _verify(self, raw: bytes, receipt: JournalReceipt, *, owned: _OwnedLifetimePlan | None = None,
                source: PasswordLifetimeIntent | None = None) -> None:
        self._checked(raw, owned=owned, source=source)
        exported = _intent_copy(raw)  # Ports never receive the execution/status authority object.
        try:
            self.journal.verify(exported, receipt.model_copy(deep=True))
        finally:
            if _intent_raw(exported) != raw:
                raise GuardDenied("Journal verification changed its detached lifetime export")
            self._checked(raw, owned=owned, source=source)

    def _before(self, raw: bytes, receipt: JournalReceipt | None = None,
                *, owned: _OwnedLifetimePlan | None = None) -> None:
        self._checked(raw, owned=owned)
        self._fresh(_intent_copy(raw))
        self._fenced(raw, owned=owned)
        if receipt is not None:
            self._verify(raw, receipt, owned=owned)
            self._fenced(raw, owned=owned)
        self._fresh(_intent_copy(raw))
        self._checked(raw, owned=owned)

    def _control(self, snap: _Snapshot) -> None:
        if (not _same(snap.get("control", "meta"), control_record(self.pin))
                or not _same(snap.get("pairing_control", "current"), pairing_control_record(self.pin, self.epoch_generation))):
            raise GuardUnavailable("Lifetime writer control is absent or closed")

    @staticmethod
    def _owner(binding: PasswordAccountBinding) -> dict:
        return binding.model_dump(mode="json", exclude={"registration_event_id", "principal_sha256"})

    def _subject(self, snap: _Snapshot, subject_id: UUID, *, initial: bool = False) -> RegisteredSubject:
        raw = snap.get("subjects", str(subject_id))
        if raw is None:
            raise GuardDenied("An independently registered subject is required")
        subject = RegisteredSubject.model_validate(raw)
        scope = Revocation(subject_uuid=subject_id, kind="subject", target=str(subject_id), revision=0)
        if subject.active is not True or snap.get("revocations", scope.key) is not None:
            raise GuardDenied("Subject lifetime is permanently denied")
        snap.event(str(subject.registration_event_id), "SUBJECT_REGISTERED", {"subject_uuid": str(subject_id)})
        floor = snap.get("pairing_auth_high_water", str(subject_id))
        expected = {"subject_uuid": str(subject_id), "auth_generation": subject.auth_generation,
                    "principal_sha256": subject.principal_sha256}
        if ((initial and floor is not None) or (not initial and not _same(floor, expected))):
            raise GuardDenied("Subject authentication lifetime is incomplete or stale")
        return subject

    def _binding(self, snap: _Snapshot, binding_id: UUID) -> tuple[PasswordAccountBinding, RegisteredSubject]:
        raw = snap.get("pairing_account_bindings", str(binding_id))
        if raw is None or snap.get("pairing_account_tombstones", str(binding_id)) is not None:
            raise GuardDenied("Password account lifetime is absent or permanently denied")
        binding = PasswordAccountBinding.model_validate(raw)
        if binding.account_binding_id != binding_id:
            raise GuardDenied("Password binding identity disagrees")
        snap.event(str(binding.registration_event_id), "PASSWORD_ACCOUNT_BOUND",
                   binding.model_dump(mode="json", exclude={"registration_event_id"}))
        owner = self._owner(binding)
        if (not _same(snap.get("pairing_subject_accounts", str(binding.subject_uuid)), owner)
                or not _same(snap.get("pairing_sql_accounts", fingerprint({"candidate_id": binding.candidate_id})), owner)):
            raise GuardDenied("Immutable account/subject ownership is incomplete")
        subject = self._subject(snap, binding.subject_uuid)
        if subject.principal_sha256 != binding.principal_sha256:
            raise GuardDenied("Password account principal disagrees")
        return binding, subject

    def _prepare(self, tx: BufferedTransaction, command: PasswordLifetimeCommand, *,
                 operation_id: UUID, event_id: UUID, now: int) -> tuple[UUID, PasswordLifetimeEffects]:
        snap = _Snapshot(tx)
        self._control(snap)
        clock = snap.get("pairing_clock", "observed")
        if (clock is None or set(clock) != {"now_ms"} or type(clock["now_ms"]) is not int
                or not 0 < clock["now_ms"] <= now <= JS_SAFE_MAX):
            raise GuardUnavailable("Operator monotonic lifetime clock is missing or ahead")
        head = snap.get("head", "global")
        if (head is None or set(head) != {"sequence", "digest"} or type(head["sequence"]) is not int
                or not 0 <= head["sequence"] < JS_SAFE_MAX or type(head["digest"]) is not str
                or len(head["digest"]) != 64 or any(c not in "0123456789abcdef" for c in head["digest"])):
            raise GuardUnavailable("Complete operator event cut is required")
        snap.absent("password_lifetime_operations", str(operation_id))
        snap.absent("events", str(event_id))
        changes: list[LifetimeRecord] = []
        def change(namespace: str, key: str, value: dict) -> None:
            snap.get(namespace, key)
            changes.append(LifetimeRecord(namespace=namespace, key=key, value=value))
        if isinstance(command, BindPasswordAccount):
            subject_id = command.subject_uuid
            subject = self._subject(snap, subject_id, initial=True)
            if subject.principal_sha256 != command.principal_sha256:
                raise GuardDenied("Trusted account plan does not match the registered principal")
            binding = PasswordAccountBinding(account_binding_id=command.account_binding_id,
                candidate_id=command.candidate_id, subject_uuid=subject_id,
                principal_sha256=command.principal_sha256, registration_event_id=event_id)
            for namespace, key in (("pairing_account_bindings", str(command.account_binding_id)),
                    ("pairing_account_tombstones", str(command.account_binding_id)),
                    ("pairing_subject_accounts", str(subject_id)),
                    ("pairing_sql_accounts", fingerprint({"candidate_id": command.candidate_id}))):
                snap.absent(namespace, key)
            change("pairing_account_bindings", str(command.account_binding_id), binding.model_dump(mode="json"))
            owner = self._owner(binding)
            change("pairing_subject_accounts", str(subject_id), owner)
            change("pairing_sql_accounts", fingerprint({"candidate_id": command.candidate_id}), owner)
            change("pairing_auth_high_water", str(subject_id), {"subject_uuid": str(subject_id),
                "auth_generation": subject.auth_generation, "principal_sha256": subject.principal_sha256})
            kind = "PASSWORD_ACCOUNT_BOUND"
            payload = binding.model_dump(mode="json", exclude={"registration_event_id"})
        else:
            binding, subject = self._binding(snap, command.account_binding_id)
            subject_id = binding.subject_uuid
            if isinstance(command, CreatePasswordWebSession):
                if command.expected_auth_generation != subject.auth_generation:
                    raise GuardDenied("New session belongs to a stale authentication generation")
                if not now < command.expires_at_ms <= now + _SESSION_MS:
                    raise GuardDenied("Web-session lifetime must be within one day")
                for namespace in ("pairing_web_sessions", "pairing_sessions", "pairing_web_session_tombstones"):
                    snap.absent(namespace, str(command.session_id))
                session = RetainedWebSession(session_id=command.session_id, account_binding_id=command.account_binding_id,
                    subject_uuid=subject_id, principal_sha256=binding.principal_sha256,
                    auth_generation=subject.auth_generation, registration_event_id=event_id,
                    issued_at_ms=now, expires_at_ms=command.expires_at_ms, active=True)
                change("pairing_web_sessions", str(command.session_id), session.model_dump(mode="json"))
                change("pairing_sessions", str(command.session_id), {"subject_uuid": str(subject_id),
                    "principal_sha256": binding.principal_sha256, "auth_generation": subject.auth_generation, "active": True})
                kind = "PASSWORD_WEB_SESSION_CREATED"
                payload = session.model_dump(mode="json", exclude={"registration_event_id"})
            elif isinstance(command, RevokePasswordWebSession):
                raw = snap.get("pairing_web_sessions", str(command.session_id))
                if raw is None:
                    raise GuardDenied("Only an existing retained session can be revoked")
                session = RetainedWebSession.model_validate(raw)
                if (session.session_id != command.session_id or session.account_binding_id != binding.account_binding_id
                        or session.subject_uuid != subject_id or session.principal_sha256 != binding.principal_sha256):
                    raise GuardDenied("Session revocation belongs to another account")
                snap.event(str(session.registration_event_id), "PASSWORD_WEB_SESSION_CREATED",
                           session.model_dump(mode="json", exclude={"registration_event_id"}))
                snap.absent("pairing_web_session_tombstones", str(command.session_id))
                projection = {"subject_uuid": str(subject_id), "principal_sha256": binding.principal_sha256,
                    "auth_generation": session.auth_generation, "active": True}
                if not _same(snap.get("pairing_sessions", str(command.session_id)), projection):
                    raise GuardDenied("Session projection is incomplete or already revoked")
                change("pairing_sessions", str(command.session_id), {**projection, "active": False})
                payload = {"session_id": str(command.session_id), "account_binding_id": str(binding.account_binding_id),
                           "subject_uuid": str(subject_id), "operation_id": str(operation_id), "event_id": str(event_id)}
                change("pairing_web_session_tombstones", str(command.session_id), payload)
                kind = "PASSWORD_WEB_SESSION_REVOKED"
            elif command.kind == "advance_auth_generation":
                if command.expected_auth_generation != subject.auth_generation or subject.auth_generation == JS_SAFE_MAX:
                    raise GuardDenied("Authentication generation is stale or exhausted")
                generation = subject.auth_generation + 1
                change("subjects", str(subject_id), {**subject.model_dump(mode="json"), "auth_generation": generation})
                payload = {"subject_uuid": str(subject_id), "auth_generation": generation,
                           "principal_sha256": subject.principal_sha256}
                change("pairing_auth_high_water", str(subject_id), payload)
                kind = "PASSWORD_AUTH_GENERATION_ADVANCED"
            else:
                if isinstance(command, DeletePasswordSubject) and command.subject_uuid != subject_id:
                    raise GuardDenied("Subject deletion belongs to another account")
                payload = {"account_binding_id": str(binding.account_binding_id), "subject_uuid": str(subject_id),
                           "operation_id": str(operation_id), "event_id": str(event_id)}
                change("pairing_account_tombstones", str(binding.account_binding_id), payload)
                change("subjects", str(subject_id), {**subject.model_dump(mode="json"), "active": False})
                revocation = Revocation(subject_uuid=subject_id, kind="subject", target=str(subject_id), revision=0)
                change("revocations", revocation.key, revocation.model_dump(mode="json"))
                kind = "PASSWORD_SUBJECT_DELETED" if isinstance(command, DeletePasswordSubject) else "PASSWORD_ACCOUNT_TOMBSTONED"
        change("pairing_clock", "observed", {"now_ms": now})
        event = {"sequence": head["sequence"] + 1, "previous": head["digest"], "kind": kind,
                 "payload": payload, "event_id": str(event_id)}
        event["digest"] = hashlib.sha256(canonical(event).encode()).hexdigest()
        changes += [LifetimeRecord(namespace="events", key=str(event_id), value=event),
                    LifetimeRecord(namespace="head", key="global", value={"sequence": event["sequence"], "digest": event["digest"]})]
        before = tuple(LifetimeRecord(namespace=namespace, key=key, value=value)
                       for (namespace, key), value in sorted(snap.records.items()))
        return subject_id, PasswordLifetimeEffects(before=before, after=tuple(changes), event=event)

    def allocate(self, command: PasswordLifetimeCommand, *, lifetime_ms: int = 60_000) -> PasswordLifetimePlan:
        if type(command) not in _COMMAND_TYPES:
            raise GuardDenied("Only a typed trusted server lifetime command is accepted")
        command = _COMMAND.validate_python(command.model_dump(mode="json"))
        now = self.now_ms()
        if (type(now) is not int or not 0 < now <= JS_SAFE_MAX or type(lifetime_ms) is not int
                or not 0 < lifetime_ms <= 60_000 or now + lifetime_ms > JS_SAFE_MAX):
            raise GuardDenied("Lifetime allocation clock or interval is invalid")
        operation_id, event_id = uuid4(), uuid4()
        captured: list[tuple[UUID, PasswordLifetimeEffects]] = []
        def read(tx: BufferedTransaction) -> None:
            captured[:] = [self._prepare(tx, command, operation_id=operation_id, event_id=event_id, now=now)]
        self.registry.run(read, before_attempt=lambda: self.fence.check(self.pin))
        self.fence.check(self.pin)
        subject_id, effects = captured[0]
        intent = PasswordLifetimeIntent(operation_id=operation_id, event_id=event_id, pin=self.pin,
            epoch_generation=self.epoch_generation, command=command, subject_uuid=subject_id,
            effects=effects, created_at_ms=now, deadline_ms=now + lifetime_ms)
        raw = _intent_raw(intent)
        plan = PasswordLifetimePlan(_intent_copy(raw), self._issuer)
        with self._lock:
            self._issued[operation_id] = _OwnedLifetimePlan(plan, raw, hashlib.sha256(raw).hexdigest())
        return plan

    def execute(self, plan: PasswordLifetimePlan) -> PasswordLifetimeExecution:
        if not isinstance(plan, PasswordLifetimePlan):
            raise GuardDenied("A server-issued lifetime plan is required")
        with self._lock:
            owned = self._issued.get(plan.intent.operation_id)
            if (owned is None or owned.export is not plan or plan._issuer is not self._issuer
                    or _intent_raw(plan.intent) != owned.raw):
                raise GuardDenied("Lifetime plan cannot be forged, changed or adopted")
            repeated = owned.attempted
            if not repeated:
                owned = replace(owned, attempted=True)
                self._issued[plan.intent.operation_id] = owned
        raw = owned.raw
        intent = _intent_copy(raw)  # Private working snapshot, never passed to an IO port.
        if repeated:
            return PasswordLifetimeExecution(self._status(raw, owned=owned))
        self._before(raw, owned=owned)
        exported = _intent_copy(raw)
        receipt = self.journal.create(exported)
        if _intent_raw(exported) != raw:
            raise GuardDenied("Journal creation changed its detached lifetime export")
        self._checked(raw, owned=owned)
        if receipt is None:
            return PasswordLifetimeExecution(self._unknown(intent))
        receipt = JournalReceipt.model_validate_json(canonical(receipt.model_dump(mode="json")))
        self._before(raw, receipt, owned=owned)
        def stage(tx: BufferedTransaction) -> None:
            # Every attempt reparses the fixed coordinator-owned canonical bytes.
            attempt = _intent_copy(raw)
            self._checked(raw, owned=owned)
            self._fresh(attempt)
            subject_id, effects = self._prepare(tx, attempt.command, operation_id=attempt.operation_id,
                                               event_id=attempt.event_id, now=attempt.created_at_ms)
            self._checked(raw, owned=owned)
            if (subject_id != attempt.subject_uuid
                    or canonical(effects.model_dump(mode="json")) != canonical(attempt.effects.model_dump(mode="json"))):
                raise GuardDenied("Lifetime snapshot changed; this protected plan cannot retarget")
            for record in effects.after:
                if record.namespace not in {"events", "head"}:
                    assert record.value is not None
                    tx.put(record.namespace, record.key, record.value,
                        immutable=record.namespace in {"pairing_account_bindings", "pairing_subject_accounts",
                            "pairing_sql_accounts", "pairing_web_sessions", "pairing_account_tombstones",
                            "pairing_web_session_tombstones", "revocations"})
            event = tx.append(str(attempt.event_id), effects.event["kind"], effects.event["payload"])
            if not _same(event, attempt.effects.event):
                raise GuardDenied("Lifetime event cut changed")
            tx.put("password_lifetime_effects", str(attempt.operation_id), attempt.effects.model_dump(mode="json"), immutable=True)
            tx.put("password_lifetime_operations", str(attempt.operation_id), {"intent_sha256": owned.sha256,
                "journal": receipt.model_dump(mode="json"), "event_id": str(attempt.event_id)}, immutable=True)
            self._fresh(attempt)
            self._checked(raw, owned=owned)
        try:
            self.registry.run(stage, before_attempt=lambda: self._before(raw, receipt, owned=owned))
        except AmbiguousCommit:
            return PasswordLifetimeExecution(self._unknown(intent))
        except GuardUnavailable:
            return PasswordLifetimeExecution(self._unknown(intent))
        try:
            self._checked(raw, owned=owned)
            self._before(raw, receipt, owned=owned)
        except (GuardDenied, GuardUnavailable):
            # Any post-Commit export/fence/time loss withholds context, using the original digest.
            return PasswordLifetimeExecution(self._unknown(intent))
        status = OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                                 intent_sha256=owned.sha256, journal=receipt)
        context = None
        if isinstance(intent.command, CreatePasswordWebSession):
            binding = next(item.value for item in intent.effects.before
                if item.namespace == "pairing_account_bindings" and item.key == str(intent.command.account_binding_id))
            assert binding is not None
            context = CandidateWebSession(candidate_id=binding["candidate_id"],
                account_binding_id=intent.command.account_binding_id, session_id=intent.command.session_id)
        self._checked(raw, owned=owned)
        return PasswordLifetimeExecution(status, context)

    def status(self, intent: PasswordLifetimeIntent) -> OperationStatus:
        raw = _intent_raw(intent)
        return self._status(raw, source=intent)

    def _status(self, raw: bytes, *, owned: _OwnedLifetimePlan | None = None,
                source: PasswordLifetimeIntent | None = None) -> OperationStatus:
        intent = _intent_copy(raw)
        if intent.pin != self.pin or intent.epoch_generation != self.epoch_generation:
            raise GuardDenied("Lifetime status belongs to another authority")
        try:
            self._fenced(raw, owned=owned, source=source)
            key = str(intent.operation_id)
            records = self.registry.read_many((("control", "meta"), ("pairing_control", "current"),
                ("password_lifetime_operations", key), ("password_lifetime_effects", key),
                ("events", str(intent.event_id)), ("head", "global")))
            self._checked(raw, owned=owned, source=source)
            if (not _same(records["control", "meta"], control_record(self.pin))
                    or not _same(records["pairing_control", "current"], pairing_control_record(self.pin, self.epoch_generation))):
                raise GuardUnavailable("Lifetime status control is closed")
            row = records["password_lifetime_operations", key]
            if (row is None or set(row) != {"intent_sha256", "journal", "event_id"}
                    or row["intent_sha256"] != hashlib.sha256(raw).hexdigest() or row["event_id"] != str(intent.event_id)
                    or not _same(records["password_lifetime_effects", key], intent.effects.model_dump(mode="json"))
                    or not _same(records["events", str(intent.event_id)], intent.effects.event)):
                raise GuardUnavailable("Lifetime operation lacks complete retained evidence")
            event = _checked_event(records["events", str(intent.event_id)])
            head = records["head", "global"]
            if (head is None or set(head) != {"sequence", "digest"} or type(head.get("sequence")) is not int
                    or not event["sequence"] <= head["sequence"] <= JS_SAFE_MAX
                    or type(head.get("digest")) is not str or len(head["digest"]) != 64
                    or any(char not in "0123456789abcdef" for char in head["digest"])
                    or (head["sequence"] == event["sequence"] and head.get("digest") != event["digest"])):
                raise GuardUnavailable("Lifetime event is ahead of the retained cut")
            receipt = JournalReceipt.model_validate(row["journal"])
            self._verify(raw, receipt, owned=owned, source=source)
            self._fenced(raw, owned=owned, source=source)
            self._checked(raw, owned=owned, source=source)
            return OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                                   intent_sha256=hashlib.sha256(raw).hexdigest(), journal=receipt)
        except (GuardDenied, GuardUnavailable, ValidationError):
            return self._unknown(intent)

    @staticmethod
    def _unknown(intent: PasswordLifetimeIntent) -> OperationStatus:
        return OperationStatus(status="UNKNOWN", operation_id=intent.operation_id, intent_sha256=intent.digest)
