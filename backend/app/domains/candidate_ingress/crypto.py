"""Separate keyed commitments; no public low-entropy credential-body digest."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from uuid import UUID

from ..recovery.contracts import canonical
from ..recovery.store import GuardDenied
from .contracts import MAX_HEADER_BYTES, IngressAssertion, IngressResource

_BODY_DOMAIN = b"hirewiz:candidate-auth-private_v1:body\x00"
_AUTH_DOMAIN = b"hirewiz:candidate-auth-private_v1:authorization\x00"
_MAC_DOMAIN = b"hirewiz:candidate-auth-private_v1:assertion\x00"


def _key(master: bytes, domain: bytes) -> bytes:
    return hmac.digest(master, b"hirewiz:candidate-auth-private_v1:key\x00" + domain, "sha256")


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError
        value[key] = child
    return value


def _constant(_: str) -> object:
    raise ValueError


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    if not value or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None:
        raise ValueError
    result = base64.urlsafe_b64decode(value + "=" * ((4 - len(value) % 4) % 4))
    if _b64(result) != value:
        raise ValueError
    return result


@dataclass(frozen=True, repr=False)
class IngressKey:
    key_id: str
    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if (
            type(self.secret) is not bytes
            or len(self.secret) != 32
            or type(self.key_id) is not str
            or re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.key_id) is None
        ):
            raise GuardDenied("A distinct exact private ingress signing key is required")

    def __repr__(self) -> str:
        return "IngressKey(<private>)"

    def commitment(self, request_id: UUID, raw: bytes, *, authorization: bool = False) -> str:
        if type(raw) is not bytes or len(raw) > 8192 or not isinstance(request_id, UUID):
            raise GuardDenied("Private ingress input is malformed")
        domain = _AUTH_DOMAIN if authorization else _BODY_DOMAIN
        return hmac.digest(
            _key(self.secret, domain),
            domain + str(request_id).encode("ascii") + b"\x00" + raw,
            "sha256",
        ).hex()

    def header(self, assertion: IngressAssertion) -> str:
        if assertion.key_id != self.key_id:
            raise GuardDenied("Private ingress key binding is invalid")
        raw = canonical(assertion.model_dump(mode="json")).encode("utf8")
        signature = hmac.digest(_key(self.secret, _MAC_DOMAIN), _MAC_DOMAIN + raw, "sha256")
        return _b64(raw) + "." + _b64(signature)

    def verify(
        self,
        header: str,
        *,
        resource: IngressResource,
        method: str,
        path: bytes,
        raw: bytes,
        authorization: bytes,
        now_ms: int,
    ) -> IngressAssertion:
        try:
            if (
                type(header) is not str
                or not header.isascii()
                or not 0 < len(header) <= MAX_HEADER_BYTES
                or type(now_ms) is not int
                or type(path) is not bytes
            ):
                raise ValueError
            parts = header.split(".")
            if len(parts) != 2:
                raise ValueError
            payload, signature = _decode(parts[0]), _decode(parts[1])
            if len(signature) != hashlib.sha256().digest_size:
                raise ValueError
            expected = hmac.digest(_key(self.secret, _MAC_DOMAIN), _MAC_DOMAIN + payload, "sha256")
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            data = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_constant)
            if type(data) is not dict or canonical(data).encode() != payload:
                raise ValueError
            assertion = IngressAssertion.model_validate(data)
            # Literal[1] also accepts True in some validation modes: canonical
            # bytes must equal the fully validated exact representation.
            if canonical(assertion.model_dump(mode="json")).encode() != payload:
                raise ValueError
            if (
                assertion.key_id != self.key_id
                or assertion.key_id != resource.key_id
                or assertion.origin != resource.origin
                or assertion.method != method
                or assertion.path.encode("ascii") != path
                or not assertion.issued_at_ms <= now_ms < assertion.expires_at_ms
                or not hmac.compare_digest(
                    assertion.body_commitment, self.commitment(assertion.request_id, raw)
                )
                or not hmac.compare_digest(
                    assertion.authorization_commitment,
                    self.commitment(assertion.request_id, authorization, authorization=True),
                )
            ):
                raise ValueError
            return assertion
        except (ValueError, TypeError, UnicodeError, RecursionError, GuardDenied):
            # Never reflect the header, body, validation error or signature.
            raise GuardDenied("Private candidate transport authentication failed") from None
