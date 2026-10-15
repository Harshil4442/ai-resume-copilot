"""Conservative journal-backed status primitives, not action/pairing authority."""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable

from pydantic import ValidationError

from .contracts import canonical
from .gcp_buffer import BufferedRegistry, BufferedTransaction
from .gcp_contracts import (
    AmbiguousCommit,
    JournalIntent,
    JournalReceipt,
    OperationStatus,
    RegistryPin,
)
from .gcp_journal import Fence, GcsJournal, UnavailableFence
from .store import GuardDenied, GuardUnavailable


def control_record(pin: RegistryPin) -> dict:
    # An operator initializes this record; no runtime bootstrap/OPEN repair exists.
    return {**pin.model_dump(mode="json"), "state": "OPEN"}


class SafetyCoordinator:
    def __init__(self, registry: BufferedRegistry, journal: GcsJournal, pin: RegistryPin,
                 *, fence: Fence | None = None,
                 now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000) -> None:
        if registry.rpc.database_resource != pin.database:
            raise GuardDenied("Registry transport/database pin mismatch")
        self.registry, self.journal, self.pin = registry, journal, pin
        self.fence, self.now_ms = fence or UnavailableFence(), now_ms

    def _check_intent(self, intent: JournalIntent) -> None:
        if intent.pin != self.pin:
            raise GuardDenied("Intent does not belong to the pinned authority")

    def _fresh(self, intent: JournalIntent) -> None:
        now = self.now_ms()
        if type(now) is not int or not intent.created_at_ms <= now < intent.deadline_ms:
            raise GuardDenied("Intent is outside its bounded operation lifetime")

    def _control_matches(self, value: dict | None) -> bool:
        return value is not None and canonical(value) == canonical(control_record(self.pin))

    @staticmethod
    def _record(intent: JournalIntent, receipt: JournalReceipt) -> dict:
        return {"intent_sha256": intent.digest, "journal": receipt.model_dump(mode="json")}

    @staticmethod
    def _effect(intent: JournalIntent, receipt: JournalReceipt) -> dict:
        return {"effect": intent.effect.model_dump(mode="json"),
                "operation_id": str(intent.operation_id), "journal": receipt.model_dump(mode="json")}

    @staticmethod
    def _event_payload(intent: JournalIntent, receipt: JournalReceipt) -> dict:
        return {"operation_id": str(intent.operation_id), "intent_sha256": intent.digest,
                "journal_sha256": receipt.sha256, "journal_generation": receipt.generation}

    def _projection(self, intent: JournalIntent, receipt: JournalReceipt,
                    effect: dict | None, event: dict | None, head: dict | None) -> None:
        if effect is None or canonical(effect) != canonical(self._effect(intent, receipt)):
            raise GuardUnavailable("Operation lacks its immutable safety effect")
        if event is None or set(event) != {"sequence", "previous", "kind", "payload", "event_id", "digest"}:
            raise GuardUnavailable("Operation lacks its exact event")
        signed = {key: value for key, value in event.items() if key != "digest"}
        if (event["kind"] != intent.effect.kind or event["event_id"] != str(intent.operation_id)
                or canonical(event["payload"]) != canonical(self._event_payload(intent, receipt))
                or type(event["sequence"]) is not int or not 0 < event["sequence"] <= 2**53 - 1
                or not isinstance(event["previous"], str)
                or re.fullmatch(r"[a-f0-9]{64}", event["previous"]) is None
                or hashlib.sha256(canonical(signed).encode()).hexdigest() != event["digest"]):
            raise GuardUnavailable("Operation event projection disagrees")
        if (head is None or set(head) != {"sequence", "digest"}
                or type(head.get("sequence")) is not int or head["sequence"] > 2**53 - 1
                or not isinstance(head.get("digest"), str)
                or re.fullmatch(r"[a-f0-9]{64}", head["digest"]) is None
                or head["sequence"] < event["sequence"]
                or (head["sequence"] == event["sequence"] and head.get("digest") != event["digest"])):
            raise GuardUnavailable("Operation event is ahead of the retained head")

    def execute(self, intent: JournalIntent) -> OperationStatus:
        self._check_intent(intent)
        self._fresh(intent)
        self.fence.check(self.pin)
        receipt = self.journal.write(intent)  # No transaction or Firestore lock spans GCS.

        def stage(tx: BufferedTransaction) -> None:
            self._fresh(intent)  # Repeated from fresh time on each definite abort.
            if not self._control_matches(tx.get("control", "meta")):
                raise GuardUnavailable("Current registry control is closed or mismatched")
            key = str(intent.operation_id)
            record = self._record(intent, receipt)
            existing = tx.get("operations", key)
            if existing is not None:
                if canonical(existing) != canonical(record):
                    raise GuardDenied("Stable operation identity conflict")
                self._projection(intent, receipt, tx.get(intent.effect.kind, intent.effect.record_key),
                                 tx.event(key), tx.get("head", "global"))
                self._fresh(intent)
                return
            effect = intent.effect
            known_effect = tx.get(effect.kind, effect.record_key)
            if (tx.event(key) is not None
                    or (known_effect is not None and known_effect.get("operation_id") == key)):
                raise GuardUnavailable("Incomplete operation projection requires closed recovery")
            # Permanent original operation/receipt prevents a fresh operation or
            # epoch from quietly rewriting opening/key ownership or a tombstone.
            tx.put(effect.kind, effect.record_key, self._effect(intent, receipt), immutable=True)
            tx.append(key, effect.kind, self._event_payload(intent, receipt))
            tx.put("operations", key, record, immutable=True)
            self._projection(intent, receipt, tx.get(effect.kind, effect.record_key),
                             tx.event(key), tx.get("head", "global"))
            self._fresh(intent)

        try:
            self.registry.run(stage)
        except AmbiguousCommit:
            return self._unknown(intent)
        try:
            self.fence.check(self.pin)
        except GuardUnavailable:
            return self._unknown(intent)
        return OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                               intent_sha256=intent.digest, journal=receipt)

    def status(self, intent: JournalIntent) -> OperationStatus:
        """No mutation, lifetime refresh or action result; missing stays UNKNOWN."""
        self._check_intent(intent)
        try:
            self.fence.check(self.pin)
            keys = (("control", "meta"), ("operations", str(intent.operation_id)),
                    (intent.effect.kind, intent.effect.record_key),
                    ("events", str(intent.operation_id)), ("head", "global"))
            records = self.registry.read_many(keys)
            if not self._control_matches(records["control", "meta"]):
                return self._unknown(intent)
            record = records["operations", str(intent.operation_id)]
            if record is None:
                return self._unknown(intent)
            if set(record) != {"intent_sha256", "journal"} or record["intent_sha256"] != intent.digest:
                raise GuardDenied("Operation receipt identity conflict")
            receipt = JournalReceipt.model_validate(record["journal"])
            if receipt.sha256 != intent.digest:
                raise GuardUnavailable("Stored journal digest mismatch")
            self._projection(intent, receipt, records[intent.effect.kind, intent.effect.record_key],
                             records["events", str(intent.operation_id)], records["head", "global"])
            self.journal.verify(intent, receipt)
            self.fence.check(self.pin)
        except (GuardUnavailable, ValidationError):
            return self._unknown(intent)
        return OperationStatus(status="COMMITTED", operation_id=intent.operation_id,
                               intent_sha256=intent.digest, journal=receipt)

    @staticmethod
    def _unknown(intent: JournalIntent) -> OperationStatus:
        return OperationStatus(status="UNKNOWN", operation_id=intent.operation_id,
                               intent_sha256=intent.digest)
