from __future__ import annotations

import hashlib
import json
import os
from typing import Any

from fastapi import BackgroundTasks, HTTPException
from sqlalchemy.orm import Session

from ... import models
from ...billing import catalog as pricing
from ..career.service import get_opportunity
from ..common import payload_fingerprint, public_id, utcnow
from ..usage import InsufficientUnitsError, release_run_usage, reserve_run_usage
from . import schemas
from .models import AnalysisRequestKey

OPERATION_UNITS = pricing.ANALYSIS_OPERATION_UNITS


def _dependency_snapshot(
    db: Session, user_id: int, operation: str, opportunity_id: str | None, payload: dict,
    *, paid_context: bool = False,
) -> dict:
    """Private semantic reuse includes mutable facts and versioned behavior."""
    from ...services.basic_matching import SCORING_VERSION
    from ...services.interview_catalog import CATALOG_VERSION
    from ...services.interview_contract import contract_versions
    from ...services.market.skill_extractor import SKILL_EXTRACTOR_VERSION

    dependencies: dict[str, Any] = {
        "contract": "analysis-input-v2", "skills": SKILL_EXTRACTOR_VERSION,
        "scoring": SCORING_VERSION, "interview": CATALOG_VERSION,
    }
    opportunity = get_opportunity(db, user_id, opportunity_id) if opportunity_id else None
    if opportunity:
        dependencies["job"] = {
            "title": opportunity.title, "description": opportunity.job_description,
            "resume_id": opportunity.resume_id,
        }
        if operation == "interview_questions":
            dependencies["job"].update({
                "company": opportunity.company, "location": opportunity.location,
                "source": opportunity.source, "source_url": opportunity.source_url,
                # Matching writes derived labels into this otherwise preserved
                # source snapshot. Those are not interview inputs or revisions.
                "snapshot": {key: value for key, value in (opportunity.job_snapshot or {}).items()
                             if key not in {"required_skills", "match_mode", "scoring_version"}},
            })
    if operation == "interview_questions":
        dependencies["interview_contract"] = contract_versions()
        profile = db.query(models.UserProfile).filter_by(user_id=user_id).first()
        dependencies["preferences"] = {
            "target_role": profile.target_role, "job_type": profile.preferred_job_type,
            "location": profile.preferred_location,
        } if profile else None
    resume_id = payload.get("resume_id") or (opportunity.resume_id if opportunity else None)
    if resume_id:
        resume = _owned_resume(db, user_id, resume_id)
        dependencies["resume"] = {
            "source_sha256": hashlib.sha256(resume.source_document or b"").hexdigest(),
            "source_format": resume.source_format, "text": resume.raw_text,
            "sections": resume.sections, "skills": resume.skills,
            "experience": resume.experience_years,
        }
    if operation in {"interview_questions", "resume_tailor"}:
        evidence_query = db.query(models.EvidenceItem).filter(
            models.EvidenceItem.user_id == user_id,
            models.EvidenceItem.approval_state == "approved",
        )
        if resume_id:
            evidence_query = evidence_query.filter(models.EvidenceItem.resume_id == resume_id)
        dependencies["approved_evidence"] = [
            {"id": item.id, "text": item.evidence_text, "skills": item.skills,
             "metrics": item.metrics, "title": item.title,
             **({"correction_version": item.updated_at.isoformat() if item.updated_at else None,
                 "provenance": item.provenance, "source_ref": item.source_ref,
                 "category": item.category, "confidence": item.confidence}
                if operation == "interview_questions" else {})}
            for item in evidence_query.order_by(models.EvidenceItem.id).all()
        ]
    if payload.get("mode") == "enhanced" or operation == "resume_tailor":
        from ...services.llm_client import GEMINI_FALLBACK_MODELS
        from ...services.prompt_privacy import PROMPT_PRIVACY_VERSION

        dependencies["generation"] = {
            "prompt": {"job_match": "match-mega-v2", "interview_questions": contract_versions()["prompt"], "resume_tailor": "resume-source-v7" if paid_context else "resume-source-v6"}.get(operation),
            "model_policy": os.getenv("LLM_MODEL", "unconfigured"),
            "provider_base": os.getenv("LLM_API_BASE", ""),
            "fallback_policy": GEMINI_FALLBACK_MODELS,
            "privacy_policy": PROMPT_PRIVACY_VERSION,
        }
        if operation == "interview_questions":
            from ...services.llm_client import LLM_API_BASE, LLM_MODEL

            dependencies["generation"].update({"effective_model": LLM_MODEL, "effective_provider_base": LLM_API_BASE})
        if operation == "resume_tailor" and paid_context:
            dependencies["generation"]["context_selection"] = "full-editable-source-v1"
    return dependencies


