from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ... import models
from ...services.matching import score_to_grade
from ..common import public_id, utcnow
from . import schemas

VALID_STAGES = {
    "saved",
    "evaluating",
    "preparing",
    "applied",
    "interviewing",
    "offer",
    "rejected",
    "withdrawn",
    "archived",
}


def _owned_resume(db: Session, user_id: int, resume_id: int) -> models.Resume:
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == resume_id, models.Resume.user_id == user_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


def get_opportunity(db: Session, user_id: int, opportunity_id: str) -> models.Opportunity:
    opportunity = (
        db.query(models.Opportunity)
        .filter(
            models.Opportunity.id == opportunity_id,
            models.Opportunity.user_id == user_id,
        )
        .first()
    )
    if not opportunity:
        raise HTTPException(status_code=404, detail="Opportunity not found")
    return opportunity


def create_opportunity(
    db: Session,
    user_id: int,
    payload: schemas.OpportunityCreate,
) -> models.Opportunity:
    if payload.resume_id is not None:
        _owned_resume(db, user_id, payload.resume_id)

    now = utcnow()
    snapshot = {
        "captured_at": now.isoformat(),
        "title": payload.title,
        "company": payload.company,
        "location": payload.location,
        "source": payload.source,
        "source_url": payload.source_url,
        "job_description": payload.job_description,
    }
    opportunity = models.Opportunity(
        id=public_id("opp"),
        user_id=user_id,
        resume_id=payload.resume_id,
        title=payload.title.strip(),
        company=payload.company.strip(),
        location=payload.location.strip(),
        source=payload.source.strip() or "manual",
        source_url=payload.source_url,
        job_description=payload.job_description.strip(),
        job_snapshot=snapshot,
        stage="saved",
        priority=payload.priority,
        compensation=payload.compensation,
        deadline_at=payload.deadline_at,
        next_action=payload.next_action,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    db.add(opportunity)
    db.flush()
    db.add(
        models.ApplicationEvent(
            id=public_id("evt"),
            user_id=user_id,
            opportunity_id=opportunity.id,
            event_type="created",
            to_stage="saved",
            source="user",
            occurred_at=now,
            recorded_at=now,
        )
    )
    db.commit()
    db.refresh(opportunity)
    return opportunity


def list_opportunities(
    db: Session,
    user_id: int,
    *,
    stage: str | None = None,
    search: str | None = None,
    include_archived: bool = False,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[models.Opportunity], int]:
    query = db.query(models.Opportunity).filter(models.Opportunity.user_id == user_id)
    if stage:
        if stage not in VALID_STAGES:
            raise HTTPException(status_code=422, detail="Unsupported opportunity stage")
        query = query.filter(models.Opportunity.stage == stage)
    elif not include_archived:
        query = query.filter(models.Opportunity.stage != "archived")
    if search and search.strip():
        pattern = f"%{search.strip()}%"
        query = query.filter(
            or_(
                models.Opportunity.title.ilike(pattern),
                models.Opportunity.company.ilike(pattern),
                models.Opportunity.location.ilike(pattern),
            )
        )
    total = query.count()
    items = (
        query.order_by(models.Opportunity.updated_at.desc())
        .offset(max(0, offset))
        .limit(max(1, min(limit, 200)))
        .all()
    )
    return items, total


def update_opportunity(
    db: Session,
    user_id: int,
    opportunity_id: str,
    payload: schemas.OpportunityUpdate,
) -> models.Opportunity:
    opportunity = get_opportunity(db, user_id, opportunity_id)
    changes = payload.model_dump(exclude_unset=True)
    if "resume_id" in changes and changes["resume_id"] is not None:
        _owned_resume(db, user_id, changes["resume_id"])
    for name, value in changes.items():
        setattr(opportunity, name, value)
    opportunity.updated_at = utcnow()
    db.commit()
    db.refresh(opportunity)
    return opportunity


def transition_opportunity(
    db: Session,
    user_id: int,
    opportunity_id: str,
    payload: schemas.OpportunityStageUpdate,
) -> models.Opportunity:
    opportunity = get_opportunity(db, user_id, opportunity_id)
    if payload.stage == "applied" and not payload.resume_version_id:
        raise HTTPException(
            status_code=422,
            detail="Select the exact resume version used for this application.",
        )
    if payload.resume_version_id:
        version = (
            db.query(models.ResumeVersion)
            .filter(
                models.ResumeVersion.id == payload.resume_version_id,
                models.ResumeVersion.user_id == user_id,
            )
            .first()
        )
        if not version:
            raise HTTPException(status_code=404, detail="Resume version not found")
        if version.opportunity_id not in {None, opportunity_id}:
            raise HTTPException(
                status_code=409,
                detail="Resume version belongs to a different opportunity",
            )

    previous = opportunity.stage
    now = utcnow()
    opportunity.stage = payload.stage
    opportunity.updated_at = now
    opportunity.archived_at = now if payload.stage == "archived" else None
    if payload.stage in {"rejected", "withdrawn"}:
        opportunity.outcome = payload.stage
        opportunity.outcome_at = now
    db.add(
        models.ApplicationEvent(
            id=public_id("evt"),
            user_id=user_id,
            opportunity_id=opportunity.id,
            event_type="stage_changed",
            from_stage=previous,
            to_stage=payload.stage,
            note=payload.note,
            source="user",
            resume_version_id=payload.resume_version_id,
            occurred_at=now,
            recorded_at=now,
        )
    )
    if payload.stage == "applied" and payload.resume_version_id:
        version.submitted_at = now
    db.commit()
    db.refresh(opportunity)
    return opportunity


def set_outcome(
    db: Session,
    user_id: int,
    opportunity_id: str,
    payload: schemas.OpportunityOutcomeUpdate,
) -> models.Opportunity:
    opportunity = get_opportunity(db, user_id, opportunity_id)
    now = utcnow()
    previous = opportunity.stage
    stage = "offer" if payload.outcome.startswith("offer_") else payload.outcome
    opportunity.stage = stage
    opportunity.outcome = payload.outcome
    opportunity.outcome_notes = payload.notes
    opportunity.outcome_at = now
    opportunity.updated_at = now
    db.add(
        models.ApplicationEvent(
            id=public_id("evt"),
            user_id=user_id,
            opportunity_id=opportunity.id,
            event_type="outcome_recorded",
            from_stage=previous,
            to_stage=stage,
            note=payload.notes,
            source="user",
            occurred_at=now,
            recorded_at=now,
        )
    )
    db.commit()
    db.refresh(opportunity)
    return opportunity


def opportunity_detail(
    db: Session,
    user_id: int,
    opportunity_id: str,
) -> dict[str, Any]:
    opportunity = get_opportunity(db, user_id, opportunity_id)
    activity = (
        db.query(models.ApplicationEvent)
        .filter(
            models.ApplicationEvent.opportunity_id == opportunity_id,
            models.ApplicationEvent.user_id == user_id,
        )
        .order_by(models.ApplicationEvent.occurred_at.desc())
        .all()
    )
    contacts = (
        db.query(models.OpportunityContact)
        .filter(
            models.OpportunityContact.opportunity_id == opportunity_id,
            models.OpportunityContact.user_id == user_id,
        )
        .order_by(models.OpportunityContact.created_at.desc())
        .all()
    )
    reminders = (
        db.query(models.Reminder)
        .filter(
            models.Reminder.opportunity_id == opportunity_id,
            models.Reminder.user_id == user_id,
        )
        .order_by(models.Reminder.due_at.asc())
        .all()
    )
    resume_versions = (
        db.query(models.ResumeVersion)
        .filter(
            models.ResumeVersion.opportunity_id == opportunity_id,
            models.ResumeVersion.user_id == user_id,
        )
        .order_by(models.ResumeVersion.version_number.desc())
        .all()
    )
    return {
        **schemas.OpportunityResponse.model_validate(opportunity).model_dump(),
        "activity": activity,
        "contacts": contacts,
        "reminders": reminders,
        "resume_versions": resume_versions,
    }


def opportunity_export(
    db: Session,
    user_id: int,
    opportunity_id: str,
) -> schemas.OpportunityExportResponse:
    detail = schemas.OpportunityDetailResponse.model_validate(
        opportunity_detail(db, user_id, opportunity_id)
    )
    latest_match = None
    if detail.latest_match_id:
        latest_match = latest_opportunity_match(db, user_id, opportunity_id)
    evidence = []
    if detail.resume_id:
        evidence = (
            db.query(models.EvidenceItem)
            .filter(
                models.EvidenceItem.user_id == user_id,
                models.EvidenceItem.resume_id == detail.resume_id,
            )
            .order_by(models.EvidenceItem.created_at.asc())
            .all()
        )
    return schemas.OpportunityExportResponse(
        exported_at=utcnow(),
        opportunity=detail,
        latest_match=latest_match,
        evidence_items=evidence,
    )


def latest_opportunity_match(
    db: Session,
    user_id: int,
    opportunity_id: str,
) -> schemas.OpportunityMatchResponse:
    opportunity = get_opportunity(db, user_id, opportunity_id)
    if not opportunity.latest_match_id:
        raise HTTPException(status_code=404, detail="This opportunity has no match analysis yet")
    match = (
        db.query(models.JobMatch)
        .filter(
            models.JobMatch.id == opportunity.latest_match_id,
            models.JobMatch.user_id == user_id,
        )
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Match analysis not found")
    return schemas.OpportunityMatchResponse(
        match_id=match.id,
        match_score=float(match.match_score or 0),
        grade=score_to_grade(float(match.match_score or 0)),
        required_skills=list(match.required_skills or []),
        full_matches=list(match.full_matches or []),
        partial_matches=list(match.partial_matches or []),
        true_gaps=list(match.true_gaps or []),
        skill_verification_rate=int(match.skill_verification_rate or 0),
        dimensions=list(match.dimension_scores or []),
        fit_summary=match.fit_summary or "",
        improvement_tips=list(match.improvement_tips or []),
        created_at=match.created_at,
    )


def create_contact(
    db: Session,
    user_id: int,
    opportunity_id: str,
    payload: schemas.ContactCreate,
) -> models.OpportunityContact:
    get_opportunity(db, user_id, opportunity_id)
    contact = models.OpportunityContact(
        id=public_id("con"),
        user_id=user_id,
        opportunity_id=opportunity_id,
        **payload.model_dump(),
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(contact)
    db.commit()
    db.refresh(contact)
    return contact


def delete_contact(db: Session, user_id: int, contact_id: str) -> None:
    contact = (
        db.query(models.OpportunityContact)
        .filter(
            models.OpportunityContact.id == contact_id,
            models.OpportunityContact.user_id == user_id,
        )
        .first()
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Contact not found")
    db.delete(contact)
    db.commit()


def create_evidence(
    db: Session,
    user_id: int,
    payload: schemas.EvidenceCreate,
    *,
    provenance: str = "user",
    confidence: str = "user_provided",
) -> models.EvidenceItem:
    if payload.resume_id is not None:
        _owned_resume(db, user_id, payload.resume_id)
    item = models.EvidenceItem(
        id=public_id("evd"),
        user_id=user_id,
        resume_id=payload.resume_id,
        category=payload.category,
        title=payload.title,
        evidence_text=payload.evidence_text,
        metrics=payload.metrics,
        skills=sorted({skill.strip() for skill in payload.skills if skill.strip()}),
        provenance=provenance,
        source_ref=payload.source_ref,
        approval_state=payload.approval_state,
        confidence=confidence,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(item)
    db.commit()
    db.refresh(item)
    return item


def import_resume_evidence(
    db: Session,
    user_id: int,
    resume_id: int,
) -> tuple[list[models.EvidenceItem], int]:
    resume = _owned_resume(db, user_id, resume_id)
    created: list[models.EvidenceItem] = []
    skipped = 0
    existing_refs = {
        value[0]
        for value in db.query(models.EvidenceItem.source_ref)
        .filter(
            models.EvidenceItem.user_id == user_id,
            models.EvidenceItem.resume_id == resume_id,
        )
        .all()
        if value[0]
    }
    for section, content in (resume.sections or {}).items():
        text = str(content or "").strip()
        if len(text) < 2:
            continue
        source_ref = f"resume:{resume_id}:section:{section}"
        if source_ref in existing_refs:
            skipped += 1
            continue
        item = models.EvidenceItem(
            id=public_id("evd"),
            user_id=user_id,
            resume_id=resume_id,
            category=str(section)[:48],
            title=str(section).replace("_", " ").title()[:180],
            evidence_text=text[:20_000],
            metrics={},
            skills=list(resume.skills or []),
            provenance="resume_extraction",
            source_ref=source_ref,
            approval_state="pending",
            confidence="extracted",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(item)
        created.append(item)
    db.commit()
    for item in created:
        db.refresh(item)
    return created, skipped


def list_evidence(
    db: Session,
    user_id: int,
    *,
    resume_id: int | None = None,
    approval_state: str | None = None,
) -> list[models.EvidenceItem]:
    query = db.query(models.EvidenceItem).filter(models.EvidenceItem.user_id == user_id)
    if resume_id is not None:
        query = query.filter(models.EvidenceItem.resume_id == resume_id)
    if approval_state:
        query = query.filter(models.EvidenceItem.approval_state == approval_state)
    return query.order_by(models.EvidenceItem.updated_at.desc()).all()


def update_evidence(
    db: Session,
    user_id: int,
    evidence_id: str,
    payload: schemas.EvidenceUpdate,
) -> models.EvidenceItem:
    item = (
        db.query(models.EvidenceItem)
        .filter(
            models.EvidenceItem.id == evidence_id,
            models.EvidenceItem.user_id == user_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Evidence item not found")
    updates = payload.model_dump(exclude_unset=True)
    factual_fields = {"category", "title", "evidence_text", "metrics", "skills"}
    if factual_fields.intersection(updates) and "approval_state" not in updates:
        item.approval_state = "pending"
    for name, value in updates.items():
        setattr(item, name, value)
    item.updated_at = utcnow()
    db.commit()
    db.refresh(item)
    return item


def delete_evidence(db: Session, user_id: int, evidence_id: str) -> None:
    item = (
        db.query(models.EvidenceItem)
        .filter(
            models.EvidenceItem.id == evidence_id,
            models.EvidenceItem.user_id == user_id,
        )
        .first()
    )
    if not item:
        raise HTTPException(status_code=404, detail="Evidence item not found")
    referenced = (
        db.query(models.ResumeVersion.id)
        .filter(models.ResumeVersion.user_id == user_id)
        .all()
    )
    # JSON membership differs by database. Keep approved evidence immutable if
    # any version exists; users can reject it while preserving claim history.
    if item.approval_state == "approved" and referenced:
        item.approval_state = "rejected"
        item.updated_at = utcnow()
    else:
        db.delete(item)
    db.commit()


def create_resume_version(
    db: Session,
    user_id: int,
    payload: schemas.ResumeVersionCreate,
) -> models.ResumeVersion:
    resume = _owned_resume(db, user_id, payload.resume_id)
    if payload.opportunity_id:
        get_opportunity(db, user_id, payload.opportunity_id)
    if payload.generation_run_id:
        run = (
            db.query(models.AnalysisRun)
            .filter(
                models.AnalysisRun.id == payload.generation_run_id,
                models.AnalysisRun.user_id == user_id,
            )
            .first()
        )
        if not run:
            raise HTTPException(status_code=404, detail="Analysis run not found")
    if payload.evidence_ids:
        owned_count = (
            db.query(func.count(models.EvidenceItem.id))
            .filter(
                models.EvidenceItem.user_id == user_id,
                models.EvidenceItem.id.in_(payload.evidence_ids),
                models.EvidenceItem.approval_state == "approved",
            )
            .scalar()
        )
        if int(owned_count or 0) != len(set(payload.evidence_ids)):
            raise HTTPException(
                status_code=422,
                detail="Every referenced evidence item must be owned and approved",
            )
    next_version = int(
        db.query(func.max(models.ResumeVersion.version_number))
        .filter(models.ResumeVersion.resume_id == resume.id)
        .scalar()
        or 0
    ) + 1
    content = payload.structured_content or {
        "contact_info": resume.contact_info or {},
        "sections": resume.sections or {},
        "skills": resume.skills or [],
        "experience_years": resume.experience_years or 0,
    }
    version = models.ResumeVersion(
        id=public_id("rsv"),
        user_id=user_id,
        resume_id=resume.id,
        opportunity_id=payload.opportunity_id,
        version_number=next_version,
        label=payload.label,
        structured_content=content,
        evidence_ids=list(dict.fromkeys(payload.evidence_ids)),
        generation_run_id=payload.generation_run_id,
        approval_state="draft",
        created_at=utcnow(),
    )
    db.add(version)
    db.commit()
    db.refresh(version)
    return version


def list_resume_versions(
    db: Session,
    user_id: int,
    *,
    resume_id: int | None = None,
    opportunity_id: str | None = None,
) -> list[models.ResumeVersion]:
    query = db.query(models.ResumeVersion).filter(models.ResumeVersion.user_id == user_id)
    if resume_id is not None:
        query = query.filter(models.ResumeVersion.resume_id == resume_id)
    if opportunity_id:
        query = query.filter(models.ResumeVersion.opportunity_id == opportunity_id)
    return query.order_by(models.ResumeVersion.created_at.desc()).all()


def update_resume_version_state(
    db: Session,
    user_id: int,
    version_id: str,
    payload: schemas.ResumeVersionStateUpdate,
) -> models.ResumeVersion:
    version = (
        db.query(models.ResumeVersion)
        .filter(
            models.ResumeVersion.id == version_id,
            models.ResumeVersion.user_id == user_id,
        )
        .first()
    )
    if not version:
        raise HTTPException(status_code=404, detail="Resume version not found")
    version.approval_state = payload.approval_state
    db.commit()
    db.refresh(version)
    return version


def create_reminder(
    db: Session,
    user_id: int,
    payload: schemas.ReminderCreate,
) -> models.Reminder:
    if payload.opportunity_id:
        get_opportunity(db, user_id, payload.opportunity_id)
    reminder = models.Reminder(
        id=public_id("rem"),
        user_id=user_id,
        opportunity_id=payload.opportunity_id,
        reminder_type=payload.reminder_type,
        message=payload.message,
        due_at=payload.due_at,
        status="scheduled",
        delivery_channel=payload.delivery_channel,
        created_at=utcnow(),
    )
    db.add(reminder)
    db.commit()
    db.refresh(reminder)
    return reminder


def list_reminders(
    db: Session,
    user_id: int,
    *,
    status: str | None = None,
) -> list[models.Reminder]:
    query = db.query(models.Reminder).filter(models.Reminder.user_id == user_id)
    if status:
        query = query.filter(models.Reminder.status == status)
    return query.order_by(models.Reminder.due_at.asc()).limit(200).all()


def update_reminder_status(
    db: Session,
    user_id: int,
    reminder_id: str,
    payload: schemas.ReminderStatusUpdate,
) -> models.Reminder:
    reminder = (
        db.query(models.Reminder)
        .filter(models.Reminder.id == reminder_id, models.Reminder.user_id == user_id)
        .first()
    )
    if not reminder:
        raise HTTPException(status_code=404, detail="Reminder not found")
    reminder.status = payload.status
    now = utcnow()
    reminder.completed_at = now if payload.status == "completed" else None
    reminder.dismissed_at = now if payload.status == "dismissed" else None
    db.commit()
    db.refresh(reminder)
    return reminder


def upsert_memory(
    db: Session,
    user_id: int,
    payload: schemas.CareerMemoryUpsert,
) -> models.CareerMemoryEntry:
    entry = (
        db.query(models.CareerMemoryEntry)
        .filter(
            models.CareerMemoryEntry.user_id == user_id,
            models.CareerMemoryEntry.memory_key == payload.memory_key,
        )
        .first()
    )
    now = utcnow()
    if entry:
        entry.category = payload.category
        entry.value = payload.value
        entry.source_ref = payload.source_ref
        entry.provenance = "user"
        entry.approval_state = "approved"
        entry.updated_at = now
    else:
        entry = models.CareerMemoryEntry(
            id=public_id("mem"),
            user_id=user_id,
            category=payload.category,
            memory_key=payload.memory_key,
            value=payload.value,
            provenance="user",
            source_ref=payload.source_ref,
            approval_state="approved",
            created_at=now,
            updated_at=now,
        )
        db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_memory(db: Session, user_id: int) -> list[models.CareerMemoryEntry]:
    return (
        db.query(models.CareerMemoryEntry)
        .filter(models.CareerMemoryEntry.user_id == user_id)
        .order_by(models.CareerMemoryEntry.category, models.CareerMemoryEntry.memory_key)
        .all()
    )


def delete_memory(db: Session, user_id: int, memory_id: str) -> None:
    entry = (
        db.query(models.CareerMemoryEntry)
        .filter(
            models.CareerMemoryEntry.id == memory_id,
            models.CareerMemoryEntry.user_id == user_id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(status_code=404, detail="Career Memory entry not found")
    db.delete(entry)
    db.commit()


def calculate_skill_roi(db: Session, user_id: int) -> schemas.SkillRoiResponse:
    opportunities = (
        db.query(models.Opportunity)
        .filter(
            models.Opportunity.user_id == user_id,
            models.Opportunity.stage != "archived",
        )
        .all()
    )
    demand: Counter[str] = Counter()
    positive_outcomes: Counter[str] = Counter()
    negative_outcomes: Counter[str] = Counter()
    for opportunity in opportunities:
        skills: list[str] = []
        if opportunity.latest_match_id:
            match = (
                db.query(models.JobMatch)
                .filter(
                    models.JobMatch.id == opportunity.latest_match_id,
                    models.JobMatch.user_id == user_id,
                )
                .first()
            )
            if match:
                skills = list(match.required_skills or [])
        if not skills:
            skills = list((opportunity.job_snapshot or {}).get("required_skills") or [])
        normalized = {str(skill).strip().lower() for skill in skills if str(skill).strip()}
        demand.update(normalized)
        if opportunity.outcome == "offer_accepted":
            positive_outcomes.update(normalized)
        elif opportunity.outcome in {"offer_declined", "rejected", "withdrawn"}:
            negative_outcomes.update(normalized)

    approved = (
        db.query(models.EvidenceItem)
        .filter(
            models.EvidenceItem.user_id == user_id,
            models.EvidenceItem.approval_state == "approved",
        )
        .all()
    )
    evidence_counts: Counter[str] = Counter()
    for item in approved:
        evidence_counts.update({str(skill).strip().lower() for skill in (item.skills or [])})

    opportunity_count = len(opportunities)
    items = []
    for skill, count in demand.items():
        ratio = count / max(1, opportunity_count)
        strength = min(1.0, evidence_counts[skill] / 2)
        outcome_signal = max(
            -1.0,
            min(1.0, (positive_outcomes[skill] - (negative_outcomes[skill] * 0.25)) / count),
        )
        estimated_hours = 8 if strength >= 1 else 16 if strength > 0 else 30
        score = round(
            (ratio * 70)
            + ((1 - strength) * 20)
            + (10 / max(1, estimated_hours / 8))
            + (outcome_signal * 10),
            2,
        )
        reason = (
            f"Appears in {count} of {opportunity_count} active opportunities; "
            + ("approved evidence already exists." if strength else "no approved evidence exists yet.")
        )
        if outcome_signal > 0:
            reason += " Your recorded outcomes add a positive signal."
        elif outcome_signal < 0:
            reason += " Your recorded outcomes reduce confidence in this priority."
        items.append(
            schemas.SkillRoiItem(
                skill=skill,
                opportunity_count=count,
                demand_ratio=round(ratio, 3),
                evidence_strength=round(strength, 2),
                outcome_signal=round(outcome_signal, 2),
                estimated_hours=estimated_hours,
                score=score,
                reason=reason,
            )
        )
    items.sort(key=lambda item: (-item.score, item.skill))
    return schemas.SkillRoiResponse(opportunity_count=opportunity_count, items=items[:30])
