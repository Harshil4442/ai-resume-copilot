from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OpportunityStage = Literal[
    "saved",
    "evaluating",
    "preparing",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
    "archived",
]
OpportunityPriority = Literal["low", "medium", "high"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OpportunityCreate(StrictModel):
    title: str = Field(min_length=2, max_length=200)
    company: str = Field(default="", max_length=200)
    location: str = Field(default="", max_length=200)
    source: str = Field(default="manual", max_length=80)
    source_url: str | None = Field(default=None, max_length=2000)
    job_description: str = Field(default="", max_length=100_000)
    resume_id: int | None = None
    priority: OpportunityPriority = "medium"
    compensation: str | None = Field(default=None, max_length=160)
    deadline_at: datetime | None = None
    next_action: str | None = Field(default=None, max_length=240)
    notes: str = Field(default="", max_length=20_000)


class OpportunityUpdate(StrictModel):
    title: str | None = Field(default=None, min_length=2, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=2000)
    job_description: str | None = Field(default=None, max_length=100_000)
    resume_id: int | None = None
    priority: OpportunityPriority | None = None
    compensation: str | None = Field(default=None, max_length=160)
    deadline_at: datetime | None = None
    next_action: str | None = Field(default=None, max_length=240)
    notes: str | None = Field(default=None, max_length=20_000)


class OpportunityStageUpdate(StrictModel):
    stage: OpportunityStage
    note: str | None = Field(default=None, max_length=2000)
    resume_version_id: str | None = Field(default=None, max_length=64)


class OpportunityOutcomeUpdate(StrictModel):
    outcome: Literal["offer_accepted", "offer_declined", "rejected", "withdrawn"]
    notes: str | None = Field(default=None, max_length=4000)


class OpportunityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resume_id: int | None
    latest_match_id: int | None
    latest_analysis_run_id: str | None
    title: str
    company: str
    location: str
    source: str
    source_url: str | None
    job_description: str
    job_snapshot: dict[str, Any]
    stage: OpportunityStage
    priority: OpportunityPriority
    compensation: str | None
    deadline_at: datetime | None
    next_action: str | None
    notes: str
    outcome: str | None
    outcome_notes: str | None
    archived_at: datetime | None
    outcome_at: datetime | None
    created_at: datetime
    updated_at: datetime


class OpportunityListResponse(BaseModel):
    items: list[OpportunityResponse]
    total: int


class ApplicationEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str
    event_type: str
    from_stage: str | None
    to_stage: str | None
    note: str | None
    source: str
    resume_version_id: str | None
    occurred_at: datetime
    recorded_at: datetime


class ContactCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    role: str | None = Field(default=None, max_length=160)
    email: str | None = Field(default=None, max_length=320)
    profile_url: str | None = Field(default=None, max_length=2000)
    notes: str | None = Field(default=None, max_length=4000)


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str
    name: str
    role: str | None
    email: str | None
    profile_url: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime


class EvidenceCreate(StrictModel):
    resume_id: int | None = None
    category: str = Field(min_length=2, max_length=48)
    title: str = Field(min_length=2, max_length=180)
    evidence_text: str = Field(min_length=2, max_length=20_000)
    metrics: dict[str, Any] = Field(default_factory=dict)
    skills: list[str] = Field(default_factory=list, max_length=100)
    source_ref: str | None = Field(default=None, max_length=2000)
    approval_state: Literal["pending", "approved", "rejected"] = "pending"


class EvidenceUpdate(StrictModel):
    category: str | None = Field(default=None, min_length=2, max_length=48)
    title: str | None = Field(default=None, min_length=2, max_length=180)
    evidence_text: str | None = Field(default=None, min_length=2, max_length=20_000)
    metrics: dict[str, Any] | None = None
    skills: list[str] | None = Field(default=None, max_length=100)
    approval_state: Literal["pending", "approved", "rejected"] | None = None


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resume_id: int | None
    category: str
    title: str
    evidence_text: str
    metrics: dict[str, Any]
    skills: list[str]
    provenance: str
    source_ref: str | None
    approval_state: str
    confidence: str
    created_at: datetime
    updated_at: datetime


class EvidenceImportResponse(BaseModel):
    created: list[EvidenceResponse]
    skipped: int


class ResumeVersionCreate(StrictModel):
    resume_id: int
    opportunity_id: str | None = Field(default=None, max_length=64)
    label: str = Field(default="Resume version", min_length=2, max_length=160)
    structured_content: dict[str, Any] | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=500)
    generation_run_id: str | None = Field(default=None, max_length=64)


class ResumeVersionStateUpdate(StrictModel):
    approval_state: Literal["draft", "approved", "rejected"]


class ResumeVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    resume_id: int
    opportunity_id: str | None
    version_number: int
    label: str
    structured_content: dict[str, Any]
    rendered_artifact_ref: str | None
    evidence_ids: list[str]
    generation_run_id: str | None
    approval_state: str
    submitted_at: datetime | None
    created_at: datetime


class ReminderCreate(StrictModel):
    opportunity_id: str | None = Field(default=None, max_length=64)
    reminder_type: str = Field(default="follow_up", max_length=40)
    message: str = Field(min_length=2, max_length=300)
    due_at: datetime
    delivery_channel: Literal["in_app", "email"] = "in_app"


class ReminderStatusUpdate(StrictModel):
    status: Literal["scheduled", "completed", "dismissed"]


class ReminderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    opportunity_id: str | None
    reminder_type: str
    message: str
    due_at: datetime
    status: str
    delivery_channel: str
    created_at: datetime
    sent_at: datetime | None
    completed_at: datetime | None
    dismissed_at: datetime | None


class CareerMemoryUpsert(StrictModel):
    category: str = Field(min_length=2, max_length=48)
    memory_key: str = Field(min_length=2, max_length=120, pattern=r"^[a-z0-9_.-]+$")
    value: Any
    source_ref: str | None = Field(default=None, max_length=2000)


class CareerMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    category: str
    memory_key: str
    value: Any
    provenance: str
    source_ref: str | None
    approval_state: str
    created_at: datetime
    updated_at: datetime


class SkillRoiItem(BaseModel):
    skill: str
    opportunity_count: int
    demand_ratio: float
    evidence_strength: float
    outcome_signal: float
    estimated_hours: int
    score: float
    reason: str


class SkillRoiResponse(BaseModel):
    opportunity_count: int
    items: list[SkillRoiItem]


class OpportunityMatchResponse(BaseModel):
    match_id: int
    match_score: float
    grade: str
    required_skills: list[str]
    full_matches: list[str]
    partial_matches: list[dict[str, Any]]
    true_gaps: list[str]
    skill_verification_rate: int
    dimensions: list[dict[str, Any]]
    fit_summary: str
    improvement_tips: list[str]
    created_at: datetime


class UsageEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    analysis_run_id: str | None
    event_type: str
    amount: int
    balance_after: int
    source_type: str
    source_id: str | None
    reason: str | None
    created_at: datetime


class UsageHistoryResponse(BaseModel):
    balance: int
    items: list[UsageEventResponse]


class OpportunityDetailResponse(OpportunityResponse):
    activity: list[ApplicationEventResponse]
    contacts: list[ContactResponse]
    reminders: list[ReminderResponse]
    resume_versions: list[ResumeVersionResponse]


class OpportunityExportResponse(BaseModel):
    exported_at: datetime
    opportunity: OpportunityDetailResponse
    latest_match: OpportunityMatchResponse | None
    evidence_items: list[EvidenceResponse]
