"""Deterministic lifetime/money races on owned disposable PostgreSQL schemas."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Event, local
from time import monotonic, sleep

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from backend.app import models
from backend.app.domains.employer import admissions
from backend.app.services import generation_budget
from backend.app.services.generation_budget import persistent_run_budget
from backend.app.services.guardrails import billable_operation
from backend.app.services.model_cost_policy import ModelCostUnavailable
from backend.tests.test_ai_efficiency import _create
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_engine as owned_pg_engine
from backend.tests.test_model_cost_budgets import admit, usage
from backend.tests.test_model_cost_liabilities import erase, no_external_calls, seed
from fastapi import HTTPException
from sqlalchemy import event, inspect, text
from sqlalchemy.orm import sessionmaker

# The imported autouse fixture forbids actual provider and HTTP calls here too.
assert no_external_calls is not None


@pytest.fixture
def pg_engine():
    yield from owned_pg_engine.__wrapped__()


@pytest.fixture
def factory(pg_engine):
    return _factory_at_revision(pg_engine, "head")


def _factory_at_revision(pg_engine, revision):
    _migrate(pg_engine, revision)
    result = sessionmaker(bind=pg_engine, autoflush=False)
    seed(result)
    with result() as db:
        db.add(models.Resume(id=1, user_id=1, original_filename="synthetic.docx",
                             raw_text="Synthetic Python", skills=["Python"], sections={}))
        db.flush()
        db.add(models.Opportunity(id="opp_test", user_id=1, resume_id=1, title="Synthetic role",
                                  company="Synthetic", job_description="Python", job_snapshot={}))
        db.commit()
    return result


def test_postgres_reservation_before_erasure_retains_hold_without_provider_lock(factory, monkeypatch):
    entered, release = Event(), Event()
    original = generation_budget.quoted_cost
    def pause_quote(*args):
        entered.set()
        assert release.wait(8), "reservation gate was not released"
        return original(*args)
    monkeypatch.setattr(generation_budget, "quoted_cost", pause_quote)
    with persistent_run_budget(factory, "private_run_1") as budget, ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(admit, budget)
        assert entered.wait(5)
        deleted = pool.submit(erase, factory)
        with pytest.raises(TimeoutError):
            deleted.result(timeout=0.1)  # actual advisory/row lock contention
        release.set()
        record = future.result(timeout=8)
        deleted.result(timeout=8)
        with factory() as db:
            assert db.get(models.AnalysisRun, "private_run_1") is None
            row = db.query(models.ModelCostLiability).one()
            assert row.reserved_cost_micros == 1267 and row.settled_at is None
        # The provider work may now be in flight; deletion already completed,
        # so no DB transaction is held while waiting for a response.
        budget.finish(record, latency_ms=1, error=TimeoutError("synthetic lost provider reply"))
    with factory() as db:
        assert db.query(models.ModelCostLiability).one().cost_state == "outcome_unknown"


@pytest.mark.parametrize("action", ["admission", "settlement", "creation"])
def test_postgres_erasure_wins_then_current_settlement_only_or_new_work_denied(factory, pg_engine, monkeypatch, action):
    role = local()
    entered, release = Event(), Event()
    pids: dict[str, int] = {}
    original = admissions.lock_application_set

    def guard(db, user_id):
        original(db, user_id)
        if getattr(role, "name", None) == "delete":
            pids["holder"] = db.scalar(text("SELECT pg_backend_pid()"))
            entered.set()
            assert release.wait(8)

    def watch_waiter(_conn, cursor, statement, _params, _context, _many):
        if getattr(role, "name", None) == "waiter" and statement.startswith("SELECT pg_advisory_xact_lock"):
            # This fixture deliberately creates a privacy-transaction wait. Give
            # only this connection the same bounded lock window as the test.
            cursor.execute("SET LOCAL lock_timeout = '8s'")
            pids["waiter"] = cursor.connection.info.backend_pid

    monkeypatch.setattr(admissions, "lock_application_set", guard)

    def deletion():
        role.name = "delete"
        erase(factory)

    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget) if action == "settlement" else None

        def contend():
            role.name = "waiter"
            if action == "admission":
                return admit(budget)
            if action == "settlement":
                return budget.finish(record, latency_ms=1, response=usage())
            return _create(factory)

        def waiting_on_erasure() -> bool:
            with pg_engine.connect() as observer:
                return observer.scalar(text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE pid=:waiter AND wait_event_type='Lock' AND wait_event='advisory' "
                    "AND :holder=ANY(pg_blocking_pids(pid)))"
                ), {"waiter": pids.get("waiter"), "holder": pids["holder"]}) is True

        with ThreadPoolExecutor(max_workers=2) as pool:
            event.listen(pg_engine, "before_cursor_execute", watch_waiter)
            try:
                deleted = pool.submit(deletion)
                assert entered.wait(5)
                future = pool.submit(contend)
                deadline = monotonic() + 3
                while not waiting_on_erasure():
                    assert not future.done() and monotonic() < deadline, "Contender never waited on the erasure fence"
                    sleep(0.01)
            finally:
                release.set()
                event.remove(pg_engine, "before_cursor_execute", watch_waiter)
            deleted.result(timeout=8)
            if action == "settlement":
                future.result(timeout=8)
            else:
                with pytest.raises((ModelCostUnavailable, HTTPException)):
                    future.result(timeout=8)
    with factory() as db:
        assert db.query(models.User).count() == db.query(models.AnalysisRun).count() == 0
        assert db.query(models.ModelCallEvent).count() == 0
        if action == "settlement":
            assert db.query(models.ModelCostLiability).one().settled_cost_micros == 17
        else:
            assert db.query(models.ModelCostLiability).count() == 0


def test_postgres_settlement_before_erasure_survives_as_exactly_once_financial_evidence(factory, monkeypatch):
    entered, release = Event(), Event()
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
        original = generation_budget.quoted_cost
        def pause_quote(*args):
            entered.set()
            assert release.wait(8)
            return original(*args)
        monkeypatch.setattr(generation_budget, "quoted_cost", pause_quote)
        with ThreadPoolExecutor(max_workers=2) as pool:
            settled = pool.submit(budget.finish, record.copy(), latency_ms=1, response=usage())
            assert entered.wait(5)
            deleted = pool.submit(erase, factory)
            with pytest.raises(TimeoutError):
                deleted.result(timeout=0.1)
            release.set()
            settled.result(timeout=8)
            deleted.result(timeout=8)
        budget.finish(record, latency_ms=2, response=usage())
    with factory() as db:
        row = db.query(models.ModelCostLiability).one()
        assert row.settled_cost_micros == 17 and row.reserved_cost_micros == 1267
        assert db.query(models.AnalysisRun).count() == db.query(models.ModelCallEvent).count() == 0


def test_postgres_creation_before_erasure_is_included_in_scan(factory, monkeypatch):
    role = local()
    entered, release = Event(), Event()
    original = admissions.lock_application_set
    def guard(db, user_id):
        original(db, user_id)
        if getattr(role, "name", None) == "create":
            entered.set()
            assert release.wait(8)
    monkeypatch.setattr(admissions, "lock_application_set", guard)
    def create():
        role.name = "create"
        return _create(factory)
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = pool.submit(create)
        assert entered.wait(5)
        deleted = pool.submit(erase, factory)
        with pytest.raises(TimeoutError):
            deleted.result(timeout=0.1)
        release.set()
        assert created.result(timeout=8)[1] is True
        deleted.result(timeout=8)
    with factory() as db:
        assert db.query(models.AnalysisRun).count() == db.query(models.ModelCostLiability).count() == 0
        assert db.query(models.User).count() == 0


def test_postgres_parallel_duplicate_current_call_settlement_after_erasure_is_exactly_once(factory):
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
        erase(factory)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(budget.finish, record.copy(), latency_ms=1, response=usage()) for _ in range(3)]
            for future in futures:
                future.result(timeout=8)
    with factory() as db:
        row = db.query(models.ModelCostLiability).one()
        assert row.settled_cost_micros == 17 and row.reserved_cost_micros == 1267
        assert row.cost_state == "settled" and db.query(models.User).count() == 0


def test_postgres_retained_unknown_liability_refuses_downgrade_atomically(factory, pg_engine):
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
        budget.finish(record, latency_ms=1, error=TimeoutError("synthetic unknown"))
    erase(factory)
    with pg_engine.connect() as db:
        original_revision = db.scalar(text("SELECT version_num FROM alembic_version"))
    with pytest.raises(RuntimeError, match="Notification preference downgrade requires reviewed native-retirement evidence"):
        _migrate(pg_engine, "20261008_0009", direction="downgrade")
    # The current release stops at schema27. Exercise the historical monetary
    # guard directly against the retained liability without removing later fences.
    versions = ScriptDirectory(str(Path(__file__).resolve().parents[1] / "alembic"))
    with pg_engine.connect() as db, Operations.context(MigrationContext.configure(db)):
        with pytest.raises(RuntimeError, match="retained model-cost liabilities without loss"):
            versions.get_revision("20261009_0010").module.downgrade()
    with pg_engine.connect() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == original_revision
        assert db.scalar(text("SELECT reserved_cost_micros FROM model_cost_liabilities")) == 1267
        assert db.scalar(text("SELECT settled_cost_micros FROM model_cost_liabilities")) is None


def test_postgres_empty_liability_migration_roundtrip_preserves_unquoted_history(pg_engine):
    _migrate(pg_engine, "20261008_0009")
    _migrate(pg_engine, "20261028_0025")
    assert "model_cost_liabilities" in inspect(pg_engine).get_table_names()
    _migrate(pg_engine, "20261008_0009", direction="downgrade")
    assert "model_cost_liabilities" not in inspect(pg_engine).get_table_names()
    _migrate(pg_engine, "20261028_0025")
    assert "model_cost_liabilities" in inspect(pg_engine).get_table_names()


@pytest.mark.parametrize("outcome", ["success", "unknown"])
def test_postgres_synchronous_call_inflight_erasure_keeps_money_and_no_customer_state(factory, outcome):
    def execute():
        with factory() as db:
            with billable_operation(user_id=1, db=db, operation="match_question", amount=0,
                                    input_payload={"mode": "direct"}):
                budget = generation_budget.current_budget()
                record = admit(budget)
                # Actual independent DB deletion while the admitted SDK work
                # would be in flight, after all financial locks are released.
                with ThreadPoolExecutor(max_workers=1) as pool:
                    pool.submit(erase, factory).result(timeout=8)
                if outcome == "unknown":
                    budget.finish(record, latency_ms=1, error=TimeoutError("synthetic unknown reply"))
                    raise TimeoutError("synthetic caller failure")
                budget.finish(record, latency_ms=1, response=usage())
    if outcome == "unknown":
        with pytest.raises(TimeoutError, match="synthetic caller"):
            execute()
    else:
        execute()
    with factory() as db:
        row = db.query(models.ModelCostLiability).one()
        assert row.reserved_cost_micros == 1267
        assert row.settled_cost_micros == (17 if outcome == "success" else None)
        assert row.cost_state == ("settled" if outcome == "success" else "outcome_unknown")
        assert db.query(models.User).count() == db.query(models.AnalysisRun).count() == 0
        assert db.query(models.ModelCallEvent).count() == db.query(models.UsageEvent).count() == 0


def test_postgres_late_settlement_rollback_preserves_hold_until_trusted_retry(factory, pg_engine):
    with persistent_run_budget(factory, "private_run_1") as budget:
        record = admit(budget)
        budget.finish(record, latency_ms=1, error=TimeoutError("synthetic unknown"))
        erase(factory)
        def reject_update(connection, cursor, statement, parameters, context, executemany):
            if statement.startswith("UPDATE model_cost_liabilities"):
                raise RuntimeError("synthetic financial update failure")
        event.listen(pg_engine, "before_cursor_execute", reject_update)
        try:
            with pytest.raises(RuntimeError, match="synthetic financial"):
                budget.finish(record, latency_ms=2, response=usage())
        finally:
            event.remove(pg_engine, "before_cursor_execute", reject_update)
        with factory() as db:
            row = db.query(models.ModelCostLiability).one()
            assert row.reserved_cost_micros == 1267 and row.settled_at is None
            assert row.settled_cost_micros is None and row.cost_state == "outcome_unknown"
        budget.finish(record, latency_ms=3, response=usage())
        budget.finish(record, latency_ms=4, response=usage())
    with factory() as db:
        assert db.query(models.ModelCostLiability).one().settled_cost_micros == 17
        assert db.query(models.User).count() == 0
