"""Exact candidate/device HTTP pairing over separately configured native authority.

The gateway assertion is not candidate reauthentication. It only selects a retained
server session. Password verification and independent consume occur in the existing
native password boundary. No SQL/bearer bootstrap, action permit or credit operation.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import math
import re
from dataclasses import dataclass
from threading import Lock, current_thread
from time import monotonic
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
from weakref import WeakKeyDictionary

from pydantic import ConfigDict, Field, ValidationError, field_validator

from .contracts import Digest, Identifier, Timestamp
from .gcp_buffer import BufferedTransaction
from .gcp_pairing import GcpPairingCoordinator, _PasswordReadTransaction
from .gcp_pairing_contracts import PairingCommand
from .pairing_auth import key_fingerprint, public_jwk, verify_signature
from .pairing_contracts import PairingContract, canonical_bytes
from .password_reauth import (
    CandidateWebSession,
    PasswordReauthService,
    RegisteredPasswordIdentity,
)
from .store import GuardDenied, GuardUnavailable

MAX_BODY = 8_192
MAX_RESPONSE = 16_384
WINDOW_MS = 5_000
PROCESSING_MS = 30_000
PREFIX = "/api/v1/browser-pairing"
FAILURE = "Browser pairing request was rejected"


class HttpContract(PairingContract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class GatewayAssertion(HttpContract):
    version: Literal[1]
    issuer: Identifier
    audience: Literal["hirewiz:browser-pairing-candidate"]
    request_id: UUID
    method: Literal["POST"]
    path: str = Field(pattern=r"^/api/v1/browser-pairing/candidate/(challenge|confirm|revoke-challenge|revoke)$")
    origin: str
    body_sha256: Digest
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp
    context: CandidateWebSession

    @field_validator("version", mode="before")
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int or value != 1:
            raise ValueError("Exact version required")
        return value


class PrepareRequest(HttpContract):
    protocol_version: Literal[2]
    operation: Literal["prepare_request"]
    key: dict[str, str]
    extension_id: Identifier
    revision: Identifier
    request_id: UUID
    issued_at_ms: Timestamp
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class KeyStatusRequest(HttpContract):
    protocol_version: Literal[2]
    operation: Literal["key_status"]
    key: dict[str, str]
    extension_id: str = Field(pattern=r"^[a-p]{32}$")
    revision: Identifier
    request_id: UUID
    issued_at_ms: Timestamp
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class DeviceLookup(HttpContract):
    protocol_version: Literal[2]
    operation: Literal["device_challenge", "status", "refresh_challenge"]
    identity: UUID
    request_id: UUID
    issued_at_ms: Timestamp
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class CreationProof(HttpContract):
    pairing_id: UUID
    nonce: UUID
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class ChallengeProof(HttpContract):
    challenge_id: UUID
    nonce: UUID
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")


class PairingSelection(HttpContract):
    pairing_id: UUID


class DeviceSelection(HttpContract):
    device_id: UUID


class VerifiedGatewayAdmission:
    """Opaque, process-local receipt; only its verifier can issue a usable one."""
    __slots__ = ("__weakref__",)

    def __new__(cls):
        raise TypeError("Gateway admissions are verifier-issued only")


@dataclass(repr=False)
class _Admission:
    envelope: GatewayAssertion
    owner: object
    thread: object
    task: object | None
    admitted_ms: int
    admitted_monotonic: float
    last_ms: int
    last_monotonic: float
    started: bool = False
    composition_owner: object | None = None


def _task():
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


def exact_json(raw: bytes) -> dict:
    """Reject duplicate keys, floats, oversized bodies and nonobject JSON without echo."""
    def pairs(items: list[tuple[str, object]]) -> dict:
        result: dict = {}
        for key, value in items:
            if key in result:
                raise GuardDenied(FAILURE)
            result[key] = value
        return result
    if type(raw) is not bytes or not 0 < len(raw) <= MAX_BODY:
        raise GuardDenied(FAILURE)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
            parse_float=lambda _: (_ for _ in ()).throw(GuardDenied(FAILURE)),
            parse_constant=lambda _: (_ for _ in ()).throw(GuardDenied(FAILURE)))
        if type(value) is not dict:
            raise GuardDenied(FAILURE)
        canonical_bytes("http-json", value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise GuardDenied(FAILURE) from None


class GatewayVerifier:
    def __init__(self, *, key: bytes, issuer: str, origin: str):
        if type(key) is not bytes or len(key) < 32 or len(key) > 128:
            raise GuardUnavailable("A dedicated gateway key is required")
        parsed = urlsplit(origin)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.path or parsed.query or parsed.fragment or origin != f"https://{parsed.netloc}"
                or any(c.isspace() for c in origin) or re.fullmatch(r"[A-Za-z0-9_-]{1,160}", issuer) is None):
            raise GuardUnavailable("A fixed HTTPS candidate origin is required")
        self._key, self.issuer, self.origin = key, issuer, origin
        self._admissions: WeakKeyDictionary[VerifiedGatewayAdmission, _Admission] = WeakKeyDictionary()
        self._admission_lock = Lock()

    def verify(self, encoded: str, signature: str, *, path: str, raw: bytes,
               now_ms: int) -> GatewayAssertion:
        if (type(encoded) is not str or not 0 < len(encoded) <= 2_048
                or type(signature) is not str or len(signature) != 64
                or any(c not in "0123456789abcdef" for c in signature)):
            raise GuardDenied(FAILURE)
        try:
            data = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if base64.urlsafe_b64encode(data).rstrip(b"=").decode() != encoded:
                raise GuardDenied(FAILURE)
            expected = hmac.new(self._key, b"hirewiz.browser-gateway.v1\n" + data,
                                hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise GuardDenied(FAILURE)
            assertion = GatewayAssertion.model_validate(exact_json(data))
            if (assertion.issuer != self.issuer or assertion.origin != self.origin
                    or assertion.path != path or assertion.body_sha256 != hashlib.sha256(raw).hexdigest()
                    or not assertion.issued_at_ms <= now_ms < assertion.expires_at_ms
                    or not 0 < assertion.expires_at_ms - assertion.issued_at_ms <= WINDOW_MS):
                raise GuardDenied(FAILURE)
            return assertion
        except (ValidationError, ValueError, TypeError, UnicodeError):
            raise GuardDenied(FAILURE) from None

    def admit(self, encoded: str, signature: str, *, owner: object, path: str,
              raw: bytes, now_ms: int) -> VerifiedGatewayAdmission:
        # This retains pairing's existing local wall-clock authority, not
        # ADR008's separately provider-anchored account-ingress clock.
        started = monotonic()
        envelope = self.verify(encoded, signature, path=path, raw=raw, now_ms=now_ms)
        exact_json(raw)
        if (type(now_ms) is not int or not 0 < now_ms <= 2**53 - 1 - PROCESSING_MS
                or envelope.expires_at_ms > 2**53 - 1 - PROCESSING_MS
                or not math.isfinite(started)):
            raise GuardDenied(FAILURE)
        admission = object.__new__(VerifiedGatewayAdmission)
        with self._admission_lock:
            self._admissions[admission] = _Admission(envelope, owner, current_thread(), _task(),
                now_ms, started, now_ms, started)
        return admission

    def admitted(self, admission: VerifiedGatewayAdmission, *, owner: object, path: str,
                 raw: bytes, now_ms: int, start: bool = False) -> GatewayAssertion:
        # Neither an unregistered object.__new__ handle nor a caller bool/time
        # grants authority. A handle is native-instance/thread/task/request bound.
        if type(admission) is not VerifiedGatewayAdmission:
            raise GuardDenied(FAILURE)
        current = monotonic()
        with self._admission_lock:
            state = self._admissions.get(admission)
            if (state is None or state.owner is not owner or state.thread is not current_thread()
                    or state.task is not _task() or (state.started if start else not state.started)):
                raise GuardDenied(FAILURE)
            if start:
                state.started = True  # Failed or interrupted starts cannot reuse this receipt.
            if (type(now_ms) is not int or not math.isfinite(current)
                    or not state.last_ms <= now_ms < state.admitted_ms + PROCESSING_MS
                    or not state.last_monotonic <= current < state.admitted_monotonic + PROCESSING_MS / 1000
                    or type(raw) is not bytes or state.envelope.path != path or state.envelope.method != "POST"
                    or state.envelope.body_sha256 != hashlib.sha256(raw).hexdigest()):
                raise GuardDenied(FAILURE)
            state.last_ms, state.last_monotonic = now_ms, current
            return state.envelope

    def admit_composition(self, encoded: str, signature: str, *, owner: object, path: str,
                          raw: bytes, now_ms: int) -> VerifiedGatewayAdmission:
        admission = self.admit(encoded, signature, owner=owner, path=path, raw=raw, now_ms=now_ms)
        with self._admission_lock:
            self._admissions[admission].composition_owner = owner
        return admission

    def bind_composed(self, admission: VerifiedGatewayAdmission, *, construction_owner: object,
                      native, path: str, raw: bytes, now_ms: int) -> None:
        # Only the original construction slot may bind this once to the exact
        # native instance sharing this verifier. No timestamp/bool is authority.
        if type(native) is not NativeBrowserPairing or native.gateway is not self:
            raise GuardDenied(FAILURE)
        if type(admission) is not VerifiedGatewayAdmission:
            raise GuardDenied(FAILURE)
        with self._admission_lock:
            state = self._admissions.get(admission)
            current = monotonic()
            if (state is None or state.owner is not construction_owner
                    or state.composition_owner is not construction_owner or state.started
                    or state.thread is not current_thread() or state.task is not _task()
                    or type(now_ms) is not int or not math.isfinite(current)
                    or not state.last_ms <= now_ms < state.admitted_ms + PROCESSING_MS
                    or not state.last_monotonic <= current < state.admitted_monotonic + PROCESSING_MS / 1000
                    or type(raw) is not bytes or state.envelope.path != path
                    or state.envelope.body_sha256 != hashlib.sha256(raw).hexdigest()):
                raise GuardDenied(FAILURE)
            state.owner, state.composition_owner = native, None
            state.last_ms, state.last_monotonic = now_ms, current

    def finish_admission(self, admission: VerifiedGatewayAdmission, *, owner: object) -> None:
        if type(admission) is not VerifiedGatewayAdmission:
            return
        with self._admission_lock:
            state = self._admissions.get(admission)
            if state is not None and state.owner is owner:
                del self._admissions[admission]


class NativeBrowserPairing:
    """One explicitly injected native coordinator, password boundary and gateway pin."""
    def __init__(self, coordinator: GcpPairingCoordinator, password: PasswordReauthService,
                 gateway: GatewayVerifier, *, paired_devices=None):
        if (type(coordinator) is not GcpPairingCoordinator
                or type(password) is not PasswordReauthService or password.pairing is not coordinator.core):
            raise GuardUnavailable("Connected native pairing/password boundaries are required")
        self.coordinator, self.password, self.gateway = coordinator, password, gateway
        self.paired_devices = paired_devices
        self.identity = RegisteredPasswordIdentity(coordinator.core.store)

    def _consume(self, namespace: str, request_id: str, *, context: CandidateWebSession | None,
                 issued: int, expires: int | None = None,
                 admission: VerifiedGatewayAdmission | None = None, operation: str = "", raw: bytes = b"") -> dict | None:
        captured: list[dict] = []
        c = self.coordinator

        def admitted(now: int) -> None:
            if admission is None:
                if context is not None:
                    raise GuardDenied(FAILURE)
                return
            envelope = self.gateway.admitted(admission, owner=self,
                path=f"{PREFIX}/candidate/{operation}", raw=raw, now_ms=now)
            if (namespace != "browser_gateway_requests" or str(envelope.request_id) != request_id
                    or envelope.context != context or envelope.issued_at_ms != issued
                    or envelope.expires_at_ms != expires):
                raise GuardDenied(FAILURE)

        def before_attempt():
            admitted(c.now_ms())
            c.fence.check(c.pin)
            admitted(c.now_ms())

        def transaction(tx: BufferedTransaction) -> None:
            c._control(tx)
            now = c.now_ms()
            deadline = issued + WINDOW_MS if expires is None else expires
            admitted(now)
            if admission is None and not issued <= now < deadline <= issued + WINDOW_MS:
                raise GuardDenied(FAILURE)
            if tx.get(namespace, request_id) is not None:
                raise GuardDenied(FAILURE)
            if context is not None:
                retained = self.identity.resolve(_PasswordReadTransaction(tx, c, context),
                    context, c.core, now)
                current = c.now_ms()
                admitted(current)
                if current != now:
                    # Repeat current-session/expiry checks over the same native
                    # transaction snapshot after its reads; no expected facts or
                    # stale identity are substituted for current account state.
                    fresh = self.identity.resolve(_PasswordReadTransaction(tx, c, context),
                        context, c.core, current)
                    if fresh != retained:
                        raise GuardDenied(FAILURE)
                admitted(c.now_ms())
                captured[:] = [retained]
                # Cover every invocation admitted within the ORIGINAL signed
                # window, including its maximum bounded processing lease. This
                # does not extend public assertion reuse or the human review.
                deadline += PROCESSING_MS
            # No body, password, assertion, cookie, signature or body digest is retained.
            tx.put(namespace, request_id, {"expires_at_ms": deadline}, immutable=True)
        c.registry.run(transaction, before_attempt=before_attempt)
        self.coordinator.fence.check(self.coordinator.pin)
        admitted(c.now_ms())
        return captured[0] if captured else None

    def _execute(self, command: PairingCommand, parameters: dict) -> dict:
        plan = self.coordinator.allocate(command, parameters)
        result = self.coordinator.execute(plan, parameters)
        if result.status != "COMMITTED" or result.result is None:
            return {"status": "UNKNOWN", "operation_id": str(result.operation_id),
                    "retry_allowed": False}
        if command == "complete_device" and self.paired_devices is not None:
            self.paired_devices.activate(self.coordinator.take_owned_ack(result))
        return result.result

    def candidate(self, operation: str, raw: bytes, assertion: str, signature: str) -> dict:
        admission = self.admit_candidate(operation, raw, assertion, signature)
        try:
            return self.candidate_admitted(operation, raw, admission)
        finally:
            self.gateway.finish_admission(admission, owner=self)

    def admit_candidate(self, operation: str, raw: bytes, assertion: str,
                        signature: str) -> VerifiedGatewayAdmission:
        return self.gateway.admit(assertion, signature, owner=self,
            path=f"{PREFIX}/candidate/{operation}", raw=raw, now_ms=self.coordinator.now_ms())

    def candidate_admitted(self, operation: str, raw: bytes, admission: VerifiedGatewayAdmission) -> dict:
        c = self.coordinator
        envelope = self.gateway.admitted(admission, owner=self,
            path=f"{PREFIX}/candidate/{operation}", raw=raw, now_ms=c.now_ms(), start=True)
        if self.paired_devices is not None:
            self.paired_devices.scoped.resolve(envelope.context)
        self.gateway.admitted(admission, owner=self, path=f"{PREFIX}/candidate/{operation}", raw=raw, now_ms=c.now_ms())
        data = exact_json(raw)
        if operation in {"challenge", "revoke-challenge"}:
            model = PairingSelection if operation == "challenge" else DeviceSelection
            selected = model.model_validate(data)
        elif operation in {"confirm", "revoke"}:
            # PasswordReauthService owns uniform secret-safe request validation.
            if data.get("operation") != ("confirm_pairing" if operation == "confirm" else "revoke_device"):
                raise GuardDenied(FAILURE)
        else:
            raise GuardDenied(FAILURE)
        identity = self._consume("browser_gateway_requests", str(envelope.request_id),
            context=envelope.context, issued=envelope.issued_at_ms, expires=envelope.expires_at_ms,
            admission=admission, operation=operation, raw=raw)
        assert identity is not None
        if operation in {"confirm", "revoke"}:
            result = self.password.execute(envelope.context, data)
        else:
            assert selected is not None
            parameters = {"subject_id": identity["subject_uuid"], "session_id": identity["session_id"]}
            if operation == "challenge":
                parameters["pairing_id"] = str(selected.model_dump()["pairing_id"])
                result = self._candidate_reply(self._execute("candidate_challenge", parameters))
            else:
                parameters["device_id"] = str(selected.model_dump()["device_id"])
                result = self._candidate_reply(self._execute("revocation_challenge", parameters))
        self.gateway.admitted(admission, owner=self, path=f"{PREFIX}/candidate/{operation}", raw=raw, now_ms=c.now_ms())
        return result

    @staticmethod
    def _candidate_reply(result: dict) -> dict:
        if result.get("status") == "UNKNOWN":
            return result
        payload = result["payload"]
        fields = ("challenge_id", "operation", "pairing_id", "device_id", "key_sha256",
                  "request_sha256", "expires_at_ms")
        return {**{key: payload[key] for key in fields if key in payload}, "nonce": result["nonce"]}

    def _lookup_key(self, proof: DeviceLookup) -> dict:
        c = self.coordinator
        found: list[dict] = []
        def read(tx: BufferedTransaction) -> None:
            c._control(tx)
            if proof.operation == "refresh_challenge":
                row = tx.get("devices", str(proof.identity))
                if row is None or row.get("active") is not True:
                    raise GuardDenied(FAILURE)
                found[:] = [public_jwk(row["public_key"])]
            else:
                row = tx.get("pairing_requests", str(proof.identity))
                if row is None:
                    raise GuardDenied(FAILURE)
                found[:] = [public_jwk(row["request"]["public_key"])]
        c.registry.run(read, before_attempt=lambda: c.fence.check(c.pin))
        c.fence.check(c.pin)
        return found[0]

    def _device_origin(self, origin: str, *, pairing_id: str | None = None,
                       device_id: str | None = None, challenge_id: str | None = None,
                       extension_id: str | None = None) -> None:
        if type(origin) is not str or re.fullmatch(r"chrome-extension://[a-p]{32}", origin) is None:
            raise GuardDenied(FAILURE)
        if extension_id is None:
            c = self.coordinator
            found: list[str] = []
            def read(tx: BufferedTransaction) -> None:
                c._control(tx)
                selected_pairing, selected_device = pairing_id, device_id
                if challenge_id is not None:
                    row = tx.get("pairing_challenges", challenge_id)
                    if row is None or row.get("kind") not in {"device", "refresh"}:
                        raise GuardDenied(FAILURE)
                    selected_pairing = row["payload"].get("pairing_id")
                    selected_device = row["payload"].get("device_id")
                if selected_pairing is not None:
                    row = tx.get("pairing_requests", selected_pairing)
                    if row is None:
                        raise GuardDenied(FAILURE)
                    found[:] = [row["request"]["extension_id"]]
                elif selected_device is not None:
                    row = tx.get("devices", selected_device)
                    if row is None:
                        raise GuardDenied(FAILURE)
                    found[:] = [row["extension_id"]]
                else:
                    raise GuardDenied(FAILURE)
            c.registry.run(read, before_attempt=lambda: c.fence.check(c.pin))
            c.fence.check(c.pin)
            extension_id = found[0]
        if origin != f"chrome-extension://{extension_id}":
            raise GuardDenied(FAILURE)

    def device(self, operation: str, raw: bytes, origin: str) -> dict:
        data = exact_json(raw)
        if operation == "inspect-key":
            inspection = KeyStatusRequest.model_validate(data)
            self._device_origin(origin, extension_id=inspection.extension_id)
            key = public_jwk(inspection.key)
            verify_signature(key, "transport-request", inspection.model_dump(mode="json", exclude={"signature"}), inspection.signature)
            if self.paired_devices is None:
                raise GuardUnavailable("Protected device key inspection is unavailable")
            self._consume("browser_device_requests", str(inspection.request_id), context=None,
                          issued=inspection.issued_at_ms)
            status = self.paired_devices.key_status(key, extension_id=inspection.extension_id,
                                                   executor_revision=inspection.revision)
            return {"protocol_version": 2, "key_sha256": key_fingerprint(key),
                    "extension_id": inspection.extension_id, "executor_revision": inspection.revision,
                    "status": status}
        if operation == "prepare":
            prepare = PrepareRequest.model_validate(data)
            self._device_origin(origin, extension_id=prepare.extension_id)
            key = public_jwk(prepare.key)
            verify_signature(key, "transport-request", prepare.model_dump(mode="json", exclude={"signature"}), prepare.signature)
            self._consume("browser_device_requests", str(prepare.request_id), context=None,
                          issued=prepare.issued_at_ms)
            return self._execute("prepare_request", {"key": key, "extension_id": prepare.extension_id,
                                                     "revision": prepare.revision})
        if operation == "create":
            creation = CreationProof.model_validate(data)
            self._device_origin(origin, pairing_id=str(creation.pairing_id))
            return self._execute("create_request", {"pairing_id": str(creation.pairing_id),
                "nonce": str(creation.nonce), "signature": creation.signature})
        if operation in {"complete", "refresh"}:
            challenge = ChallengeProof.model_validate(data)
            self._device_origin(origin, challenge_id=str(challenge.challenge_id))
            return self._execute("complete_device" if operation == "complete" else "refresh_claim",
                {"challenge_id": str(challenge.challenge_id), "nonce": str(challenge.nonce),
                 "signature": challenge.signature})
        lookup = DeviceLookup.model_validate(data)
        expected = {"challenge": "device_challenge", "status": "status", "refresh-challenge": "refresh_challenge"}
        if expected.get(operation) != lookup.operation:
            raise GuardDenied(FAILURE)
        self._device_origin(origin, device_id=str(lookup.identity) if operation == "refresh-challenge" else None,
                            pairing_id=str(lookup.identity) if operation != "refresh-challenge" else None)
        key = self._lookup_key(lookup)
        verify_signature(key, "transport-request", lookup.model_dump(mode="json", exclude={"signature"}), lookup.signature)
        self._consume("browser_device_requests", str(lookup.request_id), context=None,
                      issued=lookup.issued_at_ms)
        if operation == "status":
            return self.coordinator.pairing_status(str(lookup.identity))
        parameter = "device_id" if operation == "refresh-challenge" else "pairing_id"
        return self._execute(lookup.operation, {parameter: str(lookup.identity)})


class UnavailableBrowserPairing:
    def assistance_context(self, device_id, public_key):
        raise GuardUnavailable("Native assistance pairing is unavailable")

    def candidate(self, operation: str, raw: bytes, assertion: str, signature: str) -> dict:
        raise GuardUnavailable("Browser pairing is not available for this release")

    def device(self, operation: str, raw: bytes, origin: str) -> dict:
        raise GuardUnavailable("Browser pairing is not available for this release")


def production_browser_pairing():
    import os
    if os.environ.get("BROWSER_PAIRING_NATIVE_ENABLED") != "true":
        return UnavailableBrowserPairing()
    from .browser_pairing_production import production_browser_pairing as configured
    return configured()
