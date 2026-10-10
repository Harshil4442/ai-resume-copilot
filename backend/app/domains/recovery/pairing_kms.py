"""Exact-version Cloud KMS ES256; explicit injection, no production factory.

The adapter owns its TLS gRPC channel with retries disabled and bounded native
RPC deadlines. Only server-derived pairing schemas can reach either signer.
It does not authenticate a candidate, establish current authority, refresh a
credential, enroll a device, grant an action or fall back to a local key.
"""
from __future__ import annotations

import base64
import hashlib
import importlib.metadata
import json
import math
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field

import google_crc32c
import grpc
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from google.api_core.gapic_v1.method import wrap_method
from google.cloud import kms_v1
from pydantic import ValidationError

from .pairing_auth import public_jwk, verify_signature
from .pairing_contracts import (
    CANDIDATE_MS,
    JS_SAFE_MAX,
    CandidateAssertion,
    DeviceClaim,
    canonical_bytes,
)
from .store import GuardDenied, GuardUnavailable

_SDK_VERSION = "3.18.0"
_VERSION = re.compile(
    r"projects/(?P<project>[a-z][a-z0-9-]{4,28}[a-z0-9]|[1-9][0-9]{5,19})/"
    r"locations/(?P<location>[a-z][a-z0-9-]{1,62})/keyRings/[A-Za-z0-9_-]{1,63}/"
    r"cryptoKeys/[A-Za-z0-9_-]{1,63}/cryptoKeyVersions/[1-9][0-9]{0,18}"
)
_IDENTIFIER = re.compile(r"[A-Za-z0-9_-]{1,160}")
_TOKEN = re.compile(r"[A-Za-z0-9._~+/-]+={0,2}")
_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_CHANNEL_OPTIONS = (
    ("grpc.enable_retries", 0),
    ("grpc.enable_http_proxy", 0),
    ("grpc.max_send_message_length", 16_384),
    ("grpc.max_receive_message_length", 16_384),
)
_PROTECTION = {
    "SOFTWARE": kms_v1.ProtectionLevel.SOFTWARE,
    "HSM": kms_v1.ProtectionLevel.HSM,
}


@dataclass(frozen=True)
class KmsEs256Pin:
    version_name: str
    endpoint: str
    protection_level: str
    public_key: dict = field(repr=False)


def _checked_crc(value: object, raw: bytes) -> None:
    # Wrapper absence is None; a present CRC of zero is valid. No truthiness.
    if (type(value) is not int or not 0 <= value <= 0xFFFFFFFF
            or value != google_crc32c.value(raw)):
        raise GuardUnavailable("KMS CRC32C evidence is absent or disagrees")


def _raw64(der: bytes) -> str:
    if type(der) is not bytes or not 8 <= len(der) <= 72:
        raise GuardUnavailable("KMS returned an invalid bounded DER signature")
    try:
        r, s = decode_dss_signature(der)
        if (not 0 < r < _ORDER or not 0 < s < _ORDER
                or encode_dss_signature(r, s) != der):
            raise ValueError
    except ValueError:
        raise GuardUnavailable("KMS signature scalars or DER encoding are invalid") from None
    return base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).rstrip(b"=").decode()


