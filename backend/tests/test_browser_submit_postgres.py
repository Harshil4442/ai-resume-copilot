"""Owned local Postgres row-lock races; no native/provider transport is invoked.

The shared admission fixture permits only its dedicated loopback database and
creates/drops one unique schema per test. Native authority is explicitly synthetic.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from threading import Barrier, Event, get_ident

import pytest
from backend.app.database import Base
from backend.app.domains.browser_assistance import models, schemas, service
from backend.app.domains.employer import models as employer
from backend.tests.browser_submit_owned import OwnedSubmit
from backend.tests.test_employer_admissions_postgres import pg_engine as pg_engine
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_submit(pg_engine, monkeypatch):
    owned = OwnedSubmit(monkeypatch)
    source_factory = owned.factory
    source_engine = source_factory.kw["bind"]
    application_id = owned.app.id
    owned.db.commit()
    Base.metadata.create_all(pg_engine)
    # Copy only this synthetic fixture's populated rows through typed tables.
    with source_engine.connect() as source, pg_engine.begin() as target:
        for table in Base.metadata.sorted_tables:
            rows = [deepcopy(dict(row)) for row in source.execute(select(table)).mappings()]
            if rows:
                target.execute(table.insert(), rows)
    owned.db.close()
    source_engine.dispose()
    owned.factory = sessionmaker(bind=pg_engine, autoflush=False)
    owned.db = owned.factory()
    owned.app = owned.db.get(employer.EmployerApplication, application_id)
    owned.db.commit()
    try:
        yield owned
    finally:
        owned.db.close()


def candidate(owned, operation, identity, **values):
    with owned.factory() as db:
        try:
            return service.candidate(
                db, 1, operation, schemas.CandidateRequest(identity=identity, **values)
            )
        except HTTPException as exc:
            db.rollback()
            return {"rejected": exc.status_code}


def device(owned, request, sessions=None):
    with owned.factory() as db:
        if sessions is not None:
            sessions[get_ident()] = db
        try:
            return service.device(db, owned.origin, request)
        except HTTPException as exc:
            db.rollback()
            return {"rejected": exc.status_code}
        finally:
            if sessions is not None:
                sessions.pop(get_ident(), None)


def assert_one_held_attempt(owned, *, exported):
    audit = owned.audit()
    assert audit["credits"] == 80
    assert audit["attempts"] == audit["submit_actions"] == audit["native_calls"] == 1
    assert audit["charged_credits"] == 0 and audit["receipt"] is None
    assert audit["reservations"] == [
        {"state": "reserved", "reserved": 20, "committed": 0, "released": 0}
    ]
    action = owned.db.query(models.BrowserAssistanceRequest).filter_by(operation="submit").one()
    assert action.response["lease_exported"] is exported
    assert owned.db.query(employer.EmployerAdmission).count() == 1
    events = owned.db.query(employer.ServiceCreditEvent).all()
    assert len(events) == 1 and events[0].event_type == "reserve" and events[0].amount == -20
    owned.db.rollback()


def test_competing_exact_approvals_reserve_twenty_credits_only_once(pg_submit):
    owned = pg_submit
    review = owned.review()
    start = Barrier(2)

    def approve():
        start.wait(timeout=5)
        return candidate(
            owned, "approve", review["id"], confirmed=True, review_digest=review["review_digest"]
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.submit(approve), pool.submit(approve)
        replies = [first.result(timeout=10), second.result(timeout=10)]
    assert sum(reply.get("state") == "approved" for reply in replies) == 1
    assert sum(reply.get("rejected") == 409 for reply in replies) == 1
    audit = owned.audit()
    assert audit["credits"] == 80 and audit["native_calls"] == audit["attempts"] == 0
    assert audit["reservations"] == [
        {"state": "reserved", "reserved": 20, "committed": 0, "released": 0}
    ]
    assert owned.db.query(employer.EmployerAdmission).count() == 1
    assert owned.db.query(employer.ServiceCreditEvent).count() == 1


def test_competing_begins_export_one_lease_and_call_native_once(pg_submit):
    owned = pg_submit
    review = owned.approved()
    requests = [owned.begin_request(review), owned.begin_request(review)]
    start, native_started, release = Barrier(2), Event(), Event()
    sessions = {}

    def pause_native():
        assert not sessions[get_ident()].in_transaction(), "Native IO retained its SQL transaction"
        native_started.set()
        assert release.wait(5), "Owned native pause was not released"

    owned.native.on_reserve = pause_native

    def begin(request):
        start.wait(timeout=5)
        return device(owned, request, sessions)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(begin, request) for request in requests]
        try:
            assert native_started.wait(5)
        finally:
            release.set()
        replies = [future.result(timeout=10) for future in futures]
    assert sum(reply.get("state") == "begun" and "lease" in reply for reply in replies) == 1
    assert sum(reply.get("rejected") in {409, 503} for reply in replies) == 1
    assert_one_held_attempt(owned, exported=True)
    # Lost result delivery/replayed request never reconstructs the signed lease.
    replay = device(
        owned,
        requests[
            next(index for index, reply in enumerate(replies) if reply.get("state") == "begun")
        ],
    )
    assert replay["state"] == "unknown" and "lease" not in replay
    assert len(owned.native.calls) == 1


def test_cancel_commits_during_unlocked_native_io_and_prevents_lease_export(pg_submit):
    owned = pg_submit
    review = owned.approved()
    request = owned.begin_request(review)
    native_started, release = Event(), Event()
    sessions = {}

    def pause_native():
        assert not sessions[get_ident()].in_transaction(), "Native IO retained its SQL transaction"
        native_started.set()
        assert release.wait(5), "Owned native pause was not released"

    owned.native.on_reserve = pause_native
    with ThreadPoolExecutor(max_workers=2) as pool:
        begin = pool.submit(device, owned, request, sessions)
        try:
            assert native_started.wait(5)
            cancelled = pool.submit(candidate, owned, "cancel", review["id"], confirmed=True)
            # A retained owner/device/review SQL lock would block this commit.
            cancellation = cancelled.result(timeout=2)
            assert (
                cancellation["state"] == "cancelled"
                and cancellation["submission_status"] == "unknown"
            )
        finally:
            release.set()
        result = begin.result(timeout=10)
    assert result == {"state": "unknown"}
    assert_one_held_attempt(owned, exported=False)
    assert owned.db.get(employer.EmployerApplication, owned.app.id).cancel_requested is True
    owned.db.rollback()
    later = device(owned, owned.begin_request(review))
    assert later.get("rejected") in {409, 503} and "lease" not in later
    assert len(owned.native.calls) == 1
