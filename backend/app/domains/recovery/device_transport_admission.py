"""Request-local device timing capture; authority requires fresh native ECDSA.

Nothing in a timing capture authorizes nonce consumption or a device command.
Only this verifier can exchange it for a verified, same-request admission after
the original native origin/key checks and actual signature verification.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
from collections.abc import Callable
from dataclasses import dataclass
from threading import Lock, current_thread
from time import monotonic
from weakref import WeakKeyDictionary

from .pairing_auth import key_fingerprint, public_jwk, verify_signature
from .store import GuardDenied

SIGNED_DEVICE_OPERATIONS = frozenset({"prepare", "inspect-key", "status", "challenge", "refresh-challenge"})
FAILURE = "Browser pairing request was rejected"
WINDOW_MS = 5_000
PROCESSING_MS = 30_000


class DeviceIngressTiming:
    """Opaque timing only, never a signature/key/account authorization."""
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Device ingress timing is verifier-issued only")


class VerifiedDeviceAdmission:
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Device admissions are verifier-issued only")


@dataclass(frozen=True, repr=False)
class _Request:
    operation: str
    origin: str
    raw_sha256: str
    request_id: str
    issued_at_ms: int
    identity: str | None


@dataclass(repr=False)
class _Timing:
    request: _Request
    owner: object
    construction_owner: object | None
    thread: object
    task: object | None
    admitted_ms: int
    started_monotonic: float
    last_ms: int
    last_monotonic: float
    phase: str = "captured"


@dataclass(frozen=True, repr=False)
class _Verified:
    ingress: DeviceIngressTiming
    timing: _Timing
    key_sha256: str


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def _proof(operation: str, raw: bytes, origin: str):
    import re

    from .browser_pairing import DeviceLookup, KeyStatusRequest, PrepareRequest, exact_json
    if type(origin) is not str or re.fullmatch(r"chrome-extension://[a-p]{32}", origin) is None:
        raise GuardDenied(FAILURE)
    data = exact_json(raw)
    if operation in {"prepare", "inspect-key"}:
        proof = (PrepareRequest if operation == "prepare" else KeyStatusRequest).model_validate(data)
        if origin != f"chrome-extension://{proof.extension_id}":
            raise GuardDenied(FAILURE)
        return proof
    if operation not in SIGNED_DEVICE_OPERATIONS:
        raise GuardDenied(FAILURE)
    proof = DeviceLookup.model_validate(data)
    if proof.operation != {"status": "status", "challenge": "device_challenge",
                           "refresh-challenge": "refresh_challenge"}[operation]:
        raise GuardDenied(FAILURE)
    return proof


class DeviceTransportVerifier:
    def __init__(self, *, now_ms: Callable[[], int]):
        self._now_ms = now_ms
        self._timings: WeakKeyDictionary[DeviceIngressTiming, _Timing] = WeakKeyDictionary()
        self._admissions: WeakKeyDictionary[VerifiedDeviceAdmission, _Verified] = WeakKeyDictionary()
        self._lock = Lock()

    def capture(self, operation: str, raw: bytes, origin: str, *, owner: object) -> DeviceIngressTiming:
        return self._capture(operation, raw, origin, owner=owner, construction_owner=None)

    def capture_composition(self, operation: str, raw: bytes, origin: str, *, owner: object) -> DeviceIngressTiming:
        return self._capture(operation, raw, origin, owner=owner, construction_owner=owner)

    def _capture(self, operation: str, raw: bytes, origin: str, *, owner: object,
                 construction_owner: object | None) -> DeviceIngressTiming:
        started = monotonic()
        now_ms = self._now_ms()  # Pinned server clock; no caller-provided admission timestamp.
        proof = _proof(operation, raw, origin)
        if (type(now_ms) is not int or not 0 < now_ms <= 2**53 - 1 - PROCESSING_MS - WINDOW_MS
                or not proof.issued_at_ms <= now_ms < proof.issued_at_ms + WINDOW_MS
                or not math.isfinite(started)):
            raise GuardDenied(FAILURE)
        if operation in {"prepare", "inspect-key"}:
            # Body-contained public keys allow real signature refusal before
            # composition/IO. Native verification below still runs afresh.
            verify_signature(public_jwk(proof.key), "transport-request",
                proof.model_dump(mode="json", exclude={"signature"}), proof.signature)
        request = _Request(operation, origin, hashlib.sha256(raw).hexdigest(),
            str(proof.request_id), proof.issued_at_ms,
            str(proof.identity) if operation not in {"prepare", "inspect-key"} else None)
        ingress = object.__new__(DeviceIngressTiming)
        with self._lock:
            self._timings[ingress] = _Timing(request, owner, construction_owner,
                current_thread(), _task(), now_ms, started, now_ms, started)
        return ingress

    def _checked(self, ingress: DeviceIngressTiming, *, owner: object, operation: str,
                 raw: bytes, origin: str, now_ms: int) -> _Timing:
        if type(ingress) is not DeviceIngressTiming:
            raise GuardDenied(FAILURE)
        state = self._timings.get(ingress)
        current = monotonic()
        if (state is None or state.owner is not owner or state.thread is not current_thread()
                or state.task is not _task() or type(now_ms) is not int or not math.isfinite(current)
                or not state.last_ms <= now_ms < state.admitted_ms + PROCESSING_MS
                or not state.last_monotonic <= current < state.started_monotonic + PROCESSING_MS / 1000
                or state.request.operation != operation or state.request.origin != origin
                or type(raw) is not bytes or state.request.raw_sha256 != hashlib.sha256(raw).hexdigest()):
            raise GuardDenied(FAILURE)
        state.last_ms, state.last_monotonic = now_ms, current
        return state

    @staticmethod
    def _native(native, verifier) -> None:
        from .browser_pairing import NativeBrowserPairing
        if type(native) is not NativeBrowserPairing or native.device_transport is not verifier:
            raise GuardDenied(FAILURE)

    def bind_composed(self, ingress: DeviceIngressTiming, *, construction_owner: object,
                      native, operation: str, raw: bytes, origin: str) -> None:
        self._native(native, self)
        with self._lock:
            state = self._checked(ingress, owner=construction_owner, operation=operation,
                raw=raw, origin=origin, now_ms=self._now_ms())
            if state.construction_owner is not construction_owner or state.phase != "captured":
                raise GuardDenied(FAILURE)
            state.owner, state.construction_owner = native, None

    def check_timing(self, ingress: DeviceIngressTiming, *, native, operation: str,
                     raw: bytes, origin: str) -> None:
        self._native(native, self)
        with self._lock:
            self._checked(ingress, owner=native, operation=operation,
                raw=raw, origin=origin, now_ms=native.coordinator.now_ms())

    def verify(self, ingress: DeviceIngressTiming, *, native, operation: str,
               raw: bytes, origin: str) -> VerifiedDeviceAdmission:
        self._native(native, self)
        with self._lock:
            state = self._checked(ingress, owner=native, operation=operation,
                raw=raw, origin=origin, now_ms=native.coordinator.now_ms())
            if state.phase != "captured" or state.construction_owner is not None:
                raise GuardDenied(FAILURE)
            state.phase = "verifying"  # Interrupted/failed verification cannot reuse this capture.
        proof = _proof(operation, raw, origin)
        if operation in {"prepare", "inspect-key"}:
            native._device_origin(origin, extension_id=proof.extension_id)
            key = public_jwk(proof.key)
        else:
            native._device_origin(origin,
                device_id=str(proof.identity) if operation == "refresh-challenge" else None,
                pairing_id=str(proof.identity) if operation != "refresh-challenge" else None)
            self.check_timing(ingress, native=native, operation=operation, raw=raw, origin=origin)
            key = native._lookup_key(proof)
        self.check_timing(ingress, native=native, operation=operation, raw=raw, origin=origin)
        verify_signature(key, "transport-request", proof.model_dump(mode="json", exclude={"signature"}), proof.signature)
        with self._lock:
            current = self._checked(ingress, owner=native, operation=operation,
                raw=raw, origin=origin, now_ms=native.coordinator.now_ms())
            if current is not state or state.phase != "verifying":
                raise GuardDenied(FAILURE)
            state.phase = "verified"
            admission = object.__new__(VerifiedDeviceAdmission)
            self._admissions[admission] = _Verified(ingress, state, key_fingerprint(key))
        return admission

    def check(self, admission: VerifiedDeviceAdmission, *, native, operation: str,
              raw: bytes, origin: str, tx=None) -> _Request:
        self._native(native, self)
        if type(admission) is not VerifiedDeviceAdmission:
            raise GuardDenied(FAILURE)
        with self._lock:
            verified = self._admissions.get(admission)
            if verified is None:
                raise GuardDenied(FAILURE)
            state = self._checked(verified.ingress, owner=native, operation=operation,
                raw=raw, origin=origin, now_ms=native.coordinator.now_ms())
            if state is not verified.timing or state.phase != "verified":
                raise GuardDenied(FAILURE)
            request = state.request
        if tx is not None and request.identity is not None:
            # Fresh origin/key ownership in the nonce transaction, never the
            # earlier lookup's key as a cached allow decision.
            if operation == "refresh-challenge":
                row = tx.get("devices", request.identity)
                if row is None or row.get("active") is not True:
                    raise GuardDenied(FAILURE)
                key, extension_id = public_jwk(row["public_key"]), row["extension_id"]
            else:
                row = tx.get("pairing_requests", request.identity)
                if row is None:
                    raise GuardDenied(FAILURE)
                key, extension_id = public_jwk(row["request"]["public_key"]), row["request"]["extension_id"]
            if key_fingerprint(key) != verified.key_sha256 or origin != f"chrome-extension://{extension_id}":
                raise GuardDenied(FAILURE)
        return request

    def finish(self, handle, *, owner: object) -> None:
        with self._lock:
            if type(handle) is DeviceIngressTiming:
                state = self._timings.get(handle)
                if state is not None and state.owner is owner:
                    del self._timings[handle]
            elif type(handle) is VerifiedDeviceAdmission:
                verified = self._admissions.get(handle)
                if verified is not None and verified.timing.owner is owner:
                    del self._admissions[handle]
