"""Public, explainable bullet diagnostics without generation or invented facts."""
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..rate_limiter import limiter

router = APIRouter(prefix="/public", tags=["public"])
_ACTION_VERBS = {
    "built", "developed", "implemented", "designed", "improved", "reduced", "increased",
    "delivered", "automated", "optimized", "resolved", "tested", "created", "launched",
    "managed", "coordinated", "analyzed", "supported", "migrated", "maintained", "led",
}
_METRIC = re.compile(
    r"(?:\b\d+(?:\.\d+)?\s*(?:%|percent\b|times\b|[x×]\b|hours?\b|minutes?\b|"
    r"seconds?\b|milliseconds?\b|users?\b|customers?\b|requests?\b|engineers?\b|"
    r"teams?\b|projects?\b)|[$₹€£]\s*\d+(?:[,.]\d+)*)",
    re.IGNORECASE,
)


class OptimizeBulletRequest(BaseModel):
    bullet_text: str = Field(max_length=5000)


class OptimizeBulletResponse(BaseModel):
    action_verb_score: int
    metrics_present: bool
    recommended_bullet: str
    feedback: list[str] = Field(default_factory=list)
    provenance: str = "local_rules"


@router.post("/optimize_bullet", response_model=OptimizeBulletResponse)
@limiter.limit("20/minute")
def optimize_bullet(payload: OptimizeBulletRequest, request: Request):
    """Check wording and quantity mentions; preserve every candidate claim."""
    bullet = payload.bullet_text.strip()
    if not bullet:
        raise HTTPException(status_code=400, detail="Bullet text cannot be empty")
    words = re.findall(r"\b[A-Za-z]+\b", bullet)
    starts_with_action = bool(words and words[0].casefold() in _ACTION_VERBS)
    has_metric = bool(_METRIC.search(bullet))
    feedback = []
    if not starts_with_action:
        feedback.append("Start with an accurate action verb describing what you personally did.")
    if not has_metric:
        feedback.append("If you can verify a useful quantity or outcome, add it; do not invent a number.")
    else:
        feedback.append("A quantity is mentioned. Confirm it against your own records before submitting.")
    if len(words) > 45:
        feedback.append("Consider shortening this bullet while keeping its meaning and evidence.")
    return OptimizeBulletResponse(
        action_verb_score=85 if starts_with_action else 35,
        metrics_present=has_metric,
        recommended_bullet=bullet,
        feedback=feedback,
    )
