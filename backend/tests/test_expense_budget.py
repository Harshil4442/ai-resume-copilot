import json
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.billing.cost_policy import CostPolicyUnavailable, assert_model_funding
from backend.app.database import Base
from backend.app.models import ModelCostLiability
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


def _liability(*, state="reserved", held=70, settled=None, days=0, pool="prepaid"):
    return ModelCostLiability(
        id="synthetic-liability",
        financial_group_id="detached-group",
        attempt_number=1,
        provider="synthetic",
        model="synthetic",
        endpoint_key="0" * 64,
        currency="USD",
        pricing_quote={"expense_funding": "prepaid", "expense_pool": pool},
        input_token_estimate=10,
        reserved_cost_micros=held,
        settled_cost_micros=settled,
        cost_state=state,
        created_at=datetime.now(UTC) - timedelta(days=days),
    )


@pytest.fixture
def cost_db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


def _policy(monkeypatch, *, daily=100, monthly=100, risk=100):
    policy = synthetic_expense_policy() | {
        "daily_model_budget_micros": daily,
        "monthly_model_budget_micros": monthly,
        "monthly_promotional_model_budget_micros": risk,
        "monthly_legacy_premium_model_budget_micros": 0,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))


def test_old_unknown_liability_still_blocks_a_new_month(cost_db, monkeypatch):
    _policy(monkeypatch)
    cost_db.add(_liability(state="outcome_unknown", days=80))
    cost_db.commit()
    with pytest.raises(CostPolicyUnavailable):
        assert_model_funding(
            cost_db,
            operation="job_match",
            operation_ceiling=1000,
            reservation=40,
            funding="prepaid",
        )
    assert cost_db.query(ModelCostLiability).count() == 1


def test_known_cost_is_not_double_counted_and_old_settled_cost_is_not_current(cost_db, monkeypatch):
    _policy(monkeypatch)
    row = _liability(state="settled", held=90, settled=10)
    cost_db.add(row)
    cost_db.commit()
    assert (
        assert_model_funding(
            cost_db,
            operation="job_match",
            operation_ceiling=1000,
            reservation=90,
            funding="prepaid",
        )["expense_funding"]
        == "prepaid"
    )
    row.created_at = datetime.now(UTC) - timedelta(days=80)
    cost_db.commit()
    assert_model_funding(
        cost_db, operation="job_match", operation_ceiling=1000, reservation=100, funding="prepaid"
    )


def test_failed_paid_work_keeps_cash_cost_after_units_are_restored(cost_db, monkeypatch):
    _policy(monkeypatch, daily=1000, monthly=1000, risk=100)
    cost_db.add(_liability(state="settled", settled=70, pool="failed_work"))
    cost_db.commit()
    with pytest.raises(CostPolicyUnavailable, match="failed-work"):
        assert_model_funding(
            cost_db,
            operation="job_match",
            operation_ceiling=1000,
            reservation=40,
            funding="prepaid",
        )


def test_legacy_exposure_pool_does_not_waive_cash_budget(cost_db, monkeypatch):
    _policy(monkeypatch)
    with pytest.raises(CostPolicyUnavailable, match="legacy-access"):
        assert_model_funding(
            cost_db,
            operation="job_match",
            operation_ceiling=1000,
            reservation=1,
            funding="legacy_premium",
        )


@pytest.mark.parametrize("field", ["provider_fee_amount_minor", "customer_tax_amount_minor"])
def test_known_adverse_payment_expense_pauses_only_new_work(cost_db, monkeypatch, field):
    from backend.app.billing.cost_policy import ExpensePolicy, assert_actual_variance
    from backend.app.models import PaymentOrder
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    row = PaymentOrder(
        public_id="old-paid",
        user_id=None,
        sku="job_service_500",
        catalog_version="old",
        entitlement_kind="job_service_credits",
        entitlement_quantity=500,
        billing_country_confirmed_at=datetime.now(UTC),
        gross_amount_minor=49900,
        currency="INR",
        status="paid",
        paid_at=datetime.now(UTC),
    )
    setattr(row, field, 20000)
    cost_db.add(row)
    cost_db.commit()
    with pytest.raises(CostPolicyUnavailable, match="actual payment"):
        assert_actual_variance(cost_db, policy)
    assert cost_db.get(PaymentOrder, row.id).entitlement_quantity == 500
    assert row.gross_amount_minor == 49900 and row.status == "paid"


