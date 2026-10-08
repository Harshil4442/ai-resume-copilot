"""Injected create-only journal and live witness reads; never constructs ADC clients."""
from __future__ import annotations

import hashlib
import math
import os
from typing import Any, Protocol, cast

from google.api_core.exceptions import GoogleAPICallError, RetryError
from google.cloud.storage import __version__ as storage_version
from google.cloud.storage.bucket import Bucket
from google.cloud.storage.client import Client

from .contracts import canonical
from .gcp_contracts import JournalIntent, JournalReceipt, RegistryPin, WitnessBody, WitnessPin
from .gcp_media import BoundedSink, DownloadClient
from .store import GuardDenied, GuardUnavailable


class IdentityRpc(Protocol):
    def identity(self) -> dict[str, str]: ...


def _sdk_configuration(bucket: Bucket) -> None:
    # The pinned SDK otherwise starts an unbounded background bucket metadata GET.
    # This flag is only a denial gate, never production-authority enablement.
    if (isinstance(bucket, Bucket)
            and os.environ.get("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA") != "true"):
        raise GuardUnavailable("Dedicated Storage SDK metadata suppression is required")
    if isinstance(bucket, Bucket):
        cache = getattr(bucket.client, "_bucket_metadata_cache", None)
        if storage_version != "3.13.0" or cache is None or cache.get(bucket.name) is not None:
            raise GuardUnavailable("A fresh dedicated pinned Storage SDK client is required")


def _generation(value: object) -> str:
    if (type(value) is not str or not value.isascii() or not value.isdecimal()
            or value.startswith("0") or len(value) > 19 or int(value) > 2**63 - 1):
        raise GuardUnavailable("A concrete canonical object generation is required")
    return value


def _metadata(blob: Any, expected_length: int) -> str:
    # The pinned SDK's public .size/.generation accessors normalize via int(),
    # erasing True/float/noncanonical-string differences. Validate fresh wire values.
    generation = _generation(blob._properties.get("generation"))
    size = blob._properties.get("size")
    if (type(size) is not str or not size.isascii() or not size.isdecimal()
            or len(size) > 5 or size.startswith("0") or int(size) != expected_length
            or blob._properties.get("contentEncoding") not in {None, "identity"}):
        raise GuardUnavailable("Object metadata lacks the exact bounded byte size")
    return generation


def _exact_bytes(bucket: Bucket, path: str, expected: bytes, *, generation: str | None,
                 timeout: float) -> str:
    if type(expected) is not bytes or not 0 < len(expected) <= 65_536:
        raise GuardUnavailable("Expected object bytes must be positive and bounded")
    live = bucket.blob(path)
    _sdk_configuration(bucket)
    live.reload(retry=None, timeout=timeout)
    found_generation = _generation(live._properties.get("generation"))
    if generation is not None and found_generation != generation:
        raise GuardUnavailable("Live witness generation changed")
    _metadata(live, len(expected))
    # Fresh metadata request pins both the generation and precondition before media IO.
    _sdk_configuration(bucket)
    exact = bucket.blob(path, generation=int(found_generation))
    exact.reload(if_generation_match=int(found_generation), retry=None, timeout=timeout)
    if _metadata(exact, len(expected)) != found_generation:
        raise GuardUnavailable("Exact-generation metadata disagrees")
    _sdk_configuration(bucket)
    proxy = DownloadClient(bucket.client, bucket.name, path, found_generation, len(expected))
    sink = BoundedSink(len(expected))
    try:
        bucket.blob(path, generation=int(found_generation)).download_to_file(
            sink, client=cast(Client, proxy), start=0, end=len(expected), raw_download=True,
            single_shot_download=False, if_generation_match=int(found_generation),
            retry=None, timeout=timeout, checksum=None,
        )
        found = sink.getvalue()
    finally:
        proxy.close_responses()
        sink.close()
    if found != expected:
        raise GuardUnavailable("Generation-bound object bytes disagree")
    return found_generation


