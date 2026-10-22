"""Native admission/custody with synthetic services and disposable PostgreSQL.

These tests do not establish deployed scanner/compiler isolation or GCS behavior.
"""
from __future__ import annotations

import hashlib
from dataclasses import replace
from types import SimpleNamespace

import pytest
from backend.app import models as core
from backend.app.domains.resume_uploads import privacy, schemas, service, tasks
from backend.app.domains.resume_uploads.models import ResumeUpload, ResumeUploadCleanup
from backend.app.services import document_ingestion, native_tex
from backend.app.services.resume_layout import ResumeLayoutError, ResumeProcessingUnavailable
from backend.tests import test_employer_admissions_postgres as pg
from backend.tests import test_resume_upload_quarantine as upload
from backend.tests.test_resume_source import _client
from pydantic import ValidationError
from sqlalchemy import text

pg_engine = upload.pg_engine
SOURCE = b"synthetic TeX project; no actual compiler or scanner is used here"
PDF = b"%PDF-synthetic-private-compiler-result"
IMAGE = "synthetic-image"
POLICY = "synthetic-policy"
RENDERER = "synthetic-tex@sha256:" + "c" * 64


def inspection(content, kind, *, extracted=False):
    return document_ingestion.InspectedResume(
        parsed=("Compiled resume text" if extracted else "", {}, ["Python"] if extracted else [], 2.0 if extracted else 0.0, {}),
        sha256=hashlib.sha256(content).hexdigest(), size_bytes=len(content), source_format=kind,
        worker_image=IMAGE, policy_sha256=POLICY, scan_engine="ClamAV",
        scan_version="synthetic-1.0", scan_definitions="synthetic-definition-set",
    )


def compiled(content=SOURCE, kind="tex"):
    return SimpleNamespace(pdf=PDF, renderer_image=RENDERER, renderer_policy_sha256="d" * 64,
                           source_inspection=inspection(content, kind),
                           pdf_inspection=inspection(PDF, "pdf", extracted=True))


@pytest.fixture
def context(pg_engine, monkeypatch):
    result = upload.context.__wrapped__(pg_engine, monkeypatch)
    pg._migrate(pg_engine, "20261023_0020")
    monkeypatch.setattr(native_tex, "compile_project_inspected", lambda data, kind: compiled(data, kind))
    return result


@pytest.mark.parametrize("filename,kind,media", [
    ("resume.tex", "tex", "application/x-tex"),
    ("project.ZIP", "texzip", "application/zip"),
])
def test_native_quarantine_retains_original_and_composed_receipt_without_charging(context, filename, kind, media):
    identifier, target = upload.queue(context, content=SOURCE, filename=filename)
    assert target.source_format == kind and target.media_type == media
    assert tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        resume = db.get(core.Resume, row.result_resume_id)
        assert row.state == "released" and resume.source_document == SOURCE
        assert resume.source_format == kind and resume.raw_text == "Compiled resume text"
        assert resume.skills == ["Python"]
        assert row.scan_receipt["sha256"] == hashlib.sha256(SOURCE).hexdigest()
        assert row.scan_receipt["source_format"] == kind
        native = row.scan_receipt["native_compilation"]
        assert native["renderer_image"] == RENDERER
        assert native["renderer_policy_sha256"] == "d" * 64
        assert native["pdf_inspection"]["sha256"] == hashlib.sha256(PDF).hexdigest()
        assert native["pdf_inspection"]["source_format"] == "pdf"
        assert native["pdf_inspection"]["scan_engine"] == "ClamAV"
        grant = service.source_access(db, 1, resume.id, context[1])
        assert (grant.media_type, grant.filename, grant.size_bytes, grant.sha256) == (
            media, filename, len(SOURCE), hashlib.sha256(SOURCE).hexdigest())
        assert db.get(core.User, 1).ai_credits == 100
        assert db.get(core.User, 1).job_service_credits == 0


@pytest.mark.parametrize("boundary", ["source", "pdf"])
def test_wrong_native_receipt_never_releases_or_writes_clean_source(context, monkeypatch, boundary):
    value = compiled()
    field = "source_inspection" if boundary == "source" else "pdf_inspection"
    setattr(value, field, replace(getattr(value, field), sha256="0" * 64))
    monkeypatch.setattr(native_tex, "compile_project_inspected", lambda *_: value)
    identifier, target = upload.queue(context, content=SOURCE, filename="resume.tex")
    assert not tasks.process(context[0], identifier, context[1])
    upload.assert_no_saved(context)
    assert (target.clean_bucket, target.clean_name) not in context[1].objects
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        assert row.state == "queued" and row.last_error_code == "resume_inspection_unavailable"


