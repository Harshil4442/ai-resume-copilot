"""Session-bound bearer decoder for mapped candidate accounts only.

The numeric legacy subject remains usable for unenrolled accounts. It cannot
access a PENDING registration or a mapped account without a retained session.
"""
from __future__ import annotations

from pydantic import ValidationError

from ..recovery.password_reauth import CandidateWebSession
from ..recovery.store import GuardDenied


def context_from_claims(payload: dict, *, candidate_id: int) -> tuple[CandidateWebSession, int]:
    raw = payload.get("candidate_lifetime")
    if type(raw) is not dict or set(raw) != {"version", "context", "auth_generation"}:
        raise GuardDenied("Candidate session authentication failed")
    if (type(raw["version"]) is not int or raw["version"] != 1
            or type(raw["auth_generation"]) is not int or not 0 < raw["auth_generation"] <= 2**53 - 1):
        raise GuardDenied("Candidate session authentication failed")
    try:
        context = CandidateWebSession.model_validate(raw["context"])
    except (ValidationError, ValueError, TypeError):
        raise GuardDenied("Candidate session authentication failed") from None
    if context.candidate_id != candidate_id:
        raise GuardDenied("Candidate session authentication failed")
    return context, raw["auth_generation"]
