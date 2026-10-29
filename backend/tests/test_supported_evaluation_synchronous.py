"""Synthetic local adapters and persistent accounting; no live qualification.

Provider/native authority is replaced explicitly in adapter unit tests. Separate
core/owned-PostgreSQL tests cover its physical identity and process-only scope.
These fixtures never pretend to be reviewed resources or observed invoices.
"""
from __future__ import annotations

import copy
import json
import os
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.domains.analysis import supported_evaluation as core
from app.domains.analysis import supported_evaluation_synchronous as adapter
from app.domains.credits import synchronous
from app.services import generation_budget, llm_client, model_cost_policy, synchronous_contracts
from tests.expense_policy_fixtures import synthetic_expense_policy
from tests.model_cost_fixtures import synthetic_policy

OPERATIONS = sorted(adapter.OPERATIONS)
SOURCE = "Built 2 Python APIs and Docker services."


def resource_review(*, expired=False):
    now = datetime.now(UTC)
    return json.dumps({"checked_at": (now - timedelta(days=2)).isoformat(),
        "expires_at": (now - timedelta(days=1) if expired else now + timedelta(days=1)).isoformat(),
        "urls": {"https://docs.docker.com/get-started/": "a" * 64}})


@pytest.fixture
def local(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", resource_review())
    # Explicit catalog-operation ceilings, never permission from a legacy alias.
    policy = synthetic_policy()
    expense = synthetic_expense_policy()
    for operation in OPERATIONS:
        policy["operation_limits_micros"][operation] = 1_000_000
        expense["operation_model_ceilings_micros"][operation] = 1_000_000
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(policy))
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(expense))
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
    monkeypatch.setattr(llm_client, "LLM_API_BASE", "https://generativelanguage.googleapis.com")
    monkeypatch.setattr(llm_client, "_api_key", lambda: "synthetic-never-network")
    # Only the projection is replaced in these adapter unit tests. The core
    # integration separately validates the complete authorization, native DB,
    # source corpus, selected side and funding before calling this helper.
    def declared(run):
        metadata = run.input_payload["_quality_evaluation"]
        quote = metadata["authorization"]["routes"][metadata["side"]]["price"]
        return {"model": quote["model"], "api_base": quote["api_base"],
                "fallback_models": list(llm_client.GEMINI_FALLBACK_MODELS)}
    monkeypatch.setattr(adapter, "_generation_configuration", declared)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as db:
        db.add(models.User(id=1, email="synthetic-sync@example.invalid", password_hash="", tier="free", ai_credits=0, job_service_credits=0))
        db.flush()
        db.add(models.Resume(id=1, user_id=1, raw_text=SOURCE, skills=["Python"], experience_years=1,
            sections={"Experience": SOURCE}, source_format="pdf", source_document=b"synthetic-test-not-native"))
        db.flush()
        db.add(models.EvidenceItem(id="proof", user_id=1, resume_id=1, category="synthetic", title="proof",
            evidence_text=SOURCE, approval_state="approved", skills=[], metrics={}, provenance="synthetic_fixture"))
        db.add(models.Opportunity(id="op-sync", user_id=1, resume_id=1, title="Backend engineer", company="Synthetic evaluation",
            job_description="Build reliable Python APIs and Docker services.", source="internal_evaluation", job_snapshot={"immutable": True}))
        db.commit()
    try:
        yield factory
    finally:
        engine.dispose()


def seed(db, operation, *, side="candidate", prices=None):
    selected = next(q for q in model_cost_policy.current_policy().pricing_quotes if q.model == llm_client.LLM_MODEL)
    prices = prices or {name: selected.model_dump(mode="json") for name in ("reference", "candidate")}
    run = models.AnalysisRun(id="qev2_sync_" + operation + "_" + side, user_id=1, operation=operation,
        opportunity_id="op-sync", idempotency_key="sync-eval-" + operation + "_" + side, input_fingerprint="pending",
        status="running", usage_state="waived", estimated_units=0, committed_units=0,
        generation_attempt_limit=1, input_payload={"schema_version": core.VERSION,
            "mode": "enhanced", "num_questions": 8, "resume_id": 1, "opportunity_id": "op-sync",
            "_quality_evaluation": {"side": side, "authorization": {"routes": {
                name: {"price": price} for name, price in prices.items()}}}})
    adapter.prepare_inputs(db, run)
    run.input_fingerprint = adapter.fingerprint(db, run)
    model_cost_policy.freeze_run_quote(run)
    run.model_cost_quote = dict(run.model_cost_quote) | {"pricing_quotes": [prices[side]]}
    db.add(run)
    db.commit()
    return run


