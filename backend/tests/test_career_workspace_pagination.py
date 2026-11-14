"""Owned in-memory histories
no native identity, documents or providers."""
import socket
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, null
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import get_db
from app.domains.career import service, workspace
from app.routers.v1 import analysis, career
from app.security import get_current_user

STAMP = datetime(2026, 11, 1)
PREFIX = "/api/v1/opportunities/opp_a/workspace"
TABLES = [models.User, models.Resume, models.JobMatch, models.Opportunity, models.AnalysisRun,
          models.ApplicationEvent, models.OpportunityContact, models.Reminder, models.EvidenceItem, models.ResumeVersion]


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Network is forbidden in owned workspace fixtures")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


@pytest.fixture
def owned():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    models.Base.metadata.create_all(engine, tables=[model.__table__ for model in TABLES])
    factory = sessionmaker(bind=engine)
    identity = SimpleNamespace(id=1, email="owned@example.test")
    statements, auth_calls = [], []
    event.listen(engine, "before_cursor_execute", lambda _c, _cursor, statement, _p, _ctx, _many: statements.append(statement))
    app = FastAPI()
    app.include_router(career.router, prefix="/api/v1")
    app.include_router(analysis.router, prefix="/api/v1")

    def database():
        with factory() as db:
            yield db

    def current():
        auth_calls.append(identity.id)
        return identity

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = current
    app.dependency_overrides[career.require_career_workspace] = lambda: None
    with factory() as db:
        db.add_all([models.User(id=1, email=identity.email), models.User(id=2, email="other@example.test")])
        for rid, owner in [(10, 1), (11, 1), (12, 2)]:
            db.add(models.Resume(id=rid, user_id=owner, original_filename=f"owned-{rid}.pdf", source_document=b"synthetic source",
                source_format="pdf", sections={"private": "source body"}, raw_text="private raw", created_at=STAMP))
        for oid, rid, owner in [("opp_a", 10, 1), ("opp_b", 11, 1), ("opp_other", 12, 2)]:
            db.add(models.Opportunity(id=oid, resume_id=rid, user_id=owner, title="Engineer", company="Owned",
                job_description="private job body", job_snapshot={"private": "snapshot"}, notes="private notes", created_at=STAMP, updated_at=STAMP))
        db.commit()
    with TestClient(app) as client:
        yield client, factory, identity, statements, auth_calls
    engine.dispose()


def populate(factory, count, *, start=0):
    with factory() as db:
        for n in range(start, start + count):
            suffix = f"{n:04}"
            common = {"user_id": 1, "opportunity_id": "opp_a"}
            db.add(models.ApplicationEvent(id=f"evt_{suffix}", **common, event_type="created", note="private note", occurred_at=STAMP, recorded_at=STAMP))
            db.add(models.OpportunityContact(id=f"con_{suffix}", **common, name=f"Contact {suffix}", email="private@example.test", notes="private contact", created_at=STAMP, updated_at=STAMP))
            db.add(models.Reminder(id=f"rem_{suffix}", **common, message=f"Reminder {suffix}", due_at=STAMP, created_at=STAMP))
            db.add(models.EvidenceItem(id=f"evd_{suffix}", user_id=1, resume_id=10, category="experience", title=f"Fact {suffix}",
                evidence_text="private full fact", metrics={"private": 1}, skills=["private skill"], source_ref="private source", approval_state="approved", created_at=STAMP, updated_at=STAMP))
            db.add(models.ResumeVersion(id=f"rsv_{suffix}", **common, resume_id=10, version_number=n + 1, label=f"Version {suffix}",
                structured_content={"private": "full version body"}, evidence_ids=[f"evd_{suffix}"], created_at=STAMP))
        db.commit()


