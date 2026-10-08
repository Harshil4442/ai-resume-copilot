"""Explicit, fail-closed employer service capability and admission budgets."""
from __future__ import annotations

import os

PRICING_VERSION = "employer-services-intro-v1"


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


def prices() -> dict[str, int | str]:
    return {
        "search_credits_per_job": positive_int("EMPLOYER_SEARCH_CREDITS_PER_JOB", 1, 1000),
        "apply_credits_per_job": positive_int("EMPLOYER_APPLY_CREDITS_PER_JOB", 5, 1000),
        "max_search_jobs": positive_int("EMPLOYER_MAX_SEARCH_JOBS", 100, 100),
        "pricing_version": PRICING_VERSION,
    }


def discovery_enabled() -> bool:
    return enabled("EMPLOYER_DISCOVERY_ENABLED")


def submit_enabled() -> bool:
    return enabled("EMPLOYER_AUTO_SUBMIT_ENABLED")
