from __future__ import annotations

from contextlib import closing
from io import BytesIO
from zipfile import ZipFile

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
import pytest
from backend.app.database import Base, get_db
from backend.app.models import ApplicationEvent, EvidenceItem, Resume, ResumeVersion, User
from backend.app.routers.v1 import router as v1_router
from backend.app.security import get_current_user
from backend.app.services.resume_layout import extract_source_units
from docx import Document as DocxDocument
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.shared import Inches, Pt
from fastapi import FastAPI
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

SOURCE_EXPERIENCE = "Developed reliable Python services for customers and improved system performance."
TAILORED_EXPERIENCE = "Built reliable Python services for customers and improved system performance."


def _native_source(source_format):
    output = BytesIO()
    if source_format == "pdf":
        document = Canvas(output)
        document.setFont("Times-Bold", 16)
        document.drawString(50, 750, "Owner")
        document.setFont("Times-Bold", 11)
        document.drawString(50, 710, "EXPERIENCE")
        document.line(50, 704, 540, 704)
        document.drawString(50, 682, "Acme Engineer")
        document.drawRightString(540, 682, "Remote | 2023-2025")
        document.setFont("Times-Roman", 10)
        document.drawString(50, 656, SOURCE_EXPERIENCE)
        document.drawString(50, 50, "Original footer")
        document.save()
    else:
        document = DocxDocument()
        document.styles["Normal"].font.name = "Times New Roman"
        document.styles["Normal"].font.size = Pt(10)
        name = document.add_paragraph()
        name.alignment = WD_ALIGN_PARAGRAPH.CENTER
        name.add_run("Owner").bold = True
        document.add_heading("Experience", level=2)
        employer = document.add_paragraph()
        employer.paragraph_format.tab_stops.add_tab_stop(Inches(6), WD_TAB_ALIGNMENT.RIGHT)
        employer.add_run("Acme Engineer").bold = True
        employer.add_run("\tRemote | 2023-2025")
        body = document.add_paragraph()
        body.add_run("Developed ")
        body.add_run("reliable Python services").bold = True
        body.add_run(" for customers and improved system performance.")
        document.sections[0].footer.paragraphs[0].text = "Original footer"
        document.save(output)
    return output.getvalue()


def _pdf_text_snapshot(content):
    with pdfium.PdfDocument(content) as document:
        snapshots = []
        for index in range(len(document)):
            with closing(document[index]) as page, closing(page.get_textpage()) as textpage:
                snapshots.append([
                    (obj.extract(), obj.get_font().get_base_name(), obj.get_font_size(), obj.get_bounds())
                    for obj in page.get_objects(textpage=textpage)
                    if obj.type == pdfium_raw.FPDF_PAGEOBJ_TEXT
                ])
        return snapshots


def _client(source_format="docx"):
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
                    original_filename=f"resume.{source_format}",
                    source_document=_native_source(source_format),
                    source_format=source_format,
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


