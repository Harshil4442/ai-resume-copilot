"""The actual analysis admission/worker/settlement uses one funded credit wallet."""

import pytest
from backend.app import models
from backend.app.domains.analysis import schemas, tasks
from backend.app.domains.analysis import service as analysis_service
from backend.app.domains.credits import analysis, finance, runtime, service, service_quotes
from backend.app.domains.credits.models import CreditExecutionHold, CreditReservation
from backend.app.services.generation_budget import current_budget
from backend.tests.test_analysis_runs import _native_source
from backend.tests.test_unified_credits import factory, grant
from backend.tests.test_unified_execution import budgets, financial_policy, response, route
from fastapi import HTTPException

assert factory


@pytest.fixture
def setup(factory, monkeypatch):
    selected = route(attempts=1).model_copy(
        update={
            "operation": "enhanced_job_match",
            "credits": 3,
            "supported_limits": {
                "context_bytes": 100_000,
                "source_bytes": 100_000,
                "evidence_items": 100,
            },
        }
    )
    config = runtime.RuntimeConfig(policy=financial_policy(), budgets=budgets(), routes=(selected,))
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "1")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    with factory() as db:
        grant(db)
        resume = models.Resume(
            id=1,
            user_id=1,
            raw_text="Experienced Python developer",
            skills=["Python"],
            sections={},
            contact_info={},
        )
        db.add(resume)
        db.commit()
    return factory, config


def request():
    return schemas.AnalysisRunCreate(
        operation="job_match",
        input={
            "resume_id": 1,
            "mode": "enhanced",
            "job_description": "Build reliable Python backend APIs and services",
        },
        idempotency_key="unified-analysis-test",
    )


def quote_and_create(db):
    payload = request()
    _, fingerprint, config, selected = analysis.prepare_quote_context(
        db, user_id=1, payload=payload
    )
    q = service_quotes.issue_service_quote(
        db,
        user_id=1,
        input_fingerprint=fingerprint,
        idempotency_key="quote-analysis-request",
        route=selected,
        policy=config.policy,
    )
    db.commit()
    accepted = payload.model_copy(update={"accepted_service_quote_id": q.id})
    return analysis_service.create_run(db, user_id=1, payload=accepted, header_idempotency_key=None)


def test_canary_requires_new_review_and_does_not_fall_back_to_legacy(setup):
    factory, _ = setup
    with factory() as db:
        with pytest.raises(HTTPException) as caught:
            analysis_service.create_run(
                db, user_id=1, payload=request(), header_idempotency_key=None
            )
        assert caught.value.status_code == 409
        assert db.query(CreditReservation).count() == 0
        assert db.get(models.User, 1).ai_credits == 17


def test_actual_worker_uses_unified_budget_and_commits_once(setup, monkeypatch):
    factory, _ = setup
    with factory() as db:
        run, created = quote_and_create(db)
        assert created and run.estimated_units == 3
        assert schemas.AnalysisRunResponse.model_validate(run).billing_currency == "credits"
        identity = run.id
        assert db.get(models.User, 1).ai_credits == 17
        assert service.wallet(db, user_id=1).available_credits == 37
    calls = []

    def execute(db, run):
        budget = current_budget()
        record = budget.admit(
            "google",
            "synthetic-model",
            [{"role": "user", "content": "Synthetic test."}],
            api_base="https://generativelanguage.googleapis.com",
        )
        calls.append(record)
        budget.finish(record, latency_ms=1, response=response())
        return {"synthetic_validated_result": True}

    monkeypatch.setattr(tasks, "execute_operation", execute)
    assert tasks.process_analysis_run(identity) == "succeeded"
    assert tasks.process_analysis_run(identity) == "succeeded"
    with factory() as db:
        run = db.get(models.AnalysisRun, identity)
        assert run.usage_state == "committed" and run.committed_units == 3
        assert run.generation_attempt_count == 1
        assert db.get(models.User, 1).ai_credits == 17
        assert service.wallet(db, user_id=1).available_credits == 37
        assert len(calls) == 1
        hold = db.query(CreditExecutionHold).one()
        assert hold.delivery_state == "delivered" and hold.state == "outcome_unknown"
        assert hold.native_settled_minor is None


