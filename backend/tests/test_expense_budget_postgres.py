"""Real two-owner provider admission, against a UUID-owned local PostgreSQL schema."""

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from backend.app.database import Base
from backend.app.models import AnalysisRun, ModelCostLiability, User
from backend.app.services.generation_budget import persistent_run_budget
from backend.app.services.model_cost_policy import (
    ModelCostUnavailable,
    freeze_run_quote,
    input_token_estimate,
    quoted_cost,
)
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from backend.tests.model_cost_fixtures import synthetic_policy
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_engine():
    yield from owned_pg_engine.__wrapped__()


def test_different_owners_share_one_atomic_funded_model_limit(pg_engine, monkeypatch):
    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine)
    messages = [{"role": "user", "content": "synthetic"}]
    quote = synthetic_policy()["pricing_quotes"][0]
    hold = quoted_cost(quote, input_token_estimate(messages), quote["max_output_tokens"])
    policy = synthetic_expense_policy() | {
        "daily_model_budget_micros": hold,
        "monthly_model_budget_micros": hold,
        "monthly_promotional_model_budget_micros": hold,
        "monthly_legacy_premium_model_budget_micros": 0,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    with factory() as db:
        db.add_all([User(id=i, email=f"synthetic-{i}@example.com", ai_credits=10) for i in (1, 2)])
        db.commit()
        for i in (1, 2):
            run = AnalysisRun(
                id=f"run-{i}",
                user_id=i,
                operation="job_match",
                status="running",
                idempotency_key=f"run-{i}",
                input_fingerprint="a" * 64,
                input_payload={},
                estimated_units=1,
                generation_attempt_limit=3,
            )
            freeze_run_quote(run)
            db.add(run)
        db.commit()
    gate = Barrier(2)

    def attempt(i):
        with persistent_run_budget(factory, f"run-{i}") as budget:
            gate.wait(timeout=10)
            try:
                budget.admit(
                    quote["provider"], quote["model"], messages, api_base=quote["api_base"]
                )
                return "admitted"
            except ModelCostUnavailable:
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    assert sorted(outcomes) == ["admitted", "denied"]
    with factory() as db:
        assert db.query(ModelCostLiability).count() == 1
        assert sum(r.generation_attempt_count for r in db.query(AnalysisRun).all()) == 1
        assert sum(r.reserved_cost_micros for r in db.query(ModelCostLiability).all()) == hold


def test_two_owners_share_atomic_old_quote_cash_pool(pg_engine, monkeypatch):
    from backend.app.domains.employer import credits
    from backend.app.domains.employer.models import ServiceCreditReservation
    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    from fastapi import HTTPException

    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine)
    policy = estimated_expense_policy() | {
        "review_status": "approved",
        "reviewed_by": "local-test-review",
        "monthly_legacy_service_budget_minor": 600,
    }
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    with factory() as db:
        db.add_all(
            [User(id=i, email=f"old-{i}@example.com", job_service_credits=500) for i in (1, 2)]
        )
        db.commit()
    gate = Barrier(2)

    def attempt(i):
        with factory() as db:
            gate.wait(timeout=10)
            try:
                credits.reserve(
                    db,
                    user_id=i,
                    operation="job_application",
                    source_id=f"accepted-{i}",
                    unit_price=5,
                    count=1,
                    pricing_version="old",
                )
                db.commit()
                return "admitted"
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 503
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt, (1, 2)))
    assert sorted(outcomes) == ["admitted", "denied"]
    with factory() as db:
        row = db.query(ServiceCreditReservation).one()
        assert row.unit_price == 5 and row.cost_policy_snapshot["legacy_service_hold_minor"] == 600
        assert sum(u.job_service_credits for u in db.query(User)) == 995


