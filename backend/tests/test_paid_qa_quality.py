"""Paid Q&A succeeds only with a usable answer; provider cost survives a refund.

The SDK transport is synthetic. These tests execute the real endpoint, prompt
parser, durable provider-attempt budget and product/cost accounting transitions.
They do not establish semantic model quality or production provider prices.
"""
from __future__ import annotations

import json
import re
from types import SimpleNamespace

import pytest
from backend.app.domains.analysis.operations import _ensure_prompt_version
from backend.app.domains.usage.service import release_run_usage
from backend.app.models import (
    AnalysisRun,
    ModelCallEvent,
    ModelCostLiability,
    PromptVersion,
    UsageEvent,
    User,
)
from backend.app.routers.rag import ask_ai_about_match
from backend.app.schemas import RagAskRequest
from backend.app.services import llm_client
from backend.tests.test_learning_rag_privacy import _database
from google import genai


@pytest.fixture(autouse=True)
def _no_live_gemini_count(monkeypatch):
    monkeypatch.setattr(llm_client, "count_gemini_input", lambda *_: 10)


@pytest.fixture
def database():
    engine, factory = _database()
    try:
        yield factory
    finally:
        engine.dispose()


def _request(question="Compare two practical project approaches for this role.", mode="enhanced"):
    return RagAskRequest(job_match_id=2, question=question, mode=mode)


def _provider(monkeypatch, response, *, usage=True, error=None):
    calls = []

    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            if error is not None:
                raise error
            value = response(kwargs) if callable(response) else response
            return SimpleNamespace(
                text=value if isinstance(value, str) else json.dumps(value),
                usage_metadata=SimpleNamespace(
                    prompt_token_count=100, candidates_token_count=20,
                    thoughts_token_count=0, total_token_count=120,
                ) if usage else None,
            )

    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-only")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(models=Models(), close=lambda: None))
    return calls


@pytest.mark.parametrize("response", [
    "not JSON; private-provider-payload",
    {"answer": "private-provider-payload", "confidence": "high", "sources": ["another-owner"]},
    {"answer": "", "confidence": "high", "sources": []},
    {"answer": {"invented": "private-provider-payload"}, "confidence": "low", "sources": []},
    {"answer": "private-provider-payload", "confidence": "high", "sources": []},
])
def test_invalid_generated_answer_refunds_once_and_retains_provider_expense(
    database, monkeypatch, caplog, response,
):
    calls = _provider(monkeypatch, response)
    with database() as db:
        result = ask_ai_about_match(_request(), db, db.get(User, 1))
        assert result.mode == "unavailable"
        assert result.provenance == "local_guidance"
        assert result.sources == []
        assert "No credits were charged" in result.answer
        assert "private-provider-payload" not in result.answer
        run = db.query(AnalysisRun).one()
        assert run.status == "failed" and run.usage_state == "released"
        assert run.committed_units == 0 and run.result_payload is None
        assert run.error_code == "EnhancedAnswerUnavailable"
        assert run.generation_attempt_count == 1
        assert run.model_cost_settled_micros == 120
        assert run.model_cost_reserved_micros == 0
        assert db.get(User, 1).ai_credits == 20
        events = db.query(UsageEvent).filter_by(source_id=run.id).all()
        assert sorted(item.event_type for item in events) == ["release", "reserve"]
        assert sum(item.amount for item in events) == 0
        event = db.query(ModelCallEvent).one()
        liability = db.query(ModelCostLiability).one()
        assert event.status == "succeeded"  # The transport succeeded; the answer did not.
        assert event.cost_state == liability.cost_state == "settled"
        assert event.settled_cost_micros == liability.settled_cost_micros == 120
        assert liability.pricing_quote["expense_pool"] == "failed_work"
        # Reconciliation/cleanup may revisit a failure; it cannot refund twice.
        release_run_usage(db, run, reason="Repeated reconciliation")
        db.commit()
        assert db.query(UsageEvent).filter_by(source_id=run.id, event_type="release").count() == 1
        assert db.get(User, 1).ai_credits == 20
        assert db.query(ModelCostLiability).one().settled_cost_micros == 120
    assert len(calls) == 1
    assert "private-provider-payload" not in caplog.text


def test_invalid_answer_with_unknown_provider_usage_keeps_financial_hold(database, monkeypatch):
    calls = _provider(monkeypatch, "not JSON", usage=False)
    with database() as db:
        result = ask_ai_about_match(_request(), db, db.get(User, 1))
        assert result.mode == "unavailable"
        run = db.query(AnalysisRun).one()
        liability = db.query(ModelCostLiability).one()
        assert run.usage_state == "released" and db.get(User, 1).ai_credits == 20
        assert liability.cost_state == "usage_unavailable"
        assert liability.reserved_cost_micros > 0
        assert liability.settled_at is None
        assert run.model_cost_reserved_micros == liability.reserved_cost_micros
        assert liability.pricing_quote["expense_pool"] == "failed_work"
    assert len(calls) == 1


