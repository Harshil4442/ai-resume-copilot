"""Owned learning-link delivery controls; synthetic reviews, no resource fetching."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.routers import recommendations
from app.routers.v1 import analysis
from app.security import get_current_user
from app.services import llm_client

DOCKER_URL = "https://docs.docker.com/get-started/"


@pytest.fixture
def owned(monkeypatch):
    monkeypatch.setenv("FREE_CORE_ENABLED", "false")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.delenv("LEARNING_RESOURCE_REVIEW_JSON", raising=False)

    def denied(*_args, **_kwargs):
        pytest.fail("Learning resource delivery must not generate or fetch resources")

    monkeypatch.setattr(llm_client, "chat_json", denied)
    monkeypatch.setattr(llm_client, "_chat", denied)
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    models.Base.metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False, expire_on_commit=False)
    with factory() as db:
        db.add(models.User(id=1, email="learning-owner@example.invalid", ai_credits=17))
        db.add(
            models.Resume(
                id=1,
                user_id=1,
                skills=["Python"],
                raw_text="Python engineer",
                sections={},
                experience_years=1,
            )
        )
        db.flush()
        db.add(
            models.JobMatch(
                id=1,
                user_id=1,
                resume_id=1,
                job_title="Backend Engineer",
                company="Example",
                job_description="Build reliable Python and Docker services.",
                true_gaps=["Docker"],
                required_skills=["Docker"],
                partial_matches=[],
                improvement_tips=[],
                dimension_scores=[],
                match_score=50,
            )
        )
        db.commit()
    app = FastAPI()
    app.include_router(recommendations.router, prefix="/api")
    app.include_router(analysis.router, prefix="/api/v1")
    identity = SimpleNamespace(id=1, email="learning-owner@example.invalid")

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: identity
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, factory=factory, identity=identity)
    engine.dispose()


def test_actual_http_curated_does_not_publish_unreviewed_catalogue_url(owned):
    response = owned.client.post(
        "/api/recommendations/match_strategy", json={"match_id": 1, "mode": "curated"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["learning_priorities"][0]["skill"] == "docker"
    resources = data["learning_priorities"][0]["resources"]
    assert resources and resources[0]["title"] == "Docker Get Started"
    assert all(resource["url"] is None for resource in resources)
    assert any(
        "resource" in warning.lower() and "review" in warning.lower()
        for warning in data["warnings"]
    )
    with owned.factory() as db:
        assert db.get(models.User, 1).ai_credits == 17 and db.query(models.AnalysisRun).count() == 0


def test_actual_http_gaps_does_not_publish_unreviewed_compatibility_courses(owned):
    response = owned.client.post(
        "/api/recommendations/gaps",
        json={"target_role": "Backend Engineer", "current_skills": ["Python"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "docker" in data["skill_gaps"] and data["current_skills"] == ["Python"]
    assert data["recommended_courses"]
    assert all(course["url"] is None for course in data["recommended_courses"])
    assert any("resource" in warning.lower() for warning in data["warnings"])


def reviewed(monkeypatch, urls, *, expired=False):
    import json

    now = datetime.now(UTC)
    checked = now - timedelta(days=2 if expired else 1)
    end = now - timedelta(days=1) if expired else now + timedelta(days=1)
    monkeypatch.setenv(
        "LEARNING_RESOURCE_REVIEW_JSON",
        json.dumps(
            {
                "checked_at": checked.isoformat(),
                "expires_at": end.isoformat(),
                "urls": {url: "a" * 64 for url in urls},
            }
        ),
    )


def resources(plan):
    return [
        resource for priority in plan["learning_priorities"] for resource in priority["resources"]
    ]


def catalogue_plan(owned, monkeypatch, *, mixed=False):
    from app.services.recommender import resources_for_skills

    with owned.factory() as db:
        if mixed:
            db.get(models.JobMatch, 1).true_gaps = ["Docker", "JS"]
            db.commit()
    catalogue = resources_for_skills(["Docker", "JS"] if mixed else ["Docker"])
    urls = [row["url"] for rows in catalogue.values() for row in rows]
    reviewed(monkeypatch, urls)
    response = owned.client.post(
        "/api/recommendations/match_strategy", json={"match_id": 1, "mode": "curated"}
    )
    assert response.status_code == 200
    return response.json(), urls


@pytest.mark.parametrize("change", ["expired", "withdrawn"])
def test_funded_free_saved_replay_refreshes_links_without_new_hold_charge_or_payload_change(
    owned, monkeypatch, change
):
    import json
    from copy import deepcopy

    from backend.tests.test_free_core import synthetic_free_policy

    from app.domains.free_core.models import FreeCoreReservation

    values = synthetic_free_policy()
    values["operations"]["curated_learning"] = dict(
        values["operations"]["skill_roi"], route_id="free.curated_learning.v1"
    )
    values["execution_budgets"]["monthly_route_minor"]["free.curated_learning.v1"] = 50000
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(values))
    first, urls = catalogue_plan(owned, monkeypatch, mixed=True)
    assert all(row["url"] for row in resources(first))
    with owned.factory() as db:
        run = db.query(models.AnalysisRun).one()
        identity = run.id
        stored = deepcopy(run.result_payload)
        original_charge = (
            run.id,
            run.input_fingerprint,
            run.committed_units,
            db.get(models.User, 1).ai_credits,
        )
        expense = db.query(FreeCoreReservation).one()
        original_expense = (expense.id, expense.reserved_minor, expense.state)
    reviewed(monkeypatch, [] if change == "expired" else [DOCKER_URL], expired=change == "expired")
    # Read/reuse must survive unrelated funding-review expiry without creating
    # another plan, charge or cost hold.
    monkeypatch.delenv("FREE_CORE_POLICY_JSON")
    replay = owned.client.post(
        "/api/recommendations/match_strategy", json={"match_id": 1, "mode": "curated"}
    )
    assert replay.status_code == 200
    result = replay.json()
    assert any("expired" in warning for warning in result["warnings"])
    assert result["readiness_summary"] == first["readiness_summary"]
    assert result["project_recommendations"] == first["project_recommendations"]
    expected = {DOCKER_URL} if change == "withdrawn" else set()
    assert {row["url"] for row in resources(result) if row["url"]} == expected
    assert any(row["url"] is None for row in resources(result))
    with owned.factory() as db:
        run = db.query(models.AnalysisRun).one()
        expense = db.query(FreeCoreReservation).one()
        assert run.result_payload == stored
        assert (
            run.id,
            run.input_fingerprint,
            run.committed_units,
            db.get(models.User, 1).ai_credits,
        ) == original_charge
        assert (expense.id, expense.reserved_minor, expense.state) == original_expense
        assert db.query(FreeCoreReservation).count() == 1
    generic = owned.client.get(f"/api/v1/analysis-runs/{identity}/result")
    assert generic.status_code == 200 and generic.json()["result"] == result


def paid_saved(owned, plan):
    import hashlib

    from app.domains.credits import synchronous
    from app.schemas import LearningStrategyRequest

    request = synchronous.request(
        "learning_strategy", LearningStrategyRequest(match_id=1, mode="enhanced")
    )
    key = "owned-saved-learning-proof"
    stamp = datetime.now(UTC)
    with owned.factory() as db:
        run = models.AnalysisRun(
            id="owned-paid-learning",
            user_id=1,
            operation="sync_learning_strategy",
            status="succeeded",
            idempotency_key="sync:" + hashlib.sha256(key.encode()).hexdigest(),
            input_fingerprint="a" * 64,
            input_payload=request.model_dump(),
            result_payload=plan,
            usage_state="committed",
            estimated_units=4,
            committed_units=4,
            completed_at=stamp,
            created_at=stamp,
            updated_at=stamp,
        )
        db.add(run)
        db.commit()
    return request, key


def test_paid_saved_replay_and_generic_get_preserve_receipt_and_charge_with_partial_revocation(
    owned, monkeypatch
):
    from copy import deepcopy

    from app.domains.credits import synchronous
    from app.domains.credits.models import CreditExecutionHold, CreditReservation

    plan, _ = catalogue_plan(owned, monkeypatch, mixed=True)
    request, key = paid_saved(owned, plan)
    with owned.factory() as db:
        before = deepcopy(db.get(models.AnalysisRun, "owned-paid-learning").result_payload)
    reviewed(monkeypatch, [DOCKER_URL])
    with owned.factory() as db:
        lookup = synchronous.lookup_result(db, user_id=1, payload=request, idempotency_key=key)
        replay = synchronous.execute(
            db, user_id=1, payload=request, accepted_service_quote_id=None, idempotency_key=key
        )
        assert replay == lookup["result"]
        assert replay["credit_execution"] == {
            "run_id": "owned-paid-learning",
            "billing_currency": "credits",
            "committed_credits": 4,
            "charged_this_request": 0,
            "reused": True,
        }
        assert {row["url"] for row in resources(replay) if row["url"]} == {DOCKER_URL}
        assert any(row["url"] is None for row in resources(replay))
        assert any("withdrawn" in warning for warning in replay["warnings"])
        assert db.get(models.AnalysisRun, "owned-paid-learning").result_payload == before
        assert db.get(models.User, 1).ai_credits == 17
        assert db.query(models.AnalysisRun).count() == 1
        assert db.query(CreditExecutionHold).count() == db.query(CreditReservation).count() == 0
    generic = owned.client.get("/api/v1/analysis-runs/owned-paid-learning/result")
    assert generic.status_code == 200
    without_credit = {key: value for key, value in replay.items() if key != "credit_execution"}
    # The generic endpoint retains the original plan's optional metadata;
    # it must not invent the paid replay wrapper's execution annotation.
    assert generic.json()["result"] == without_credit | {
        "credit_execution": before["credit_execution"]
    }
    owned.identity.id = 2
    assert owned.client.get("/api/v1/analysis-runs/owned-paid-learning/result").status_code == 404


@pytest.mark.parametrize(
    "review",
    [
        None,
        "{}",
        "not json",
        "[]",
        '{"urls":{}}',
        '{"checked_at":"not a date","expires_at":"bad","urls":{}}',
    ],
)
def test_malformed_reviews_fail_closed_urls_without_editing_prose(owned, monkeypatch, review):
    from copy import deepcopy

    from app.services.synchronous_contracts import learning_resource_delivery

    original, _ = catalogue_plan(owned, monkeypatch)
    before = deepcopy(original)
    if review is None:
        monkeypatch.delenv("LEARNING_RESOURCE_REVIEW_JSON", raising=False)
    else:
        monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", review)
    view = learning_resource_delivery(original)
    assert all(row["url"] is None for row in resources(view))
    assert (
        view["readiness_summary"] == original["readiness_summary"]
        and view["timeline"] == original["timeline"]
    )
    assert original == before
    view["learning_priorities"][0]["reason"] = "Changed caller view only"
    assert original == before


def test_aliases_are_currently_reviewed_but_catalogue_replacements_never_rewrite_saved_urls(
    owned, monkeypatch
):
    from copy import deepcopy

    from app.services import recommender
    from app.services.synchronous_contracts import learning_resource_delivery

    plan, _ = catalogue_plan(owned, monkeypatch, mixed=True)
    alias = next(row for row in plan["learning_priorities"] if row["skill"] == "javascript")
    alias["skill"] = "JS"
    first = learning_resource_delivery(plan)
    assert all(row["url"] for row in resources(first))
    before = deepcopy(plan)
    replacement = "https://docs.docker.com/engine/"
    monkeypatch.setattr(
        recommender,
        "COURSES",
        [
            {
                "title": "Synthetic replacement, not fetched",
                "platform": "Docker",
                "url": replacement,
                "skill": "docker",
                "skills": ["docker"],
            }
        ],
    )
    reviewed(monkeypatch, [replacement])
    view = learning_resource_delivery(plan)
    assert all(row["url"] is None for row in resources(view))
    assert replacement not in repr(view)
    assert [row["title"] for row in resources(view)] == [row["title"] for row in resources(plan)]
    assert plan == before


def test_partial_review_compatibility_gaps_retains_only_exact_reviewed_catalogue_link(
    owned, monkeypatch
):
    reviewed(monkeypatch, [DOCKER_URL])
    response = owned.client.post(
        "/api/recommendations/gaps",
        json={"target_role": "Backend Engineer", "current_skills": ["Python"]},
    )
    assert response.status_code == 200
    body = response.json()
    assert {row["url"] for row in body["recommended_courses"] if row["url"]} == {DOCKER_URL}
    assert any(row["url"] is None for row in body["recommended_courses"])
    assert body["warnings"] and "docker" in body["skill_gaps"]


@pytest.mark.parametrize("damage", ["naive", "future", "too_long", "bad_evidence"])
def test_invalid_review_dates_or_evidence_cannot_publish_links(owned, monkeypatch, damage):
    import json
    import os

    from app.services.synchronous_contracts import learning_resource_delivery

    plan, _ = catalogue_plan(owned, monkeypatch)
    review = json.loads(os.environ["LEARNING_RESOURCE_REVIEW_JSON"])
    now = datetime.now(UTC)
    if damage == "naive":
        review["checked_at"] = now.replace(tzinfo=None).isoformat()
    elif damage == "future":
        review["checked_at"] = (now + timedelta(hours=1)).isoformat()
    elif damage == "too_long":
        review["expires_at"] = (
            datetime.fromisoformat(review["checked_at"]) + timedelta(days=32)
        ).isoformat()
    else:
        review["urls"][DOCKER_URL] = "not-retained-evidence"
    monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", json.dumps(review))
    assert all(row["url"] is None for row in resources(learning_resource_delivery(plan)))


def test_actual_http_legacy_unavailable_generation_fallback_projects_resource_links(
    owned, monkeypatch
):
    from contextlib import contextmanager

    from app.domains.credits import synchronous

    context = {
        "approved_evidence": [],
        "resume": {"skills": ["Python"], "experience_years": 1},
        "match": {
            "true_gaps": ["Docker"],
            "partial_matches": [],
            "required_skills": ["Docker"],
            "improvement_tips": [],
            "dimension_scores": [],
        },
    }
    monkeypatch.setattr(
        synchronous,
        "owned_context",
        lambda *_a, **_k: SimpleNamespace(context=context, fingerprint="a" * 64),
    )

    @contextmanager
    def unavailable(*_a, **_k):
        raise ValueError("Synthetic admission unavailable before supplier work")
        yield

    monkeypatch.setattr(recommendations, "billable_operation", unavailable)
    response = owned.client.post(
        "/api/recommendations/match_strategy", json={"match_id": 1, "mode": "enhanced"}
    )
    assert response.status_code == 200
    plan = response.json()
    assert plan["generated_by"] == "fallback"
    assert all(row["url"] is None for row in resources(plan))
    assert any("resource" in warning.lower() for warning in plan["warnings"])
    with owned.factory() as db:
        assert db.query(models.AnalysisRun).count() == 0 and db.get(models.User, 1).ai_credits == 17


def test_generic_legacy_learning_is_projected_but_unrelated_results_are_unchanged(
    owned, monkeypatch
):
    from copy import deepcopy

    plan, _ = catalogue_plan(owned, monkeypatch)
    before = deepcopy(plan)
    now = datetime.now(UTC)
    with owned.factory() as db:
        for identity, operation in [
            ("legacy-learning", "learning_strategy"),
            ("other-result", "interview_questions"),
        ]:
            db.add(
                models.AnalysisRun(
                    id=identity,
                    user_id=1,
                    operation=operation,
                    status="succeeded",
                    idempotency_key=identity,
                    input_fingerprint="c" * 64,
                    input_payload={},
                    result_payload=plan,
                    committed_units=1,
                    completed_at=now,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.commit()
    reviewed(monkeypatch, [], expired=True)
    learning = owned.client.get("/api/v1/analysis-runs/legacy-learning/result")
    other = owned.client.get("/api/v1/analysis-runs/other-result/result")
    assert learning.status_code == other.status_code == 200
    assert all(row["url"] is None for row in resources(learning.json()["result"]))
    assert other.json()["result"] == before
    with owned.factory() as db:
        assert db.get(models.AnalysisRun, "legacy-learning").result_payload == before
        assert db.get(models.AnalysisRun, "other-result").result_payload == before
