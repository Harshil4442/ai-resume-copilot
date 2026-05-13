import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from ..database import get_db
from .. import models, schemas
from ..services.matching import (
    extract_required_skills_from_jd,
    build_skill_confidence_map,
    _batch_get_coverages,
    _normalize_skill_list,
    compute_skill_scores,
    combine_scores,
    score_to_grade,
    get_dynamic_weights,
)
from ..security import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/match", response_model=schemas.JobMatchResponse)
def match_job(
    payload: schemas.JobMatchRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == payload.resume_id,
                models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found for this user")

    jd_text = (
        payload.job_description
        if isinstance(payload.job_description, str)
        else str(payload.job_description)
    )
    sections      = resume.sections or {}
    exp_years     = resume.experience_years or 0.0
    resume_skills = resume.skills or []

    # ── Step 1: Extract JD skills ────────────────────────────────────────────
    required_skills: list = []
    try:
        from ..services.llm_client import extract_jd_skills_llm
        required_skills = extract_jd_skills_llm(jd_text)
    except Exception:
        required_skills = extract_required_skills_from_jd(jd_text)

    # ── Step 2: Skill confidence map (no LLM — purely section-based) ─────────
    confidence_map = build_skill_confidence_map(resume_skills, sections)

    # ── Step 3: Pairwise coverage (DB → LLM) ─────────────────────────────────
    rs_norm  = _normalize_skill_list(resume_skills)
    req_norm = [s.lower() for s in required_skills]
    coverage_map = _batch_get_coverages(rs_norm, req_norm, db)

    # ── Step 4: Evidence-weighted skill scoring ───────────────────────────────
    applied_score, claimed_score, verif_rate, full_matches, partial_matches, true_gaps = (
        compute_skill_scores(rs_norm, req_norm, coverage_map, confidence_map, jd_text)
    )

    # ── Step 5: Holistic multi-dimensional scoring (LLM) ─────────────────────
    holistic_dimensions: list = []
    improvement_tips:    list = []
    try:
        from ..services.llm_client import compute_holistic_match_llm
        holistic_result = compute_holistic_match_llm(
            experience_text       = sections.get("experience", ""),
            projects_text         = sections.get("projects",   ""),
            education_text        = sections.get("education",  ""),
            resume_skills         = resume_skills,
            experience_years      = exp_years,
            jd_text               = jd_text,
            job_title             = payload.job_title,
            applied_skills_score  = applied_score,
            claimed_skills_score  = claimed_score,
            skill_verification_rate = verif_rate,
        )
        holistic_dimensions = holistic_result.get("dimensions", [])
        improvement_tips    = holistic_result.get("improvement_tips", [])
    except Exception as e:
        log.error("compute_holistic_match_llm failed: %s", e, exc_info=True)
        holistic_dimensions = [
            {"name": d, "score": 50, "feedback": f"Analysis unavailable ({type(e).__name__}: {str(e)[:80]})"}
            for d in get_dynamic_weights(exp_years)[0].keys()
        ]

    # ── Step 6: Combine all dimensions into one overall score ─────────────────
    overall_score = combine_scores(
        applied_score, claimed_score, verif_rate, holistic_dimensions, exp_years
    )
    grade = score_to_grade(overall_score)

    # ── Step 7: Fit summary ───────────────────────────────────────────────────
    fit_summary = ""
    try:
        from ..services.llm_client import generate_fit_summary_llm
        fit_summary = generate_fit_summary_llm(
            resume_skills  = resume_skills,
            job_title      = payload.job_title,
            jd_text        = jd_text,
            match_score    = overall_score,
            missing_skills = true_gaps,
            weak_skills    = [p["via"] for p in partial_matches],
        )
    except Exception:
        fit_summary = (
            f"{grade} match ({overall_score:.0f}/100) for {payload.job_title}. "
            + (f"Key gaps: {', '.join(true_gaps[:3])}." if true_gaps else "Strong overall fit.")
        )

    # ── Step 8: Persist ───────────────────────────────────────────────────────
    match = models.JobMatch(
        user_id                 = current_user.id,
        resume_id               = resume.id,
        job_title               = payload.job_title,
        company                 = payload.company or "",
        job_description         = jd_text,
        match_score             = float(overall_score),
        required_skills         = req_norm,
        full_matches            = full_matches,
        partial_matches         = partial_matches,
        true_gaps               = true_gaps,
        fit_summary             = fit_summary,
        dimension_scores        = holistic_dimensions,
        skill_verification_rate = float(verif_rate),
        improvement_tips        = improvement_tips,
    )
    db.add(match)
    db.commit()
    db.refresh(match)

    return schemas.JobMatchResponse(
        match_id                = match.id,
        match_score             = overall_score,
        grade                   = grade,
        required_skills         = req_norm,
        full_matches            = full_matches,
        partial_matches         = [schemas.PartialMatch(**p) for p in partial_matches],
        true_gaps               = true_gaps,
        skill_verification_rate = verif_rate,
        dimensions              = [schemas.DimensionScore(**d) for d in holistic_dimensions],
        fit_summary             = fit_summary,
        improvement_tips        = improvement_tips,
    )


@router.get("/matches", response_model=schemas.JobMatchHistoryResponse)
def get_match_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    matches = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.user_id == current_user.id)
        .order_by(models.JobMatch.created_at.desc())
        .limit(50)
        .all()
    )
    return schemas.JobMatchHistoryResponse(
        matches=[
            schemas.JobMatchHistoryItem(
                match_id   = m.id,
                job_title  = m.job_title,
                company    = m.company,
                match_score= m.match_score,
                created_at = m.created_at,
            )
            for m in matches
        ]
    )