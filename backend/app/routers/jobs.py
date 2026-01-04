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

    # Normalize JD to string for saving (and robustness)
    jd = payload.job_description
    if isinstance(jd, list):
        jd_text = "\n".join([str(x) for x in jd])
    else:
        jd_text = str(jd or "")

    required_skills = extract_required_skills_from_jd(jd_text)

    # compute_match_score returns: (score, required_skills, missing_skills)
    score, req, missing = compute_match_score(
        resume.skills or [],
        jd_text,
        required_skills=required_skills,
    )

    match = models.JobMatch(
        user_id=current_user.id,
        resume_id=resume.id,
        job_title=getattr(payload, "job_title", "") or "",
        company=getattr(payload, "company", "") or "",
        job_description=jd_text,
        match_score=float(score or 0.0),
        extracted_skills=required_skills,
        missing_skills=missing,
    )
    db.add(match)
    db.commit()
    db.refresh(match)

    return {
        "match_score": float(score or 0.0),
        "required_skills": req,
        "missing_skills": missing,
    }