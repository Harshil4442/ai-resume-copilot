"""Owned synthetic workspace histories; no providers or native authority claims."""

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.routers.v1 import career
from app.security import get_current_user

STAMP = datetime(2026, 11, 5, 12)
PATH = "/api/v1/opportunities/page"


@pytest.fixture
def owned(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    models.Base.metadata.create_all(
        engine, tables=[models.User.__table__, models.Opportunity.__table__]
    )
    factory = sessionmaker(engine)
    app = FastAPI()
    app.include_router(career.router, prefix="/api/v1")
    identity = SimpleNamespace(id=1, email="owned-page@example.invalid")
    auth_calls = []

    def database():
        with factory() as db:
            yield db

    def current():
        auth_calls.append(identity.id)
        return identity

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = current
    monkeypatch.setattr(
        career, "decide_feature", lambda *_args, **_kwargs: SimpleNamespace(enabled=True)
    )
    with factory() as db:
        db.add_all(
            [
                models.User(id=1, email=identity.email),
                models.User(id=2, email="other-page@example.invalid"),
            ]
        )
        db.commit()
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _c, _cur, statement, _p, _ctx, _many: statements.append(statement),
    )
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client,
            factory=factory,
            identity=identity,
            auth_calls=auth_calls,
            app=app,
            sql=statements,
            current=current,
        )
    engine.dispose()


def seed(factory, count, *, owner=1, start=0, tied=False, large=False):
    body = "synthetic-private-job-" + ("x" * 99970) if large else "synthetic-private-job"
    with factory() as db:
        for value in range(start, start + count):
            db.add(
                models.Opportunity(
                    id=f"opp_{owner}_{value:05}",
                    user_id=owner,
                    title=f"Role {value:05}",
                    company="Owned",
                    location="Remote",
                    stage="saved",
                    priority="medium",
                    next_action="Review",
                    job_description=body,
                    job_snapshot={"job_description": body},
                    notes="synthetic-private-notes",
                    outcome_notes="synthetic-private-outcome",
                    created_at=STAMP,
                    updated_at=STAMP if tied else STAMP + timedelta(seconds=value),
                )
            )
        db.commit()


def test_new_page_avoids_actual_legacy_body_prefix_and_finds_role201(owned):
    seed(owned.factory, 201, large=True)
    # Actual original endpoint: full-body first200, older role inaccessible to
    # the existing local-prefix search. This remains compatibility behavior.
    legacy = owned.client.get("/api/v1/opportunities?limit=200")
    assert legacy.status_code == 200
    assert len(legacy.content) > 30_000_000
    assert "opp_1_00000" not in {row["id"] for row in legacy.json()["items"]}
    owned.sql.clear()
    owned.auth_calls.clear()
    response = owned.client.get(PATH, params={"search": "Role 00000"})
    assert response.status_code == 200
    data = response.json()
    assert [row["id"] for row in data["items"]] == ["opp_1_00000"]
    assert data["total"] == 1 and data["total_owned"] == data["active_count"] == 201
    assert len(response.content) < 2000 and "synthetic-private" not in response.text


def test_default_page_is_bounded_closed_metadata_with_one_auth_two_sql(owned):
    seed(owned.factory, 70)
    seed(owned.factory, 20, owner=2)
    owned.sql.clear()
    owned.auth_calls.clear()
    response = owned.client.get(PATH)
    assert response.status_code == 200 and response.headers["cache-control"] == "private, no-store"
    data = response.json()
    assert data["schema_version"] == "opportunity-page-v1"
    assert len(data["items"]) == 20 and data["next_cursor"]
    assert data["total"] == data["total_owned"] == data["active_count"] == 70
    assert data["interview_count"] == 0 and owned.auth_calls == [1]
    assert set(data) == {
        "schema_version",
        "items",
        "total",
        "total_owned",
        "active_count",
        "interview_count",
        "next_cursor",
    }
    assert all(
        set(row)
        == {"id", "title", "company", "location", "stage", "priority", "next_action", "updated_at"}
        for row in data["items"]
    )
    assert len(owned.sql) == 2
    assert not any(
        field in statement
        for statement in owned.sql
        for field in ("job_description", "job_snapshot", "outcome_notes", "opportunities.notes")
    )
    assert "synthetic-private" not in response.text


