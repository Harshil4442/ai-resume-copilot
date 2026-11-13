"""Real admission/budget/worker with synthetic eight-question supplier transport.

No Gemini, native service, or semantic quality qualification. Callback races use
independent SQL sessions and retain original accounting/unknown claim behavior.
"""
from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.domains.analysis import schemas, service, tasks
from backend.app.domains.credits import analysis as billing
from backend.app.services import llm_client
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from backend.tests.model_cost_fixtures import synthetic_policy
from backend.tests.test_ai_efficiency import _database
from backend.tests.test_llm_client import _question_set
from google import genai
from sqlalchemy.exc import DBAPIError


@pytest.fixture
def interview_factory(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("LLM_API_KEY", "synthetic-no-provider-key")
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(synthetic_policy()))
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(synthetic_expense_policy()))
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(genai, "Client", lambda **_: pytest.fail("No Gemini client is permitted"))
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def create_interview(factory):
    with factory() as db:
        run, created = service.create_run(db, user_id=1,
            payload=schemas.AnalysisRunCreate(operation="interview_questions", opportunity_id="opp_test",
                input={"mode": "enhanced", "num_questions": 8}),
            header_idempotency_key="fresh-interview-inputs-001")
        assert created
        return run.id


def synthetic_response():
    return SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=100,
        candidates_token_count=20, thoughts_token_count=0, total_token_count=120))


def synthetic_transport(monkeypatch, *, counts, generations):
    def chat(messages, budget):
        budget.preflight("google", "gemini-3.6-flash", messages, "https://generativelanguage.googleapis.com")
        counts.append("owned-count")
        event = budget.admit("google", "gemini-3.6-flash", messages,
            api_base="https://generativelanguage.googleapis.com")
        generations.append("owned-generation")
        budget.finish(event, latency_ms=1, response=synthetic_response())
        return json.dumps(_question_set())
    monkeypatch.setattr(llm_client, "_chat_with_budget", chat)


@pytest.mark.parametrize("stage", ["preflight", "before"])
def test_interview_change_during_original_budget_admission_never_reaches_supplier(
        interview_factory, monkeypatch, stage):
    factory = interview_factory
    original_context = billing.persistent_run_budget
    callbacks = []

    @contextmanager
    def changed(*args, **kwargs):
        with original_context(*args, **kwargs) as budget:
            original = getattr(budget, stage)

            def callback(*args, **kwargs):
                result = original(*args, **kwargs)
                with factory() as other:
                    other.get(models.Opportunity, "opp_test").job_description += " Changed while admission completed."
                    other.commit()
                callbacks.append(stage)
                return result

            setattr(budget, stage, callback)
            yield budget

    monkeypatch.setattr(billing, "persistent_run_budget", changed)
    counts, generations = [], []
    synthetic_transport(monkeypatch, counts=counts, generations=generations)
    run_id = create_interview(factory)
    assert tasks.process_analysis_run(run_id) == "failed"
    assert callbacks == [stage] and not generations
    assert counts == ([] if stage == "preflight" else ["owned-count"])
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert run.error_code == "SourceTranscriptionChanged"
        assert run.result_payload is None and run.usage_state == "released" and run.committed_units == 0
        assert db.get(models.User, 1).ai_credits == 100
        liabilities = db.query(models.ModelCostLiability).all()
        if stage == "preflight":
            assert not liabilities
        else:
            # Original before() already retained its admission; a refused
            # supplier dispatch cannot invent an outcome or free that exposure.
            assert len(liabilities) == 1 and liabilities[0].settled_cost_micros is None


def test_current_interview_still_delivers_eight_and_commits_original_budget_once(interview_factory, monkeypatch):
    counts, generations = [], []
    synthetic_transport(monkeypatch, counts=counts, generations=generations)
    run_id = create_interview(interview_factory)
    assert tasks.process_analysis_run(run_id) == "succeeded"
    assert tasks.process_analysis_run(run_id) == "succeeded"
    assert counts == ["owned-count"] and generations == ["owned-generation"]
    with interview_factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert len(run.result_payload["questions"]) == 8
        assert run.committed_units == 1 and run.usage_state == "committed"
        assert db.get(models.User, 1).ai_credits == 99
        assert db.query(models.ModelCostLiability).count() == 1


def test_retained_numeric_resume_selection_keeps_its_existing_meaning(interview_factory):
    run_id = create_interview(interview_factory)
    with interview_factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        payload = dict(run.input_payload, resume_id="1")
        run.input_payload = payload
        run.input_fingerprint = service._current_input_fingerprint(db, run, payload)
        db.commit()
        service.ensure_interview_inputs_current(db, run, payload)
        db.rollback()


@pytest.mark.parametrize("dialect,state,translated", [("postgresql", "55P03", True),
    ("postgresql", "40001", False), ("sqlite", "55P03", False)])
def test_only_postgres_nowait_conflict_becomes_nonretryable_busy_result(monkeypatch, dialect, state, translated):
    from backend.app.services import result_commit
    from backend.app.services.interview_contract import InterviewInputsChanged

    class OwnedDriverError(Exception):
        sqlstate = state

    original = DBAPIError(None, None, OwnedDriverError("owned fixed diagnostic"))
    rollbacks = []

    def query(*_):
        raise original

    db = SimpleNamespace(no_autoflush=nullcontext(), query=query, rollback=lambda: rollbacks.append(True),
        get_bind=lambda: SimpleNamespace(dialect=SimpleNamespace(name=dialect)))
    monkeypatch.setattr(result_commit, "begin_result_commit", lambda *_: None)
    expected = InterviewInputsChanged if translated else DBAPIError
    with pytest.raises(expected) as caught:
        service.lock_interview_inputs_current(db, SimpleNamespace(user_id=1, id="owned-run"), {})
    assert len(rollbacks) == int(translated)
    if translated:
        assert caught.value.retryable is False and "being edited" in str(caught.value)
    else:
        assert caught.value is original
