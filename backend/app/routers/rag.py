from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user
from ..services.guardrails import billable_operation
from ..services.rag.chat import (
    EnhancedAnswerUnavailable,
    ask_match_ai,
    unavailable_enhanced_answer,
)

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

    evidence = (
        db.query(models.EvidenceItem)
        .filter(models.EvidenceItem.user_id == current_user.id,
                models.EvidenceItem.resume_id == resume.id,
                models.EvidenceItem.approval_state == "approved")
        .order_by(models.EvidenceItem.created_at).all()
    )
    answer_input = {
        "resume": resume,
        "match": match,
        "question": payload.question.strip(),
        "recent_messages": payload.recent_messages,
        "approved_evidence": [
            {"id": item.id, "title": item.title, "text": item.evidence_text} for item in evidence
        ],
    }
    # Ownership checks above apply equally to free and generated answers. A
    # stored-data response needs neither a credit reservation nor an AI policy,
    # even when the candidate initially selected enhanced mode.
    direct = ask_match_ai(**answer_input, mode="basic")
    if payload.mode != "enhanced" or direct.mode == "direct":
        return direct

    from ..domains.credits import runtime, synchronous
    from ..services.synchronous_contracts import SynchronousOutputUnavailable

    if runtime.requires_unified_billing(db, current_user.id) or payload.accepted_service_quote_id:
        try:
            return synchronous.execute(db, user_id=current_user.id,
                payload=synchronous.request("enhanced_qa", payload),
                accepted_service_quote_id=payload.accepted_service_quote_id,
                idempotency_key=payload.idempotency_key)
        except SynchronousOutputUnavailable:
            return unavailable_enhanced_answer()

    try:
        with billable_operation(
            user_id=current_user.id,
            db=db,
            operation="match_question",
            amount=1,
            input_payload={"job_match_id": match.id, "resume_id": resume.id, "mode": payload.mode},
        ):
            return ask_match_ai(**answer_input, mode=payload.mode)
    except EnhancedAnswerUnavailable:
        # The context has rolled back the deliverable and restored the product
        # charge. Real provider expense remains in its independent cost ledger.
        # This response contains no cached candidate data or generated claims.
        return unavailable_enhanced_answer()