def test_unknown_provider_failure_refunds_without_worker_retry(setup, monkeypatch):
    factory, _ = setup
    with factory() as db:
        run, _ = quote_and_create(db)
        identity = run.id

    def execute(db, run):
        budget = current_budget()
        record = budget.admit(
            "google",
            "synthetic-model",
            [{"role": "user", "content": "Synthetic test."}],
            api_base="https://generativelanguage.googleapis.com",
        )
        budget.finish(record, latency_ms=1, error=TimeoutError())
        raise TimeoutError("Synthetic provider timeout")

    monkeypatch.setattr(tasks, "execute_operation", execute)
    assert tasks.process_analysis_run(identity) == "failed"
    with factory() as db:
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.get(models.User, 1).ai_credits == 17
        assert db.query(CreditExecutionHold).one().state == "outcome_unknown"
        assert db.query(models.ModelCostLiability).one().cost_state == "outcome_unknown"


def test_queued_cancel_returns_only_unified_credits(setup):
    factory, _ = setup
    with factory() as db:
        run, _ = quote_and_create(db)
        analysis_service.cancel_run(db, 1, run.id)
        assert run.status == "cancelled"
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.get(models.User, 1).ai_credits == 17
        assert db.query(models.ModelCostLiability).one().settled_cost_micros == 0


def test_runtime_never_infers_a_canary_or_qualification(monkeypatch):
    monkeypatch.delenv("UNIFIED_CREDIT_EXECUTION_ENABLED", raising=False)
    with pytest.raises(finance.FundingUnavailable):
        runtime.current_runtime(1)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "1")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", "{}")
    with pytest.raises(finance.FundingUnavailable):
        runtime.current_runtime(1)


def test_changed_resume_after_quote_requires_new_review(setup):
    factory, _ = setup
    with factory() as db:
        payload = request()
        _, fingerprint, config, selected = analysis.prepare_quote_context(
            db, user_id=1, payload=payload
        )
        q = service_quotes.issue_service_quote(
            db,
            user_id=1,
            input_fingerprint=fingerprint,
            idempotency_key="quote-before-change",
            route=selected,
            policy=config.policy,
        )
        db.commit()
        db.get(models.Resume, 1).raw_text = "Newly approved experience changes the source"
        db.commit()
        with pytest.raises(HTTPException) as caught:
            analysis_service.create_run(
                db,
                user_id=1,
                payload=payload.model_copy(update={"accepted_service_quote_id": q.id}),
                header_idempotency_key=None,
            )
        assert caught.value.status_code == 409
        assert db.query(models.AnalysisRun).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40


def test_crashed_worker_cannot_repeat_unfunded_native_execution(setup, monkeypatch):
    factory, _ = setup
    with factory() as db:
        run, _ = quote_and_create(db)
        identity = run.id
    # Simulate the committed start followed by a worker crash before response.
    with analysis.analysis_budget(factory, identity):
        pass
    calls = []
    monkeypatch.setattr(tasks, "execute_operation", lambda *_: calls.append("must-not-run"))
    assert tasks.process_analysis_run(identity) == "failed"
    assert calls == []
    with factory() as db:
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.query(CreditExecutionHold).one().state == "outcome_unknown"


def test_changed_input_after_admission_does_not_start_worker(setup, monkeypatch):
    factory, _ = setup
    with factory() as db:
        run, _ = quote_and_create(db)
        identity = run.id
        db.get(models.Resume, 1).raw_text = "Changed after admission"
        db.commit()
    calls = []
    monkeypatch.setattr(tasks, "execute_operation", lambda *_: calls.append("must-not-run"))
    assert tasks.process_analysis_run(identity) == "failed"
    assert calls == []
    with factory() as db:
        assert service.wallet(db, user_id=1).available_credits == 40
        assert all(
            row.settled_cost_micros == 0 for row in db.query(models.ModelCostLiability).all()
        )


