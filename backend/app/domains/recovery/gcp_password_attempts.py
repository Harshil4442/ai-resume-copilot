"""Explicit create-only signing admission; no retained-object adoption or factory."""
from __future__ import annotations

import hashlib
from typing import Protocol

from google.cloud.storage import Bucket

from .contracts import canonical
from .gcp_journal import _exact_bytes
from .gcp_pairing_attempts import GcsPairingAttempts
from .gcp_password_contracts import PasswordSigningMarker, PasswordSigningReceipt
from .store import GuardDenied, GuardUnavailable


class PasswordSigningStore(Protocol):
    def consume(self, marker: PasswordSigningMarker) -> PasswordSigningReceipt | None: ...
    def verify(self, marker: PasswordSigningMarker, receipt: PasswordSigningReceipt) -> None: ...


class UnavailablePasswordSigning:
    def consume(self, marker: PasswordSigningMarker) -> PasswordSigningReceipt | None:
        raise GuardUnavailable("Protected password-signing configuration is unavailable")

    def verify(self, marker: PasswordSigningMarker, receipt: PasswordSigningReceipt) -> None:
        raise GuardUnavailable("Protected password-signing configuration is unavailable")


class GcsPasswordSigning:
    def __init__(self, bucket: Bucket, *, rpc_timeout: float = 2.0):
        self._creator = GcsPairingAttempts(bucket, rpc_timeout=rpc_timeout)
        self.bucket, self.rpc_timeout = bucket, rpc_timeout

    @staticmethod
    def _bytes(marker: PasswordSigningMarker) -> tuple[str, bytes]:
        raw = canonical(marker.model_dump(mode="json")).encode()
        if not 0 < len(raw) <= 65_536:
            raise GuardDenied("Signing marker exceeds its byte budget")
        authority = hashlib.sha256(str(marker.pin.authority_id).encode()).hexdigest()
        # A new service, operation ID or assertion ID cannot evade consumption.
        return f"authority-password-signing/{authority}/{marker.challenge_id}.json", raw

    def consume(self, marker: PasswordSigningMarker) -> PasswordSigningReceipt | None:
        path, raw = self._bytes(marker)
        generation = self._creator._fresh_create(path, raw)
        if generation is None:
            return None
        return PasswordSigningReceipt(bucket=self.bucket.name, path=path, generation=generation,
                                      sha256=hashlib.sha256(raw).hexdigest())

    def verify(self, marker: PasswordSigningMarker, receipt: PasswordSigningReceipt) -> None:
        path, raw = self._bytes(marker)
        if (receipt.bucket != self.bucket.name or receipt.path != path
                or receipt.sha256 != hashlib.sha256(raw).hexdigest()):
            raise GuardUnavailable("Signing receipt is not bound to its exact marker")
        try:
            _exact_bytes(self.bucket, path, raw, generation=receipt.generation, timeout=self.rpc_timeout)
        except Exception:
            raise GuardUnavailable("Protected signing consumption is unavailable") from None
