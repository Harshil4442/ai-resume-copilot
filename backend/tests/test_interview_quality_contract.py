"""Real admission/cache/accounting contracts with synthetic model transport.

This suite is not a substitute for blinded relevance/usefulness evaluation.
"""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.app.domains.analysis import schemas, service, tasks
from backend.app.domains.common import utcnow
from backend.app.models import (
    AnalysisRun,
    EvidenceItem,
    ModelCallEvent,
    ModelCostLiability,
    Opportunity,
    Resume,
    UsageEvent,
    User,
    UserProfile,
)
from backend.app.services import interview_contract, llm_client
from backend.tests.test_ai_efficiency import _database
from backend.tests.test_llm_client import _question_set
from fastapi import HTTPException
from google import genai


@pytest.fixture
def database(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def _create(factory, *, key="interview-contract-001", count=8, mode="enhanced", user_id=1):
    with factory() as db:
        run, created = service.create_run(db, user_id=user_id, payload=schemas.AnalysisRunCreate(
            operation="interview_questions", opportunity_id="opp_test",
            input={"mode": mode, "num_questions": count}), header_idempotency_key=key)
        return run.id, created


def _evidence(identity="fact-01", **overrides):
    return EvidenceItem(id=identity, user_id=1, resume_id=1, category="experience",
                        title="Approved project", evidence_text="Built Python services.",
                        approval_state="approved", **overrides)


@pytest.mark.parametrize("count", [1, 3, 7, 9, 12, "8", 8.0, True])
def test_paid_request_requires_exact_eight_before_reserving(database, count):
    with pytest.raises(HTTPException) as exc:
        _create(database, count=count)
    assert exc.value.status_code == 422
    with database() as db:
        assert db.query(AnalysisRun).count() == db.query(UsageEvent).count() == 0
        assert db.get(User, 1).ai_credits == 100


def test_curated_route_keeps_its_separately_labelled_count(database, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda _: pytest.fail("Curated questions do not need a model"))
    run_id, _ = _create(database, count=3, mode="curated")
    assert tasks.process_analysis_run(run_id) == "succeeded"
    with database() as db:
        result = db.get(AnalysisRun, run_id).result_payload
        assert result["mode"] == "curated" and len(result["questions"]) == 3
        assert db.query(ModelCallEvent).count() == 0


@pytest.mark.parametrize("defect", ["extra", "unknown_citation", "duplicate", "missing_guidance"])
def test_invalid_eight_question_contract_is_never_silently_reduced(monkeypatch, defect):
    data = _question_set()
    if defect == "extra":
        data.append({**data[0], "question": "What else would you evaluate before shipping?"})
    elif defect == "unknown_citation":
        data[0]["evidence_ids"] = ["not-approved"]
    elif defect == "duplicate":
        data[1]["question"] = data[0]["question"].upper().replace(" ", "  ").replace("?", " !")
    else:
        data[0]["coaching_angle"] = " "
    calls = []
    monkeypatch.setattr(llm_client, "_chat", lambda messages: calls.append(messages) or json.dumps(data))
    with pytest.raises(llm_client.InterviewOutputError, match="complete set of 8"):
        llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")
    assert len(calls) == 2


def test_complete_long_evidence_and_job_are_preserved_in_prompt_and_guidance(monkeypatch):
    fact = ("Measured search latency and verified the result. " * 150) + "Final metric: reduced latency by 22.75%."
    job = ("Design search systems and test ranking reliability. " * 160) + "Final requirement: disaster recovery."
    data = _question_set()
    data[0]["evidence_ids"] = ["fact-long"]
    calls = []
    monkeypatch.setattr(llm_client, "_chat", lambda messages: calls.append(messages) or json.dumps(data))
    result = llm_client.generate_interview_questions("Search Engineer", job, approved_evidence=[
        {"id": "fact-long", "title": "Verified project", "text": fact},
    ])
    supplied = calls[0][1]["content"].split("\n", 1)[1].split("\n\nNumber of Qs:", 1)[0]
    source = json.loads(supplied)
    assert source["job"]["description"] == job
    assert source["approved_evidence"][0]["text"] == fact
    assert fact in result[0]["answer"]
    assert result[0]["answer_state"] == "evidence_backed"


def test_repair_keeps_complete_context_without_replaying_untrusted_model_output(monkeypatch):
    private_output = "untrusted-private-provider-output" * 4000
    replies = iter([private_output, json.dumps(_question_set())])
    calls = []
    def chat(messages):
        calls.append(list(messages))
        return next(replies)
    monkeypatch.setattr(llm_client, "_chat", chat)
    result = llm_client.generate_interview_questions("Search Engineer", "Build reliable search.")
    assert len(result) == 8 and len(calls) == 2
    assert calls[0][1] == calls[1][1]
    assert all(private_output not in message["content"] for message in calls[1])
    assert "exactly 8 distinct questions" in calls[1][-1]["content"]


def test_oversized_complete_context_refuses_before_reservation_or_generation(database, monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda _: pytest.fail("Oversize inputs must not reach a model"))
    with database() as db:
        fact = _evidence()
        fact.evidence_text = "Important approved fact. " * 4000
        db.add(fact)
        db.commit()
    with pytest.raises(HTTPException) as exc:
        _create(database)
    assert exc.value.status_code == 422
    assert exc.value.detail["code"] == "interview_context_unavailable"
    assert "No content was shortened" in exc.value.detail["message"]
    with database() as db:
        assert db.query(AnalysisRun).count() == db.query(UsageEvent).count() == 0
        assert db.query(ModelCallEvent).count() == 0


def test_more_than_forty_complete_owned_evidence_units_reach_generation(database, monkeypatch):
    with database() as db:
        db.add_all([_evidence(f"fact-{index:02d}") for index in range(45)])
        db.add(User(id=2, email="other@example.com"))
        db.add(EvidenceItem(id="other-private", user_id=2, resume_id=1, category="experience",
                            title="Other owner", evidence_text="Other private fact", approval_state="approved"))
        db.add(EvidenceItem(id="pending", user_id=1, resume_id=1, category="experience",
                            title="Pending", evidence_text="Not approved", approval_state="pending"))
        db.commit()
    calls = []
    def chat(messages):
        calls.append(messages)
        data = _question_set()
        data[0]["evidence_ids"] = ["fact-44"]
        return json.dumps(data)
    monkeypatch.setattr(llm_client, "_chat", chat)
    run_id, _ = _create(database)
    assert tasks.process_analysis_run(run_id) == "succeeded"
    supplied = calls[0][1]["content"].split("\n", 1)[1].split("\n\nNumber of Qs:", 1)[0]
    assert len(json.loads(supplied)["approved_evidence"]) == 45
    assert "other-private" not in supplied and "Not approved" not in supplied
    with database() as db:
        result = db.get(AnalysisRun, run_id).result_payload
        assert len(result["questions"]) == 8
        assert result["questions"][0]["evidence_ids"] == ["fact-44"]
        assert result["contract_versions"] == interview_contract.contract_versions()


@pytest.mark.parametrize("change", ["source", "correction", "approval", "provenance", "job_revision", "preferences",
                                    "prompt", "schema", "validator", "context", "model", "effective_model"])
def test_semantic_cache_invalidates_material_dependency_changes(database, monkeypatch, change):
    with database() as db:
        db.add(_evidence())
        db.add(UserProfile(user_id=1, target_role="Software Engineer"))
        db.commit()
    original, created = _create(database)
    assert created
    assert _create(database, key="second-equivalent-request") == (original, False)
    if change in {"prompt", "schema", "validator", "context"}:
        key = {"prompt": "PROMPT_VERSION", "schema": "SCHEMA_VERSION", "validator": "VALIDATOR_VERSION",
               "context": "CONTEXT_VERSION"}[change]
        monkeypatch.setattr(interview_contract, key, "new-tested-version")
    elif change == "model":
        monkeypatch.setenv("LLM_MODEL", "new-reviewed-model")
    elif change == "effective_model":
        monkeypatch.setattr(llm_client, "LLM_MODEL", "new-effective-model")
    else:
        with database() as db:
            fact = db.get(EvidenceItem, "fact-01")
            if change == "source":
                db.get(Resume, 1).source_document = b"changed-native-source"
            elif change == "correction":
                fact.updated_at = utcnow() + timedelta(seconds=1)
            elif change == "approval":
                fact.approval_state = "pending"
            elif change == "provenance":
                fact.source_ref = "corrected-source-span"
            elif change == "job_revision":
                db.get(Opportunity, "opp_test").job_snapshot = {"revision": "updated-origin-revision"}
            else:
                db.query(UserProfile).one().preferred_location = "Bengaluru"
            db.commit()
    revised, created = _create(database, key="third-revised-request")
    assert created and revised != original


def test_private_reuse_rechecks_ownership(database):
    _create(database)
    with database() as db:
        db.add(User(id=2, email="other@example.com", ai_credits=100))
        db.commit()
    with pytest.raises(HTTPException) as exc:
        _create(database, key="other-owner-request", user_id=2)
    assert exc.value.status_code == 404


def test_changed_inputs_before_worker_release_without_model_call(database, monkeypatch):
    run_id, _ = _create(database)
    with database() as db:
        db.get(Opportunity, "opp_test").job_description = "A different role with different job requirements."
        db.commit()
    monkeypatch.setattr(llm_client, "_chat", lambda _: pytest.fail("Stale accepted inputs cannot generate"))
    assert tasks.process_analysis_run(run_id) == "failed"
    with database() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.error_code == "InterviewInputsChanged" and run.usage_state == "released"
        assert run.result_payload is None and db.get(User, 1).ai_credits == 100
        assert db.query(ModelCallEvent).count() == 0


def test_evidence_revoked_during_generation_releases_credit_and_retains_expense(database, monkeypatch):
    with database() as db:
        db.add(_evidence())
        db.commit()
    class Models:
        def generate_content(self, **kwargs):
            with database() as db:
                db.get(EvidenceItem, "fact-01").approval_state = "pending"
                db.commit()
            questions = _question_set()
            questions[0]["evidence_ids"] = ["fact-01"]
            return SimpleNamespace(text=json.dumps(questions), usage_metadata=SimpleNamespace(
                prompt_token_count=100, candidates_token_count=20, thoughts_token_count=0, total_token_count=120))
    monkeypatch.setenv("LLM_API_KEY", "synthetic-test-only")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(models=Models(), close=lambda: None))
    run_id, _ = _create(database)
    assert tasks.process_analysis_run(run_id) == "failed"
    with database() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.error_code == "InterviewInputsChanged" and run.usage_state == "released"
        assert run.result_payload is None and db.get(User, 1).ai_credits == 100
        liability = db.query(ModelCostLiability).one()
        assert liability.cost_state == "settled" and liability.settled_cost_micros == 120
        assert liability.pricing_quote["expense_pool"] == "failed_work"
        assert db.query(UsageEvent).filter_by(source_id=run_id, event_type="release").count() == 1