def output(operation):
    return {
        "resume_enrichment": {"skills": ["Python", "Docker", "UnclaimedSkill"]},
        "bullet_rewrite": {"bullets": [{"original_text": SOURCE, "replacement_text": "Developed 2 Python APIs and Docker services.", "evidence_ids": ["proof"]}], "summary": "Clearer action verb."},
        "enhanced_qa": {"answer": "Use the approved API work as a starting point and explain the trade-offs actually considered.", "confidence": "medium", "sources": ["proof"], "suggested_followups": []},
        "learning_strategy": {"readiness_summary": "Focus on Docker evidence for this role.", "missing_hiring_signals": [{"signal": "Docker", "why_it_matters": "Role requirement", "severity": "high"}],
            "learning_priorities": [{"skill": "docker", "priority": "high", "current_status": "true_gap", "reason": "Saved gap", "expected_outcome": "Working container"}],
            "project_recommendations": [{"title": "Container project", "covers_gaps": ["Docker"], "description": "Build a container", "implementation_steps": ["Write Dockerfile", "Run tests"], "resume_bullets": [], "interview_talking_points": ["Explain image size"]}],
            "timeline": [{"phase": "Week 1", "focus": "Docker", "deliverable": "Container"}]},
    }[operation]


def synthetic_transport(monkeypatch, operation, *, raw=None, unknown=False, callback=None):
    calls = []

    def transport(messages, budget):
        budget.preflight("google", llm_client.LLM_MODEL, messages, llm_client.LLM_API_BASE)
        record = budget.admit("google", llm_client.LLM_MODEL, messages, api_base=llm_client.LLM_API_BASE)
        calls.append(messages)
        if callback:
            callback()
        if unknown:
            failure = RuntimeError("synthetic-unknown-no-provider-body")
            budget.finish(record, latency_ms=1, error=failure)
            raise failure
        budget.finish(record, latency_ms=1, response=SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=80, thoughts_token_count=20, total_token_count=200)))
        return json.dumps(output(operation) if raw is None else raw)

    monkeypatch.setattr(llm_client, "_chat_with_budget", transport)
    return calls


@pytest.mark.parametrize("operation", OPERATIONS)
def test_actual_shared_complete_executor_keeps_zero_rights_and_supplier_accounting(local, monkeypatch, operation):
    calls = synthetic_transport(monkeypatch, operation)
    # This test exercises the adapter/budget, not the independently tested native
    # process authorization. It does not represent production permission.
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    with local() as db:
        run = seed(db, operation)
        original = copy.deepcopy(run.input_payload)
        before = (db.get(models.Resume, 1).source_document, db.get(models.Resume, 1).raw_text, db.get(models.Resume, 1).skills)
        with generation_budget.persistent_run_budget(local, run.id):
            result = adapter.execute(db, run)
        assert len(calls) == 1
        context = json.loads(calls[0][-1]["content"].split("COMPLETE SOURCE DATA:\n", 1)[1])
        assert context["resume"]["raw_text"] == SOURCE
        assert context["approved_evidence"][0]["id"] == "proof"
        assert run.input_payload == original
        assert before == (db.get(models.Resume, 1).source_document, db.get(models.Resume, 1).raw_text, db.get(models.Resume, 1).skills)
        assert db.get(models.User, 1).ai_credits == db.get(models.User, 1).job_service_credits == 0
        event = db.query(models.ModelCallEvent).one()
        assert event.cost_state == "settled" and event.prompt_version == "sync-evidence-v2"
        assert db.query(models.ModelCostLiability).one().settled_cost_micros == 200
        assert result.get("credit_execution") is None
        if operation in {"enhanced_qa", "learning_strategy"}:
            match = db.query(models.JobMatch).one()
            assert match.dimension_scores == [] and match.skill_verification_rate == 0
            assert run.input_payload["synchronous_input"]["basic_scoring_version"] == "basic-catalog-overlap-v1"
        if operation == "learning_strategy":
            assert result["learning_priorities"][0]["resources"][0]["url"] == "https://docs.docker.com/get-started/"
        if operation == "resume_enrichment":
            assert "Docker" in result["skills"] and "UnclaimedSkill" not in result["skills"]


