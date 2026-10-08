"""Injected identity-only native bridge; no production factory or portal routes.

The real PairingService owns lifecycle/crypto rules. Only whitelisted commands
can enter this scoped store. Receipt/status replay never reconstructs nonce or
claim output. IAM, KMS, authentication, lifetime and restore policy remain
external unmet deployment requirements; synthetic adapters do not prove them.
"""
from __future__ import annotations

import contextvars
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedRegistry, BufferedTransaction
from .gcp_contracts import AmbiguousCommit, JournalReceipt, OperationStatus, RegistryPin
from .gcp_journal import Fence, GcsJournal, UnavailableFence
from .gcp_pairing_contracts import (
    ID_COUNTS,
    NONCE_POSITIONS,
    PairingCommand,
    PairingExecution,
    PairingJournalIntent,
)
from .gcp_service import control_record
from .pairing_auth import AssertionVerifier, ClaimIssuer, UnavailableClaims
from .pairing_contracts import JS_SAFE_MAX, canonical_bytes, digest, nonce_digest
from .pairing_service import PairingService
from .store import GuardDenied, GuardUnavailable, Transaction

_PARAMETERS = {
    "prepare_request": {"key", "extension_id", "revision"},
    "create_request": {"pairing_id", "nonce", "signature"},
    "candidate_challenge": {"pairing_id", "subject_id", "session_id"},
    "confirm_candidate": {"challenge_id", "nonce", "envelope"},
    "device_challenge": {"pairing_id"}, "complete_device": {"challenge_id", "nonce", "signature"},
    "refresh_challenge": {"device_id"}, "refresh_claim": {"challenge_id", "nonce", "signature"},
    "revocation_challenge": {"device_id", "subject_id", "session_id"},
    "revoke_device": {"challenge_id", "nonce", "envelope"},
}
_WRITES = {
    "prepare_request": {"pairing_requests", "pairing_device_requests", "pairing_challenges"},
    "create_request": {"pairing_requests", "pairing_challenges"},
    "candidate_challenge": {"pairing_challenges"},
    "confirm_candidate": {"pairing_requests", "pairing_challenges", "pairing_confirmations", "pairing_assertions"},
    "device_challenge": {"pairing_requests", "pairing_challenges"},
    "complete_device": {"devices", "pairing_key_owners", "pairing_requests", "pairing_challenges"},
    "refresh_challenge": {"pairing_challenges"}, "refresh_claim": {"pairing_challenges"},
    "revocation_challenge": {"pairing_challenges"},
    "revoke_device": {"devices", "pairing_requests", "pairing_challenges", "pairing_assertions",
                      "revocations", "pairing_device_tombstones"},
}
_EVENTS = {
    "prepare_request": "PAIRING_PREPARED", "create_request": "PAIRING_REQUESTED",
    "candidate_challenge": "PAIRING_CANDIDATE_CHALLENGE", "confirm_candidate": "PAIRING_CANDIDATE_CONFIRMED",
    "device_challenge": "PAIRING_DEVICE_CHALLENGE", "complete_device": "PAIRING_COMPLETED",
    "refresh_challenge": "PAIRING_REFRESH_CHALLENGE", "refresh_claim": "PAIRING_CLAIM_REFRESHED",
    "revocation_challenge": "PAIRING_CANDIDATE_CHALLENGE", "revoke_device": "PAIRING_DEVICE_REVOKED",
}
_READS = {"pairing_extensions", "subjects", "revocations", "pairing_sessions", "pairing_requests",
          "pairing_device_requests", "pairing_challenges", "pairing_assertions", "pairing_confirmations",
          "devices", "pairing_key_owners", "pairing_device_tombstones"}
_PERMANENT = {"pairing_device_requests", "pairing_assertions", "pairing_confirmations",
              "pairing_key_owners", "revocations", "pairing_device_tombstones"}


class _Replay(GuardUnavailable):
    pass


@dataclass
class _Invocation:
    intent: PairingJournalIntent
    receipt: JournalReceipt
    allocated_ids: tuple[UUID, ...] = field(repr=False)
    cursor: int = 0
    confirmed: bool = False


