"""Distinct exact final-submit contracts; legacy fill bindings remain unchanged."""

from __future__ import annotations

import re
from typing import Annotated, Literal
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from .contracts import Contract, Digest, Identifier, Timestamp, fingerprint
from .gcp_contracts import Generation, RegistryPin
from .pairing_contracts import PositiveInteger


class SubmitContract(Contract):
    model_config = ConfigDict(
        extra="forbid", frozen=True, hide_input_in_errors=True, revalidate_instances="always"
    )


class SubmitContext(SubmitContract):
    """Server-resolved expected credential revision, never a manufactured session."""

    subject_uuid: UUID
    account_binding_id: UUID
    candidate_id: PositiveInteger
    principal_sha256: Digest
    auth_generation: PositiveInteger
    credential_sha256: Digest


class SubmitBinding(SubmitContract):
    action: Literal["submit"] = "submit"
    candidate_id: PositiveInteger
    device_id: UUID
    device_key_sha256: Digest
    device_key_generation: PositiveInteger
    executor_revision: Identifier
    review_id: UUID
    review_digest: Digest
    application_id: Identifier
    target_url: Annotated[str, Field(max_length=2048)]
    employer_key: Digest
    tenant_id: Literal["razorpaysoftwareprivatelimited"] = "razorpaysoftwareprivatelimited"
    opening_key: Digest
    artifact_id: Identifier
    artifact_sha256: Digest
    artifact_generation: Generation
    package_digest: Digest
    policy_sha256: Digest
    pricing_sha256: Digest
    admission_id: Identifier
    admission_digest: Digest
    upload_evidence_sha256: Digest
    context_digest: Digest
    document_id: Identifier
    approved_at_ms: Timestamp
    review_expires_at_ms: Timestamp
    issued_at_ms: Timestamp
    deadline_ms: Timestamp

    @field_validator("target_url")
    @classmethod
    def exact_destination(cls, value: str) -> str:
        if (
            re.fullmatch(
                r"https://job-boards\.greenhouse\.io/razorpaysoftwareprivatelimited/jobs/[0-9]+",
                value,
            )
            is None
        ):
            raise ValueError("Exact supported HTTPS employer opening is required")
        return value

    @model_validator(mode="after")
    def interval(self) -> SubmitBinding:
        if not (
            self.approved_at_ms
            <= self.issued_at_ms
            < self.deadline_ms
            <= min(self.review_expires_at_ms, self.issued_at_ms + 10_000)
        ):
            raise ValueError("Final submit needs exact approval and its original short deadline")
        return self

    @property
    def digest(self) -> str:
        return fingerprint(self.model_dump(mode="json"))

    def opening_claim_key(self, subject_uuid: UUID) -> str:
        # Review, device, package, operation and epoch cannot namespace a resend.
        return fingerprint(
            {
                "subject_uuid": str(subject_uuid),
                "employer_key": self.employer_key,
                "tenant_id": self.tenant_id,
                "opening_key": self.opening_key,
            }
        )


class SubmitDeviceGrant(SubmitContract):
    """Operator-owned pinned enrollment authorization; not a browser declaration."""

    version: Literal[1] = 1
    kind: Literal["submit_device_enrollment"] = "submit_device_enrollment"
    pin: RegistryPin
    epoch_generation: PositiveInteger
    context: SubmitContext
    device_id: UUID
    public_key: dict[str, str]
    key_sha256: Digest
    key_generation: PositiveInteger
    executor_revision: Identifier
    enrollment_provenance_sha256: Digest

    @model_validator(mode="after")
    def exact_key(self) -> SubmitDeviceGrant:
        from .pairing_auth import key_fingerprint, public_jwk

        key = public_jwk(self.public_key)
        if key != self.public_key or key_fingerprint(key) != self.key_sha256:
            raise ValueError("Operator enrollment must bind an exact public device key")
        return self


class SubmitGrantPin(SubmitContract):
    bucket: Identifier
    path: Annotated[str, Field(pattern=r"^authority-submit-device-grants/[a-f0-9-]{36}\.json$")]
    generation: Generation
    sha256: Digest


class SubmitDeviceRegistration(SubmitContract):
    operation_id: UUID
    grant: SubmitDeviceGrant
    grant_pin: SubmitGrantPin

    @model_validator(mode="after")
    def exact_grant(self) -> SubmitDeviceRegistration:
        if (
            self.grant_pin.path != f"authority-submit-device-grants/{self.grant.device_id}.json"
            or self.grant_pin.sha256 != fingerprint(self.grant.model_dump(mode="json"))
        ):
            raise ValueError("Device registration requires the exact pinned full operator grant")
        return self