class GcsJournal:
    def __init__(self, bucket: Bucket, *, rpc_timeout: float = 2.0) -> None:
        if (type(rpc_timeout) not in {int, float} or not 0 < rpc_timeout <= 10
                or not math.isfinite(rpc_timeout)):
            raise ValueError("Journal RPC timeout is invalid")
        self.bucket, self.rpc_timeout = bucket, rpc_timeout

    @staticmethod
    def _intent_bytes(intent: JournalIntent) -> tuple[str, bytes]:
        raw = canonical(intent.model_dump(mode="json")).encode("utf-8")
        if len(raw) > 65_536:
            raise GuardDenied("Journal intent byte budget exceeded")
        authority = hashlib.sha256(str(intent.pin.authority_id).encode()).hexdigest()
        path = f"authority-intents/{authority}/{intent.partition}/{intent.operation_id}.json"
        return path, raw

    def verify(self, intent: JournalIntent, receipt: JournalReceipt) -> None:
        path, raw = self._intent_bytes(intent)
        if (receipt.bucket != self.bucket.name or receipt.path != path
                or receipt.sha256 != hashlib.sha256(raw).hexdigest()):
            raise GuardUnavailable("Journal receipt is not bound to the exact intent")
        try:
            _exact_bytes(self.bucket, path, raw, generation=receipt.generation,
                         timeout=self.rpc_timeout)
        except (GoogleAPICallError, RetryError, OSError, TimeoutError) as exc:
            raise GuardUnavailable("Generation-bound journal receipt is unavailable") from exc

    def write(self, intent: JournalIntent) -> JournalReceipt:
        path, raw = self._intent_bytes(intent)
        _sdk_configuration(self.bucket)
        try:
            self.bucket.blob(path).upload_from_string(
                raw, content_type="application/json", if_generation_match=0,
                retry=None, timeout=self.rpc_timeout, checksum=None,
            )
        except (GoogleAPICallError, RetryError, OSError, TimeoutError):
            # Reconcile only exact bytes, including 412/uncertain upload. No retry POST,
            # metadata repair, overwrite, deletion, or assertion that missing means abort.
            pass
        try:
            generation = _exact_bytes(self.bucket, path, raw, generation=None,
                                      timeout=self.rpc_timeout)
        except (GoogleAPICallError, RetryError, OSError, TimeoutError) as exc:
            raise GuardUnavailable("Protected intent cannot be verified") from exc
        return JournalReceipt(bucket=self.bucket.name, path=path, generation=generation,
                              sha256=hashlib.sha256(raw).hexdigest())


class Fence(Protocol):
    def check(self, pin: RegistryPin) -> None: ...


class UnavailableFence:
    def check(self, pin: RegistryPin) -> None:
        raise GuardUnavailable("Production restore-fence configuration is unavailable")


class GcsWitnessFence:
    """Pins are operator-supplied; this does not prove IAM/manifest completeness."""

    def __init__(self, bucket: Bucket, identity: IdentityRpc, pin: WitnessPin,
                 body: WitnessBody, *, rpc_timeout: float = 2.0) -> None:
        raw = canonical(body.model_dump(mode="json")).encode("utf-8")
        if (bucket.name != pin.bucket or body.registry != pin.registry or body.state != "OPEN"
                or hashlib.sha256(raw).hexdigest() != pin.sha256):
            raise GuardDenied("Witness deployment pin/body mismatch")
        if (type(rpc_timeout) not in {int, float} or not 0 < rpc_timeout <= 10
                or not math.isfinite(rpc_timeout)):
            raise ValueError("Witness RPC timeout is invalid")
        self.bucket, self.identity, self.pin = bucket, identity, pin
        self.raw, self.rpc_timeout = raw, rpc_timeout

    def check(self, pin: RegistryPin) -> None:
        if pin != self.pin.registry:
            raise GuardUnavailable("Registry deployment pin changed")
        _sdk_configuration(self.bucket)
        try:
            identity = self.identity.identity()
            if identity != {"name": pin.database, "uid": str(pin.database_uid),
                            "type": "FIRESTORE_NATIVE"}:
                raise GuardUnavailable("Current database incarnation disagrees")
            _exact_bytes(self.bucket, self.pin.path, self.raw,
                         generation=self.pin.generation, timeout=self.rpc_timeout)
        except (GoogleAPICallError, RetryError, OSError, TimeoutError) as exc:
            raise GuardUnavailable("Current restore witness is unavailable") from exc
