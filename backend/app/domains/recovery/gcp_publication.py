"""Disabled full-intent admission authority; no initializer, reopen, factory or ADC.

All mutation callbacks are private/pure. Storage and identity checks remain outside
both this protected native transaction and the ordinary execution/user transaction.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from uuid import uuid4

from google.cloud.storage import Bucket
from pydantic import ValidationError

from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedRegistry, BufferedTransaction
from .gcp_closure_contracts import AuthorityCloseReceipt, NativeClosedControl
from .gcp_contracts import AmbiguousCommit, JournalIntent
from .gcp_pairing_contracts import PairingJournalIntent
from .gcp_password_lifetime_contracts import PasswordLifetimeIntent
from .gcp_publication_contracts import (
    PublicationGate,
    PublicationPin,
    PublicationRecord,
    PublicationReference,
    PublicationResourceReceipt,
    publication_resource_path,
)
from .gcp_rpc import FirestoreRpc
from .store import GuardDenied, GuardUnavailable


def _same(value: dict | None, expected: dict) -> bool:
    return value is not None and canonical(value) == canonical(expected)


def record(raw: bytes, pin: PublicationPin) -> PublicationRecord:
    if type(raw) is not bytes or not 0 < len(raw) <= 65_536:
        raise GuardDenied("Publication intent bytes are not exact and bounded")
    typed = None
    for schema in (JournalIntent, PairingJournalIntent, PasswordLifetimeIntent):
        try:
            candidate = schema.model_validate_json(raw)
            if canonical(candidate.model_dump(mode="json")).encode() == raw:
                typed = candidate
                break
        except (ValidationError, ValueError, TypeError):
            continue
    if typed is None or typed.pin != pin.target:
        raise GuardDenied(
            "Publication requires a full canonical typed intent for its exact native pin"
        )
    authority = hashlib.sha256(str(pin.target.authority_id).encode()).hexdigest()
    kind = (
        "safety_effect"
        if isinstance(typed, JournalIntent)
        else "pairing_command"
        if isinstance(typed, PairingJournalIntent)
        else "password_lifetime_effects"
    )
    ref = PublicationReference(
        operation_id=typed.operation_id,
        sha256=hashlib.sha256(raw).hexdigest(),
        partition=typed.partition,
        path=f"authority-intents/{authority}/{typed.partition}/{typed.operation_id}.json",
        byte_size=len(raw),
        kind=kind,
    )
    return PublicationRecord(reference=ref, intent=json.loads(raw))


@dataclass(frozen=True)
class SealedPublicationCut:
    """Private frozen bytes; detached exports carry inventory evidence only."""

    _raw: bytes

    @property
    def gate(self) -> PublicationGate:
        return PublicationGate.model_validate_json(self._raw)


class GcpPublicationCoordinator:
    def __init__(
        self,
        registry: BufferedRegistry,
        resource: PublicationResourceReceipt,
        witness_bucket: Bucket,
        *,
        rpc_timeout: float = 2.0,
    ):
        if (
            type(rpc_timeout) not in {int, float}
            or not 0 < rpc_timeout <= 10
            or not math.isfinite(rpc_timeout)
        ):
            raise ValueError("Publication witness RPC timeout is invalid")
        if (
            type(registry.rpc) is not FirestoreRpc
            or registry.rpc.database_resource != resource.pin.registry.database
            or witness_bucket.name != resource.bucket
        ):
            raise GuardDenied(
                "Actual independently pinned publication authority and witness are required"
            )
        self.registry, self.bucket, self.rpc_timeout = registry, witness_bucket, rpc_timeout
        self._resource = canonical(resource.model_dump(mode="json")).encode()

    @property
    def resource(self) -> PublicationResourceReceipt:
        return PublicationResourceReceipt.model_validate_json(self._resource)

    def _fresh(self) -> None:
        # Delayed import avoids journal/publication module initialization cycles.
        from .gcp_journal import _exact_bytes

        resource = self.resource
        try:
            rpc = self.registry.rpc
            assert isinstance(rpc, FirestoreRpc)
            identity = rpc.identity()
            expected = resource.pin.registry
            if (
                identity.get("name") != expected.database
                or identity.get("uid") != str(expected.database_uid)
                or identity.get("type") != "FIRESTORE_NATIVE"
            ):
                raise GuardUnavailable("Publication authority immutable identity changed")
            raw = canonical(resource.pin.model_dump(mode="json")).encode()
            _exact_bytes(
                self.bucket,
                publication_resource_path(resource.pin),
                raw,
                generation=resource.generation,
                timeout=self.rpc_timeout,
            )
        except Exception:
            raise GuardUnavailable(
                "Independent publication resource/witness is unavailable"
            ) from None

    def _gate(self, tx: BufferedTransaction) -> PublicationGate:
        try:
            gate = PublicationGate.model_validate(tx.get("control", "publication"))
            if gate.pin != self.resource.pin:
                raise ValueError("Publication gate pin")
            return gate
        except (ValidationError, ValueError, TypeError):
            raise GuardUnavailable(
                "Publication authority gate is missing, restored or corrupt"
            ) from None

    def current(self) -> PublicationGate:
        self._fresh()
        captured: list[PublicationGate] = []
        self.registry.run(lambda tx: captured.append(self._gate(tx)), before_attempt=self._fresh)
        self._fresh()
        return PublicationGate.model_validate_json(canonical(captured[0].model_dump(mode="json")))

    def retain(self, raw: bytes, bucket: str) -> None:
        pin = self.resource.pin
        if bucket != pin.journal_bucket:
            raise GuardDenied("Publication target journal bucket pin changed")
        item = record(raw, pin)
        fixed = canonical(item.model_dump(mode="json")).encode()

        # No IO port or caller receives this private byte snapshot.
        def stage(tx: BufferedTransaction) -> None:
            gate = self._gate(tx)
            candidate = PublicationRecord.model_validate_json(fixed)
            if gate.phase == "SEALED":
                raise GuardUnavailable("Publication admission is permanently sealed")
            if gate.phase == "DENY_ONLY":
                intent = PasswordLifetimeIntent.model_validate(candidate.intent)
                assert gate.close is not None
                before_control = next(
                    (
                        v.value
                        for v in intent.effects.before
                        if v.namespace == "control" and v.key == "meta"
                    ),
                    None,
                )
                control = NativeClosedControl.model_validate(before_control)
                if (
                    fingerprint(intent.command.model_dump(mode="json"))
                    != gate.close.body.command_sha256
                    or control.pin != pin.target
                    or control.close != gate.close
                    or any(
                        e.kind == "password_lifetime_effects"
                        and e.sha256 != candidate.reference.sha256
                        for e in gate.entries
                        if e.operation_id == candidate.reference.operation_id
                    )
                ):
                    raise GuardDenied("Only the exact protected closed denial can publish")
            key = str(candidate.reference.operation_id)
            existing = tx.get("publication_intents", key)
            if existing is not None:
                if not _same(existing, candidate.model_dump(mode="json")):
                    raise GuardUnavailable(
                        "Retained publication bytes conflict with operation identity"
                    )
                # Generic safety replay has always been status/effect idempotence,
                # not action ownership. Pairing/lifetime require their own fresh ACK.
                if (
                    candidate.reference.kind == "safety_effect"
                    and candidate.reference in gate.entries
                    and _same(existing, candidate.model_dump(mode="json"))
                ):
                    return
                raise GuardUnavailable(
                    "Publication identity was already retained; no owner can be adopted"
                )
            if len(gate.entries) >= 63:
                raise GuardUnavailable("Complete publication admission budget exhausted")
            entries = tuple(
                sorted((*gate.entries, candidate.reference), key=lambda v: str(v.operation_id))
            )
            updated = gate.model_copy(update={"entries": entries})
            # Revalidate the complete canonical gate, never trust model_copy updates.
            updated = PublicationGate.model_validate_json(
                canonical(updated.model_dump(mode="json"))
            )
            tx.put("publication_intents", key, candidate.model_dump(mode="json"), immutable=True)
            tx._stage("control", "publication", updated.model_dump(mode="json"), immutable=False)

        try:
            self.registry.run(stage, before_attempt=self._fresh)
        except AmbiguousCommit:
            raise GuardUnavailable(
                "Protected publication Commit is unknown; no upload owner"
            ) from None
        self._fresh()
        rows = self.registry.read_many(
            (("control", "publication"), ("publication_intents", str(item.reference.operation_id)))
        )
        gate = PublicationGate.model_validate(rows["control", "publication"])
        if (
            gate.pin != pin
            or gate.phase == "SEALED"
            or item.reference not in gate.entries
            or not _same(
                rows["publication_intents", str(item.reference.operation_id)],
                item.model_dump(mode="json"),
            )
        ):
            raise GuardUnavailable("Protected publication acknowledgement lost current admission")
        self._fresh()

    def close(self, receipt: AuthorityCloseReceipt) -> bool:
        self._fresh()
        if receipt.body.pin != self.resource.pin.target:
            raise GuardDenied("Publication close belongs to another native authority")
        expected: list[PublicationGate] = []

        def stage(tx: BufferedTransaction) -> None:
            gate = self._gate(tx)
            if gate.phase != "OPEN":
                raise GuardUnavailable("Only an OPEN publication authority can acquire its close")
            updated = PublicationGate(
                pin=gate.pin, phase="DENY_ONLY", entries=gate.entries, close=receipt
            )
            tx._stage("control", "publication", updated.model_dump(mode="json"), immutable=False)
            expected[:] = [updated]

        try:
            self.registry.run(stage, before_attempt=self._fresh)
            self._fresh()
            return _same(
                self.registry.read("control", "publication"), expected[0].model_dump(mode="json")
            )
        except (AmbiguousCommit, GuardUnavailable):
            return False

    def check_closed(self, receipt: AuthorityCloseReceipt, *, status_only: bool = False) -> None:
        gate = self.current()
        if gate.close != receipt or gate.phase not in (
            {"DENY_ONLY", "SEALED"} if status_only else {"DENY_ONLY"}
        ):
            raise GuardUnavailable("Protected denial publication admission is closed or mismatched")

    def seal(self, receipt: AuthorityCloseReceipt) -> SealedPublicationCut | None:
        expected: list[PublicationGate] = []

        def stage(tx: BufferedTransaction) -> None:
            gate = self._gate(tx)
            if gate.phase != "DENY_ONLY" or gate.close != receipt:
                raise GuardUnavailable(
                    "Only its acknowledged protected denial owner can seal publication"
                )
            updated = PublicationGate(
                pin=gate.pin, phase="SEALED", entries=gate.entries, close=receipt, seal_id=uuid4()
            )
            tx._stage("control", "publication", updated.model_dump(mode="json"), immutable=False)
            expected[:] = [updated]

        try:
            self.registry.run(stage, before_attempt=self._fresh)
        except AmbiguousCommit:
            return None
        self._fresh()
        raw = canonical(expected[0].model_dump(mode="json")).encode()
        if not _same(
            self.registry.read("control", "publication"), expected[0].model_dump(mode="json")
        ):
            raise GuardUnavailable("Publication seal changed before own acknowledgement")
        self._fresh()
        return SealedPublicationCut(raw)

    def complete(self, cut: SealedPublicationCut) -> tuple[PublicationRecord, ...]:
        gate = cut.gate
        if gate.pin != self.resource.pin or gate.phase != "SEALED":
            raise GuardDenied("Exact sealed publication cut is required")
        self._fresh()
        keys = (
            ("control", "publication"),
            *tuple(("publication_intents", str(e.operation_id)) for e in gate.entries),
        )
        rows = self.registry.read_many(keys)
        if not _same(rows["control", "publication"], gate.model_dump(mode="json")):
            raise GuardUnavailable("Publication cut was restored or changed")
        result = []
        for entry in gate.entries:
            try:
                item = PublicationRecord.model_validate(
                    rows["publication_intents", str(entry.operation_id)]
                )
                verified = record(canonical(item.intent).encode(), gate.pin)
                if item.reference != entry or verified != item:
                    raise ValueError("Publication record cut binding")
                result.append(item)
            except (ValidationError, ValueError, TypeError, GuardDenied):
                raise GuardUnavailable(
                    "Complete retained publication record is missing or corrupt"
                ) from None
        self._fresh()
        return tuple(result)


def retain_before_upload(
    publication: GcpPublicationCoordinator | None, raw: bytes, bucket: str
) -> None:
    if type(publication) is not GcpPublicationCoordinator:
        raise GuardUnavailable("Protected publication admission configuration is unavailable")
    publication.retain(raw, bucket)
