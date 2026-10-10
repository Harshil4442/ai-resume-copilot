"""Typed, identity-only command receipts; not browser/action authority."""
from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import Contract, Digest, Identifier, Timestamp, fingerprint
from .gcp_contracts import Generation, JournalReceipt, RegistryPin
from .gcp_password_contracts import PasswordSigningReference
from .pairing_contracts import PositiveInteger

PairingCommand = Literal[
    "prepare_request", "create_request", "candidate_challenge", "confirm_candidate",
    "device_challenge", "complete_device", "refresh_challenge", "refresh_claim",
    "revocation_challenge", "revoke_device",
]
ID_COUNTS = {
    "prepare_request": 6, "create_request": 1, "candidate_challenge": 3,
    "confirm_candidate": 2, "device_challenge": 3, "complete_device": 2,
    "refresh_challenge": 3, "refresh_claim": 3, "revocation_challenge": 3,
    "revoke_device": 1,
}
NONCE_POSITIONS = {"prepare_request": (0,), "candidate_challenge": (0,),
                   "device_challenge": (0,), "refresh_challenge": (0,),
                   "revocation_challenge": (0,)}


class PairingJournalIntent(Contract):
    version: Literal[1] = 1
    kind: Literal["pairing_identity_command"] = "pairing_identity_command"
    operation_id: UUID
    pin: RegistryPin
    epoch_generation: PositiveInteger
    command: PairingCommand
    input_sha256: Digest
    allocated_ids: tuple[UUID, ...]
    nonce_commitments: tuple[Digest, ...]
    created_at_ms: Timestamp
    deadline_ms: Timestamp
    # Preserve existing v1 canonical intents when this explicit native port is unused.
    password_signing: PasswordSigningReference | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def exact_plan(self) -> PairingJournalIntent:
        secret_positions = NONCE_POSITIONS.get(self.command, ())
        if (len(self.allocated_ids) != ID_COUNTS[self.command] - len(secret_positions)
                or len(self.nonce_commitments) != len(secret_positions)
                or len(set(self.allocated_ids)) != len(self.allocated_ids)
                or self.operation_id in self.allocated_ids):
            raise ValueError("An exact unique preallocated command ID plan is required")
        if not self.created_at_ms < self.deadline_ms <= self.created_at_ms + 120_000:
            raise ValueError("Pairing command intent exceeds its bounded lifetime")
        if self.password_signing is not None and self.command not in {"confirm_candidate", "revoke_device"}:
            raise ValueError("Password signing is restricted to candidate consumption")
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    @property
    def partition(self) -> str:
        # No unverified subject declaration establishes an owner partition.
        return fingerprint({"pairing_identity_command": self.input_sha256})


class PairingExecution(Contract):
    status: Literal["COMMITTED", "UNKNOWN"]
    operation_id: UUID
    intent_sha256: Digest
    journal: JournalReceipt | None = None
    result: dict | None = None

    @model_validator(mode="after")
    def unknown_has_no_authority(self) -> PairingExecution:
        if self.status == "UNKNOWN" and (self.journal is not None or self.result is not None):
            raise ValueError("Unknown commit cannot return pairing authority")
        return self


class PairingAttemptReceipt(Contract):
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^authority-pairing-attempts/[a-f0-9]{64}/[a-f0-9-]{36}\.json$")]
    generation: Generation
    sha256: Digest


class _PairingAttemptBinding(Contract):
    version: Literal[1] = 1
    operation_id: UUID
    attempt_id: UUID
    pin: RegistryPin
    epoch_generation: PositiveInteger
    intent_sha256: Digest
    journal: JournalReceipt

    def matches(self, intent: PairingJournalIntent, receipt: JournalReceipt) -> bool:
        return (self.operation_id == intent.operation_id and self.pin == intent.pin
                and self.epoch_generation == intent.epoch_generation
                and self.intent_sha256 == intent.digest and self.journal == receipt)


class PairingAttemptMarker(_PairingAttemptBinding):
    """Secret-free protected attempt consumption before native admission."""

    kind: Literal["pairing_attempt_consumption"] = "pairing_attempt_consumption"


class PairingInvocationClaim(_PairingAttemptBinding):
    """Immutable attempt consumption, never a resume/dispatch credential."""

    kind: Literal["pairing_invocation_attempt"] = "pairing_invocation_attempt"
    marker: PairingAttemptReceipt

    def attempt_marker(self) -> PairingAttemptMarker:
        return PairingAttemptMarker.model_validate({
            **self.model_dump(mode="json", exclude={"marker"}), "kind": "pairing_attempt_consumption",
        })
