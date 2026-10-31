"""Protocol-5 price and input contracts; no wallet, dispatch or native authority.

Protocol 4 remains the accepted legacy 20-service-credit contract. These types
describe a distinct three-unified-credit review, never enable its execution.
"""
from __future__ import annotations

import hashlib
import json
from typing import Annotated, Final, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

PROTOCOL: Final = 5
BILLING_VERSION: Final = "unified-browser-submission-v1"
INPUT_VERSION: Final = "verified-submission-input-v1"
TARIFF_VERSION: Final = "credit-services-2026-10-27-v1"
OPERATION: Final = "verified_submission"
CREDITS: Final = 3
FUNDING_CURRENCY: Final = "INR"
MAX_EXECUTION_MINOR: Final = 135

Digest = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Identifier = Annotated[str, Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")]
Milliseconds = Annotated[int, Field(strict=True, gt=0, le=2**53 - 1)]


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)


class PriceCore(Closed):
    billing_currency: Literal["credits"] = "credits"
    operation: Literal["verified_submission"] = "verified_submission"
    credits: Literal[3] = 3
    tariff_version: Literal["credit-services-2026-10-27-v1"] = TARIFF_VERSION
    route_id: Identifier
    route_version: Identifier
    delivery_contract_sha256: Digest
    financial_policy_sha256: Digest

    @field_validator("credits", mode="before")
    @classmethod
    def exact_credit_units(cls, value):
        if type(value) is not int or value != CREDITS:
            raise ValueError("Exactly three unified credits are required")
        return value


class SubmissionBilling(PriceCore):
    schema_version: Literal["unified-browser-submission-v1"] = BILLING_VERSION
    quote_id: Annotated[str, Field(pattern=r"^csq_[a-f0-9]{32}$")]
    input_fingerprint: Digest
    quote_contract_sha256: Digest
    quote_expires_at_ms: Milliseconds


class SubmissionApproval(Closed):
    identity: UUID
    review_digest: Digest
    confirmed: Literal[True]
    accepted_service_quote_id: Annotated[str, Field(pattern=r"^csq_[a-f0-9]{32}$")]

    @field_validator("confirmed", mode="before")
    @classmethod
    def exact_confirmation(cls, value):
        if value is not True:
            raise ValueError("Explicit confirmation is required")
        return value


class SubmissionInput(Closed):
    """Server-only preimage: no generated quote or final package digest cycle.

    Actual answers, forms, original file bytes and credentials stay in their
    owned stores. Their current hashes and exact recipient/version are bound.
    """
    schema_version: Literal["verified-submission-input-v1"] = INPUT_VERSION
    user_id: int = Field(strict=True, gt=0)
    application_id: Annotated[str, Field(min_length=1, max_length=64)]
    review_id: UUID
    device_id: UUID
    device_key_sha256: Digest
    credential_revision_sha256: Digest
    opening_key: Digest
    employer_key: Digest
    job_content_sha256: Digest
    form_sha256: Digest
    target_url: Annotated[str, Field(pattern=r"^https://job-boards\.greenhouse\.io/razorpaysoftwareprivatelimited/jobs/[0-9]+$")]
    resume_id: int = Field(strict=True, gt=0)
    resume_choice: Literal["original", "tailored", "custom"]
    resume_version_id: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    artifact_id: Annotated[str, Field(min_length=1, max_length=64)]
    artifact_sha256: Digest
    artifact_generation: Annotated[str, Field(pattern=r"^[1-9][0-9]*$", max_length=30)]
    reviewed_answers_sha256: Digest
    upload_evidence_sha256: Digest
    expected_application_sha256: Digest
    native_context_sha256: Digest
    context_digest: Digest
    document_id: Identifier
    price: PriceCore

    @model_validator(mode="after")
    def exact_resume_choice(self):
        if (self.resume_choice == "tailored") != (self.resume_version_id is not None):
            raise ValueError("Only tailored choice requires its exact version")
        return self


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def input_fingerprint(value: SubmissionInput) -> str:
    return digest(SubmissionInput.model_validate(value).model_dump(mode="json"))


def billing_digest(value: SubmissionBilling) -> str:
    return digest(SubmissionBilling.model_validate(value).model_dump(mode="json"))
