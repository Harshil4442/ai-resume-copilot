from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..database import get_db
from .. import models, schemas
from ..security import get_current_user
from ..services.llm_client import rewrite_bullets, generate_interview_questions

router = APIRouter(prefix="/llm", tags=["llm"])

@router.post("/rewrite_bullets", response_model=schemas.RewriteBulletsResponse)
async def rewrite_bullets_endpoint(
    payload: schemas.RewriteBulletsRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.guardrails import verify_and_deduct_credit
    verify_and_deduct_credit(current_user.id, db)

    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == payload.resume_id, models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found for this user.")

    data = rewrite_bullets(resume.raw_text, payload.job_description, payload.tone)
    return schemas.RewriteBulletsResponse(
        rewritten_bullets=data.get("bullets", []),
        summary=data.get("summary", ""),
    )

@router.post("/interview_questions", response_model=schemas.InterviewQuestionsResponse)
async def interview_questions_endpoint(
    payload: schemas.InterviewQuestionsRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.guardrails import verify_and_deduct_credit
    verify_and_deduct_credit(current_user.id, db)

    questions = generate_interview_questions(payload.job_title, payload.job_description)
    return schemas.InterviewQuestionsResponse(questions=questions)