def test_tailoring_change_between_budget_check_and_generation_refunds(setup, monkeypatch):
    factory, _ = setup
    selected = route(attempts=1).model_copy(update={
        "supported_source_formats": ("docx",),
        "supported_limits": {
            "context_bytes": 100_000,
            "source_bytes": 100_000,
            "evidence_items": 100,
        },
        "model_quotes": tuple(
            price.model_copy(update={"max_input_tokens": 100_000})
            for price in route(attempts=1).model_quotes
        ),
    })
    config = runtime.RuntimeConfig(
        policy=financial_policy(), budgets=budgets(), routes=(selected,),
    )
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    with factory() as db:
        resume = db.get(models.Resume, 1)
        resume.source_document = _native_source()
        resume.source_format = "docx"
        db.add_all([
            models.Opportunity(
                id="opp_tailor_worker_race", user_id=1, resume_id=1,
                title="Platform Engineer", company="Example Co",
                job_description="Build reliable Python services for global customers.",
                job_snapshot={},
            ),
            models.EvidenceItem(
                id="ev_tailor_worker_race", user_id=1, resume_id=1,
                category="experience", title="Platform work",
                evidence_text="Developed reliable Python services for customers.",
                skills=["Python"], metrics={}, approval_state="approved",
            ),
        ])
        db.commit()
        payload = schemas.AnalysisRunCreate(
            operation="resume_tailor", opportunity_id="opp_tailor_worker_race",
            input={"tailoring_scope": "standard"},
            idempotency_key="tailor-worker-race-001",
        )
        _, fingerprint, _, route_for_quote = analysis.prepare_quote_context(
            db, user_id=1, payload=payload,
        )
        quote = service_quotes.issue_service_quote(
            db, user_id=1, input_fingerprint=fingerprint,
            idempotency_key="quote-tailor-worker-race", route=route_for_quote,
            policy=config.policy,
        )
        db.commit()
        run, created = analysis_service.create_run(
            db, user_id=1,
            payload=payload.model_copy(update={"accepted_service_quote_id": quote.id}),
            header_idempotency_key=None,
        )
        assert created and run.estimated_units == 8
        run_id = run.id
        assert service.wallet(db, user_id=1).available_credits == 32

    original = tasks.execute_operation

    def change_after_budget(db, run):
        db.get(models.Opportunity, "opp_tailor_worker_race").job_description += " Changed after budget check."
        db.commit()
        return original(db, run)

    monkeypatch.setattr(tasks, "execute_operation", change_after_budget)
    assert tasks.process_analysis_run(run_id) == "failed"
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert run.committed_units == 0 and run.usage_state == "released"
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.query(models.ResumeVersion).count() == 0
        assert all(
            row.settled_cost_micros == 0 for row in db.query(models.ModelCostLiability).all()
        )


def test_public_audience_requires_release_bound_to_exact_runtime(setup, monkeypatch):
    from types import SimpleNamespace

    from backend.app.domains.common import payload_fingerprint
    from backend.app.domains.credits import release

    _, config = setup
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "reviewed_public")
    monkeypatch.setattr(
        release,
        "current_review",
        lambda: SimpleNamespace(policy=config.policy, evidence={"delivery_routes": "0" * 64}),
    )
    assert not runtime.enabled_for(2000)
    digest = payload_fingerprint(config.model_dump(mode="json"))
    monkeypatch.setattr(
        release,
        "current_review",
        lambda: SimpleNamespace(policy=config.policy, evidence={"delivery_routes": digest}),
    )
    assert runtime.enabled_for(2000)
    altered = config.model_copy(
        update={"budgets": config.budgets.model_copy(update={"daily_global_minor": 9999})}
    )
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", altered.model_dump_json())
    assert not runtime.enabled_for(2000)