class KmsEs256Boundary:
    """Reusable explicitly configured KMS boundary, not a caller-selected key.

    The short-lived OAuth token is supplied by a trusted runtime boundary.
    No ADC, token refresh, environment endpoint, mTLS fallback or supplied
    transport/channel is accepted. Create a new explicitly configured boundary
    when the external credential boundary replaces an expiring token.
    """

    def __init__(self, pin: KmsEs256Pin, *, access_token: str, rpc_timeout: float = 2.0,
                 deadline_seconds: float = 5.0, monotonic: Callable[[], float] = time.monotonic):
        if not isinstance(pin, KmsEs256Pin):
            raise GuardDenied("An operator-pinned exact KMS version is required")
        match = _VERSION.fullmatch(pin.version_name) if type(pin.version_name) is str else None
        if (match is None or type(pin.endpoint) is not str or type(pin.protection_level) is not str
                or pin.endpoint not in {"cloudkms.googleapis.com:443",
                f"{match.group('location')}-cloudkms.googleapis.com:443"}
                or pin.endpoint == "global-cloudkms.googleapis.com:443"
                or pin.protection_level not in _PROTECTION):
            raise GuardDenied("KMS resource, official endpoint and protection pin are invalid")
        if (type(access_token) is not str or not 1 <= len(access_token) <= 8192
                or _TOKEN.fullmatch(access_token) is None):
            raise GuardDenied("An explicit canonical short-lived KMS credential is required")
        for value in (rpc_timeout, deadline_seconds):
            if type(value) not in {int, float} or not math.isfinite(value) or not 0 < value <= 10:
                raise GuardDenied("KMS deadlines must be finite, positive and at most ten seconds")
        if importlib.metadata.version("google-cloud-kms") != _SDK_VERSION:
            raise GuardUnavailable("The exact reviewed Cloud KMS SDK version is required")
        jwk = public_jwk(pin.public_key)
        self._jwk = json.loads(json.dumps(jwk))
        coordinates = [int.from_bytes(base64.urlsafe_b64decode(jwk[name] + "="), "big") for name in ("x", "y")]
        checked = ec.EllipticCurvePublicNumbers(*coordinates, ec.SECP256R1()).public_key()
        self._spki = checked.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        self._name, self._protection = pin.version_name, _PROTECTION[pin.protection_level]
        self._rpc_timeout, self._budget, self._monotonic = float(rpc_timeout), float(deadline_seconds), monotonic
        # Explicit static call credentials cannot refresh or discover ADC during
        # an RPC. The owned channel disables gRPC transparent/service retries.
        credentials = grpc.composite_channel_credentials(
            grpc.ssl_channel_credentials(), grpc.access_token_call_credentials(access_token),
        )
        self._channel = grpc.secure_channel(pin.endpoint, credentials, options=_CHANNEL_OPTIONS)
        self._metadata = (("x-goog-request-params", f"name={self._name}"),)
        # Do not instantiate the generated KMS transport/client: SDK3.18.0's
        # logging interceptor records raw signing replies before validation.
        # These native bindings retain the exact SDK protobuf wire contract;
        # wrap_method adds only error/deadline handling, with no logging or retry.
        service = "/google.cloud.kms.v1.KeyManagementService/"
        self._get_public_key = wrap_method(self._channel.unary_unary(
            service + "GetPublicKey", request_serializer=kms_v1.GetPublicKeyRequest.serialize,
            response_deserializer=kms_v1.PublicKey.deserialize, _registered_method=True,
        ), default_retry=None, default_timeout=None, client_info=None)
        self._asymmetric_sign = wrap_method(self._channel.unary_unary(
            service + "AsymmetricSign", request_serializer=kms_v1.AsymmetricSignRequest.serialize,
            response_deserializer=kms_v1.AsymmetricSignResponse.deserialize, _registered_method=True,
        ), default_retry=None, default_timeout=None, client_info=None)

    def close(self) -> None:
        self._channel.close()

    def _remaining(self, deadline: float) -> float:
        now = self._monotonic()
        if type(now) not in {int, float} or not math.isfinite(now) or now >= deadline:
            raise GuardUnavailable("KMS operation deadline is exhausted")
        return min(self._rpc_timeout, deadline - now)

    def _public_identity(self, deadline: float) -> None:
        reply = self._get_public_key(
            kms_v1.GetPublicKeyRequest(name=self._name), retry=None,
            timeout=self._remaining(deadline), metadata=self._metadata,
        )
        if (not isinstance(reply, kms_v1.PublicKey) or reply.name != self._name
                or reply.algorithm != kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256
                or reply.protection_level != self._protection
                or type(reply.pem) is not str or not 0 < len(reply.pem.encode()) <= 4096):
            raise GuardUnavailable("KMS public-key version, algorithm or protection disagrees")
        raw = reply.pem.encode()
        _checked_crc(reply.pem_crc32c, raw)
        try:
            key = serialization.load_pem_public_key(raw)
            if (not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(key.curve, ec.SECP256R1)
                    or key.public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo) != self._spki):
                raise ValueError
        except (ValueError, TypeError):
            raise GuardUnavailable("KMS public key differs from the pinned P-256 identity") from None
        self._remaining(deadline)

    def _sign(self, kind: str, payload: dict) -> dict:
        if kind not in {"candidate-assertion", "device-claim"}:
            raise GuardDenied("KMS signer has no authority for this canonical domain")
        # Keep schema/audience protection at the reusable crypto boundary too;
        # endpoint wrappers add their fixed issuer and current lifetime checks.
        payload = _payload(kind, CandidateAssertion if kind == "candidate-assertion" else DeviceClaim, payload)
        message = canonical_bytes(kind, payload)
        if len(message) > 8192:
            raise GuardDenied("Pairing signing payload exceeds the byte budget")
        started = self._monotonic()
        if type(started) not in {int, float} or not math.isfinite(started):
            raise GuardUnavailable("A finite monotonic signing clock is required")
        deadline = started + self._budget
        try:
            self._public_identity(deadline)
            hashed = hashlib.sha256(message).digest()
            reply = self._asymmetric_sign(kms_v1.AsymmetricSignRequest({
                "name": self._name, "digest": {"sha256": hashed},
                "digest_crc32c": google_crc32c.value(hashed),
            }), retry=None, timeout=self._remaining(deadline), metadata=self._metadata)
            if (not isinstance(reply, kms_v1.AsymmetricSignResponse) or reply.name != self._name
                    or reply.verified_digest_crc32c is not True or reply.protection_level != self._protection):
                raise GuardUnavailable("KMS signature version, digest integrity or protection disagrees")
            _checked_crc(reply.signature_crc32c, reply.signature)
            signature = _raw64(reply.signature)
            verify_signature(self._jwk, kind, payload, signature)
            self._remaining(deadline)
            return {"payload": payload, "signature": signature}
        except Exception:
            # No RPC/body/credential text escapes. Failure/lost reply never falls
            # back to a private key and never retries the sign operation.
            pass
        # Raise outside the except block, so even the exception's context/cause
        # graph does not retain an RPC error containing provider response data.
        raise GuardUnavailable("KMS returned no verified pairing signature")


