"""Strict, data-minimal contracts for a disabled recovery-authority core.

AuthenticatedActor is supplied by a trusted authentication adapter, never a
client's role declaration. No production authentication/storage adapter exists.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]{1,160}$")]
Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Timestamp = Annotated[int, Field(strict=True, gt=0, le=2**53 - 1)]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AuthenticatedActor(Contract):
    actor_id: Identifier
    role: Literal["candidate", "device", "operator"]
    subject_uuid: UUID | None = None
    device_id: Identifier | None = None
    key_sha256: Digest
    executor_revision: Identifier

    @model_validator(mode="after")
    def role_scope(self) -> AuthenticatedActor:
        if self.role == "operator":
            if self.subject_uuid is not None or self.device_id is not None:
                raise ValueError("Operator identity must not impersonate a candidate/device")
        elif self.subject_uuid is None or (self.role == "device") != (self.device_id is not None):
            raise ValueError("Candidate/device subject and device scope are required")
        return self


class Binding(Contract):
    authority_id: UUID
    subject_uuid: UUID
    epoch_id: UUID
    epoch_generation: Annotated[int, Field(strict=True, gt=0)]
    application_id: Identifier
    employer_key: Digest
    tenant_id: Identifier
    opening_key: Digest
    approval_id: Identifier
    approval_revision: Annotated[int, Field(strict=True, gt=0)]
    admission_id: Identifier
    policy_sha256: Digest
    pricing_sha256: Digest
    artifact_sha256: Digest
    artifact_generation: Annotated[str, Field(pattern=r"^[1-9][0-9]{0,30}$")]
    package_digest: Digest
    review_digest: Digest
    grant_id: Identifier
    grant_revision: Annotated[int, Field(strict=True, gt=0)]
    device_id: Identifier
    device_key_sha256: Digest
    executor_revision: Identifier
    origin: str
    action: Literal["fill"]
    field_id: Identifier
    value_sha256: Digest
    deadline_ms: Timestamp

    @field_validator("origin")
    @classmethod
    def exact_https_origin(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
                    parsed.password or parsed.port not in {None, 443} or parsed.path or
                    parsed.query or parsed.fragment or value != f"https://{parsed.netloc}" or
                    any(character.isspace() for character in value)):
                raise ValueError("An exact HTTPS origin is required")
        except ValueError as exc:
            raise ValueError("An exact HTTPS origin is required") from exc
        return value

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    @property
    def opening_claim_key(self) -> str:
        # Epochs, devices, packages and fresh approvals cannot namespace away a
        # possible disclosure. This first slice permits one action per opening.
        return fingerprint({key: self.model_dump(mode="json")[key] for key in (
            "subject_uuid", "employer_key", "tenant_id", "opening_key",
        )})


class Revocation(Contract):
    subject_uuid: UUID
    kind: Literal["subject", "approval", "device", "grant", "artifact"]
    target: Identifier
    revision: Annotated[int, Field(strict=True, ge=0)]

    @property
    def key(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


class ReplayBarrier(Contract):
    authority_id: UUID
    sequence: Annotated[int, Field(strict=True, gt=0)]
    event_digest: Digest
    evidence_id: Identifier


class MayAct(Contract):
    """One winning local-core response, not a signed production capability."""
    decision_nonce: UUID
    permit_id: Identifier
    binding: Binding
    begun_sequence: Annotated[int, Field(strict=True, gt=0)]
    issued_at_ms: Timestamp
    expires_at_ms: Timestamp

    @model_validator(mode="after")
    def bounded_deadline(self) -> MayAct:
        if not self.issued_at_ms < self.expires_at_ms <= self.binding.deadline_ms:
            raise ValueError("Decision must remain inside the exact approval deadline")
        return self


class BeginDecision(Contract):
    status: Literal["BEGUN", "ALREADY_BEGUN", "UNKNOWN", "LOCAL_FILLED"]
    may_act: MayAct | None = None

    @model_validator(mode="after")
    def permission_only_for_winner(self) -> BeginDecision:
        if self.may_act is not None and self.status != "BEGUN":
            raise ValueError("Only the unique begin winner may receive a decision")
        return self


def canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)


def fingerprint(value: dict) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()
