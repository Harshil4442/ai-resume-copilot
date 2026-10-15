from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from backend.app.database import get_db
from backend.app.domains.analysis import schemas, tasks
from backend.app.domains.analysis.tailoring_failures import (
    persist_tailoring_failure,
    public_tailoring_guidance,
)
from backend.app.models import AnalysisRun, Resume, ResumeVersion, UsageEvent, User
from backend.app.security import get_current_user
from backend.app.services import llm_client
from backend.app.services.resume_layout import ResumeLayoutError, apply_source_edits
from backend.tests.native_docx_ci_fixtures import configure_native_docx_worker
from backend.tests.test_analysis_runs import (
    PDF_OVERFLOW_ORIGINAL,
    PDF_OVERFLOW_REPLACEMENT,
    TAILORED_EXPERIENCE,
    _configure_pdf_tailoring_source,
    _create_tailoring_run,
    _database,
    _pdf_tailoring_edit,
    _source_edit_response,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Load the unchanged analysis transport in isolation. The application-wide v1
# registry also imports unrelated protected pairing code; this test is evidence
# for this route/SQLite contract, not global router or full browser verification.
_router_spec = importlib.util.spec_from_file_location(
    "backend.app.routers.v1._tailoring_contract_router",
    Path(__file__).parents[1] / "app/routers/v1/analysis.py",
)
assert _router_spec is not None and _router_spec.loader is not None
_router_module = importlib.util.module_from_spec(_router_spec)
_router_spec.loader.exec_module(_router_module)
router = _router_module.router


def _poll_client(factory, owner=1):
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def database():
        with factory() as db:
            yield db

    def user():
        with factory() as db:
            return db.get(User, owner)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = user
    return TestClient(app)


@pytest.mark.parametrize("kind", ["docx", "pdf"])
def test_real_rejected_layout_refunds_once_and_polling_has_actionable_guidance(monkeypatch, kind):
    if kind == "docx":
        configure_native_docx_worker(monkeypatch)
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    calls = []
    if kind == "pdf":
        source, units = _configure_pdf_tailoring_source(factory)
        edit = _pdf_tailoring_edit(units[PDF_OVERFLOW_ORIGINAL], PDF_OVERFLOW_REPLACEMENT)
        with pytest.raises(ResumeLayoutError):
            apply_source_edits(source, "pdf", [edit])
    else:
        with factory() as db:
            source = db.get(Resume, 10).source_document

    def propose(**kwargs):
        calls.append(kwargs)
        if kind == "pdf":
            return {"source_edits": [edit], "evidence_needed": [], "evidence_policy": "approved_only"}
        return _source_edit_response(kwargs["source_units"], replacement=TAILORED_EXPERIENCE * 5)

    monkeypatch.setattr(llm_client, "tailor_resume_from_evidence", propose)
    expected_reason = "source_layout_changed" if kind == "docx" else "replacement_does_not_fit"
    expected_action = "keep_original_layout" if kind == "docx" else "shorten_wording"
    try:
        run_id = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(run_id) == "failed"
        assert tasks.process_analysis_run(run_id) == "failed"
        assert len(calls) == 2
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            assert run.error_code == "TailoringOutputError"
            assert run.error_message == "resume-tailoring-failure/v1:" + expected_reason
            assert run.committed_units == 0 and run.usage_state == "released"
            assert run.attempt_count == 1 and run.result_payload is None
            assert db.get(User, 1).ai_credits == 50
            assert db.get(Resume, 10).source_document == source
            assert db.query(ResumeVersion).count() == 0
            events = db.query(UsageEvent).order_by(UsageEvent.created_at).all()
            assert [(event.event_type, event.amount) for event in events] == [("reserve", -2), ("release", 2)]
        with _poll_client(factory) as client:
            response = client.get(f"/api/v1/analysis-runs/{run_id}")
            assert response.status_code == 200
            data = response.json()
            assert data["failure_guidance"] == {"reason_code": expected_reason, "next_action": expected_action}
            assert "error_message" not in data
            assert "unit_id" not in response.text and TAILORED_EXPERIENCE not in response.text
            assert client.get("/api/v1/analysis-runs").json()["items"][0]["failure_guidance"] == data["failure_guidance"]
            assert client.get(f"/api/v1/analysis-runs/{run_id}/result").status_code == 409
        with _poll_client(factory, owner=2) as client:
            assert client.get(f"/api/v1/analysis-runs/{run_id}").status_code == 404
            assert client.get("/api/v1/analysis-runs").json()["items"] == []
        assert len(calls) == 2  # Polling and reading errors never regenerate.
    finally:
        engine.dispose()


def test_unavailable_real_tex_compiler_maps_to_original_pdf_next_action(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.delenv("NATIVE_TEX_COMPILER_URL", raising=False)
    monkeypatch.delenv("NATIVE_TEX_IMAGE", raising=False)
    monkeypatch.delenv("NATIVE_TEX_IMAGE_DIGEST", raising=False)
    source = b"\\documentclass{article}\n\\begin{document}\nDeveloped reliable Python services for customers and improved system performance.\n\\end{document}\n"
    with factory() as db:
        resume = db.get(Resume, 10)
        resume.source_document = source
        resume.source_format = "tex"
        db.commit()
    calls = []

    def propose(**kwargs):
        calls.append(kwargs)
        return _source_edit_response(kwargs["source_units"], replacement=TAILORED_EXPERIENCE)

    monkeypatch.setattr(llm_client, "tailor_resume_from_evidence", propose)
    try:
        run_id = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(run_id) == "failed"
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            data = schemas.AnalysisRunResponse.model_validate(run).model_dump(mode="json")
            assert data["failure_guidance"] == {"reason_code": "tex_worker_unavailable", "next_action": "use_original_or_custom"}
            assert run.committed_units == 0 and run.usage_state == "released"
            assert db.get(Resume, 10).source_document == source
            assert db.query(ResumeVersion).count() == 0
    finally:
        engine.dispose()


@pytest.mark.parametrize("private", [
    "provider stderr: /private/customer/resume.docx private@example.com",
    "The original PDF font is missing a required glyph. /private/customer.pdf",
    "resume-tailoring-failure/v1:/private/customer.pdf",
    "<script>private candidate document text</script>",
])
def test_unknown_provider_and_legacy_strings_never_enter_public_guidance(monkeypatch, caplog, private):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    def fail(**kwargs):
        error = llm_client.TailoringOutputError(private, reason_code="/private/customer.pdf", unit_id="private@example.com")
        raise error

    monkeypatch.setattr(llm_client, "tailor_resume_from_evidence", fail)
    try:
        run_id = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(run_id) == "failed"
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            assert run.error_message == "resume-tailoring-failure/v1:validation_failed"
            data = schemas.AnalysisRunResponse.model_validate(run).model_dump(mode="json")
            assert data["failure_guidance"] == {"reason_code": "validation_failed", "next_action": "use_original_or_custom"}
            assert private not in json.dumps(data) and private not in caplog.text
            # Persisted rows written before this contract must also stay generic.
            run.error_message = private
            data = schemas.AnalysisRunResponse.model_validate(run).model_dump(mode="json")
            assert data["failure_guidance"]["reason_code"] == "validation_failed"
            assert private not in json.dumps(data)
    finally:
        engine.dispose()


@pytest.mark.parametrize(("message", "reason", "action"), [
    ("The original PDF font is missing a required glyph.", "font_unsupported", "use_supported_font"),
    ("The replacement changes an original DOCX hyperlink label.", "source_links_protected", "keep_link_text"),
    ("The replacement changes text covered by an original PDF link.", "source_links_protected", "keep_link_text"),
    ("An included TeX source or asset is missing. Include every project file in the ZIP or upload PDF.", "tex_dependency_unsupported", "check_tex_dependencies"),
    ("Signed PDFs cannot be tailored without invalidating the signature.", "source_protected", "use_original_or_custom"),
    ("Return between one and twelve useful source replacements", "no_supported_changes", "use_original_or_custom"),
])
def test_fixed_native_causes_survive_the_final_wrapper(message, reason, action):
    outer = llm_client.TailoringOutputError("Could not apply useful changes while preserving the resume format")
    outer.__cause__ = ResumeLayoutError(message, unit_id="private:unit", repair_hint="private provider detail")
    marker = persist_tailoring_failure(outer)
    assert public_tailoring_guidance("resume_tailor", "failed", marker) == {"reason_code": reason, "next_action": action}
    assert "private" not in marker


def test_bounded_cause_traversal_and_arbitrary_reason_attributes_are_generic():
    error = llm_client.TailoringOutputError("private provider text", reason_code="font_unsupported")
    error.__cause__ = error
    assert persist_tailoring_failure(error) == "resume-tailoring-failure/v1:validation_failed"
    assert public_tailoring_guidance("job_match", "failed", "resume-tailoring-failure/v1:font_unsupported") is None
    assert public_tailoring_guidance("resume_tailor", "succeeded", "resume-tailoring-failure/v1:font_unsupported") is None
    assert public_tailoring_guidance("resume_tailor", "queued", None) is None


def test_actual_isolated_analysis_router_openapi_matches_composed_contract():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    actual = app.openapi()["components"]["schemas"]
    composed = json.loads((Path(__file__).parents[1] / "openapi.json").read_text())["components"]["schemas"]
    for name in ("AnalysisRunResponse", "TailoringFailureGuidance"):
        assert actual[name] == composed[name]
    assert "failure_guidance" not in actual["AnalysisRunResponse"]["required"]
    assert "error_message" not in actual["AnalysisRunResponse"]["properties"]
