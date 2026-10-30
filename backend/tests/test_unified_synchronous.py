"""Real inline reservations around synthetic provider calls and actual route functions."""
import asyncio
import json
from contextlib import contextmanager
from datetime import timedelta

import pytest
from backend.app import models, schemas
from backend.app.domains.credits import (
    execution,
    pricing,
    runtime,
    service,
    service_quotes,
    synchronous,
)
from backend.app.domains.credits.models import (
    CreditExecutionAttempt,
    CreditExecutionHold,
    CreditReservation,
)
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.routers import llm, rag, recommendations, resume
from backend.app.services import llm_client
from backend.app.services.generation_budget import current_budget
from backend.tests.fixtures.transcription_source import source_for_text
from backend.tests.test_unified_credits import NOW, factory, grant
from backend.tests.test_unified_execution import budgets, financial_policy, response, route
from fastapi import HTTPException

assert factory


@pytest.fixture(autouse=True)
def _no_live_gemini_count(monkeypatch):
    monkeypatch.setattr(llm_client, "count_gemini_input", lambda *_: 10)


@pytest.fixture
def setup(factory, monkeypatch):
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("FREE_CORE_ENABLED", "false")
    monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", json.dumps({
        "checked_at": (NOW - timedelta(days=1)).isoformat(),
        "expires_at": (NOW + timedelta(days=20)).isoformat(),
        "urls": {"https://docs.docker.com/get-started/": "a" * 64}}))
    with factory() as db:
        grant(db)
        db.add(models.Resume(id=1, user_id=1, raw_text="Built 2 Python APIs and Docker services.", skills=["Python"], sections={}, contact_info={},
            source_format="pdf", source_document=source_for_text("Built 2 Python APIs and Docker services.")))
        db.flush()
        db.add(models.JobMatch(id=1, user_id=1, resume_id=1, job_title="Backend engineer", company="Example", job_description="Build reliable Python APIs and Docker services.", true_gaps=["Docker"], required_skills=["Python", "Docker"], partial_matches=[], match_score=75, fit_summary="Relevant Python experience", dimension_scores=[], improvement_tips=[], full_matches=[]))
        db.add(models.EvidenceItem(id="evidence-1", user_id=1, resume_id=1, category="experience", title="API work", evidence_text="Built 2 Python APIs and Docker services.", approval_state="approved", source_ref="resume:1", skills=["Python", "Docker"], metrics={}))
        db.commit()
    return factory, monkeypatch


def configure(monkeypatch, operation):
    from backend.app.services.delivery_contracts import current_output_contract

    chosen = route(attempts=1)
    chosen = chosen.model_copy(update={"operation": operation, "credits": pricing.SERVICE_CREDITS[operation],
        "output_contract_sha256": current_output_contract(operation),
        "model_quotes": tuple(item.model_copy(update={"max_input_tokens": 20_000}) for item in chosen.model_quotes),
        "supported_limits": {"context_bytes": 40_000, "evidence_items": 100}})
    config = runtime.RuntimeConfig(policy=financial_policy(), budgets=budgets(), routes=(chosen,))
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
    reviewed_runtime(monkeypatch, config)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    return config


def payload(operation):
    inputs = {
        "resume_enrichment": {"resume_id": 1},
        "bullet_rewrite": {"resume_id": 1, "job_description": "Build Python APIs and Docker services", "tone": "concise"},
        "enhanced_qa": {"job_match_id": 1, "resume_id": 1, "question": "Suggest a story structure for a trade-off conversation", "recent_messages": [], "mode": "enhanced"},
        "learning_strategy": {"match_id": 1, "mode": "enhanced"},
    }
    return synchronous.SynchronousServiceRequest(operation=operation, input=inputs[operation])


