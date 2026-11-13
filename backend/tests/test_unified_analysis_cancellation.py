"""Cancellation versus funded provider admission; no supplier or cloud calls."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from backend.app import models
from backend.app.domains.analysis.service import cancel_run
from backend.app.domains.credits import analysis, execution, finance, pricing, service
from backend.app.domains.credits.models import (
    CreditExecutionAttempt,
    CreditExecutionHold,
    CreditReservation,
)
from backend.tests.test_mail_budget_postgres import owned_pg_url
from backend.tests.test_unified_credits import factory, grant
from backend.tests.test_unified_execution import (
    NOW,
    admitted,
    budgets,
    claim,
    financial_policy,
    response,
    route,
)
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

assert factory and owned_pg_url


def seed(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db)
        run = db.get(models.AnalysisRun, "run-one")
        if run is None:
            run = models.AnalysisRun(
                id="run-one", user_id=1, operation="resume_tailor", status="running",
                idempotency_key="execute-run-one", input_fingerprint="a" * 64,
                input_payload={}, usage_state="reserved", estimated_units=8,
                model_cost_group_id=hold.financial_group_id,
                model_cost_quote={"billing_currency": "credits", "unified_hold_id": hold.id},
            )
            db.add(run)
        run.status = "running"
        run.usage_state, run.estimated_units = "reserved", 8
        hold_id = hold.id
        db.commit()
    return hold_id


def invoke(budget, stage):
    args = ("google", "synthetic-model", [{"role": "user", "content": "Synthetic."}])
    if stage == "preflight":
        return budget.preflight(*args, "https://generativelanguage.googleapis.com")
    return budget.admit(*args, api_base="https://generativelanguage.googleapis.com")


@pytest.mark.parametrize("stage", ["preflight", "claim"])
def test_acknowledged_cancel_after_budget_entry_refuses_next_request(factory, stage):
    hold_id = seed(factory)
    with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
            route=route(), policy=financial_policy(), analysis_run_id="run-one") as budget:
        with factory() as db:
            assert cancel_run(db, 1, "run-one").cancel_requested
        with pytest.raises(finance.FundingUnavailable, match="cancel|active"):
            invoke(budget, stage)
    with factory() as db:
        assert db.query(CreditExecutionAttempt).filter_by(state="started").count() == 0


@pytest.mark.parametrize("path", ["entry", "direct_claim"])
def test_omitted_run_id_does_not_bypass_cancelled_source(factory, path):
    hold_id = seed(factory)
    with factory() as db:
        cancel_run(db, 1, "run-one")
    with pytest.raises(finance.FundingUnavailable, match="cancel|active"):
        if path == "entry":
            with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
                    route=route(), policy=financial_policy()):
                pytest.fail("Cancelled source cannot enter a budget without its run ID")
        else:
            with factory() as db:
                claim(db, db.get(CreditExecutionHold, hold_id))


@pytest.mark.parametrize("stage", ["preflight", "claim"])
def test_cancel_before_repair_preserves_prior_cost_and_blocks_next_attempt(factory, stage):
    hold_id = seed(factory)
    with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
            route=route(), policy=financial_policy()) as budget:
        record = invoke(budget, "claim")
        budget.finish(record, latency_ms=1, response=response())
        with factory() as db:
            cancel_run(db, 1, "run-one")
        with pytest.raises(finance.FundingUnavailable, match="cancel|active"):
            invoke(budget, stage)
    with factory() as db:
        first, second = db.query(CreditExecutionAttempt).order_by(CreditExecutionAttempt.ordinal).all()
        liability = db.get(models.ModelCostLiability, first.liability_id)
        assert first.state == "settled" and liability.settled_cost_micros > 0
        assert second.state == "planned"


@pytest.mark.parametrize("outcome", ["known", "unknown"])
def test_original_inflight_completion_survives_cancel_and_customer_refund(factory, outcome):
    hold_id = seed(factory)
    with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
            route=route(), policy=financial_policy(), analysis_run_id="run-one") as budget:
        record = invoke(budget, "claim")
        with factory() as db:
            cancel_run(db, 1, "run-one")
        budget.finish(record, latency_ms=1, response=response() if outcome == "known" else None,
            error=TimeoutError() if outcome == "unknown" else None)
    with factory() as db:
        run = db.get(models.AnalysisRun, "run-one")
        run.status = "cancelled"
        with pytest.raises(pricing.CreditContractError, match="outcome"):
            analysis.settle_analysis(db, run, delivered=True)
        assert analysis.settle_analysis(db, run, delivered=False)
        db.commit()
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40
        hold = db.get(CreditExecutionHold, hold_id)
        first, second = db.query(CreditExecutionAttempt).order_by(CreditExecutionAttempt.ordinal).all()
        liability = db.get(models.ModelCostLiability, first.liability_id)
        assert hold.delivery_state == "cancelled" and hold.state == "outcome_unknown"
        assert hold.native_settled_minor is None and second.state == "cancelled"
        if outcome == "unknown":
            assert first.state == liability.cost_state == "outcome_unknown"
            assert liability.reserved_cost_micros > 0 and liability.settled_cost_micros is None
        else:
            assert first.state == liability.cost_state == "settled" and liability.settled_cost_micros > 0


@pytest.mark.parametrize("trigger", ["delivery_failures", "expense_overrun"])
def test_accepted_route_circuit_stops_next_provider_request_without_losing_settlement(factory, trigger):
    blocked_id = seed(factory)
    other_route = route().model_copy(update={"id": "other-tailor"})
    with factory() as db:
        grant(db, quantity=40, source="additional-funded-order")
        inflight_id = admitted(db, source="inflight-tailor").id
        sibling_id = admitted(db, source="unaffected-tailor", selected=other_route,
            limits=budgets(monthly_route_minor={other_route.id: 10000})).id
        trigger_ids = [admitted(db, source=f"trigger-{index}").id
                       for index in range(3 if trigger == "delivery_failures" else 1)]
        binding, _ = claim(db, db.get(CreditExecutionHold, inflight_id))
        db.commit()

    with execution.persistent_execution_budget(factory, user_id=1, hold_id=blocked_id,
            route=route(), policy=financial_policy()) as budget:
        assert invoke(budget, "preflight") == 1024
        with factory() as db:
            if trigger == "delivery_failures":
                for identity in trigger_ids:
                    execution.settle_delivery(db, user_id=1, hold_id=identity, delivered=False, now=NOW)
            else:
                execution.record_native_cost(db, user_id=1, hold_id=trigger_ids[0], actual_minor=14, now=NOW)
            db.commit()
        for stage in ("preflight", "claim"):
            with pytest.raises(finance.FundingUnavailable, match="This route is paused"):
                invoke(budget, stage)
        assert budget.used == 0 and budget.attempts == []

    with execution.persistent_execution_budget(factory, user_id=1, hold_id=sibling_id,
            route=other_route, policy=financial_policy()) as budget:
        assert invoke(budget, "preflight") == 1024
        record = invoke(budget, "claim")
        budget.finish(record, latency_ms=1, response=response())

    with factory() as db:
        pending = db.query(CreditExecutionAttempt).filter_by(hold_id=blocked_id).all()
        assert pending and all(attempt.state == "planned" and attempt.claim_key is None for attempt in pending)
        execution.finish_attempt(db, binding=binding, error=TimeoutError(), now=NOW)
        execution.settle_delivery(db, user_id=1, hold_id=inflight_id, delivered=False, now=NOW)
        execution.settle_delivery(db, user_id=1, hold_id=blocked_id, delivered=False, now=NOW)
        db.commit()
        unknown = db.get(models.ModelCostLiability, binding.liability_id)
        assert unknown.cost_state == "outcome_unknown" and unknown.settled_cost_micros is None
        reservation = db.get(CreditReservation, db.get(CreditExecutionHold, blocked_id).reservation_id)
        assert reservation.released_credits == 8 and reservation.committed_credits == 0


@pytest.mark.parametrize("change", ["owner", "group", "hold", "fingerprint", "operation", "source", "missing", "currency"])
def test_budget_rejects_cross_bound_or_missing_analysis_source(factory, change):
    hold_id = seed(factory)
    with factory() as db:
        run = db.get(models.AnalysisRun, "run-one")
        if change == "owner":
            run.user_id = 2
        elif change == "group":
            run.model_cost_group_id = "foreign-group"
        elif change == "hold":
            run.model_cost_quote = dict(run.model_cost_quote, unified_hold_id="foreign-hold")
        elif change == "currency":
            run.model_cost_quote = dict(run.model_cost_quote, billing_currency="legacy_ai_units")
        elif change == "fingerprint":
            run.input_fingerprint = "b" * 64
        elif change == "operation":
            run.operation = "interview_questions"
        elif change == "source":
            db.query(CreditReservation).one().source_id = "different-source"
        elif change == "missing":
            db.delete(run)
        db.commit()
    with pytest.raises(pricing.CreditContractError, match="analysis"):
        with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
                route=route(), policy=financial_policy()):
            pytest.fail("Invalid source cannot become non-run work by omitting its ID")


@pytest.mark.parametrize("state", ["succeeded", "failed", "cancelled"])
def test_terminal_run_cannot_admit_new_generation(factory, state):
    hold_id = seed(factory)
    with factory() as db:
        db.get(models.AnalysisRun, "run-one").status = state
        db.commit()
    with pytest.raises(finance.FundingUnavailable, match="active"):
        with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
                route=route(), policy=financial_policy()):
            pytest.fail("Terminal source cannot dispatch")


def test_explicit_other_run_id_is_refused_and_true_nonrun_budget_stays_supported(factory):
    hold_id = seed(factory)
    with pytest.raises(pricing.CreditContractError, match="analysis"):
        with execution.persistent_execution_budget(factory, user_id=1, hold_id=hold_id,
                route=route(), policy=financial_policy(), analysis_run_id="different-run"):
            pytest.fail("Explicit source cannot be substituted")
    with factory() as db:
        native = admitted(db, source="native-one", selected=route(native=True))
        native_id = native.id
        db.commit()
    with execution.persistent_execution_budget(factory, user_id=1, hold_id=native_id,
            route=route(native=True), policy=financial_policy()) as budget:
        assert budget.run_id == "unified:" + native_id and budget.limit == 0


def test_cancelled_settlement_is_idempotent_and_cannot_mean_delivered(factory):
    hold_id = seed(factory)
    with factory() as db:
        with pytest.raises(pricing.CreditContractError, match="outcome"):
            execution.settle_delivery(db, user_id=1, hold_id=hold_id, delivered=True, cancelled=True)
        with pytest.raises(pricing.CreditContractError, match="outcome"):
            execution.settle_delivery(db, user_id=1, hold_id=hold_id, delivered=False, cancelled="true")
        run = db.get(models.AnalysisRun, "run-one")
        run.status = "cancelled"
        assert analysis.settle_analysis(db, run, delivered=False)
        db.commit()
        assert analysis.settle_analysis(db, run, delivered=False)
        db.commit()
        assert db.get(CreditExecutionHold, hold_id).delivery_state == "cancelled"
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40
        with pytest.raises(pricing.CreditContractError, match="different final"):
            execution.settle_delivery(db, user_id=1, hold_id=hold_id, delivered=False)


@pytest.fixture
def analysis_factory():
    from backend.tests.test_unified_analysis import factory as source_factory

    yield from source_factory.__wrapped__()


def test_real_analysis_admission_and_budget_bind_before_late_cancel(analysis_factory, monkeypatch):
    from backend.tests.test_unified_analysis import quote_and_create
    from backend.tests.test_unified_analysis import setup as analysis_setup

    analysis_setup.__wrapped__(analysis_factory, monkeypatch)
    with analysis_factory() as db:
        run, created = quote_and_create(db)
        assert created
        identity = run.id
        run.status = "running"
        db.commit()
    with analysis.analysis_budget(analysis_factory, identity) as budget:
        assert budget.run_id == identity
        with analysis_factory() as db:
            cancel_run(db, 1, identity)
        with pytest.raises(finance.FundingUnavailable, match="cancel|active"):
            invoke(budget, "claim")


def test_worker_refunds_accepted_route_paused_between_count_and_claim(analysis_factory, monkeypatch):
    from backend.app.domains.analysis import tasks
    from backend.app.services import llm_client
    from backend.tests.test_unified_analysis import quote_and_create
    from backend.tests.test_unified_analysis import setup as analysis_setup

    _, config = analysis_setup.__wrapped__(analysis_factory, monkeypatch)
    selected = config.routes[0]
    selected = selected.model_copy(update={"model_quotes": tuple(
        price.model_copy(update={"model": "gemini-3.6-flash"}) for price in selected.model_quotes)})
    from backend.tests.fixtures.reviewed_runtime import reviewed_runtime

    config = config.model_copy(update={"routes": (selected,)})
    reviewed_runtime(monkeypatch, config)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    monkeypatch.setenv("LLM_API_KEY", "synthetic-no-provider-key")
    monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")

    with analysis_factory() as db:
        run, _ = quote_and_create(db)
        identity = run.id
        incident = admitted(db, source="worker-route-overrun", selected=selected).id
        db.commit()

    count_calls = []

    def count(*_args):
        count_calls.append(True)
        with analysis_factory() as db:
            execution.record_native_cost(db, user_id=1, hold_id=incident, actual_minor=14, now=NOW)
            db.commit()
        return 100

    def forbidden_generation(*_args, **_kwargs):
        pytest.fail("The stopped route sent a generation request")

    from google import genai

    monkeypatch.setattr(llm_client, "count_gemini_input", count)
    monkeypatch.setattr(genai, "Client", forbidden_generation)
    monkeypatch.setattr(tasks, "execute_operation", lambda *_: llm_client._chat(
        [{"role": "user", "content": "Synthetic input."}]))
    assert tasks.process_analysis_run(identity) == "failed"
    assert tasks.process_analysis_run(identity) == "failed"
    assert count_calls == [True]
    with analysis_factory() as db:
        run = db.get(models.AnalysisRun, identity)
        assert run.attempt_count == 1 and run.generation_attempt_count == 0
        assert run.usage_state == "released" and run.committed_units == 0 and run.result_payload is None
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 37


@pytest.mark.parametrize("outcome", ["cancelled", "failed"])
def test_actual_worker_cancellations_do_not_trip_real_failure_circuit(
        analysis_factory, monkeypatch, outcome):
    from backend.app.domains.analysis import service as analysis_service
    from backend.app.domains.analysis import tasks
    from backend.app.domains.credits import admission, service_quotes
    from backend.tests.test_unified_analysis import request
    from backend.tests.test_unified_analysis import setup as analysis_setup
    from fastapi import HTTPException

    _, config = analysis_setup.__wrapped__(analysis_factory, monkeypatch)

    def create(index):
        payload = request()
        payload.input["job_description"] += f" Unique fixture role {index}."
        payload.idempotency_key = f"worker-circuit-{index}"
        with analysis_factory() as db:
            _, fingerprint, _, selected = analysis.prepare_quote_context(db, user_id=1, payload=payload)
            quote = service_quotes.issue_service_quote(db, user_id=1, input_fingerprint=fingerprint,
                idempotency_key=f"worker-circuit-quote-{index}", route=selected, policy=config.policy)
            db.commit()
            run, created = analysis_service.create_run(db, user_id=1,
                payload=payload.model_copy(update={"accepted_service_quote_id": quote.id}),
                header_idempotency_key=None)
            assert created
            return run.id

    def execute(_db, run):
        if outcome == "failed":
            raise ValueError("Synthetic execution failure")
        with analysis_factory() as db:
            assert cancel_run(db, 1, run.id).cancel_requested
        return {"synthetic_validated_result": True}

    monkeypatch.setattr(tasks, "execute_operation", execute)
    for index in range(3):
        assert tasks.process_analysis_run(create(index)) == outcome
    if outcome == "failed":
        with pytest.raises(HTTPException, match="repeated delivery failures"):
            create(3)
    else:
        assert create(3)
    with analysis_factory() as db:
        holds = db.query(CreditExecutionHold).filter(CreditExecutionHold.delivery_state != "pending").all()
        assert len(holds) == 3 and all(hold.delivery_state == outcome for hold in holds)
        assert all(hold.state == "outcome_unknown" and hold.native_settled_minor is None for hold in holds)
        assert service.wallet(db, user_id=1).available_credits == (37 if outcome == "cancelled" else 40)
        assert admission._hold_usage(db, start=NOW, incident=True) > 0


@pytest.fixture
def pg_factory(owned_pg_url):
    schema = "credit_cancel_" + uuid4().hex
    admin = create_engine(owned_pg_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(owned_pg_url, connect_args={"application_name": "credit-cancel-owned",
        "options": f"-csearch_path={schema} -clock_timeout=3000 -cstatement_timeout=8000 -ctimezone=UTC"})
    models.Base.metadata.create_all(engine)
    maker = sessionmaker(engine, autoflush=False)
    with maker() as db:
        db.add_all([models.User(id=1, email="cancel-owner@example.invalid"),
            models.User(id=2, email="cancel-other@example.invalid")])
        db.commit()
    try:
        yield maker
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_actual_pg_queued_cancel_run_lock_wins_without_owner_lock_deadlock(pg_factory):
    hold_id = seed(pg_factory)
    with pg_factory() as db:
        db.get(models.AnalysisRun, "run-one").status = "queued"
        db.commit()
    with pg_factory() as cancellation:
        cancellation.query(models.AnalysisRun).filter_by(id="run-one").with_for_update().one()

        def attempt():
            with pg_factory() as db:
                with pytest.raises(finance.FundingUnavailable, match="being cancelled"):
                    claim(db, db.get(CreditExecutionHold, hold_id))
                # The exact55P03 branch rolled back, releasing owner/hold locks.
                assert db.scalar(text("SELECT 1")) == 1

        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(attempt).result(timeout=6)
        assert cancel_run(cancellation, 1, "run-one").status == "cancelled"
    with pg_factory() as db:
        assert db.query(CreditExecutionAttempt).filter_by(state="started").count() == 0
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_actual_pg_claim_commit_wins_then_cancel_keeps_unknown_supplier_cost(pg_factory):
    hold_id = seed(pg_factory)
    entered, completed = Event(), Event()

    def cancel():
        with pg_factory() as db:
            entered.set()
            result = cancel_run(db, 1, "run-one")
            completed.set()
            return result.cancel_requested

    with ThreadPoolExecutor(max_workers=1) as pool, pg_factory() as db:
        binding, _ = claim(db, db.get(CreditExecutionHold, hold_id))
        future = pool.submit(cancel)
        assert entered.wait(timeout=3) and not completed.wait(timeout=0.1)
        db.commit()
        assert future.result(timeout=6)
    with pg_factory() as db:
        execution.finish_attempt(db, binding=binding, error=TimeoutError(), now=NOW)
        run = db.get(models.AnalysisRun, "run-one")
        run.status = "cancelled"
        assert analysis.settle_analysis(db, run, delivered=False)
        db.commit()
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40
        assert db.get(models.ModelCostLiability, binding.liability_id).cost_state == "outcome_unknown"
        assert db.get(CreditExecutionHold, hold_id).state == "outcome_unknown"
