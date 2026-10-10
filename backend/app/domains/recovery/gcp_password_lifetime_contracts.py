"""Typed server-only lifetime plans and complete protected replay effects."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from .contracts import Contract, Digest, Timestamp, fingerprint
from .gcp_contracts import RegistryPin
from .pairing_contracts import PairingContract, PositiveInteger


class BindPasswordAccount(PairingContract):
    kind: Literal["bind_password_account"] = "bind_password_account"
    candidate_id: PositiveInteger
    account_binding_id: UUID
    subject_uuid: UUID
    principal_sha256: Digest


class EnrollPasswordAccount(PairingContract):
    """Fresh credential registration only; no SQL/email-derived past lifetime."""

    kind: Literal["enroll_password_account"] = "enroll_password_account"
    candidate_id: PositiveInteger
    account_binding_id: UUID
    subject_uuid: UUID
    principal_sha256: Digest
    credential_sha256: Digest
    subject_registration_event_id: UUID


class CreateVerifiedPasswordWebSession(PairingContract):
    kind: Literal["create_verified_password_web_session"] = "create_verified_password_web_session"
    account_binding_id: UUID
    session_id: UUID
    expected_auth_generation: PositiveInteger
    credential_sha256: Digest
    expires_at_ms: Timestamp


class CreatePasswordWebSession(PairingContract):
    kind: Literal["create_password_web_session"] = "create_password_web_session"
    account_binding_id: UUID
    session_id: UUID
    expected_auth_generation: PositiveInteger
    expires_at_ms: Timestamp


class RevokePasswordWebSession(PairingContract):
    kind: Literal["revoke_web_session"] = "revoke_web_session"
    account_binding_id: UUID
    session_id: UUID


class AdvancePasswordAuthGeneration(PairingContract):
    kind: Literal["advance_auth_generation"] = "advance_auth_generation"
    account_binding_id: UUID
    expected_auth_generation: PositiveInteger


class TombstonePasswordAccount(PairingContract):
    kind: Literal["tombstone_password_account"] = "tombstone_password_account"
    account_binding_id: UUID


class DeletePasswordSubject(PairingContract):
    kind: Literal["delete_subject"] = "delete_subject"
    account_binding_id: UUID
    subject_uuid: UUID


PasswordLifetimeCommand = Annotated[
    BindPasswordAccount | CreatePasswordWebSession | EnrollPasswordAccount | CreateVerifiedPasswordWebSession | RevokePasswordWebSession
    | AdvancePasswordAuthGeneration | TombstonePasswordAccount | DeletePasswordSubject,
    Field(discriminator="kind"),
]


class RegisteredSubject(PairingContract):
    active: Literal[True, False]
    registration_event_id: UUID
    principal_sha256: Digest
    auth_generation: PositiveInteger

    @field_validator("active", mode="before")
    @classmethod
    def exact_active(cls, value: object) -> object:
        if type(value) is not bool:
            raise ValueError("Subject activity requires an exact boolean")
        return value


class LifetimeRecord(Contract):
    namespace: Literal[
        "control", "pairing_control", "pairing_clock", "head", "events", "subjects", "revocations",
        "pairing_account_bindings", "pairing_account_tombstones", "pairing_web_sessions", "pairing_sessions",
        "pairing_subject_accounts", "pairing_sql_accounts", "pairing_web_session_tombstones",
        "pairing_auth_high_water", "password_lifetime_operations", "password_credential_revisions",
    ]
    key: Annotated[str, Field(min_length=1, max_length=200)]
    value: dict | None


class PasswordLifetimeEffects(Contract):
    """Full native preconditions, after-images and chained event; no digest-only replay."""

    before: tuple[LifetimeRecord, ...]
    after: tuple[LifetimeRecord, ...]
    event: dict

    @model_validator(mode="after")
    def bounded(self) -> PasswordLifetimeEffects:
        if not 1 <= len(self.before) <= 32 or not 1 <= len(self.after) <= 16:
            raise ValueError("Lifetime effects exceed their record budget")
        for values in (self.before, self.after):
            if len({(item.namespace, item.key) for item in values}) != len(values):
                raise ValueError("Lifetime records must be unique")
        if any(item.value is None for item in self.after):
            raise ValueError("Lifetime effects cannot delete retained records")
        return self


class PasswordLifetimeIntent(Contract):
    version: Literal[1] = 1
    kind: Literal["password_lifetime_effects"] = "password_lifetime_effects"
    operation_id: UUID
    event_id: UUID
    pin: RegistryPin
    epoch_generation: PositiveInteger
    command: PasswordLifetimeCommand
    subject_uuid: UUID
    effects: PasswordLifetimeEffects
    created_at_ms: Timestamp
    deadline_ms: Timestamp

    @model_validator(mode="after")
    def bounded(self) -> PasswordLifetimeIntent:
        if not self.created_at_ms < self.deadline_ms <= self.created_at_ms + 60_000:
            raise ValueError("Lifetime execution requires a bounded interval")
        if self.event_id == self.operation_id:
            raise ValueError("Operation and event identities must be distinct")
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    @property
    def partition(self) -> str:
        return fingerprint({"subject_uuid": str(self.subject_uuid)})


class LifetimeGrantEvidence(Contract):
    """Export of an original owned ACK, never a browser or detached status grant."""

    version: Literal[1] = 1
    intent: PasswordLifetimeIntent
    status: dict

    @model_validator(mode="after")
    def exact_committed(self) -> LifetimeGrantEvidence:
        from .gcp_contracts import OperationStatus
        status = OperationStatus.model_validate(self.status)
        if (status.status != "COMMITTED" or status.journal is None
                or status.operation_id != self.intent.operation_id
                or status.intent_sha256 != self.intent.digest
                or not isinstance(self.intent.command, (EnrollPasswordAccount, CreateVerifiedPasswordWebSession))):
            raise ValueError("Only exact original credential enrollment/session ACK evidence is accepted")
        return self
