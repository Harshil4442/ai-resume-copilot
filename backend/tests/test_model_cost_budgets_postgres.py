"""Actual PostgreSQL locking/crash/migration proof on a local disposable schema."""
from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest
from backend.app import models
from backend.app.services.generation_budget import persistent_run_budget
from backend.app.services.model_cost_policy import (
    ModelCostUnavailable,
    input_token_estimate,
    quoted_cost,
)
from backend.tests.model_cost_fixtures import synthetic_policy
from backend.tests.test_employer_admissions_postgres import _migrate
from backend.tests.test_employer_admissions_postgres import pg_engine as admission_pg_engine
from backend.tests.test_model_cost_budgets import MESSAGES, admit, new_run, usage
from sqlalchemy import inspect, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_engine():
    yield from admission_pg_engine.__wrapped__()


@pytest.fixture
def pg_cost_context(pg_engine):
    _migrate(pg_engine, "head")
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    with factory() as db:
        db.add(models.User(id=1, email="synthetic-model-cost@example.com", ai_credits=100))
        db.commit()
    return factory


def test_postgres_simultaneous_reservations_accept_exactly_one_affordable_attempt(pg_cost_context):
    factory = pg_cost_context
    price = synthetic_policy()["pricing_quotes"][0]
    hold = quoted_cost(price, input_token_estimate(MESSAGES), price["max_output_tokens"])
    run_id = new_run(factory, ceiling=hold, attempts=6)
    start = Barrier(2)
    def attempt():
        with persistent_run_budget(factory, run_id) as budget:
            start.wait(timeout=10)
            try:
                admit(budget)
                return "accepted"
            except ModelCostUnavailable:
                return "denied"
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: attempt(), range(2)))
    assert sorted(results) == ["accepted", "denied"]
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert run.generation_attempt_count == 1
        assert run.model_cost_reserved_micros == hold
        assert db.query(models.ModelCallEvent).count() == 1
        assert db.get(models.User, 1).ai_credits == 100


def test_postgres_concurrent_attempt_count_is_shared_across_worker_contexts(pg_cost_context):
    factory = pg_cost_context
    run_id = new_run(factory)
    start = Barrier(5)
    def attempt():
        with persistent_run_budget(factory, run_id) as budget:
            start.wait(timeout=10)
            try:
                return admit(budget)["attempt_number"]
            except ModelCostUnavailable:
                return 0
    with ThreadPoolExecutor(max_workers=5) as workers:
        results = list(workers.map(lambda _: attempt(), range(5)))
    assert sorted(results) == [0, 0, 1, 2, 3]
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        events = db.query(models.ModelCallEvent).all()
        assert run.generation_attempt_count == len(events) == 3
        assert run.model_cost_reserved_micros == sum(e.reserved_cost_micros for e in events)


def test_postgres_process_exit_after_admission_retains_hold_and_denies_retry(pg_cost_context, pg_engine):
    factory = pg_cost_context
    price = synthetic_policy()["pricing_quotes"][0]
    hold = quoted_cost(price, input_token_estimate(MESSAGES), price["max_output_tokens"])
    run_id = new_run(factory, ceiling=hold, attempts=6)
    with pg_engine.connect() as connection:
        schema = connection.scalar(text("SELECT current_schema()"))
    script = """
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.app.services.generation_budget import persistent_run_budget
engine = create_engine(os.environ['HIREWIZ_TEST_POSTGRES_URL'], connect_args={'options': '-csearch_path=' + os.environ['HIREWIZ_COST_TEST_SCHEMA']})
factory = sessionmaker(bind=engine, autoflush=False)
with persistent_run_budget(factory, 'run_cost') as budget:
    budget.admit('google', 'gemini-3.6-flash', [{'role': 'user', 'content': 'Synthetic Python role'}], api_base='https://generativelanguage.googleapis.com')
    os._exit(17)
"""
    env = os.environ.copy()
    env["HIREWIZ_COST_TEST_SCHEMA"] = schema
    backend_dir = Path(__file__).resolve().parents[1]
    env["PYTHONPATH"] = str(backend_dir.parent)
    result = subprocess.run([sys.executable, "-c", script], cwd=backend_dir, env=env, capture_output=True, timeout=30)
    assert result.returncode == 17, result.stderr.decode()
    with persistent_run_budget(factory, run_id) as budget:
        with pytest.raises(ModelCostUnavailable, match="estimated spend"):
            admit(budget)
    with factory() as db:
        assert db.get(models.AnalysisRun, run_id).model_cost_reserved_micros == hold
        assert db.query(models.ModelCallEvent).one().cost_state == "reserved"