def test_expense_snapshot_downgrade_preserves_paid_history(pg_engine):
    from backend.tests.test_employer_admissions_postgres import _migrate
    from sqlalchemy import text

    _migrate(pg_engine, "head")
    with pg_engine.begin() as db:
        db.execute(
            text("""INSERT INTO service_credit_reservations
          (id,operation,source_id,unit_price,requested_count,reserved_amount,committed_amount,released_amount,state,pricing_version,created_at,cost_policy_snapshot)
          VALUES ('legacy','job_application','old',5,1,5,0,0,'reserved','old',CURRENT_TIMESTAMP,'{"version":"accepted-review"}')""")
        )
    with pytest.raises(RuntimeError, match="financial history"):
        _migrate(pg_engine, "20261009_0012", direction="downgrade")
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261010_0014"
        assert (
            db.scalar(
                text(
                    "SELECT cost_policy_snapshot ->> 'version' FROM service_credit_reservations WHERE id='legacy'"
                )
            )
            == "accepted-review"
        )


def test_postgres_new_first_allocations_release_exactly_and_accept_later_topup(pg_engine):
    from backend.app.billing.cost_policy import current_service_funding
    from backend.app.domains.employer import credits
    from backend.app.domains.usage import service as usage
    from backend.tests.test_expense_budget import _accounting_run, _fund_bundle

    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    with factory() as db:
        db.add(User(id=1, email="pg-mixed@example.com", job_service_credits=600, ai_credits=12))
        _fund_bundle(db, reference="pg-first")
        db.commit()
        row = credits.reserve(
            db, user_id=1, operation="job_search", source_id="pg-500", unit_price=5, count=100
        )
        assert current_service_funding(db, 1) == 0
        credits.settle(db, row, completed_count=100, reason="Delivered")
        run = _accounting_run(db, "pg-mixed-5", 5)
        usage.reserve_run_usage(db, user_id=1, run=run, units=5)
        assert usage.current_analysis_funding(db, 1) == 0
        usage.release_run_usage(db, run, reason="No valid result")
        assert usage.current_analysis_funding(db, 1) == 2
        usage.reserve_run_usage(
            db, user_id=1, run=_accounting_run(db, "pg-committed-5", 5), units=5
        )
        db.commit()
        user = db.get(User, 1)
        user.job_service_credits += 100
        user.ai_credits += 2
        _fund_bundle(db, reference="pg-topup")
        db.commit()
        assert current_service_funding(db, 1) == 100
        assert usage.current_analysis_funding(db, 1) == 2


def test_postgres_two_paid_owners_share_finite_empty_search_preparation_pool(
    pg_engine, monkeypatch
):
    from backend.app.domains.employer import credits
    from backend.app.domains.employer.models import ServiceCreditReservation
    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    from backend.tests.test_expense_budget import _fund_bundle
    from fastapi import HTTPException

    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    monkeypatch.setenv(
        "HIREWIZ_EXPENSE_POLICY_JSON",
        json.dumps(
            estimated_expense_policy()
            | {
                "review_status": "approved",
                "reviewed_by": "local-test-review",
                "monthly_legacy_service_budget_minor": 60,
            }
        ),
    )
    with factory() as db:
        for owner in (1, 2):
            db.add(User(id=owner, email=f"paid-empty-{owner}@example.com", job_service_credits=100))
            _fund_bundle(db, reference=f"paid-empty-bundle-{owner}", user_id=owner)
        db.commit()
    gate = Barrier(2)

    def attempt(owner):
        with factory() as db:
            gate.wait(timeout=10)
            try:
                row = credits.reserve(
                    db,
                    user_id=owner,
                    operation="job_search",
                    source_id=f"empty-{owner}",
                    unit_price=1,
                    count=0,
                )
                credits.settle(db, row, completed_count=0, reason="No fresh matches")
                db.commit()
                return "admitted"
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 503
                return "denied"

    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(attempt, (1, 2))) == ["admitted", "denied"]
    with factory() as db:
        assert (
            db.query(ServiceCreditReservation)
            .one()
            .cost_policy_snapshot["failed_service_hold_minor"]
            == 60
        )
        assert sum(user.job_service_credits for user in db.query(User)) == 200
