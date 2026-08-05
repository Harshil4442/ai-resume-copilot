from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ... import models
from ...database import get_db
from ...domains.career import schemas, service
from ...feature_flags import decide_feature
from ...security import get_current_user
from ...services.resume_artifacts import render_resume_version


def require_career_workspace(
    current_user: models.User = Depends(get_current_user),
) -> None:
    decision = decide_feature(
        "career_workspace",
        user_id=int(current_user.id),
        email=str(current_user.email),
    )
    if not decision.enabled:
        raise HTTPException(status_code=404, detail="Career Workspace is not enabled")


router = APIRouter(
    tags=["career-workspace"],
    dependencies=[Depends(require_career_workspace)],
)


@router.post("/opportunities", response_model=schemas.OpportunityResponse, status_code=201)
def create_opportunity(
    payload: schemas.OpportunityCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.create_opportunity(db, current_user.id, payload)


@router.get("/opportunities", response_model=schemas.OpportunityListResponse)
def list_opportunities(
    stage: str | None = Query(default=None, max_length=32),
    search: str | None = Query(default=None, max_length=200),
    include_archived: bool = False,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    items, total = service.list_opportunities(
        db,
        current_user.id,
        stage=stage,
        search=search,
        include_archived=include_archived,
        offset=offset,
        limit=limit,
    )
    return schemas.OpportunityListResponse(items=items, total=total)


@router.get("/applications", response_model=schemas.OpportunityListResponse)
def list_applications(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    items, _ = service.list_opportunities(db, current_user.id, include_archived=False, limit=200)
    application_stages = {"applied", "interviewing", "offer", "rejected", "withdrawn"}
    applications = [item for item in items if item.stage in application_stages]
    return schemas.OpportunityListResponse(items=applications, total=len(applications))


@router.get("/opportunities/{opportunity_id}", response_model=schemas.OpportunityDetailResponse)
def get_opportunity(
    opportunity_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.opportunity_detail(db, current_user.id, opportunity_id)


@router.get(
    "/opportunities/{opportunity_id}/export",
    response_model=schemas.OpportunityExportResponse,
)
def export_opportunity(
    opportunity_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    payload = service.opportunity_export(db, current_user.id, opportunity_id)
    response = JSONResponse(content=jsonable_encoder(payload))
    response.headers["Content-Disposition"] = (
        f'attachment; filename="hirewiz-opportunity-{opportunity_id}.json"'
    )
    response.headers["Cache-Control"] = "private, no-store"
    return response


@router.get(
    "/opportunities/{opportunity_id}/match",
    response_model=schemas.OpportunityMatchResponse,
)
def get_opportunity_match(
    opportunity_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.latest_opportunity_match(db, current_user.id, opportunity_id)


@router.patch("/opportunities/{opportunity_id}", response_model=schemas.OpportunityResponse)
def update_opportunity(
    opportunity_id: str,
    payload: schemas.OpportunityUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.update_opportunity(db, current_user.id, opportunity_id, payload)


@router.post("/opportunities/{opportunity_id}/stage", response_model=schemas.OpportunityResponse)
def transition_opportunity(
    opportunity_id: str,
    payload: schemas.OpportunityStageUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.transition_opportunity(db, current_user.id, opportunity_id, payload)


@router.post("/opportunities/{opportunity_id}/outcome", response_model=schemas.OpportunityResponse)
def record_outcome(
    opportunity_id: str,
    payload: schemas.OpportunityOutcomeUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.set_outcome(db, current_user.id, opportunity_id, payload)


@router.delete("/opportunities/{opportunity_id}", response_model=schemas.OpportunityResponse)
def archive_opportunity(
    opportunity_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.transition_opportunity(
        db,
        current_user.id,
        opportunity_id,
        schemas.OpportunityStageUpdate(stage="archived", note="Archived by user"),
    )


@router.post(
    "/opportunities/{opportunity_id}/contacts",
    response_model=schemas.ContactResponse,
    status_code=201,
)
def create_contact(
    opportunity_id: str,
    payload: schemas.ContactCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.create_contact(db, current_user.id, opportunity_id, payload)


@router.delete("/contacts/{contact_id}", status_code=204)
def delete_contact(
    contact_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    service.delete_contact(db, current_user.id, contact_id)
    return Response(status_code=204)


@router.post("/evidence-items", response_model=schemas.EvidenceResponse, status_code=201)
def create_evidence(
    payload: schemas.EvidenceCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.create_evidence(db, current_user.id, payload)


@router.get("/evidence-items", response_model=list[schemas.EvidenceResponse])
def list_evidence(
    resume_id: int | None = None,
    approval_state: str | None = Query(default=None, max_length=24),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.list_evidence(
        db,
        current_user.id,
        resume_id=resume_id,
        approval_state=approval_state,
    )


@router.post(
    "/evidence-items/import-resume/{resume_id}",
    response_model=schemas.EvidenceImportResponse,
)
def import_resume_evidence(
    resume_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    created, skipped = service.import_resume_evidence(db, current_user.id, resume_id)
    return schemas.EvidenceImportResponse(created=created, skipped=skipped)


@router.patch("/evidence-items/{evidence_id}", response_model=schemas.EvidenceResponse)
def update_evidence(
    evidence_id: str,
    payload: schemas.EvidenceUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.update_evidence(db, current_user.id, evidence_id, payload)


@router.delete("/evidence-items/{evidence_id}", status_code=204)
def delete_evidence(
    evidence_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    service.delete_evidence(db, current_user.id, evidence_id)
    return Response(status_code=204)


@router.post("/resume-versions", response_model=schemas.ResumeVersionResponse, status_code=201)
def create_resume_version(
    payload: schemas.ResumeVersionCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.create_resume_version(db, current_user.id, payload)


@router.get("/resume-versions", response_model=list[schemas.ResumeVersionResponse])
def list_resume_versions(
    resume_id: int | None = None,
    opportunity_id: str | None = Query(default=None, max_length=64),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.list_resume_versions(
        db,
        current_user.id,
        resume_id=resume_id,
        opportunity_id=opportunity_id,
    )


@router.get(
    "/resume-versions/{version_id}/download",
    response_class=Response,
    responses={
        200: {
            "description": "Rendered resume version",
            "content": {
                "application/pdf": {"schema": {"type": "string", "format": "binary"}},
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {
                    "schema": {"type": "string", "format": "binary"}
                },
            },
        }
    },
)
def download_resume_version(
    version_id: str,
    artifact_format: Literal["pdf", "docx"] = Query(default="pdf", alias="format"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    version = service.get_resume_version(db, current_user.id, version_id)
    resume = (
        db.query(models.Resume)
        .filter(
            models.Resume.id == version.resume_id,
            models.Resume.user_id == current_user.id,
        )
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    artifact = render_resume_version(version, resume, artifact_format)
    return Response(
        content=artifact.content,
        media_type=artifact.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.patch("/resume-versions/{version_id}", response_model=schemas.ResumeVersionResponse)
def update_resume_version(
    version_id: str,
    payload: schemas.ResumeVersionStateUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.update_resume_version_state(db, current_user.id, version_id, payload)


@router.post("/reminders", response_model=schemas.ReminderResponse, status_code=201)
def create_reminder(
    payload: schemas.ReminderCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.create_reminder(db, current_user.id, payload)


@router.get("/reminders", response_model=list[schemas.ReminderResponse])
def list_reminders(
    status: str | None = Query(default=None, max_length=24),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.list_reminders(db, current_user.id, status=status)


@router.patch("/reminders/{reminder_id}", response_model=schemas.ReminderResponse)
def update_reminder(
    reminder_id: str,
    payload: schemas.ReminderStatusUpdate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.update_reminder_status(db, current_user.id, reminder_id, payload)


@router.put("/career-memory", response_model=schemas.CareerMemoryResponse)
def upsert_career_memory(
    payload: schemas.CareerMemoryUpsert,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.upsert_memory(db, current_user.id, payload)


@router.get("/career-memory", response_model=list[schemas.CareerMemoryResponse])
def list_career_memory(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.list_memory(db, current_user.id)


@router.delete("/career-memory/{memory_id}", status_code=204)
def delete_career_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    service.delete_memory(db, current_user.id, memory_id)
    return Response(status_code=204)


@router.get("/skill-roi", response_model=schemas.SkillRoiResponse)
def get_skill_roi(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return service.calculate_skill_roi(db, current_user.id)


@router.get("/usage-events", response_model=schemas.UsageHistoryResponse)
def get_usage_history(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    items = (
        db.query(models.UsageEvent)
        .filter(models.UsageEvent.user_id == current_user.id)
        .order_by(models.UsageEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    db.refresh(current_user)
    return schemas.UsageHistoryResponse(balance=int(current_user.ai_credits or 0), items=items)
