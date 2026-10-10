"""Detached financial holds survive real erasure; all inputs/prices are synthetic."""
from __future__ import annotations

import json
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest
import requests
from backend.app import models
from backend.app.database import Base
from backend.app.domains.operations import maintenance
from backend.app.routers.auth import delete_account
from backend.app.services import generation_budget
from backend.app.services.generation_budget import persistent_run_budget
from backend.app.services.guardrails import billable_operation
from backend.app.services.model_cost_policy import ModelCostUnavailable, freeze_run_quote
from backend.tests.test_model_cost_budgets import GOOGLE, admit, usage
from fastapi import Request
from google import genai
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def no_external_calls(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("Liability tests must not call providers or construct SDKs")
    monkeypatch.setattr(genai, "Client", deny)
    monkeypatch.setattr(httpx.Client, "send", deny)
    monkeypatch.setattr(requests.Session, "request", deny)


def seed(factory, *, other_owner=False):
    with factory() as db:
        db.add(models.User(id=1, email="synthetic-private@example.com", ai_credits=100))
        if other_owner:
            db.add(models.User(id=2, email="synthetic-other@example.com", ai_credits=100))
        db.commit()
        for identity in ([1, 2] if other_owner else [1]):
            run = models.AnalysisRun(
                id=f"private_run_{identity}", user_id=identity, operation="job_match", status="running",
                idempotency_key=f"private_request_{identity}", input_fingerprint="private-fingerprint",
                input_payload={"resume": "synthetic-private-resume", "prompt": "synthetic-private-prompt"},
            )
            freeze_run_quote(run)
            db.add(run)
        db.commit()


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    @event.listens_for(engine, "connect")
    def enable_fk(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    result = sessionmaker(bind=engine, autoflush=False)
    seed(result, other_owner=True)
    try:
        yield result
    finally:
        engine.dispose()


def erase(factory):
    with factory() as db:
        # Legacy financial fixtures have no protected CandidatePasswordAccount.
        request = Request({"type": "http", "method": "POST", "scheme": "https", "path": "/api/auth/delete-account", "raw_path": b"/api/auth/delete-account", "query_string": b"", "headers": [], "server": ("owned-test.invalid", 443), "client": ("127.0.0.1", 12345)})
        assert delete_account(request=request, db=db, current_user=db.get(models.User, 1)) == {"status": "deleted"}


@pytest.mark.parametrize("state", ["reserved", "outcome_unknown", "usage_unavailable"])
def test_erasure_retains_full_unknown_hold_and_late_current_call_settles_once(factory, state):
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
        if state == "outcome_unknown":
            budget.finish(record, latency_ms=1, error=TimeoutError("synthetic-private-provider-error"))
        elif state == "usage_unavailable":
            budget.finish(record, latency_ms=1, response={})
        with factory() as db:
            event_row = db.get(models.ModelCallEvent, record["event_id"])
            liability_id, original = event_row.liability_id, event_row.reserved_cost_micros
            assert original == 1267
        erase(factory)
        with factory() as db:
            assert db.get(models.User, 1) is db.get(models.AnalysisRun, "private_run_1") is None
            assert db.get(models.ModelCallEvent, record["event_id"]) is None
            liability = db.get(models.ModelCostLiability, liability_id)
            assert liability.cost_state == state and liability.reserved_cost_micros == original
            assert liability.settled_at is liability.settled_cost_micros is None
            assert db.get(models.User, 2) is not None
        with pytest.raises(ModelCostUnavailable, match="no longer authorized"):
            admit(budget)
        budget.finish(record.copy(), latency_ms=2, response=usage())
        budget.finish(record, latency_ms=3, response=usage())
        budget.finish(record, latency_ms=4, error=TimeoutError("late duplicate"))
    with factory() as db:
        liability = db.get(models.ModelCostLiability, liability_id)
        assert liability.settled_cost_micros == 17 and liability.cost_state == "settled"
        assert liability.reserved_cost_micros == original and liability.settled_at is not None
        assert db.get(models.User, 1) is db.get(models.AnalysisRun, "private_run_1") is None
        assert db.query(models.ModelCallEvent).count() == 0


def test_retained_ledger_has_exact_financial_whitelist_and_no_owner_foreign_keys(factory):
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
    erase(factory)
    with factory() as db:
        row = db.query(models.ModelCostLiability).one()
        assert set(row.pricing_quote) == set(generation_budget.LIABILITY_QUOTE_FIELDS)
        data = {column.name: getattr(row, column.name) for column in row.__table__.columns}
        encoded = json.dumps(data, default=str)
        for private in ("synthetic-private", "private_run_1", "private_request_1", "private-fingerprint",
                        record["event_id"], GOOGLE, "api_base", "user_id", "prompt_version"):
            assert private not in encoded
        assert row.financial_group_id.startswith("fin_") and row.id.startswith("mcl_")
        assert row.currency == "USD" and len(row.endpoint_key) == 64
        assert not inspect(db.get_bind()).get_foreign_keys("model_cost_liabilities")


def test_other_owner_attempt_cannot_settle_through_current_or_deleted_budget(factory):
    with persistent_run_budget(factory, "private_run_1") as first:
        own = admit(first)
    with persistent_run_budget(factory, "private_run_2") as second:
        other = admit(second)
    with pytest.raises(ModelCostUnavailable, match="does not belong"):
        first.finish(other.copy(), latency_ms=1, response=usage())
    erase(factory)
    with pytest.raises(ModelCostUnavailable, match="does not belong"):
        first.finish(other.copy(), latency_ms=1, response=usage())
    changed = {**own, "model": "different-model"}
    with pytest.raises(ModelCostUnavailable, match="does not belong"):
        first.finish(changed, latency_ms=1, response=usage())
    with factory() as db:
        assert all(row.settled_at is None for row in db.query(models.ModelCostLiability))


def test_failed_reservation_transaction_cannot_leave_detached_hold(factory):
    engine = factory.kw["bind"]
    def reject_event(connection, cursor, statement, parameters, context, executemany):
        if statement.startswith("INSERT INTO model_call_events"):
            raise RuntimeError("synthetic event insert failure")
    event.listen(engine, "before_cursor_execute", reject_event)
    try:
        with persistent_run_budget(factory, "private_run_1") as budget:
            with pytest.raises(RuntimeError, match="synthetic event"):
                admit(budget)
    finally:
        event.remove(engine, "before_cursor_execute", reject_event)
    with factory() as db:
        assert db.query(models.ModelCostLiability).count() == db.query(models.ModelCallEvent).count() == 0
        run = db.get(models.AnalysisRun, "private_run_1")
        assert run.generation_attempt_count == run.model_cost_reserved_micros == 0


def test_model_telemetry_pruning_never_removes_hold_or_blocks_current_call_settlement(factory, monkeypatch):
    monkeypatch.setattr(maintenance, "queue_due_reminder_emails", lambda db: 0)
    monkeypatch.setattr(maintenance, "deliver_pending_notifications", lambda db: SimpleNamespace(to_dict=lambda: {}))
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
        with factory() as db:
            db.get(models.ModelCallEvent, record["event_id"]).created_at -= timedelta(days=500)
            db.commit()
            assert maintenance.run_maintenance(db).model_events_deleted == 1
            assert db.query(models.ModelCostLiability).one().settled_at is None
        budget.finish(record, latency_ms=1, response=usage())
    with factory() as db:
        assert db.query(models.ModelCostLiability).one().settled_cost_micros == 17
        assert db.get(models.AnalysisRun, "private_run_1").model_cost_reserved_micros == 0


def test_no_price_policy_or_unscoped_attempt_never_creates_financial_evidence(factory, monkeypatch):
    monkeypatch.delenv("LLM_MODEL_COST_POLICY_JSON")
    with persistent_run_budget(factory, "private_run_1") as budget:
        with pytest.raises(ModelCostUnavailable):
            admit(budget)
    with factory() as db:
        assert db.query(models.ModelCostLiability).count() == 0


def test_synchronous_billable_context_after_erasure_settles_money_without_customer_state(factory):
    with factory() as db:
        with billable_operation(user_id=1, db=db, operation="match_question", amount=0,
                                input_payload={"mode": "direct"}):
            budget = generation_budget.current_budget()
            record = admit(budget)
            erase(factory)
            budget.finish(record, latency_ms=1, response=usage())
    with factory() as db:
        assert db.get(models.User, 1) is None
        assert db.query(models.AnalysisRun).filter_by(user_id=1).count() == 0
        assert db.query(models.ModelCallEvent).count() == 0
        assert db.query(models.ModelCostLiability).one().settled_cost_micros == 17