def test_unknown_payment_facts_are_not_fabricated_as_actual_zero(cost_db):
    from backend.app.billing.cost_policy import ExpensePolicy, assert_actual_variance
    from backend.app.models import PaymentOrder
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    row = PaymentOrder(
        public_id="unknown-fees",
        user_id=None,
        sku="job_service_500",
        catalog_version="old",
        entitlement_kind="job_service_credits",
        entitlement_quantity=500,
        billing_country_confirmed_at=datetime.now(UTC),
        gross_amount_minor=49900,
        currency="INR",
        status="paid",
        paid_at=datetime.now(UTC),
    )
    cost_db.add(row)
    cost_db.commit()
    assert_actual_variance(cost_db, ExpensePolicy.model_validate(estimated_expense_policy()))
    assert row.provider_fee_amount_minor is None and row.customer_tax_amount_minor is None


def test_processed_refunds_beyond_reviewed_risk_pause_new_work(cost_db):
    from backend.app.billing.cost_policy import ExpensePolicy, assert_actual_variance
    from backend.app.models import PaymentRefund
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    cost_db.add(
        PaymentRefund(
            order_id=1,
            transaction_id=1,
            provider="synthetic",
            provider_refund_id="old-refund",
            amount_minor=100,
            currency="INR",
            status="processed",
            processed_at=datetime.now(UTC),
        )
    )
    cost_db.commit()
    with pytest.raises(CostPolicyUnavailable, match="refund expenses"):
        assert_actual_variance(cost_db, ExpensePolicy.model_validate(estimated_expense_policy()))
    assert cost_db.query(PaymentRefund).one().amount_minor == 100


