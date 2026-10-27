"""Real PostgreSQL inline replay race; only synthetic provider output."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from backend.app import models
from backend.app.database import Base
from backend.app.domains.credits import service
from backend.app.domains.credits.models import CreditExecutionHold, CreditReservation
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from backend.tests.test_unified_synchronous import (
    configure,
    invoke,
    payload,
    provider,
    quote,
    setup,
)
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def factory():
    for engine in owned_pg_engine.__wrapped__():
        Base.metadata.create_all(engine)
        maker = sessionmaker(engine, autoflush=False)
        with maker() as db:
            db.add_all([models.User(id=1, email="sync-pg@example.invalid", ai_credits=17),
                        models.User(id=2, email="sync-other@example.invalid", ai_credits=17)])
            db.commit()
        yield maker


def test_concurrent_identical_inline_requests_cannot_generate_or_charge_twice(factory, monkeypatch):
    setup.__wrapped__(factory, monkeypatch)
    configure(monkeypatch, "enhanced_qa")
    calls = provider(monkeypatch, "enhanced_qa")
    request = payload("enhanced_qa")
    with factory() as db:
        quote_id = quote(db, request)
    ready = Barrier(2)
    def action(_):
        ready.wait(timeout=10)
        with factory() as db:
            try:
                return invoke(db, request, quote_id)
            except HTTPException as exc:
                db.rollback()
                return exc.detail
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(action, range(2)))
    assert len(calls) == 1
    assert sum(item.get("credit_execution", {}).get("charged_this_request", 0) for item in outcomes) == 2
    assert all("credit_execution" in item or item["code"] == "synchronous_service_in_use" for item in outcomes)
    with factory() as db:
        assert db.query(CreditReservation).count() == db.query(CreditExecutionHold).count() == 1
        assert db.query(DispatchOutbox).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 38
        assert db.get(models.User, 1).ai_credits == 17
