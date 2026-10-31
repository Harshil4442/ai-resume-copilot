"""Required owned-PostgreSQL wallet races; synthetic native ports, no employer IO.

The dedicated existing fixture validates its loopback target and creates one
invocation-owned schema. These controls do not establish live funding or native
acceptance. Never substitute SQLite for these concurrency assertions.
"""
from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from backend.app.domains.credits import service, submission
from backend.app.domains.credits.models import CreditExecutionHold, CreditReservation
from backend.app.domains.employer import models as employer
from backend.tests.browser_submission_unified_fixture import approve, configure, review
from backend.tests.test_browser_submit_postgres import candidate, device
from backend.tests.test_browser_submit_postgres import pg_submit as pg_submit


@pytest.fixture
def pg_unified(request, monkeypatch):
    if os.getenv("HIREWIZ_APP_COMPATIBILITY_REQUIRED") == "true" and not os.getenv("HIREWIZ_TEST_POSTGRES_URL"):
        pytest.fail("Unified browser submission requires its owned PostgreSQL URL")
    owned = request.getfixturevalue("pg_submit")
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "true")
    configure(owned, monkeypatch)
    return owned


def parallel(*actions):
    gate = Barrier(len(actions))

    def run(action):
        gate.wait(timeout=10)
        return action()

    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


def accept_action(owned, value):
    return candidate(owned, "approve", value["id"], confirmed=True,
        review_digest=value["review_digest"], accepted_service_quote_id=value["billing"]["quote_id"])


def assert_held(owned):
    owned.db.expire_all()
    row, hold = submission.held(owned.db, owned.app, pending=True)
    assert row.reserved_credits == 3 and row.committed_credits == row.released_credits == 0
    assert hold.native_reserved_minor == 13 and hold.native_settled_minor is None
    assert service.wallet(owned.db, user_id=1).available_credits == 37
    assert owned.db.query(CreditReservation).count() == owned.db.query(CreditExecutionHold).count() == 1
    assert owned.db.query(employer.ServiceCreditReservation).count() == 0


def test_parallel_acceptance_reserves_three_once_without_legacy_debit(pg_unified):
    value = review(pg_unified)
    pg_unified.db.rollback()
    results = parallel(lambda: accept_action(pg_unified, value), lambda: accept_action(pg_unified, value))
    assert sum(result.get("state") == "approved" for result in results) == 1
    assert_held(pg_unified)
    assert pg_unified.audit()["credits"] == 100 and pg_unified.audit()["native_calls"] == 0


def test_parallel_begin_exports_one_exact_five_lease_and_one_native_claim(pg_unified):
    value = review(pg_unified)
    approve(pg_unified, value)
    requests = [pg_unified.begin_request(value) for _ in range(2)]
    pg_unified.db.rollback()
    results = parallel(*(lambda r=r: device(pg_unified, r) for r in requests))
    leases = [result["lease"]["payload"] for result in results if result.get("state") == "begun"]
    assert len(leases) == 1
    assert leases[0]["review_protocol_version"] == 5 and leases[0]["billing"] == value["billing"]
    assert pg_unified.audit()["native_calls"] == pg_unified.audit()["submit_actions"] == 1
    assert_held(pg_unified)


def test_cancellation_during_native_claim_prevents_export_but_keeps_three_held(pg_unified):
    value = review(pg_unified)
    approve(pg_unified, value)
    request = pg_unified.begin_request(value)
    entered, release = Event(), Event()

    def pause():
        entered.set()
        assert release.wait(timeout=10)

    pg_unified.native.on_reserve = pause
    pg_unified.db.rollback()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(device, pg_unified, request)
        try:
            assert entered.wait(timeout=10)
            candidate(pg_unified, "cancel", value["id"], confirmed=True)
        finally:
            release.set()
        assert future.result(timeout=10) == {"state": "unknown"}
    assert_held(pg_unified)
    assert pg_unified.audit()["native_calls"] == 1


def test_parallel_preclaim_cancel_releases_three_once_and_never_releases_native_exposure(pg_unified):
    value = review(pg_unified)
    approve(pg_unified, value)
    pg_unified.db.rollback()
    results = parallel(*(lambda: candidate(pg_unified, "cancel", value["id"], confirmed=True) for _ in range(2)))
    assert all(result.get("state") == "cancelled" for result in results)
    pg_unified.db.expire_all()
    row, hold = submission.held(pg_unified.db, pg_unified.app)
    assert row.released_credits == 3 and row.committed_credits == 0
    assert service.wallet(pg_unified.db, user_id=1).available_credits == 40
    assert hold.delivery_state == "failed" and hold.native_settled_minor is None
    assert hold.reserved_minor == 13 and pg_unified.audit()["native_calls"] == 0
