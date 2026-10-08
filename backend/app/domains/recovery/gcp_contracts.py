"""Inert GCP adapter transport contracts, not production enablement evidence."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, Field, model_validator

from .contracts import Contract, Digest, Identifier, Timestamp, fingerprint
from .store import GuardUnavailable


class AmbiguousCommit(GuardUnavailable):
    """Commit was sent; no authorizing value may escape or be reconstructed."""


@dataclass(frozen=True)
class RpcSnapshot:
    path: str
    raw: bytes | None
    version: object | None


@dataclass(frozen=True)
class RpcWrite:
    path: str
    raw: bytes
    version: object | None


@dataclass(frozen=True)
class RpcCommit:
    write_count: int


def _int64_generation(value: str) -> str:
    if int(value) > 2**63 - 1:
        raise ValueError("Generation must fit the Storage int64 wire value")
    return value


Generation = Annotated[str, Field(pattern=r"^[1-9][0-9]{0,18}$"), AfterValidator(_int64_generation)]


class RegistryPin(Contract):
    database: Annotated[str, Field(pattern=r"^projects/[a-z][a-z0-9-]{4,62}/databases/[a-z][a-z0-9-]{0,62}$")]
    database_uid: UUID
    authority_id: UUID
    incarnation: Annotated[int, Field(strict=True, gt=0, le=2**53 - 1)]
    epoch_id: UUID


class OpeningHold(Contract):
    kind: Literal["opening_hold"] = "opening_hold"
    subject_uuid: UUID
    employer_key: Digest
    tenant_id: Identifier
    opening_key: Digest
    binding_sha256: Digest

    @property
    def record_key(self) -> str:
        # A fresh epoch, operation, device or binding cannot evade an old hold.
        return fingerprint({key: self.model_dump(mode="json")[key] for key in (
            "subject_uuid", "employer_key", "tenant_id", "opening_key",
        )})


class KeyOwnership(Contract):
    kind: Literal["key_ownership"] = "key_ownership"
    subject_uuid: UUID
    device_id: Identifier
    key_sha256: Digest

    @property
    def record_key(self) -> str:
        # Ownership is global to the key, independent of device/subject/epoch.
        return self.key_sha256


class DenyScope(Contract):
    kind: Literal["deny"] = "deny"
    subject_uuid: UUID
    scope: Literal["subject", "device", "approval", "grant", "artifact"]
    target: Identifier
    revision: Generation

    @property
    def record_key(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


SafetyEffect = Annotated[OpeningHold | KeyOwnership | DenyScope, Field(discriminator="kind")]


class JournalIntent(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    pin: RegistryPin
    effect: SafetyEffect
    created_at_ms: Timestamp
    deadline_ms: Timestamp

    @model_validator(mode="after")
    def bounded_lifetime(self) -> JournalIntent:
        if not self.created_at_ms < self.deadline_ms <= self.created_at_ms + 300_000:
            raise ValueError("Operation intent lifetime must be within five minutes")
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    @property
    def partition(self) -> str:
        return fingerprint({"subject_uuid": str(self.effect.subject_uuid)})


class JournalReceipt(Contract):
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^authority-intents/[a-f0-9]{64}/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    generation: Generation
    sha256: Digest


class WitnessPin(Contract):
    registry: RegistryPin
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^[A-Za-z0-9_/-]{1,200}$")]
    generation: Generation
    sha256: Digest


class WitnessBody(Contract):
    version: Literal[1] = 1
    registry: RegistryPin
    state: Literal["OPEN", "CLOSED"]
    manifest_generation: Generation
    manifest_sha256: Digest
    partitions_sha256: Digest


class OperationStatus(Contract):
    status: Literal["COMMITTED", "UNKNOWN"]
    operation_id: UUID
    intent_sha256: Digest
    journal: JournalReceipt | None = None

    @model_validator(mode="after")
    def receipt_only_on_commit(self) -> OperationStatus:
        if (self.status == "COMMITTED") != (self.journal is not None):
            raise ValueError("Only a committed status carries an exact journal receipt")
        return self
