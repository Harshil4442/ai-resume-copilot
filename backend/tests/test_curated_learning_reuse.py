"""Saved curated guidance is an owned read, not optional processing admission."""
from __future__ import annotations

import copy
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from backend.app import models
from backend.app.database import Base, get_db
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.credits.models import CreditReservation
from backend.app.domains.free_core import policy
from backend.app.domains.free_core import service as free_service
from backend.app.domains.free_core import synchronous as curated
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.routers import recommendations
from backend.app.security import get_current_user
from backend.app.services import llm_client, synchronous_contracts
from backend.tests.test_free_core import synthetic_free_policy
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory() as db:
        db.add_all([models.User(id=1, email="curated-owner@example.invalid", ai_credits=100),
                    models.User(id=2, email="other-curated@example.invalid")])
        db.add(models.Resume(id=1, user_id=1, skills=["Python"], experience_years=2))
        db.add(models.JobMatch(id=5, user_id=1, resume_id=1, job_title="Backend Engineer",
            company="Synthetic", job_description="Build Python and SQL services.",
            match_score=70, required_skills=["Python", "SQL"], true_gaps=["SQL"],
            partial_matches=[], improvement_tips=["Practice SQL queries"]))
        db.commit()

    def database():
        with factory() as db:
            yield db

    def owner(db=Depends(get_db)):
        return db.get(models.User, 1)

    app = FastAPI()
    app.include_router(recommendations.router)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    try:
        with TestClient(app) as client:
            yield client, factory, engine
    finally:
        engine.dispose()


@pytest.fixture
def funded(monkeypatch):
    values = synthetic_free_policy(free_allocation_minor=20)
    values["operations"]["curated_learning"] = {
        "route_id": "free.curated_learning.v1", "reserved_minor": 20,
        "max_input_bytes": 10000, "max_source_records": 100, "max_attempts": 1,
        "per_account_30_days": 4,
    }
    values["execution_budgets"]["monthly_route_minor"]["free.curated_learning.v1"] = 10000
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(values))
    return values


def create_plan(client):
    response = client.post("/recommendations/match_strategy", json={"match_id": 5, "mode": "curated"})
    assert response.status_code == 200, response.text
    assert response.json()["generated_by"] == "curated"
    return response.json()


def ledger(factory):
    with factory() as db:
        return {
            "runs": [(row.id, row.operation, row.input_fingerprint, row.result_payload, row.status)
                     for row in db.query(models.AnalysisRun).order_by(models.AnalysisRun.id)],
            "free_holds": [(row.id, row.reserved_minor, row.settled_minor, row.state)
                           for row in db.query(FreeCoreReservation)],
            "free_requests": db.query(FreeCoreRequest).count(),
            "paid_reservations": db.query(CreditReservation).count(),
            "model_liabilities": db.query(models.ModelCostLiability).count(),
            "usage_events": db.query(models.UsageEvent).count(),
            "credits": db.get(models.User, 1).ai_credits,
        }


@contextmanager
def read_only_lookup(engine, monkeypatch):
    statements = []

    def refuse(*_args, **_kwargs):
        pytest.fail("Saved lookup must not generate, reserve, lock, or inspect funding")

    def only_select(_connection, _cursor, statement, _parameters, _context, _many):
        assert statement.lstrip().upper().startswith("SELECT"), statement
        statements.append(statement)

    with monkeypatch.context() as patch:
        patch.setattr(policy, "enabled", refuse)
        patch.setattr(policy, "current_policy", refuse)
        patch.setattr(free_service, "admit", refuse)
        patch.setattr(curated, "curated_learning", refuse)
        patch.setattr(curated, "begin_result_commit", refuse)
        patch.setattr(curated, "lock_application_set", refuse)
        patch.setattr(curated.credits, "_lock_owner", refuse)
        patch.setattr(recommendations, "build_fallback_learning_strategy", refuse)
        patch.setattr(llm_client, "_chat", refuse)
        event.listen(engine, "before_cursor_execute", only_select)
        try:
            yield statements
        finally:
            event.remove(engine, "before_cursor_execute", only_select)


def test_missing_saved_curated_plan_is_a_null_read_not_a_creation(api, monkeypatch):
    client, factory, _ = api
    with read_only_lookup(api[2], monkeypatch):
        response = client.get("/recommendations/match_strategy/saved?match_id=5")
    assert response.status_code == 200
    assert response.json() is None
    with factory() as db:
        assert db.query(models.AnalysisRun).count() == 0


