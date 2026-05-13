from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..services.matching import extract_required_skills_from_jd, compute_match_score
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
        .filter(models.Resume.id == payload.resume_id, models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found for this user")

    jd_text = (
        payload.job_description
        if isinstance(payload.job_description, str)
        else str(payload.job_description)
    )

    # Step 1: Extract JD skills — LLM preferred, regex heuristic fallback
    required_skills = []
    try:
        from ..services.llm_client import extract_jd_skills_llm
        required_skills = extract_jd_skills_llm(jd_text)
    except Exception:
        required_skills = extract_required_skills_from_jd(jd_text)

    # Step 2: Compute score, missing skills, weak skills
    score, req, missing, weak = compute_match_score(
        resume.skills or [],
        jd_text,
        required_skills=required_skills,
    )

    # Step 3: Generate LLM fit summary — template fallback if LLM unavailable
    fit_summary = ""
    try:
        from ..services.llm_client import generate_fit_summary_llm
        fit_summary = generate_fit_summary_llm(
            resume_skills=resume.skills or [],
            job_title=payload.job_title,
            jd_text=jd_text,
            match_score=score,
            missing_skills=missing,
            weak_skills=weak,
        )
    except Exception:
        if score >= 70:
            fit_summary = f"Strong match ({score:.0f}/100) for the {payload.job_title} role."
        elif score >= 40:
            fit_summary = (
                f"Moderate match ({score:.0f}/100). "
                f"Consider upskilling in: {', '.join(missing[:3])}."
            )
        else:
            fit_summary = (
                f"Partial match ({score:.0f}/100) for {payload.job_title}. "
                f"Key gaps: {', '.join(missing[:5])}."
            )

    # Step 4: Persist to DB
    match = models.JobMatch(
        user_id=current_user.id,
        resume_id=resume.id,
        job_title=payload.job_title,
        company=payload.company or "",
        job_description=jd_text,
        match_score=float(score),
        required_skills=req,
        missing_skills=missing,
        weak_skills=weak,
        fit_summary=fit_summary,
    )
    db.add(match)
    db.commit()
    db.refresh(match)

    return schemas.JobMatchResponse(
        match_id=match.id,
        match_score=score,
        required_skills=req,
        missing_skills=missing,
        weak_skills=weak,
        fit_summary=fit_summary,
    )


@router.get("/matches", response_model=schemas.JobMatchHistoryResponse)
def get_match_history(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Return the last 50 job matches for the current user."""
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
                match_id=m.id,
                job_title=m.job_title,
                company=m.company,
                match_score=m.match_score,
                created_at=m.created_at,
            )
            for m in matches
        ]
    )