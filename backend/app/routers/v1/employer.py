from __future__ import annotations

import copy
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from sqlalchemy.orm import Session

from ... import models as core
from ...database import get_db
from ...domains.employer import (
    artifacts,
    batches,
    manual_reporting,
    models,
    schemas,
    search_pages,
    service,
)
from ...domains.employer import (
    credit_history as service_credit_history,
)
from ...security import decode_access_token_identity, get_current_user, oauth2_scheme
from .admin import require_admin

router = APIRouter(prefix="/employer-jobs", tags=["employer-jobs"])


class _PrivateSavedSearchReadRoute(APIRoute):
    """Keep personal saved-search responses, including validation errors, private."""
    def get_route_handler(self):
        original = super().get_route_handler()

        async def private_response(request: Request) -> Response:
            try:
                response = await original(request)
            except RequestValidationError:
                response = JSONResponse(status_code=422, content={"detail": [{
                    "loc": ["request"], "msg": "Invalid saved-search page request. Check the page size, cursor and saved search.",
                    "type": "invalid_search_page_request",
                }]})
            except HTTPException as exc:
                exc.headers = {**(exc.headers or {}), "Cache-Control": "private, no-store"}
                raise
            response.headers["Cache-Control"] = "private, no-store"
            return response

        return private_response


_saved_search_reads = APIRouter(route_class=_PrivateSavedSearchReadRoute)


@router.get("/catalog")
def get_catalog(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    user_id, claims = decode_access_token_identity(token)
    native = "candidate_lifetime" in claims
    if native:
        get_current_user(token=token, db=db)  # Existing independent current native denial check.
    return service.catalog_for_verified_subject(db, user_id, credential_epoch=claims.get("credential_epoch"),
                                                native_validated=native)


@router.post("/searches", status_code=201, responses={
    202: {"description": "Search preparation accepted", "content": {"application/json": {"schema": {}}}},
})
def create_search(payload: schemas.SearchCreate, db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    result = service.create_search(db, user.id, payload)
    if isinstance(result, models.EmployerSearchPreparation) or result.status == "preparing":
        return JSONResponse(status_code=202, content=jsonable_encoder(service.search_preparation_status(db, user.id, result.id)),
                            headers={"Cache-Control": "private, no-store"})
    return service.search_response(result)


@router.get("/searches")
def list_searches(limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    return {"items": service.list_searches(db, user.id, limit)}


@_saved_search_reads.get("/search-summaries", response_model=schemas.SearchSummaryPage)
def list_search_summaries(response: Response, limit: int = Query(10, ge=1, le=20),
                          cursor: str | None = Query(None, min_length=1, max_length=512),
                          db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return search_pages.list_summaries(db, user.id, limit=limit, cursor=cursor)


@_saved_search_reads.get("/search-preparations/{identity}")
def get_search_preparation(response: Response, identity: str = Path(min_length=1, max_length=64),
                           db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return service.search_preparation_status(db, user.id, identity)


@_saved_search_reads.post("/search-preparations/{identity}/cancel")
def cancel_search_preparation(response: Response, identity: str = Path(min_length=1, max_length=64),
                              db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return service.cancel_search_preparation(db, user.id, identity)


@_saved_search_reads.get("/searches/{identity}/results", response_model=schemas.SavedSearchResultsPage,
            response_model_exclude_unset=True)
def get_saved_search_results(response: Response, identity: str = Path(min_length=1, max_length=64),
                             limit: int = Query(10, ge=1, le=20),
                             cursor: str | None = Query(None, min_length=1, max_length=512),
                             db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return search_pages.saved_results(db, user.id, identity, limit=limit, cursor=cursor)


router.include_router(_saved_search_reads)


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


@router.post("/applications/{identity}/report-submitted")
def report_manual_submission(response: Response, identity: str, payload: schemas.ManualSubmissionReport,
                             db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    application = manual_reporting.report(db, user.id, identity, payload)
    return {"id": application.id, "status": application.status, "candidate_report": copy.deepcopy(application.receipt),
            "receipt": None, "application_mode": "manual", "charged_credits": 0, "reserved_credits": 0}


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


@router.get("/credits", response_model=service_credit_history.ServiceCreditHistory)
def credit_history(response: Response, limit: int = Query(100, ge=1, le=200),
                   cursor: str | None = Query(None, min_length=1, max_length=64),
                   operation: str | None = Query(None, pattern="^(job_search|job_application)$"),
                   db: Session = Depends(get_db), user: core.User = Depends(get_current_user)):
    response.headers["Cache-Control"] = "private, no-store"
    return service_credit_history.history(db, user.id, balance=int(user.job_service_credits or 0),
                                         reservation_model=models.ServiceCreditReservation,
                                         event_model=models.ServiceCreditEvent,
                                         limit=limit, cursor=cursor, operation=operation)


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
