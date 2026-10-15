"""Bounded safe-to-retain assertion data, never raw credential input."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from ..recovery.contracts import Contract, Digest, Timestamp
from ..recovery.gcp_contracts import RegistryPin

ISSUER: Literal["hirewiz_candidate_bff_v1"] = "hirewiz_candidate_bff_v1"
AUDIENCE: Literal["hirewiz:candidate-auth-private_v1"] = "hirewiz:candidate-auth-private_v1"
MAX_FRESHNESS_MS: Literal[5000] = 5_000
MAX_HEADER_BYTES = 2_048
HEADER = "x-hirewiz-candidate-ingress"
CANONICAL_OPERATIONS = frozenset(
    {
        ("GET", "/api/auth/candidate/v1/availability"),
        ("GET", "/api/auth/candidate/v1/session"),
        ("POST", "/api/auth/candidate/v1/register"),
        ("POST", "/api/auth/candidate/v1/login"),
        ("POST", "/api/auth/candidate/v1/registration-status"),
        ("POST", "/api/auth/candidate/v1/logout"),
        ("POST", "/api/auth/candidate/v1/web-logout"),
        ("POST", "/api/auth/candidate/v1/password"),
        ("POST", "/api/auth/register"),
        ("POST", "/api/auth/login"),
        ("POST", "/api/auth/delete-account"),
    }
)
KeyId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


def https_origin(value: str) -> str:
    try:
        p = urlsplit(value)
        if (
            p.scheme != "https"
            or not p.hostname
            or p.username
            or p.password
            or p.path
            or p.query
            or p.fragment
            or not value.isascii()
            or re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", p.hostname) is None
        ):
            raise ValueError
        port = p.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError
        expected = "https://" + p.hostname + (f":{port}" if port not in (None, 443) else "")
        if value != expected:
            raise ValueError
    except (TypeError, ValueError, UnicodeError):
        raise ValueError("An exact canonical HTTPS website origin is required") from None
    return value


class IngressAssertion(Contract):
    version: Literal[1] = 1
    issuer: Literal["hirewiz_candidate_bff_v1"] = ISSUER
    audience: Literal["hirewiz:candidate-auth-private_v1"] = AUDIENCE
    key_id: KeyId
    request_id: UUID
    method: Literal["GET", "POST"]
    path: str
    origin: str
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp
    body_commitment: Digest
    authorization_commitment: Digest

    @field_validator("request_id", mode="before")
    @classmethod
    def canonical_uuid(cls, value: object) -> object:
        if isinstance(value, UUID):
            return value
        if type(value) is not str or str(UUID(value)) != value:
            raise ValueError("A canonical request UUID is required")
        return value

    @field_validator("origin")
    @classmethod
    def origin_value(cls, value: str) -> str:
        return https_origin(value)

    @model_validator(mode="after")
    def exact_operation_and_lifetime(self) -> IngressAssertion:
        if (self.method, self.path) not in CANONICAL_OPERATIONS:
            raise ValueError("A canonical private candidate operation is required")
        if not self.issued_at_ms < self.expires_at_ms <= self.issued_at_ms + MAX_FRESHNESS_MS:
            raise ValueError("Private ingress expires within five seconds")
        return self


class IngressResource(Contract):
    version: Literal[1] = 1
    registry: RegistryPin
    action_registries: Annotated[tuple[RegistryPin, RegistryPin], Field(min_length=2, max_length=2)]
    origin: str
    key_id: KeyId
    custody_id: UUID
    restore_generation: Annotated[int, Field(strict=True, gt=0, le=2**53 - 1)]
    clock_witness_id: UUID
    max_freshness_ms: Literal[5000] = MAX_FRESHNESS_MS
    retention: Literal["NO_RUNTIME_PURGE_V1"] = "NO_RUNTIME_PURGE_V1"

    @field_validator("origin")
    @classmethod
    def origin_value(cls, value: str) -> str:
        return https_origin(value)

    @model_validator(mode="after")
    def independent_database(self) -> IngressResource:
        if any(
            p.database == self.registry.database or p.database_uid == self.registry.database_uid
            for p in self.action_registries
        ):
            raise ValueError("Ingress replay must use an independent pinned database")
        if (
            self.action_registries[0].database == self.action_registries[1].database
            or self.action_registries[0].database_uid == self.action_registries[1].database_uid
        ):
            raise ValueError("Both actual distinct action database pins are required")
        return self


class IngressControl(Contract):
    kind: Literal["candidate_private_ingress_control_v1"] = "candidate_private_ingress_control_v1"
    resource: IngressResource
    state: Literal["OPEN", "CLOSED"]


class RetainedIngress(Contract):
    kind: Literal["candidate_private_ingress_replay_v1"] = "candidate_private_ingress_replay_v1"
    resource: IngressResource
    assertion: IngressAssertion
    accepted_at_ms: Timestamp

    @model_validator(mode="after")
    def exact_retained_binding(self) -> RetainedIngress:
        if (
            self.assertion.key_id != self.resource.key_id
            or self.assertion.origin != self.resource.origin
            or not self.assertion.issued_at_ms <= self.accepted_at_ms < self.assertion.expires_at_ms
        ):
            raise ValueError("Retained ingress must bind its complete resource and fresh assertion")
        return self