def _payload(kind: str, schema: type[CandidateAssertion] | type[DeviceClaim], payload: dict) -> dict:
    if type(payload) is not dict or set(payload) != set(schema.model_fields):
        raise GuardDenied("Exactly the complete server-derived pairing payload is required")
    try:
        raw = canonical_bytes(kind, payload)
        if len(raw) > 8192:
            raise ValueError
        frozen = json.loads(raw.split(b"\n", 1)[1])
        validated = schema.model_validate(frozen).model_dump(mode="json")
        if canonical_bytes(kind, validated) != raw:
            raise ValueError
        return frozen
    except (TypeError, ValueError, ValidationError, UnicodeError):
        pass
    raise GuardDenied("Pairing payload schema or exact canonical representation is invalid")


def _current(payload: dict, now_ms: Callable[[], int], *, assertion: bool) -> None:
    now = now_ms()
    if (type(now) is not int or not 0 < now <= JS_SAFE_MAX
            or not payload["issued_at_ms"] <= now < payload["expires_at_ms"]
            or (assertion and now - payload["authenticated_at_ms"] > CANDIDATE_MS)):
        raise GuardDenied("Pairing signature is outside its current authentication or claim lifetime")


class KmsCandidateAssertionIssuer:
    """Trusted recent-auth endpoint only; never accepts client identity claims."""

    def __init__(self, boundary: KmsEs256Boundary, *, issuer: str, now_ms: Callable[[], int]):
        if type(issuer) is not str or _IDENTIFIER.fullmatch(issuer) is None:
            raise GuardDenied("A fixed candidate-authentication issuer is required")
        self._boundary, self._issuer, self._now = boundary, issuer, now_ms

    def sign(self, payload: dict) -> dict:
        frozen = _payload("candidate-assertion", CandidateAssertion, payload)
        if frozen["issuer"] != self._issuer:
            raise GuardDenied("Candidate assertion belongs to another trusted issuer")
        _current(frozen, self._now, assertion=True)
        envelope = self._boundary._sign("candidate-assertion", frozen)
        _current(frozen, self._now, assertion=True)
        return envelope


class KmsPairingClaimIssuer:
    """Actual PairingService ClaimIssuer seam; identity-only, no fill authority."""

    def __init__(self, boundary: KmsEs256Boundary, *, now_ms: Callable[[], int]):
        self._boundary, self._now = boundary, now_ms

    def sign(self, payload: dict) -> dict:
        frozen = _payload("device-claim", DeviceClaim, payload)
        _current(frozen, self._now, assertion=False)
        envelope = self._boundary._sign("device-claim", frozen)
        _current(frozen, self._now, assertion=False)
        return envelope