def output(operation):
    return {
        "resume_enrichment": {"skills": ["Python", "Docker", "Fabricated"]},
        "bullet_rewrite": {"bullets": [{"original_text": "Built 2 Python APIs and Docker services.", "replacement_text": "Developed 2 Python APIs and Docker services.", "evidence_ids": ["evidence-1"]}], "summary": "Clearer action verb."},
        "enhanced_qa": {"answer": "Use your approved API work as a starting point and explain the trade-offs you actually considered.", "confidence": "medium", "sources": ["evidence-1"], "suggested_followups": []},
        "learning_strategy": {"readiness_summary": "Focus on Docker evidence for this role.", "missing_hiring_signals": [{"signal": "Docker", "why_it_matters": "Role requirement", "severity": "high"}], "learning_priorities": [{"skill": "docker", "priority": "high", "current_status": "true_gap", "reason": "Saved gap", "expected_outcome": "Working container"}], "project_recommendations": [{"title": "Container project", "covers_gaps": ["Docker"], "description": "Build a container", "implementation_steps": ["Write Dockerfile", "Run tests"], "resume_bullets": [], "interview_talking_points": ["Explain image size"]}], "timeline": [{"phase": "Week 1", "focus": "Docker", "deliverable": "Container"}]},
    }[operation]


def quote(db, request):
    _, fingerprint, config, chosen = synchronous.prepare_quote_context(db, user_id=1, payload=request)
    saved = service_quotes.issue_service_quote(db, user_id=1, input_fingerprint=fingerprint,
        idempotency_key="quote-sync-test", route=chosen, policy=config.policy)
    db.commit()
    return saved.id


def provider(monkeypatch, operation, *, raw=None, callback=None, error=False):
    calls = []
    def chat(messages, budget):
        assert budget is current_budget()
        event = budget.admit("google", "synthetic-model", messages, api_base="https://generativelanguage.googleapis.com")
        calls.append(messages)
        if callback:
            callback()
        if error:
            failure = RuntimeError("Synthetic lost response")
            budget.finish(event, latency_ms=1, error=failure)
            raise failure
        budget.finish(event, latency_ms=1, response=response())
        return json.dumps(raw if raw is not None else output(operation))
    monkeypatch.setattr(llm_client, "LLM_MODEL", "synthetic-model")
    monkeypatch.setattr(llm_client, "LLM_API_BASE", "https://generativelanguage.googleapis.com")
    monkeypatch.setattr(llm_client, "_api_key", lambda: "synthetic-never-network")
    monkeypatch.setattr(llm_client, "_chat_with_budget", chat)
    return calls


def invoke(db, request, quote_id, *, key="execute-sync-test"):
    data = request.input | {"accepted_service_quote_id": quote_id, "idempotency_key": key}
    user = db.get(models.User, 1)
    if request.operation == "resume_enrichment":
        return resume.enrich_saved_resume(1, schemas.ResumeEnrichmentRequest(**data), db, user)
    if request.operation == "bullet_rewrite":
        return asyncio.run(llm.rewrite_bullets_endpoint(schemas.RewriteBulletsRequest(**data), db, user))
    if request.operation == "enhanced_qa":
        return rag.ask_ai_about_match(schemas.RagAskRequest(**data), db, user)
    return asyncio.run(recommendations.match_learning_strategy(schemas.LearningStrategyRequest(**data), db, user))


@pytest.mark.parametrize("operation", ["resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"])
def test_actual_sync_endpoint_charges_once_and_replays_without_dispatch(setup, operation):
    factory, monkeypatch = setup
    configure(monkeypatch, operation)
    calls = provider(monkeypatch, operation)
    with factory() as db:
        request = payload(operation)
        quote_id = quote(db, request)
        result = invoke(db, request, quote_id)
        assert result["credit_execution"]["charged_this_request"] == pricing.SERVICE_CREDITS[operation]
        assert db.query(DispatchOutbox).count() == 0
        assert db.query(CreditReservation).count() == 1
        assert db.get(models.User, 1).ai_credits == 17
        hold = db.query(CreditExecutionHold).one()
        assert hold.delivery_state == "delivered" and hold.native_settled_minor is None
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
        again = invoke(db, request, quote_id)
        assert again["credit_execution"]["reused"] and again["credit_execution"]["charged_this_request"] == 0
        assert len(calls) == 1
        assert synchronous.lookup_result(db, user_id=1, payload=request)["result"]["credit_execution"]["reused"]
        assert service.wallet(db, user_id=1).available_credits == 40 - pricing.SERVICE_CREDITS[operation]


def test_no_quote_or_changed_question_never_calls_or_reserves(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        with pytest.raises(HTTPException):
            invoke(db, request, None)
        quote_id = quote(db, request)
        request.input["question"] += " Changed after review."
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote_id)
        assert caught.value.detail["run_created"] is False
        assert db.query(CreditReservation).count() == db.query(models.AnalysisRun).count() == 0
        assert not calls


