from __future__ import annotations

from io import BytesIO

from backend.app.database import Base, get_db
from backend.app.models import ApplicationEvent, EvidenceItem, Resume, ResumeVersion, User
from backend.app.routers.v1 import router as v1_router
from backend.app.security import get_current_user
from docx import Document as DocxDocument
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with factory() as db:
        db.add_all(
            [
                User(id=1, email="owner@example.com", ai_credits=50, tier="free"),
                User(id=2, email="other@example.com", ai_credits=50, tier="free"),
                Resume(
                    id=10,
                    user_id=1,
                    original_filename="resume.pdf",
                    raw_text="Python engineer",
                    skills=["python", "postgresql"],
                    sections={"experience": "Reduced API latency by 40 percent."},
                    contact_info={"name": "Owner"},
                ),
                Resume(id=20, user_id=2, original_filename="private.pdf", raw_text="Private"),
            ]
        )
        db.commit()

    app = FastAPI()
    app.include_router(v1_router, prefix="/api")

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    def override_user():
        with factory() as db:
            return db.get(User, 1)

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    return engine, factory, TestClient(app)


def test_complete_opportunity_evidence_and_resume_version_flow():
    engine, factory, client = _client()
    try:
        created = client.post(
            "/api/v1/opportunities",
            json={
                "title": "Senior Platform Engineer",
                "company": "Example Co",
                "location": "Remote",
                "resume_id": 10,
                "job_description": "Build Python and PostgreSQL systems with measurable reliability outcomes.",
                "priority": "high",
            },
        )
        assert created.status_code == 201, created.text
        opportunity = created.json()
        opportunity_id = opportunity["id"]
        assert opportunity["job_snapshot"]["title"] == "Senior Platform Engineer"

        imported = client.post("/api/v1/evidence-items/import-resume/10")
        assert imported.status_code == 200, imported.text
        evidence_id = imported.json()["created"][0]["id"]
        approved = client.patch(
            f"/api/v1/evidence-items/{evidence_id}",
            json={"approval_state": "approved"},
        )
        assert approved.status_code == 200
        edited = client.patch(
            f"/api/v1/evidence-items/{evidence_id}",
            json={"evidence_text": "Reduced API latency by 40 percent after profiling."},
        )
        assert edited.status_code == 200
        assert edited.json()["approval_state"] == "pending"
        reapproved = client.patch(
            f"/api/v1/evidence-items/{evidence_id}",
            json={"approval_state": "approved"},
        )
        assert reapproved.status_code == 200

        version = client.post(
            "/api/v1/resume-versions",
            json={
                "resume_id": 10,
                "opportunity_id": opportunity_id,
                "label": "Example Co application",
                "evidence_ids": [evidence_id],
            },
        )
        assert version.status_code == 201, version.text
        version_id = version.json()["id"]
        assert version.json()["version_number"] == 1

        stage = client.post(
            f"/api/v1/opportunities/{opportunity_id}/stage",
            json={"stage": "applied", "resume_version_id": version_id, "note": "Applied on careers site"},
        )
        assert stage.status_code == 200, stage.text
        assert stage.json()["stage"] == "applied"

        reminder = client.post(
            "/api/v1/reminders",
            json={
                "opportunity_id": opportunity_id,
                "message": "Follow up with recruiter",
                "due_at": "2026-08-10T09:00:00Z",
            },
        )
        assert reminder.status_code == 201, reminder.text

        contact = client.post(
            f"/api/v1/opportunities/{opportunity_id}/contacts",
            json={"name": "Recruiter", "role": "Talent Partner"},
        )
        assert contact.status_code == 201, contact.text

        detail = client.get(f"/api/v1/opportunities/{opportunity_id}")
        assert detail.status_code == 200, detail.text
        body = detail.json()
        assert body["stage"] == "applied"
        assert len(body["activity"]) == 2
        assert len(body["contacts"]) == 1
        assert len(body["reminders"]) == 1
        assert len(body["resume_versions"]) == 1

        exported = client.get(f"/api/v1/opportunities/{opportunity_id}/export")
        assert exported.status_code == 200, exported.text
        assert exported.headers["content-disposition"].endswith(f'{opportunity_id}.json"')
        export_body = exported.json()
        assert export_body["opportunity"]["job_snapshot"]["title"] == "Senior Platform Engineer"
        assert export_body["opportunity"]["resume_versions"][0]["submitted_at"] is not None
        assert export_body["evidence_items"][0]["id"] == evidence_id

        with factory() as db:
            assert db.query(ApplicationEvent).count() == 2
            assert db.query(EvidenceItem).one().approval_state == "approved"
    finally:
        client.close()
        engine.dispose()


