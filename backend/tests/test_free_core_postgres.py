"""Local PostgreSQL race and native-upload admission evidence; synthetic stores only."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from types import SimpleNamespace

import pytest
from backend.app.database import Base
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.domains.resume_uploads import scanner, tasks
from backend.app.models import User
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
