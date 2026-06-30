import json
import logging
from typing import List

import requests

from ...schemas import RagAskResponse, RagMessage
from ..llm_client import chat_json
from .chunking import EvidenceChunk, build_match_chunks
from .prompts import build_ask_ai_messages
from .retrieval import rank_chunks

log = logging.getLogger(__name__)


def _fallback_response(question: str, chunks: List[EvidenceChunk]) -> RagAskResponse:
    if not chunks:
        return RagAskResponse(
            answer="I could not find enough match context to answer that reliably.",
            confidence="low",
            suggested_followups=[
                "Which skills am I missing?",
                "Why is my match score low?",
                "What should I improve first?",
            ],
        )
    titles = ", ".join(chunk.title for chunk in chunks[:3])
    return RagAskResponse(
        answer=(
            "I could not get a structured LLM answer, but the most relevant context for your question "
            f"appears to be: {titles}. Try asking a more specific question about missing skills, score, "
            "resume evidence, or interview preparation."
        ),
        confidence="low",
        suggested_followups=[
            "Which skills are true gaps?",
            "Which resume evidence supports this job?",
            "What should I improve first?",
        ],
    )


def ask_match_ai(*, resume, match, question: str, recent_messages: List[RagMessage]) -> RagAskResponse:
    chunks = build_match_chunks(resume, match)
    intent, top_chunks = rank_chunks(question, chunks)
    messages = build_ask_ai_messages(
        question=question,
        intent=intent,
        chunks=top_chunks,
        recent_messages=[
            {"role": msg.role, "content": msg.content[:1200]}
            for msg in recent_messages[-6:]
        ],
    )

    try:
        data = chat_json(messages)
        answer = str(data.get("answer", "")).strip()
        confidence = str(data.get("confidence", "medium")).strip().lower()
        followups = data.get("suggested_followups", [])
        if confidence not in {"high", "medium", "low"}:
            confidence = "medium"
        if not isinstance(followups, list):
            followups = []
        followups = [str(item).strip() for item in followups if str(item).strip()][:4]
        if not answer:
            raise ValueError("LLM returned an empty answer")
        return RagAskResponse(
            answer=answer,
            confidence=confidence,
            suggested_followups=followups,
        )
    except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
        # JSON-shape problems → use the deterministic fallback so the UX
        # stays useful. Transport/provider errors (below) are surfaced.
        log.warning("RAG fallback (parse error): %s", exc)
        return _fallback_response(question, top_chunks)
    except requests.exceptions.RequestException as exc:
        # Network / 429 / 5xx errors should be visible to the user instead
        # of silently degraded.
        log.error("RAG provider error: %s", exc)
        raise
