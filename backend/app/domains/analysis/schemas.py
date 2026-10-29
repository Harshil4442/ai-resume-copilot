from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .tailoring_failures import (
    TailoringFailureReason,
    TailoringNextAction,
    public_tailoring_guidance,
)

AnalysisOperation = Literal[
    "job_match",
    "interview_questions",
    "market_analysis",
    "resume_tailor",
    "skill_roi",
]


class AnalysisQuoteAcceptance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pricing_version: str = Field(strict=True, min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    units: int = Field(strict=True, ge=0, le=100_000)


class AnalysisRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: AnalysisOperation
    opportunity_id: str | None = Field(default=None, max_length=64)
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)
    accepted_quote: AnalysisQuoteAcceptance | None = None
    accepted_service_quote_id: str | None = Field(default=None, min_length=5, max_length=64)

    @model_validator(mode="after")
    def one_purchase_contract(self):
        if self.accepted_quote is not None and self.accepted_service_quote_id is not None:
            raise ValueError("Review one billing contract for this operation")
        return self


class TailoringFailureGuidance(BaseModel):
    reason_code: TailoringFailureReason
    next_action: TailoringNextAction


class AnalysisRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str | None
    operation: str
    status: str
    estimated_units: int
    committed_units: int
    billing_currency: Literal["legacy_ai_units", "credits", "none"] = "legacy_ai_units"
    usage_state: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    error_code: str | None
    failure_guidance: TailoringFailureGuidance | None = Field(default_factory=lambda: None)

    attempt_count: int
    generation_attempt_limit: int = 3
    generation_attempt_count: int = 0
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    input_purged_at: datetime | None
    result_purged_at: datetime | None

    @model_validator(mode="before")
    @classmethod
    def add_failure_guidance(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            data = dict(value)
            stored_message = data.pop("error_message", None)
            funding = data.get("model_cost_quote") or {}
        else:
            data = {name: getattr(value, name) for name in cls.model_fields if hasattr(value, name)}
            stored_message = getattr(value, "error_message", None)
            funding = getattr(value, "model_cost_quote", None) or {}
        if funding.get("billing_currency") == "credits":
            data["billing_currency"] = "credits"
        elif data.get("estimated_units") == 0:
            data["billing_currency"] = "none"
        data["failure_guidance"] = public_tailoring_guidance(
            data.get("operation", ""), data.get("status", ""), stored_message, data.get("error_code"),
        )
        return data


class AnalysisRunListResponse(BaseModel):
    items: list[AnalysisRunResponse]


class AnalysisRunResultResponse(BaseModel):
    id: str
    operation: str
    result: dict[str, Any]
    completed_at: datetime


class AnalysisRunCancelResponse(BaseModel):
    id: str
    status: str
    cancel_requested: bool