@pytest.mark.parametrize("error,state", [
    (ResumeLayoutError("Unsupported source dependency"), "rejected"),
    (ResumeProcessingUnavailable("Compiler service unavailable"), "queued"),
])
def test_document_refusal_and_service_outage_have_different_retry_outcomes(context, monkeypatch, error, state):
    def fail(*_):
        raise error
    monkeypatch.setattr(native_tex, "compile_project_inspected", fail)
    identifier, _ = upload.queue(context, content=SOURCE, filename="resume.tex")
    assert not tasks.process(context[0], identifier, context[1])
    upload.assert_no_saved(context)
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == state
        cleanup = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one_or_none()
        assert (cleanup is not None) == (state == "rejected")


def test_native_cancellation_during_compilation_cannot_release_a_late_result(context, monkeypatch):
    identifier, target = upload.queue(context, content=SOURCE, filename="project.zip")
    def cancel_then_return(data, kind):
        upload.cancel(context, identifier)
        return compiled(data, kind)
    monkeypatch.setattr(native_tex, "compile_project_inspected", cancel_then_return)
    assert not tasks.process(context[0], identifier, context[1])
    upload.assert_no_saved(context)
    assert (target.clean_bucket, target.clean_name) not in context[1].objects
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).state == "cancelled"
        assert db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one_or_none()


def test_native_resume_deletion_retains_detached_cleanup_and_closes_both_targets(context):
    identifier, target = upload.queue(context, content=SOURCE, filename="project.zip")
    assert tasks.process(context[0], identifier, context[1])
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        db.delete(db.get(core.Resume, row.result_resume_id))
        db.commit()
    # The existing PostgreSQL FK unlink trigger revokes the owner binding and
    # preserves cleanup even when deletion does not call the new upload domain.
    assert privacy.cleanup_due(context[0], context[1], limit=1) == 1
    with context[0]() as db:
        row = db.get(ResumeUpload, identifier)
        cleanup = db.query(ResumeUploadCleanup).filter_by(upload_id=identifier).one()
        assert row.state == "cancelled" and row.scan_receipt is None
        assert cleanup.payload_retired_at is not None
    for bucket, name in [(target.quarantine_bucket, target.quarantine_name), (target.clean_bucket, target.clean_name)]:
        content, metadata = context[1].objects[bucket, name]
        assert content == b"" and metadata.retired


def test_schema20_preserves_rows_and_refuses_lossy_downgrade(context, pg_engine):
    identifier, _ = upload.queue(context, content=SOURCE, filename="project.zip")
    with pytest.raises(RuntimeError, match="cleanup history must be retained"):
        pg._migrate(pg_engine, "20261015_0019", direction="downgrade")
    with context[0]() as db:
        assert db.scalar(text("SELECT version_num FROM alembic_version")) == "20261023_0020"
        assert db.get(ResumeUpload, identifier).source_format == "texzip"
        assert db.get(core.User, 1).ai_credits == 100


def test_schema19_to20_preserves_existing_pdf_custody_and_round_trips(pg_engine, monkeypatch):
    context = upload.context.__wrapped__(pg_engine, monkeypatch)
    identifier, _ = upload.queue(context)
    assert tasks.process(context[0], identifier, context[1])
    pg._migrate(pg_engine, "20261015_0019")

    def snapshot():
        with pg_engine.connect() as db:
            return {
                table: [dict(row) for row in db.execute(text(f"SELECT * FROM {table} ORDER BY id")).mappings()]
                for table in ("users", "resumes", "resume_uploads", "resume_upload_cleanups")
            }

    before = snapshot()
    pg._migrate(pg_engine, "20261023_0020")
    assert snapshot() == before
    pg._migrate(pg_engine, "20261015_0019", direction="downgrade")
    assert snapshot() == before
    with context[0]() as db:
        assert db.get(ResumeUpload, identifier).scan_receipt["sha256"] == hashlib.sha256(upload.CONTENT).hexdigest()


@pytest.mark.parametrize("filename", ["resume.tex", "project.zip"])
def test_native_legacy_parse_refuses_before_parser_compiler_or_persistence(monkeypatch, filename):
    def no_compile(*_):
        pytest.fail("Legacy upload must not compile or admit native bytes")
    monkeypatch.setattr(native_tex, "compile_project_inspected", no_compile)
    engine, factory, client = _client()
    try:
        response = client.post("/api/resume/parse", files={"file": (filename, SOURCE, "application/octet-stream")})
        assert response.status_code == 409 and "secure upload workflow" in response.json()["detail"]
        with factory() as db:
            assert db.query(core.Resume).count() == 2
            assert db.get(core.User, 1).ai_credits == 50
    finally:
        client.close()
        engine.dispose()


@pytest.mark.parametrize("filename", ["resume", "tex", "resume.exe", "resume.tex\x00"])
def test_secure_native_filename_requires_supported_real_extension(filename):
    with pytest.raises(ValidationError):
        schemas.UploadCreate(filename=filename, size_bytes=1, sha256="a" * 64)
