"""Owned in-memory metadata fixtures; no documents, provider or identity calls."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.routers import resume
from app.security import get_current_user


@pytest.fixture
def owned():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[models.User.__table__, models.Resume.__table__])
    factory = sessionmaker(bind=engine)
    identity = SimpleNamespace(id=1)
    statements = []
    event.listen(engine, "before_cursor_execute", lambda _c, _cursor, statement, _p, _ctx, _many: statements.append(statement))
    app = FastAPI()
    app.include_router(resume.router, prefix="/api")

    def database():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = lambda: identity
    with TestClient(app) as client:
        yield client, factory, identity, statements
    engine.dispose()


def add(factory, identity, *, user=1, stamp=None):
    with factory() as db:
        db.add(models.Resume(id=identity, user_id=user, original_filename=f"owned-{identity}.pdf",
            created_at=stamp or datetime(2026, 11, 1), raw_text="private extracted text", skills=["private skill"],
            sections={"private": "section"}, contact_info={"private": "contact"}, source_document=b"private bytes", source_format="pdf"))
        db.commit()


def test_metadata_projection_and_legacy_array_are_compatible(owned):
    client, factory, _, statements = owned
    add(factory, 1)
    statements.clear()
    response = client.get("/api/resume/list")
    assert response.status_code == 200
    assert response.json() == {"resumes": [{"id": 1, "filename": "owned-1.pdf", "created_at": "2026-11-01T00:00:00",
        "source_available": True, "source_format": "pdf"}]}
    response = client.get("/api/resume/page?limit=1&current_id=1")
    assert response.status_code == 200
    assert response.json()["selected_resume"] == response.json()["resumes"][0]
    selects = [sql for sql in statements if sql.startswith("SELECT")]
    assert len(selects) == 2
    for sql in selects:
        projection = sql.partition("\nFROM")[0]
        assert all(f"resumes.{field}" not in projection for field in ["raw_text", "skills", "sections", "contact_info", "experience_years"])
        # Presence is computed in SQL; the source bytes themselves are never selected.
        assert "resumes.source_document IS NOT NULL" in projection
        assert "resumes.source_document," not in projection and "resumes.source_document AS" not in projection
        assert "LIMIT" in sql and "resumes.user_id" in sql


def test_tied_order_keyset_ignores_newer_insert_and_survives_deleted_anchor(owned):
    client, factory, _, _ = owned
    for identity in range(1, 6):
        add(factory, identity)
    first = client.get("/api/resume/page?limit=2").json()
    assert [row["id"] for row in first["resumes"]] == [5, 4]
    add(factory, 6, stamp=datetime(2026, 11, 2))
    with factory() as db:
        db.query(models.Resume).filter_by(id=4).delete()
        db.commit()
    second = client.get("/api/resume/page", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    third = client.get("/api/resume/page", params={"limit": 2, "cursor": second["next_cursor"]}).json()
    assert [row["id"] for row in second["resumes"]] == [3, 2]
    assert [row["id"] for row in third["resumes"]] == [1]
    assert third["next_cursor"] is None


def test_dates_and_independently_owned_older_selection_stay_bounded(owned):
    client, factory, _, statements = owned
    for identity in range(1, 7):
        add(factory, identity, stamp=datetime(2026, 11, 1) + timedelta(days=identity))
    add(factory, 100, user=2, stamp=datetime(2026, 12, 1))
    statements.clear()
    page = client.get("/api/resume/page?limit=2&current_id=1").json()
    assert [row["id"] for row in page["resumes"]] == [6, 5]
    assert page["selected_resume"]["id"] == 1
    assert len([sql for sql in statements if sql.startswith("SELECT")]) == 2
    for identity in [100, 999]:
        response = client.get(f"/api/resume/page?limit=1&current_id={identity}")
        assert response.status_code == 404 and response.json()["detail"] == "Selected resume is unavailable"


def test_cursor_cannot_be_tampered_or_reused_by_another_owner(owned):
    client, factory, owner, _ = owned
    add(factory, 1)
    add(factory, 2)
    cursor = client.get("/api/resume/page?limit=1").json()["next_cursor"]
    mutated = ("A" if cursor[0] != "A" else "B") + cursor[1:]
    for value in [mutated, "bad", "", "a=b", "a" * 255]:
        assert client.get("/api/resume/page", params={"cursor": value}).status_code == 400
    owner.id = 2
    assert client.get("/api/resume/page", params={"cursor": cursor}).status_code == 400
    assert client.get("/api/resume/list").json() == {"resumes": []}


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "limit=abc", "current_id=0", "current_id=-1", "current_id=9223372036854775808", "cursor=" + "a" * 257])
def test_transport_bounds(owned, query):
    assert owned[0].get("/api/resume/page?" + query).status_code == 422


def test_legacy_exact_cap_then_explicit_migration_refusal(owned):
    client, factory, _, statements = owned
    with factory() as db:
        db.execute(models.Resume.__table__.insert(), [{"id": identity, "user_id": 1, "created_at": datetime(2026, 11, 1), "original_filename": "owned.pdf"} for identity in range(1, 502)])
        db.commit()
    statements.clear()
    response = client.get("/api/resume/list")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "resume_pagination_required"
    assert response.json()["detail"]["page_path"] == "/api/resume/page"
    assert len(statements) == 1 and "LIMIT" in statements[0]
    with factory() as db:
        db.query(models.Resume).filter_by(id=501).delete()
        db.commit()
    response = client.get("/api/resume/list")
    assert response.status_code == 200 and len(response.json()["resumes"]) == 500
    assert response.json()["resumes"][0]["id"] == 500


def test_empty_page(owned):
    assert owned[0].get("/api/resume/page").json() == {"resumes": [], "next_cursor": None, "selected_resume": None}


@pytest.mark.parametrize("payload", [
    [True, 1, "2026-11-01T00:00:00", 1],
    [1, True, "2026-11-01T00:00:00", 1],
    [1, 1, "2026-11-01", 1],
    [1, 1, "2026-11-01T00:00:00+00:00", 1],
    [1, 1, "not-a-timestamp", 1],
    [1, 1, "2026-11-01T00:00:00", True],
    [1, 1, "2026-11-01T00:00:00", 0],
    [1, 1, "2026-11-01T00:00:00", 2**63],
])
def test_even_signed_malformed_cursor_is_rejected_before_sql(owned, payload):
    import base64
    import hashlib
    import hmac
    import json

    raw = json.dumps(payload, separators=(",", ":")).encode()
    from app.domains.resume_uploads import browsing

    signature = hmac.new(browsing.JWT_SECRET.encode(), browsing._CURSOR_PURPOSE + raw, hashlib.sha256).digest()
    cursor = base64.urlsafe_b64encode(raw + signature).decode().rstrip("=")
    owned[3].clear()
    assert owned[0].get("/api/resume/page", params={"cursor": cursor}).status_code == 400
    assert owned[3] == []


def test_pre_extraction_v1_cursor_golden_bytes_and_http_continuation(owned, monkeypatch):
    """Fixed old V1 JSON/purpose/signature vector remains valid after extraction."""
    from app.domains.resume_uploads import browsing

    monkeypatch.setattr(browsing, "JWT_SECRET", "owned-resume-cursor-v1-compatibility-vector")
    golden = "WzEsMSwiMjAyNi0xMS0wMVQwMDowMDowMCIsMl2BV5mAs8mBS1CoDee7zG8i65VsomqOhSdLVI9Hpbu8Pw"
    row = SimpleNamespace(created_at=datetime(2026, 11, 1), id=2)
    assert browsing._resume_cursor(1, row) == golden
    assert browsing._read_resume_cursor(golden, 1) == (datetime(2026, 11, 1), 2)
    client, factory, _, _ = owned
    for identity in [1, 2, 3]:
        add(factory, identity)
    response = client.get("/api/resume/page", params={"limit": 1, "cursor": golden})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["resumes"]] == [1]
    assert response.json()["next_cursor"] is None
