"""Concurrency evidence against the isolated loopback-only test database."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from backend.app.database import Base
from backend.app.domains.credits import pricing, service
from backend.app.domains.credits.models import (
    CreditAccount,
    CreditEvent,
    CreditGrant,
    CreditPurchaseQuote,
    CreditReservation,
)
from backend.app.models import User
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from backend.tests.test_unified_credits import NOW, grant, reserve
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def factory():
    for engine in owned_pg_engine.__wrapped__():
        Base.metadata.create_all(engine)
        maker = sessionmaker(engine, autoflush=False)
        with maker() as db:
            db.add(
                User(id=1, email="unified-pg@example.com", ai_credits=17, job_service_credits=13)
            )
            db.commit()
        yield maker


def parallel(*actions):
    gate = Barrier(len(actions))

    def run(action):
        gate.wait(timeout=10)
        return action()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


def test_parallel_spends_do_not_share_the_same_credits(factory):
    with factory() as db:
        grant(db, quantity=30)
        db.commit()

    def spend(number):
        with factory() as db:
            try:
                reserve(db, credits=20, source=f"run-{number}", key=f"request-{number}")
                db.commit()
                return "reserved"
            except service.InsufficientCredits:
                db.rollback()
                return "insufficient"

    assert sorted(parallel(lambda: spend(1), lambda: spend(2))) == ["insufficient", "reserved"]
    with factory() as db:
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 10
        assert db.query(CreditReservation).count() == 1
        assert db.query(CreditEvent).filter_by(event_type="reserve").count() == 1


def test_parallel_equivalent_requests_reserve_once(factory):
    with factory() as db:
        grant(db)
        db.commit()

    def same_request():
        with factory() as db:
            row = reserve(db)
            db.commit()
            return row.id

    ids = parallel(same_request, same_request)
    assert ids[0] == ids[1]
    with factory() as db:
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 32
        assert db.query(CreditReservation).count() == 1


def test_parallel_payment_grants_are_idempotent(factory):
    def fulfill():
        with factory() as db:
            row = grant(db)
            db.commit()
            return row.id

    ids = parallel(fulfill, fulfill)
    assert ids[0] == ids[1]
    with factory() as db:
        assert db.query(CreditGrant).count() == 1
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_quote_cannot_fund_two_orders_under_race(factory):
    with factory() as db:
        quote = service.issue_purchase_quote(
            db,
            user_id=1,
            sku="starter_pass",
            idempotency_key="quote-request",
            checkout_enabled=True,
            now=NOW,
        )
        db.commit()
        quote_id = quote.id

    def accept(order):
        with factory() as db:
            try:
                service.accept_purchase_quote(
                    db, user_id=1, quote_id=quote_id, order_id=order, now=NOW
                )
                db.commit()
                return "accepted"
            except pricing.CreditContractError:
                db.rollback()
                return "refused"

    assert sorted(parallel(lambda: accept("order-one"), lambda: accept("order-two"))) == [
        "accepted",
        "refused",
    ]
    with factory() as db:
        assert db.query(CreditPurchaseQuote).one().accepted_order_id in {"order-one", "order-two"}


def test_cash_refund_racing_failure_release_preserves_no_free_use(factory):
    with factory() as db:
        grant(db)
        row = reserve(db, credits=8)
        db.commit()
        reservation_id = row.id

    def refund():
        with factory() as db:
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=40,
                now=NOW,
            )
            db.commit()

    def settle():
        with factory() as db:
            service.settle_reservation(
                db, user_id=1, reservation_id=reservation_id, committed_credits=3, now=NOW
            )
            db.commit()

    parallel(refund, settle)
    with factory() as db:
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 0
        assert db.get(CreditAccount, 1).debt_credits == 3
        assert sum(event.amount for event in db.query(CreditEvent).all()) == -3
        assert db.query(CreditEvent).filter_by(event_type="revoke").count() == 1
        assert db.query(CreditEvent).filter_by(event_type="settle").count() == 1


def test_credit_half_rolls_back_when_money_admission_fails(factory):
    with factory() as db:
        grant(db)
        db.commit()
    with factory() as db:
        reserve(db)
        # The caller's monetary envelope admission refused before outbox/commit.
        db.rollback()
    with factory() as db:
        assert db.query(CreditReservation).count() == 0
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_preloaded_lot_is_refreshed_after_another_transaction_spends(factory):
    with factory() as db:
        grant(db)
        db.commit()
    with factory() as stale:
        cached = stale.query(CreditGrant).one()
        assert cached.remaining_credits == 40
        with factory() as first:
            reserve(first, source="first-run", key="first-request")
            first.commit()
        reserve(stale, source="second-run", key="second-request")
        stale.commit()
        assert cached.remaining_credits == 24
    with factory() as db:
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 24
        assert sum(event.amount for event in db.query(CreditEvent).all()) == 24


def test_whole_operation_global_money_serializes_across_different_accounts(factory):
    from backend.app.domains.credits.finance import FundingUnavailable
    from backend.app.domains.credits.models import CreditExecutionHold
    from backend.tests.test_unified_execution import admitted, budgets

    with factory() as db:
        db.add(
            User(id=2, email="unified-second-pg@example.com", ai_credits=0, job_service_credits=0)
        )
        db.flush()
        grant(db, owner=1)
        grant(db, owner=2)
        db.commit()

    def attempt(owner):
        with factory() as db:
            try:
                row = admitted(
                    db, owner=owner, source=f"run-{owner}", limits=budgets(daily_global_minor=27)
                )
                db.commit()
                return row.id
            except FundingUnavailable:
                db.rollback()
                return "no-money"

    result = parallel(lambda: attempt(1), lambda: attempt(2))
    assert result.count("no-money") == 1
    with factory() as db:
        assert db.query(CreditExecutionHold).count() == 1
        assert (
            sum(service.wallet(db, user_id=owner, now=NOW).available_credits for owner in (1, 2))
            == 72
        )