@dataclass(frozen=True)
class PairingCommandPlan:
    """Server-private retry memory. Never log/serialize/return the nonce plan.

    A process loss can reconcile only intent status, not reconstruct output.
    Public IDs and nonce commitments are the only allocation data journaled.
    """
    intent: PairingJournalIntent
    _allocated_ids: tuple[UUID, ...] = field(repr=False)
    _issuer: object | None = field(default=None, repr=False, compare=False)

    def validate(self) -> None:
        if (len(self._allocated_ids) != ID_COUNTS[self.intent.command]
                or any(not isinstance(item, UUID) for item in self._allocated_ids)):
            raise GuardDenied("Exact typed private pairing plan is required")
        positions = NONCE_POSITIONS.get(self.intent.command, ())
        public = tuple(item for position, item in enumerate(self._allocated_ids) if position not in positions)
        commitments = tuple(nonce_digest(str(self._allocated_ids[position])) for position in positions)
        if (len(set(self._allocated_ids)) != len(self._allocated_ids)
                or self.intent.operation_id in self._allocated_ids
                or public != self.intent.allocated_ids or commitments != self.intent.nonce_commitments):
            raise GuardDenied("Private pairing retry plan does not match its public commitments")


def pairing_control_record(pin: RegistryPin, epoch_generation: int) -> dict:
    if type(epoch_generation) is not int or not 0 < epoch_generation <= JS_SAFE_MAX:
        raise GuardDenied("An exact pinned pairing epoch generation is required")
    return {"pin": pin.model_dump(mode="json"), "protocol_version": 2,
            "generation": epoch_generation, "state": "OPEN"}


def _checked_event(value: dict | None) -> dict:
    if value is None or set(value) != {"sequence", "previous", "kind", "payload", "event_id", "digest"}:
        raise GuardUnavailable("Complete native event evidence is required")
    signed = {key: item for key, item in value.items() if key != "digest"}
    if (type(value["sequence"]) is not int or not 0 < value["sequence"] <= JS_SAFE_MAX
            or not isinstance(value["previous"], str) or len(value["previous"]) != 64
            or any(char not in "0123456789abcdef" for char in value["previous"])
            or value["digest"] != hashlib.sha256(canonical(signed).encode()).hexdigest()):
        raise GuardUnavailable("Native pairing event projection disagrees")
    return value


class _PairingTransaction:
    def __init__(self, tx: BufferedTransaction, coordinator: GcpPairingCoordinator,
                 command: PairingCommand | None):
        self.tx, self.coordinator, self.command = tx, coordinator, command
        self.changes: list[dict] = []
        self.appended: dict | None = None

    def get(self, namespace: str, key: str) -> dict | None:
        if (namespace, key) == ("control", "current"):
            self.coordinator._control(self.tx)
            pin = self.coordinator.pin
            return {"authority_id": str(pin.authority_id), "incarnation": pin.incarnation,
                    "epoch_id": str(pin.epoch_id), "generation": self.coordinator.epoch_generation, "status": "OPEN"}
        if (namespace, key) == ("clock", "observed"):
            value = self.tx.get("pairing_clock", "observed")
            if (value is None or set(value) != {"now_ms"} or type(value["now_ms"]) is not int
                    or not 0 < value["now_ms"] <= JS_SAFE_MAX):
                raise GuardUnavailable("An operator-initialized monotonic pairing clock is required")
            return value
        if namespace not in _READS:
            raise GuardDenied("Pairing command cannot read this namespace")
        return self.tx.get(namespace, key)

    def put(self, namespace: str, key: str, value: dict, *, immutable: bool = False) -> None:
        if self.command is None:
            raise GuardDenied("Pairing status cannot mutate authority")
        if (namespace, key) == ("clock", "observed"):
            prior = self.get(namespace, key)
            assert prior is not None
            if (set(value) != {"now_ms"} or type(value["now_ms"]) is not int
                    or not prior["now_ms"] <= value["now_ms"] <= JS_SAFE_MAX):
                raise GuardDenied("Pairing clock cannot move backward or bootstrap")
            namespace = "pairing_clock"
        elif namespace not in _WRITES[self.command]:
            raise GuardDenied("Pairing command cannot write this namespace")
        immutable = immutable or namespace in _PERMANENT
        self.tx.put(namespace, key, value, immutable=immutable)
        self.changes.append({"namespace": namespace, "key": key, "value": value})

    def event(self, event_id: str) -> dict | None:
        value = self.tx.event(event_id)
        return _checked_event(value) if value is not None else None

    def append(self, event_id: str, kind: str, payload: dict) -> dict:
        if (self.command is None or kind != _EVENTS[self.command] or self.appended is not None
                or self.tx.event(event_id) is not None):
            raise GuardDenied("Command requires its one fresh exact pairing event")
        self.appended = self.tx.append(event_id, kind, payload)
        return _checked_event(self.appended)

    def head(self) -> tuple[int, str]:
        return self.tx.head()


