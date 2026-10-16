"""Schema-independent generation quiescence; never a cost-policy substitute.

Set OPTIONAL_AI_GENERATION_ENABLED=false explicitly on a compatibility release.
Old revisions do not read this flag and still require separate retirement evidence.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

PAUSED_MESSAGE = "Optional AI generation is temporarily paused. Deterministic tools remain available."


class GenerationPaused(RuntimeError):
    retryable = False
    budget_exhausted = True


def generation_enabled() -> bool:
    # Preserve the existing release's behavior when unset. Compatibility and
    # cost-policy releases must supply their reviewed setting explicitly.
    return os.getenv("OPTIONAL_AI_GENERATION_ENABLED", "true").strip().lower() == "true"


def require_generation() -> None:
    if not generation_enabled():
        raise GenerationPaused(PAUSED_MESSAGE)


def requires_generation(operation: str, payload: dict[str, Any] | None = None) -> bool:
    data = payload or {}
    if operation in {"resume_tailor", "resume_tailor_legacy", "rewrite_bullets", "interview_questions_legacy"}:
        return True
    if operation in {"job_match", "job_match_legacy"}:
        return data.get("mode", "basic") != "basic"
    if operation == "interview_questions":
        return data.get("mode", "curated") != "curated"
    return operation in {"resume_enrichment", "match_question", "learning_strategy"} and data.get("mode") == "enhanced"


def require_operation_generation(operation: str, payload: dict[str, Any] | None = None) -> None:
    if requires_generation(operation, payload):
        require_generation()


def check_generation_admission(operation: str, payload: dict[str, Any] | None = None) -> None:
    try:
        require_operation_generation(operation, payload)
    except GenerationPaused as exc:
        raise HTTPException(status_code=503, detail=PAUSED_MESSAGE) from exc
