"""Owner-only AI smoke authorization, with no external provider requests."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.database import Base
from backend.app.services import ai_pilot, llm_client
from backend.app.services.generation_budget import GenerationBudget, GenerationBudgetExhausted
from backend.app.services.generation_gate import (
    GenerationPaused,
    require_operation_generation,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _scope(monkeypatch, *, owner: str = "1", operation: str = "resume_tailor"):
    stamp = datetime.now(UTC)
    monkeypatch.setenv("HIREWIZ_AI_PILOT_MODE", "owner_only")
    monkeypatch.setenv("HIREWIZ_AI_PILOT_OWNER_USER_ID", owner)
    monkeypatch.setenv("HIREWIZ_AI_PILOT_OPERATION", operation)
    monkeypatch.setenv("HIREWIZ_AI_PILOT_MODEL", "gemini-3-flash")
    monkeypatch.setenv("HIREWIZ_AI_PILOT_STARTS_AT", (stamp - timedelta(minutes=5)).isoformat())
    monkeypatch.setenv("HIREWIZ_AI_PILOT_EXPIRES_AT", (stamp + timedelta(minutes=30)).isoformat())
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    return stamp


@pytest.fixture
def funded_scope(monkeypatch):
    _scope(monkeypatch)
    from backend.app.billing import cost_policy

    monkeypatch.setattr(cost_policy, "current_policy", lambda: SimpleNamespace(
        monthly_model_budget_micros=3_000_000,
        daily_model_budget_micros=1_000_000,
        monthly_promotional_model_budget_micros=2_000_000,
        monthly_legacy_premium_model_budget_micros=1_000_000,
        adverse_fx_minor_per_usd=10_000,
    ))


@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    try:
        with factory() as db:
            db.add(models.User(id=1, email="owner@example.invalid", ai_credits=10, tier="free"))
            db.add(models.User(id=2, email="other@example.invalid", ai_credits=10, tier="free"))
            db.commit()
        yield factory
    finally:
        engine.dispose()


def test_production_default_and_test_bypass_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.delenv("HIREWIZ_AI_PILOT_MODE", raising=False)
    with pytest.raises(GenerationPaused):
        require_operation_generation("resume_tailor", user_id=1)
    monkeypatch.setenv("HIREWIZ_AI_PILOT_MODE", "test_legacy_open")
    with pytest.raises(GenerationPaused):
        require_operation_generation("resume_tailor", user_id=1)


def test_owner_scope_rejects_other_users_routes_expiry_and_oversized_budget(funded_scope, monkeypatch):
    require_operation_generation("resume_tailor", user_id=1)
    for user_id, operation in [(2, "resume_tailor"), (1, "interview_questions")]:
        with pytest.raises(GenerationPaused):
            require_operation_generation(operation, {"mode": "enhanced"}, user_id=user_id)
    monkeypatch.setenv("HIREWIZ_AI_PILOT_EXPIRES_AT", (datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    with pytest.raises(GenerationPaused):
        require_operation_generation("resume_tailor", user_id=1)
    _scope(monkeypatch)
    from backend.app.billing import cost_policy

    monkeypatch.setattr(cost_policy, "current_policy", lambda: SimpleNamespace(
        monthly_model_budget_micros=70_000_000,
        daily_model_budget_micros=1_000_000,
        monthly_promotional_model_budget_micros=2_000_000,
        monthly_legacy_premium_model_budget_micros=1_000_000,
        adverse_fx_minor_per_usd=10_000,
    ))
    with pytest.raises(GenerationPaused):
        require_operation_generation("resume_tailor", user_id=1)


def test_one_retained_model_capable_operation_consumes_pilot_quota(funded_scope, database):
    with database() as db:
        db.add(models.AnalysisRun(id="run_deterministic", user_id=1, operation="job_match",
            status="succeeded", idempotency_key="synthetic-basic-001", input_fingerprint="c" * 64,
            input_payload={"mode": "basic"}, model_cost_state="unquoted",
            created_at=datetime.now(UTC).replace(tzinfo=None), updated_at=datetime.now(UTC).replace(tzinfo=None)))
        db.commit()
        assert ai_pilot.require_new_operation(db, 1, "resume_tailor") is not None
        run = models.AnalysisRun(id="run_pilot_synthetic", user_id=1, operation="resume_tailor",
            status="failed", idempotency_key="pilot-1", input_fingerprint="a" * 64,
            input_payload={}, model_cost_quote={"version": "synthetic"}, model_cost_state="active",
            generation_attempt_limit=1,
            created_at=datetime.now(UTC).replace(tzinfo=None), updated_at=datetime.now(UTC).replace(tzinfo=None))
        db.add(run)
        db.commit()
        with pytest.raises(ai_pilot.PilotUnavailable):
            ai_pilot.require_new_operation(db, 1, "resume_tailor")


def test_provider_preflight_requires_exact_owner_run_model_and_single_attempt(funded_scope, database):
    with database() as db:
        run = models.AnalysisRun(id="run_pilot_provider", user_id=1, operation="resume_tailor",
            status="running", idempotency_key="pilot-2", input_fingerprint="b" * 64,
            input_payload={}, model_cost_quote={"version": "synthetic"}, model_cost_state="active",
            generation_attempt_limit=1,
            created_at=datetime.now(UTC).replace(tzinfo=None), updated_at=datetime.now(UTC).replace(tzinfo=None))
        db.add(run)
        db.commit()
        ai_pilot.require_provider_attempt(db, run, "google", "gemini-3-flash",
                                          "https://generativelanguage.googleapis.com")
        for provider, model in [("openai", "gemini-3-flash"), ("google", "gemini-3.6-flash")]:
            with pytest.raises(ai_pilot.PilotUnavailable):
                ai_pilot.require_provider_attempt(db, run, provider, model,
                                                  "https://generativelanguage.googleapis.com")
        run.generation_attempt_limit = 3
        with pytest.raises(ai_pilot.PilotUnavailable):
            ai_pilot.require_provider_attempt(db, run, "google", "gemini-3-flash",
                                              "https://generativelanguage.googleapis.com")
        run.generation_attempt_limit = 1
        run.generation_attempt_count = 1
        with pytest.raises(ai_pilot.PilotUnavailable):
            ai_pilot.require_provider_attempt(db, run, "google", "gemini-3-flash",
                                              "https://generativelanguage.googleapis.com")


@pytest.mark.parametrize("model,base", [
    ("gemini-3-flash", "https://generativelanguage.googleapis.com"),
    ("gpt-4o-mini", "https://api.openai.com/v1"),
])
def test_alternate_budget_cannot_reach_any_provider(funded_scope, monkeypatch, model, base):
    monkeypatch.setenv("LLM_API_KEY", "synthetic-not-a-key")
    monkeypatch.setattr(llm_client, "LLM_MODEL", model)
    monkeypatch.setattr(llm_client, "LLM_API_BASE", base)
    monkeypatch.setattr(llm_client.httpx, "post", lambda *a, **k: pytest.fail("No provider POST permitted"))
    with pytest.raises(GenerationBudgetExhausted):
        llm_client._chat_with_budget([{"role": "user", "content": "synthetic"}], GenerationBudget(limit=3))