def test_valid_answer_commits_once_and_keeps_grounded_sources(database, monkeypatch):
    def valid(kwargs):
        content = kwargs["contents"][-1]["parts"][0]["text"]
        source = re.search(r'"id": "([^"]+)"', content).group(1)
        return {"answer": "Compare scope, testability and relevance to the role.",
                "confidence": "medium", "sources": [source], "suggested_followups": []}

    calls = _provider(monkeypatch, valid)
    with database() as db:
        result = ask_ai_about_match(_request(), db, db.get(User, 1))
        assert result.mode == "enhanced" and result.provenance == "generated"
        assert result.sources
        run = db.query(AnalysisRun).one()
        assert run.status == "succeeded" and run.usage_state == "committed"
        assert run.committed_units == run.estimated_units > 0
        assert db.get(User, 1).ai_credits == 20 - run.committed_units
        assert db.query(UsageEvent).filter_by(source_id=run.id, event_type="commit").count() == 1
        assert db.query(UsageEvent).filter_by(source_id=run.id, event_type="release").count() == 0
        assert db.query(ModelCostLiability).one().settled_cost_micros == 120
    assert len(calls) == 1


@pytest.mark.parametrize("mode", ["basic", "enhanced"])
def test_stored_data_answer_is_free_without_model_policy(database, monkeypatch, mode):
    monkeypatch.delenv("LLM_MODEL_COST_POLICY_JSON", raising=False)
    monkeypatch.setattr(llm_client, "_chat", lambda *args: pytest.fail("No generation is needed"))
    with database() as db:
        result = ask_ai_about_match(_request("Show my approved evidence.", mode), db, db.get(User, 1))
        assert result.mode == "direct" and result.sources == ["evidence:approved"]
        assert db.get(User, 1).ai_credits == 20
        assert db.query(AnalysisRun).count() == db.query(UsageEvent).count() == 0
        assert db.query(ModelCallEvent).count() == db.query(ModelCostLiability).count() == 0


def test_basic_unanswerable_question_does_not_reserve_credits(database, monkeypatch):
    monkeypatch.delenv("LLM_MODEL_COST_POLICY_JSON", raising=False)
    with database() as db:
        result = ask_ai_about_match(_request(mode="basic"), db, db.get(User, 1))
        assert result.mode == "unavailable"
        assert db.query(AnalysisRun).count() == db.query(UsageEvent).count() == 0
        assert db.get(User, 1).ai_credits == 20


def test_provider_failure_is_not_reported_as_a_completed_answer(database, monkeypatch):
    calls = _provider(monkeypatch, None, error=ValueError("private-provider-rejection"))
    with database() as db:
        with pytest.raises(llm_client.LLMProviderError, match="provider rejected"):
            ask_ai_about_match(_request(), db, db.get(User, 1))
        run = db.query(AnalysisRun).one()
        assert run.status == "failed" and run.usage_state == "released"
        assert db.get(User, 1).ai_credits == 20
        liability = db.query(ModelCostLiability).one()
        assert liability.cost_state == "outcome_unknown"
        assert liability.reserved_cost_micros > 0 and liability.settled_at is None
        assert "private-provider-rejection" not in run.error_message
    assert len(calls) == 1


def _prompt_callable():
    return "Synthetic prompt registration fixture"


def test_prompt_registration_is_not_quality_evaluation(database):
    with database() as db:
        prompt = _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
        assert prompt.release_status == "observed" and prompt.activated_at is None
        assert prompt.evaluation_result == {"status": "not_evaluated", "provenance": "runtime_registration"}
        db.commit()
        assert db.query(PromptVersion).count() == 1


def test_known_automatic_legacy_pass_is_not_preserved_as_qualification(database):
    with database() as db:
        prompt = _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
        prompt.evaluation_result = {"contract_gate": "golden-v1", "passed": True}
        prompt.release_status = "active"
        db.commit()
        restored = _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
        assert restored.evaluation_result["status"] == "not_evaluated"
        assert "passed" not in restored.evaluation_result
        assert restored.evaluation_result["superseded_automatic_metadata"] == {"contract_gate": "golden-v1", "passed": True}
        assert restored.release_status == "observed"


def test_actual_review_metadata_is_not_overwritten_by_runtime_registration(database):
    with database() as db:
        prompt = _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
        evidence = {"status": "qualified", "report_sha256": "a" * 64,
                    "provenance": "explicit_review", "passed": True}
        prompt.evaluation_result = evidence
        prompt.release_status = "active"
        db.commit()
        restored = _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
        assert restored.evaluation_result == evidence and restored.release_status == "active"


def test_changed_prompt_still_requires_a_version_bump(database):
    with database() as db:
        prompt = _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
        prompt.template_checksum = "b" * 64
        db.commit()
        with pytest.raises(RuntimeError, match="version bump"):
            _ensure_prompt_version(db, operation="job_match", version="test-registration-v1", callable_object=_prompt_callable)