@pytest.mark.parametrize("pause", ["expired", "disabled", "exhausted"])
def test_funded_saved_plan_is_readable_without_current_optional_capacity(api, funded, monkeypatch, pause):
    client, factory, engine = api
    original = create_plan(client)
    before = ledger(factory)
    assert len(before["runs"]) == len(before["free_holds"]) == 1
    assert before["free_holds"][0][1] == funded["free_allocation_minor"]
    if pause == "expired":
        stamp = datetime.now(UTC)
        expired = funded | {"valid_from": (stamp - timedelta(days=2)).isoformat(),
                            "expires_at": (stamp - timedelta(days=1)).isoformat()}
        monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(expired))
    elif pause == "disabled":
        monkeypatch.setenv("FREE_CORE_ENABLED", "false")
        monkeypatch.delenv("FREE_CORE_POLICY_JSON")
    with read_only_lookup(engine, monkeypatch) as statements:
        response = client.get("/recommendations/match_strategy/saved?match_id=5")
    assert response.status_code == 200 and response.json() == original
    assert response.headers["cache-control"] == "private, no-store"
    assert ledger(factory) == before
    query = next(statement for statement in statements if "FROM analysis_runs" in statement)
    assert "analysis_runs.user_id =" in query and "analysis_runs.operation =" in query
    assert "analysis_runs.input_fingerprint =" in query and "LIMIT" in query


def test_context_builder_preserves_original_curated_hash(api, funded):
    client, factory, _ = api
    create_plan(client)
    with factory() as db:
        match, resume = db.get(models.JobMatch, 5), db.get(models.Resume, 1)
        original = {"match": {name: getattr(match, name) for name in (
            "id", "job_title", "company", "job_description", "match_score", "true_gaps",
            "partial_matches", "improvement_tips", "required_skills")},
            "resume_skills": resume.skills, "experience_years": float(resume.experience_years)}
        assert curated.curated_learning_context(match, resume) == original
        assert db.query(models.AnalysisRun).one().input_fingerprint == payload_fingerprint(
            {"contract": "curated-learning-v1", "context": original})


@pytest.mark.parametrize("change", ["skills", "experience", "description", "score"])
def test_changed_current_resume_or_match_does_not_reuse_old_plan(api, funded, monkeypatch, change):
    client, factory, engine = api
    create_plan(client)
    with factory() as db:
        if change == "skills":
            db.get(models.Resume, 1).skills = ["Python", "SQL"]
        elif change == "experience":
            db.get(models.Resume, 1).experience_years = 3
        elif change == "description":
            db.get(models.JobMatch, 5).job_description = "Different saved role requirements"
        else:
            db.get(models.JobMatch, 5).match_score = 80
        db.commit()
    before = ledger(factory)
    with read_only_lookup(engine, monkeypatch):
        response = client.get("/recommendations/match_strategy/saved?match_id=5")
    assert response.status_code == 200 and response.json() is None
    assert ledger(factory) == before


@pytest.mark.parametrize("match_id", [6, 999])
def test_foreign_and_missing_matches_are_the_same_owned_404(api, monkeypatch, match_id):
    client, factory, engine = api
    with factory() as db:
        db.add(models.JobMatch(id=6, user_id=2, job_title="Other owner's saved role"))
        db.commit()
    with read_only_lookup(engine, monkeypatch):
        response = client.get(f"/recommendations/match_strategy/saved?match_id={match_id}")
    assert response.status_code == 404
    assert response.json() == {"detail": "Job match not found for this user"}


@pytest.mark.parametrize("query", ["", "?match_id=0", "?match_id=-1", "?match_id=invalid"])
def test_saved_lookup_requires_positive_match_id(api, query):
    response = api[0].get("/recommendations/match_strategy/saved" + query)
    assert response.status_code == 422


def test_saved_links_receive_fresh_projection_without_mutating_stored_plan(api, funded, monkeypatch):
    client, factory, engine = api
    create_plan(client)
    with factory() as db:
        run = db.query(models.AnalysisRun).one()
        result = copy.deepcopy(run.result_payload)
        priority = result["learning_priorities"][0]
        priority["resources"] = [{"title": "Previously reviewed SQL guide", "platform": "Synthetic",
                                  "skill": priority["skill"], "url": "https://example.org/old-guide"}]
        run.result_payload = result
        db.commit()
    before = ledger(factory)
    monkeypatch.setattr(synchronous_contracts, "reviewed_learning_resources", lambda _skills: {})
    with read_only_lookup(engine, monkeypatch):
        response = client.get("/recommendations/match_strategy/saved?match_id=5")
    assert response.status_code == 200
    assert response.json()["learning_priorities"][0]["resources"][0]["url"] is None
    assert response.json()["readiness_summary"] == result["readiness_summary"]
    assert ledger(factory) == before


@pytest.mark.parametrize("change", ["owner", "operation", "running", "purged", "cancelled", "empty"])
def test_lookup_never_substitutes_other_or_unavailable_stored_runs(api, funded, monkeypatch, change):
    client, factory, engine = api
    create_plan(client)
    with factory() as db:
        run = db.query(models.AnalysisRun).one()
        if change == "owner":
            run.user_id = 2
        elif change == "operation":
            run.operation = "sync_learning_strategy"
        elif change == "running":
            run.status = "running"
        elif change == "purged":
            run.result_purged_at = datetime.now(UTC)
        elif change == "cancelled":
            run.cancel_requested = True
        else:
            run.result_payload = None
        db.commit()
    before = ledger(factory)
    with read_only_lookup(engine, monkeypatch):
        response = client.get("/recommendations/match_strategy/saved?match_id=5")
    assert response.status_code == 200 and response.json() is None
    assert ledger(factory) == before
