"""Purpose-isolated email opt-out proofs; never account or send authority.

Issue only from a freshly checked native consent head. A valid token can request
disabling that exact generation; the native writer must still check current
account/mailbox/consent state. Its CAS, not this codec, makes redemption one-use.
Tokens belong in the confirmation page's fragment, then the POST body. GET does
not change preferences. Neither the plaintext token nor claims may be logged.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ..candidate_accounts.contracts import CandidateAccountIdentity
from ..recovery.store import GuardDenied

PURPOSE = b"hirewiz:weekly-digest-disable:v1"
MAX_LIFETIME_MS = 90 * 24 * 60 * 60 * 1000
MAX_TOKEN_BYTES = 4096
_VERIFIED = object()
_MAX_INTEGER = 2**53 - 1


def _deny() -> GuardDenied:
    return GuardDenied("Digest unsubscribe proof is unavailable or expired")


def _time(value: object) -> int:
    if type(value) is not int or not 0 < value <= _MAX_INTEGER:
        raise _deny()
    return value


class _Claims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True,
                              hide_input_in_errors=True, revalidate_instances="always")
    version: Literal[1] = 1
    purpose: Literal["weekly-digest-disable"] = "weekly-digest-disable"
    audience: Literal["hirewiz-notification-unsubscribe-v1"] = "hirewiz-notification-unsubscribe-v1"
    identity: CandidateAccountIdentity = Field(repr=False)
    email_sha256: str = Field(pattern=r"^[a-f0-9]{64}$", repr=False)
    expected_generation: int = Field(strict=True, gt=0, le=_MAX_INTEGER)
    policy_version: str = Field(pattern=r"^[A-Za-z0-9._:-]{1,80}$")
    issued_at_ms: int = Field(strict=True, gt=0, le=_MAX_INTEGER)
    expires_at_ms: int = Field(strict=True, gt=0, le=_MAX_INTEGER)
    proof_id: UUID

    @model_validator(mode="after")
    def bounded(self) -> _Claims:
        if not 0 < self.expires_at_ms - self.issued_at_ms <= MAX_LIFETIME_MS or self.proof_id.version != 4:
            raise ValueError("Bounded unsubscribe proof required")
        return self


def _canonical(claims: _Claims) -> bytes:
    return json.dumps(claims.model_dump(mode="json"), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False).encode("ascii")


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _decode(raw: str) -> bytes:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        raise _deny()
    try:
        value = base64.b64decode(raw + "=" * (-len(raw) % 4), altchars=b"-_", validate=True)
    except (ValueError, TypeError):
        raise _deny() from None
    if _encode(value) != raw:
        raise _deny()
    return value


def _unique_pairs(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("Duplicate field")
        value[key] = item
    return value


class _ProofSeal:
    """Retain the original verified values across ordinary dataclass copies."""

    __slots__ = ("_claims", "_proof_sha256")
    _claims: _Claims
    _proof_sha256: str

    def __init__(self, claims: _Claims, proof_sha256: str, marker: object):
        if marker is not _VERIFIED:
            raise _deny()
        object.__setattr__(self, "_claims", claims)
        object.__setattr__(self, "_proof_sha256", proof_sha256)

    def __setattr__(self, name, value):
        raise AttributeError("Verified proof seal is immutable")

    def matches(self, proof: VerifiedUnsubscribeProof) -> bool:
        claims = self._claims
        return (type(proof.identity) is CandidateAccountIdentity and proof.identity == claims.identity
            and proof.email_sha256 == claims.email_sha256
            and type(proof.expected_generation) is int and proof.expected_generation == claims.expected_generation
            and proof.policy_version == claims.policy_version
            and type(proof.issued_at_ms) is int and proof.issued_at_ms == claims.issued_at_ms
            and type(proof.expires_at_ms) is int and proof.expires_at_ms == claims.expires_at_ms
            and proof.proof_sha256 == self._proof_sha256)


@dataclass(frozen=True, slots=True, repr=False)
class VerifiedUnsubscribeProof:
    """Only a MAC-verified disable request, never a native acknowledgment."""

    identity: CandidateAccountIdentity
    email_sha256: str
    expected_generation: int
    policy_version: str
    issued_at_ms: int
    expires_at_ms: int
    proof_sha256: str
    _seal: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if type(self._seal) is not _ProofSeal or not self._seal.matches(self):
            raise _deny()

    def require_current(self, now_ms: int) -> None:
        now = _time(now_ms)
        if (type(self._seal) is not _ProofSeal or not self._seal.matches(self)
                or not self.issued_at_ms <= now < self.expires_at_ms):
            raise _deny()


class UnsubscribeTokens:
    """Injected private secret; no environment access or credentials at import."""

    def __init__(self, secret: bytes):
        if type(secret) is not bytes or len(secret) < 32:
            raise _deny()
        self._key = hmac.new(secret, PURPOSE, hashlib.sha256).digest()

    def issue(self, identity: CandidateAccountIdentity, *, email_sha256: str,
              expected_generation: int, policy_version: str, now_ms: int,
              lifetime_ms: int = MAX_LIFETIME_MS) -> str:
        now = _time(now_ms)
        if type(identity) is not CandidateAccountIdentity or type(lifetime_ms) is not int or not 0 < lifetime_ms <= MAX_LIFETIME_MS:
            raise _deny()
        try:
            claims = _Claims(identity=identity, email_sha256=email_sha256,
                expected_generation=expected_generation, policy_version=policy_version,
                issued_at_ms=now, expires_at_ms=now + lifetime_ms, proof_id=uuid4())
        except (ValidationError, ValueError, TypeError):
            raise _deny() from None
        payload = _encode(_canonical(claims))
        signature = _encode(hmac.new(self._key, b"hwdu1." + payload.encode("ascii"), hashlib.sha256).digest())
        return "hwdu1." + payload + "." + signature

    def verify(self, token: str, *, now_ms: int) -> VerifiedUnsubscribeProof:
        now = _time(now_ms)
        if type(token) is not str or not 1 <= len(token) <= MAX_TOKEN_BYTES or not token.isascii():
            raise _deny()
        try:
            prefix, payload, signature = token.split(".")
            if prefix != "hwdu1":
                raise _deny()
            expected = hmac.new(self._key, b"hwdu1." + payload.encode("ascii"), hashlib.sha256).digest()
            if not hmac.compare_digest(_decode(signature), expected):
                raise _deny()
            raw = _decode(payload)
            json.loads(raw, object_pairs_hook=_unique_pairs)
            claims = _Claims.model_validate_json(raw, strict=True)
            if _canonical(claims) != raw or not claims.issued_at_ms <= now < claims.expires_at_ms:
                raise _deny()
        except (ValidationError, ValueError, TypeError, UnicodeError, RecursionError):
            raise _deny() from None
        digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        seal = _ProofSeal(claims, digest, _VERIFIED)
        return VerifiedUnsubscribeProof(claims.identity, claims.email_sha256, claims.expected_generation,
            claims.policy_version, claims.issued_at_ms, claims.expires_at_ms,
            digest, seal)