def test_bootstrap_one_auth_tab_gated_projection(owned):
    client, factory, _, sql, auth = owned
    populate(factory, 25)
    sql.clear()
    auth.clear()
    response = client.get(PREFIX)
    assert response.status_code == 200, response.text
    data = response.json()
    assert response.headers["cache-control"] == "private, no-store" and len(auth) == 1
    assert set(data["opportunity"]).isdisjoint({"job_description", "job_snapshot", "notes", "outcome_notes"})
    for collection in ["resume_versions", "contacts", "reminders"]:
        assert len(data["pages"][collection]["items"]) == 20
    assert data["pages"]["evidence"] is data["pages"]["activity"] is None
    assert data["resumes"]["selected_resume"]["id"] == 10
    assert "private" not in response.text
    for statement in sql:
        projection = statement.partition("\nFROM")[0]
        assert all(field not in projection for field in ["structured_content", "evidence_text", "opportunities.job_description", "opportunities.job_snapshot"])
    data = client.get(PREFIX, params={"tab": "resume", "limit": 2}).json()
    assert data["approved_evidence_count"] == 25 and len(data["pages"]["evidence"]["items"]) == 2
    assert data["pages"]["contacts"] is data["pages"]["reminders"] is None
    assert "snapshot_fingerprint" not in data["pages"]["evidence"]["items"][0]


@pytest.mark.parametrize("collection,prefix,reverse", [("activity", "evt", True), ("contacts", "con", True),
    ("reminders", "rem", False), ("evidence", "evd", True), ("resume_versions", "rsv", True)])
def test_ties_deleted_anchor_new_insert_complete_traversal(owned, collection, prefix, reverse):
    client, factory, _, _, _ = owned
    populate(factory, 7)
    first = client.get(f"{PREFIX}/{collection}", params={"limit": 2}).json()
    expected = list(range(7))[::-1] if reverse else list(range(7))
    assert [item["id"] for item in first["items"]] == [f"{prefix}_{i:04}" for i in expected[:2]]
    model = workspace._SPECS[collection][0]
    with factory() as db:
        db.query(model).filter(model.id == first["items"][-1]["id"]).delete()
        db.commit()
    if reverse:
        populate(factory, 1, start=99)
    items, cursor = first["items"], first["next_cursor"]
    while cursor:
        response = client.get(f"{PREFIX}/{collection}", params={"limit": 2, "cursor": cursor})
        assert response.status_code == 200, response.text
        items += response.json()["items"]
        cursor = response.json()["next_cursor"]
    assert [item["id"] for item in items] == [f"{prefix}_{i:04}" for i in expected]


def test_cursor_binds_owner_opportunity_source_collection_filter_limit(owned):
    client, factory, owner, _, _ = owned
    populate(factory, 3)
    token = client.get(f"{PREFIX}/evidence", params={"limit": 1, "state": "approved"}).json()["next_cursor"]
    for path, params in [(f"{PREFIX}/evidence", {"limit": 2, "state": "approved"}), (f"{PREFIX}/contacts", {"limit": 1}),
        (f"{PREFIX}/evidence", {"limit": 1}), ("/api/v1/opportunities/opp_b/workspace/evidence", {"limit": 1, "state": "approved"})]:
        assert client.get(path, params={**params, "cursor": token}).status_code == 400
    for value in ["bad", "", token + "=", ("A" if token[0] != "A" else "B") + token[1:]]:
        assert client.get(f"{PREFIX}/evidence", params={"limit": 1, "state": "approved", "cursor": value}).status_code == 400
    with factory() as db:
        db.query(models.Opportunity).filter_by(id="opp_a").update({"resume_id": 11})
        db.commit()
    assert client.get(f"{PREFIX}/evidence", params={"limit": 1, "state": "approved", "cursor": token}).status_code == 400
    owner.id = 2
    assert client.get(PREFIX).status_code == 404
    assert client.get("/api/v1/opportunities/opp_other/workspace/evidence", params={"limit": 1, "state": "approved", "cursor": token}).status_code == 400


