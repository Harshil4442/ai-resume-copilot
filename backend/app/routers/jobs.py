from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..services.matching import extract_required_skills_from_jd, compute_match_score
from ..security import get_current_user

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/match", response_model=dict)
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

    # Extract required skills from the JD (robust even if JD text is messy)
    required_skills = extract_required_skills_from_jd(payload.job_description)

    # compute_match_score returns (score, required_skills, missing_skills)
    score, req, missing = compute_match_score(
        resume.skills or [],
        payload.job_description,
        required_skills=required_skills,
    )

    # Store JD as a string (schema says str, but keeping this safe)
    jd_text = payload.job_description if isinstance(payload.job_description, str) else str(payload.job_description)

    match = models.JobMatch(
        user_id=current_user.id,
        resume_id=resume.id,
        job_title=payload.job_title,
        company=payload.company or "",
        job_description=jd_text,
        match_score=float(score),
        required_skills=req,          # ✅ correct column name
        missing_skills=missing,       # ✅ correct column name
    )

    db.add(match)
    db.commit()
    db.refresh(match)

    return {
        "match_score": float(score),
        "required_skills": req,
        "missing_skills": missing,
    }