def test_workspace_rejects_cross_user_resume_and_unknown_opportunity():
    engine, _, client = _client()
    try:
        response = client.post(
            "/api/v1/opportunities",
            json={"title": "Private role", "resume_id": 20},
        )
        assert response.status_code == 404
        assert client.get("/api/v1/opportunities/opp_not_owned").status_code == 404
    finally:
        client.close()
        engine.dispose()


def test_applied_stage_requires_exact_resume_version():
    engine, _, client = _client()
    try:
        created = client.post(
            "/api/v1/opportunities",
            json={
                "title": "Platform Engineer",
                "resume_id": 10,
                "job_description": "Build reliable Python services and PostgreSQL data systems.",
            },
        )
        opportunity_id = created.json()["id"]
        response = client.post(
            f"/api/v1/opportunities/{opportunity_id}/stage",
            json={"stage": "applied"},
        )
        assert response.status_code == 422
        assert "exact resume version" in response.json()["detail"]
    finally:
        client.close()
        engine.dispose()


def test_resume_version_downloads_private_pdf_and_editable_docx():
    engine, factory, client = _client()
    try:
        created = client.post(
            "/api/v1/opportunities",
            json={
                "title": "Platform Engineer",
                "company": "Example Co",
                "resume_id": 10,
                "job_description": "Build reliable Python services and PostgreSQL data systems.",
            },
        )
        opportunity_id = created.json()["id"]
        version = client.post(
            "/api/v1/resume-versions",
            json={
                "resume_id": 10,
                "opportunity_id": opportunity_id,
                "label": "Example Co Platform Engineer",
                "structured_content": {
                    "target_job_title": "Platform Engineer",
                    "summary_items": [
                        {
                            "text": "Python engineer focused on reliable data systems.",
                            "evidence_ids": ["evd_internal_trace"],
                        }
                    ],
                    "bullets": [
                        {
                            "text": "Improved <API> & reliability\u0001 through focused profiling.",
                            "evidence_ids": ["evd_internal_trace"],
                        }
                    ],
                    "skills": ["Python", "PostgreSQL"],
                    "evidence_policy": "approved_only",
                },
            },
        )
        assert version.status_code == 201, version.text
        version_id = version.json()["id"]

        pdf = client.get(f"/api/v1/resume-versions/{version_id}/download?format=pdf")
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"] == "application/pdf"
        assert pdf.headers["cache-control"] == "private, no-store"
        assert pdf.headers["x-content-type-options"] == "nosniff"
        assert pdf.headers["content-disposition"].endswith("-v1.pdf\"")
        assert pdf.content.startswith(b"%PDF-")

        docx = client.get(f"/api/v1/resume-versions/{version_id}/download?format=docx")
        assert docx.status_code == 200, docx.text
        assert docx.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert docx.headers["content-disposition"].endswith("-v1.docx\"")
        document = DocxDocument(BytesIO(docx.content))
        text = "\n".join(paragraph.text for paragraph in document.paragraphs)
        assert "Owner" in text
        assert "Platform Engineer" in text
        assert "Improved <API> & reliability" in text
        assert "Reduced API latency by 40 percent" in text
        assert "evd_internal_trace" not in text
        assert "\x01" not in text

        with factory() as db:
            db.add(
                ResumeVersion(
                    id="rsv_private",
                    user_id=2,
                    resume_id=20,
                    version_number=1,
                    label="Private version",
                    structured_content={},
                    evidence_ids=[],
                )
            )
            db.commit()
        assert (
            client.get("/api/v1/resume-versions/rsv_private/download?format=pdf").status_code
            == 404
        )
    finally:
        client.close()
        engine.dispose()


def test_career_memory_is_explicit_editable_and_deletable():
    engine, _, client = _client()
    try:
        first = client.put(
            "/api/v1/career-memory",
            json={
                "category": "preferences",
                "memory_key": "target.location",
                "value": "Remote India",
            },
        )
        assert first.status_code == 200, first.text
        memory_id = first.json()["id"]
        updated = client.put(
            "/api/v1/career-memory",
            json={
                "category": "preferences",
                "memory_key": "target.location",
                "value": "Bengaluru or remote",
            },
        )
        assert updated.json()["id"] == memory_id
        assert updated.json()["value"] == "Bengaluru or remote"
        assert len(client.get("/api/v1/career-memory").json()) == 1
        assert client.delete(f"/api/v1/career-memory/{memory_id}").status_code == 204
        assert client.get("/api/v1/career-memory").json() == []
    finally:
        client.close()
        engine.dispose()
