"""Distinct one-shot native submit admission; no employer receipt or payment authority."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from threading import Lock
from typing import cast
from uuid import UUID, uuid4

from google.cloud.storage import Bucket, Client

from .contracts import canonical, fingerprint
from .gcp_buffer import BufferedTransaction
from .gcp_contracts import AmbiguousCommit, JournalReceipt
from .gcp_journal import GcsJournal, GcsWitnessFence, _generation, _metadata, _sdk_configuration
from .gcp_media import BoundedSink, DownloadClient
from .gcp_partitioned_contracts import (
    PartitionedRecord,
    PartitionedResource,
    ScopedJournalIntent,
    ScopedSubject,
    ScopedSubmitBegin,
    ScopedSubmitDeviceEnrollment,
)
from .gcp_partitioned_publication import PartitionedPublicationCoordinator
from .gcp_scoped_authority import current_subject
from .gcp_submit_contracts import (
    SubmitBinding,
    SubmitContext,
    SubmitDeviceGrant,
    SubmitDeviceRegistration,
    SubmitGrantPin,
)
from .store import GuardDenied, GuardUnavailable


def _json(value) -> dict:
    return value.model_dump(mode="json")


def read_operator_grant(bucket: Bucket, pin: SubmitGrantPin) -> SubmitDeviceGrant:
    """Fresh exact-generation bounded bytes; never cached metadata or a client grant."""
    if bucket.name != pin.bucket:
        raise GuardUnavailable("Submit operator grant bucket changed")
    _sdk_configuration(bucket)
    live = bucket.blob(pin.path)
    live.reload(retry=None, timeout=2.0)
    size = live._properties.get("size")
    if (
        _generation(live._properties.get("generation")) != pin.generation
        or type(size) is not str
        or not size.isascii()
        or not size.isdecimal()
        or size.startswith("0")
        or not 0 < int(size) <= 16_384
    ):
        raise GuardUnavailable("Submit operator grant generation or byte bound changed")
    length = int(size)
    exact = bucket.blob(pin.path, generation=int(pin.generation))
    _sdk_configuration(bucket)
    exact.reload(if_generation_match=int(pin.generation), retry=None, timeout=2.0)
    if _metadata(exact, length) != pin.generation:
        raise GuardUnavailable("Submit operator grant exact generation changed")
    proxy = DownloadClient(bucket.client, bucket.name, pin.path, pin.generation, length)
    sink = BoundedSink(length)
    try:
        _sdk_configuration(bucket)
        exact.download_to_file(
            sink,
            client=cast(Client, proxy),
            start=0,
            end=length,
            raw_download=True,
            single_shot_download=False,
            if_generation_match=int(pin.generation),
            retry=None,
            timeout=2.0,
            checksum=None,
        )
        raw = sink.getvalue()
    finally:
        proxy.close_responses()
        sink.close()
    if len(raw) != length or hashlib.sha256(raw).hexdigest() != pin.sha256:
        raise GuardUnavailable("Submit operator grant exact bytes changed")
    grant = SubmitDeviceGrant.model_validate_json(raw)
    if (
        canonical(_json(grant)).encode() != raw
        or pin.path != f"authority-submit-device-grants/{grant.device_id}.json"
    ):
        raise GuardUnavailable("Submit operator grant identity or canonical bytes changed")
    return grant


def checked_subject(tx: BufferedTransaction, context: SubmitContext) -> ScopedSubject:
    subject = current_subject(tx, context.subject_uuid)
    if subject.model_dump(mode="json", exclude={"enrollment_operation"}) != _json(context):
        raise GuardDenied("Current protected candidate credential revision changed")
    return subject


def checked_device(
    tx: BufferedTransaction,
    context: SubmitContext,
    binding: SubmitBinding,
    resource: PartitionedResource,
) -> SubmitDeviceRegistration:
    device = SubmitDeviceRegistration.model_validate(
        tx.get("v3_submit_devices", str(binding.device_id))
    )
    grant = device.grant
    full = PartitionedRecord.model_validate(tx.get("v3_records", str(device.operation_id)))
    intent = ScopedJournalIntent.model_validate(full.intent)
    command = intent.command
    if (
        not isinstance(command, ScopedSubmitDeviceEnrollment)
        or intent.pin != resource.pin.authority.target
        or command.grant != grant
        or command.grant_pin != device.grant_pin
        or grant.pin != intent.pin
        or grant.context != context
        or grant.device_id != binding.device_id
        or grant.key_sha256 != binding.device_key_sha256
        or grant.key_generation != binding.device_key_generation
        or grant.executor_revision != binding.executor_revision
        or tx.get("v3_submit_key_owners", grant.key_sha256)
        != {
            "device_id": str(grant.device_id),
            "subject_uuid": str(context.subject_uuid),
            "registration_operation": str(device.operation_id),
        }
    ):
        raise GuardDenied("Exact independently enrolled native submit device/key is unavailable")
    if tx.get("v3_submit_device_tombstones", str(grant.device_id)) is not None:
        raise GuardDenied("Native submit device has been denied")
    return device


def reservation_record(intent: ScopedJournalIntent) -> dict:
    return {"operation_id": str(intent.operation_id), "intent_sha256": fingerprint(_json(intent))}


def retain_submit_reservation(
    tx: BufferedTransaction, intent: ScopedJournalIntent, resource: PartitionedResource
) -> None:
    """Atomic with protected full-intent publication, before any journal/BEGIN IO."""
    command = intent.command
    if not isinstance(command, ScopedSubmitBegin):
        raise GuardDenied("Exact distinct submit command required")
    context = SubmitContext.model_validate(
        command.subject.model_dump(exclude={"enrollment_operation"})
    )
    if (
        checked_subject(tx, context) != command.subject
        or checked_device(tx, context, command.action, resource) != command.device
    ):
        raise GuardDenied("Submit reservation cannot retarget current native owner/device")
    key = command.action.opening_claim_key(context.subject_uuid)
    if tx.get("v3_submit_reservations", key) is not None:
        raise GuardDenied("This employer opening already has a submit attempt; no automatic resend")
    tx.put("v3_submit_reservations", key, reservation_record(intent), immutable=True)


@dataclass(frozen=True)
class SubmitBeginAck:
    _raw: bytes = field(repr=False)
    _journal: JournalReceipt = field(repr=False)
    _issuer: object = field(repr=False)


class GcpSubmitAuthority:
    def __init__(
        self,
        publication: PartitionedPublicationCoordinator,
        journal: GcsJournal,
        fence: GcsWitnessFence,
        grant_pin: SubmitGrantPin,
        *,
        epoch_generation: int,
        executor_revision: str,
        now_ms: Callable[[], int] = lambda: time.time_ns() // 1_000_000,
    ):
        if (
            type(publication) is not PartitionedPublicationCoordinator
            or type(journal) is not GcsJournal
            or type(fence) is not GcsWitnessFence
            or journal.publication is not publication
            or journal.bucket.name != publication.resource.bucket
            or fence.pin.registry != publication.resource.pin.authority.target
            or type(epoch_generation) is not int
            or epoch_generation < 1
            or not executor_revision
        ):
            raise GuardDenied(
                "Exact independent native submit composition and deployment pins required"
            )
        self.publication, self.journal, self.fence = publication, journal, fence
        self.grant_pin, self.epoch_generation, self.executor_revision = (
            grant_pin,
            epoch_generation,
            executor_revision,
        )
        self.now_ms, self._issuer, self._lock = now_ms, object(), Lock()
        self._acks: dict[UUID, SubmitBeginAck] = {}

    def _now(self) -> int:
        now = self.now_ms()
        if type(now) is not int or not 0 < now <= 2**53 - 1:
            raise GuardUnavailable("Trusted submit authority clock is unavailable")
        return now

    def _fresh(self) -> SubmitDeviceGrant:
        self.fence.check(self.publication.resource.pin.authority.target)
        self.publication._fresh_open()
        grant = read_operator_grant(
            self.journal.bucket.client.bucket(self.grant_pin.bucket), self.grant_pin
        )
        if (
            grant.pin != self.publication.resource.pin.authority.target
            or grant.epoch_generation != self.epoch_generation
            or grant.executor_revision != self.executor_revision
        ):
            raise GuardDenied("Fresh native submit enrollment epoch or executor changed")
        return grant

    def _current(
        self,
        tx: BufferedTransaction,
        context: SubmitContext,
        binding: SubmitBinding,
        expected: ScopedSubmitBegin | None = None,
    ) -> tuple[ScopedSubject, SubmitDeviceRegistration]:
        self.publication._root(tx, opened=True)
        now = self._now()
        if (
            context.candidate_id != binding.candidate_id
            or binding.executor_revision != self.executor_revision
            or not binding.issued_at_ms <= now < binding.deadline_ms
        ):
            raise GuardDenied("Exact current candidate submit approval and short deadline required")
        subject = checked_subject(tx, context)
        device = checked_device(tx, context, binding, self.publication.resource)
        if (
            device.grant_pin != self.grant_pin
            or device.grant.epoch_generation != self.epoch_generation
            or expected is not None
            and (expected.subject != subject or expected.device != device)
        ):
            raise GuardDenied("Native submit enrollment or protected credential state changed")
        return subject, device

    def _run(self, stage: Callable[[BufferedTransaction], None]) -> None:
        def refresh() -> None:
            self._fresh()

        self.publication.registry.run(stage, before_attempt=refresh)
        self._fresh()

    def provision_device(self) -> None:
        """Explicit operator port only. Never called by factory/reserve or SQL adoption."""
        grant = self._fresh()
        subject: list[ScopedSubject] = []
        self._run(lambda tx: subject.append(checked_subject(tx, grant.context)))
        now = self._now()
        intent = ScopedJournalIntent(
            operation_id=uuid4(),
            pin=grant.pin,
            command=ScopedSubmitDeviceEnrollment(
                subject=subject[-1], grant=grant, grant_pin=self.grant_pin
            ),
            created_at_ms=now,
            deadline_ms=now + 60_000,
        )
        receipt = self.journal.write(intent)
        self.journal.verify(intent, receipt)
        fixed = canonical(_json(intent)).encode()
        device = SubmitDeviceRegistration(
            operation_id=intent.operation_id, grant=grant, grant_pin=self.grant_pin
        )

        def register(tx: BufferedTransaction) -> None:
            self.publication._root(tx, opened=True)
            if (
                checked_subject(tx, grant.context) != subject[-1]
                or self._now() >= intent.deadline_ms
            ):
                raise GuardDenied("Original submit device owner or enrollment interval changed")
            full = PartitionedRecord.model_validate(tx.get("v3_records", str(intent.operation_id)))
            if canonical(full.intent).encode() != fixed:
                raise GuardUnavailable(
                    "Submit device enrollment lost its full retained native intent"
                )
            if any(
                tx.get(namespace, key) is not None
                for namespace, key in (
                    ("v3_submit_devices", str(grant.device_id)),
                    ("v3_submit_key_owners", grant.key_sha256),
                    ("v3_submit_device_tombstones", str(grant.device_id)),
                )
            ):
                raise GuardDenied("Submit device/key cannot adopt an existing or denied enrollment")
            tx.put("v3_submit_devices", str(grant.device_id), _json(device), immutable=True)
            tx.put(
                "v3_submit_key_owners",
                grant.key_sha256,
                {
                    "device_id": str(grant.device_id),
                    "subject_uuid": str(grant.context.subject_uuid),
                    "registration_operation": str(intent.operation_id),
                },
                immutable=True,
            )

        try:
            self._run(register)
        except AmbiguousCommit:
            raise GuardUnavailable(
                "Submit enrollment commit unknown; no registration acknowledgement"
            ) from None

    def reserve(self, context: SubmitContext, binding: SubmitBinding) -> SubmitBeginAck | None:
        context = SubmitContext.model_validate_json(canonical(_json(context)))
        binding = SubmitBinding.model_validate_json(canonical(_json(binding)))
        current: list[tuple[ScopedSubject, SubmitDeviceRegistration]] = []
        self._run(lambda tx: current.append(self._current(tx, context, binding)))
        now = self._now()
        intent = ScopedJournalIntent(
            operation_id=uuid4(),
            pin=self.publication.resource.pin.authority.target,
            command=ScopedSubmitBegin(
                subject=current[-1][0], device=current[-1][1], action=binding
            ),
            created_at_ms=now,
            deadline_ms=binding.deadline_ms,
        )
        # Full native publication also irreversibly reserves this opening before
        # journal IO. A lost publication or final commit ACK cannot be resumed.
        receipt = self.journal.write(intent)
        self.journal.verify(intent, receipt)
        fixed = canonical(_json(intent)).encode()

        def begin(tx: BufferedTransaction) -> None:
            command = intent.command
            assert isinstance(command, ScopedSubmitBegin)
            self._current(tx, context, binding, command)
            full = PartitionedRecord.model_validate(tx.get("v3_records", str(intent.operation_id)))
            key = binding.opening_claim_key(context.subject_uuid)
            if (
                canonical(full.intent).encode() != fixed
                or tx.get("v3_submit_reservations", key) != reservation_record(intent)
                or tx.get("v3_submit_begins", str(intent.operation_id)) is not None
            ):
                raise GuardDenied(
                    "Original one-shot native submit reservation is absent or consumed"
                )
            tx.put(
                "v3_submit_begins",
                str(intent.operation_id),
                {**reservation_record(intent), "journal": _json(receipt)},
                immutable=True,
            )

        try:
            self._run(begin)
        except AmbiguousCommit:
            return None
        if self._now() >= binding.deadline_ms:
            raise GuardDenied("Native submit deadline expired after durable BEGIN")
        ack = SubmitBeginAck(fixed, receipt, self._issuer)
        with self._lock:
            self._acks[intent.operation_id] = ack
        return ack

    def consume_ack(self, ack: SubmitBeginAck, binding: SubmitBinding) -> dict:
        if type(ack) is not SubmitBeginAck or ack._issuer is not self._issuer:
            raise GuardDenied("Exact original native submit begin acknowledgement required")
        intent = ScopedJournalIntent.model_validate_json(ack._raw)
        command = intent.command
        if not isinstance(
            command, ScopedSubmitBegin
        ) or command.action != SubmitBinding.model_validate_json(canonical(_json(binding))):
            raise GuardDenied("Submit acknowledgement belongs to another exact action")
        # Burn original process ownership before any IO; clones/status/process
        # restarts cannot reconstruct or repeatedly export a possibly used ACK.
        with self._lock:
            if self._acks.pop(intent.operation_id, None) is not ack:
                raise GuardDenied("Native submit acknowledgement is consumed or not owned")
        context = command.device.grant.context
        self.journal.verify(intent, ack._journal)

        def export(tx: BufferedTransaction) -> None:
            self._current(tx, context, binding, command)
            full = PartitionedRecord.model_validate(tx.get("v3_records", str(intent.operation_id)))
            if (
                canonical(full.intent).encode() != ack._raw
                or tx.get("v3_submit_reservations", binding.opening_claim_key(context.subject_uuid))
                != reservation_record(intent)
                or tx.get("v3_submit_begins", str(intent.operation_id))
                != {**reservation_record(intent), "journal": _json(ack._journal)}
                or tx.get("v3_submit_exports", str(intent.operation_id)) is not None
            ):
                raise GuardDenied("Original native submit BEGIN is unavailable or already exported")
            tx.put(
                "v3_submit_exports",
                str(intent.operation_id),
                reservation_record(intent),
                immutable=True,
            )

        try:
            self._run(export)
        except AmbiguousCommit:
            raise GuardUnavailable(
                "Submit export commit unknown; no re-export or reconstructed permission"
            ) from None
        if self._now() >= binding.deadline_ms:
            raise GuardDenied("Native submit deadline expired after durable export consumption")
        return {
            "kind": "native_submit_begin",
            "operation_id": str(intent.operation_id),
            "binding_sha256": binding.digest,
            "intent_sha256": fingerprint(_json(intent)),
            "native_pin_sha256": fingerprint(_json(intent.pin)),
            "journal_generation": ack._journal.generation,
            "journal_sha256": ack._journal.sha256,
            "deadline_ms": binding.deadline_ms,
            "employer_receipt": False,
            "settlement_authorized": False,
        }
