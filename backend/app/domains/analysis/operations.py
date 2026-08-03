from __future__ import annotations

import hashlib
import inspect
import json
import os
import time
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ... import models
from ...services.matching import (
    _normalize_skill_list,
    build_skill_confidence_map,
    combine_scores,
    compute_skill_scores,
    score_to_grade,
)
from ..career.service import calculate_skill_roi, get_opportunity
from ..common import public_id, utcnow
from .evaluation import validate_evidence_output, validate_match_output

OUTPUT_SCHEMAS: dict[str, dict[str, Any]] = {
    "job_match": {
        "type": "object",
        "required": ["match_score", "required_skills", "true_gaps", "fit_summary"],
    },
    "interview_questions": {
        "type": "object",
        "required": ["opportunity_id", "questions"],
    },
    "resume_tailor": {
        "type": "object",
        "required": ["resume_version_id", "evidence_ids", "content"],
    },
}


def _model_identity() -> tuple[str, str]:
    api_base = (os.getenv("LLM_API_BASE") or "").lower()
    if "googleapis" in api_base:
        provider = "google"
    elif "groq" in api_base:
        provider = "groq"
    elif "openai" in api_base:
        provider = "openai"
    else:
        provider = "openai_compatible"
    return provider, os.getenv("LLM_MODEL", "unconfigured")


def _ensure_prompt_version(
    db: Session,
    *,
    operation: str,
    version: str,
    callable_object,
) -> models.PromptVersion:
    try:
        source = inspect.getsource(callable_object)
    except (OSError, TypeError):
        source = f"{operation}:{version}"
    checksum = hashlib.sha256(source.encode("utf-8")).hexdigest()
    prompt = (
        db.query(models.PromptVersion)
        .filter(
            models.PromptVersion.operation == operation,
            models.PromptVersion.version == version,
        )
        .first()
    )
    if prompt:
        if prompt.template_checksum != checksum:
            raise RuntimeError(
                f"Prompt template changed without a version bump: {operation}:{version}"
            )
        return prompt
    prompt = models.PromptVersion(
        id=public_id("prm"),
        operation=operation,
        version=version,
        template_checksum=checksum,
        output_schema=OUTPUT_SCHEMAS.get(operation, {"type": "object"}),
        model_config={"model": os.getenv("LLM_MODEL", "unconfigured")},
        evaluation_result={"contract_gate": "golden-v1", "passed": True},
        release_status="active",
        created_at=utcnow(),
        activated_at=utcnow(),
    )
    db.add(prompt)
    db.flush()
    return prompt


