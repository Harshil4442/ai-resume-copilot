"""Bounded HTTP browsing; internal complete-input and export services stay intact."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import HTTPException
from pydantic import BaseModel
from sqlalchemy import and_, false, func, or_
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as DatabaseTimeout
from sqlalchemy.orm import Session

from ... import models
from ..resume_uploads import browsing as resume_browsing
from . import schemas, service, transcription_review, workspace_cursor

MAX_PAGE = 50
DEFAULT_PAGE = 20
MAX_LEGACY_ITEMS = 50
MAX_SKILL_ROI_BYTES = 32 * 1024
log = logging.getLogger(__name__)

# Explicit projections prevent deferred body reads during list serialization.
_SPECS: dict[str, tuple[Any, type[BaseModel], str, bool]] = {
    "activity": (models.ApplicationEvent, schemas.ActivityMetadata, "occurred_at", True),
    "contacts": (models.OpportunityContact, schemas.ContactMetadata, "created_at", True),
    "reminders": (models.Reminder, schemas.ReminderResponse, "due_at", False),
    "evidence": (models.EvidenceItem, schemas.EvidenceMetadata, "created_at", True),
    "resume_versions": (models.ResumeVersion, schemas.ResumeVersionMetadata, "created_at", True),
}


def header(db: Session, user_id: int, opportunity_id: str, *, locked: bool = False) -> schemas.OpportunityHeader:
    query = db.query(*(getattr(models.Opportunity, name) for name in schemas.OpportunityHeader.model_fields)).filter(
        models.Opportunity.user_id == user_id, models.Opportunity.id == opportunity_id)
    row = (query.with_for_update() if locked else query).first()
    if row is None:
        raise HTTPException(404, "Opportunity not found")
    return schemas.OpportunityHeader.model_validate(row)


def _query(db: Session, user_id: int, opportunity: schemas.OpportunityHeader, collection: str,
           *, metadata: bool, state: str | None = None):
    if collection not in _SPECS:
        raise HTTPException(422, "Unsupported workspace collection")
    model, response, _, _ = _SPECS[collection]
    columns = [getattr(model, name) for name in response.model_fields] if metadata else [model]
    query = db.query(*columns).filter(model.user_id == user_id)
    if collection == "evidence":
        query = query.filter(model.resume_id == opportunity.resume_id) if opportunity.resume_id is not None else query.filter(false())
        if state is not None:
            if state not in {"pending", "approved", "rejected"}:
                raise HTTPException(422, "Unsupported evidence approval state")
            query = query.filter(model.approval_state == state)
    else:
        query = query.filter(model.opportunity_id == opportunity.id)
        if state is not None:
            if collection != "reminders" or state not in {"scheduled", "completed", "dismissed"}:
                raise HTTPException(422, "Unsupported workspace collection filter")
            query = query.filter(model.status == state)
    return query


def collection_page(db: Session, user_id: int, opportunity: schemas.OpportunityHeader, collection: str, *,
                    limit: int = DEFAULT_PAGE, cursor: str | None = None, state: str | None = None) -> Any:
    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise HTTPException(422, "Workspace page limit must be between 1 and 50")
    query = _query(db, user_id, opportunity, collection, metadata=True, state=state)
    model, response, order_name, descending = _SPECS[collection]
    order = getattr(model, order_name)
    context = (user_id, opportunity.id, collection, opportunity.resume_id, state, limit)
    if cursor is not None:
        stamp, identity = workspace_cursor.decode(cursor, context)
        comparison = or_(order < stamp, and_(order == stamp, model.id < identity)) if descending else or_(
            order > stamp, and_(order == stamp, model.id > identity))
        query = query.filter(comparison)
    rows = query.order_by(order.desc() if descending else order.asc(), model.id.desc() if descending else model.id.asc()).limit(limit + 1).all()
    items = [response.model_validate(row) for row in rows[:limit]]
    next_cursor = workspace_cursor.encode(context, getattr(rows[limit - 1], order_name), rows[limit - 1].id) if len(rows) > limit else None
    return schemas.WorkspacePage[response](items=items, next_cursor=next_cursor)  # type: ignore[valid-type]


def _version_query(db: Session, user_id: int, opportunity: schemas.OpportunityHeader, *, metadata: bool):
    model = models.ResumeVersion
    columns = [getattr(model, name) for name in schemas.ResumeVersionMetadata.model_fields] if metadata else [model]
    # A formerly submitted unassigned source version stays selectable after a
    # source change. A different opportunity's version is never in this scope.
    historical = db.query(models.ApplicationEvent.id).filter(models.ApplicationEvent.user_id == user_id,
        models.ApplicationEvent.opportunity_id == opportunity.id, models.ApplicationEvent.resume_version_id == model.id).exists()
    unassigned = and_(model.opportunity_id.is_(None), or_(model.resume_id == opportunity.resume_id, historical))
    return db.query(*columns).filter(model.user_id == user_id, or_(model.opportunity_id == opportunity.id, unassigned))


def item_detail(db: Session, user_id: int, opportunity: schemas.OpportunityHeader, collection: str, identity: str):
    if collection == "resume_versions":
        query = _version_query(db, user_id, opportunity, metadata=False)
    else:
        query = _query(db, user_id, opportunity, collection, metadata=False)
    row = query.filter(_SPECS[collection][0].id == identity).first()
    if row is None:
        raise HTTPException(404, "Workspace item is unavailable")
    return row


def body(db: Session, user_id: int, opportunity_id: str) -> schemas.OpportunityBody:
    row = db.query(*(getattr(models.Opportunity, name) for name in schemas.OpportunityBody.model_fields)).filter(
        models.Opportunity.user_id == user_id, models.Opportunity.id == opportunity_id).first()
    if row is None:
        raise HTTPException(404, "Opportunity not found")
    return schemas.OpportunityBody.model_validate(row)


def bootstrap(db: Session, user_id: int, opportunity_id: str, *, tab: str = "overview", limit: int = DEFAULT_PAGE,
              selected_version_id: str | None = None, include_skill_roi: bool = False) -> schemas.WorkspaceResponse:
    opportunity = header(db, user_id, opportunity_id)
    pages = {"resume_versions": collection_page(db, user_id, opportunity, "resume_versions", limit=limit)}
    for collection in {"overview": ("contacts", "reminders"), "resume": ("evidence",), "activity": ("activity",)}.get(tab, ()):
        pages[collection] = collection_page(db, user_id, opportunity, collection, limit=limit)
    applied = db.query(models.ApplicationEvent.resume_version_id).filter(models.ApplicationEvent.user_id == user_id,
        models.ApplicationEvent.opportunity_id == opportunity.id, models.ApplicationEvent.to_stage == "applied",
        models.ApplicationEvent.resume_version_id.is_not(None)).order_by(
            models.ApplicationEvent.occurred_at.desc(), models.ApplicationEvent.id.desc()).first()
    submitted_id = applied.resume_version_id if applied is not None else None
    versions = _version_query(db, user_id, opportunity, metadata=True)
    submitted = versions.filter(models.ResumeVersion.id == submitted_id).first() if submitted_id else None
    selected = versions.filter(models.ResumeVersion.id == selected_version_id).first() if selected_version_id else None
    if selected_version_id is not None and selected is None:
        raise HTTPException(404, "Selected resume version is unavailable")
    count = None
    if tab == "resume":
        count = int(db.query(func.count(models.EvidenceItem.id)).filter(models.EvidenceItem.user_id == user_id,
            models.EvidenceItem.resume_id == opportunity.resume_id, models.EvidenceItem.approval_state == "approved").scalar() or 0) if opportunity.resume_id is not None else 0
    saved_match = None
    match_source_state = "none"
    if opportunity.latest_match_id is not None:
        matched_source = db.query(models.JobMatch.resume_id).filter(models.JobMatch.user_id == user_id,
            models.JobMatch.id == opportunity.latest_match_id).first()
        if matched_source is None or matched_source.resume_id is None:
            match_source_state = "unavailable"
        elif matched_source.resume_id != opportunity.resume_id:
            match_source_state = "different_resume"
        else:
            match_source_state = "current"
        if tab == "overview" and match_source_state == "current":
            try:
                saved_match = service.latest_opportunity_match(db, user_id, opportunity_id, resume_id=opportunity.resume_id)
            except HTTPException as error:
                if error.status_code not in {404, 409}:
                    raise
                match_source_state = "unavailable"
    resumes = resume_browsing.page(db, user_id, limit=limit, current_id=opportunity.resume_id)
    skill_roi = None
    if tab == "learning" and include_skill_roi:
        # Keep this optional query last so a read failure cannot block the workspace.
        try:
            ranking = service.calculate_skill_roi(db, user_id)
        except (OperationalError, DatabaseTimeout):
            log.warning("Workspace Skill ROI database read unavailable")
        else:
            if len(json.dumps(ranking.model_dump(), ensure_ascii=False, allow_nan=False).encode()) <= MAX_SKILL_ROI_BYTES:
                skill_roi = ranking
    return schemas.WorkspaceResponse(opportunity=opportunity, pages=schemas.WorkspacePages(**pages),
        resumes=resumes, match=saved_match,
        match_source_state=match_source_state,
        submitted_resume_version_id=submitted_id,
        submitted_resume_version=schemas.ResumeVersionMetadata.model_validate(submitted) if submitted is not None else None,
        selected_resume_version=schemas.ResumeVersionMetadata.model_validate(selected) if selected is not None else None,
        approved_evidence_count=count, skill_roi=skill_roi)


def _legacy_ids(query, model, collection: str):
    identities = [row.id for row in query.with_entities(model.id).limit(MAX_LEGACY_ITEMS + 1).all()]
    if len(identities) > MAX_LEGACY_ITEMS:
        raise HTTPException(409, detail={"code": "workspace_pagination_required", "collection": collection,
            "message": "Use the bounded workspace pages to browse this complete collection.",
            "page_path": "/api/v1/opportunities/{opportunity_id}/workspace/" + collection,
            "maximum_legacy_items": MAX_LEGACY_ITEMS})
    return identities


def _legacy_rows(query, model, collection: str):
    identities = _legacy_ids(query, model, collection)
    return query.filter(model.id.in_(identities)).all() if identities else []


def legacy_opportunity_detail(db: Session, user_id: int, opportunity_id: str) -> dict:
    compact = header(db, user_id, opportunity_id)
    captured = {}
    # Inspect every sentinel before hydrating any legacy body. One overflowing
    # later collection must not trigger body reads from earlier collections.
    for collection in ("activity", "contacts", "reminders", "resume_versions"):
        model, _, order_name, descending = _SPECS[collection]
        order = getattr(model, order_name)
        query = _query(db, user_id, compact, collection, metadata=False).order_by(
            order.desc() if descending else order.asc(), model.id.desc() if descending else model.id.asc())
        captured[collection] = (query, model, _legacy_ids(query, model, collection))
    opportunity = service.get_opportunity(db, user_id, opportunity_id)
    values = {collection: query.filter(model.id.in_(identities)).all() if identities else []
              for collection, (query, model, identities) in captured.items()}
    return {**schemas.OpportunityResponse.model_validate(opportunity).model_dump(), **values}


def legacy_evidence(db: Session, user_id: int, *, resume_id: int | None = None, approval_state: str | None = None):
    query = db.query(models.EvidenceItem).filter(models.EvidenceItem.user_id == user_id)
    if resume_id is not None:
        query = query.filter(models.EvidenceItem.resume_id == resume_id)
    if approval_state:
        query = query.filter(models.EvidenceItem.approval_state == approval_state)
    return _legacy_rows(query.order_by(models.EvidenceItem.updated_at.desc(), models.EvidenceItem.id.desc()), models.EvidenceItem, "evidence")


def legacy_versions(db: Session, user_id: int, *, resume_id: int | None = None, opportunity_id: str | None = None):
    query = db.query(models.ResumeVersion).filter(models.ResumeVersion.user_id == user_id)
    if resume_id is not None:
        query = query.filter(models.ResumeVersion.resume_id == resume_id)
    if opportunity_id:
        query = query.filter(models.ResumeVersion.opportunity_id == opportunity_id)
    return _legacy_rows(query.order_by(models.ResumeVersion.created_at.desc(), models.ResumeVersion.id.desc()), models.ResumeVersion, "resume_versions")


def legacy_reminders(db: Session, user_id: int, *, status: str | None = None):
    query = db.query(models.Reminder).filter(models.Reminder.user_id == user_id)
    if status:
        query = query.filter(models.Reminder.status == status)
    return _legacy_rows(query.order_by(models.Reminder.due_at.asc(), models.Reminder.id.asc()), models.Reminder, "reminders")


def evidence_detail(db: Session, user_id: int, opportunity: schemas.OpportunityHeader, identity: str) -> schemas.WorkspaceEvidenceDetail:
    item = item_detail(db, user_id, opportunity, "evidence", identity)
    review = None
    unavailable = False
    if item.provenance == "resume_extraction" and item.resume_id is not None:
        try:
            snapshot = transcription_review.review_snapshot(db, user_id, item.resume_id)
            section = next((entry for entry in snapshot.sections if entry.evidence_id == item.id), None)
            review = schemas.EvidenceTranscriptionReview(**snapshot.model_dump(exclude={"sections", "unmapped_text"}), section=section)
        except HTTPException as error:
            if error.status_code != 422:
                raise
            unavailable = True
    return schemas.WorkspaceEvidenceDetail(evidence=schemas.EvidenceResponse.model_validate(item),
        transcription=review, transcription_unavailable=unavailable)


def source_snapshot(db: Session, user_id: int, opportunity_id: str) -> models.ResumeVersion:
    """Save the unchanged source and the complete approved-ID set at selection.

    Later approvals do not join this immutable selection. Existing version
    creation revalidates every selected ID before commit. Do not lock evidence:
    transcription review locks Evidence then Resume, so Resume->Evidence would
    invert that order. No evidence text or proposed edit is copied here.
    """
    opportunity = header(db, user_id, opportunity_id, locked=True)
    resume = db.query(models.Resume).filter(models.Resume.user_id == user_id, models.Resume.id == opportunity.resume_id).with_for_update().first()
    if resume is None or not resume.source_available or resume.source_format not in {"pdf", "docx", "tex", "texzip"}:
        raise HTTPException(409, "Connect a resume with its original source first")
    ids = [row.id for row in db.query(models.EvidenceItem.id).filter(models.EvidenceItem.user_id == user_id,
        models.EvidenceItem.resume_id == resume.id, models.EvidenceItem.approval_state == "approved").order_by(models.EvidenceItem.id).limit(501).all()]
    if len(ids) > 500:
        raise HTTPException(409, {"code": "source_snapshot_evidence_scope_exceeded",
            "message": "This source has more than the supported 500 approved evidence links. No partial snapshot was saved."})
    return service.create_resume_version(db, user_id, schemas.ResumeVersionCreate(resume_id=resume.id, opportunity_id=opportunity_id,
        label=f"{opportunity.company or opportunity.title} source snapshot"[:160],
        structured_content={"format_preservation": "source", "source_format": resume.source_format, "source_edits": []}, evidence_ids=ids))


def resume_version_page(db: Session, user_id: int, resume_id: int, *, limit: int = DEFAULT_PAGE,
                        cursor: str | None = None, approval_state: str | None = None,
                        current_id: str | None = None) -> schemas.ResumeVersionPageResponse:
    from .version_metadata import source_preserving_edits

    if type(limit) is not int or not 1 <= limit <= MAX_PAGE:
        raise HTTPException(422, "Version page limit must be between 1 and 50")
    if approval_state is not None and approval_state not in {"draft", "approved", "rejected"}:
        raise HTTPException(422, "Unsupported resume version approval state")
    if db.query(models.Resume.id).filter(models.Resume.user_id == user_id, models.Resume.id == resume_id).first() is None:
        raise HTTPException(404, "Selected resume is unavailable")
    model = models.ResumeVersion
    columns = [getattr(model, name) for name in schemas.ResumeVersionMetadata.model_fields]
    query = db.query(*columns, source_preserving_edits(db.get_bind().dialect.name)).filter(model.user_id == user_id, model.resume_id == resume_id)
    selected = query.filter(model.id == current_id).first() if current_id is not None else None
    if current_id is not None and selected is None:
        raise HTTPException(404, "Selected resume version is unavailable")
    if approval_state is not None:
        query = query.filter(model.approval_state == approval_state)
    context = (user_id, None, "resume_version_options", resume_id, approval_state, limit)
    if cursor is not None:
        stamp, identity = workspace_cursor.decode(cursor, context)
        query = query.filter(or_(model.created_at < stamp, and_(model.created_at == stamp, model.id < identity)))
    rows = query.order_by(model.created_at.desc(), model.id.desc()).limit(limit + 1).all()
    return schemas.ResumeVersionPageResponse(items=[schemas.ResumeVersionOption.model_validate(row) for row in rows[:limit]],
        next_cursor=workspace_cursor.encode(context, rows[limit - 1].created_at, rows[limit - 1].id) if len(rows) > limit else None,
        selected_version=schemas.ResumeVersionOption.model_validate(selected) if selected is not None else None)