def test_saved_interview_pages_keep_owned_results_after_input_changes(owned):
    client, factory, owner, sql, _ = owned
    results_path = f"{PREFIX}/interview_results"
    with factory() as db:
        def saved(identity, **changes):
            values = {"id": identity, "user_id": 1, "opportunity_id": "opp_a",
                "operation": "interview_questions", "status": "succeeded", "completed_at": STAMP,
                "idempotency_key": identity, "input_fingerprint": "a" * 64,
                "input_payload": {"private": "historical evidence"}, "model": "retired-model",
                "result_payload": {"questions": [{"question": "Private earlier question"}]}}
            db.add(models.AnalysisRun(**(values | changes)))

        saved("interview_b", input_payload={"purged": True}, input_purged_at=STAMP + timedelta(days=30))
        saved("interview_a")
        saved("other_opportunity", opportunity_id="opp_b")
        saved("other_owner", user_id=2, opportunity_id="opp_other")
        saved("other_operation", operation="resume_tailor")
        saved("pending", status="queued", completed_at=None)
        saved("purged", result_purged_at=STAMP)
        saved("missing_payload", result_payload=None)
        saved("sql_null_payload", result_payload=null())
        db.query(models.Opportunity).filter_by(id="opp_a").update({"latest_analysis_run_id": "other_operation"})
        db.commit()

    assert client.get(PREFIX).json()["pages"].get("interview_results") is None
    sql.clear()
    response = client.get(PREFIX, params={"tab": "interview", "limit": 1})
    assert response.status_code == 200, response.text
    first = response.json()["pages"]["interview_results"]
    assert [item["id"] for item in first["items"]] == ["interview_b"]
    assert set(first["items"][0]) == {"id", "completed_at"}
    assert first["next_cursor"]
    assert not any("result_payload" in statement.partition("\nFROM")[0] or "input_payload" in statement
                   for statement in sql)
    with factory() as db:
        db.query(models.Opportunity).filter_by(id="opp_a").update({"resume_id": 11, "job_description": "Changed role"})
        db.commit()
    next_params = {"cursor": first["next_cursor"], "limit": 1}
    assert client.get(results_path, params=next_params).json() == {
        "items": [{"id": "interview_a", "completed_at": first["items"][0]["completed_at"]}], "next_cursor": None}
    assert client.get(results_path.replace("opp_a", "opp_b"), params=next_params).status_code == 400
    detail_path = "/api/v1/analysis-runs/interview_b/result"
    assert client.get(detail_path).json()["result"]["questions"] == [{"question": "Private earlier question"}]
    owner.id = 2
    assert client.get(results_path).status_code == 404
    assert client.get(detail_path).status_code == 404
    assert client.get(results_path.replace("opp_a", "opp_other"), params=next_params).status_code == 400
    owner.id = 1
    with factory() as db:
        db.query(models.AnalysisRun).filter_by(id="interview_b").update({
            "result_payload": None, "result_purged_at": STAMP + timedelta(days=90)})
        db.commit()
    assert client.get(detail_path).status_code == 410
    assert [item["id"] for item in client.get(results_path).json()["items"]] == ["interview_a"]


@pytest.mark.parametrize("query", ["limit=0", "limit=51", "limit=abc", "tab=unknown", "selected_version_id=", "selected_version_id=" + "x" * 65])
def test_transport_bounds(owned, query):
    assert owned[0].get(PREFIX + "?" + query).status_code == 422


def test_exact_lazy_bodies_and_foreign_scope_denial(owned):
    client, factory, _, _, _ = owned
    populate(factory, 25)
    item = client.get(f"{PREFIX}/evidence/evd_0000").json()
    assert item["evidence"]["evidence_text"] == "private full fact" and len(item["evidence"]["snapshot_fingerprint"]) == 64
    assert item["transcription"] is None and item["transcription_unavailable"] is False
    assert client.get(f"{PREFIX}/resume_versions/rsv_0000").json()["structured_content"] == {"private": "full version body"}
    assert client.get(f"{PREFIX}/activity/evt_0000").json()["note"] == "private note"
    assert client.get(f"{PREFIX}/contacts/con_0000").json()["email"] == "private@example.test"
    assert client.get(f"{PREFIX}/body").json()["job_description"] == "private job body"
    for path in [f"{PREFIX}/evidence/absent", "/api/v1/opportunities/opp_b/workspace/evidence/evd_0000", "/api/v1/opportunities/opp_b/workspace/resume_versions/rsv_0000"]:
        assert client.get(path).status_code == 404


