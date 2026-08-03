from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ... import models
from ...database import SessionLocal, get_db
from ...domains.analysis import schemas
from ...domains.analysis.service import (
    cancel_run,
    create_run,
    dispatch_run,
    get_owned_run,
)
from ...feature_flags import decide_feature
from ...security import get_current_user

router = APIRouter(prefix="/analysis-runs", tags=["analysis-runs"])


@router.post("", response_model=schemas.AnalysisRunResponse, status_code=202)
def create_analysis_run(
    payload: schemas.AnalysisRunCreate,
    background_tasks: BackgroundTasks,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    async_decision = decide_feature(
        "async_analysis",
        user_id=int(current_user.id),
        email=str(current_user.email),
    )
    if not async_decision.enabled:
        raise HTTPException(status_code=404, detail="Async analysis is not enabled")
    if payload.operation == "resume_tailor":
        tailoring_decision = decide_feature(
            "evidence_tailoring",
            user_id=int(current_user.id),
            email=str(current_user.email),
        )
        if not tailoring_decision.enabled:
            raise HTTPException(status_code=404, detail="Evidence tailoring is not enabled")
    run, created = create_run(
        db,
        user_id=current_user.id,
        payload=payload,
        header_idempotency_key=idempotency_key,
    )
    if created:
        dispatch_run(db, run, background_tasks)
    return run


@router.get("", response_model=schemas.AnalysisRunListResponse)
def list_analysis_runs(
    status: str | None = Query(default=None, max_length=24),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    query = db.query(models.AnalysisRun).filter(models.AnalysisRun.user_id == current_user.id)
    if status:
        query = query.filter(models.AnalysisRun.status == status)
    items = query.order_by(models.AnalysisRun.created_at.desc()).limit(limit).all()
    return schemas.AnalysisRunListResponse(items=items)


@router.get("/{run_id}", response_model=schemas.AnalysisRunResponse)
def get_analysis_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    return get_owned_run(db, current_user.id, run_id)


@router.post("/{run_id}/cancel", response_model=schemas.AnalysisRunCancelResponse)
def cancel_analysis_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    run = cancel_run(db, current_user.id, run_id)
    return schemas.AnalysisRunCancelResponse(
        id=run.id,
        status=run.status,
        cancel_requested=run.cancel_requested,
    )


@router.get("/{run_id}/result", response_model=schemas.AnalysisRunResultResponse)
def get_analysis_result(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    run = get_owned_run(db, current_user.id, run_id)
    if run.result_purged_at is not None:
        raise HTTPException(
            status_code=410,
            detail="This analysis result passed its retention period and was deleted.",
        )
    if run.status != "succeeded" or run.result_payload is None or run.completed_at is None:
        raise HTTPException(
            status_code=409,
            detail={"message": "Analysis result is not ready", "status": run.status},
        )
    return schemas.AnalysisRunResultResponse(
        id=run.id,
        operation=run.operation,
        result=run.result_payload,
        completed_at=run.completed_at,
    )


@router.get("/{run_id}/events")
def stream_analysis_events(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    get_owned_run(db, current_user.id, run_id)
    user_id = current_user.id

    async def event_stream():
        previous = None
        for _ in range(60):
            event_db = SessionLocal()
            try:
                run = (
                    event_db.query(models.AnalysisRun)
                    .filter(
                        models.AnalysisRun.id == run_id,
                        models.AnalysisRun.user_id == user_id,
                    )
                    .first()
                )
                if not run:
                    yield "event: error\ndata: {\"message\":\"run not found\"}\n\n"
                    return
                state = {
                    "id": run.id,
                    "status": run.status,
                    "attempt_count": run.attempt_count,
                    "updated_at": run.updated_at.isoformat(),
                    "error_code": run.error_code,
                }
                serialized = json.dumps(state, separators=(",", ":"))
                if serialized != previous:
                    yield f"event: status\ndata: {serialized}\n\n"
                    previous = serialized
                if run.status in {"succeeded", "failed", "cancelled"}:
                    return
            finally:
                event_db.close()
            await asyncio.sleep(1)
        yield "event: timeout\ndata: {}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
