import json
from typing import List

from .chunking import EvidenceChunk


def build_ask_ai_messages(
    *,
    question: str,
    intent: str,
    chunks: List[EvidenceChunk],
    recent_messages: List[dict],
) -> List[dict]:
    context = [
        {
            "source": chunk.source,
            "title": chunk.title,
            "text": chunk.text,
        }
        for chunk in chunks
    ]
    recent = recent_messages[-6:]

    system = (
        "You are an AI career copilot answering questions about one resume-to-job match analysis.\n"
        "Use ONLY the provided context. Do not invent skills, metrics, companies, education, projects, or experience.\n"
        "If the context is insufficient, say what is missing and lower confidence.\n"
        "Separate evidence-based facts from suggestions in the answer when helpful.\n"
        "Keep the answer concise, practical, and specific to this match.\n"
        "Return ONLY a valid JSON object with keys: answer, confidence, suggested_followups.\n"
        "confidence must be one of: high, medium, low.\n"
        "suggested_followups must be an array of 2-4 short questions."
    )

    user = (
        f"QUESTION INTENT: {intent}\n\n"
        f"RECENT CHAT MESSAGES:\n{json.dumps(recent, ensure_ascii=False)}\n\n"
        f"RETRIEVED CONTEXT:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"USER QUESTION:\n{question}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