@pytest.mark.parametrize("raw", [{}, {"answer": "invented", "confidence": "high", "sources": ["unapproved"], "suggested_followups": []}])
def test_invalid_output_refunds_once_and_retains_supplier_cost(setup, raw):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa", raw=raw)
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote_id)
        assert caught.value.detail["credits_charged"] == 0
        run_id = caught.value.detail["run_id"]
        assert db.query(models.ModelCostLiability).one().settled_cost_micros > 0
        assert db.query(CreditExecutionHold).one().native_settled_minor is None
        assert service.wallet(db, user_id=1).available_credits == 40
        with pytest.raises(HTTPException) as replay:
            invoke(db, request, quote_id)
        assert replay.value.detail["run_id"] == run_id
        assert len(calls) == 1


def test_unknown_provider_failure_refunds_but_cannot_retry_same_quote(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa", error=True)
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        with pytest.raises(RuntimeError):
            invoke(db, request, quote_id)
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.query(CreditExecutionHold).one().state == "outcome_unknown"
        assert synchronous.lookup_result(db, user_id=1, payload=request, idempotency_key="execute-sync-test")["reused_run"]["status"] == "failed"
        with pytest.raises(HTTPException):
            invoke(db, request, quote_id, key="different-new-key")
        assert len(calls) == 1


@pytest.mark.parametrize("stage", ["preflight", "before"])
def test_source_change_during_native_budget_io_refuses_supplier_and_retains_any_claim(setup, stage):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    provider(monkeypatch, "enhanced_qa")
    original_context = execution.persistent_execution_budget
    @contextmanager
    def interrupted(*args, **kwargs):
        with original_context(*args, **kwargs) as active:
            original = getattr(active, stage)
            def callback(*args, **kwargs):
                result = original(*args, **kwargs)
                with factory() as other:
                    other.get(models.Resume, 1).source_document += b"source-rotation-after-native-admission"
                    other.commit()
                return result
            setattr(active, stage, callback)
            yield active
    monkeypatch.setattr(execution, "persistent_execution_budget", interrupted)
    counts, generations = [], []
    def chat(messages, active):
        active.preflight("google", "synthetic-model", messages, "https://generativelanguage.googleapis.com")
        counts.append("synthetic-count-only")
        event = active.admit("google", "synthetic-model", messages, api_base="https://generativelanguage.googleapis.com")
        generations.append("synthetic-generation-only")
        active.finish(event, latency_ms=1, response=response())
        return json.dumps(output("enhanced_qa"))
    monkeypatch.setattr(llm_client, "_chat_with_budget", chat)
    with factory() as db:
        selected = payload("enhanced_qa")
        quote_id = quote(db, selected)
        with pytest.raises(HTTPException) as caught:
            invoke(db, selected, quote_id)
        assert caught.value.detail["credits_charged"] == 0
        assert counts == ([] if stage == "preflight" else ["synthetic-count-only"])
        assert not generations
        assert service.wallet(db, user_id=1).available_credits == 40
        run = db.query(models.AnalysisRun).one()
        assert run.status == "failed" and run.result_payload is None and run.committed_units == 0
        attempts = db.query(CreditExecutionAttempt).all()
        if stage == "before":
            assert any(item.state in {"started", "outcome_unknown"} for item in attempts)
            assert db.query(CreditExecutionHold).one().state == "outcome_unknown"
            assert db.query(models.ModelCostLiability).one().settled_cost_micros is None
        else:
            assert all(item.state == "cancelled" for item in attempts)
        with pytest.raises(HTTPException):
            invoke(db, selected, quote_id)
        assert not generations


@pytest.mark.parametrize("change", ["source", "evidence", "job", "history"])
def test_complete_semantic_context_invalidates_quote_without_spend(setup, change):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        if change == "source":
            db.get(models.Resume, 1).raw_text += " A changed achievement."
        elif change == "evidence":
            db.get(models.EvidenceItem, "evidence-1").approval_state = "rejected"
        elif change == "job":
            db.get(models.JobMatch, 1).job_description += " A new requirement."
        else:
            request.input["recent_messages"] = [{"role": "user", "content": "Use a different goal."}]
        db.commit()
        with pytest.raises(HTTPException):
            invoke(db, request, quote_id)
        assert not calls and db.query(CreditReservation).count() == 0


def test_revocation_during_generation_refunds_with_supplier_cost_retained(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    def revoke():
        with factory() as other:
            other.get(models.EvidenceItem, "evidence-1").approval_state = "rejected"
            other.commit()
    provider(monkeypatch, "enhanced_qa", callback=revoke)
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote_id)
        assert caught.value.detail["credits_charged"] == 0
        assert db.query(models.AnalysisRun).one().result_payload is None
        assert db.query(models.ModelCostLiability).one().settled_cost_micros > 0
        assert service.wallet(db, user_id=1).available_credits == 40


def test_full_units_and_all_history_are_in_prompt_or_rejected_not_truncated(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        long = "x" * 5001 + "TAIL MUST REMAIN"
        db.get(models.Resume, 1).raw_text = long
        db.get(models.EvidenceItem, "evidence-1").evidence_text = long
        db.commit()
        request.input["recent_messages"] = [{"role": "user", "content": f"message-{i}-" + "x" * 1201} for i in range(7)]
        prepared = synchronous.owned_context(db, user_id=1, payload=request)
        text = prepared.messages[1]["content"]
        assert text.count("TAIL MUST REMAIN") >= 2
        assert "message-0-" in text and "message-6-" in text
        db.get(models.Resume, 1).raw_text = "x" * 100_000
        db.commit()
        with pytest.raises(HTTPException) as caught:
            synchronous.prepare_quote_context(db, user_id=1, payload=request)
        assert "No content was shortened" in caught.value.detail["message"]
        assert db.query(CreditReservation).count() == 0


def test_other_owner_cannot_quote_or_recover_private_work(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    with factory() as db:
        with pytest.raises(HTTPException) as caught:
            synchronous.lookup_result(db, user_id=2, payload=payload("enhanced_qa"))
        assert caught.value.status_code == 404
        assert db.query(CreditReservation).count() == 0


def test_no_useful_enrichment_restores_credits_and_keeps_original_source(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "resume_enrichment")
    provider(monkeypatch, "resume_enrichment", raw={"skills": ["Python", "InventedSkill"]})
    with factory() as db:
        request = payload("resume_enrichment")
        quote_id = quote(db, request)
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote_id)
        assert caught.value.detail["credits_charged"] == 0
        assert db.get(models.Resume, 1).raw_text == "Built 2 Python APIs and Docker services."
        assert db.get(models.Resume, 1).skills == ["Python"]
        assert service.wallet(db, user_id=1).available_credits == 40


@pytest.mark.parametrize("operation", ["enhanced_qa", "learning_strategy", "bullet_rewrite"])
def test_unsupported_personal_claim_refunds_once_and_retains_real_supplier_accounting(setup, operation):
    factory, monkeypatch = setup
    configure(monkeypatch, operation)
    raw = output(operation)
    if operation == "enhanced_qa":
        raw["answer"] = "You have 10 years of Java experience."
    elif operation == "learning_strategy":
        raw["timeline"][0]["deliverable"] = "You led Python platform."
    else:
        raw["bullets"][0]["replacement_text"] = "Led 2 Python APIs and Docker services."
    calls = provider(monkeypatch, operation, raw=raw)
    with factory() as db:
        request = payload(operation)
        quote_id = quote(db, request)
        with pytest.raises(HTTPException) as failure:
            invoke(db, request, quote_id)
        assert failure.value.detail["credits_charged"] == 0
        run = db.query(models.AnalysisRun).one()
        assert run.status == "failed" and run.result_payload is None
        assert service.wallet(db, user_id=1).available_credits == 40
        liability = db.query(models.ModelCostLiability).one()
        assert liability.cost_state == "settled" and liability.settled_cost_micros > 0
        assert db.query(CreditExecutionHold).one().delivery_state == "failed"
        with pytest.raises(HTTPException):
            invoke(db, request, quote_id)
        assert len(calls) == 1 and service.wallet(db, user_id=1).available_credits == 40


def test_reused_new_key_is_durably_bound_to_original_result(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        first = invoke(db, request, quote_id)
        second = invoke(db, request, None, key="new-reuse-request")
        assert second["credit_execution"]["run_id"] == first["credit_execution"]["run_id"]
        assert synchronous.lookup_result(db, user_id=1, payload=request, idempotency_key="new-reuse-request")["reused_run"]["id"] == first["credit_execution"]["run_id"]
        request.input["question"] += " changed"
        with pytest.raises(HTTPException):
            invoke(db, request, quote_id, key="new-reuse-request")
        assert len(calls) == 1


def test_expired_resource_review_blocks_new_paid_learning_but_not_saved_reuse(setup):
    from datetime import UTC, datetime
    factory, monkeypatch = setup
    configure(monkeypatch, "learning_strategy")
    calls = provider(monkeypatch, "learning_strategy")
    with factory() as db:
        request = payload("learning_strategy")
        quote_id = quote(db, request)
        invoke(db, request, quote_id)
        review = json.loads(__import__('os').environ["LEARNING_RESOURCE_REVIEW_JSON"])
        review["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
        monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", json.dumps(review))
        reused = synchronous.lookup_result(db, user_id=1, payload=request)
        assert "expired" in reused["result"]["warnings"][-1]
        db.get(models.JobMatch, 1).job_description += " Material changed scope"
        db.commit()
        with pytest.raises(HTTPException) as caught:
            synchronous.prepare_quote_context(db, user_id=1, payload=request)
        assert caught.value.status_code == 503 and len(calls) == 1


def test_curated_learning_has_funded_zero_credit_hold_and_saved_reuse(setup):
    from backend.app.domains.free_core.models import FreeCoreReservation
    from backend.tests.test_free_core import synthetic_free_policy
    factory, monkeypatch = setup
    values = synthetic_free_policy()
    values["operations"]["curated_learning"] = dict(values["operations"]["skill_roi"], route_id="free.curated_learning.v1")
    values["execution_budgets"]["monthly_route_minor"]["free.curated_learning.v1"] = 50_000
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(values))
    monkeypatch.setattr(llm_client, "chat_json", lambda _: pytest.fail("Curated guidance cannot generate"))
    with factory() as db:
        data = schemas.LearningStrategyRequest(match_id=1)
        result = asyncio.run(recommendations.match_learning_strategy(data, db, db.get(models.User, 1)))
        assert result["provenance"] == "curated"
        assert db.query(FreeCoreReservation).one().reserved_minor == 20
        assert db.query(models.AnalysisRun).one().committed_units == 0
        assert db.query(CreditReservation).count() == 0 and db.get(models.User, 1).ai_credits == 17
        monkeypatch.delenv("FREE_CORE_POLICY_JSON")
        assert asyncio.run(recommendations.match_learning_strategy(data, db, db.get(models.User, 1))) == result
        assert db.query(FreeCoreReservation).count() == 1


def test_curated_learning_missing_optional_allowance_pauses_without_new_run(setup):
    from backend.app.domains.free_core.models import FreeCoreReservation
    from backend.tests.test_free_core import synthetic_free_policy
    factory, monkeypatch = setup
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(synthetic_free_policy()))
    with factory() as db:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(recommendations.match_learning_strategy(schemas.LearningStrategyRequest(match_id=1), db, db.get(models.User, 1)))
        assert caught.value.status_code == 503
        assert db.query(models.AnalysisRun).count() == db.query(FreeCoreReservation).count() == 0


def test_legacy_credit_downgrade_is_blocked_after_unified_rollout_is_paused(setup):
    from backend.app.services.guardrails import billable_operation
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    provider(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        invoke(db, request, quote(db, request))
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
        with pytest.raises(HTTPException) as caught:
            with billable_operation(db=db, user_id=1, operation="resume_tailor_legacy", amount=2):
                pytest.fail("Legacy generation cannot start")
        assert caught.value.detail["run_created"] is False
        assert db.get(models.User, 1).ai_credits == 17


def test_actual_provider_client_uses_inline_budget_and_usage(setup):
    from types import SimpleNamespace

    from google import genai
    factory, monkeypatch = setup
    config = configure(monkeypatch, "enhanced_qa")
    chosen = config.routes[0].model_copy(update={"model_quotes": (config.routes[0].model_quotes[0].model_copy(update={"model": "gemini-3.6-flash"}),)})
    changed = config.model_copy(update={"routes": (chosen,)})
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
    reviewed_runtime(monkeypatch, changed)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", changed.model_dump_json())
    monkeypatch.setenv("LLM_API_KEY", "synthetic-local-test-only")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    calls = []
    class Models:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=json.dumps(output("enhanced_qa")), response_id="synthetic-provider-id",
                usage_metadata=SimpleNamespace(prompt_token_count=40, candidates_token_count=10, thoughts_token_count=0))
    monkeypatch.setattr(genai, "Client", lambda **kwargs: SimpleNamespace(models=Models()))
    with factory() as db:
        request = payload("enhanced_qa")
        result = invoke(db, request, quote(db, request))
        assert result["credit_execution"]["charged_this_request"] == 2
        assert len(calls) == 1
        liability = db.query(models.ModelCostLiability).one()
        assert liability.cost_state == "settled" and liability.settled_cost_micros > 0
        assert liability.provider == "google"


def test_count_preflight_failure_refunds_unified_service_without_provider_attempt(setup):
    from backend.app.services.model_cost_policy import ModelCostUnavailable
    from google import genai

    factory, monkeypatch = setup
    config = configure(monkeypatch, "enhanced_qa")
    chosen = config.routes[0].model_copy(update={
        "model_quotes": (
            config.routes[0].model_quotes[0].model_copy(update={"model": "gemini-3.6-flash"}),
        ),
    })
    changed = config.model_copy(update={"routes": (chosen,)})
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
    reviewed_runtime(monkeypatch, changed)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", changed.model_dump_json())
    monkeypatch.setenv("LLM_API_KEY", "synthetic-local-test-only")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")

    def unavailable(*_):
        raise ModelCostUnavailable("Gemini token preflight is unavailable")

    monkeypatch.setattr(llm_client, "count_gemini_input", unavailable)
    monkeypatch.setattr(genai, "Client", lambda **_: pytest.fail("Preflight must prevent generation"))
    with factory() as db:
        request = payload("enhanced_qa")
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote(db, request))
        assert caught.value.status_code == 503
        assert caught.value.detail["credits_charged"] == 0
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.query(models.ModelCallEvent).count() == 0
        assert db.query(models.ModelCostLiability).one().settled_cost_micros == 0
        assert db.query(CreditExecutionHold).one().delivery_state == "failed"


def test_expired_route_preflight_sends_no_prompt_or_provider_request(setup):
    from backend.app.domains.credits import execution
    from google import genai

    factory, monkeypatch = setup
    config = configure(monkeypatch, "enhanced_qa")
    chosen = config.routes[0].model_copy(update={
        "model_quotes": (
            config.routes[0].model_quotes[0].model_copy(update={"model": "gemini-3.6-flash"}),
        ),
    })
    changed = config.model_copy(update={"routes": (chosen,)})
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
    reviewed_runtime(monkeypatch, changed)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", changed.model_dump_json())
    monkeypatch.setenv("LLM_API_KEY", "synthetic-local-test-only")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    original_require_route = execution.require_route

    def now_expired(selected, policy, *, now):
        return original_require_route(selected, policy, now=selected.qualified_until)

    monkeypatch.setattr(execution, "require_route", now_expired)
    monkeypatch.setattr(llm_client, "count_gemini_input", lambda *_: pytest.fail("No prompt may leave an expired route"))
    monkeypatch.setattr(genai, "Client", lambda **_: pytest.fail("No provider generation after route expiry"))
    with factory() as db:
        request = payload("enhanced_qa")
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote(db, request))
        assert caught.value.status_code == 503
        assert caught.value.detail["credits_charged"] == 0
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.query(models.ModelCallEvent).count() == 0
        assert db.query(CreditExecutionHold).one().delivery_state == "failed"


