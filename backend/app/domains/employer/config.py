"""Explicit, fail-closed employer service capability and admission budgets."""
from __future__ import annotations

import os

PRICING_VERSION = "employer-services-intro-v1"
ADMISSION_VERSION = "employer-admissions-v1"


def enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"true", "1", "yes", "on"}


def positive_int(name: str, default: int, maximum: int = 10_000) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer") from exc
    if not 1 <= value <= maximum:
        raise RuntimeError(f"{name} must be between 1 and {maximum}")
    return value


def prices() -> dict:
    from ...billing.cost_policy import service_prices
    reviewed = service_prices()
    # A deployment may tighten prices, never silently undercut the audit.
    for key, env in (("search_credits_per_job", "EMPLOYER_SEARCH_CREDITS_PER_JOB"),
                     ("apply_credits_per_job", "EMPLOYER_APPLY_CREDITS_PER_JOB")):
        value = positive_int(env, reviewed[key], 1000)
        if value < reviewed[key]:
            from ...billing.cost_policy import CostPolicyUnavailable
            raise CostPolicyUnavailable("Service prices require a renewed expense review.")
        reviewed[key] = value
    return reviewed | {"max_search_jobs": positive_int("EMPLOYER_MAX_SEARCH_JOBS", 100, 100)}


def discovery_enabled() -> bool:
    return enabled("EMPLOYER_DISCOVERY_ENABLED")


def submit_enabled() -> bool:
    return enabled("EMPLOYER_AUTO_SUBMIT_ENABLED")


def admission_limits() -> dict:
    """Strictly positive fleet configuration, frozen in each reviewed quote."""
    return {
        "version": ADMISSION_VERSION,
        "daily_limit": positive_int("EMPLOYER_CANDIDATE_DAILY_LIMIT", 10, 1000),
        "rolling_limit": positive_int("EMPLOYER_CANDIDATE_ROLLING_LIMIT", 50, 10_000),
        "rolling_days": positive_int("EMPLOYER_CANDIDATE_ROLLING_DAYS", 30, 365),
        "pending_limit": positive_int("EMPLOYER_CANDIDATE_PENDING_LIMIT", 10, 1000),
        "daily_credit_limit": positive_int("EMPLOYER_CANDIDATE_DAILY_CREDIT_LIMIT", 100, 100_000),
        "rolling_credit_limit": positive_int("EMPLOYER_CANDIDATE_ROLLING_CREDIT_LIMIT", 500, 1_000_000),
        "batch_limit": positive_int("EMPLOYER_BATCH_LIMIT", 10, 100),
        "batch_credit_limit": positive_int("EMPLOYER_BATCH_CREDIT_LIMIT", 100, 100_000),
        "quote_hours": positive_int("EMPLOYER_ADMISSION_QUOTE_HOURS", 24, 168),
        "day_boundary": "UTC",
        "counting_policy": "pending_and_unknown_plus_possible_sends_in_window",
    }


def employer_limits(source) -> dict:
    from .schemas import EmployerAdmissionPolicy
    supplied = getattr(source, "admission_policy", None)
    policy = EmployerAdmissionPolicy.model_validate(supplied).model_dump() if supplied else None
    # Verified public Figma policy observed 8 October 2026. This is a safety cap,
    # never a submission grant or a claim of hiring-rejection reconciliation.
    if source.platform == "greenhouse" and source.board_token == "figma":
        effective = {
            "version": "figma-public-2026-10-08", "daily_limit": 5,
            "rolling_limit": 5, "rolling_days": 30,
            "evidence_url": "https://job-boards.greenhouse.io/figma/jobs/5707966004",
            "evidence_note": "The employer states a maximum of five applications in a rolling 30-day period. Hiring rejection/reapplication history needs independent verification before write access.",
        }
        if policy:
            # Operator evidence may tighten a known employer restriction, but
            # cannot silently replace that restriction with a looser cap.
            effective["daily_limit"] = min(5, policy["daily_limit"])
            effective["rolling_limit"] = min(5, policy["rolling_limit"])
            effective["rolling_days"] = max(30, policy["rolling_days"])
            effective["operator_policy"] = policy
        return effective
    if policy:
        return policy
    return {
        "version": ADMISSION_VERSION,
        "daily_limit": positive_int("EMPLOYER_PER_EMPLOYER_DAILY_LIMIT", 5, 1000),
        "rolling_limit": positive_int("EMPLOYER_PER_EMPLOYER_ROLLING_LIMIT", 10, 1000),
        "rolling_days": positive_int("EMPLOYER_PER_EMPLOYER_ROLLING_DAYS", 30, 365),
        "evidence_url": None, "evidence_note": "Conservative service safety limits; no employer submission permission is implied.",
    }
