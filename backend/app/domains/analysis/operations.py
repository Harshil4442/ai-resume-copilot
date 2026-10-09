from __future__ import annotations

import hashlib
import inspect
import json
import logging
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
from ...services.result_commit import begin_result_commit
from ..career.service import calculate_skill_roi, get_opportunity
from ..common import public_id, utcnow
from .evaluation import validate_evidence_output, validate_match_output

log = logging.getLogger("hirewiz.analysis.operations")
_TAILORING_VALIDATION_REASONS = {
    "Return a JSON object containing source_edits": "invalid_output_format",
    "Return between one and twelve useful source replacements": "invalid_edit_count",
    "at least one source replacement is required": "invalid_edit_count",
    "Every source replacement must be an object": "invalid_edit_item",
    "Use each supplied source location at most once": "invalid_source_location",
    "Copy original_text exactly from the source location": "source_text_mismatch",
    "Replacements must change the text and fit its original length": "invalid_replacement_length",
    "Replacements must fit their original length including boundary spacing": "invalid_replacement_length",
    "Use only characters available in the source font": "missing_pdf_glyph",
    "Preserve all original numbers, dates, and metrics exactly": "changed_source_numbers",
    "Every replacement must explain its relevance to the role": "missing_edit_reason",
    "Every replacement must cite supplied approved evidence": "unsupported_evidence",
    "Every factual skill or named term must be supported by the original text or cited evidence": "unsupported_factual_term",
    "The original source text no longer matches the proposed edit.": "source_text_mismatch",
    "Source edits must retain adjoining fragment boundaries.": "changed_fragment_boundary",
    "Source edits must preserve all original numbers and dates.": "changed_source_numbers",
    "The original PDF font is missing a required glyph.": "missing_pdf_glyph",
    "The original PDF whitespace cannot be retained safely.": "unsupported_pdf_whitespace",
    "The original PDF word spacing cannot be retained safely.": "unsafe_pdf_word_spacing",
    "The replacement does not fit its original PDF text slot.": "pdf_slot_overflow",
    "Saved PDF text exceeds its original slot.": "pdf_slot_overflow",
    "The replacement overlaps neighboring PDF content.": "pdf_content_overlap",
    "The original PDF text stream cannot be updated safely.": "unsupported_pdf_text_stream",
    "Saved PDF text or font differs from the accepted source edit.": "changed_pdf_text_or_style",
    "The replacement is too long for its original DOCX paragraph.": "docx_paragraph_overflow",
    "The PDF changed visually outside the accepted text slots.": "changed_pdf_layout",
}
_TAILORING_REASON_CODES = set(_TAILORING_VALIDATION_REASONS.values()) | {
    "unchanged_source_text",
    "invalid_replacement_text",
    "duplicate_source_location",
}

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
    from ...services.generation_budget import current_budget

    budget = current_budget()
    if budget is not None and budget.attempts:
        # Actual per-provider events are already committed independently of
        # the domain transaction; do not add a duplicate logical-call row.
        successful = [item for item in budget.attempts if item.get("status") == "succeeded"]
        last = (successful or budget.attempts)[-1]
        run.provider, run.model = last["provider"], last["model"]
        run.prompt_version = prompt_version
        return
    provider, model = _model_identity()
    input_text = json.dumps(input_payload, default=str)
    output_text = json.dumps(output_payload, default=str)
    input_tokens = max(1, len(input_text) // 4)
    output_tokens = max(0, len(output_text) // 4)
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
            # A logical adapter result without an admitted provider attempt
            # has no verified price/usage. Missing rates never mean free.
            estimated_cost_micros=None,
            cost_state="unavailable",
            token_estimate_provenance="logical-character-estimate-v1",
            status=status,
            error_code=error_code,
            created_at=utcnow(),
        )
    )
    run.provider = provider
    run.model = model
    run.prompt_version = prompt_version


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

    if payload.get("mode", "basic") == "basic":
        from ...services.basic_matching import basic_match

        basic = basic_match(resume.skills or [], jd_text, job_title)
        begin_result_commit(db, user_id, run.id if run is not None else None)
        match = models.JobMatch(
            user_id=user_id, resume_id=resume.id, job_title=job_title,
            company=company, job_description=jd_text, match_score=basic["score"],
            required_skills=basic["required_skills"], full_matches=basic["matched_skills"],
            partial_matches=[], true_gaps=basic["missing_evidence"],
            fit_summary=" ".join(basic["reasons"] + basic["uncertainties"][:1]),
            dimension_scores=[], skill_verification_rate=0.0,
            improvement_tips=["Review missing evidence; add only skills and experience you can substantiate."],
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
            opportunity.job_snapshot = {
                **dict(opportunity.job_snapshot or {}),
                "required_skills": basic["required_skills"],
                "match_mode": "basic", "scoring_version": basic["scoring_version"],
            }
            opportunity.updated_at = utcnow()
        return {
            "match_id": match.id, "match_score": basic["score"],
            "grade": score_to_grade(basic["score"]),
            "required_skills": basic["required_skills"],
            "full_matches": basic["matched_skills"], "partial_matches": [],
            "true_gaps": basic["missing_evidence"], "skill_verification_rate": 0.0,
            "dimensions": [], "fit_summary": match.fit_summary,
            "improvement_tips": match.improvement_tips,
            "mode": "basic", "scoring_version": basic["scoring_version"],
            "uncertainties": basic["uncertainties"], "provenance": "local_rules",
        }

    from ...services.llm_client import analyze_job_match_mega_llm

    if run:
        _ensure_prompt_version(
            db,
            operation="job_match",
            version="match-mega-v2",
            callable_object=analyze_job_match_mega_llm,
        )
        db.commit()  # Release prompt registration before provider admission.
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
            begin_result_commit(db, user_id, run.id)
            _record_model_call(
                db,
                run=run,
                prompt_version="match-mega-v2",
                input_payload=payload,
                output_payload={},
                latency_ms=int((time.perf_counter() - started) * 1000),
                status="failed",
                error_code=type(exc).__name__,
            )
            db.flush()
        raise

    begin_result_commit(db, user_id, run.id if run is not None else None)
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
        "mode": "enhanced", "scoring_version": "enhanced-mega-v2",
        "provenance": "generated",
    }
    evaluation = validate_match_output(result)
    if run:
        _record_model_call(
            db,
            run=run,
            prompt_version="match-mega-v2",
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
    opportunity = get_opportunity(
        db, user_id, run.opportunity_id or str(payload.get("opportunity_id", ""))
    )
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

    if payload.get("mode", "curated") == "curated":
        from ...services.interview_catalog import CATALOG_VERSION, curated_interview_questions

        questions = curated_interview_questions(
            opportunity.title, opportunity.job_description,
            num_questions=max(3, min(int(payload.get("num_questions", 8)), 12)),
            approved_evidence=evidence_payload,
        )
        return {
            "opportunity_id": opportunity.id, "questions": questions,
            "mode": "curated", "provenance": "curated", "catalog_version": CATALOG_VERSION,
        }

    _ensure_prompt_version(
        db,
        operation="interview_questions",
        version="interview-evidence-v4",
        callable_object=generate_interview_questions,
    )
    db.commit()
    started = time.perf_counter()
    try:
        questions = generate_interview_questions(
            opportunity.title,
            opportunity.job_description,
            num_questions=max(3, min(int(payload.get("num_questions", 8)), 12)),
            approved_evidence=evidence_payload,
        )
    except Exception as exc:
        begin_result_commit(db, user_id, run.id)
        _record_model_call(
            db,
            run=run,
            prompt_version="interview-evidence-v4",
            input_payload=payload,
            output_payload={},
            latency_ms=int((time.perf_counter() - started) * 1000),
            status="failed",
            error_code=type(exc).__name__,
        )
        raise
    begin_result_commit(db, user_id, run.id)
    _record_model_call(
        db,
        run=run,
        prompt_version="interview-evidence-v4",
        input_payload=payload,
        output_payload=questions,
        latency_ms=int((time.perf_counter() - started) * 1000),
        status="succeeded",
    )
    return {
        "opportunity_id": opportunity.id,
        "questions": [{**item, "provenance": "generated"} for item in questions],
        "mode": "enhanced", "provenance": "generated", "catalog_version": None,
    }


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

    from ...services.llm_client import TailoringOutputError, tailor_resume_from_evidence
    from ...services.resume_layout import (
        ResumeLayoutError,
        apply_source_edits,
        extract_source_units,
    )

    if not resume.source_document or resume.source_format not in {"pdf", "docx", "tex", "texzip"}:
        raise TailoringOutputError("Upload the original resume before tailoring")
    units = extract_source_units(resume.source_document, resume.source_format)
    # Limit model context by whole units, never by truncating JSON or a source passage.
    source_units: list[dict[str, Any]] = []
    context_length = 0
    for unit in sorted(units, key=lambda item: item.get("generation_priority", 5)):
        if len(source_units) >= 100 or context_length + len(unit["text"]) > 24_000:
            continue
        source_units.append(unit)
        context_length += len(unit["text"])
    if not source_units:
        raise ResumeLayoutError("No source text can be edited while preserving this layout. Keep the original/custom resume or upload editable DOCX/TeX source; protected fonts, macros or image-only text need manual editing.")

    prompt_version = "resume-source-v5"
    _ensure_prompt_version(
        db,
        operation="resume_tailor",
        version=prompt_version,
        callable_object=tailor_resume_from_evidence,
    )
    source_bytes = resume.source_document
    source_format = resume.source_format
    evidence_snapshot = hashlib.sha256(json.dumps(evidence_payload, sort_keys=True, default=str).encode()).hexdigest()
    db.commit()  # Generation and rendering never hold the resume row lock.
    started = time.perf_counter()
    try:
        content = None
        repair_note = ""
        last_validation_error: Exception | None = None
        known_unit_ids = {unit["unit_id"] for unit in source_units}
        first_constraint: tuple[str, str | None] | None = None
        for _attempt in range(3):
            candidate = None
            try:
                candidate = tailor_resume_from_evidence(
                    job_title=opportunity.title,
                    jd_text=opportunity.job_description,
                    approved_evidence=evidence_payload,
                    source_units=source_units,
                    repair_note=repair_note,
                )
                candidate["format_preservation"] = "source"
                candidate["source_format"] = resume.source_format
                rejected = candidate.get("rejected_source_edits")
                if isinstance(rejected, list) and rejected:
                    candidate["partial_tailoring"] = True
                    candidate["omitted_edits"] = len(rejected)
                    log.warning(
                        "Tailoring proposals excluded run_id=%s attempt=%d stage=model_validation rejected=%d retained=%d",
                        run.id,
                        _attempt + 1,
                        len(rejected),
                        len(candidate["source_edits"]),
                    )
                evaluation = validate_evidence_output(
                    candidate,
                    {str(item["id"]) for item in evidence_payload},
                )
                if not evaluation.passed:
                    raise TailoringOutputError(evaluation.errors[0])
                while True:
                    try:
                        if source_format in {"tex", "texzip"}:
                            from ...services.native_tex import prepare_artifact
                            candidate["sealed_native_artifact"] = prepare_artifact(source_bytes, source_format, candidate["source_edits"], evidence_payload)
                        else:
                            apply_source_edits(source_bytes, source_format, candidate["source_edits"])
                        break
                    except ResumeLayoutError as exc:
                        unit_id = getattr(exc, "unit_id", None)
                        edits = candidate["source_edits"]
                        if (
                            unit_id not in known_unit_ids
                            or len(edits) <= 1
                            or not any(item.get("unit_id") == unit_id for item in edits)
                        ):
                            raise
                        # Discard only the identified unsafe proposal, then
                        # verify the entire remaining document again. A saved
                        # version always contains a fully proven set of edits.
                        candidate["source_edits"] = [
                            item for item in edits if item.get("unit_id") != unit_id
                        ]
                        candidate["partial_tailoring"] = True
                        candidate["omitted_edits"] = int(candidate.get("omitted_edits", 0)) + 1
                        reason = _TAILORING_VALIDATION_REASONS.get(
                            str(exc), "other_validation_failure"
                        )
                        log.warning(
                            "Tailoring proposal excluded run_id=%s attempt=%d reason=%s unit_id=%s retained=%d",
                            run.id,
                            _attempt + 1,
                            reason,
                            unit_id,
                            len(candidate["source_edits"]),
                        )
                candidate.pop("rejected_source_edits", None)
                content = candidate
                break
            except (TailoringOutputError, ResumeLayoutError) as exc:
                last_validation_error = exc
                unit_id = getattr(exc, "unit_id", None)
                if not isinstance(unit_id, str) or unit_id not in known_unit_ids:
                    unit_id = None
                reason = _TAILORING_VALIDATION_REASONS.get(str(exc), "other_validation_failure")
                reason_code = getattr(exc, "reason_code", None)
                if isinstance(reason_code, str) and reason_code in _TAILORING_REASON_CODES:
                    reason = reason_code
                proposed_edits = candidate.get("source_edits") if candidate is not None else None
                edit_count = len(proposed_edits) if isinstance(proposed_edits, list) else "unknown"
                # These controlled fields survive the worker's DB rollback and
                # the JSON formatter; never log document or replacement text.
                log.warning(
                    "Tailoring validation rejected run_id=%s attempt=%d type=%s "
                    "reason=%s unit_id=%s source_units=%d edits=%s",
                    run.id,
                    _attempt + 1,
                    type(exc).__name__,
                    reason,
                    unit_id or "none",
                    len(source_units),
                    edit_count,
                )
                repair_note = f"Source unit {unit_id}: {exc}" if unit_id else str(exc)
                hint = getattr(exc, "repair_hint", None)
                if isinstance(hint, str) and hint:
                    repair_note += f" {hint[:350]}"
                # A schema or text repair can expose a different native-fit
                # constraint next. Allow one repair for that new constraint;
                # repeated failures at the same boundary stop after two
                # calls. Every generation is capped at three provider calls.
                constraint = (reason, unit_id)
                if _attempt == 0:
                    first_constraint = constraint
                elif _attempt == 1 and constraint == first_constraint:
                    break
        if content is None:
            raise TailoringOutputError(
                "Could not apply useful changes while preserving the resume format"
            ) from last_validation_error
    except Exception as exc:
        begin_result_commit(db, user_id, run.id)
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
    begin_result_commit(db, user_id, run.id)
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
            for item in content.get("source_edits", [])
            for evidence_id in item.get("evidence_ids", [])
        )
    )
    # Revalidate source and approvals after expensive work. Short write locks
    # now protect version allocation and prevent a revoked fact being saved.
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == resume.id, models.Resume.user_id == user_id)
        .populate_existing().with_for_update().one()
    )
    current_evidence = (
        db.query(models.EvidenceItem)
        .filter(models.EvidenceItem.user_id == user_id, models.EvidenceItem.resume_id == resume.id,
                models.EvidenceItem.approval_state == "approved")
        .order_by(models.EvidenceItem.created_at.asc()).limit(100)
        .populate_existing().with_for_update().all()
    )
    current_payload = [
        {"id": item.id, "title": item.title, "text": item.evidence_text,
         "metrics": item.metrics or {}, "skills": item.skills or []}
        for item in current_evidence
    ]
    current_hash = hashlib.sha256(json.dumps(current_payload, sort_keys=True, default=str).encode()).hexdigest()
    if resume.source_document != source_bytes or current_hash != evidence_snapshot:
        raise HTTPException(status_code=409, detail="Resume source or approved evidence changed during tailoring. Review the changes and try again.")
    next_version = (
        int(
            db.query(func.max(models.ResumeVersion.version_number))
            .filter(models.ResumeVersion.resume_id == resume.id)
            .scalar()
            or 0
        )
        + 1
    )
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