def test_accepted_five_credit_quote_uses_separate_cash_pool_without_repricing(cost_db, monkeypatch):
    from backend.app.domains.employer import credits
    from backend.app.models import User
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    policy = estimated_expense_policy() | {
        "review_status": "approved",
        "reviewed_by": "bounded-test-review",
        "monthly_legacy_service_budget_minor": 600,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    cost_db.add(User(id=1, email="old-order@example.com", job_service_credits=500))
    cost_db.commit()
    row = credits.reserve(
        cost_db,
        user_id=1,
        operation="job_application",
        source_id="accepted-old-quote",
        unit_price=5,
        count=1,
        pricing_version="accepted-old-v1",
    )
    cost_db.commit()
    assert (
        row.unit_price == 5
        and row.reserved_amount == 5
        and row.pricing_version == "accepted-old-v1"
    )
    assert row.cost_policy_snapshot["legacy_service_hold_minor"] == 600
    credits.settle(cost_db, row, completed_count=0, reason="No successful submission")
    cost_db.commit()
    assert cost_db.get(User, 1).job_service_credits == 500
    with pytest.raises(Exception) as error:
        credits.reserve(
            cost_db,
            user_id=1,
            operation="job_application",
            source_id="next-old-quote",
            unit_price=5,
            count=1,
        )
    assert error.value.status_code == 503
    assert cost_db.get(User, 1).job_service_credits == 500


def test_old_detached_unsettled_service_cost_survives_month_boundary(cost_db):
    from backend.app.billing.cost_policy import ExpensePolicy, legacy_service_hold
    from backend.app.domains.employer.models import ServiceCreditReservation
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    row = ServiceCreditReservation(
        id="old-reserve",
        user_id=None,
        operation="job_application",
        source_id="removed-candidate",
        unit_price=5,
        requested_count=1,
        reserved_amount=5,
        committed_amount=0,
        released_amount=0,
        state="reserved",
        pricing_version="old-v1",
        created_at=datetime.now(UTC) - timedelta(days=80),
    )
    cost_db.add(row)
    cost_db.commit()
    policy = ExpensePolicy.model_validate(
        estimated_expense_policy() | {"monthly_legacy_service_budget_minor": 600}
    )
    with pytest.raises(CostPolicyUnavailable, match="finite legacy"):
        legacy_service_hold(cost_db, policy, operation="job_application", unit_price=5, count=1)
    assert row.unit_price == 5 and row.user_id is None


def test_tiny_failed_calls_still_consume_worker_cash_pool(cost_db, monkeypatch):
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    policy = estimated_expense_policy() | {
        "review_status": "approved",
        "reviewed_by": "local-test-review",
        "daily_model_budget_micros": 100000,
        "monthly_model_budget_micros": 100000,
        "monthly_promotional_model_budget_micros": 10000,
        "monthly_legacy_premium_model_budget_micros": 0,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    cost_db.add(_liability(state="settled", held=1, settled=1, pool="failed_work"))
    cost_db.commit()
    with pytest.raises(CostPolicyUnavailable, match="failed-work"):
        assert_model_funding(
            cost_db,
            operation="job_match",
            operation_ceiling=250000,
            reservation=1,
            funding="prepaid",
        )
    # Recorded provider cost remains one micro-dollar; conservative estimated
    # worker cash is included only in the separate funded-work admission guard.
    assert cost_db.query(ModelCostLiability).one().settled_cost_micros == 1


def test_historical_cheap_balance_is_funded_even_at_current_service_price(cost_db, monkeypatch):
    from backend.app.domains.employer import credits
    from backend.app.models import User
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    policy = estimated_expense_policy() | {
        "review_status": "approved",
        "reviewed_by": "local-test-review",
        "monthly_legacy_service_budget_minor": 600,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    cost_db.add(User(id=1, email="old-cheap-balance@example.com", job_service_credits=500))
    cost_db.commit()
    row = credits.reserve(
        cost_db,
        user_id=1,
        operation="job_application",
        source_id="current-price-old-balance",
        unit_price=20,
        count=1,
    )
    cost_db.commit()
    assert (
        row.reserved_amount == 20 and row.cost_policy_snapshot["legacy_service_hold_minor"] == 600
    )
    assert cost_db.get(User, 1).job_service_credits == 480


def test_current_paid_bundle_provenance_funds_balance_before_legacy_carryover(cost_db):
    from backend.app.billing.cost_policy import ExpensePolicy, legacy_service_hold
    from backend.app.domains.employer.models import ServiceCreditEvent
    from backend.app.models import PaymentOrder, User
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    now = datetime.now(UTC)
    cost_db.add(User(id=1, email="mixed-balance@example.com", job_service_credits=600))
    cost_db.add(
        PaymentOrder(
            public_id="current-bundle",
            user_id=1,
            sku="starter_bundle",
            catalog_version="current",
            entitlement_kind="credit_bundle",
            entitlement_quantity=100,
            cost_policy_snapshot={"version": policy.version},
            billing_country_confirmed_at=now,
            gross_amount_minor=64900,
            currency="INR",
            status="paid",
            paid_at=now,
        )
    )
    cost_db.add(
        ServiceCreditEvent(
            id="current-grant",
            user_id=1,
            event_type="grant",
            amount=100,
            balance_after=600,
            idempotency_key="current-grant",
            source_type="payment_order",
            source_id="current-bundle",
            reason="captured",
            created_at=now,
        )
    )
    cost_db.commit()
    assert (
        legacy_service_hold(
            cost_db, policy, operation="job_application", unit_price=20, count=1, user_id=1
        )
        == 0
    )
    from backend.app.domains.employer.models import ServiceCreditReservation

    cost_db.add(
        ServiceCreditReservation(
            id="spent-new",
            user_id=1,
            operation="job_search",
            source_id="spent-search",
            unit_price=1,
            requested_count=100,
            reserved_amount=100,
            committed_amount=100,
            released_amount=0,
            state="settled",
            pricing_version=policy.version,
            cost_policy_snapshot={"paid_current_reserved_credits": 100},
        )
    )
    cost_db.commit()
    assert (
        legacy_service_hold(
            cost_db, policy, operation="job_application", unit_price=20, count=1, user_id=1
        )
        == 600
    )


def test_legacy_tailoring_endpoint_new_request_uses_two_units(cost_db, monkeypatch):
    from backend.app.models import AnalysisRun, JobMatch, Resume, UsageEvent, User
    from backend.app.routers import jobs
    from backend.app.schemas import ResumeTailorRequest
    from backend.app.services import llm_client, native_tex

    user = User(id=1, email="synthetic-tailor@example.com", ai_credits=2)
    resume = Resume(
        id=1,
        user_id=1,
        original_filename="synthetic.txt",
        raw_text="Built Python tools.",
        skills=["Python"],
        sections={},
    )
    cost_db.add_all([user, resume])
    cost_db.commit()
    cost_db.add(
        JobMatch(
            id=1,
            user_id=1,
            resume_id=1,
            job_description="Python tools",
            match_score=80,
            full_matches=["Python"],
            true_gaps=[],
        )
    )
    cost_db.commit()
    monkeypatch.setattr(
        llm_client, "tailor_resume_mega_llm", lambda **kwargs: "Synthetic source-supported draft"
    )
    # Unit-price compatibility only. Native format admission and actual compiler
    # failures are covered by the separate native suites; this boundary returns
    # an explicit local synthetic document, without invoking a provider/compiler.
    from io import BytesIO

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    output = BytesIO()
    writer.write(output)
    monkeypatch.setattr(native_tex, "compile_project", lambda *_args: (output.getvalue(), "fixture"))
    response = jobs.tailor_resume(1, ResumeTailorRequest(), db=cost_db, current_user=user)
    assert response.tailored_resume_markdown == "Synthetic source-supported draft"
    assert cost_db.get(User, 1).ai_credits == 0
    run = cost_db.query(AnalysisRun).one()
    assert (
        run.operation == "resume_tailor_legacy" and run.estimated_units == run.committed_units == 2
    )
    assert cost_db.query(UsageEvent).filter_by(event_type="reserve").one().amount == -2


def _fund_bundle(db, *, reference, service=100, units=2, user_id=1):
    from backend.app.domains.employer.models import ServiceCreditEvent
    from backend.app.models import PaymentOrder, UsageEvent

    now = datetime.now(UTC)
    db.flush()  # Fixture users must exist before PostgreSQL ledger FK inserts.
    db.add(
        PaymentOrder(
            public_id=reference,
            user_id=user_id,
            sku="starter_bundle",
            catalog_version="current",
            entitlement_kind="credit_bundle",
            entitlement_quantity=service,
            cost_policy_snapshot={"version": "approved-estimate"},
            billing_country_confirmed_at=now,
            gross_amount_minor=64900,
            currency="INR",
            status="paid",
            paid_at=now,
        )
    )
    db.add(
        ServiceCreditEvent(
            id=reference + "-service",
            user_id=user_id,
            event_type="grant",
            amount=service,
            balance_after=0,
            idempotency_key=reference + "-service",
            source_type="payment_order",
            source_id=reference,
            reason="captured",
            created_at=now,
        )
    )
    db.add(
        UsageEvent(
            id=reference + "-ai",
            user_id=user_id,
            event_type="grant",
            amount=units,
            balance_after=0,
            idempotency_key=reference + "-ai",
            source_type="payment_order",
            source_id=reference,
            actor="system",
            reason="captured",
            created_at=now,
        )
    )
    db.flush()


def test_new_first_mixed_service_allocation_does_not_create_legacy_debt(cost_db, monkeypatch):
    from backend.app.billing.cost_policy import current_service_funding
    from backend.app.domains.employer import credits
    from backend.app.models import User
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    monkeypatch.setenv(
        "HIREWIZ_EXPENSE_POLICY_JSON",
        json.dumps(
            estimated_expense_policy()
            | {"review_status": "approved", "reviewed_by": "local-test-review"}
        ),
    )
    cost_db.add(User(id=1, email="mixed-service@example.com", job_service_credits=600))
    _fund_bundle(cost_db, reference="new-1")
    cost_db.commit()
    row = credits.reserve(
        cost_db, user_id=1, operation="job_search", source_id="mixed500", unit_price=5, count=100
    )
    assert row.cost_policy_snapshot["paid_current_reserved_credits"] == 100
    assert row.cost_policy_snapshot["legacy_service_hold_minor"] == 2400
    credits.settle(cost_db, row, completed_count=100, reason="delivered")
    cost_db.commit()
    assert current_service_funding(cost_db, 1) == 0
    assert cost_db.get(User, 1).job_service_credits == 100
    old = credits.reserve(
        cost_db, user_id=1, operation="job_search", source_id="remaining100", unit_price=5, count=20
    )
    assert old.cost_policy_snapshot["paid_current_reserved_credits"] == 0
    assert old.cost_policy_snapshot["legacy_service_hold_minor"] == 600
    credits.settle(cost_db, old, completed_count=20, reason="delivered")
    cost_db.commit()
    cost_db.get(User, 1).job_service_credits += 100
    _fund_bundle(cost_db, reference="new-2")
    cost_db.commit()
    assert current_service_funding(cost_db, 1) == 100
    fresh = credits.reserve(
        cost_db, user_id=1, operation="job_search", source_id="fresh100", unit_price=1, count=100
    )
    assert fresh.cost_policy_snapshot["paid_current_reserved_credits"] == 100
    assert fresh.cost_policy_snapshot["legacy_service_hold_minor"] == 0


def test_partial_service_completion_restores_exact_paid_allocation(cost_db):
    from backend.app.billing.cost_policy import current_service_funding
    from backend.app.domains.employer import credits
    from backend.app.models import User

    cost_db.add(User(id=1, email="partial-service@example.com", job_service_credits=600))
    _fund_bundle(cost_db, reference="partial")
    cost_db.commit()
    row = credits.reserve(
        cost_db, user_id=1, operation="job_search", source_id="partial60", unit_price=1, count=60
    )
    assert current_service_funding(cost_db, 1) == 40
    credits.settle(cost_db, row, completed_count=20, reason="20 delivered;40 unused")
    cost_db.commit()
    assert current_service_funding(cost_db, 1) == 80
    assert cost_db.get(User, 1).job_service_credits == 580


def _accounting_run(db, reference, units):
    from backend.app.models import AnalysisRun

    run = AnalysisRun(
        id=reference,
        user_id=1,
        operation="market_analysis",
        status="running",
        usage_state="pending",
        idempotency_key=reference,
        input_fingerprint="f" * 64,
        input_payload={},
        estimated_units=units,
    )
    db.add(run)
    db.flush()
    return run


def test_mixed_ai_release_and_new_topup_restore_only_actual_paid_units(cost_db):
    from backend.app.domains.usage import service as usage
    from backend.app.models import User

    cost_db.add(User(id=1, email="mixed-ai@example.com", ai_credits=12))
    _fund_bundle(cost_db, reference="ai-1")
    cost_db.commit()
    run = _accounting_run(cost_db, "mixed-ai-5", 5)
    usage.reserve_run_usage(cost_db, user_id=1, run=run, units=5)
    cost_db.commit()
    assert usage.funding_for_run(cost_db, user_id=1, run=run, units=5) == "promotion"
    assert usage.current_analysis_funding(cost_db, 1) == 0
    usage.release_run_usage(cost_db, run, reason="failed result")
    cost_db.commit()
    assert usage.current_analysis_funding(cost_db, 1) == 2 and cost_db.get(User, 1).ai_credits == 12
    second = _accounting_run(cost_db, "mixed-ai-committed", 5)
    usage.reserve_run_usage(cost_db, user_id=1, run=second, units=5)
    usage.commit_run_usage(cost_db, second)
    cost_db.commit()
    assert usage.current_analysis_funding(cost_db, 1) == 0
    # Purging run telemetry cannot erase the paid-unit usage receipt.
    second.input_payload = {}
    second.model_cost_quote = None
    cost_db.commit()
    cost_db.get(User, 1).ai_credits += 2
    _fund_bundle(cost_db, reference="ai-2")
    cost_db.commit()
    assert usage.current_analysis_funding(cost_db, 1) == 2
    tail = _accounting_run(cost_db, "new-paid-2", 2)
    usage.reserve_run_usage(cost_db, user_id=1, run=tail, units=2)
    cost_db.commit()
    assert usage.funding_for_run(cost_db, user_id=1, run=tail, units=2) == "prepaid"


def test_pre_purchase_ai_release_cannot_invent_paid_units(cost_db):
    from backend.app.domains.usage import service as usage
    from backend.app.models import User

    cost_db.add(User(id=1, email="pre-purchase@example.com", ai_credits=10))
    cost_db.commit()
    old = _accounting_run(cost_db, "before-purchase", 5)
    usage.reserve_run_usage(cost_db, user_id=1, run=old, units=5)
    cost_db.commit()
    cost_db.get(User, 1).ai_credits += 2
    _fund_bundle(cost_db, reference="later-paid")
    cost_db.commit()
    usage.release_run_usage(cost_db, old, reason="unused")
    cost_db.commit()
    assert usage.current_analysis_funding(cost_db, 1) == 2


@pytest.mark.parametrize(
    "key",
    [
        "expense_paid_units",
        "_expense_paid_units",
        "expense_funding",
        "cost_policy_snapshot",
        "paid_current_reserved_credits",
    ],
)
def test_client_cannot_forge_paid_allocation(cost_db, key):
    from backend.app.domains.analysis import schemas, service
    from backend.app.models import AnalysisRun
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as error:
        service.create_run(
            cost_db,
            user_id=1,
            payload=schemas.AnalysisRunCreate(operation="job_match", input={key: 1000}),
            header_idempotency_key="forged-funding-test",
        )
    assert error.value.status_code == 422
    assert cost_db.query(AnalysisRun).count() == 0


def test_repeated_empty_paid_search_restores_credits_but_not_preparation_cash(cost_db, monkeypatch):
    from backend.app.billing.cost_policy import current_service_funding
    from backend.app.domains.employer import credits
    from backend.app.models import User
    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    from fastapi import HTTPException

    policy = estimated_expense_policy() | {
        "review_status": "approved",
        "reviewed_by": "local-test-review",
        "monthly_legacy_service_budget_minor": 120,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    cost_db.add(User(id=1, email="empty-paid@example.com", job_service_credits=100))
    _fund_bundle(cost_db, reference="empty-paid-bundle")
    cost_db.commit()
    for number in range(2):
        row = credits.reserve(
            cost_db,
            user_id=1,
            operation="job_search",
            source_id=f"fresh-empty-{number}",
            unit_price=1,
            count=100,
        )
        assert row.cost_policy_snapshot["failed_service_hold_minor"] == 60
        credits.settle(cost_db, row, completed_count=0, reason="No new matches")
        credits.settle(cost_db, row, completed_count=0, reason="Idempotent replay")
        cost_db.commit()
        assert current_service_funding(cost_db, 1) == 100
        assert cost_db.get(User, 1).job_service_credits == 100
    with pytest.raises(HTTPException) as error:
        credits.reserve(
            cost_db,
            user_id=1,
            operation="job_search",
            source_id="third-fresh-empty",
            unit_price=1,
            count=100,
        )
    assert error.value.status_code == 503
    assert cost_db.get(User, 1).job_service_credits == 100


def test_partial_paid_search_upward_rounds_small_preparation_hold(cost_db, monkeypatch):
    from backend.app.domains.employer import credits
    from backend.app.models import User
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    monkeypatch.setenv(
        "HIREWIZ_EXPENSE_POLICY_JSON",
        json.dumps(
            estimated_expense_policy()
            | {
                "review_status": "approved",
                "reviewed_by": "local-test-review",
            }
        ),
    )
    cost_db.add(User(id=1, email="partial-cash@example.com", job_service_credits=100))
    _fund_bundle(cost_db, reference="partial-cash-bundle")
    cost_db.commit()
    row = credits.reserve(
        cost_db, user_id=1, operation="job_search", source_id="three", unit_price=1, count=3
    )
    credits.settle(cost_db, row, completed_count=1, reason="one of three delivered")
    cost_db.commit()
    assert row.cost_policy_snapshot["failed_service_hold_minor"] == 40
    assert cost_db.get(User, 1).job_service_credits == 99


def test_settled_paid_provider_result_still_holds_risk_until_usage_commit(cost_db, monkeypatch):
    from backend.app.domains.usage import service as usage
    from backend.app.models import User

    _policy(monkeypatch, daily=1000, monthly=1000, risk=30)
    authority = assert_model_funding(
        cost_db, operation="job_match", operation_ceiling=1000, reservation=20, funding="prepaid"
    )
    assert authority["expense_pool"] == "failed_work"
    row = _liability(state="settled", held=20, settled=20, pool="failed_work")
    row.pricing_quote = authority
    cost_db.add(row)
    cost_db.add(User(id=1, email="result-pending@example.com", ai_credits=10))
    run = _accounting_run(cost_db, "pending-result", 1)
    run.model_cost_group_id = row.financial_group_id
    usage.reserve_run_usage(cost_db, user_id=1, run=run, units=1)
    cost_db.commit()
    with pytest.raises(CostPolicyUnavailable, match="failed-work"):
        assert_model_funding(
            cost_db,
            operation="job_match",
            operation_ceiling=1000,
            reservation=20,
            funding="prepaid",
        )
    usage.commit_run_usage(cost_db, run)
    cost_db.commit()
    assert row.pricing_quote["expense_pool"] == "prepaid"
    assert_model_funding(
        cost_db, operation="job_match", operation_ceiling=1000, reservation=20, funding="prepaid"
    )


def test_actual_empty_search_admits_preparation_before_index_scan_and_replays_free(monkeypatch):
    from backend.app.domains.employer.models import ServiceCreditReservation
    from backend.tests.test_employer_services import context
    from sqlalchemy import event

    factory, client, _ = context.__wrapped__(monkeypatch)
    policy = synthetic_expense_policy() | {"monthly_legacy_service_budget_minor": 4}
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    payload = {
        "resume_id": 10,
        "role": "Zebra Unmatched",
        "desired_count": 100,
        "idempotency_key": "empty-workflow-0",
    }
    first = client.post("/api/v1/employer-jobs/searches", json=payload)
    assert first.status_code == 201, first.text
    assert first.json()["charged_credits"] == first.json()["delivered_count"] == 0
    payload["idempotency_key"] = "empty-workflow-1"
    second = client.post("/api/v1/employer-jobs/searches", json=payload)
    assert second.status_code == 201, second.text
    with factory() as db:
        rows = db.query(ServiceCreditReservation).all()
        assert len(rows) == 2
        assert all(
            row.requested_count == 0 and row.cost_policy_snapshot["failed_service_hold_minor"] == 2
            for row in rows
        )

    def reject_scan(connection, cursor, statement, parameters, context, executemany):
        if "from employer_postings" in statement.lower():
            raise AssertionError(
                "Exhausted preparation must refuse before scanning the employer index"
            )

    engine = factory.kw["bind"]
    event.listen(engine, "before_cursor_execute", reject_scan)
    try:
        payload["idempotency_key"] = "empty-workflow-2"
        refused = client.post("/api/v1/employer-jobs/searches", json=payload)
        assert refused.status_code == 503, refused.text
        payload["idempotency_key"] = "empty-workflow-0"
        replay = client.post("/api/v1/employer-jobs/searches", json=payload)
        assert replay.status_code == 201 and replay.json()["id"] == first.json()["id"]
    finally:
        event.remove(engine, "before_cursor_execute", reject_scan)
        client.close()