def test_unified_upload_preserves_original_without_unquoted_enrichment(setup, synthetic_document_inspector):
    import io

    from docx import Document
    from starlette.datastructures import Headers, UploadFile
    from starlette.requests import Request
    factory, monkeypatch = setup
    monkeypatch.setattr(llm_client, "chat_json", lambda _: pytest.fail("Upload cannot generate without review"))
    doc = Document()
    doc.add_paragraph("Candidate with Python and Docker skills")
    stream = io.BytesIO()
    doc.save(stream)
    original = stream.getvalue()
    request = Request({"type": "http", "method": "POST", "path": "/resume/parse", "headers": [], "client": ("127.0.0.1", 1)})
    upload = UploadFile(io.BytesIO(original), filename="resume.docx", headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}))
    with factory() as db:
        result = asyncio.run(resume.parse_resume.__wrapped__(request, upload, True, db, db.get(models.User, 1)))
        assert result.enrichment_state == "requires_review" and result.enrichment_units == 0
        assert db.get(models.Resume, result.resume_id).source_document == original
        assert db.query(CreditReservation).count() == db.query(models.AnalysisRun).count() == 0


def test_complete_prompt_refused_before_quote_when_provider_envelope_too_small(setup):
    factory, monkeypatch = setup
    config = configure(monkeypatch, "enhanced_qa")
    chosen = config.routes[0].model_copy(update={"model_quotes": (config.routes[0].model_quotes[0].model_copy(update={"max_input_tokens": 1}),)})
    changed = config.model_copy(update={"routes": (chosen,)})
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
    reviewed_runtime(monkeypatch, changed)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", changed.model_dump_json())
    with factory() as db:
        with pytest.raises(HTTPException) as caught:
            synchronous.prepare_quote_context(db, user_id=1, payload=payload("enhanced_qa"))
        assert caught.value.detail["run_created"] is False
        assert db.query(CreditReservation).count() == 0


