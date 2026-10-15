"""Secret-free challenge-scoped signing consumption, never a signing credential."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import Contract, Digest, Identifier, Timestamp, fingerprint
from .gcp_contracts import Generation, RegistryPin
from .pairing_contracts import PositiveInteger
from .password_reauth import CandidateWebSession


class PasswordSigningReceipt(Contract):
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^authority-password-signing/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    generation: Generation
    sha256: Digest


class PasswordSigningMarker(Contract):
    version: Literal[1] = 1
    kind: Literal["password_signing_consumption"] = "password_signing_consumption"
    challenge_id: UUID
    attempt_id: UUID
    pin: RegistryPin
    epoch_generation: PositiveInteger
    context: CandidateWebSession
    operation: Literal["confirm_pairing", "revoke_device"]
    subject_uuid: UUID
    principal_sha256: Digest
    auth_generation: PositiveInteger
    challenge_sha256: Digest
    assertion_sha256: Digest
    nonce_sha256: Digest
    created_at_ms: Timestamp
    deadline_ms: Timestamp

    @model_validator(mode="after")
    def bounded(self) -> PasswordSigningMarker:
        if not self.created_at_ms < self.deadline_ms <= self.created_at_ms + 60_000:
            raise ValueError("Signing consumption requires a bounded assertion lifetime")
        return self


class PasswordSigningClaim(Contract):
    marker: PasswordSigningMarker
    receipt: PasswordSigningReceipt

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


class PasswordSigningReference(Contract):
    challenge_id: UUID
    claim_sha256: Digest
