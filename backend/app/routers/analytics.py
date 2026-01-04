from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models
from ..security import get_current_user

router = APIRouter(prefix="/analytics", tags=["analytics"])

@router.get("/summary")
def analytics_summary(
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resumes_q = db.query(models.Resume).filter(models.Resume.user_id == current_user.id)
    matches_q = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.user_id == current_user.id)
        .order_by(models.JobMatch.created_at.asc())
    )

    resumes = resumes_q.all()
    job_matches = matches_q.all()

    resume_count = len(resumes)

    total_skills = len(set(s for r in resumes for s in (r.skills or [])))

    avg_match = (
        sum(float(jm.match_score or 0.0) for jm in job_matches) / len(job_matches)
        if job_matches else 0.0
    )

    history = [
        {
            "timestamp": jm.created_at.isoformat(),
            "match_score": float(jm.match_score or 0.0),
        }
        for jm in job_matches
    ]

    return {
        "profile_completeness": min(100, total_skills * 3),
        "average_match_score": float(avg_match),
        "applications_count": len(job_matches),
        "resume_count": resume_count,          
        "match_history": history,              
    }