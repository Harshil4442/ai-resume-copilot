"""Narrow, fail-closed authority for a single owner AI smoke after schema25.

This is an additional admission fence, not a replacement for the model-cost
policy, expense holds, product units, or the global generation kill switch.
The production default is closed even when the older global switch is true.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models

PILOT_PAUSED = "Optional AI generation is temporarily paused. Deterministic tools remain available."
_MODEL_MONTHLY_CEILING_MICROS = 3_000_000  # $3; below the pilot's ₹450 provider cap.
_PILOT_OPERATIONS = frozenset({"resume_tailor", "interview_questions"})


class PilotUnavailable(RuntimeError):
    """No new model work may be admitted or sent under this configuration."""


@dataclass(frozen=True)
class OwnerPilot:
    user_id: int
    operation: str
    model: str
    starts_at: datetime
    expires_at: datetime


def _instant(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(UTC)


def current_pilot(*, now: datetime | None = None) -> OwnerPilot | None:
    """Return the active owner scope or refuse; only tests can opt out."""
    mode = os.getenv("HIREWIZ_AI_PILOT_MODE", "closed").strip()
    if (mode == "test_legacy_open" and os.getenv("APP_ENV", "").strip().lower() == "test"
            and os.getenv("PYTEST_CURRENT_TEST")):
        return None
    if mode != "owner_only":
        raise PilotUnavailable(PILOT_PAUSED)
    try:
        owner = os.environ["HIREWIZ_AI_PILOT_OWNER_USER_ID"]
        operation = os.environ["HIREWIZ_AI_PILOT_OPERATION"]
        model = os.environ["HIREWIZ_AI_PILOT_MODEL"]
        starts = _instant(os.environ["HIREWIZ_AI_PILOT_STARTS_AT"])
        expires = _instant(os.environ["HIREWIZ_AI_PILOT_EXPIRES_AT"])
        stamp = now or datetime.now(UTC)
        if (
            not owner.isdecimal() or not 0 < int(owner) < 2**31
            or operation not in _PILOT_OPERATIONS
            or not model.startswith("gemini-") or len(model) > 80
            or not starts <= stamp < expires
            or expires - starts > timedelta(hours=24)
        ):
            raise ValueError("invalid scope")
        # The internal funding ceiling must be materially below the provider
        # cap, leaving headroom for delayed provider cutoff and unrelated calls.
        from ..billing.cost_policy import CostPolicyUnavailable, current_policy

        try:
            policy = current_policy()
        except CostPolicyUnavailable as exc:
            raise ValueError("pilot funding unavailable") from exc
        if (
            policy.monthly_model_budget_micros > _MODEL_MONTHLY_CEILING_MICROS
            or policy.daily_model_budget_micros > _MODEL_MONTHLY_CEILING_MICROS
            or policy.monthly_promotional_model_budget_micros
               + policy.monthly_legacy_premium_model_budget_micros > _MODEL_MONTHLY_CEILING_MICROS
            or policy.adverse_fx_minor_per_usd < 10_000
        ):
            raise ValueError("pilot funding ceiling exceeded")
        return OwnerPilot(int(owner), operation, model, starts, expires)
    except (KeyError, ValueError, TypeError):
        raise PilotUnavailable(PILOT_PAUSED) from None


def require_owner(user_id: int | None, operation: str) -> OwnerPilot | None:
    pilot = current_pilot()
    if pilot is not None and (user_id != pilot.user_id or operation != pilot.operation):
        raise PilotUnavailable(PILOT_PAUSED)
    return pilot


def _pilot_run_count(db: Session, owner_id: int, starts_at: datetime) -> int:
    # The caller holds the existing owner admission lock. The state changes
    # when the quote freezes and remains retained after failed delivery. JSON
    # `None` is not a portable SQL NULL, so do not filter the quote column.
    return int(db.query(func.count(models.AnalysisRun.id)).filter(
        models.AnalysisRun.user_id == owner_id,
        models.AnalysisRun.created_at >= starts_at.replace(tzinfo=None),
        models.AnalysisRun.model_cost_state != "unquoted",
    ).scalar() or 0)


def require_new_operation(db: Session, user_id: int, operation: str) -> OwnerPilot | None:
    pilot = require_owner(user_id, operation)
    if pilot is not None and _pilot_run_count(db, user_id, pilot.starts_at) != 0:
        raise PilotUnavailable(PILOT_PAUSED)
    return pilot


def require_provider_attempt(db: Session, run: models.AnalysisRun, provider: str,
                             model: str, api_base: str) -> None:
    pilot = require_owner(run.user_id, run.operation)
    if pilot is None:
        return
    created = run.created_at
    if created is not None:
        created = created.replace(tzinfo=UTC) if created.tzinfo is None else created.astimezone(UTC)
    if (
        created is None
        or created < pilot.starts_at
        or run.status not in {"queued", "running"}
        or run.cancel_requested
        or db.get(models.User, pilot.user_id) is None
        or run.generation_attempt_limit != 1
        or run.generation_attempt_count != 0
        or run.model_cost_quote is None
        or provider != "google"
        or model != pilot.model
        or api_base.rstrip("/") != "https://generativelanguage.googleapis.com"
        or _pilot_run_count(db, run.user_id, pilot.starts_at) != 1
    ):
        raise PilotUnavailable(PILOT_PAUSED)


def require_budget_context(budget, provider: str, model: str, api_base: str) -> None:
    """Stop alternate budget adapters before even a Gemini countTokens call."""
    pilot = current_pilot()
    if pilot is None:
        return
    if budget.limit != 1 or not isinstance(budget.run_id, str) or not budget.run_id:
        raise PilotUnavailable(PILOT_PAUSED)
    from ..database import SessionLocal

    with SessionLocal() as db:
        run = db.get(models.AnalysisRun, budget.run_id)
        if run is None or run.user_id != pilot.user_id:
            raise PilotUnavailable(PILOT_PAUSED)
        require_provider_attempt(db, run, provider, model, api_base)
