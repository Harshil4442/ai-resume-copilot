from __future__ import annotations

import os
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from ... import models
from ..career.service import get_opportunity
from ..common import payload_fingerprint, public_id, utcnow
from ..usage import InsufficientUnitsError, release_run_usage, reserve_run_usage
from . import schemas
from .tasks import enqueue_cloud_task, process_analysis_run

OPERATION_UNITS = {
    "job_match": 1,
    "interview_questions": 1,
    "market_analysis": 5,
    "resume_tailor": 10,
    "skill_roi": 0,
}


def _owned_resume(db: Session, user_id: int, resume_id: Any) -> models.Resume:
    try:
        value = int(resume_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="A valid resume_id is required") from exc
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == value, models.Resume.user_id == user_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")
    return resume


def validate_analysis_input(
    db: Session,
    *,
    user_id: int,
    operation: str,
    opportunity_id: str | None,
    payload: dict[str, Any],
) -> None:
    opportunity = get_opportunity(db, user_id, opportunity_id) if opportunity_id else None
    if operation == "job_match":
        resume = _owned_resume(db, user_id, payload.get("resume_id"))
        description = payload.get("job_description") or (
            opportunity.job_description if opportunity else ""
        )
        if not isinstance(description, (str, list)) or len(str(description).strip()) < 20:
            raise HTTPException(status_code=422, detail="Job description is too short")
        payload["resume_id"] = resume.id
        payload.setdefault("job_title", opportunity.title if opportunity else "Target role")
        payload.setdefault("company", opportunity.company if opportunity else "")
        payload["job_description"] = description
    elif operation == "interview_questions":
        if not opportunity:
            raise HTTPException(status_code=422, detail="opportunity_id is required")
    elif operation == "market_analysis":
        role = str(payload.get("target_role") or "").strip()
        if len(role) < 2:
            raise HTTPException(status_code=422, detail="target_role is required")
        if payload.get("resume_id") is not None:
            _owned_resume(db, user_id, payload["resume_id"])
    elif operation == "resume_tailor":
        if not opportunity:
            raise HTTPException(status_code=422, detail="opportunity_id is required")
        if not opportunity.resume_id:
            raise HTTPException(status_code=422, detail="Connect a resume before tailoring")
        _owned_resume(db, user_id, opportunity.resume_id)
        approved_count = (
            db.query(models.EvidenceItem.id)
            .filter(
                models.EvidenceItem.user_id == user_id,
                models.EvidenceItem.resume_id == opportunity.resume_id,
                models.EvidenceItem.approval_state == "approved",
            )
            .limit(1)
            .first()
        )
        if not approved_count:
            raise HTTPException(
                status_code=422,
                detail="Approve at least one evidence item before tailoring",
            )
        payload["resume_id"] = opportunity.resume_id
    elif operation == "skill_roi":
        return
    else:
        raise HTTPException(status_code=422, detail="Unsupported analysis operation")


def create_run(
    db: Session,
    *,
    user_id: int,
    payload: schemas.AnalysisRunCreate,
    header_idempotency_key: str | None,
) -> tuple[models.AnalysisRun, bool]:
    idempotency_key = (header_idempotency_key or payload.idempotency_key or "").strip()
    if len(idempotency_key) < 8 or len(idempotency_key) > 160:
        raise HTTPException(
            status_code=422,
            detail="Provide an Idempotency-Key header between 8 and 160 characters",
        )
    input_payload = dict(payload.input)
    validate_analysis_input(
        db,
        user_id=user_id,
        operation=payload.operation,
        opportunity_id=payload.opportunity_id,
        payload=input_payload,
    )
    fingerprint = payload_fingerprint(
        {
            "operation": payload.operation,
            "opportunity_id": payload.opportunity_id,
            "input": input_payload,
        }
    )
    existing = (
        db.query(models.AnalysisRun)
        .filter(
            models.AnalysisRun.user_id == user_id,
            models.AnalysisRun.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(
                status_code=409,
                detail="This idempotency key was already used with different input",
            )
        return existing, False

    now = utcnow()
    run = models.AnalysisRun(
        id=public_id("run"),
        user_id=user_id,
        opportunity_id=payload.opportunity_id,
        operation=payload.operation,
        status="queued",
        idempotency_key=idempotency_key,
        input_fingerprint=fingerprint,
        input_payload=input_payload,
        estimated_units=OPERATION_UNITS[payload.operation],
        committed_units=0,
        usage_state="pending",
        created_at=now,
        updated_at=now,
    )
    db.add(run)
    try:
        db.flush()
        reserve_run_usage(
            db,
            user_id=user_id,
            run=run,
            units=run.estimated_units,
        )
        if payload.opportunity_id:
            opportunity = get_opportunity(db, user_id, payload.opportunity_id)
            opportunity.latest_analysis_run_id = run.id
            opportunity.updated_at = now
        db.commit()
    except InsufficientUnitsError as exc:
        db.rollback()
        raise HTTPException(
            status_code=402,
            detail={
                "message": str(exc),
                "balance": exc.balance,
                "required": exc.required,
            },
        ) from exc
    db.refresh(run)
    return run, True


def dispatch_run(
    db: Session,
    run: models.AnalysisRun,
    background_tasks: BackgroundTasks,
) -> None:
    mode = (os.getenv("ANALYSIS_TASKS_MODE") or "inline").strip().lower()
    try:
        if mode == "cloud_tasks":
            enqueue_cloud_task(run.id)
        elif mode == "manual":
            return
        elif mode == "inline":
            background_tasks.add_task(process_analysis_run, run.id)
        else:
            raise ValueError(f"Unsupported ANALYSIS_TASKS_MODE: {mode}")
    except Exception as exc:
        current = (
            db.query(models.AnalysisRun)
            .filter(models.AnalysisRun.id == run.id)
            .with_for_update()
            .one()
        )
        current.status = "failed"
        current.error_code = "dispatch_failed"
        current.error_message = str(exc)[:500]
        current.completed_at = utcnow()
        current.updated_at = utcnow()
        release_run_usage(db, current, reason="Task dispatch failed")
        db.commit()
        raise HTTPException(status_code=503, detail="Analysis could not be queued") from exc


def get_owned_run(db: Session, user_id: int, run_id: str) -> models.AnalysisRun:
    run = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.id == run_id, models.AnalysisRun.user_id == user_id)
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    return run


def cancel_run(db: Session, user_id: int, run_id: str) -> models.AnalysisRun:
    run = (
        db.query(models.AnalysisRun)
        .filter(models.AnalysisRun.id == run_id, models.AnalysisRun.user_id == user_id)
        .with_for_update()
        .first()
    )
    if not run:
        raise HTTPException(status_code=404, detail="Analysis run not found")
    if run.status in {"succeeded", "failed", "cancelled"}:
        return run
    run.cancel_requested = True
    run.updated_at = utcnow()
    if run.status == "queued":
        run.status = "cancelled"
        run.cancelled_at = utcnow()
        release_run_usage(db, run, reason="Cancelled before execution")
    db.commit()
    db.refresh(run)
    return run
