from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from ..domains.analysis.tasks import RetryableRunError, process_analysis_run
from ..domains.operations import run_maintenance

router = APIRouter(prefix="/internal/tasks", tags=["internal-worker"])


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
    return run_maintenance(db).to_dict()
