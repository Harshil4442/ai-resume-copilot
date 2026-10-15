"""Detached crypto boundary; production identity verification/claim issuance are absent.

An explicitly pinned issuer can attest a synthetic recent-authentication assertion.
It cannot prove current subject/session state, consume a challenge or grant an action.
Those checks belong to the independently retained service transaction. No bearer,
environment, database or identity-provider fallback is supplied here.
"""
from __future__ import annotations

import base64
import re
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from pydantic import ValidationError

from .pairing_contracts import CandidateAssertion, canonical_bytes, digest
from .store import GuardDenied, GuardUnavailable

_B64URL = re.compile(r"^[A-Za-z0-9_-]+$")
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,160}$")
_DIGEST = re.compile(r"^[a-f0-9]{64}$")
_P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
_JS_SAFE_MAX = 2**53 - 1
_AUTH_MAX_MS = 60_000


def _decode(value: object, size: int) -> bytes:
    if type(value) is not str or not _B64URL.fullmatch(value):
        raise GuardDenied("Canonical unpadded base64url is required")
    if len(value) != (size * 8 + 5) // 6:
        raise GuardDenied("Incorrect encoded crypto size")
    raw = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    if len(raw) != size or base64.urlsafe_b64encode(raw).rstrip(b"=").decode() != value:
        raise GuardDenied("Noncanonical or incorrectly sized crypto bytes")
    return raw


def public_jwk(value: dict) -> dict:
    """Accept only canonical public P-256 coordinates on the actual curve."""
    if type(value) is not dict or set(value) != {"kty", "crv", "x", "y"}:
        raise GuardDenied("Exactly the public P-256 JWK fields are required")
    if any(type(item) is not str for item in value.values()):
        raise GuardDenied("Public JWK fields must be exact strings")
    if value["kty"] != "EC" or value["crv"] != "P-256":
        raise GuardDenied("Only EC P-256 public keys are supported")
    x, y = _decode(value["x"], 32), _decode(value["y"], 32)
    try:
        ec.EllipticCurvePublicNumbers(
            int.from_bytes(x, "big"), int.from_bytes(y, "big"), ec.SECP256R1()
        ).public_key()
    except ValueError as exc:
        raise GuardDenied("Public JWK is not a valid P-256 point") from exc
    return {key: value[key] for key in ("kty", "crv", "x", "y")}


def key_fingerprint(value: dict) -> str:
    return digest("public-key", public_jwk(value))


def _key(value: dict) -> ec.EllipticCurvePublicKey:
    jwk = public_jwk(value)
    return ec.EllipticCurvePublicNumbers(
        int.from_bytes(_decode(jwk["x"], 32), "big"),
        int.from_bytes(_decode(jwk["y"], 32), "big"),
        ec.SECP256R1(),
    ).public_key()


def _verify(key: ec.EllipticCurvePublicKey, kind: str, payload: dict, signature: str) -> None:
    raw = _decode(signature, 64)
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    if not 0 < r < _P256_ORDER or not 0 < s < _P256_ORDER:
        raise GuardDenied("Invalid ES256 signature scalars")
    if type(payload) is not dict:
        raise GuardDenied("An exact signed payload object is required")
    try:
        encoded = canonical_bytes(kind, payload)
        key.verify(encode_dss_signature(r, s), encoded, ec.ECDSA(hashes.SHA256()))
    except (InvalidSignature, TypeError, ValueError) as exc:
        raise GuardDenied("Detached ES256 signature or canonical payload is invalid") from exc


def verify_signature(jwk: dict, kind: str, payload: dict, signature: str) -> None:
    """Verify raw r||s ES256 over canonical domain-separated bytes, never a JWT.

    Valid high-S signatures are interoperable with WebCrypto. Replay identities must
    be operation/assertion/challenge IDs, never the malleable signature bytes.
    """
    _verify(_key(jwk), kind, payload, signature)


class AssertionVerifier(Protocol):
    def verify(self, envelope: dict, *, binding_sha256: str, operation: str,
               now_ms: int) -> CandidateAssertion: ...


class UnavailableAssertions:
    def verify(self, envelope: dict, *, binding_sha256: str, operation: str,
               now_ms: int) -> CandidateAssertion:
        raise GuardUnavailable("Production recent-candidate authentication is unavailable")


class PinnedAssertions:
    """Explicit injected trust only; no genuine password/provider adapter is provided."""

    def __init__(self, *, issuer: str, public_key: dict):
        if type(issuer) is not str or not _IDENTIFIER.fullmatch(issuer):
            raise GuardDenied("A fixed trusted assertion issuer is required")
        self._issuer = issuer
        self._key = _key(public_key)  # Freeze the checked key, not caller-owned JWK data.

    def verify(self, envelope: dict, *, binding_sha256: str, operation: str,
               now_ms: int) -> CandidateAssertion:
        if type(now_ms) is not int or not 0 < now_ms <= _JS_SAFE_MAX:
            raise GuardDenied("A current strict JS-safe millisecond clock is required")
        if (type(binding_sha256) is not str or not _DIGEST.fullmatch(binding_sha256)
                or type(operation) is not str
                or operation not in {"confirm_pairing", "revoke_device"}):
            raise GuardDenied("An exact trusted operation and binding are required")
        if type(envelope) is not dict or set(envelope) != {"payload", "signature"}:
            raise GuardDenied("Exactly the signed candidate assertion envelope is required")
        payload = envelope["payload"]
        if type(payload) is not dict or set(payload) != set(CandidateAssertion.model_fields):
            raise GuardDenied("All exact candidate assertion fields are required")
        _verify(self._key, "candidate-assertion", payload, envelope["signature"])
        try:
            assertion = CandidateAssertion.model_validate(payload)
        except (ValidationError, TypeError, ValueError) as exc:
            raise GuardDenied("Candidate assertion contract is invalid") from exc
        if (assertion.issuer != self._issuer or assertion.operation != operation
                or assertion.binding_sha256 != binding_sha256):
            raise GuardDenied("Candidate assertion issuer, operation or binding mismatch")
        authenticated = assertion.authenticated_at_ms
        issued, expires = assertion.issued_at_ms, assertion.expires_at_ms
        if not (authenticated <= issued <= now_ms < expires
                and 0 < expires - issued <= _AUTH_MAX_MS
                and now_ms - authenticated <= _AUTH_MAX_MS):
            raise GuardDenied("Candidate assertion is expired, future or insufficiently recent")
        return assertion


class ClaimIssuer(Protocol):
    def sign(self, payload: dict) -> dict: ...


class UnavailableClaims:
    def sign(self, payload: dict) -> dict:
        raise GuardUnavailable("Production pairing claim issuance is unavailable")


def production_assertions() -> AssertionVerifier:
    return UnavailableAssertions()


def production_claims() -> ClaimIssuer:
    return UnavailableClaims()