@pytest.mark.parametrize("operation", OPERATIONS)
def test_stored_operation_label_without_real_core_authority_never_calls(local, monkeypatch, operation):
    calls = synthetic_transport(monkeypatch, operation)
    with local() as db:
        run = seed(db, operation)
        with pytest.raises(ValueError, match="validated v2 runner"):
            adapter.execute(db, run)
    assert not calls


@pytest.mark.parametrize("kind", ["absent", "ephemeral", "different_run"])
def test_adapter_requires_its_exact_persistent_budget_even_with_test_scope(local, monkeypatch, kind):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    with local() as db:
        run = seed(db, "bullet_rewrite")
        budget = None if kind == "absent" else generation_budget.GenerationBudget(run_id=run.id if kind == "ephemeral" else "other", before=lambda *_: None, after=lambda *_: None, preflight=lambda *_: 1)
        if kind == "ephemeral":
            budget.before = None
        monkeypatch.setattr(generation_budget, "current_budget", lambda: budget)
        with pytest.raises(ValueError, match="exact persistent"):
            adapter.execute(db, run)


@pytest.mark.parametrize("review", ["", "{}", "not json", resource_review(expired=True)])
def test_learning_without_genuinely_fresh_review_refuses_before_generation(local, monkeypatch, review):
    monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", review)
    calls = synthetic_transport(monkeypatch, "learning_strategy")
    with local() as db:
        with pytest.raises(synchronous_contracts.SynchronousOutputUnavailable, match="current review"):
            seed(db, "learning_strategy")
        assert db.query(models.ModelCallEvent).count() == 0
        assert db.query(models.ModelCostLiability).count() == 0
    assert not calls


@pytest.mark.parametrize("change", ["source", "evidence", "job", "match", "question", "binding", "resource_review"])
def test_complete_input_drift_refuses_before_dispatch(local, monkeypatch, change):
    with local() as db:
        run = seed(db, "learning_strategy" if change == "resource_review" else "enhanced_qa")
        if change == "source":
            db.get(models.Resume, 1).raw_text += " Added unreviewed source."
        elif change == "evidence":
            db.get(models.EvidenceItem, "proof").evidence_text += " New assertion."
        elif change == "job":
            db.get(models.Opportunity, "op-sync").job_description += " New role requirement."
        elif change == "match":
            db.query(models.JobMatch).one().match_score = 99
        elif change == "question":
            value = copy.deepcopy(run.input_payload)
            value["synchronous_input"]["request"]["input"]["question"] = "An unreviewed question?"
            run.input_payload = value
        elif change == "binding":
            value = copy.deepcopy(run.input_payload)
            value["synchronous_input"]["unexpected_authority"] = True
            run.input_payload = value
        else:
            monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", resource_review())
        db.commit()
        with pytest.raises(ValueError, match="changed"):
            adapter.immutable_inputs_current(db, run)


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("unknown", [False, True])
def test_invalid_or_unknown_delivery_keeps_actual_supplier_obligation(local, monkeypatch, operation, unknown):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    calls = synthetic_transport(monkeypatch, operation, raw={}, unknown=unknown)
    with local() as db:
        run = seed(db, operation)
        with generation_budget.persistent_run_budget(local, run.id), pytest.raises((RuntimeError, ValueError)):
            adapter.execute(db, run)
        assert len(calls) == 1
        event = db.query(models.ModelCallEvent).one()
        liability = db.query(models.ModelCostLiability).one()
        assert event.cost_state == liability.cost_state == ("outcome_unknown" if unknown else "settled")
        assert liability.settled_cost_micros == (None if unknown else 200)