@pytest.mark.parametrize("source_format", ["pdf", "docx"])
def test_resume_version_native_download_preserves_layout_and_rejects_cross_format(source_format):
    engine, factory, client = _client(source_format)
    try:
        with factory() as db:
            original = db.get(Resume, 10).source_document
            unit = next(
                item for item in extract_source_units(original, source_format)
                if item["text"].strip() == SOURCE_EXPERIENCE
            )
            db.add(
                EvidenceItem(
                    id="evd_native_export", user_id=1, resume_id=10,
                    category="experience", title="Platform work",
                    evidence_text=SOURCE_EXPERIENCE, approval_state="approved",
                )
            )
            db.commit()
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
                "evidence_ids": ["evd_native_export"],
                "structured_content": {
                    "format_preservation": "source",
                    "source_format": source_format,
                    "source_edits": [
                        {
                            "unit_id": unit["unit_id"],
                            "original_text": unit["text"],
                            "replacement_text": TAILORED_EXPERIENCE,
                            "evidence_ids": ["evd_native_export"],
                            "reason": "Use concise wording supported by approved experience.",
                        }
                    ],
                    "evidence_policy": "approved_only",
                },
            },
        )
        assert version.status_code == 201, version.text
        version_id = version.json()["id"]

        downloaded = client.get(f"/api/v1/resume-versions/{version_id}/download?format={source_format}")
        assert downloaded.status_code == 200, downloaded.text
        assert downloaded.headers["cache-control"] == "private, no-store"
        assert downloaded.headers["x-content-type-options"] == "nosniff"
        assert downloaded.headers["content-disposition"].endswith(f'-v1.{source_format}"')
        if source_format == "pdf":
            assert downloaded.headers["content-type"] == "application/pdf"
            before = _pdf_text_snapshot(original)
            after = _pdf_text_snapshot(downloaded.content)
            assert len(before) == len(after) == 1
            assert len(before[0]) == len(after[0])
            assert [item for item in before[0] if item[0].strip() != SOURCE_EXPERIENCE] == [
                item for item in after[0] if item[0].strip() != TAILORED_EXPERIENCE
            ]
            replacement = next(item for item in after[0] if item[0].strip() == TAILORED_EXPERIENCE)
            assert replacement[1] == "Times-Roman"
        else:
            assert downloaded.headers["content-type"].startswith(
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            )
            edited = DocxDocument(BytesIO(downloaded.content))
            assert edited.paragraphs[-1].text == TAILORED_EXPERIENCE
            assert edited.styles["Normal"].font.name == "Times New Roman"
            assert edited.paragraphs[0].alignment == WD_ALIGN_PARAGRAPH.CENTER
            assert edited.paragraphs[2].text == "Acme Engineer\tRemote | 2023-2025"
            assert any(
                run.bold and run.text == "reliable Python services"
                for run in edited.paragraphs[-1].runs
            )
            with ZipFile(BytesIO(original)) as source, ZipFile(BytesIO(downloaded.content)) as output:
                assert source.namelist() == output.namelist()
                for name in source.namelist():
                    if name != "word/document.xml":
                        assert source.read(name) == output.read(name)
        with factory() as db:
            assert db.get(Resume, 10).source_document == original

        other_format = "docx" if source_format == "pdf" else "pdf"
        cross_format = client.get(f"/api/v1/resume-versions/{version_id}/download?format={other_format}")
        assert cross_format.status_code == 409
        assert source_format.upper() in cross_format.json()["detail"]

        for empty_content in (None, {}):
            snapshot_payload = {"resume_id": 10, "label": "Original snapshot"}
            if empty_content is not None:
                snapshot_payload["structured_content"] = empty_content
            snapshot = client.post("/api/v1/resume-versions", json=snapshot_payload)
            assert snapshot.status_code == 201, snapshot.text
            assert snapshot.json()["structured_content"] == {
                "format_preservation": "source",
                "source_format": source_format,
                "source_edits": [],
            }
            unchanged = client.get(
                f"/api/v1/resume-versions/{snapshot.json()['id']}/download?format={source_format}"
            )
            assert unchanged.status_code == 200, unchanged.text
            assert unchanged.content == original

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


def test_resume_download_rejects_missing_source_old_templates_and_unsafe_edits():
    engine, factory, client = _client()
    try:
        with factory() as db:
            native = db.get(Resume, 10)
            unit = next(item for item in extract_source_units(native.source_document, "docx")
                        if item["text"] == SOURCE_EXPERIENCE)
            db.add(Resume(id=30, user_id=1, original_filename="legacy.pdf", raw_text="Legacy source"))
            db.add_all([
                ResumeVersion(
                    id="rsv_missing_source", user_id=1, resume_id=30, version_number=1,
                    label="Legacy source", evidence_ids=[],
                    structured_content={"format_preservation": "source", "source_format": "pdf", "source_edits": []},
                ),
                ResumeVersion(
                    id="rsv_old_template", user_id=1, resume_id=10, version_number=1,
                    label="Old generic template", evidence_ids=[], structured_content={"summary_items": []},
                ),
                ResumeVersion(
                    id="rsv_unsafe_edit", user_id=1, resume_id=10, version_number=2,
                    label="Unsafe edit", evidence_ids=[],
                    structured_content={
                        "format_preservation": "source", "source_format": "docx",
                        "source_edits": [{
                            "unit_id": unit["unit_id"], "original_text": unit["text"],
                            "replacement_text": TAILORED_EXPERIENCE * 5,
                        }],
                    },
                ),
            ])
            db.commit()
        missing = client.get("/api/v1/resume-versions/rsv_missing_source/download?format=pdf")
        assert missing.status_code == 409
        assert "Upload" in missing.json()["detail"]
        generic = client.get("/api/v1/resume-versions/rsv_old_template/download?format=docx")
        assert generic.status_code == 409
        assert "old resume template" in generic.json()["detail"]
        unsafe = client.get("/api/v1/resume-versions/rsv_unsafe_edit/download?format=docx")
        assert unsafe.status_code == 409
        assert "too long" in unsafe.json()["detail"]
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