def test_expired_quote_is_definitively_rejected_before_any_run(setup):
    from datetime import UTC, datetime, timedelta

    factory, _ = setup
    with factory() as db:
        payload = request()
        _, fingerprint, config, selected = analysis.prepare_quote_context(
            db, user_id=1, payload=payload
        )
        q = service_quotes.issue_service_quote(
            db,
            user_id=1,
            input_fingerprint=fingerprint,
            idempotency_key="expired-service-quote",
            route=selected,
            policy=config.policy,
            now=datetime.now(UTC) - timedelta(minutes=16),
        )
        db.commit()
        with pytest.raises(HTTPException) as caught:
            analysis_service.create_run(
                db,
                user_id=1,
                payload=payload.model_copy(update={"accepted_service_quote_id": q.id}),
                header_idempotency_key=None,
            )
        assert caught.value.status_code == 409
        assert caught.value.detail["code"] == "credit_service_quote_rejected"
        assert caught.value.detail["run_created"] is False
        assert db.query(models.AnalysisRun).count() == db.query(CreditReservation).count() == 0


def test_used_quote_never_claims_no_run_was_created(setup):
    from backend.app.domains.credits.models import CreditServiceQuote

    factory, _ = setup
    with factory() as db:
        original, _ = quote_and_create(db)
        q = db.query(CreditServiceQuote).one()
        original.status = "failed"
        db.commit()
        payload = request().model_copy(
            update={
                "idempotency_key": "different-client-intent",
                "accepted_service_quote_id": q.id,
                "input": request().input | {"force_new": True},
            }
        )
        with pytest.raises(HTTPException) as caught:
            analysis_service.create_run(db, user_id=1, payload=payload, header_idempotency_key=None)
        assert caught.value.detail["code"] == "credit_service_quote_in_use"
        assert caught.value.detail["run_created"] is True
        assert caught.value.detail["run_id"] == original.id
        assert db.query(models.AnalysisRun).count() == db.query(CreditReservation).count() == 1


def test_exact_accepted_intent_recovers_after_source_removed(setup):
    from backend.app.domains.credits.models import CreditServiceQuote

    factory, _ = setup
    with factory() as db:
        original, _ = quote_and_create(db)
        q = db.query(CreditServiceQuote).one()
        db.query(models.Resume).filter_by(id=1).delete()
        db.commit()
        recovered, created = analysis_service.create_run(
            db,
            user_id=1,
            payload=request().model_copy(update={"accepted_service_quote_id": q.id}),
            header_idempotency_key=None,
        )
        assert recovered.id == original.id and created is False
        assert db.query(CreditReservation).count() == 1


def test_unaccepted_quote_with_removed_source_has_definite_no_run(setup):
    factory, _ = setup
    with factory() as db:
        payload = request()
        _, fingerprint, config, selected = analysis.prepare_quote_context(
            db, user_id=1, payload=payload
        )
        q = service_quotes.issue_service_quote(
            db,
            user_id=1,
            input_fingerprint=fingerprint,
            idempotency_key="removed-source-quote",
            route=selected,
            policy=config.policy,
        )
        db.commit()
        db.query(models.Resume).filter_by(id=1).delete()
        db.commit()
        with pytest.raises(HTTPException) as caught:
            analysis_service.create_run(
                db,
                user_id=1,
                payload=payload.model_copy(update={"accepted_service_quote_id": q.id}),
                header_idempotency_key=None,
            )
        assert caught.value.detail["code"] == "credit_service_quote_rejected"
        assert caught.value.detail["run_created"] is False
        assert db.query(models.AnalysisRun).count() == 0


def test_pause_preserves_persistent_contract_without_legacy_debit(setup, monkeypatch):
    factory, _ = setup
    with factory() as db:
        original, _ = quote_and_create(db)
        original.status = "failed"
        db.commit()
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
        with pytest.raises(HTTPException) as caught:
            analysis_service.create_run(
                db,
                user_id=1,
                payload=request().model_copy(update={"idempotency_key": "new-after-pause"}),
                header_idempotency_key=None,
            )
        assert caught.value.detail["code"] == "credit_service_quote_rejected"
        assert db.get(models.User, 1).ai_credits == 17
        assert db.query(models.AnalysisRun).count() == 1
