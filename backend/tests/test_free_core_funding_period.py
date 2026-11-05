"""Fixed free allocations survive review renewal; all money here is synthetic."""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.domains.free_core import service
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.models import User
from fastapi import HTTPException
from test_free_core import database as database
from test_free_core import funded as funded
from test_free_core import reserve


def configure(monkeypatch, values, *, stamp, period=None, allocation=None, version=None):
    review = copy.deepcopy(values)
    review["valid_from"] = stamp.isoformat()
    if period is not None:
        review["funding_period_id"] = period
    if allocation is not None:
        review["free_allocation_minor"] = allocation
    if version is not None:
        review["version"] = version
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(review))
    return review


def test_review_renewal_cannot_spend_the_same_settled_allocation_twice(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    original = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=20)
    with database() as db:
        row, _ = reserve(db, key="original-funded-operation", now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp)
        db.commit()
    configure(monkeypatch, original, stamp=stamp + timedelta(seconds=1), version="reviewed-same-period-v2")
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, key="renewed-review-operation", now=stamp + timedelta(seconds=2))
        assert error.value.status_code == 503
        assert "allowance is fully allocated" in error.value.detail["message"]
        assert db.query(FreeCoreReservation).count() == 1
        assert db.query(FreeCoreReservation).one().settled_minor == 20


def test_partial_measured_cost_uses_actual_without_resetting_on_review_renewal(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=28)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=8,
            receipt_sha256="b" * 64, now=stamp)
        db.commit()
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), version="partial-cost-review-v2")
    with database() as db:
        reserve(db, key="remaining-funded-envelope", now=stamp + timedelta(seconds=2))
        assert service.allocation_usage(db, funding_period_id=review["funding_period_id"]) == 28
        db.commit()
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, key="exceeds-measured-remainder", now=stamp + timedelta(seconds=3))
        assert error.value.status_code == 503


@pytest.mark.parametrize("same_period", [True, False])
def test_old_unknown_cost_carries_forward_once_in_same_or_new_period(database, funded, monkeypatch, same_period):
    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=40)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        db.get(FreeCoreReservation, row.reservation_id).created_at = stamp - timedelta(days=40)
        db.commit()
    period = review["funding_period_id"] if same_period else "separately-reviewed-next-period"
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), period=period, version="carry-forward-review-v2")
    with database() as db:
        assert service.allocation_usage(db, funding_period_id=period) == 20
        reserve(db, key="one-remaining-envelope", now=stamp + timedelta(seconds=2))
        assert service.allocation_usage(db, funding_period_id=period) == 40
        db.commit()
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, key="no-second-remaining-envelope", now=stamp + timedelta(seconds=3))
        assert error.value.status_code == 503


def test_settled_other_period_is_not_charged_against_reviewed_new_allocation(database, funded, monkeypatch):
    from backend.app.domains.credits import admission

    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=20)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp)
        db.commit()
    period = "separately-reviewed-next-period"
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), period=period, version="new-funded-review-v2")
    with database() as db:
        assert service.allocation_usage(db, funding_period_id=period) == 0
        reserve(db, key="new-period-first-envelope", now=stamp + timedelta(seconds=2))
        assert service.allocation_usage(db, funding_period_id=period) == 20
        # Neither allocation classification nor review renewal removes actual
        # expenses from the independently enforced common execution budget.
        assert admission._hold_usage(db, start=stamp - timedelta(days=1)) == 40


def test_new_funding_period_does_not_reset_rolling_global_operation_quota(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    funded["global_30_day_operations"] = 1
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=20)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp)
        db.add(User(id=2, email="second-free-owner@example.invalid"))
        db.commit()
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), period="reviewed-next-period")
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, owner=2, key="other-owner-rolling-quota", now=stamp + timedelta(seconds=2))
        assert error.value.status_code == 503
        assert "capacity is fully allocated" in error.value.detail["message"]


def test_new_funding_period_does_not_reset_one_parse_per_account_quota(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=20)
    with database() as db:
        row, _ = reserve(db, operation="resume_parse", now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp)
        db.commit()
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), period="reviewed-next-period")
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, operation="resume_parse", key="second-parse", now=stamp + timedelta(seconds=2))
        assert error.value.status_code == 429
        assert error.value.detail["code"] == "free_processing_account_limit"


def test_new_period_is_not_permission_to_bypass_the_common_cash_budget(database, funded, monkeypatch):
    stamp = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    funded["execution_budgets"]["daily_global_minor"] = 20
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=20)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp)
        db.commit()
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), period="reviewed-next-period")
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, key="still-needs-common-headroom", now=stamp + timedelta(seconds=2))
        assert error.value.status_code == 503
        assert "global execution budget" in error.value.detail["message"]


def test_detached_settled_expense_still_consumes_the_same_period(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=20)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp)
        db.get(FreeCoreReservation, row.reservation_id).user_id = None
        db.delete(row)
        db.add(User(id=2, email="new-free-owner@example.invalid"))
        db.commit()
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), version="after-erasure-review-v2")
    with database() as db:
        with pytest.raises(HTTPException) as error:
            reserve(db, owner=2, key="detached-money-counts", now=stamp + timedelta(seconds=2))
        assert error.value.status_code == 503
        assert db.query(FreeCoreRequest).count() == 0
        assert service.allocation_usage(db, funding_period_id=review["funding_period_id"]) == 20


def test_measured_overrun_remains_spent_after_policy_version_renewal(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=1), allocation=40)
    with database() as db:
        row, _ = reserve(db, now=stamp)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=21,
            receipt_sha256="b" * 64, now=stamp)
        db.commit()
    configure(monkeypatch, review, stamp=stamp + timedelta(seconds=1), version="reviewed-overrun-v2")
    with database() as db:
        assert service.allocation_usage(db, funding_period_id=review["funding_period_id"]) == 21
        with pytest.raises(HTTPException) as error:
            reserve(db, key="overrun-not-forgotten", now=stamp + timedelta(seconds=2))
        assert error.value.status_code == 503
