"""Explainable catalog overlap; no generative or embedding dependency."""
from __future__ import annotations

from typing import Any

from .market.skill_extractor import extract_skills_from_text
from .market.skill_taxonomy import canonical_skill

SCORING_VERSION = "basic-catalog-overlap-v1"


def basic_match(
    resume_skills: list[str],
    job_description: str,
    title: str = "",
    preferences: dict | None = None,
) -> dict[str, Any]:
    """Compare documented skill mentions, never claim hiring probability.

    Preferences belong to retrieval/eligibility where structured job fields
    are available. Narrative overlap cannot establish location, sponsorship,
    seniority or legal eligibility, so this helper does not invent those.
    """
    required, warnings = extract_skills_from_text(job_description)
    declared = {canonical_skill(str(skill)).casefold() for skill in resume_skills}
    matched = sorted((skill for skill in required if skill.casefold() in declared), key=str.casefold)
    missing = sorted((skill for skill in required if skill.casefold() not in declared), key=str.casefold)
    score = round(100 * len(matched) / len(required), 1) if required else 0.0
    reasons = [f"{len(matched)} of {len(required)} catalog skills mentioned in the role are documented in the resume."]
    if matched:
        reasons.append("Documented overlap: " + ", ".join(matched) + ".")
    if missing:
        reasons.append("Missing resume evidence: " + ", ".join(missing) + ".")
    uncertainties = [
        "This is skill mention overlap, not a prediction of selection or verified proficiency.",
        "Unlisted or unfamiliar terminology and transferable experience require review.",
    ]
    if not required:
        uncertainties.append("No catalog skills were identified; the zero score means insufficient catalog evidence, not ineligibility.")
    return {
        "score": score,
        "matched_skills": matched,
        "missing_evidence": missing,
        "required_skills": sorted(required, key=str.casefold),
        "reasons": reasons,
        "uncertainties": [*uncertainties, *warnings],
        "scoring_version": SCORING_VERSION,
        "mode": "basic",
    }