class _PairingStore:
    def __init__(self, coordinator: GcpPairingCoordinator):
        self.coordinator = coordinator

    def transact(self, operation: Callable[[Transaction], Any]) -> Any:
        coordinator = self.coordinator
        scope = coordinator._scope.get()
        if scope is None:
            if coordinator._reading.get():
                read_result: list[Any] = []
                def read(tx: BufferedTransaction) -> None:
                    coordinator._control(tx)
                    result = operation(_PairingTransaction(tx, coordinator, None))
                    if tx.overlay:
                        raise GuardDenied("Pairing status cannot mutate authority")
                    read_result[:] = [result]
                coordinator.registry.run(read, before_attempt=lambda: coordinator.fence.check(coordinator.pin))
                coordinator.fence.check(coordinator.pin)
                return read_result[0]
            raise GuardUnavailable("Pairing store is unavailable outside a restricted invocation")
        start_cursor = scope.cursor
        captured: list[Any] = []
        mutated: list[bool] = []

        def stage(tx: BufferedTransaction) -> None:
            # BufferedRegistry reruns stage only after definite ABORTED. No UUID
            # is minted by a retry; the current phase reuses its exact plan.
            scope.cursor = start_cursor
            coordinator._fresh(scope.intent)
            coordinator._control(tx)
            if tx.get("pairing_operations", str(scope.intent.operation_id)) is not None:
                raise _Replay("Existing pairing operation is status-only")
            wrapped = _PairingTransaction(tx, coordinator, scope.intent.command)
            result = operation(wrapped)
            wrote = bool(tx.overlay)
            if wrote:
                if wrapped.appended is None or scope.cursor != len(scope.allocated_ids):
                    raise GuardDenied("Pairing mutation lacks its complete stable command plan")
                projection = {"changes": wrapped.changes, "event": wrapped.appended}
                tx.put("pairing_projections", str(scope.intent.operation_id), projection, immutable=True)
                tx.put("pairing_operations", str(scope.intent.operation_id), {
                    "intent_sha256": scope.intent.digest, "journal": scope.receipt.model_dump(mode="json"),
                    "projection_sha256": fingerprint(projection), "event_id": wrapped.appended["event_id"],
                }, immutable=True)
            captured[:] = [result]
            mutated[:] = [wrote]
            coordinator._fresh(scope.intent)

        coordinator.registry.run(stage, before_attempt=lambda: coordinator._before(scope.intent))
        if mutated == [True]:
            scope.confirmed = True
        try:
            coordinator._before(scope.intent)
        except (GuardUnavailable, GuardDenied) as exc:
            raise AmbiguousCommit("Pairing output withheld after post-transaction fence failure") from exc
        return captured[0]


class _FencedClaims:
    def __init__(self, coordinator: GcpPairingCoordinator, target: ClaimIssuer):
        self.coordinator, self.target = coordinator, target

    def sign(self, payload: dict) -> dict:
        scope = self.coordinator._scope.get()
        if scope is None or not scope.confirmed:
            raise GuardUnavailable("Claim signing requires this command's confirmed native commit")
        self.coordinator._before(scope.intent)
        return self.target.sign(payload)


