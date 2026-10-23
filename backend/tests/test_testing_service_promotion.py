"""Synthetic ledger safety; no provider, production account or payment writes."""
from __future__ import annotations

import json

import pytest
from backend.app import models as core
from backend.app.billing.cost_policy import current_service_funding
from backend.app.database import Base
from backend.app.domains.employer import credits, models
from backend.app.domains.employer import testing_promotion as promotion
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

OPPORTUNITY = "opp_" + "a" * 32
OPERATOR = promotion.Operator("operator@example.invalid", "b" * 64)
REASON = "Owner-authorized noncommercial search acceptance testing"


def seed(factory):
    with factory() as db:
        db.add_all([core.User(id=1, email="owner@example.invalid", ai_credits=7,
                              tier="free", job_service_credits=0),
                    core.User(id=2, email=OPERATOR.email, ai_credits=11,
                              tier="premium", job_service_credits=0)])
        db.flush()
        db.add(core.Resume(id=28, user_id=1, original_filename="synthetic.pdf", raw_text="Synthetic"))
        db.flush()
        db.add(core.Opportunity(id=OPPORTUNITY, user_id=1, resume_id=28,
                                title="Synthetic engineer", company="Synthetic"))
        db.commit()


@pytest.fixture
def owned():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    seed(factory)
    try:
        yield factory
    finally:
        engine.dispose()


def grant(db, **change):
    values = dict(actor=OPERATOR, owner_id=1, opportunity_id=OPPORTUNITY,
                  campaign=promotion.CAMPAIGN, amount=3, reason=REASON,
                  accepted=True, idempotency_key="synthetic-owner-test-v1", correlation_id="test-1")
    return promotion.grant(db, **(values | change))


def test_actual_actor_and_unpaid_ledger_without_payment_or_ai_mutation(owned):
    with owned() as db:
        result = grant(db)
        db.commit()
        assert result["balance"] == 3 and result["unpaid"] is True
        owner, operator = db.get(core.User, 1), db.get(core.User, 2)
        assert (owner.ai_credits, owner.tier, operator.ai_credits, operator.tier) == (7, "free", 11, "premium")
        row = db.query(models.ServiceCreditEvent).one()
        assert (row.event_type, row.source_type, row.source_id, row.amount) == (
            "grant", "testing_promotion", promotion.CAMPAIGN, 3)
        audit = db.query(core.AdminAuditEvent).one()
        assert audit.actor_email == OPERATOR.email and audit.actor_user_id == 2
        assert audit.target_id == "1" and audit.before_state == {"job_service_credits": 0}
        assert audit.after_state["operator_native_identity_sha256"] == OPERATOR.native_identity_sha256
        assert audit.after_state["actual_work_admission_required"] is True
        assert current_service_funding(db, 1) == 0
        for model in (core.PaymentOrder, core.PaymentTransaction, core.EntitlementLedger,
                      core.UsageEvent, core.ModelCallEvent, core.NotificationOutbox):
            assert db.query(model).count() == 0


def test_exact_replay_has_one_event_and_audit(owned):
    with owned() as db:
        first = grant(db)
        db.commit()
        replay = grant(db)
        db.commit()
        assert replay == first | {"idempotent_replay": True}
        assert db.query(models.ServiceCreditEvent).count() == db.query(core.AdminAuditEvent).count() == 1
        assert promotion.review(db, actor=OPERATOR, owner_id=1,
                                opportunity_id=OPPORTUNITY)["status"] == "granted"


@pytest.mark.parametrize("change", [{"amount": 2}, {"reason": "Changed testing purpose"},
                                     {"campaign": "another-campaign"}, {"accepted": False},
                                     {"idempotency_key": "another-review-key"},
                                     {"actor": promotion.Operator("other@example.invalid", "c" * 64)}])
def test_replay_changed_terms_never_grants_again(owned, change):
    with owned() as db:
        grant(db)
        db.commit()
        with pytest.raises(HTTPException) as error:
            grant(db, **change)
        db.rollback()
        assert error.value.status_code == 409
        assert db.get(core.User, 1).job_service_credits == 3
        assert db.query(models.ServiceCreditEvent).count() == 1


@pytest.mark.parametrize("change", [{"amount": 4}, {"amount": 2}, {"amount": True},
                                     {"accepted": False}, {"campaign": "other"},
                                     {"idempotency_key": "bad key"}])
def test_campaign_cap_and_review_are_not_browser_controlled(owned, change):
    with owned() as db:
        with pytest.raises(HTTPException) as error:
            grant(db, **change)
        db.rollback()
        assert error.value.status_code == 422
        assert db.get(core.User, 1).job_service_credits == 0
        assert db.query(models.ServiceCreditEvent).count() == db.query(core.AdminAuditEvent).count() == 0


def test_exact_owner_resume_opportunity_binding_required(owned):
    with owned() as db:
        with pytest.raises(HTTPException) as error:
            grant(db, owner_id=2)
        assert error.value.status_code == 409
        assert db.get(core.User, 2).job_service_credits == 0


def test_campaign_history_survives_owner_unlink_and_blocks_other_owner(owned):
    with owned() as db:
        grant(db)
        db.commit()
        db.get(models.ServiceCreditEvent, promotion.EVENT_ID).user_id = None
        db.get(core.AdminAuditEvent, promotion.AUDIT_ID).actor_user_id = None
        db.commit()
        with pytest.raises(HTTPException) as error:
            grant(db)
        assert error.value.status_code == 409
        assert db.query(models.ServiceCreditEvent).one().amount == 3


def test_audit_failure_rolls_back_balance_and_ledger(owned):
    def refuse(_mapper, _connection, _target):
        raise RuntimeError("Synthetic audit refusal")

    event.listen(core.AdminAuditEvent, "before_insert", refuse)
    try:
        with owned() as db:
            with pytest.raises(RuntimeError, match="Synthetic audit refusal"):
                grant(db)
            db.rollback()
            assert db.get(core.User, 1).job_service_credits == 0
            assert db.query(models.ServiceCreditEvent).count() == 0
    finally:
        event.remove(core.AdminAuditEvent, "before_insert", refuse)


def test_promotion_search_still_reserves_finite_nonpayment_pool(owned):
    with owned() as db:
        grant(db)
        db.commit()
        reservation = credits.reserve(db, user_id=1, operation="job_search", source_id="synthetic-search",
                                      unit_price=1, count=3)
        assert reservation.cost_policy_snapshot["paid_current_reserved_credits"] == 0
        assert reservation.cost_policy_snapshot["paid_service_cost_count"] == 0
        assert reservation.cost_policy_snapshot["legacy_service_hold_minor"] > 0
        credits.settle(db, reservation, completed_count=2, reason="Two synthetic jobs delivered")
        db.commit()
        assert db.get(core.User, 1).job_service_credits == 1
        assert db.get(core.User, 1).ai_credits == 7


def test_exhausted_expense_pool_does_not_issue_free_credits(owned, monkeypatch):
    policy = synthetic_expense_policy() | {"monthly_legacy_service_budget_minor": 0}
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    with owned() as db:
        with pytest.raises(HTTPException) as error:
            grant(db)
        db.rollback()
        assert error.value.status_code == 503
        assert db.get(core.User, 1).job_service_credits == 0
        assert db.query(models.ServiceCreditEvent).count() == db.query(core.AdminAuditEvent).count() == 0


def test_retained_operator_does_not_require_or_expand_admin_emails(owned, monkeypatch):
    monkeypatch.delenv("ADMIN_EMAILS", raising=False)
    with owned() as db:
        assert grant(db)["balance"] == 3
        db.rollback()
