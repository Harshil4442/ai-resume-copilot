from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..domains.analysis.tasks import RetryableRunError, process_analysis_run
from ..domains.dispatch.service import (
    RetryableDispatchError,
    WorkerScopeError,
    dispatch_pending,
    process_event,
)
from ..domains.operations import run_maintenance

router = APIRouter(prefix="/internal/tasks", tags=["internal-worker"])


@router.post("/dispatch/{event_id}")
def execute_dispatch_task(
    event_id: str,
    x_cloudtasks_taskname: str | None = Header(default=None),
    x_hirewiz_task_token: str | None = Header(default=None),
):
    _verify_task_request(x_cloudtasks_taskname, x_hirewiz_task_token)
    try:
        status = process_event(event_id)
    except WorkerScopeError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except RetryableDispatchError as exc:
        raise HTTPException(status_code=503, detail="Work will retry from its recorded state") from exc
    if status == "missing":
        raise HTTPException(status_code=404, detail="Dispatch work not found")
    return {"id": event_id, "status": status}


def _verify_task_request(
    task_name: str | None,
    task_token: str | None,
) -> None:
    expected_token = os.getenv("ANALYSIS_TASK_TOKEN")
    if expected_token and task_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid task token")
    app_env = (os.getenv("APP_ENV") or "production").lower()
    if app_env not in {"development", "dev", "local", "test"} and not task_name:
        raise HTTPException(status_code=403, detail="Cloud Tasks request header required")


@router.post("/analysis-runs/{run_id}")
def execute_analysis_task(
    run_id: str,
    x_cloudtasks_taskname: str | None = Header(default=None),
    x_hirewiz_task_token: str | None = Header(default=None),
):
    _verify_task_request(x_cloudtasks_taskname, x_hirewiz_task_token)
    allowed = {part.strip() for part in os.getenv("WORKER_ALLOWED_TOPICS", "").split(",") if part.strip()}
    if allowed and "analysis.run" not in allowed:
        raise HTTPException(status_code=403, detail="This worker cannot execute analysis")
    try:
        status = process_analysis_run(run_id)
    except RetryableRunError as exc:
        raise HTTPException(status_code=503, detail="Retryable provider failure") from exc
    if status == "missing":
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return {"id": run_id, "status": status}


@router.post("/maintenance")
def execute_maintenance_task(
    x_cloudscheduler: str | None = Header(default=None),
    x_hirewiz_task_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
):
    app_env = (os.getenv("APP_ENV") or "production").lower()
    expected_token = os.getenv("ANALYSIS_TASK_TOKEN")
    if expected_token and x_hirewiz_task_token != expected_token:
        raise HTTPException(status_code=401, detail="Invalid task token")
    scheduler_request = bool(x_cloudscheduler and x_cloudscheduler.lower() == "true")
    if app_env not in {"development", "dev", "local", "test"} and not scheduler_request:
        raise HTTPException(status_code=403, detail="Cloud Scheduler request header required")
    from ..domains.employer.privacy import enqueue_artifact_cleanup
    from ..domains.employer.service import enqueue_due_sources
    queued_cleanup = enqueue_artifact_cleanup(db, limit=100)
    queued_sources = enqueue_due_sources(db, limit=100)
    db.commit()
    summary = run_maintenance(db).to_dict()
    summary["sources_queued"] = queued_sources
    summary["artifact_cleanup_queued"] = queued_cleanup
    summary["dispatch"] = dispatch_pending(limit=100)
    return summary