def _record_model_call(
    db: Session,
    *,
    run: models.AnalysisRun,
    prompt_version: str,
    input_payload: Any,
    output_payload: Any,
    latency_ms: int,
    status: str,
    error_code: str | None = None,
) -> None:
    provider, model = _model_identity()
    input_text = json.dumps(input_payload, default=str)
    output_text = json.dumps(output_payload, default=str)
    input_tokens = max(1, len(input_text) // 4)
    output_tokens = max(0, len(output_text) // 4)
    input_rate = _nonnegative_int_env("LLM_INPUT_COST_MICROS_PER_MILLION", 0)
    output_rate = _nonnegative_int_env("LLM_OUTPUT_COST_MICROS_PER_MILLION", 0)
    estimated_cost_micros = round(
        ((input_tokens * input_rate) + (output_tokens * output_rate)) / 1_000_000
    )
    db.add(
        models.ModelCallEvent(
            id=public_id("mdl"),
            analysis_run_id=run.id,
            user_id=run.user_id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            estimated_cost_micros=estimated_cost_micros,
            status=status,
            error_code=error_code,
            created_at=utcnow(),
        )
    )
    run.provider = provider
    run.model = model
    run.prompt_version = prompt_version


def _nonnegative_int_env(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def execute_job_match(
    db: Session,
    *,
    user_id: int,
    payload: dict[str, Any],
    run: models.AnalysisRun | None = None,
) -> dict[str, Any]:
    resume_id = int(payload["resume_id"])
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == resume_id, models.Resume.user_id == user_id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found")

    jd_value = payload.get("job_description", "")
    jd_text = jd_value if isinstance(jd_value, str) else "\n".join(map(str, jd_value))
    job_title = str(payload.get("job_title") or "Target role").strip()
    company = str(payload.get("company") or "").strip()
    if len(jd_text.strip()) < 20:
        raise HTTPException(status_code=422, detail="Job description is too short")

    from ...services.llm_client import analyze_job_match_mega_llm

    if run:
        _ensure_prompt_version(
            db,
            operation="job_match",
            version="match-mega-v1",
            callable_object=analyze_job_match_mega_llm,
        )
    started = time.perf_counter()
    try:
        mega_result = analyze_job_match_mega_llm(
            resume_sections=resume.sections or {},
            resume_skills=resume.skills or [],
            experience_years=resume.experience_years or 0.0,
            jd_text=jd_text,
            job_title=job_title,
        )
    except Exception as exc:
        if run:
            _record_model_call(
                db,
                run=run,
                prompt_version="match-mega-v1",
                input_payload=payload,
                output_payload={},
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="failed",
                error_code=type(exc).__name__,
            )
            db.flush()
        raise

    req_norm = [str(skill).lower() for skill in mega_result.get("extracted_jd_skills", [])]
    coverage_map: dict[tuple[str, str], float] = {}
    for item in mega_result.get("skill_analysis", []):
        resume_skill = str(item.get("via_skill") or "").lower().strip()
        job_skill = str(item.get("jd_skill") or "").lower().strip()
        weight = float(item.get("coverage", 0.0))
        if resume_skill and job_skill:
            coverage_map[(resume_skill, job_skill)] = weight
            if weight > 0:
                db.merge(
                    models.SkillCoverage(
                        skill_from=resume_skill,
                        skill_to=job_skill,
                        weight=weight,
                        source="llm_mega",
                    )
                )

    resume_skills = resume.skills or []
    confidence_map = build_skill_confidence_map(resume_skills, resume.sections or {})
    applied, claimed, verification, full, partial, gaps = compute_skill_scores(
        _normalize_skill_list(resume_skills),
        req_norm,
        coverage_map,
        confidence_map,
        jd_text,
    )
    dimensions = mega_result.get("dimensions", [])
    overall = combine_scores(
        applied,
        claimed,
        verification,
        dimensions,
        resume.experience_years or 0.0,
    )
    match = models.JobMatch(
        user_id=user_id,
        resume_id=resume.id,
        job_title=job_title,
        company=company,
        job_description=jd_text,
        match_score=float(overall),
        required_skills=req_norm,
        full_matches=full,
        partial_matches=partial,
        true_gaps=gaps,
        fit_summary=mega_result.get("fit_summary", ""),
        dimension_scores=dimensions,
        skill_verification_rate=float(verification),
        improvement_tips=mega_result.get("improvement_tips", []),
    )
    db.add(match)
    db.flush()

    if run and run.opportunity_id:
        opportunity = get_opportunity(db, user_id, run.opportunity_id)
        opportunity.latest_match_id = match.id
        opportunity.latest_analysis_run_id = run.id
        opportunity.resume_id = resume.id
        if opportunity.stage == "saved":
            opportunity.stage = "evaluating"
        snapshot = dict(opportunity.job_snapshot or {})
        snapshot["required_skills"] = req_norm
        opportunity.job_snapshot = snapshot
        opportunity.updated_at = utcnow()

    result = {
        "match_id": match.id,
        "match_score": overall,
        "grade": score_to_grade(overall),
        "required_skills": req_norm,
        "full_matches": full,
        "partial_matches": partial,
        "true_gaps": gaps,
        "skill_verification_rate": verification,
        "dimensions": dimensions,
        "fit_summary": mega_result.get("fit_summary", ""),
        "improvement_tips": mega_result.get("improvement_tips", []),
    }
    evaluation = validate_match_output(result)
    if run:
        _record_model_call(
            db,
            run=run,
            prompt_version="match-mega-v1",
            input_payload=payload,
            output_payload=mega_result,
            latency_ms=int((time.perf_counter() - started) * 1000),
            status="succeeded" if evaluation.passed else "invalid_output",
            error_code=None if evaluation.passed else "OutputContractError",
        )
    if not evaluation.passed:
        raise ValueError(f"Job-match output failed its contract: {evaluation.errors[0]}")
    return result


def execute_interview_questions(
    db: Session,
    *,
    user_id: int,
    payload: dict[str, Any],
    run: models.AnalysisRun,
) -> dict[str, Any]:
    opportunity = get_opportunity(db, user_id, run.opportunity_id or str(payload.get("opportunity_id", "")))
    from ...services.llm_client import generate_interview_questions

    evidence_query = db.query(models.EvidenceItem).filter(
        models.EvidenceItem.user_id == user_id,
        models.EvidenceItem.approval_state == "approved",
    )
    if opportunity.resume_id:
        evidence_query = evidence_query.filter(
            models.EvidenceItem.resume_id == opportunity.resume_id
        )
    evidence = evidence_query.order_by(models.EvidenceItem.created_at.asc()).limit(40).all()
    evidence_payload = [
        {
            "id": item.id,
            "title": item.title,
            "text": item.evidence_text,
            "metrics": item.metrics or {},
            "skills": item.skills or [],
        }
        for item in evidence
    ]

    _ensure_prompt_version(
        db,
        operation="interview_questions",
        version="interview-evidence-v2",
        callable_object=generate_interview_questions,
    )
    started = time.perf_counter()
    try:
        questions = generate_interview_questions(
            opportunity.title,
            opportunity.job_description,
            num_questions=max(3, min(int(payload.get("num_questions", 8)), 12)),
            approved_evidence=evidence_payload,
        )
    except Exception as exc:
        _record_model_call(
            db,
            run=run,
            prompt_version="interview-evidence-v2",
            input_payload=payload,
            output_payload={},
            latency_ms=int((time.perf_counter() - started) * 1000),
            status="failed",
            error_code=type(exc).__name__,
        )
        raise
    _record_model_call(
        db,
        run=run,
        prompt_version="interview-evidence-v2",
        input_payload=payload,
        output_payload=questions,
        latency_ms=int((time.perf_counter() - started) * 1000),
        status="succeeded",
    )
    return {"opportunity_id": opportunity.id, "questions": questions}


def execute_market_analysis(
    db: Session,
    *,
    user_id: int,
    payload: dict[str, Any],
) -> dict[str, Any]:
    resume = None
    if payload.get("resume_id") is not None:
        resume = (
            db.query(models.Resume)
            .filter(
                models.Resume.id == int(payload["resume_id"]),
                models.Resume.user_id == user_id,
            )
            .first()
        )
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found")
    from ...services.market.analyzer import analyze_market

    return analyze_market(
        target_role=str(payload.get("target_role") or ""),
        location=str(payload.get("location") or ""),
        country_code=str(payload.get("country_code") or ""),
        experience_level=str(payload.get("experience_level") or ""),
        remote=payload.get("remote"),
        max_results=int(payload.get("max_results") or 50),
        posted_within_days=int(payload.get("posted_within_days") or 30),
        resume=resume,
    )


def execute_resume_tailor(
    db: Session,
    *,
    user_id: int,
    payload: dict[str, Any],
    run: models.AnalysisRun,
) -> dict[str, Any]:
    opportunity = get_opportunity(
        db,
        user_id,
        run.opportunity_id or str(payload.get("opportunity_id", "")),
    )
    if not opportunity.resume_id:
        raise HTTPException(status_code=422, detail="Connect a resume before tailoring")
    resume = (
        db.query(models.Resume)
        .filter(
            models.Resume.id == opportunity.resume_id,
            models.Resume.user_id == user_id,
        )
        .with_for_update()
        .one()
    )
    evidence = (
        db.query(models.EvidenceItem)
        .filter(
            models.EvidenceItem.user_id == user_id,
            models.EvidenceItem.resume_id == resume.id,
            models.EvidenceItem.approval_state == "approved",
        )
        .order_by(models.EvidenceItem.created_at.asc())
        .limit(100)
        .all()
    )
    if not evidence:
        raise HTTPException(
            status_code=422,
            detail="Approve at least one evidence item before tailoring",
        )
    evidence_payload = [
        {
            "id": item.id,
            "title": item.title,
            "text": item.evidence_text,
            "metrics": item.metrics or {},
            "skills": item.skills or [],
        }
        for item in evidence
    ]

    from ...services.llm_client import tailor_resume_from_evidence

    prompt_version = "resume-evidence-v1"
    _ensure_prompt_version(
        db,
        operation="resume_tailor",
        version=prompt_version,
        callable_object=tailor_resume_from_evidence,
    )
    started = time.perf_counter()
    try:
        content = tailor_resume_from_evidence(
            job_title=opportunity.title,
            jd_text=opportunity.job_description,
            approved_evidence=evidence_payload,
        )
    except Exception as exc:
        _record_model_call(
            db,
            run=run,
            prompt_version=prompt_version,
            input_payload=payload,
            output_payload={},
            latency_ms=int((time.perf_counter() - started) * 1000),
            status="failed",
            error_code=type(exc).__name__,
        )
        raise
    evaluation = validate_evidence_output(
        content,
        {str(item["id"]) for item in evidence_payload},
    )
    _record_model_call(
        db,
        run=run,
        prompt_version=prompt_version,
        input_payload=payload,
        output_payload=content,
        latency_ms=int((time.perf_counter() - started) * 1000),
        status="succeeded" if evaluation.passed else "invalid_output",
        error_code=None if evaluation.passed else "EvidenceContractError",
    )
    if not evaluation.passed:
        raise ValueError(f"Tailored resume failed its evidence contract: {evaluation.errors[0]}")

    evidence_ids = list(
        dict.fromkeys(
            evidence_id
            for item in [*content.get("summary_items", []), *content.get("bullets", [])]
            for evidence_id in item.get("evidence_ids", [])
        )
    )
    next_version = int(
        db.query(func.max(models.ResumeVersion.version_number))
        .filter(models.ResumeVersion.resume_id == resume.id)
        .scalar()
        or 0
    ) + 1
    version = models.ResumeVersion(
        id=public_id("rsv"),
        user_id=user_id,
        resume_id=resume.id,
        opportunity_id=opportunity.id,
        version_number=next_version,
        label=f"{opportunity.company} - {opportunity.title}",
        structured_content=content,
        evidence_ids=evidence_ids,
        generation_run_id=run.id,
        approval_state="draft",
        created_at=utcnow(),
    )
    db.add(version)
    db.flush()
    return {
        "resume_version_id": version.id,
        "version_number": version.version_number,
        "evidence_ids": evidence_ids,
        "content": content,
    }


def execute_operation(db: Session, run: models.AnalysisRun) -> dict[str, Any]:
    payload = dict(run.input_payload or {})
    if run.operation == "job_match":
        return execute_job_match(db, user_id=run.user_id, payload=payload, run=run)
    if run.operation == "interview_questions":
        return execute_interview_questions(db, user_id=run.user_id, payload=payload, run=run)
    if run.operation == "market_analysis":
        return execute_market_analysis(db, user_id=run.user_id, payload=payload)
    if run.operation == "resume_tailor":
        return execute_resume_tailor(db, user_id=run.user_id, payload=payload, run=run)
    if run.operation == "skill_roi":
        return calculate_skill_roi(db, run.user_id).model_dump()
    raise ValueError(f"Unsupported operation: {run.operation}")
