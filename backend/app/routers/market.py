from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..security import get_current_user
from ..services.market.analyzer import analyze_market

router = APIRouter(prefix="/market", tags=["market"])


@router.post("/analyze", response_model=schemas.MarketAnalyzeResponse)
def analyze_market_trends(
    payload: schemas.MarketAnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    from ..services.guardrails import verify_and_deduct_credit
    verify_and_deduct_credit(current_user.id, db, amount=5)

    resume = None
    if payload.resume_id:
        resume = (
            db.query(models.Resume)
            .filter(models.Resume.id == payload.resume_id, models.Resume.user_id == current_user.id)
            .first()
        )
        if not resume:
            raise HTTPException(status_code=404, detail="Resume not found for this user")

    return analyze_market(
        target_role=payload.target_role.strip(),
        location=(payload.location or "").strip(),
        country_code=(payload.country_code or "").strip().upper(),
        experience_level=(payload.experience_level or "").strip(),
        remote=payload.remote,
        max_results=payload.max_results,
        posted_within_days=payload.posted_within_days,
        resume=resume,
    )
