from __future__ import annotations

import copy
from types import SimpleNamespace

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from ... import models as core
from ...database import get_db
from ...domains.employer import artifacts, batches, models, schemas, service
from ...security import get_current_user
from .admin import require_admin

router = APIRouter(prefix="/employer-jobs", tags=["employer-jobs"])


@router.get("/catalog")
def get_catalog(db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.catalog(db, user.id)


@router.post("/searches", status_code=201)
def create_search(payload: schemas.SearchCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.search_response(service.create_search(db, user.id, payload))


@router.get("/searches")
def list_searches(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return {"items": service.list_searches(db, user.id, limit)}


@router.get("/searches/{identity}")
def get_search(identity: str, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.get_search(db, user.id, identity)


@router.post("/applications", status_code=201)
def create_application(payload: schemas.ApplicationCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.application_response(db, service.create_application(db, user.id, payload))


@router.get("/applications")
def list_applications(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    applications = db.query(models.EmployerApplication).filter_by(user_id=user.id).order_by(models.EmployerApplication.created_at.desc()).limit(limit).all()
    return {"items": [service.application_response(db, application) for application in applications]}


@router.get("/applications/{identity}")
def get_application(identity: str, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.application_response(db, service._owned(db, models.EmployerApplication, identity, user.id))


@router.put("/applications/{identity}/package")
def update_package(identity: str, payload: schemas.PackageUpdate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.application_response(db, service.update_package(db, user.id, identity, payload))


@router.post("/applications/{identity}/approve")
def approve_package(identity: str, payload: schemas.ApprovalCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.application_response(db, service.approve_application(db, user.id, identity, payload))


@router.post("/applications/{identity}/execute")
def execute_package(identity: str, payload: schemas.ExecuteCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.application_response(db, service.request_execution(db, user.id, identity, payload))


@router.post("/applications/{identity}/cancel")
def cancel_package(identity: str, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return service.application_response(db, service.cancel_application(db, user.id, identity))


@router.post("/application-batches", status_code=201)
def create_batch(payload: schemas.BatchCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return batches.response(db, batches.create(db, user.id, payload))


@router.get("/application-batches/{identity}")
def get_batch(identity: str, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return batches.response(db, batches._owned(db, user.id, identity))


@router.post("/application-batches/{identity}/approve")
def approve_batch(identity: str, payload: schemas.ExecuteCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return batches.response(db, batches.approve(db, user.id, identity, payload))


@router.post("/application-batches/{identity}/execute")
def execute_batch(identity: str, payload: schemas.ExecuteCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return batches.response(db, batches.execute(db, user.id, identity, payload))


@router.post("/application-batches/{identity}/cancel")
def cancel_batch(identity: str, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return batches.response(db, batches.cancel(db, user.id, identity))


@router.get("/applications/{identity}/artifact")
def preview_artifact(identity: str, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    application = service._owned(db, models.EmployerApplication, identity, user.id)
    artifact = service._owned(db, models.SealedApplicationArtifact, application.artifact_id, user.id)
    snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(artifact, key)) for key in (
        "sha256", "size_bytes", "gcs_object", "gcs_generation", "content", "filename", "media_type",
    )})
    db.rollback()
    content = artifacts.read(snapshot)
    return Response(content, media_type=snapshot.media_type, headers={
        "Content-Disposition": f'inline; filename="{snapshot.filename}"', "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff", "X-Artifact-SHA256": snapshot.sha256,
    })


@router.get("/credits")
def credit_history(limit: int = Query(100, ge=1, le=200), db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    events = db.query(models.ServiceCreditEvent).filter_by(user_id=user.id).order_by(models.ServiceCreditEvent.created_at.desc()).limit(limit).all()
    held = db.query(models.ServiceCreditReservation).filter_by(user_id=user.id, state="reserved").all()
    return {"balance": int(user.job_service_credits or 0), "reserved_credits": sum(row.reserved_amount for row in held),
            "reservations": [{key: getattr(row, key) for key in ("id", "operation", "source_id", "reserved_amount", "state", "created_at")} for row in held],
            "items": [{key: getattr(row, key) for key in ("id", "event_type", "amount", "balance_after", "source_type", "source_id", "reason", "created_at")} for row in events]}


@router.post("/applications/{identity}/reconcile")
def reconcile_application(identity: str, payload: schemas.ReconciliationCreate, db: Session = Depends(get_db), admin: core.User = Depends(require_admin)):
    return service.application_response(db, service.reconcile_application(db, identity, admin, payload))


@router.get("/sources")
def list_sources(db: Session = Depends(get_db), _: core.User = Depends(require_admin)):
    return {"items": [service.source_response(source) for source in db.query(models.EmployerSource).order_by(models.EmployerSource.employer).limit(1000).all()]}


@router.post("/sources", status_code=201)
def create_source(payload: schemas.SourceCreate, db: Session = Depends(get_db), admin: core.User = Depends(require_admin)):
    return service.source_response(service.create_source(db, admin.id, payload))


@router.patch("/sources/{identity}")
def set_source_state(identity: str, payload: schemas.SourceStateUpdate, db: Session = Depends(get_db), admin: core.User = Depends(require_admin)):
    source = service._source(db, identity)
    source.enabled = payload.enabled
    db.add(core.AdminAuditEvent(id=service.public_id("audit"), actor_user_id=admin.id, actor_email=admin.email,
        action="enable_employer_source" if payload.enabled else "disable_employer_source", target_type="employer_source",
        target_id=identity, reason=payload.reason, after_state={"enabled": payload.enabled}))
    db.commit()
    return service.source_response(source)


@router.post("/sources/{identity}/refresh", status_code=202)
def refresh_source(identity: str, db: Session = Depends(get_db), _: core.User = Depends(require_admin)):
    event = service.enqueue_source_refresh(db, identity)
    return {"id": event.id, "status": event.status, "source_id": identity}
