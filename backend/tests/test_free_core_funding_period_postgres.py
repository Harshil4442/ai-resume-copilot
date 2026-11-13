"""Actual owned local PostgreSQL races across renewed free funding reviews."""
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.domains.free_core import service
from backend.app.domains.free_core.models import FreeCoreReservation
from test_free_core import reserve
from test_free_core_funding_period import configure
from test_free_core_postgres import admitted, parallel
from test_free_core_postgres import factory as factory
from test_free_core_postgres import funded as funded


@pytest.mark.parametrize("same_period", [True, False])
def test_reviewed_period_allocation_is_atomic_across_two_owners(factory, funded, monkeypatch, same_period):
    stamp = datetime.now(UTC)
    review = configure(monkeypatch, funded, stamp=stamp - timedelta(minutes=3), allocation=40 if same_period else 20)
    with factory() as db:
        original, _ = reserve(db, key="earlier-settled-funding", now=stamp - timedelta(minutes=2))
        service.settle_measured_expense(db, reservation_id=original.reservation_id, actual_minor=20,
            receipt_sha256="b" * 64, now=stamp - timedelta(minutes=2))
        db.commit()
    period = review["funding_period_id"] if same_period else "separately-reviewed-funding-period"
    configure(monkeypatch, review, stamp=stamp - timedelta(minutes=1), period=period, version="renewed-pg-review-v2")
    outcomes = parallel(
        lambda: admitted(factory, owner=1, key="first-racing-owner"),
        lambda: admitted(factory, owner=2, key="second-racing-owner"),
    )
    assert sum(isinstance(value, str) for value in outcomes) == 1
    assert outcomes.count(503) == 1
    with factory() as db:
        assert db.query(FreeCoreReservation).count() == 2
        assert service.allocation_usage(db, funding_period_id=period) == (40 if same_period else 20)
