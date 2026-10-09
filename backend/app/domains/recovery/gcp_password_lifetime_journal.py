"""Explicit create-only complete lifetime effects; no factory or retained-object adoption."""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Protocol

from google.cloud.storage import Bucket

from .contracts import canonical
from .gcp_contracts import JournalReceipt
from .gcp_journal import _exact_bytes
from .gcp_pairing_attempts import GcsPairingAttempts
from .gcp_password_lifetime_contracts import PasswordLifetimeIntent
from .store import GuardDenied, GuardUnavailable

if TYPE_CHECKING:
    from .gcp_partitioned_publication import PartitionedPublicationCoordinator
    from .gcp_publication import GcpPublicationCoordinator


class PasswordLifetimeJournal(Protocol):
    def create(self, intent: PasswordLifetimeIntent) -> JournalReceipt | None: ...
    def verify(self, intent: PasswordLifetimeIntent, receipt: JournalReceipt) -> None: ...


class UnavailablePasswordLifetimeJournal:
    def create(self, intent: PasswordLifetimeIntent) -> JournalReceipt | None:
        raise GuardUnavailable("Protected password-lifetime configuration is unavailable")

    def verify(self, intent: PasswordLifetimeIntent, receipt: JournalReceipt) -> None:
        raise GuardUnavailable("Protected password-lifetime configuration is unavailable")


class GcsPasswordLifetimeJournal:
    def __init__(self, bucket: Bucket, *, rpc_timeout: float = 2.0,
                 publication: GcpPublicationCoordinator | PartitionedPublicationCoordinator | None = None):
        self.bucket, self.rpc_timeout = bucket, rpc_timeout
        self.publication = publication
        self._creator = GcsPairingAttempts(bucket, rpc_timeout=rpc_timeout)

    @staticmethod
    def _bytes(intent: PasswordLifetimeIntent) -> tuple[str, bytes]:
        raw = canonical(intent.model_dump(mode="json")).encode()
        if not 0 < len(raw) <= 65_536:
            raise GuardDenied("Protected lifetime effects exceed the byte budget")
        authority = hashlib.sha256(str(intent.pin.authority_id).encode()).hexdigest()
        return f"authority-intents/{authority}/{intent.partition}/{intent.operation_id}.json", raw

    def create(self, intent: PasswordLifetimeIntent) -> JournalReceipt | None:
        path, raw = self._bytes(intent)
        from .gcp_publication import retain_before_upload
        try:
            retain_before_upload(self.publication, raw, self.bucket.name)
        except GuardUnavailable:
            return None
        generation = self._creator._fresh_create(path, raw)
        if generation is None:
            return None
        return JournalReceipt(bucket=self.bucket.name, path=path, generation=generation,
                              sha256=hashlib.sha256(raw).hexdigest())

    def verify(self, intent: PasswordLifetimeIntent, receipt: JournalReceipt) -> None:
        path, raw = self._bytes(intent)
        if (receipt.bucket != self.bucket.name or receipt.path != path
                or receipt.sha256 != hashlib.sha256(raw).hexdigest()):
            raise GuardUnavailable("Lifetime receipt does not bind the full effects")
        try:
            _exact_bytes(self.bucket, path, raw, generation=receipt.generation, timeout=self.rpc_timeout)
        except Exception:
            raise GuardUnavailable("Protected lifetime effects are unavailable") from None
