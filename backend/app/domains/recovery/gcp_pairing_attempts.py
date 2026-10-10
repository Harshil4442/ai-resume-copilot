"""Injected protected attempt consumption; no production client/factory.

An existing or ambiguously created object never establishes dispatch authority.
SDK timeouts bound individual inactivity/RPC behavior, not total wall-clock time.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

from google.cloud.storage import Bucket

from .contracts import canonical
from .gcp_contracts import JournalReceipt
from .gcp_journal import GcsJournal, _exact_bytes, _metadata, _sdk_configuration
from .gcp_pairing_contracts import PairingAttemptMarker, PairingAttemptReceipt, PairingJournalIntent
from .store import GuardDenied, GuardUnavailable

if TYPE_CHECKING:
    from .gcp_partitioned_publication import PartitionedPublicationCoordinator
    from .gcp_publication import GcpPublicationCoordinator


class PairingAttemptStore(Protocol):
    def write_intent(self, intent: PairingJournalIntent) -> JournalReceipt | None: ...
    def consume(self, marker: PairingAttemptMarker) -> PairingAttemptReceipt | None: ...
    def verify(self, marker: PairingAttemptMarker, receipt: PairingAttemptReceipt) -> None: ...


class UnavailablePairingAttempts:
    def write_intent(self, intent: PairingJournalIntent) -> JournalReceipt | None:
        raise GuardUnavailable("Protected pairing-attempt configuration is unavailable")

    def consume(self, marker: PairingAttemptMarker) -> PairingAttemptReceipt | None:
        raise GuardUnavailable("Protected pairing-attempt configuration is unavailable")

    def verify(self, marker: PairingAttemptMarker, receipt: PairingAttemptReceipt) -> None:
        raise GuardUnavailable("Protected pairing-attempt configuration is unavailable")


class GcsPairingAttempts:
    def __init__(self, bucket: Bucket, *, rpc_timeout: float = 2.0,
                 publication: GcpPublicationCoordinator | PartitionedPublicationCoordinator | None = None):
        # Use the same pinned SDK bound/configuration as the intent journal.
        GcsJournal(bucket, rpc_timeout=rpc_timeout)
        self.bucket, self.rpc_timeout = bucket, rpc_timeout
        self.publication = publication

    @staticmethod
    def _bytes(marker: PairingAttemptMarker) -> tuple[str, bytes]:
        raw = canonical(marker.model_dump(mode="json")).encode()
        if not 0 < len(raw) <= 65_536:
            raise GuardDenied("Pairing attempt marker exceeds its byte budget")
        authority = hashlib.sha256(str(marker.pin.authority_id).encode()).hexdigest()
        return f"authority-pairing-attempts/{authority}/{marker.operation_id}.json", raw

    def _fresh_create(self, path: str, raw: bytes) -> str | None:
        _sdk_configuration(self.bucket)
        live = self.bucket.blob(path)
        try:
            live.upload_from_string(raw, content_type="application/json", if_generation_match=0,
                retry=None, timeout=self.rpc_timeout, checksum=None)
            # This is the fresh POST acknowledgement, never reloaded metadata.
            # A missing/malformed reply consumes the attempt conservatively.
            if live._properties.get("name") != path or live._properties.get("bucket") != self.bucket.name:
                return None
            generation = _metadata(live, len(raw))
            _exact_bytes(self.bucket, path, raw, generation=generation, timeout=self.rpc_timeout)
        except Exception:
            # Includes 412, lost replies and malformed acknowledgements. Never
            # GET/reconcile an upload error, retry POST, adopt, overwrite or delete.
            return None
        return generation

    def write_intent(self, intent: PairingJournalIntent) -> JournalReceipt | None:
        # Generic GcsJournal.write keeps its existing exact-byte reconciliation.
        # Pairing dispatch has the stronger fresh-owner acknowledgement gate.
        path, raw = GcsJournal._intent_bytes(intent)
        from .gcp_publication import retain_before_upload
        try:
            retain_before_upload(self.publication, raw, self.bucket.name)
        except GuardUnavailable:
            return None
        generation = self._fresh_create(path, raw)
        if generation is None:
            return None
        return JournalReceipt(bucket=self.bucket.name, path=path, generation=generation,
                              sha256=hashlib.sha256(raw).hexdigest())

    def consume(self, marker: PairingAttemptMarker) -> PairingAttemptReceipt | None:
        path, raw = self._bytes(marker)
        generation = self._fresh_create(path, raw)
        if generation is None:
            return None
        return PairingAttemptReceipt(bucket=self.bucket.name, path=path, generation=generation,
                                     sha256=hashlib.sha256(raw).hexdigest())

    def verify(self, marker: PairingAttemptMarker, receipt: PairingAttemptReceipt) -> None:
        path, raw = self._bytes(marker)
        if (receipt.bucket != self.bucket.name or receipt.path != path
                or receipt.sha256 != hashlib.sha256(raw).hexdigest()):
            raise GuardUnavailable("Pairing attempt receipt is not bound to its exact marker")
        try:
            _exact_bytes(self.bucket, path, raw, generation=receipt.generation, timeout=self.rpc_timeout)
        except Exception as exc:
            raise GuardUnavailable("Protected pairing attempt marker is unavailable") from exc