def test_repreparing_does_not_create_new_basic_dependency_or_replace_binding(local):
    with local() as db:
        run = seed(db, "enhanced_qa")
        before = copy.deepcopy(run.input_payload)
        adapter.prepare_inputs(db, run)
        assert run.input_payload == before
        assert db.query(models.JobMatch).count() == 1


def test_no_supported_claims_or_numbers_are_weakened_by_adapter(local, monkeypatch):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    raw = output("bullet_rewrite")
    raw["bullets"][0]["replacement_text"] = "Developed 20 Python APIs and Docker services."
    synthetic_transport(monkeypatch, "bullet_rewrite", raw=raw)
    with local() as db:
        run = seed(db, "bullet_rewrite")
        with generation_budget.persistent_run_budget(local, run.id), pytest.raises(synchronous_contracts.SynchronousOutputUnavailable):
            adapter.execute(db, run)


def test_learning_cannot_turn_future_work_into_completed_personal_claim(local, monkeypatch):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    raw = output("learning_strategy")
    raw["project_recommendations"][0]["resume_bullets"] = ["Built 5 Kubernetes clusters."]
    synthetic_transport(monkeypatch, "learning_strategy", raw=raw)
    with local() as db:
        run = seed(db, "learning_strategy")
        with generation_budget.persistent_run_budget(local, run.id), pytest.raises(synchronous_contracts.SynchronousOutputUnavailable):
            adapter.execute(db, run)


def test_resource_expiry_during_provider_work_prevents_delivery(local, monkeypatch):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    synthetic_transport(monkeypatch, "learning_strategy", callback=lambda: monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", resource_review(expired=True)))
    with local() as db:
        run = seed(db, "learning_strategy")
        with generation_budget.persistent_run_budget(local, run.id), pytest.raises((ValueError, synchronous_contracts.SynchronousOutputUnavailable)):
            adapter.execute(db, run)
        assert db.query(models.ModelCostLiability).one().cost_state == "settled"


def test_unknown_attempt_cannot_be_replayed_through_a_fresh_budget_context(local, monkeypatch):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    calls = synthetic_transport(monkeypatch, "enhanced_qa", unknown=True)
    with local() as db:
        run = seed(db, "enhanced_qa")
        with generation_budget.persistent_run_budget(local, run.id), pytest.raises(RuntimeError):
            adapter.execute(db, run)
        with generation_budget.persistent_run_budget(local, run.id), pytest.raises(RuntimeError, match="exhausted"):
            adapter.execute(db, run)
        assert len(calls) == db.query(models.ModelCostLiability).count() == 1
        assert db.query(models.ModelCostLiability).one().cost_state == "outcome_unknown"


def test_complete_prompt_is_retained_or_refused_without_shortening(local):
    with local() as db:
        source = SOURCE + "\n" + "Complete untrusted data unit. " * 500
        db.get(models.Resume, 1).raw_text = source
        db.commit()
        run = seed(db, "enhanced_qa")
        _, prepared = adapter._prepared(db, run)
        context = json.loads(prepared.messages[-1]["content"].split("COMPLETE SOURCE DATA:\n", 1)[1])
        assert context["resume"]["raw_text"] == source
        db.get(models.Resume, 1).raw_text = SOURCE + "\n" + "Long complete source unit. " * 5000
        db.commit()
        # A fresh preparation may refuse an unsupported complete context; it
        # must never produce an apparently successful truncated prompt.
        other = models.AnalysisRun(user_id=1, operation="enhanced_qa", opportunity_id="op-sync",
            input_payload={"resume_id": 1, "opportunity_id": "op-sync", "_quality_evaluation": run.input_payload["_quality_evaluation"]})
        with pytest.raises(HTTPException, match="complete context exceeds"):
            adapter.prepare_inputs(db, other)


@pytest.mark.parametrize("operation", ["bullet_rewrite", "enhanced_qa"])
def test_legacy_alias_cost_ceiling_does_not_fund_new_catalog_operation(local, monkeypatch, operation):
    policy = json.loads(os.environ["LLM_MODEL_COST_POLICY_JSON"])
    del policy["operation_limits_micros"][operation]
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(policy))
    with local() as db, pytest.raises(model_cost_policy.ModelCostUnavailable, match="no cost ceiling"):
        seed(db, operation)