def test_configured_model_change_invalidates_quote_before_provider_call(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        monkeypatch.setattr(llm_client, "LLM_MODEL", "different-reviewed-model")
        with pytest.raises(HTTPException):
            invoke(db, request, quote_id)
        assert not calls and db.query(CreditReservation).count() == 0


@pytest.mark.parametrize("change", ["numbers", "unrelated_evidence"])
def test_bullet_personal_claims_require_original_numbers_and_matching_approved_fact(setup, change):
    factory, monkeypatch = setup
    configure(monkeypatch, "bullet_rewrite")
    raw = output("bullet_rewrite")
    if change == "numbers":
        raw["bullets"][0]["replacement_text"] = "Developed 200 Python APIs and Docker services."
    with factory() as db:
        if change == "unrelated_evidence":
            db.get(models.EvidenceItem, "evidence-1").evidence_text = "Completed Docker training."
            db.commit()
        calls = provider(monkeypatch, "bullet_rewrite", raw=raw)
        request = payload("bullet_rewrite")
        with pytest.raises(HTTPException) as caught:
            invoke(db, request, quote(db, request))
        assert caught.value.detail["credits_charged"] == 0 and len(calls) == 1
        assert service.wallet(db, user_id=1).available_credits == 40


def test_exact_accepted_key_recovers_original_result_after_evidence_changed(setup):
    factory, monkeypatch = setup
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa")
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        first = invoke(db, request, quote_id)
        db.get(models.EvidenceItem, "evidence-1").evidence_text = "Changed later, after completion."
        db.commit()
        result = synchronous.lookup_result(db, user_id=1, payload=request, idempotency_key="execute-sync-test")
        assert result["result"]["answer"] == first["answer"]
        assert synchronous.lookup_result(db, user_id=1, payload=request)["reused_run"] is None
        repeated = invoke(db, request, quote_id)
        assert repeated["credit_execution"]["charged_this_request"] == 0
        assert len(calls) == 1


def test_already_accepted_public_work_keeps_frozen_authority_when_canary_pointer_is_cleared(setup):
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
    from backend.tests.test_canary_admission import prepare

    factory, monkeypatch = setup
    config = configure(monkeypatch, "enhanced_qa")
    with factory() as db:
        prepare(db, monkeypatch, config=config)
        db.commit()
    reviewed_runtime(monkeypatch, config)
    calls = provider(monkeypatch, "enhanced_qa")
    transport = llm_client._chat_with_budget
    def paused_after_acceptance(messages, budget):
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary")
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_ID")
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256")
        return transport(messages, budget)
    monkeypatch.setattr(llm_client, "_chat_with_budget", paused_after_acceptance)
    with factory() as db:
        request = payload("enhanced_qa")
        quote_id = quote(db, request)
        result = invoke(db, request, quote_id)
        assert result["credit_execution"]["charged_this_request"] == 2 and len(calls) == 1
        hold = db.query(CreditExecutionHold).one()
        reservation = db.query(CreditReservation).one()
        assert hold.snapshot["public_admission"]["source_id"] == reservation.source_id
        assert hold.delivery_state == "delivered"
        assert service.wallet(db, user_id=1).available_credits == 38
        assert invoke(db, request, quote_id)["credit_execution"]["reused"]
        assert len(calls) == 1
