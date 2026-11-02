"""Fixed owned dashboard advisory projection; never an action or auth grant."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, inspect
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.orm import Session, object_session

from ...feature_flags import decide_feature
from ...models import JobMatch, Opportunity, Reminder, Resume, User
from ...schemas import UserProfileResponse
from ..career.schemas import OpportunityStage
from ..credits.availability import availability_view
from ..credits.schemas import CreditAvailabilityView, CreditWalletView
from ..credits.service import wallet
from ..recovery.store import GuardDenied
from .bootstrap import MAX_COMPONENT_BYTES, OwnedBootstrapView, _features, _subject
from .profile_view import profile_view

Component = Literal["summary", "opportunities", "reminders"]
MAX_BYTES = {"summary": 1024, "opportunities": 32768, "reminders": 8192}


class ComponentUnavailable(Exception):
    """A valid advisory component cannot fit its fixed response budget."""


class DashboardResumeSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidenced_skills: int = Field(ge=0)
    verification_rate: int = Field(ge=0, le=100)


class DashboardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    resume_count: int = Field(ge=0)
    applications_count: int = Field(ge=0)
    average_match_score: float
    resume_quality: DashboardResumeSignal


class DashboardOpportunity(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: str = Field(max_length=64)
    title: str = Field(max_length=200)
    company: str = Field(max_length=200)
    stage: OpportunityStage
    next_action: str | None = Field(max_length=240)


class DashboardOpportunities(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[DashboardOpportunity] = Field(max_length=8)
    total: int = Field(ge=0)


class DashboardReminder(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    id: str = Field(max_length=64)
    message: str = Field(max_length=240)
    due_at: datetime


class OwnedDashboardView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["owned-dashboard-v1"] = "owned-dashboard-v1"
    bootstrap: OwnedBootstrapView
    summary: DashboardSummary | None
    opportunities: DashboardOpportunities | None
    reminders: list[DashboardReminder] | None = Field(max_length=5)
    unavailable: list[Component]


def _summary(db: Session, user_id: int) -> DashboardSummary:
    resumes = db.query(func.count(Resume.id)).filter(Resume.user_id == user_id).scalar()
    matches, total_score = db.query(func.count(JobMatch.id), func.sum(func.coalesce(JobMatch.match_score, 0.0))).filter(
        JobMatch.user_id == user_id).one()
    # The legacy summary counts NULL scores as zero in the denominator.
    average = float(total_score or 0.0) / matches if matches else 0.0
    # NULL is older than any dated upload. Equal timestamps previously had no
    # defined secondary order; use lowest ID to make this projection stable.
    latest = db.query(Resume.skills, Resume.sections).filter(Resume.user_id == user_id).order_by(
        Resume.created_at.desc().nulls_last(), Resume.id.asc()).first()
    evidenced, rate = 0, 0
    if latest is not None:
        if len(json.dumps(dict(latest._mapping), ensure_ascii=False, allow_nan=False).encode()) > 2 * 1024 * 1024:
            raise ComponentUnavailable()
        skills = [str(skill).strip().lower() for skill in (latest.skills or []) if str(skill).strip()]
        sections = latest.sections or {}
        evidence = f"{(sections.get('experience') or '').lower()}\n{(sections.get('projects') or '').lower()}"
        evidenced = len({skill for skill in skills if skill in evidence})
        rate = round(evidenced / max(len(skills), 1) * 100)
    return DashboardSummary(resume_count=resumes, applications_count=matches,
        average_match_score=average,
        resume_quality=DashboardResumeSignal(evidenced_skills=evidenced, verification_rate=rate))


def _opportunities(db: Session, user_id: int) -> DashboardOpportunities:
    query = db.query(Opportunity).filter(Opportunity.user_id == user_id, Opportunity.stage != "archived")
    total = query.count()
    rows = query.with_entities(Opportunity.id, Opportunity.title, Opportunity.company,
        Opportunity.stage, Opportunity.next_action).order_by(Opportunity.updated_at.desc()).limit(8).all()
    return DashboardOpportunities(items=[DashboardOpportunity.model_validate(dict(row._mapping)) for row in rows], total=total)


def _reminders(db: Session, user_id: int) -> list[DashboardReminder]:
    rows = db.query(Reminder.id, Reminder.message, Reminder.due_at).filter(
        Reminder.user_id == user_id, Reminder.status == "scheduled").order_by(Reminder.due_at.asc()).limit(5).all()
    return [DashboardReminder.model_validate(dict(row._mapping)) for row in rows]


def _workspace_allowed(user_id: int, email: str) -> bool:
    decision = decide_feature("career_workspace", user_id=user_id, email=email)
    if type(decision.enabled) is not bool:
        raise GuardDenied("Workspace decision is unavailable")
    return decision.enabled


def dashboard_view(db: Session, user: User) -> OwnedDashboardView:
    state = inspect(user, raiseerr=False)
    identity = state.identity if state is not None else None
    if (type(user) is not User or object_session(user) is not db or type(identity) is not tuple
            or len(identity) != 1 or type(identity[0]) is not int or identity[0] <= 0):
        raise GuardDenied("Not authenticated")
    user_id = identity[0]
    # Fixed original profile/feature/credit readers, not action grants or another
    # simulated auth call. Malformed state is never downgraded to partial success.
    with db.no_autoflush:
        before = _subject(db, user_id)
        workspace_allowed = _workspace_allowed(user_id, user.email)
        values: dict[str, Any] = {}
        unavailable = []
        readers = (
            ("profile", lambda: UserProfileResponse.model_validate(profile_view(db, user))),
            ("features", lambda: _features(user_id, user.email)),
            ("credit_availability", lambda: CreditAvailabilityView.model_validate(availability_view(db, user))),
            ("credit_wallet", lambda: CreditWalletView.model_validate(wallet(db, user_id=user_id, max_lots=5000), strict=True)),
            ("summary", lambda: _summary(db, user_id)),
            ("opportunities", lambda: _opportunities(db, user_id)),
            ("reminders", lambda: _reminders(db, user_id)),
        )
        for name, read in readers:
            if name in {"opportunities", "reminders"}:
                if not workspace_allowed or values.get("features") is None:
                    values[name] = None
                    unavailable.append(name)
                    continue
                if not _workspace_allowed(user_id, user.email):
                    raise GuardDenied("Workspace decision changed")
            try:
                value = read()
                raw = ([item.model_dump(mode="json") for item in value] if isinstance(value, list)
                    else value.model_dump(mode="json") if isinstance(value, BaseModel) else value)
                bound = MAX_COMPONENT_BYTES[name] if name in MAX_COMPONENT_BYTES else MAX_BYTES[name]
                if len(json.dumps(raw, ensure_ascii=False, allow_nan=False).encode()) > bound:
                    raise ComponentUnavailable()
                values[name] = value
            except GuardDenied:
                raise
            except (OperationalError, PoolTimeout, ComponentUnavailable):
                db.rollback()
                values[name] = None
                unavailable.append(name)
        if _subject(db, user_id) != before:
            raise GuardDenied("Not authenticated")
        if _workspace_allowed(user_id, user.email) != workspace_allowed:
            raise GuardDenied("Workspace decision changed")
        base_names = tuple(MAX_COMPONENT_BYTES)
        base = OwnedBootstrapView(**{name: values.pop(name) for name in base_names},
            unavailable=[name for name in unavailable if name in base_names])
        return OwnedDashboardView(bootstrap=base, **values,
            unavailable=[name for name in unavailable if name not in base_names])
