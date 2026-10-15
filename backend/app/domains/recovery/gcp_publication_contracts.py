"""Disabled independently pinned full-publication admission contracts."""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from .contracts import Contract, Digest, Identifier, canonical, fingerprint
from .gcp_closure_contracts import AuthorityCloseReceipt
from .gcp_contracts import Generation, RegistryPin


class PublicationContract(Contract):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class PublicationPin(PublicationContract):
    registry: RegistryPin
    target: RegistryPin
    journal_bucket: Identifier

    @model_validator(mode="after")
    def separate(self) -> PublicationPin:
        if (
            self.registry.database == self.target.database
            or self.registry.database_uid == self.target.database_uid
        ):
            raise ValueError("Publication authority must be independently pinned")
        return self


def publication_resource_path(pin: PublicationPin) -> str:
    return f"authority-publication-resources/{fingerprint(pin.target.model_dump(mode='json'))}.json"


class PublicationResourceReceipt(PublicationContract):
    pin: PublicationPin
    bucket: Identifier
    generation: Generation
    sha256: Digest

    @model_validator(mode="after")
    def exact(self) -> PublicationResourceReceipt:
        if self.bucket != self.pin.journal_bucket or self.sha256 != fingerprint(
            self.pin.model_dump(mode="json")
        ):
            raise ValueError("Publication resource receipt does not bind its exact pin")
        return self


class PublicationReference(PublicationContract):
    operation_id: UUID
    sha256: Digest
    partition: Digest
    path: Annotated[
        str, Field(pattern=r"^authority-intents/[a-f0-9]{64}/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")
    ]
    byte_size: Annotated[int, Field(strict=True, gt=0, le=65_536)]
    kind: Literal["safety_effect", "pairing_command", "password_lifetime_effects"]


class PublicationGate(PublicationContract):
    pin: PublicationPin
    phase: Literal["OPEN", "DENY_ONLY", "SEALED"]
    entries: tuple[PublicationReference, ...] = ()
    close: AuthorityCloseReceipt | None = None
    seal_id: UUID | None = None

    @model_validator(mode="after")
    def exact(self) -> PublicationGate:
        ids = tuple(str(item.operation_id) for item in self.entries)
        if (
            len(ids) > 63
            or ids != tuple(sorted(set(ids)))
            or len({item.path for item in self.entries}) != len(ids)
        ):
            raise ValueError("Publication gate cut must be complete unique ordered and bounded")
        if (
            (self.phase == "OPEN") != (self.close is None)
            or (self.phase == "SEALED") != (self.seal_id is not None)
            or (self.close is not None and self.close.body.pin != self.pin.target)
        ):
            raise ValueError("Publication gate phase/close/seal pin disagrees")
        return self


class PublicationRecord(PublicationContract):
    reference: PublicationReference
    intent: dict

    @model_validator(mode="after")
    def exact(self) -> PublicationRecord:
        raw = canonical(self.intent).encode()
        if (
            len(raw) != self.reference.byte_size
            or fingerprint(self.intent) != self.reference.sha256
        ):
            raise ValueError("Publication record lacks its exact full canonical bytes")
        if (
            len(
                canonical(
                    {
                        "namespace": "publication_intents",
                        "key": str(self.reference.operation_id),
                        "value": self.model_dump(mode="json"),
                    }
                ).encode()
            )
            > 65_536
        ):
            raise ValueError("Publication full record and envelope exceed their total byte budget")
        return self


class PublicationSealReceipt(PublicationContract):
    pin: PublicationPin
    seal_id: UUID
    close_sha256: Digest
    entries_sha256: Digest
    path: Annotated[
        str, Field(pattern=r"^authority-publication-seals/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")
    ]
    generation: Generation
    sha256: Digest

    @model_validator(mode="after")
    def path_binding(self) -> PublicationSealReceipt:
        expected = f"authority-publication-seals/{fingerprint(self.pin.model_dump(mode='json'))}/{self.seal_id}.json"
        if self.path != expected:
            raise ValueError("Publication seal receipt path disagrees with its exact resource pin")
        return self