def test_offpage_submitted_selection_keeps_missing_id_explicit(owned):
    client, factory, _, _, _ = owned
    populate(factory, 25)
    with factory() as db:
        db.add(models.ApplicationEvent(id="evt_applied", user_id=1, opportunity_id="opp_a", event_type="stage_changed", to_stage="applied", resume_version_id="rsv_0000", occurred_at=STAMP + timedelta(days=1)))
        db.commit()
    data = client.get(PREFIX, params={"limit": 1, "selected_version_id": "rsv_0000"}).json()
    assert data["pages"]["resume_versions"]["items"][0]["id"] != "rsv_0000"
    assert data["submitted_resume_version"]["id"] == data["selected_resume_version"]["id"] == "rsv_0000"
    with factory() as db:
        db.query(models.ResumeVersion).filter_by(id="rsv_0000").delete()
        db.commit()
    data = client.get(PREFIX, params={"limit": 1}).json()
    assert data["submitted_resume_version_id"] == "rsv_0000" and data["submitted_resume_version"] is None
    assert client.get(PREFIX, params={"selected_version_id": "rsv_0000"}).status_code == 404


def test_legacy_cap_before_body_reads_internal_export_complete(owned):
    client, factory, _, sql, _ = owned
    populate(factory, 51)
    for path in ["/api/v1/opportunities/opp_a", "/api/v1/evidence-items?resume_id=10", "/api/v1/resume-versions?opportunity_id=opp_a", "/api/v1/reminders"]:
        sql.clear()
        response = client.get(path)
        assert response.status_code == 409 and response.json()["detail"]["code"] == "workspace_pagination_required"
        assert all("evidence_text" not in statement and "structured_content" not in statement for statement in sql)
    with factory() as db:
        assert len(service.list_evidence(db, 1, resume_id=10)) == len(service.list_resume_versions(db, 1, opportunity_id="opp_a")) == 51
        exported = service.opportunity_export(db, 1, "opp_a")
        assert len(exported.evidence_items) == len(exported.opportunity.activity) == len(exported.opportunity.resume_versions) == 51


def test_source_snapshot_uses_all_approved_ids_beyond_page(owned):
    client, factory, _, _, _ = owned
    populate(factory, 75)
    response = client.post(f"{PREFIX}/source-snapshot", json={})
    assert response.status_code == 201, response.text
    assert len(response.json()["evidence_ids"]) == 75
    assert response.json()["structured_content"] == {"format_preservation": "source", "source_format": "pdf", "source_edits": []}


def test_source_snapshot_refuses_the_resume_changed_in_another_tab(owned):
    client, factory, _, _, _ = owned
    shown = client.get(PREFIX, params={"tab": "resume"}).json()["opportunity"]["resume_id"]
    assert shown == 10
    changed = client.patch("/api/v1/opportunities/opp_a", json={"resume_id": 11})
    assert changed.status_code == 200, changed.text

    stale = client.post(f"{PREFIX}/source-snapshot/selected", json={"expected_resume_id": shown})
    assert stale.status_code == 409, stale.text
    assert stale.json()["detail"]["code"] == "source_snapshot_resume_changed"
    with factory() as db:
        assert db.query(models.ResumeVersion).count() == 0

    fresh = client.get(PREFIX, params={"tab": "resume"}).json()["opportunity"]["resume_id"]
    assert fresh == 11
    saved = client.post(f"{PREFIX}/source-snapshot/selected", json={"expected_resume_id": fresh})
    assert saved.status_code == 201, saved.text
    assert saved.json()["resume_id"] == 11
    assert saved.json()["structured_content"] == {"format_preservation": "source", "source_format": "pdf", "source_edits": []}


@pytest.mark.parametrize("body", [{}, {"expected_resume_id": None}, {"expected_resume_id": False},
    {"expected_resume_id": "10"}, {"resume_id": 10}])
def test_source_snapshot_rejects_invalid_explicit_resume_identity(owned, body):
    client, factory, _, _, _ = owned
    assert client.post(f"{PREFIX}/source-snapshot/selected", json=body).status_code == 422
    with factory() as db:
        assert db.query(models.ResumeVersion).count() == 0


def test_source_snapshot_overflow_refuses_before_any_version_write(owned):
    client, factory, _, _, _ = owned
    with factory() as db:
        db.execute(models.EvidenceItem.__table__.insert(), [{"id": f"evd_{i:04}", "user_id": 1, "resume_id": 10,
            "category": "experience", "title": "Owned fact", "evidence_text": "Full owned fact", "approval_state": "approved"} for i in range(501)])
        db.commit()
    response = client.post(f"{PREFIX}/source-snapshot")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "source_snapshot_evidence_scope_exceeded"
    with factory() as db:
        assert db.query(models.ResumeVersion).count() == 0