def test_both_declared_model_sides_prepare_without_rewriting_actual_client(local, monkeypatch):
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    policy = json.loads(os.environ["LLM_MODEL_COST_POLICY_JSON"])
    reference = next(q for q in policy["pricing_quotes"] if q["model"] == llm_client.LLM_MODEL)
    candidate = next(q for q in policy["pricing_quotes"] if q["provider"] == "google" and q["model"] != reference["model"])
    candidate["input_rate_micros_per_million"], candidate["output_rate_micros_per_million"] = 2_000_000, 3_000_000
    monkeypatch.setenv("LLM_MODEL_COST_POLICY_JSON", json.dumps(policy))
    prices = {"reference": reference, "candidate": candidate}
    calls = synthetic_transport(monkeypatch, "enhanced_qa")
    with local() as db:
        runs = {side: seed(db, "enhanced_qa", side=side, prices=prices) for side in prices}
        assert llm_client.LLM_MODEL == reference["model"]
        contexts = {side: adapter._prepared(db, run)[1].context for side, run in runs.items()}
        assert contexts["reference"]["generation_configuration"]["model"] == reference["model"]
        assert contexts["candidate"]["generation_configuration"]["model"] == candidate["model"]
        with generation_budget.persistent_run_budget(local, runs["reference"].id):
            assert adapter.execute(db, runs["reference"])["answer"]
        with generation_budget.persistent_run_budget(local, runs["candidate"].id), pytest.raises(ValueError, match="actual model client"):
            adapter.execute(db, runs["candidate"])
        assert len(calls) == 1
        monkeypatch.setattr(llm_client, "LLM_MODEL", candidate["model"])
        with generation_budget.persistent_run_budget(local, runs["candidate"].id):
            assert adapter.execute(db, runs["candidate"])["answer"]
        events = {event.analysis_run_id: event for event in db.query(models.ModelCallEvent)}
        assert events[runs["reference"].id].model == reference["model"]
        assert events[runs["reference"].id].estimated_cost_micros == 200
        assert events[runs["candidate"].id].model == candidate["model"]
        assert events[runs["candidate"].id].estimated_cost_micros == 500
        assert len(calls) == 2
        retail = synchronous.owned_context(db, user_id=1, payload=synchronous.SynchronousServiceRequest(
            operation="resume_enrichment", input={"resume_id": 1}))
        assert retail.context["generation_configuration"]["model"] == candidate["model"]


def test_actual_sync_adapter_redacts_complete_structured_source_without_corrupting_json(local, monkeypatch):
    from app.services.prompt_privacy import REDACTED, redact_prompt_text

    source = SOURCE + '\nprivate@example.invalid\nNotes: "quoted" C:\\portfolio\\draft\nAge: 33\nKeep the final source sentinel.'
    with local() as db:
        resume = db.get(models.Resume, 1)
        resume.raw_text, resume.sections = source, {"Experience": source}
        db.commit()
    calls = synthetic_transport(monkeypatch, "enhanced_qa")
    monkeypatch.setattr(core, "require_provider_authority", lambda *_: None)
    with local() as db:
        run = seed(db, "enhanced_qa")
        with generation_budget.persistent_run_budget(local, run.id):
            adapter.execute(db, run)
        assert db.get(models.Resume, 1).raw_text == source
    context = json.loads(calls[0][-1]["content"].split("COMPLETE SOURCE DATA:\n", 1)[1])
    assert context["resume"]["raw_text"] == redact_prompt_text(source)
    assert "private@example.invalid" not in context["resume"]["raw_text"]
    assert "33" not in context["resume"]["raw_text"] and REDACTED in context["resume"]["raw_text"]
    assert '"quoted" C:\\portfolio\\draft' in context["resume"]["raw_text"]
    assert "final source sentinel" in context["resume"]["raw_text"]
    assert context["approved_evidence"][0]["text"] == SOURCE