def test_tied_times_deleted_anchor_and_newest_insert_do_not_break_older_traversal(owned):
    seed(owned.factory, 7, tied=True)
    first = owned.client.get(PATH, params={"limit": 2}).json()
    assert [row["id"] for row in first["items"]] == ["opp_1_00006", "opp_1_00005"]
    with owned.factory() as db:
        db.query(models.Opportunity).filter_by(id="opp_1_00005").delete()
        db.commit()
    seed(owned.factory, 1, start=99, tied=True)
    rows = list(first["items"])
    cursor = first["next_cursor"]
    while cursor:
        response = owned.client.get(PATH, params={"limit": 2, "cursor": cursor})
        assert response.status_code == 200
        current = response.json()
        rows.extend(current["items"])
        cursor = current["next_cursor"]
    assert [row["id"] for row in rows] == [f"opp_1_{n:05}" for n in reversed(range(7))]
    newest = owned.client.get(PATH, params={"limit": 2}).json()
    assert newest["items"][0]["id"] == "opp_1_00099"


@pytest.mark.parametrize(
    "changed", [{"stage": "saved"}, {"search": "Role"}, {"include_archived": "true"}, {"limit": 3}]
)
def test_cursor_is_filter_and_limit_bound(owned, changed):
    seed(owned.factory, 5)
    first = owned.client.get(PATH, params={"limit": 2}).json()
    response = owned.client.get(PATH, params={"limit": 2, "cursor": first["next_cursor"]} | changed)
    assert (
        response.status_code == 400
        and response.json()["detail"]["code"] == "invalid_opportunity_cursor"
    )


def test_cursor_is_owner_bound_tamper_evident_and_not_a_workspace_token(owned):
    from app.domains.career import workspace_cursor

    seed(owned.factory, 5)
    first = owned.client.get(PATH, params={"limit": 2}).json()
    cursor = first["next_cursor"]
    owned.identity.id = 2
    assert owned.client.get(PATH, params={"limit": 2, "cursor": cursor}).status_code == 400
    assert owned.client.get(PATH).json()["items"] == []
    owned.identity.id = 1
    for bad in (
        cursor[:-1] + ("A" if cursor[-1] != "A" else "B"),
        cursor + "=",
        "!",
        workspace_cursor.encode((1, "opportunities"), STAMP, "opp_1_00001"),
    ):
        assert owned.client.get(PATH, params={"limit": 2, "cursor": bad}).status_code == 400


def test_stage_global_counts_and_existing_archive_semantics(owned):
    from app.domains.career.service import VALID_STAGES

    seed(owned.factory, len(VALID_STAGES))
    seed(owned.factory, 2, owner=2)
    with owned.factory() as db:
        for index, stage in enumerate(sorted(VALID_STAGES)):
            db.query(models.Opportunity).filter_by(id=f"opp_1_{index:05}").update(
                {models.Opportunity.stage: stage}
            )
        db.commit()
    default = owned.client.get(PATH).json()
    assert (
        default["total_owned"],
        default["total"],
        default["active_count"],
        default["interview_count"],
    ) == (9, 8, 6, 1)
    archived = owned.client.get(PATH, params={"stage": "archived"}).json()
    assert archived["total"] == 1 and archived["items"][0]["stage"] == "archived"
    assert (archived["active_count"], archived["interview_count"]) == (6, 1)
    assert owned.client.get(PATH, params={"include_archived": "true"}).json()["total"] == 9


@pytest.mark.parametrize(
    "search,expected",
    [
        ("%", "100% engineer"),
        ("_", "under_score engineer"),
        ("\\", "back\\slash engineer"),
        ("  LoCaTiOn  ", "location role"),
    ],
)
def test_search_is_literal_case_insensitive_contains(owned, search, expected):
    seed(owned.factory, 6)
    names = [
        "100% engineer",
        "100X engineer",
        "under_score engineer",
        "underXscore engineer",
        "back\\slash engineer",
        "location role",
    ]
    with owned.factory() as db:
        for index, name in enumerate(names):
            row = db.get(models.Opportunity, f"opp_1_{index:05}")
            row.title = name
            row.location = "LOCATION" if index == 5 else "Elsewhere"
        db.commit()
    response = owned.client.get(PATH, params={"search": search})
    assert response.status_code == 200
    assert [row["title"] for row in response.json()["items"]] == [expected]


