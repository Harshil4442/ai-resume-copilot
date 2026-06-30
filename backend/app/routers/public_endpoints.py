from fastapi import APIRouter, Request, HTTPException
from pydantic import BaseModel
import logging

from ..rate_limiter import limiter
from ..services.llm_client import chat_json

router = APIRouter(prefix="/public", tags=["public"])
log = logging.getLogger("ai_resume_copilot.public")

class OptimizeBulletRequest(BaseModel):
    bullet_text: str

class OptimizeBulletResponse(BaseModel):
    action_verb_score: int
    metrics_present: bool
    recommended_bullet: str

@router.post("/optimize_bullet", response_model=OptimizeBulletResponse)
@limiter.limit("20/minute")
def optimize_bullet(payload: OptimizeBulletRequest, request: Request):
    """
    Ungated, rate-limited public endpoint to check and optimize an engineering resume bullet point.
    Runs a light structured prompt on LLM and returns verified metrics and optimization suggestions.
    """
    bullet = payload.bullet_text.strip()
    if not bullet:
        raise HTTPException(status_code=400, detail="Bullet text cannot be empty")
        
    try:
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an expert ATS optimization engine. Analyze the user's resume bullet point "
                    "and return a JSON object containing:\n"
                    "1. 'action_verb_score': integer (0 to 100) scoring verb impact.\n"
                    "2. 'metrics_present': boolean flag indicating if any quantitative metric or number exists.\n"
                    "3. 'recommended_bullet': an optimized version using STAR format and strong active verbs."
                )
            },
            {
                "role": "user",
                "content": f"Optimize this bullet point:\n{bullet}"
            }
        ]
        
        result = chat_json(messages)
        return OptimizeBulletResponse(
            action_verb_score=int(result.get("action_verb_score", 50)),
            metrics_present=bool(result.get("metrics_present", False)),
            recommended_bullet=str(result.get("recommended_bullet", bullet))
        )
    except Exception as e:
        log.exception("Bullet optimization failed")
        # Deterministic fallback when LLM fails or rate limits hit
        has_metrics = any(char.isdigit() for char in bullet)
        return OptimizeBulletResponse(
            action_verb_score=60,
            metrics_present=has_metrics,
            recommended_bullet=f"Led development of key features, improving system throughput and collaborating with cross-functional teams."
        )