class GcpPairingCoordinator:
    def __init__(self, registry: BufferedRegistry, journal: GcsJournal, pin: RegistryPin, *,
                 epoch_generation: int, fence: Fence | None = None,
                 assertions: AssertionVerifier | None = None, claims: ClaimIssuer | None = None,
                 now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000):
        if registry.rpc.database_resource != pin.database:
            raise GuardDenied("Pairing transport/database pin mismatch")
        pairing_control_record(pin, epoch_generation)
        self.registry, self.journal, self.pin = registry, journal, pin
        self.epoch_generation, self.fence, self.now_ms = epoch_generation, fence or UnavailableFence(), now_ms
        self._issuer = object()
        self._plan_lock = Lock()
        self._issued: dict[UUID, tuple[str, tuple[UUID, ...], bool]] = {}
        self._scope: contextvars.ContextVar[_Invocation | None] = contextvars.ContextVar("gcp_pairing_invocation", default=None)
        self._reading: contextvars.ContextVar[bool] = contextvars.ContextVar("gcp_pairing_status", default=False)
        self.core = PairingService(_PairingStore(self), assertions=assertions,
            claims=_FencedClaims(self, claims if claims is not None else UnavailableClaims()),
            now_ms=now_ms, allocate_uuid=self._next_id)

    @staticmethod
    def _input(command: PairingCommand, parameters: dict) -> str:
        if set(parameters) != _PARAMETERS[command] or len(canonical_bytes("gcp-input", parameters)) > 65_536:
            raise GuardDenied("Exact bounded pairing command parameters are required")
        return digest("gcp-input", {"command": command, "parameters": parameters})

    def allocate(self, command: PairingCommand, parameters: dict) -> PairingCommandPlan:
        if command not in ID_COUNTS:
            raise GuardDenied("Unsupported native pairing command")
        parameters = self._snapshot(parameters)
        now = self.now_ms()
        ids = tuple(uuid4() for _ in range(ID_COUNTS[command]))
        positions = NONCE_POSITIONS.get(command, ())
        intent = PairingJournalIntent(operation_id=uuid4(), pin=self.pin, epoch_generation=self.epoch_generation,
            command=command, input_sha256=self._input(command, parameters),
            allocated_ids=tuple(item for position, item in enumerate(ids) if position not in positions),
            nonce_commitments=tuple(nonce_digest(str(ids[position])) for position in positions),
            created_at_ms=now, deadline_ms=now + 120_000)
        with self._plan_lock:
            self._issued[intent.operation_id] = (intent.digest, ids, False)
        return PairingCommandPlan(intent, ids, self._issuer)

    @staticmethod
    def _snapshot(parameters: dict) -> dict:
        """Bind one deep JSON snapshot, never a caller's mutable nested input."""
        if type(parameters) is not dict:
            raise GuardDenied("Pairing command requires exact dictionary inputs")
        try:
            raw = canonical(parameters)
            if len(raw.encode()) > 65_536:
                raise GuardDenied("Exact bounded pairing command parameters are required")
            return json.loads(raw)
        except (TypeError, ValueError) as exc:
            raise GuardDenied("Pairing command requires canonical JSON inputs") from exc

    def _attempted(self, plan: PairingCommandPlan, *, claim: bool = False) -> bool:
        """An issued operation is one-shot locally, including cloned plans.

        No IO runs under this lock. A new coordinator cannot deserialize or
        execute old plans; public intent status is the only restart interface.
        """
        with self._plan_lock:
            issued = self._issued.get(plan.intent.operation_id)
            if (plan._issuer is not self._issuer or issued is None
                    or issued[:2] != (plan.intent.digest, plan._allocated_ids)):
                raise GuardDenied("Pairing plan was not issued by this coordinator")
            if claim and not issued[2]:
                self._issued[plan.intent.operation_id] = (issued[0], issued[1], True)
            return issued[2]

    def _receipt_only(self, intent: PairingJournalIntent) -> PairingExecution:
        status = self.status(intent)
        return PairingExecution(status=status.status, operation_id=intent.operation_id,
                                intent_sha256=intent.digest, journal=status.journal)

    def _next_id(self) -> UUID:
        scope = self._scope.get()
        if scope is None or scope.cursor >= len(scope.allocated_ids):
            raise GuardDenied("Pairing command exhausted its preallocated identity plan")
        value = scope.allocated_ids[scope.cursor]
        scope.cursor += 1
        return value

    def _fresh(self, intent: PairingJournalIntent) -> None:
        now = self.now_ms()
        if type(now) is not int or not intent.created_at_ms <= now < intent.deadline_ms:
            raise GuardDenied("Pairing command is outside its bounded lifetime")

    def _before(self, intent: PairingJournalIntent) -> None:
        self._fresh(intent)
        self.fence.check(self.pin)

    def _controls_match(self, native: dict | None, pairing: dict | None) -> bool:
        return (native is not None and pairing is not None
                and canonical(native) == canonical(control_record(self.pin))
                and canonical(pairing) == canonical(pairing_control_record(self.pin, self.epoch_generation)))

    def _control(self, tx: BufferedTransaction) -> None:
        if not self._controls_match(tx.get("control", "meta"), tx.get("pairing_control", "current")):
            raise GuardUnavailable("Pinned native/pairing operator control is absent or closed")

    def execute(self, plan: PairingCommandPlan, parameters: dict) -> PairingExecution:
        if not isinstance(plan, PairingCommandPlan):
            raise GuardDenied("Execute requires the original server-private command plan")
        plan.validate()
        parameters = self._snapshot(parameters)
        intent = plan.intent
        if (intent.pin != self.pin or intent.epoch_generation != self.epoch_generation
                or intent.input_sha256 != self._input(intent.command, parameters)):
            raise GuardDenied("Pairing command does not match its immutable intent")
        if self._scope.get() is not None:
            raise GuardDenied("Pairing commands cannot nest or borrow another command's authority")
        if self._attempted(plan):
            return self._receipt_only(intent)
        self._before(intent)
        if self._attempted(plan, claim=True):
            return self._receipt_only(intent)
        receipt = self.journal.write(intent)  # No Firestore transaction spans GCS.
        scope = _Invocation(intent, receipt, plan._allocated_ids)
        token = self._scope.set(scope)
        try:
            result = getattr(self.core, intent.command)(**parameters)
            if not scope.confirmed:
                raise GuardUnavailable("Pairing command has no confirmed mutation receipt")
            self._before(intent)
            return PairingExecution(status="COMMITTED", operation_id=intent.operation_id,
                                    intent_sha256=intent.digest, journal=receipt, result=result)
        except _Replay:
            return self._receipt_only(intent)
        except AmbiguousCommit:
            return self._unknown(intent)
        except Exception:
            if scope.confirmed:
                return self._unknown(intent)
            raise
        finally:
            self._scope.reset(token)

    def status(self, intent: PairingJournalIntent) -> OperationStatus:
        """Read-only commit evidence. Never reconstruct challenge or identity output."""
        if intent.pin != self.pin or intent.epoch_generation != self.epoch_generation:
            raise GuardDenied("Pairing status belongs to another pinned authority")
        try:
            self.fence.check(self.pin)
            key = str(intent.operation_id)
            records = self.registry.read_many((("control", "meta"), ("pairing_control", "current"),
                ("pairing_operations", key), ("pairing_projections", key), ("head", "global")))
            if not self._controls_match(records["control", "meta"], records["pairing_control", "current"]):
                raise GuardUnavailable("Pairing status control is unavailable")
            row, projection = records["pairing_operations", key], records["pairing_projections", key]
            if row is None or projection is None:
                raise GuardUnavailable("Pairing operation has no complete projection")
            if (set(row) != {"intent_sha256", "journal", "projection_sha256", "event_id"}
                    or row["intent_sha256"] != intent.digest or fingerprint(projection) != row["projection_sha256"]
                    or set(projection) != {"changes", "event"}):
                raise GuardUnavailable("Pairing operation projection disagrees")
            receipt = JournalReceipt.model_validate(row["journal"])
            event = _checked_event(projection["event"])
            if (event["event_id"] != row["event_id"] or event["kind"] != _EVENTS[intent.command]
                    or self.registry.read("events", row["event_id"]) != event):
                raise GuardUnavailable("Pairing operation lacks its exact immutable event")
            head = records["head", "global"]
            if (head is None or set(head) != {"sequence", "digest"} or type(head["sequence"]) is not int
                    or not event["sequence"] <= head["sequence"] <= JS_SAFE_MAX
                    or (head["sequence"] == event["sequence"] and head["digest"] != event["digest"])):
                raise GuardUnavailable("Pairing event is ahead of retained head")
            self.journal.verify(intent, receipt)
            self.fence.check(self.pin)
            return OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                                   intent_sha256=intent.digest, journal=receipt)
        except (GuardUnavailable, ValidationError):
            return OperationStatus(status="UNKNOWN", operation_id=intent.operation_id, intent_sha256=intent.digest)

    def pairing_status(self, pairing_id: str) -> dict:
        """The existing core's redacted lifecycle status, through a read-only scope."""
        if self._scope.get() is not None or self._reading.get():
            raise GuardDenied("Pairing status cannot nest inside another command")
        token = self._reading.set(True)
        try:
            return self.core.status(pairing_id)
        finally:
            self._reading.reset(token)

    @staticmethod
    def _unknown(intent: PairingJournalIntent) -> PairingExecution:
        return PairingExecution(status="UNKNOWN", operation_id=intent.operation_id, intent_sha256=intent.digest)
