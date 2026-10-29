"""Deterministic prompt-size admission before a paid analysis reservation.

These are UTF-8/framing estimates, not provider-certified token counts. The
model ledger still enforces the same estimate before every actual dispatch.
"""

from __future__ import annotations

from .interview_contract import (
    MAX_CONTEXT_ESTIMATE,
    InterviewContextUnavailable,
    build_interview_messages,
    require_complete_context,
)
from .llm_client import build_tailoring_messages
from .model_cost_policy import input_token_estimate
from .prompt_privacy import redact_messages

# The v8 interview validator emits finite local error strings and positions;
# its sole repair adds one user message. This exceeds the longest current
# message plus framing. Keep the version fixed until a reviewed prompt change.
INTERVIEW_REPAIR_DELTA = 4_096

# Tailoring can make at most two further model calls. The builder accepts a
# 3,000-character repair note. A large UTF-8 note is counted here, and runtime
# refuses any redacted repair larger than this admission envelope.
TAILORING_REPAIR_DELTA = 16_384


def _estimate(messages: list[dict]) -> int:
    return input_token_estimate(redact_messages(messages))


def interview_prompt_bound(job_title: str, jd_text: str, evidence: list[dict]) -> int:
    initial = build_interview_messages(job_title, jd_text, evidence)
    # Count the complete v8 repair shape with a deliberately longer error than
    # any of its current finite validator messages and the longest location.
    repair = {
        "role": "user",
        "content": (
            f"The response could not be used: {'X' * 1_024} Question 8, preparation step "
            "4, needs replacement. Return a replacement JSON list with exactly 8 distinct "
            "questions and all required fields. Keep using the original job description and "
            "approved evidence. Each item has only question, preparation_steps (two to four "
            "neutral, role-specific actions) and evidence_ids. Do not assert personal history "
            "or results. Use empty evidence_ids when no approved fact supports a question."
        ),
    }
    require_complete_context([*initial, repair])
    if max(
        input_token_estimate(initial) + INTERVIEW_REPAIR_DELTA,
        input_token_estimate([*initial, repair]),
    ) > MAX_CONTEXT_ESTIMATE:
        raise InterviewContextUnavailable(
            "The full interview repair exceeds the supported context limit"
        )
    bound = max(_estimate(initial) + INTERVIEW_REPAIR_DELTA, _estimate([*initial, repair]))
    return bound


def tailoring_prompt_bound(
    *, job_title: str, jd_text: str, approved_evidence: list[dict], source_units: list[dict],
    complete_context: bool | None = None,
) -> int:
    initial = _estimate(build_tailoring_messages(
        job_title=job_title, jd_text=jd_text,
        approved_evidence=approved_evidence, source_units=source_units,
        complete_context=complete_context,
    ))
    # Build the complete repair form, including every source unit and fixed
    # repair instruction. Four-byte code points cover the maximum UTF-8 size
    # of the existing 3,000-character repair-note slice.
    repaired = _estimate(build_tailoring_messages(
        job_title=job_title, jd_text=jd_text,
        approved_evidence=approved_evidence, source_units=source_units,
        repair_note="\U0010ffff" * 3_000,
        complete_context=complete_context,
    ))
    return max(repaired, initial + TAILORING_REPAIR_DELTA)


def require_tailoring_repair_bound(
    *, job_title: str, jd_text: str, approved_evidence: list[dict], source_units: list[dict],
    repair_note: str, messages: list[dict],
) -> None:
    """A changed repair note must not exceed the amount checked at quote time."""
    if not repair_note:
        return
    if _estimate(messages) > tailoring_prompt_bound(
        job_title=job_title,
        jd_text=jd_text,
        approved_evidence=approved_evidence,
        source_units=source_units,
    ):
        raise ValueError("Repair feedback exceeds the quoted prompt envelope")
