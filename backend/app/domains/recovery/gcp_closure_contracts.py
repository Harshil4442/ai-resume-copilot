"""Protected permanent closure and bounded CLOSED inventory, never opening authority."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from .contracts import Contract, Digest, Identifier, Timestamp, fingerprint
from .gcp_contracts import Generation, RegistryPin


def closure_path(pin: RegistryPin) -> str:
    return f"authority-closures/{fingerprint(pin.model_dump(mode='json'))}.json"


class ClosureContract(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True,
                              revalidate_instances="always")

    @field_validator("version", mode="before", check_fields=False)
    @classmethod
    def exact_version(cls, value: object) -> object:
        if type(value) is not int:
            raise ValueError("Closure version requires an exact integer")
        return value


class AuthorityCloseIntent(ClosureContract):
    version: Literal[1] = 1
    kind: Literal["password_denial_close"] = "password_denial_close"
    pin: RegistryPin
    close_id: UUID
    command_sha256: Digest
    created_at_ms: Timestamp

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


class AuthorityCloseReceipt(ClosureContract):
    body: AuthorityCloseIntent
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^authority-closures/[a-f0-9]{64}\.json$")]
    generation: Generation
    sha256: Digest

    @model_validator(mode="after")
    def exact(self) -> AuthorityCloseReceipt:
        if self.path != closure_path(self.body.pin) or self.sha256 != self.body.digest:
            raise ValueError("Close receipt does not bind the complete protected intent")
        return self


class NativeHeadCut(ClosureContract):
    sequence: Annotated[int, Field(strict=True, ge=0, le=2**53 - 1)]
    digest: Digest


class NativeClosedControl(ClosureContract):
    version: Literal[1] = 1
    kind: Literal["protected_denial_closed"] = "protected_denial_closed"
    pin: RegistryPin
    state: Literal["CLOSED"] = "CLOSED"
    close: AuthorityCloseReceipt
    head_at_close: NativeHeadCut

    @model_validator(mode="after")
    def exact(self) -> NativeClosedControl:
        if self.pin != self.close.body.pin:
            raise ValueError("Native closed control belongs to another protected pin")
        return self


class ClosedIntentReference(ClosureContract):
    path: Annotated[str, Field(pattern=r"^authority-intents/[a-f0-9]{64}/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    generation: Generation
    sha256: Digest
    byte_size: Annotated[int, Field(strict=True, gt=0, le=65_536)]
    partition: Digest
    kind: Literal["safety_effect", "pairing_command", "password_lifetime_effects"]


class ClosedInventoryManifest(ClosureContract):
    version: Literal[1] = 1
    kind: Literal["closed_inventory_only"] = "closed_inventory_only"
    control: NativeClosedControl
    head_while_closed: NativeHeadCut
    prefix: Annotated[str, Field(pattern=r"^authority-intents/[a-f0-9]{64}/$")]
    complete_prefix: Literal[True] = True
    projection_complete: Literal[False] = False
    entries: tuple[ClosedIntentReference, ...]
    partitions: tuple[Digest, ...]
    publication_cut: dict
    publication_resource: dict

    @field_validator("complete_prefix", "projection_complete", mode="before")
    @classmethod
    def exact_bool(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("Closed inventory status requires an exact boolean")
        return value

    @model_validator(mode="after")
    def bounded(self) -> ClosedInventoryManifest:
        from .gcp_publication_contracts import (
            PublicationReference,
            PublicationResourceReceipt,
            PublicationSealReceipt,
        )
        cut = PublicationSealReceipt.model_validate(self.publication_cut)
        refs = sorted((PublicationReference(operation_id=UUID(item.path.rsplit("/", 1)[1][:-5]),
            sha256=item.sha256, partition=item.partition, path=item.path, byte_size=item.byte_size, kind=item.kind)
            for item in self.entries), key=lambda v: str(v.operation_id))
        resource = PublicationResourceReceipt.model_validate(self.publication_resource)
        if (cut.pin != resource.pin or cut.pin.target != self.control.pin
                or cut.close_sha256 != fingerprint(self.control.close.model_dump(mode="json"))
                or cut.entries_sha256 != fingerprint({"entries": [v.model_dump(mode="json") for v in refs]})):
            raise ValueError("Inventory lacks its exact complete independently sealed publication cut")
        if len(self.entries) > 64 or sum(item.byte_size for item in self.entries) > 4_194_304:
            raise ValueError("Closed inventory exceeds its complete-cut budget")
        paths = tuple(item.path for item in self.entries)
        if paths != tuple(sorted(set(paths))) or any(not item.path.startswith(self.prefix) for item in self.entries):
            raise ValueError("Closed inventory paths are not unique and complete ordered references")
        if self.partitions != tuple(sorted({item.partition for item in self.entries})):
            raise ValueError("Closed inventory partition coverage disagrees")
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))


class ClosedInventoryReceipt(ClosureContract):
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^authority-closed-cuts/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    generation: Generation
    sha256: Digest
