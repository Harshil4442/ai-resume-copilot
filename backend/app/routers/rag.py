from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user
from ..services.rag.chat import ask_match_ai

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/ask", response_model=schemas.RagAskResponse)
def ask_ai_about_match(
    payload: schemas.RagAskRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    match = (
        db.query(models.JobMatch)
        .filter(models.JobMatch.id == payload.job_match_id, models.JobMatch.user_id == current_user.id)
        .first()
    )
    if not match:
        raise HTTPException(status_code=404, detail="Job match not found for this user")

    resume_id = payload.resume_id or match.resume_id
    if payload.resume_id and match.resume_id and payload.resume_id != match.resume_id:
        raise HTTPException(status_code=400, detail="Resume does not belong to this job match")
    if not resume_id:
        raise HTTPException(status_code=400, detail="This job match does not have an associated resume")

    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == resume_id, models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found for this user")

    return ask_match_ai(
        resume=resume,
        match=match,
        question=payload.question.strip(),
        recent_messages=payload.recent_messages,
    )
