"""Provider integration tests use explicit synthetic prices, never live pricing."""
import json

import pytest
from backend.app.models import AnalysisRun
from backend.app.services.generation_budget import persistent_run_budget
from backend.app.services.model_cost_policy import freeze_run_quote
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from backend.tests.model_cost_fixtures import synthetic_policy


@pytest.fixture(autouse=True)
def model_cost_test_policy(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(synthetic_policy()))


@pytest.fixture(autouse=True)
def expense_cost_test_policy(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(synthetic_expense_policy()))


@pytest.fixture
def persisted_model_budget():
    from backend.tests.test_ai_efficiency import _database

    engine, factory = _database()
    with factory() as db:
        run = AnalysisRun(id="run_provider_test", user_id=1, operation="job_match", status="running",
                          idempotency_key="synthetic-provider-test", input_fingerprint="a" * 64,
                          input_payload={}, generation_attempt_limit=3)
        freeze_run_quote(run)
        db.add(run)
        db.commit()
    try:
        with persistent_run_budget(factory, "run_provider_test") as budget:
            yield budget
    finally:
        engine.dispose()
