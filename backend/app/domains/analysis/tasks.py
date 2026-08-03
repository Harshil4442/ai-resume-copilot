from __future__ import annotations

import json
import logging
import os
from datetime import timedelta

from google.cloud import tasks_v2
from sqlalchemy import or_

from ... import models
from ...database import SessionLocal
from ...observability import correlation_id_var
from ..common import utcnow
from ..notifications import enqueue_notification
from ..usage import commit_run_usage, release_run_usage
from .operations import execute_operation

log = logging.getLogger("hirewiz.analysis.tasks")
TERMINAL_STATES = {"succeeded", "failed", "cancelled"}


class RetryableRunError(RuntimeError):
    pass


def _retryable(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(
        marker in message
        for marker in ("429", "rate limit", "timeout", "timed out", "temporarily", "502", "503", "504")
    )


def enqueue_cloud_task(run_id: str) -> str:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    location = os.environ["ANALYSIS_TASKS_LOCATION"]
    queue = os.environ["ANALYSIS_TASKS_QUEUE"]
    worker_url = os.environ["ANALYSIS_WORKER_URL"].rstrip("/")
    service_account = os.environ["ANALYSIS_TASKS_SERVICE_ACCOUNT"]
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(project, location, queue)
    request: dict = {
        "http_method": tasks_v2.HttpMethod.POST,
        "url": f"{worker_url}/internal/tasks/analysis-runs/{run_id}",
        "headers": {
            "Content-Type": "application/json",
            "X-Correlation-ID": correlation_id_var.get(),
        },
        "body": json.dumps({"run_id": run_id}).encode("utf-8"),
        "oidc_token": {
            "service_account_email": service_account,
            "audience": worker_url,
        },
    }
    shared_token = os.getenv("ANALYSIS_TASK_TOKEN")
    if shared_token:
        request["headers"]["X-HireWiz-Task-Token"] = shared_token
    response = client.create_task(parent=parent, task={"http_request": request})
    return response.name


def process_analysis_run(run_id: str) -> str:
    db = SessionLocal()
    try:
        stale_before = utcnow() - timedelta(minutes=20)
        claimed = (
            db.query(models.AnalysisRun)
            .filter(
                models.AnalysisRun.id == run_id,
                or_(
                    models.AnalysisRun.status == "queued",
                    (
                        (models.AnalysisRun.status == "running")
                        & (models.AnalysisRun.started_at < stale_before)
                    ),
                ),
            )
            .update(
                {
                    models.AnalysisRun.status: "running",
                    models.AnalysisRun.started_at: utcnow(),
                    models.AnalysisRun.updated_at: utcnow(),
                    models.AnalysisRun.attempt_count: models.AnalysisRun.attempt_count + 1,
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if claimed == 0:
            existing = db.query(models.AnalysisRun).filter(models.AnalysisRun.id == run_id).first()
            return existing.status if existing else "missing"

        run = db.query(models.AnalysisRun).filter(models.AnalysisRun.id == run_id).one()
        if run.cancel_requested:
            run.status = "cancelled"
            run.cancelled_at = utcnow()
            run.updated_at = utcnow()
            release_run_usage(db, run, reason="Cancelled before execution")
            db.commit()
            return run.status

        try:
            result = execute_operation(db, run)
            db.flush()
            db.refresh(run)
            if run.cancel_requested:
                run.status = "cancelled"
                run.cancelled_at = utcnow()
                run.updated_at = utcnow()
                release_run_usage(db, run, reason="Cancelled during execution")
            else:
                run.result_payload = result
                run.status = "succeeded"
                run.completed_at = utcnow()
                run.updated_at = utcnow()
                run.error_code = None
                run.error_message = None
                commit_run_usage(db, run)
                user = db.query(models.User).filter(models.User.id == run.user_id).one()
                enqueue_notification(
                    db,
                    user_id=run.user_id,
                    notification_type="analysis_completed",
                    recipient=str(user.email),
                    payload={
                        "run_id": run.id,
                        "operation": run.operation,
                        "opportunity_id": run.opportunity_id,
                    },
                    idempotency_key=f"analysis:{run.id}:completed",
                )
            db.commit()
            return run.status
        except Exception as exc:
            db.rollback()
            run = (
                db.query(models.AnalysisRun)
                .filter(models.AnalysisRun.id == run_id)
                .with_for_update()
                .one()
            )
            run.error_code = type(exc).__name__[:80]
            run.error_message = str(exc)[:500]
            run.updated_at = utcnow()
            if _retryable(exc) and run.attempt_count < 3:
                run.status = "queued"
                db.commit()
                raise RetryableRunError(str(exc)) from exc
            run.status = "failed"
            run.completed_at = utcnow()
            release_run_usage(db, run, reason=f"Analysis failed: {type(exc).__name__}")
            db.commit()
            log.exception("Analysis run %s failed", run_id)
            return run.status
    finally:
        db.close()
