"""Disabled identity-only pairing contracts; no browser/action capabilities."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Digest, Identifier, Timestamp

JS_SAFE_MAX = 2**53 - 1
PositiveInteger = Annotated[int, Field(strict=True, gt=0, le=JS_SAFE_MAX)]
REQUEST_MS = 120_000
CANDIDATE_MS = 60_000
DEVICE_MS = 10_000
CLAIM_MS = 300_000


def _data(value: object) -> None:
    if value is None or type(value) is bool:
        return
    if type(value) is int and -JS_SAFE_MAX <= value <= JS_SAFE_MAX:
        return
    if type(value) is str:
        value.encode("utf-8", errors="strict")
        return
    if type(value) is list:
        for item in value:
            _data(item)
        return
    if type(value) is dict:
        for key, item in value.items():
            if type(key) is not str or not key.isascii():
                raise ValueError("Canonical object keys must be ASCII strings")
            _data(item)
        return
    raise ValueError("Only Unicode scalars, safe integers and JSON containers are canonical")


def canonical_bytes(kind: str, value: dict) -> bytes:
    if type(kind) is not str or re.fullmatch(r"[a-z][a-z0-9-]{0,63}", kind) is None:
        raise ValueError("A bounded canonical domain is required")
    if type(value) is not dict:
        raise ValueError("Canonical envelope must be an object")
    _data(value)
    return (f"hirewiz.pairing.{kind}.v2\n" + json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False,
    )).encode("utf-8")


def digest(kind: str, value: dict) -> str:
    return hashlib.sha256(canonical_bytes(kind, value)).hexdigest()


def nonce_digest(nonce: str) -> str:
    if type(nonce) is not str or str(UUID(nonce)) != nonce:
        raise ValueError("Nonce must be a canonical server UUID")
    return digest("nonce", {"nonce": nonce})


class PairingContract(Contract):
    @field_validator("protocol_version", mode="before", check_fields=False)
    @classmethod
    def exact_protocol(cls, value: object) -> object:
        if type(value) is not int or value != 2:
            raise ValueError("Exact protocol integer 2 is required")
        return value

    @model_validator(mode="before")
    @classmethod
    def exact_uuids(cls, value: object) -> object:
        if isinstance(value, dict):
            for name, field in cls.model_fields.items():
                if field.annotation is UUID and name in value:
                    item = value[name]
                    if isinstance(item, UUID):
                        continue
                    if type(item) is not str or str(UUID(item)) != item:
                        raise ValueError("UUID fields require canonical strings")
        return value


class CandidateAssertion(PairingContract):
    protocol_version: Literal[2]
    assertion_id: UUID
    issuer: Identifier
    audience: Literal["hirewiz:pairing-only"]
    operation: Literal["confirm_pairing", "revoke_device"]
    subject_uuid: UUID
    auth_generation: PositiveInteger
    principal_sha256: Digest
    session_id: UUID
    method: Literal["password_reauth", "provider_reauth"]
    authenticated_at_ms: Timestamp
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp
    binding_sha256: Digest
    confirmed: Literal[True]

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_confirmation(cls, value: object) -> object:
        if type(value) is not bool or value is not True:
            raise ValueError("Explicit true confirmation is required")
        return value

    @model_validator(mode="after")
    def interval(self) -> CandidateAssertion:
        if not (self.authenticated_at_ms <= self.issued_at_ms < self.expires_at_ms
                <= self.issued_at_ms + CANDIDATE_MS):
            raise ValueError("Recent authentication and assertion bounds are required")
        return self


class PairingRequest(PairingContract):
    protocol_version: Literal[2] = 2
    operation: Literal["create_pairing"] = "create_pairing"
    issuer: Literal["hirewiz-pairing"] = "hirewiz-pairing"
    audience: Literal["hirewiz:pairing-only"] = "hirewiz:pairing-only"
    operation_id: UUID
    pairing_id: UUID
    device_id: UUID
    authority_id: UUID
    authority_incarnation: PositiveInteger
    epoch_id: UUID
    epoch_generation: PositiveInteger
    extension_id: Identifier
    executor_revision: Identifier
    public_key: dict[str, str]
    key_sha256: Digest
    challenge_id: UUID
    nonce_sha256: Digest
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp

    @model_validator(mode="after")
    def interval(self) -> PairingRequest:
        if not self.issued_at_ms < self.expires_at_ms <= self.issued_at_ms + REQUEST_MS:
            raise ValueError("Pairing request exceeds its bound")
        return self

    @field_validator("public_key")
    @classmethod
    def checked_public_key(cls, value: dict) -> dict:
        # Local import avoids a crypto/contract initialization cycle.
        from .pairing_auth import public_jwk
        return public_jwk(value)

    @property
    def fingerprint(self) -> str:
        return digest("request", self.model_dump(mode="json"))


class DeviceClaim(PairingContract):
    protocol_version: Literal[2] = 2
    operation: Literal["device_identity"] = "device_identity"
    issuer: Literal["hirewiz-pairing"] = "hirewiz-pairing"
    audience: Literal["hirewiz:pairing-only"] = "hirewiz:pairing-only"
    claim_id: UUID
    device_id: UUID
    subject_uuid: UUID
    key_sha256: Digest
    key_generation: PositiveInteger
    auth_generation: PositiveInteger
    authority_id: UUID
    authority_incarnation: PositiveInteger
    epoch_id: UUID
    epoch_generation: PositiveInteger
    extension_id: Identifier
    executor_revision: Identifier
    event_sequence: PositiveInteger
    event_sha256: Digest
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp

    @model_validator(mode="after")
    def interval(self) -> DeviceClaim:
        if not self.issued_at_ms < self.expires_at_ms <= self.issued_at_ms + CLAIM_MS:
            raise ValueError("Identity claim exceeds its bound")
        return self
