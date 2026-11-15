"""Local PostgreSQL race and native-upload admission evidence; synthetic stores only."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace

import pytest
from backend.app.database import Base
from backend.app.domains.common import utcnow
from backend.app.domains.free_core import service as free_service
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.domains.resume_uploads import privacy, scanner, tasks, wake
from backend.app.domains.resume_uploads import service as uploads
from backend.app.domains.resume_uploads.models import ResumeUpload
from backend.app.models import User
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from backend.tests.test_free_core import reserve, synthetic_free_policy
from backend.tests.test_resume_upload_quarantine import FakeStore, admission, queue
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def funded(monkeypatch):
    values = synthetic_free_policy()
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(values))
    return values


@pytest.fixture
def factory():
    for engine in owned_pg_engine.__wrapped__():
        Base.metadata.create_all(engine)
        maker = sessionmaker(engine, autoflush=False)
        with maker() as db:
            db.add_all([User(id=1, email="free-1@example.invalid", ai_credits=0), User(id=2, email="free-2@example.invalid", ai_credits=0)])
            db.commit()
        yield maker


def parallel(*actions):
    ready = Barrier(len(actions))
    def run(action):
        ready.wait(timeout=10)
        return action()
    with ThreadPoolExecutor(max_workers=len(actions)) as pool:
        return list(pool.map(run, actions))


def admitted(factory, *, owner, key):
    with factory() as db:
        try:
            row, _ = reserve(db, key=key, owner=owner)
            identity = row.id
            db.commit()
            return identity
        except HTTPException as exc:
            db.rollback()
            return exc.status_code


def test_two_owners_share_one_atomic_free_cash_allowance(factory, funded, monkeypatch):
    funded["free_allocation_minor"] = 20
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(funded))
    result = parallel(lambda: admitted(factory, owner=1, key="one"), lambda: admitted(factory, owner=2, key="two"))
    assert sum(isinstance(value, str) for value in result) == 1
    assert result.count(503) == 1
    with factory() as db:
        assert db.query(FreeCoreReservation).count() == 1
        assert sum(row.ai_credits for row in db.query(User).all()) == 0


def test_tenth_free_comparison_cannot_be_double_admitted(factory, funded):
    with factory() as db:
        for number in range(9):
            reserve(db, key=f"earlier-{number}")
        db.commit()
    result = parallel(lambda: admitted(factory, owner=1, key="racing-one"), lambda: admitted(factory, owner=1, key="racing-two"))
    assert sum(isinstance(value, str) for value in result) == 1 and result.count(429) == 1
    with factory() as db:
        assert db.query(FreeCoreReservation).count() == 10


def test_exact_replay_race_keeps_one_financial_hold(factory, funded):
    result = parallel(lambda: admitted(factory, owner=1, key="same"), lambda: admitted(factory, owner=1, key="same"))
    assert isinstance(result[0], str) and result[0] == result[1]
    with factory() as db:
        assert db.query(FreeCoreReservation).count() == db.query(FreeCoreRequest).count() == 1


def test_owner_erasure_detaches_money_and_removes_private_bindings(factory, funded):
    admitted(factory, owner=1, key="erasable-request")
    with factory() as db:
        db.execute(text("DELETE FROM users WHERE id = 1"))
        db.commit()
        assert db.query(FreeCoreRequest).count() == 0
        expense = db.query(FreeCoreReservation).one()
        assert expense.user_id is None and expense.reserved_minor == 20


def test_native_upload_is_funded_before_capability_and_scan_and_replays_once(factory, funded, monkeypatch):
    monkeypatch.setenv("RESUME_DIRECT_UPLOAD_ENABLED", "true")
    store = FakeStore()
    seen = []
    def inspect(content, *, source_format):
        with factory() as db:
            assert db.query(FreeCoreReservation).one().reserved_minor == 20
        seen.append(content)
        return SimpleNamespace(parsed=("Synthetic", {"summary": "Synthetic"}, ["Python"], 1, {}), receipt=lambda: {"synthetic": True})
    monkeypatch.setattr(scanner, "inspect", inspect)
    identifier, _ = queue((factory, store))
    same, _ = admission((factory, store))
    assert same.upload_id == identifier
    assert tasks.process(factory, identifier, store)
    assert not tasks.process(factory, identifier, store)
    assert len(seen) == 1
    with factory() as db:
        assert db.query(FreeCoreReservation).count() == 1
        assert db.query(FreeCoreReservation).one().settled_minor is None
        assert db.query(FreeCoreRequest).one().state == "completed"
    with pytest.raises(HTTPException) as exc:
        admission((factory, store), key="second-upload")
    assert exc.value.status_code == 429


def test_missing_native_funding_never_signs_upload_or_starts_scan(factory, funded, monkeypatch):
    monkeypatch.setenv("RESUME_DIRECT_UPLOAD_ENABLED", "true")
    monkeypatch.delenv("FREE_CORE_POLICY_JSON", raising=False)
    store = FakeStore()
    with pytest.raises(HTTPException) as exc:
        admission((factory, store))
    assert exc.value.status_code == 503 and store.signs == 0
    with factory() as db:
        assert db.query(FreeCoreReservation).count() == 0


@pytest.fixture
def funded_upload(funded, monkeypatch):
    monkeypatch.setenv("RESUME_DIRECT_UPLOAD_ENABLED", "true")
    monkeypatch.setenv("RESUME_UPLOAD_TASK_WAKE_ENABLED", "true")
    monkeypatch.setattr(wake, "confirm", lambda *_: None)
    for engine in owned_pg_engine.__wrapped__():
        # Use the earliest released migration with funded reservations + existing upload tables.
        _migrate(engine, "20261027_0024")
        maker = sessionmaker(engine, autoflush=False)
        with maker() as db:
            db.add(User(id=1, email="funded-wake@example.invalid", ai_credits=17, job_service_credits=13))
            db.commit()
        yield maker, FakeStore()


@pytest.mark.parametrize("terminal", ["expired", "cancelled"])
def test_definitive_unclaimed_upload_allows_one_fresh_racing_admission(funded_upload, monkeypatch, terminal):
    factory, store = funded_upload
    def timeout(*_args):
        raise wake.WakeUnavailable
    with monkeypatch.context() as fail:
        fail.setattr(wake, "confirm", timeout)
        with pytest.raises(HTTPException) as error:
            admission(funded_upload, key="first")
        assert error.value.status_code == 503 and store.signs == 0
    with factory() as db:
        old = db.query(ResumeUpload).one()
        identifier = old.id
        if terminal == "expired":
            old.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
    # An awaiting intent still counts until the expiry/cancellation actually commits.
    with pytest.raises(HTTPException) as before:
        admission(funded_upload, key="still-counts")
    assert before.value.detail["code"] == "free_processing_account_limit"
    with factory() as db:
        if terminal == "cancelled":
            assert uploads.cancel(db, 1, identifier).state == "cancelled"
        else:
            assert privacy.sweep(db) == 1
    assert privacy.run_cleanup(factory, identifier, store)

    def fresh(key):
        try:
            return admission(funded_upload, key=key)[0].state
        except HTTPException as failure:
            assert failure.detail["code"] == "free_processing_account_limit"
            return failure.status_code
    outcomes = parallel(lambda: fresh("new-a"), lambda: fresh("new-b"))
    assert outcomes.count("awaiting_upload") == outcomes.count(429) == 1
    with factory() as db:
        requests = db.query(FreeCoreRequest).all()
        reservations = db.query(FreeCoreReservation).all()
        old = db.get(ResumeUpload, identifier)
        assert old.state == terminal and old.attempt_count == 0
        assert len(requests) == len(reservations) == 2
        assert all(row.state == "admitted" and row.result_id is None for row in requests)
        assert all(row.state == "reserved" and row.settled_minor is None for row in reservations)
        assert free_service.allocation_usage(db, funding_period_id=reservations[0].funding_period_id) == 40
        owner = db.get(User, 1)
        assert (owner.ai_credits, owner.job_service_credits) == (17, 13)


@pytest.mark.parametrize("policy_limit", ["global_30_day_operations", "free_allocation_minor"])
def test_unclaimed_terminal_upload_still_counts_in_global_funding(funded_upload, funded, monkeypatch, policy_limit):
    factory, _ = funded_upload
    first, _ = admission(funded_upload)
    with factory() as db:
        uploads.cancel(db, 1, first.upload_id)
    funded[policy_limit] = 1 if policy_limit == "global_30_day_operations" else 20
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(funded))
    with pytest.raises(HTTPException) as denied:
        admission(funded_upload, key="globally-denied")
    assert denied.value.status_code == 503 and denied.value.detail["code"] == "free_processing_unavailable"
    with factory() as db:
        assert db.query(FreeCoreRequest).count() == db.query(FreeCoreReservation).count() == 1


@pytest.mark.parametrize("binding", ["missing", "mismatch", "legacy", "unlinked", "started", "completed"])
def test_unproved_or_used_resume_reservations_still_count(funded_upload, monkeypatch, binding):
    factory, store = funded_upload
    if binding in {"started", "completed"}:
        identifier, _ = queue(funded_upload)
        if binding == "started":
            def interrupted(*_args):
                raise RuntimeError("synthetic interruption after claim")
            monkeypatch.setattr(store, "inspect_object", interrupted)
            with pytest.raises(RuntimeError, match="interruption after claim"):
                tasks.process(factory, identifier, store)
        else:
            monkeypatch.setattr(scanner, "inspect", lambda *_a, **_k: SimpleNamespace(
                parsed=("Synthetic", {}, [], 0, {}), receipt=lambda: {"synthetic": True}))
            assert tasks.process(factory, identifier, store)
    else:
        identifier = admission(funded_upload)[0].upload_id
    with factory() as db:
        if binding != "completed":
            uploads.cancel(db, 1, identifier)
        request = db.query(FreeCoreRequest).one()
        if binding == "missing":
            db.delete(db.get(ResumeUpload, identifier))
        elif binding == "mismatch":
            request.input_fingerprint = "f" * 64
        elif binding == "legacy":
            request.source_type = "legacy_parse"
        elif binding == "unlinked":
            db.execute(text("UPDATE resume_uploads SET user_id=NULL WHERE id=:id"), {"id": identifier})
        db.commit()
    with pytest.raises(HTTPException) as denied:
        admission(funded_upload, key="must-deny")
    assert denied.value.status_code == 429 and denied.value.detail["code"] == "free_processing_account_limit"
