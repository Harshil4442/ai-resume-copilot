import logging

import httpx

from ...schemas import RagAskResponse, RagMessage
from ..llm_client import chat_json
from .chunking import build_match_chunks
from .direct_answers import direct_match_answer, unsupported_direct_answer
from .prompts import build_ask_ai_messages
from .retrieval import rank_chunks

log = logging.getLogger(__name__)


class EnhancedAnswerUnavailable(ValueError):
    """Generation returned no valid deliverable; billing must release the charge."""

    retryable = False

    def __init__(self):
        # A stable message is safe for the failure ledger. Provider text and
        # supplied citations must never be copied to logs or financial records.
        super().__init__("Enhanced Q&A did not produce a valid grounded answer.")


def unavailable_enhanced_answer() -> RagAskResponse:
    """Free guidance returned only after failed generation has left billing."""
    return RagAskResponse(
        answer=(
            "Enhanced generation did not produce a reliable answer. No credits were charged for "
            "this request. You can still ask to see your recorded match score, missing resume "
            "evidence, approved facts or curated interview questions."
        ),
        confidence="low",
        mode="unavailable", provenance="local_guidance",
        suggested_followups=[
            "Which skills lack resume evidence?",
            "What is my recorded match score?",
            "Show my approved evidence.",
        ],
    )


def ask_match_ai(*, resume, match, question: str, recent_messages: list[RagMessage], mode: str = "basic", approved_evidence: list[dict] | None = None) -> RagAskResponse:
    direct = direct_match_answer(resume=resume, match=match, question=question, approved_evidence=approved_evidence)
    if direct:
        return direct
    if mode != "enhanced":
        return unsupported_direct_answer()
    chunks = build_match_chunks(resume, match)
    intent, top_chunks = rank_chunks(question, chunks)
    messages = build_ask_ai_messages(
        question=question,
        intent=intent,
        chunks=top_chunks,
        recent_messages=[
            {"role": msg.role, "content": msg.content}
            for msg in recent_messages
        ],
    )

    try:
        data = chat_json(messages)
        if not isinstance(data, dict):
            raise ValueError("Generated answer must be an object")
        answer = data.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("Generated answer must be non-empty text")
        answer = answer.strip()
        confidence = str(data.get("confidence", "medium")).strip().lower()
        followups = data.get("suggested_followups", [])
        citations = data.get("sources", [])
        allowed_sources = {chunk.id for chunk in top_chunks}
        if not isinstance(citations, list) or any(not isinstance(item, str) or item not in allowed_sources for item in citations):
            raise ValueError("Generated answer references an unsupported source")
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        if not isinstance(followups, list):
            followups = []
        followups = [str(item).strip() for item in followups if str(item).strip()][:4]
        if not citations and confidence != "low":
            raise ValueError("Generated answer must cite retrieved context")
        return RagAskResponse(
            answer=answer,
            confidence=confidence,
            suggested_followups=followups,
            mode="enhanced", provenance="generated",
            sources=list(dict.fromkeys(citations)),
        )
    except (ValueError, KeyError, TypeError) as exc:
        # Returning guidance here would make billable_operation commit a
        # failed paid answer. Let its durable failure path release the credits;
        # the router may then return explicitly free guidance.
        log.warning("Enhanced Q&A output rejected (%s)", type(exc).__name__)
        raise EnhancedAnswerUnavailable() from None
    except httpx.HTTPError as exc:
        # Network / 429 / 5xx errors should be visible to the user instead
        # of silently degraded.
        log.error("RAG provider error: %s", exc)
        raise
