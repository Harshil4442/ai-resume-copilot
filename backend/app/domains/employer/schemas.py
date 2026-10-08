from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SearchCreate(StrictModel):
    resume_id: int = Field(gt=0)
    role: str = Field(min_length=2, max_length=200)
    location: str = Field(default="", max_length=200)
    desired_count: int = Field(default=10, ge=1, le=100)
    remote_only: bool = False
    published_within_days: int | None = Field(default=None, ge=1, le=365)
    excluded_employers: list[str] = Field(default_factory=list, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class ResumeSelection(StrictModel):
    resume_id: int = Field(gt=0)
    resume_choice: Literal["original", "tailored", "custom"] = "original"
    resume_version_id: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def version_matches_choice(self):
        if (self.resume_choice == "tailored") != bool(self.resume_version_id):
            raise ValueError("Only tailored resume selection requires a resume_version_id")
        return self


class ApplicationCreate(ResumeSelection):
    posting_id: str = Field(min_length=3, max_length=64)
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")


class PackageUpdate(ResumeSelection):
    answers: dict[str, str | list[str]] = Field(default_factory=dict, max_length=200)
    consents: dict[str, bool] = Field(default_factory=dict, max_length=100)

    @field_validator("answers")
    @classmethod
    def bounded_answers(cls, values):
        for key, value in values.items():
            if len(key) > 160:
                raise ValueError("Answer field identifiers must be at most 160 characters")
            items = value if isinstance(value, list) else [value]
            if len(items) > 100 or any(len(item) > 10_000 for item in items):
                raise ValueError("Answer values exceed the supported size")
        return values


class ApprovalCreate(StrictModel):
    package_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    allowed_actions: list[Literal["fill", "upload", "submit"]] = Field(min_length=1, max_length=3)


class ExecuteCreate(StrictModel):
    package_digest: str = Field(pattern=r"^[a-f0-9]{64}$")


class EmployerAdmissionPolicy(StrictModel):
    version: str = Field(min_length=3, max_length=80)
    daily_limit: int = Field(ge=1, le=1000)
    rolling_limit: int = Field(ge=1, le=1000)
    rolling_days: int = Field(ge=1, le=365)
    evidence_url: str = Field(min_length=12, max_length=2000, pattern=r"^https://")
    evidence_note: str = Field(min_length=20, max_length=1000)


class BatchItem(ApprovalCreate):
    application_id: str = Field(min_length=3, max_length=64)


class BatchCreate(StrictModel):
    items: list[BatchItem] = Field(min_length=1, max_length=100)
    max_total_credits: int = Field(ge=1, le=100_000)
    idempotency_key: str = Field(min_length=8, max_length=160, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def unique_applications(self):
        if len({item.application_id for item in self.items}) != len(self.items):
            raise ValueError("Each application may appear only once in a batch")
        return self


class SourceCreate(StrictModel):
    employer: str = Field(min_length=2, max_length=200)
    employer_key: str | None = Field(default=None, min_length=3, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")
    admission_policy: EmployerAdmissionPolicy | None = None
    platform: Literal["greenhouse", "lever", "ashby", "smartrecruiters", "workable", "personio", "pinpoint"]
    board_token: str = Field(min_length=1, max_length=120, pattern=r"^[A-Za-z0-9_-]+$")
    region: Literal["global", "eu"] = "global"
    careers_url: str = Field(min_length=12, max_length=2000)
    allowed_hosts: list[str] = Field(min_length=1, max_length=20)
    verification_url: str = Field(min_length=12, max_length=2000)
    verification_note: str = Field(min_length=20, max_length=1000)
    submission_enabled: bool = False
    submission_grant: str | None = Field(default=None, max_length=500)
    credential_env: str | None = Field(default=None, pattern=r"^EMPLOYER_CREDENTIAL_[A-Z0-9_]+$", max_length=120)
    form_parity_verified: bool = False
    receipt_contract: dict[str, Any] | None = None

    @model_validator(mode="after")
    def write_permission(self):
        if self.platform == "greenhouse" and self.board_token.lower() == "internal":
            raise ValueError("Only externally published employer boards may be indexed")
        if self.region == "eu" and self.platform != "lever":
            raise ValueError("This release supports the explicit EU host for Lever only")
        if self.submission_enabled:
            if self.platform != "greenhouse":
                raise ValueError("This release has a permissioned API submission adapter for Greenhouse only")
            if not (self.submission_grant and self.credential_env and self.form_parity_verified):
                raise ValueError("Submission requires a scoped grant, credential reference and verified form parity")
            contract = self.receipt_contract or {}
            if not all(contract.get(key) for key in ("receipt_id_field", "completion_field", "verification_note")):
                raise ValueError("Submission requires an independently verified complete-application receipt contract")
            if "completion_value" not in contract:
                raise ValueError("Receipt contract must explicitly state the completion value")
            if not isinstance(contract["completion_value"], (str, bool)) or not contract["completion_value"]:
                raise ValueError("Receipt completion value must be an explicit positive boolean or status string")
        return self


class SourceStateUpdate(StrictModel):
    enabled: bool
    reason: str = Field(min_length=10, max_length=500)


class ReconciliationCreate(StrictModel):
    outcome: Literal["confirmed", "not_submitted"]
    proof_kind: Literal["provider_lookup", "provider_support"]
    proof_reference: str = Field(min_length=10, max_length=1000)
    reason: str = Field(min_length=20, max_length=500)
    provider_receipt: dict[str, Any] | None = None
    employer_posting_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def positive_receipt(self):
        if self.outcome == "confirmed" and not self.provider_receipt:
            raise ValueError("Verified completion requires the provider receipt")
        if self.provider_receipt and len(str(self.provider_receipt)) > 20_000:
            raise ValueError("Provider receipt exceeds the supported size")
        return self