def ensure_interview_inputs_current(db: Session, run: models.AnalysisRun, payload: dict) -> None:
    """Reject work whose selected evidence/job changed after its quote or model call."""
    from ...services.interview_contract import InterviewInputsChanged

    # Model work occurs outside the result-write transaction. Reload mutable
    # source rows so the session identity map cannot mask a concurrent edit.
    db.expire_all()
    semantic_input = {key: value for key, value in payload.items() if key != "_accepted_analysis_quote"}
    current = payload_fingerprint({
        "operation": run.operation, "opportunity_id": run.opportunity_id,
        "input": semantic_input,
        "dependencies": _dependency_snapshot(db, run.user_id, run.operation, run.opportunity_id, semantic_input),
    })
    if current != run.input_fingerprint:
        raise InterviewInputsChanged(
            "The selected resume, approved evidence, job or interview contract changed. "
            "Review the current inputs and request a new question set; this attempt will not be charged."
        )


class TailoringInputsChanged(ValueError):
    retryable = False


def ensure_tailoring_inputs_current(
    db: Session, run: models.AnalysisRun, payload: dict,
) -> None:
    """Refuse a paid tailoring result if any quoted source changed."""
    db.expire_all()
    semantic_input = {
        key: value for key, value in payload.items()
        if key != "_accepted_analysis_quote"
    }
    current = payload_fingerprint({
        "operation": run.operation,
        "opportunity_id": run.opportunity_id,
        "input": semantic_input,
        "dependencies": _dependency_snapshot(
            db, run.user_id, run.operation, run.opportunity_id,
            semantic_input, paid_context=True,
        ),
    })
    if current != run.input_fingerprint:
        raise TailoringInputsChanged(
            "The selected job, resume, approved evidence or tailoring contract changed. "
            "Review the current inputs and request a new quote."
        )


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
        mode = payload.setdefault("mode", "basic")
        if mode not in {"basic", "enhanced"}:
            raise HTTPException(status_code=422, detail="mode must be basic or enhanced")
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
        from ...services.interview_contract import (
            QUESTION_COUNT,
            InterviewContextUnavailable,
            build_interview_messages,
            load_approved_evidence,
        )

        if not opportunity:
            raise HTTPException(status_code=422, detail="opportunity_id is required")
        mode = payload.setdefault("mode", "curated")
        if mode not in {"curated", "enhanced"}:
            raise HTTPException(status_code=422, detail="mode must be curated or enhanced")
        try:
            count = int(payload.get("num_questions", 8))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="num_questions must be an integer") from exc
        if mode == "enhanced" and (type(payload.get("num_questions", QUESTION_COUNT)) is not int or count != QUESTION_COUNT):
            raise HTTPException(status_code=422, detail="An enhanced interview set contains exactly eight questions")
        if mode == "curated" and (count < 3 or count > 12):
            raise HTTPException(status_code=422, detail="num_questions must be between 3 and 12")
        payload["num_questions"] = count
        if opportunity.resume_id:
            _owned_resume(db, user_id, opportunity.resume_id)
        # The selected opportunity owns the resume choice. A stray resume_id
        # must not make the cache describe a different resume from execution.
        payload["resume_id"] = opportunity.resume_id
        if mode == "enhanced":
            try:
                build_interview_messages(opportunity.title, opportunity.job_description,
                                         load_approved_evidence(db, user_id, opportunity.resume_id))
            except InterviewContextUnavailable as exc:
                raise HTTPException(status_code=422, detail={
                    "code": "interview_context_unavailable", "message": str(exc),
                }) from None
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
        resume = _owned_resume(db, user_id, opportunity.resume_id)
        if not resume.source_available:
            raise HTTPException(
                status_code=409,
                detail="Upload the original resume file again before tailoring to preserve its formatting.",
            )
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
    from ..employer.admissions import lock_application_set

    lock_application_set(db, user_id)
    idempotency_key = (header_idempotency_key or payload.idempotency_key or "").strip()
    if len(idempotency_key) < 8 or len(idempotency_key) > 160:
        raise HTTPException(
            status_code=422,
            detail="Provide an Idempotency-Key header between 8 and 160 characters",
        )
    input_payload = dict(payload.input)
    if any(key in input_payload for key in ("expense_paid_units", "_expense_paid_units", "expense_funding", "cost_policy_snapshot", "paid_current_reserved_credits", "_accepted_analysis_quote")):
        raise HTTPException(422, detail="Funding allocation is server-owned and cannot be supplied by the client")
    from ..credits.analysis import _quote_rejected, recover_paid_run

    recovered = recover_paid_run(db, user_id=user_id, payload=payload, idempotency_key=idempotency_key)
    if recovered is not None:
        return recovered
    try:
        validate_analysis_input(db, user_id=user_id, operation=payload.operation,
            opportunity_id=payload.opportunity_id, payload=input_payload)
    except HTTPException as exc:
        if payload.accepted_service_quote_id and exc.status_code in {404, 409, 422}:
            raise _quote_rejected("The selected inputs are unavailable or changed. Review the current inputs before a new quote.") from None
        raise
    # Serialize admission for one owner, including equivalent requests with
    # different client keys. This short lock ends before any worker/model call.
    db.query(models.User.id).filter(models.User.id == user_id).with_for_update().one()
    dependencies = _dependency_snapshot(
        db, user_id, payload.operation, payload.opportunity_id, input_payload,
        paid_context=bool(payload.accepted_service_quote_id),
    )
    fingerprint = payload_fingerprint(
        {
            "operation": payload.operation,
            "opportunity_id": payload.opportunity_id,
            "input": input_payload,
            "dependencies": dependencies,
        }
    )
    alias = db.get(AnalysisRequestKey, (user_id, idempotency_key))
    if alias:
        if alias.input_fingerprint != fingerprint:
            raise HTTPException(status_code=409, detail="This idempotency key was already used with different input")
        return get_owned_run(db, user_id, alias.analysis_run_id), False
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
        db.add(AnalysisRequestKey(user_id=user_id, idempotency_key=idempotency_key,
                                  analysis_run_id=existing.id, input_fingerprint=fingerprint))
        db.commit()
        return existing, False

    if not input_payload.get("force_new") and payload.operation in {
        "job_match", "interview_questions", "resume_tailor",
    }:
        reusable = (
            db.query(models.AnalysisRun)
            .filter(
                models.AnalysisRun.user_id == user_id,
                models.AnalysisRun.operation == payload.operation,
                models.AnalysisRun.input_fingerprint == fingerprint,
                models.AnalysisRun.status.in_(["queued", "running", "succeeded"]),
                models.AnalysisRun.cancel_requested.is_(False),
                models.AnalysisRun.input_purged_at.is_(None),
                models.AnalysisRun.result_purged_at.is_(None),
            )
            .order_by(models.AnalysisRun.created_at.desc())
            .first()
        )
        if reusable:
            db.add(AnalysisRequestKey(user_id=user_id, idempotency_key=idempotency_key,
                                      analysis_run_id=reusable.id, input_fingerprint=fingerprint))
            db.commit()
            return reusable, False

    from ..credits.analysis import maybe_create_paid_run

    unified = maybe_create_paid_run(db, user_id=user_id, payload=payload,
        idempotency_key=idempotency_key, input_payload=input_payload, fingerprint=fingerprint)
    if unified is not None:
        return unified

    # Matching owned requests above return their original run and stored units
    # without a new reservation. Only a new tailoring admission accepts a quote.
    if payload.operation == "resume_tailor":
        quote = {"pricing_version": pricing.ANALYSIS_PRICING_VERSION,
                 "units": OPERATION_UNITS[payload.operation]}
        if payload.accepted_quote is None or payload.accepted_quote.model_dump() != quote:
            raise HTTPException(status_code=409, detail={
                "code": "analysis_quote_requires_review",
                "message": "Review the current AI-unit quote before starting this analysis.",
            })
        # Add server-owned metadata only after the semantic fingerprint, so a
        # later price change cannot create another paid equivalent request.
        input_payload["_accepted_analysis_quote"] = quote

    from ...services.generation_gate import check_generation_admission

    check_generation_admission(payload.operation, input_payload)
    from ..free_core import policy as free_policy
    from ..free_core import service as free_service

    free_operation = free_service.analysis_operation(payload.operation, input_payload) if free_policy.enabled() else None
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
        estimated_units=0 if free_operation else OPERATION_UNITS[payload.operation],
        generation_attempt_limit=3 if (
            input_payload.get("mode") == "enhanced" or payload.operation == "resume_tailor"
        ) else 0,
        committed_units=0,
        usage_state="pending",
        created_at=now,
        updated_at=now,
    )
    if run.generation_attempt_limit:
        from ...services.model_cost_policy import ModelCostUnavailable, freeze_run_quote

        try:
            freeze_run_quote(run)
        except ModelCostUnavailable as exc:
            db.rollback()
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    db.add(run)
    try:
        if free_operation:
            free_service.admit(db, user_id=user_id, operation=free_operation,
                source_type="analysis_run", source_id=run.id, key="analysis:" + hashlib.sha256(idempotency_key.encode()).hexdigest(),
                fingerprint=fingerprint, input_bytes=len(json.dumps({"input": input_payload, "dependencies": dependencies},
                    ensure_ascii=False, default=str).encode("utf-8")), attempts=3)
        db.flush()
        db.add(AnalysisRequestKey(user_id=user_id, idempotency_key=idempotency_key,
                                  analysis_run_id=run.id, input_fingerprint=fingerprint))
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
        from ..dispatch.service import enqueue
        enqueue(
            db, topic="analysis.run", aggregate_id=run.id,
            payload={"run_id": run.id}, key=f"analysis:{run.id}",
        )
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
    except HTTPException:
        db.rollback()
        raise
    db.refresh(run)
    return run, True


def dispatch_run(
    db: Session,
    run: models.AnalysisRun,
    background_tasks: BackgroundTasks,
) -> None:
    mode = (os.getenv("ANALYSIS_TASKS_MODE") or "inline").strip().lower()
    if mode == "manual":
        return
    if mode not in {"inline", "cloud_tasks"}:
        raise HTTPException(status_code=503, detail="Analysis task mode is not configured")
    from ..dispatch.service import dispatch_pending

    background_tasks.add_task(dispatch_pending, limit=100, topics={"analysis.run"})


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
