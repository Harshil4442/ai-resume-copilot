from __future__ import annotations

import pytest
from backend.app.database import Base
from backend.app.models import AnalysisRun, UsageEvent, User
from backend.app.services.guardrails import billable_operation
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with factory() as db:
        db.add(User(id=1, email="legacy@example.com", ai_credits=10, tier="free"))
        db.commit()
    return engine, factory


def test_synchronous_success_commits_reserved_units():
    engine, factory = _database()
    try:
        with factory() as db:
            with billable_operation(
                user_id=1,
                db=db,
                operation="legacy_success",
                amount=2,
                input_payload={"owned_resource_id": 7},
            ):
                pass
        with factory() as db:
            run = db.query(AnalysisRun).one()
            assert run.status == "succeeded"
            assert run.usage_state == "committed"
            assert run.committed_units == 2
            assert db.get(User, 1).ai_credits == 8
            assert [event.event_type for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                "reserve",
                "commit",
            ]
    finally:
        engine.dispose()


def test_synchronous_failure_releases_reserved_units():
    engine, factory = _database()
    try:
        with factory() as db, pytest.raises(RuntimeError, match="provider unavailable"):
            with billable_operation(
                user_id=1,
                db=db,
                operation="legacy_failure",
                amount=2,
            ):
                raise RuntimeError("provider unavailable")
        with factory() as db:
            run = db.query(AnalysisRun).one()
            assert run.status == "failed"
            assert run.usage_state == "released"
            assert run.committed_units == 0
            assert db.get(User, 1).ai_credits == 10
            assert [event.event_type for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                "reserve",
                "release",
            ]
    finally:
        engine.dispose()