def test_postgres_concurrent_duplicate_usage_settles_exactly_once(pg_cost_context):
    factory = pg_cost_context
    run_id = new_run(factory)
    with persistent_run_budget(factory, run_id) as budget:
        record = admit(budget)
    start = Barrier(2)
    def settle():
        with persistent_run_budget(factory, run_id) as budget:
            start.wait(timeout=10)
            budget.finish(record.copy(), latency_ms=5, response=usage())
    with ThreadPoolExecutor(max_workers=2) as workers:
        list(workers.map(lambda _: settle(), range(2)))
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert run.model_cost_reserved_micros == 0
        assert run.model_cost_settled_micros == 17
        assert db.query(models.ModelCallEvent).one().settled_cost_micros == 17


def test_postgres_legacy_populated_migration_roundtrip_keeps_history_unquoted(pg_engine):
    _migrate(pg_engine, "20261008_0009")
    with pg_engine.begin() as connection:
        connection.execute(text("INSERT INTO users (id, email, ai_credits, job_service_credits) VALUES (1, 'synthetic-legacy@example.com', 100, 0)"))
        connection.execute(text("""INSERT INTO analysis_runs
            (id,user_id,operation,status,idempotency_key,input_fingerprint,input_payload,estimated_units,
             committed_units,usage_state,attempt_count,generation_attempt_limit,generation_attempt_count,
             cancel_requested,created_at,updated_at)
            VALUES ('run_cost',1,'job_match','running','legacy-cost','legacy','{}',1,0,'reserved',1,3,1,
                    false,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"""))
        connection.execute(text("""INSERT INTO model_call_events
            (id,analysis_run_id,user_id,provider,model,prompt_version,input_tokens,output_tokens,tokens_estimated,
             attempt_number,latency_ms,estimated_cost_micros,status,created_at)
            VALUES ('mdl_legacy','run_cost',1,'google','gemini-3.6-flash','legacy',10,0,true,1,0,0,'attempted',CURRENT_TIMESTAMP)"""))
    _migrate(pg_engine, "head")
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    with persistent_run_budget(factory, "run_cost") as budget:
        with pytest.raises(ModelCostUnavailable, match="legacy"):
            admit(budget)
    with factory() as db:
        run = db.get(models.AnalysisRun, "run_cost")
        event = db.get(models.ModelCallEvent, "mdl_legacy")
        assert run.generation_attempt_count == 1 and run.model_cost_quote is None
        assert event.estimated_cost_micros == 0 and event.cost_state == "unavailable"
    _migrate(pg_engine, "20261008_0009", direction="downgrade")
    assert "model_cost_quote" not in {c["name"] for c in inspect(pg_engine).get_columns("analysis_runs")}
    _migrate(pg_engine, "head")
    with factory() as db:
        assert db.get(models.AnalysisRun, "run_cost").generation_attempt_count == 1


def test_postgres_downgrade_refuses_high_value_history_without_truncation(pg_cost_context, pg_engine):
    factory = pg_cost_context
    run_id = new_run(factory)
    with persistent_run_budget(factory, run_id) as budget:
        record = admit(budget)
    with factory() as db:
        event = db.get(models.ModelCallEvent, record["event_id"])
        event.estimated_cost_micros = 2_147_483_648
        db.commit()
    with pytest.raises(RuntimeError, match="without loss"):
        _migrate(pg_engine, "20261008_0009", direction="downgrade")
    with factory() as db:
        assert db.get(models.ModelCallEvent, record["event_id"]).estimated_cost_micros == 2_147_483_648
        assert db.get(models.AnalysisRun, run_id).model_cost_quote is not None
    with pg_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261009_0011"