def test_long_unicode_filter_has_bounded_cursor_and_normalized_whitespace(owned):
    seed(owned.factory, 3, tied=True)
    text = "界" * 200
    with owned.factory() as db:
        db.query(models.Opportunity).update({models.Opportunity.title: text})
        db.commit()
    first = owned.client.get(PATH, params={"search": text, "limit": 1}).json()
    assert len(first["next_cursor"]) < 1024
    assert text not in first["next_cursor"]
    assert (
        owned.client.get(
            PATH, params={"search": text, "limit": 1, "cursor": first["next_cursor"]}
        ).status_code
        == 200
    )
    plain = owned.client.get(PATH, params={"search": "", "limit": 1}).json()
    assert (
        owned.client.get(
            PATH, params={"search": "  ", "stage": " ", "limit": 1, "cursor": plain["next_cursor"]}
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 51},
        {"stage": "unknown"},
        {"search": "x" * 201},
        {"cursor": "x" * 1025},
    ],
)
def test_invalid_bounds_are_rejected_before_collection_reads(owned, params):
    owned.sql.clear()
    assert owned.client.get(PATH, params=params).status_code == 422
    assert owned.sql == []


def test_actual_http_retains_existing_auth_and_feature_gate(owned, monkeypatch):
    owned.sql.clear()

    def unauthenticated():
        raise HTTPException(401, "Not authenticated")

    owned.app.dependency_overrides[get_current_user] = unauthenticated
    assert owned.client.get(PATH).status_code == 401
    owned.app.dependency_overrides[get_current_user] = owned.current
    monkeypatch.setattr(
        career, "decide_feature", lambda *_args, **_kwargs: SimpleNamespace(enabled=False)
    )
    assert owned.client.get(PATH).status_code == 404
    assert owned.sql == []


def test_direct_reader_does_not_autoflush_or_hydrate_pending_bodies(owned):
    from pydantic import ValidationError

    from app.domains.career import browsing
    from app.domains.career.schemas import OpportunityPageItem, OpportunityPageResponse

    seed(owned.factory, 1)
    with owned.factory() as db:
        # Missing required title would fail an implicit autoflush.
        pending = models.Opportunity(id="pending", user_id=1)
        db.add(pending)
        owned.sql.clear()
        result = browsing.page(db, 1)
        assert result.total_owned == 1 and pending in db.new and len(owned.sql) == 2
        assert not any(statement.startswith("INSERT") for statement in owned.sql)
        with pytest.raises(ValidationError):
            OpportunityPageItem.model_validate(
                result.items[0].model_dump() | {"job_description": "private"}
            )
        with pytest.raises(ValidationError):
            OpportunityPageResponse.model_validate(result.model_dump() | {"arbitrary": "private"})
        db.rollback()


def test_initial_default_payload_against_retained_legacy_lower_bound(owned, record_testsuite_property):
    # Same201row/100kJD corpus as the before-fix control. Reuse that proven
    # >30MB legacy lower bound; do not repeat the old large-body request here.
    seed(owned.factory, 201, large=True)
    response = owned.client.get(PATH)
    assert response.status_code == 200
    data = response.json()
    assert len(data["items"]) == 20 and data["total_owned"] == 201
    assert len(response.content) < 10_000
    record_testsuite_property("retained_legacy_initial200_response_bytes_lower_bound", 30_000_000)
    record_testsuite_property("new_initial_default20_response_bytes", len(response.content))
    record_testsuite_property("same_synthetic_corpus_rows", 201)
    maximum = owned.client.get(PATH, params={"limit": 50})
    assert maximum.status_code == 200 and len(maximum.json()["items"]) == 50