@pytest.mark.parametrize("collection,state", [("contacts", "approved"), ("evidence", "invalid"), ("reminders", "approved")])
def test_unsupported_filters_fail_explicitly(owned, collection, state):
    assert owned[0].get(f"{PREFIX}/{collection}", params={"state": state}).status_code == 422


def test_exact_legacy_cap_preserves_complete_array_contract(owned):
    client, factory, _, _, _ = owned
    populate(factory, 50)
    for path in ["/api/v1/evidence-items?resume_id=10", "/api/v1/resume-versions?opportunity_id=opp_a", "/api/v1/reminders"]:
        response = client.get(path)
        assert response.status_code == 200 and len(response.json()) == 50
    assert len(client.get("/api/v1/opportunities/opp_a").json()["activity"]) == 50


def test_linked_transcription_detail_keeps_exact_single_section_and_full_fact(monkeypatch, owned):
    from app.domains.career import schemas, transcription_review
    client, factory, _, _, _ = owned
    populate(factory, 2)
    with factory() as db:
        db.query(models.EvidenceItem).filter_by(id="evd_0000").update({"provenance": "resume_extraction"})
        db.commit()
    snapshot = schemas.TranscriptionReviewResponse(resume_id=10, filename="owned.pdf", source_format="pdf",
        source_sha256="a" * 64, assessment_fingerprint="b" * 64, status="needs_review",
        sections=[schemas.TranscriptionSectionReview(section="experience", original_text="complete visible section", evidence_id="evd_0000", evidence_fingerprint="c" * 64, reviewed=False),
            schemas.TranscriptionSectionReview(section="unrelated", original_text="unrelated private section", evidence_id="evd_0001", evidence_fingerprint="d" * 64, reviewed=True)])
    monkeypatch.setattr(transcription_review, "review_snapshot", lambda *_args: snapshot)
    response = client.get(f"{PREFIX}/evidence/evd_0000")
    assert response.status_code == 200
    data = response.json()
    assert data["transcription"]["section"]["original_text"] == "complete visible section"
    assert data["transcription"]["assessment_fingerprint"] == "b" * 64
    assert data["transcription"]["status"] == "needs_review"
    assert "unrelated private section" not in response.text
    assert data["evidence"]["snapshot_fingerprint"] != "c" * 64


def test_unavailable_transcription_stays_explicit_without_inventing_review(monkeypatch, owned):
    from fastapi import HTTPException

    from app.domains.career import transcription_review
    client, factory, _, _, _ = owned
    populate(factory, 1)
    with factory() as db:
        db.query(models.EvidenceItem).filter_by(id="evd_0000").update({"provenance": "resume_extraction"})
        db.commit()
    def unavailable(*_args):
        raise HTTPException(422, "Complete source unavailable")
    monkeypatch.setattr(transcription_review, "review_snapshot", unavailable)
    response = client.get(f"{PREFIX}/evidence/evd_0000")
    assert response.status_code == 200
    assert response.json()["transcription_unavailable"] is True and response.json()["transcription"] is None
    assert len(response.json()["evidence"]["snapshot_fingerprint"]) == 64


def test_overview_saved_match_does_not_hydrate_job_or_other_tab_bodies(owned):
    client, factory, _, statements, _ = owned
    with factory() as db:
        db.add(models.JobMatch(id=42, user_id=1, resume_id=10, job_title="Engineer", company="Owned",
            match_score=75, fit_summary="Visible saved match", created_at=STAMP))
        db.query(models.Opportunity).filter_by(id="opp_a").update({"latest_match_id": 42})
        db.commit()
    statements.clear()
    response = client.get(PREFIX)
    assert response.status_code == 200, response.text
    assert response.json()["match"]["match_id"] == 42
    assert response.json()["match"]["fit_summary"] == "Visible saved match"
    for statement in statements:
        projection = statement.partition("\nFROM")[0]
        assert "opportunities.job_description" not in projection and "opportunities.job_snapshot" not in projection
    assert client.get(PREFIX, params={"tab": "activity"}).json()["match"] is None


