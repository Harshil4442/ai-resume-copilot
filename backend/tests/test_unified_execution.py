"""Whole-operation accounting uses real DB transactions and synthetic rate evidence."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.app.domains.credits import (
    admission,
    execution,
    finance,
    pricing,
    service,
    service_quotes,
)
from backend.app.domains.credits.models import (
    CreditExecutionAttempt,
    CreditExecutionHold,
    CreditReservation,
    CreditServiceQuote,
)
from backend.app.domains.credits.privacy import erase_owner_credit_links
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.models import ModelCostLiability, User
from backend.app.services.delivery_contracts import current_output_contract
from backend.app.services.model_cost_policy import ModelPriceQuote
from backend.tests.test_unified_credits import NOW, factory, grant

assert factory


def financial_policy():
    return finance.FinancialPolicy(
        version="synthetic-v1",
        valid_from=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        provenance="synthetic_test",
        evidence_sha256="f" * 64,
    )


def route(*, attempts=2, native=False):
    model = ModelPriceQuote(
        provider="google",
        model="synthetic-model",
        api_base="https://generativelanguage.googleapis.com",
        version="synthetic-rate-v1",
        price_source_url="https://example.com/price",
        output_limit_source_url="https://example.com/limits",
        usage_source_url="https://example.com/usage",
        valid_from=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        input_rate_micros_per_million=100_000,
        output_rate_micros_per_million=400_000,
        max_input_tokens=1024,
        max_output_tokens=1024,
        output_limit_parameter="max_output_tokens",
        token_estimator="utf8-json-bytes-framing-v1",
    )
    return service_quotes.QualifiedRoute(
        id="native-discovery" if native else "qualified-tailor",
        version="synthetic-v1",
        operation="job_discovery_batch" if native else "resume_tailor",
        tariff_version=pricing.TARIFF_VERSION,
        credits=1 if native else 8,
        mode="native" if native else "enhanced",
        deliverable="Synthetic complete deliverable",
        supported_limits={"max_file_bytes": 10000},
        supported_source_formats=("pdf",),
        qualification_id="synthetic-qualification",
        qualification_evidence_sha256="a" * 64,
        output_contract_sha256=None if native else current_output_contract("resume_tailor"),
        qualified_from=NOW - timedelta(days=1),
        qualified_until=NOW + timedelta(days=30),
        native_cost_evidence_sha256="b" * 64,
        native_processing_minor=5,
        worker_processing_minor=8,
        model_quotes=() if native else (model,) * attempts,
    )


def budgets(**changes):
    values = dict(
        version="synthetic-funding-v1",
        evidence_sha256="d" * 64,
        valid_from=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
        daily_global_minor=10000,
        monthly_global_minor=10000,
        monthly_account_minor=10000,
        monthly_incident_minor=10000,
        monthly_route_minor={"qualified-tailor": 10000, "native-discovery": 10000},
        legacy_search_cost_minor=10,
        legacy_application_cost_minor=30,
        legacy_preparation_minor=10,
        legacy_provider_attempt_overhead_minor=2,
    )
    return admission.ExecutionBudgetPolicy.model_validate(values | changes)


def quote(db, *, owner=1, source="run-one", selected=None, now=NOW):
    return service_quotes.issue_service_quote(
        db,
        user_id=owner,
        input_fingerprint="a" * 64,
        idempotency_key=f"quote-{source}",
        route=selected or route(),
        policy=financial_policy(),
        now=now,
    )


def admitted(db, *, owner=1, source="run-one", selected=None, limits=None, now=NOW):
    q = quote(db, owner=owner, source=source, selected=selected, now=now)
    return admission.admit(
        db,
        user_id=owner,
        quote_id=q.id,
        input_fingerprint="a" * 64,
        idempotency_key=f"execute-{source}",
        source_id=source,
        route=selected or route(),
        policy=financial_policy(),
        budgets=limits or budgets(),
        dispatch_topic="analysis.run",
        dispatch_payload={"run_id": source},
        now=now,
    )


def claim(db, hold, *, key="claim-one", selected=None):
    return execution.claim_attempt(
        db,
        user_id=1,
        hold_id=hold.id,
        claim_key=key,
        provider="google",
        model="synthetic-model",
        api_base="https://generativelanguage.googleapis.com",
        messages=[{"role": "user", "content": "Synthetic test."}],
        route=selected or route(),
        policy=financial_policy(),
        now=NOW,
    )


def response():
    return SimpleNamespace(
        usage_metadata=SimpleNamespace(
            prompt_token_count=300,
            candidates_token_count=100,
            total_token_count=450,
            thoughts_token_count=50,
        )
    )


def test_quote_expiry_input_binding_and_no_silent_new_terms(factory):
    with factory() as db:
        grant(db)
        q = quote(db)
        assert service_quotes.quote_view(q, now=NOW)["credits"] == 8
        with pytest.raises(pricing.CreditContractError, match="inputs"):
            admission.admit(
                db,
                user_id=1,
                quote_id=q.id,
                input_fingerprint="b" * 64,
                idempotency_key="execute-run-one",
                source_id="run-one",
                route=route(),
                policy=financial_policy(),
                budgets=budgets(),
                dispatch_topic="analysis.run",
                dispatch_payload={"run_id": "run-one"},
                now=NOW,
            )
        with pytest.raises(pricing.CreditContractError, match="expired"):
            admitted(db, now=NOW + timedelta(minutes=15))
        assert db.query(CreditExecutionHold).count() == 0
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_full_money_credits_and_outbox_are_atomic_and_idempotent(factory):
    with factory() as db:
        grant(db)
        db.commit()
        hold = admitted(db)
        assert hold.reserved_minor == 27
        assert db.query(ModelCostLiability).count() == 2
        assert db.query(CreditExecutionAttempt).count() == 2
        assert db.query(DispatchOutbox).count() == 1
        assert admitted(db).id == hold.id
        db.rollback()
        assert db.query(CreditReservation).count() == 0
        assert db.query(ModelCostLiability).count() == 0
        assert db.query(DispatchOutbox).count() == 0
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_global_refusal_leaves_no_partial_reservation(factory):
    with factory() as db:
        grant(db)
        db.commit()
        with pytest.raises(finance.FundingUnavailable, match="global"):
            admitted(db, limits=budgets(daily_global_minor=26))
        db.rollback()
        assert db.query(CreditReservation).count() == 0
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_claim_is_durable_and_never_dispatches_same_attempt_twice(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db)
        binding, record = claim(db, hold)
        db.commit()
        assert record["attempt_number"] == 1
        assert db.get(ModelCostLiability, binding.liability_id).cost_state == "reserved"
        with pytest.raises(finance.FundingUnavailable, match="reconciliation"):
            claim(db, hold)
        assert db.query(CreditExecutionAttempt).filter_by(state="planned").count() == 1


def test_failure_returns_usable_credits_but_unknown_provider_money_remains(factory):
    with factory() as db:
        grant(db, expiry=NOW + timedelta(hours=1))
        hold = admitted(db)
        binding, _ = claim(db, hold)
        execution.finish_attempt(db, binding=binding, error=TimeoutError(), now=NOW)
        execution.record_native_cost(db, user_id=1, hold_id=hold.id, actual_minor=7, now=NOW)
        later = NOW + timedelta(days=2)
        execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=False, now=later)
        db.commit()
        assert service.wallet(db, user_id=1, now=later).available_credits == 8
        assert all(
            lot.expires_at >= later + timedelta(days=7)
            for lot in service.wallet(db, user_id=1, now=later).lots
        )
        assert hold.state == "outcome_unknown"
        assert hold.reserved_minor == 27
        assert db.get(ModelCostLiability, binding.liability_id).reserved_cost_micros == 512
        assert db.query(CreditExecutionAttempt).filter_by(state="cancelled").count() == 1
        assert admission._hold_usage(db, start=NOW + timedelta(days=40)) == 27
        execution.finish_attempt(db, binding=binding, response=response(), now=later)
        db.commit()
        assert hold.state == "settled" and hold.settled_minor == 9
        assert admission._hold_usage(db, start=NOW + timedelta(days=40)) == 0


def test_success_commits_once_and_releases_only_unused_recovery_money(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db)
        binding, _ = claim(db, hold)
        execution.finish_attempt(db, binding=binding, response=response(), now=NOW)
        execution.record_native_cost(db, user_id=1, hold_id=hold.id, actual_minor=7, now=NOW)
        assert admission._hold_usage(db, start=NOW) == 27
        execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=True, now=NOW)
        execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=True, now=NOW)
        assert hold.state == "settled" and hold.settled_minor == 9
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 32
        assert admission._hold_usage(db, start=NOW, incident=True) == 0


def test_native_route_has_no_provider_liabilities(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db, selected=route(native=True))
        assert hold.reserved_minor == 13
        assert db.query(ModelCostLiability).count() == 0
        execution.record_native_cost(db, user_id=1, hold_id=hold.id, actual_minor=9, now=NOW)
        execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=True, now=NOW)
        assert hold.state == "settled" and hold.settled_minor == 9
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 39


def test_account_erasure_retains_unknown_cost_and_private_completion_binding(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db)
        binding, _ = claim(db, hold)
        db.commit()
        erase_owner_credit_links(db, user_id=1)
        db.delete(db.get(User, 1))
        db.commit()
        assert db.get(CreditExecutionHold, hold.id).user_id is None
        assert db.query(CreditServiceQuote).count() == 0
        assert admission._hold_usage(db, start=NOW + timedelta(days=40)) == 27
        execution.finish_attempt(db, binding=binding, response=response(), now=NOW)
        db.commit()
        assert db.get(ModelCostLiability, binding.liability_id).cost_state == "settled"
        assert admission._hold_usage(db, start=NOW + timedelta(days=40)) == 27


def test_legacy_unknown_liability_from_prior_month_blocks_new_work(factory):
    with factory() as db:
        grant(db)
        db.add(
            ModelCostLiability(
                id="old",
                financial_group_id="old-group",
                attempt_number=1,
                provider="google",
                model="synthetic",
                endpoint_key="a" * 64,
                currency="USD",
                pricing_quote={},
                input_token_estimate=100,
                reserved_cost_micros=100_000,
                cost_state="outcome_unknown",
                created_at=NOW - timedelta(days=40),
            )
        )
        db.commit()
        with pytest.raises(finance.FundingUnavailable, match="global"):
            admitted(db, limits=budgets(daily_global_minor=100))
        db.rollback()
        assert db.query(CreditReservation).count() == 0
        assert db.query(ModelCostLiability).count() == 1


def test_whole_operation_usage_does_not_double_count_its_provider_holds(factory):
    with factory() as db:
        grant(db)
        admitted(db)
        assert admission._hold_usage(db, start=NOW) == 27
        assert (
            admission._legacy_usage(db, start=NOW, policy=financial_policy(), budgets=budgets())
            == 0
        )


def test_three_failed_deliveries_pause_route_before_another_spend(factory):
    with factory() as db:
        grant(db)
        for index in range(3):
            hold = admitted(db, source=f"failed-{index}")
            execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=False, now=NOW)
        with pytest.raises(finance.FundingUnavailable, match="repeated delivery failures"):
            admitted(db, source="fourth-attempt")
        assert db.query(CreditReservation).count() == 3
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_inline_admission_has_no_fake_worker_dispatch(factory):
    with factory() as db:
        grant(db)
        q = quote(db)
        hold = admission.admit(
            db,
            user_id=1,
            quote_id=q.id,
            input_fingerprint="a" * 64,
            idempotency_key="inline-request",
            source_id="sync-run",
            route=route(),
            policy=financial_policy(),
            budgets=budgets(),
            inline=True,
            now=NOW,
        )
        assert hold.snapshot["execution_mode"] == "inline"
        assert db.query(DispatchOutbox).count() == 0
        assert db.query(CreditReservation).count() == 1
        assert db.query(ModelCostLiability).count() == 2
        with pytest.raises(pricing.CreditContractError, match="complete dispatch intent"):
            admission.admit(
                db,
                user_id=1,
                quote_id=q.id,
                input_fingerprint="a" * 64,
                idempotency_key="inline-request",
                source_id="sync-run",
                route=route(),
                policy=financial_policy(),
                budgets=budgets(),
                now=NOW,
            )
