from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

AnalysisOperation = Literal[
    "job_match",
    "interview_questions",
    "market_analysis",
    "resume_tailor",
    "skill_roi",
]


class AnalysisRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: AnalysisOperation
    opportunity_id: str | None = Field(default=None, max_length=64)
    input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class AnalysisRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str | None
    operation: AnalysisOperation
    status: str
    estimated_units: int
    committed_units: int
    usage_state: str
    provider: str | None
    model: str | None
    prompt_version: str | None
    error_code: str | None
    attempt_count: int
    cancel_requested: bool
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    cancelled_at: datetime | None
    input_purged_at: datetime | None
    result_purged_at: datetime | None


class AnalysisRunListResponse(BaseModel):
    items: list[AnalysisRunResponse]


class AnalysisRunResultResponse(BaseModel):
    id: str
    operation: AnalysisOperation
    result: dict[str, Any]
    completed_at: datetime


class AnalysisRunCancelResponse(BaseModel):
    id: str
    status: str
    cancel_requested: bool
