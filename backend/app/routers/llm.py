from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user
from ..services.guardrails import billable_operation
from ..services.llm_client import generate_interview_questions, rewrite_bullets

router = APIRouter(prefix="/llm", tags=["llm"])

@router.post("/rewrite_bullets", response_model=schemas.RewriteBulletsResponse)
async def rewrite_bullets_endpoint(
    payload: schemas.RewriteBulletsRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    resume = (
        db.query(models.Resume)
        .filter(models.Resume.id == payload.resume_id, models.Resume.user_id == current_user.id)
        .first()
    )
    if not resume:
        raise HTTPException(status_code=404, detail="Resume not found for this user.")

    from ..domains.credits import runtime, synchronous

    if runtime.requires_unified_billing(db, current_user.id) or payload.accepted_service_quote_id:
        return synchronous.execute(db, user_id=current_user.id,
            payload=synchronous.request("bullet_rewrite", payload),
            accepted_service_quote_id=payload.accepted_service_quote_id,
            idempotency_key=payload.idempotency_key)

    request = synchronous.request("bullet_rewrite", payload)
    prepared = synchronous.owned_context(db, user_id=current_user.id, payload=request)
    evidence = prepared.context["approved_evidence"]
    with billable_operation(
        user_id=current_user.id,
        db=db,
        operation="rewrite_bullets",
        amount=1,
        input_payload={"resume_id": resume.id, "tone": payload.tone, "context_fingerprint": prepared.fingerprint},
    ) as run, synchronous.guard_prepared_inputs(db, user_id=current_user.id, payload=request,
            prepared=prepared, run_id=run.id):
        data = rewrite_bullets(prepared.context["resume"]["raw_text"], payload.job_description, payload.tone,
                               approved_evidence=evidence)
        from ..services.result_commit import begin_result_commit

        begin_result_commit(db, current_user.id, run.id)
        synchronous.require_current_inputs(db, user_id=current_user.id, payload=request, prepared=prepared)
    return schemas.RewriteBulletsResponse(
        rewritten_bullets=data.get("bullets", []),
        summary=data.get("summary", ""),
        evidence_links=data.get("evidence_links", []),
    )

@router.post("/interview_questions", response_model=schemas.InterviewQuestionsResponse)
async def interview_questions_endpoint(
    payload: schemas.InterviewQuestionsRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    if len(payload.job_description.strip()) < 20:
        raise HTTPException(status_code=422, detail="Job description is too short")
    with billable_operation(
        user_id=current_user.id,
        db=db,
        operation="interview_questions_legacy",
        amount=1,
        input_payload={"job_title": payload.job_title},
    ):
        questions = generate_interview_questions(payload.job_title, payload.job_description)
    return schemas.InterviewQuestionsResponse(questions=questions)