def test_unassigned_historical_version_remains_owned_after_source_change(owned):
    client, factory, _, _, _ = owned
    populate(factory, 1)
    with factory() as db:
        db.query(models.ResumeVersion).filter_by(id="rsv_0000").update({"opportunity_id": None})
        db.add(models.ApplicationEvent(id="evt_applied", user_id=1, opportunity_id="opp_a", event_type="stage_changed", to_stage="applied", resume_version_id="rsv_0000"))
        db.query(models.Opportunity).filter_by(id="opp_a").update({"resume_id": 11})
        db.commit()
    response = client.get(PREFIX, params={"selected_version_id": "rsv_0000"})
    assert response.status_code == 200
    assert response.json()["submitted_resume_version"]["id"] == "rsv_0000"
    assert response.json()["selected_resume_version"]["id"] == "rsv_0000"
    assert client.get(f"{PREFIX}/resume_versions/rsv_0000").status_code == 200
    assert client.get(f"{PREFIX}/evidence/evd_0000").status_code == 404


def test_metadata_edits_do_not_move_evidence_keyset(owned):
    client, factory, _, _, _ = owned
    populate(factory, 4)
    first = client.get(f"{PREFIX}/evidence", params={"limit": 2}).json()
    with factory() as db:
        db.query(models.EvidenceItem).filter_by(id="evd_0000").update({"title": "Changed fact", "updated_at": STAMP + timedelta(days=5)})
        db.commit()
    second = client.get(f"{PREFIX}/evidence", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert [row["id"] for row in first["items"] + second["items"]] == ["evd_0003", "evd_0002", "evd_0001", "evd_0000"]


def test_later_legacy_collection_overflow_refuses_before_any_body_hydration(owned):
    client, factory, _, statements, _ = owned
    with factory() as db:
        db.execute(models.OpportunityContact.__table__.insert(), [{"id": f"con_{n:04}", "user_id": 1,
            "opportunity_id": "opp_a", "name": "Owned contact", "notes": "private contact body"} for n in range(51)])
        db.commit()
    statements.clear()
    assert client.get("/api/v1/opportunities/opp_a").status_code == 409
    for statement in statements:
        projection = statement.partition("\nFROM")[0]
        assert "opportunities.job_description" not in projection
        assert "opportunity_contacts.notes" not in projection and "application_events.note" not in projection


def test_legacy_workspace_keeps_default_submitted_version_after_resume_change(owned):
    client, factory, _, _, _ = owned
    populate(factory, 2)
    with factory() as db:
        db.query(models.Opportunity).filter_by(id="opp_a").update({"resume_id": 11})
        db.add(models.ResumeVersion(id="rsv_new_source", user_id=1, resume_id=11,
            opportunity_id="opp_a", version_number=1, created_at=STAMP + timedelta(days=1)))
        db.commit()

    legacy = client.get("/api/v1/opportunities/opp_a")
    assert legacy.status_code == 200
    assert legacy.json()["resume_versions"][0]["id"] == "rsv_0001"

    page = client.get(f"{PREFIX}/resume_versions")
    assert page.status_code == 200
    assert page.json()["items"][0]["id"] == "rsv_new_source"


VERSION_PAGE = "/api/v1/resume-versions/page"
VALID_EDIT = {"unit_id": "unit", "original_text": "before", "replacement_text": "after", "reason": "role", "evidence_ids": ["evd_0000"]}
VALID_CONTENT = {"format_preservation": "source", "source_format": "pdf", "source_edits": [VALID_EDIT]}


def test_global_version_metadata_ties_owner_source_filter_limit_and_offpage_selection(owned):
    client, factory, _, statements, _ = owned
    populate(factory, 25)
    with factory() as db:
        db.query(models.ResumeVersion).filter(models.ResumeVersion.id != "rsv_0000").update({"approval_state": "approved", "structured_content": VALID_CONTENT})
        db.commit()
    statements.clear()
    params = {"resume_id": 10, "limit": 2, "approval_state": "approved", "current_id": "rsv_0000"}
    first = client.get(VERSION_PAGE, params=params)
    assert first.status_code == 200, first.text
    data = first.json()
    assert [item["id"] for item in data["items"]] == ["rsv_0024", "rsv_0023"]
    assert all(item["source_preserving_edits"] for item in data["items"])
    assert data["selected_version"]["id"] == "rsv_0000" and data["selected_version"]["approval_state"] == "draft"
    assert "structured_content" not in data["items"][0]
    for sql in statements:
        projection = sql.partition("\nFROM")[0]
        assert "resume_versions.structured_content AS" not in projection
        assert "resumes.source_document" not in projection and "evidence_ids AS" not in projection
    token = data["next_cursor"]
    for changed in [{"resume_id": 11}, {"limit": 3}, {"approval_state": "rejected"}]:
        assert client.get(VERSION_PAGE, params={**{key: value for key, value in params.items() if key != "current_id"}, **changed, "cursor": token}).status_code == 400
    assert client.get(VERSION_PAGE, params={**params, "resume_id": 12}).status_code == 404
    assert client.get(VERSION_PAGE, params={**params, "resume_id": 11}).status_code == 404
    with factory() as db:
        db.query(models.ResumeVersion).filter_by(id="rsv_0023").delete()
        db.commit()
    rest = client.get(VERSION_PAGE, params={**params, "cursor": token}).json()
    assert [item["id"] for item in rest["items"]] == ["rsv_0022", "rsv_0021"]


@pytest.mark.parametrize("content,expected", [
    (VALID_CONTENT, True), ({**VALID_CONTENT, "source_format": "texzip", "evidence_needed": []}, True),
    ({**VALID_CONTENT, "source_edits": [{**VALID_EDIT, "evidence_ids": []}]}, True),
    ({}, False), (None, False), (["array"], False), ("scalar", False),
    ({**VALID_CONTENT, "format_preservation": "other"}, False), ({**VALID_CONTENT, "source_format": "html"}, False),
    ({**VALID_CONTENT, "source_edits": []}, False), ({**VALID_CONTENT, "source_edits": "text"}, False),
    ({**VALID_CONTENT, "source_edits": [None]}, False), ({**VALID_CONTENT, "source_edits": ["not-object"]}, False),
    ({**VALID_CONTENT, "source_edits": [{**VALID_EDIT, "unit_id": None}]}, False),
    ({**VALID_CONTENT, "source_edits": [{**VALID_EDIT, "evidence_ids": "not-array"}]}, False),
    ({**VALID_CONTENT, "source_edits": [{**VALID_EDIT, "evidence_ids": [1]}]}, False),
    ({**VALID_CONTENT, "source_edits": [VALID_EDIT, {"unit_id": "incomplete"}]}, False),
    ({**VALID_CONTENT, "evidence_needed": None}, False), ({**VALID_CONTENT, "evidence_needed": [1]}, False),
    ({**VALID_CONTENT, "evidence_needed": ["A fact"]}, True),
])
def test_global_metadata_eligibility_matches_existing_source_shape_without_hydration(owned, content, expected):
    client, factory, _, _, _ = owned
    populate(factory, 1)
    with factory() as db:
        db.query(models.ResumeVersion).filter_by(id="rsv_0000").update({"structured_content": content})
        db.commit()
    response = client.get(VERSION_PAGE, params={"resume_id": 10})
    assert response.status_code == 200, response.text
    assert response.json()["items"][0]["source_preserving_edits"] is expected
    assert "structured_content" not in response.json()["items"][0]


@pytest.mark.parametrize("params", [{}, {"resume_id": 0}, {"resume_id": 10, "limit": 51}, {"resume_id": 10, "limit": 0},
    {"resume_id": 10, "approval_state": "unknown"}, {"resume_id": 10, "current_id": ""}])
def test_global_version_transport_bounds(owned, params):
    assert owned[0].get(VERSION_PAGE, params=params).status_code == 422


def test_snapshot_never_inverts_existing_transcription_evidence_then_resume_locks(owned):
    from sqlalchemy.dialects import postgresql
    _, factory, _, _, _ = owned
    populate(factory, 2)
    observed = []
    with factory() as db:
        def statement(state):
            observed.append(str(state.statement.compile(dialect=postgresql.dialect())))
        event.listen(db, "do_orm_execute", statement)
        workspace.source_snapshot(db, 1, "opp_a")
        event.remove(db, "do_orm_execute", statement)
    assert any("FROM opportunities" in sql and "FOR UPDATE" in sql for sql in observed)
    assert any("FROM resumes" in sql and "FOR UPDATE" in sql for sql in observed)
    assert all("FOR UPDATE" not in sql for sql in observed if "FROM evidence_items" in sql)


def add_saved_match(factory, *, match_id=42, user=1, resume=10, pointer=True):
    with factory() as db:
        db.add(models.JobMatch(id=match_id, user_id=user, resume_id=resume, job_title="Engineer", company="Owned",
            match_score=75, fit_summary="Saved analysis", created_at=STAMP))
        if pointer:
            db.query(models.Opportunity).filter_by(id="opp_a").update({"latest_match_id": match_id})
        db.commit()


def test_match_source_current_view_is_bound_on_every_tab_and_optional_route(owned):
    client, factory, _, _, _ = owned
    add_saved_match(factory)
    for tab in ["overview", "resume", "learning", "interview", "outcome", "activity"]:
        response = client.get(PREFIX, params={"tab": tab})
        assert response.status_code == 200
        data = response.json()
        assert data["match_source_state"] == "current"
        assert (data["match"] is not None) == (tab == "overview")
    assert client.get("/api/v1/opportunities/opp_a/match", params={"resume_id": 10}).status_code == 200


def test_source_switch_does_not_relabel_saved_score_or_erase_history_export(owned):
    client, factory, _, _, _ = owned
    add_saved_match(factory)
    assert client.patch("/api/v1/opportunities/opp_a", json={"resume_id": 11}).status_code == 200
    data = client.get(PREFIX).json()
    assert data["opportunity"]["resume_id"] == 11 and data["opportunity"]["latest_match_id"] == 42
    assert data["match_source_state"] == "different_resume" and data["match"] is None
    for resume in [10, 11]:
        response = client.get("/api/v1/opportunities/opp_a/match", params={"resume_id": resume})
        assert response.status_code == 409 and response.json()["detail"]["code"] == "match_refresh_required"
    historical = client.get("/api/v1/opportunities/opp_a/match")
    assert historical.status_code == 200 and historical.json()["match_id"] == 42
    exported = client.get("/api/v1/opportunities/opp_a/export")
    assert exported.status_code == 200
    assert exported.json()["latest_match"]["match_id"] == 42 and exported.json()["opportunity"]["resume_id"] == 11
    with factory() as db:
        assert db.get(models.JobMatch, 42).resume_id == 10
        assert db.get(models.Opportunity, "opp_a").latest_match_id == 42


@pytest.mark.parametrize("kind", ["missing", "foreign", "unknown_source"])
def test_match_unavailable_never_borrows_another_source_or_owner(owned, kind):
    client, factory, owner, _, _ = owned
    if kind == "missing":
        with factory() as db:
            db.query(models.Opportunity).filter_by(id="opp_a").update({"latest_match_id": 42})
            db.commit()
    else:
        add_saved_match(factory, user=2 if kind == "foreign" else 1, resume=12 if kind == "foreign" else None)
    data = client.get(PREFIX).json()
    assert data["match_source_state"] == "unavailable" and data["match"] is None
    assert client.get("/api/v1/opportunities/opp_a/match", params={"resume_id": 10}).status_code == 409
    owner.id = 2
    assert client.get(PREFIX).status_code == 404
    assert client.get("/api/v1/opportunities/opp_a/match", params={"resume_id": 10}).status_code == 404


def test_match_none_keeps_historical_absence_and_current_refresh_explicit(owned):
    client = owned[0]
    data = client.get(PREFIX).json()
    assert data["match_source_state"] == "none" and data["match"] is None
    assert client.get("/api/v1/opportunities/opp_a/match").status_code == 404
    response = client.get("/api/v1/opportunities/opp_a/match", params={"resume_id": 10})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "match_refresh_required"


@pytest.mark.parametrize("value", ["", "0", "-1", str(2**63)])
def test_match_current_source_transport_bounds(owned, value):
    assert owned[0].get("/api/v1/opportunities/opp_a/match", params={"resume_id": value}).status_code == 422
