import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

from ..database import get_db
from .. import models, schemas
from ..services.matching import (
    build_skill_confidence_map,
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
    from ..services.guardrails import verify_and_deduct_credit
    verify_and_deduct_credit(current_user.id, db)

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
    rs_norm       = _normalize_skill_list(resume_skills)

    # Initialise outside the try so the variable is unambiguously defined even
    # if a future edit moves the assignment into a conditional branch.
    req_norm: list[str] = []

    # ── SINGLE MEGA PROMPT CALL ──────────────────────────────────────────────
    try:
        from ..services.llm_client import analyze_job_match_mega_llm
        mega_result = analyze_job_match_mega_llm(
            resume_sections  = sections,
            resume_skills    = resume_skills,
            experience_years = exp_years,
            jd_text          = jd_text,
            job_title        = payload.job_title
        )
    except Exception as e:
        log.error("Mega-Match LLM failed: %s", e, exc_info=True)
        # Check if it was a 429
        err_msg = str(e)
        if "429" in err_msg:
            detail = "API Rate Limit (429) hit. Please wait a minute before trying again, or check your API Billing/Balance."
        else:
            detail = f"Step 1 (LLM Call) failed: {err_msg}"
        raise HTTPException(status_code=500, detail=detail)

    # ── Step 2: Post-processing LLM results ──────────────────────────────────
    try:
        req_norm = [s.lower() for s in mega_result.get("extracted_jd_skills", [])]
        
        coverage_map = {}
        new_coverage_records = []
        for item in mega_result.get("skill_analysis", []):
            rs_skill = (item.get("via_skill") or "").lower().strip()
            jd_skill = (item.get("jd_skill") or "").lower().strip()
            weight   = float(item.get("coverage", 0.0))
            
            if rs_skill and jd_skill:
                coverage_map[(rs_skill, jd_skill)] = weight
                if weight > 0:
                    new_coverage_records.append(models.SkillCoverage(
                        skill_from=rs_skill, skill_to=jd_skill, weight=weight, source="llm_mega"
                    ))
    except Exception as e:
        log.error("JSON Post-processing failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Step 2 (Data Processing) failed: {str(e)}")

    # Bulk persist to Neon
    if new_coverage_records:
        try:
            for rec in new_coverage_records:
                db.merge(rec)
            db.commit()
        except Exception as e:
            db.rollback()
            log.warning("Could not persist mega-coverage to DB: %s", e)
            # We don't raise 500 here so the user still sees their match results 
            # even if the cache save fails.

    # ── Step 3: Skill confidence & weighted scoring ──────────────────────────
    confidence_map = build_skill_confidence_map(resume_skills, sections)
    
    applied_score, claimed_score, verif_rate, full_matches, partial_matches, true_gaps = (
        compute_skill_scores(rs_norm, req_norm, coverage_map, confidence_map, jd_text)
    )

    # ── Step 4: Final combined score ─────────────────────────────────────────
    holistic_dimensions = mega_result.get("dimensions", [])
    overall_score = combine_scores(
        applied_score, claimed_score, verif_rate, holistic_dimensions, exp_years
    )
    grade = score_to_grade(overall_score)
    
    fit_summary      = mega_result.get("fit_summary", "")
    improvement_tips = mega_result.get("improvement_tips", [])

    # ── Step 5: Persist ───────────────────────────────────────────────────────
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
        ]
    )

@router.post("/match/{match_id}/tailor", response_model=schemas.ResumeTailorResponse)
def tailor_resume(
    match_id: int,
    payload: schemas.ResumeTailorRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.guardrails import verify_and_deduct_credit
    
    # 1. Fetch JobMatch
    match = db.query(models.JobMatch).filter(
        models.JobMatch.id == match_id,
        models.JobMatch.user_id == current_user.id
    ).first()
    
    if not match:
        raise HTTPException(status_code=404, detail="Job match not found")
        
    resume = match.resume
    if not resume:
        raise HTTPException(status_code=404, detail="Associated resume not found")

    # 2. Verify and Deduct 10 Credits
    verify_and_deduct_credit(current_user.id, db, amount=10)

    # 3. Call LLM
    try:
        from ..services.llm_client import tailor_resume_mega_llm
        
        # Convert partial_matches (which is a list of dicts in DB) to what LLM expects
        partial_matches = match.partial_matches or []
        true_gaps = match.true_gaps or []
        
        tailored_latex = tailor_resume_mega_llm(
            resume_text=resume.raw_text,
            jd_text=match.job_description,
            template_type=payload.template_type,
            true_gaps=true_gaps,
            partial_matches=partial_matches
        )
        
        pdf_b64 = None
        import tempfile
        import subprocess
        import os
        import base64
        
        with tempfile.TemporaryDirectory() as tempdir:
            tex_path = os.path.join(tempdir, "resume.tex")
            with open(tex_path, "w", encoding="utf-8") as f:
                f.write(tailored_latex)
            
            try:
                subprocess.run(
                    ["pdflatex", "-interaction=nonstopmode", "resume.tex"],
                    cwd=tempdir,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=15
                )
                pdf_path = os.path.join(tempdir, "resume.pdf")
                if os.path.exists(pdf_path):
                    with open(pdf_path, "rb") as pdf_file:
                        pdf_b64 = base64.b64encode(pdf_file.read()).decode("utf-8")
            except Exception as latex_err:
                log.warning(f"Failed to compile LaTeX: {latex_err}")
        
        return schemas.ResumeTailorResponse(
            tailored_resume_markdown=tailored_latex,
            pdf_base64=pdf_b64
        )
        
    except Exception as e:
        log.error(f"Tailoring failed: {e}", exc_info=True)
        # Refund credits if it fails? (optional, skipping for now to keep simple)
        raise HTTPException(status_code=500, detail=f"Failed to tailor resume: {str(e)}")