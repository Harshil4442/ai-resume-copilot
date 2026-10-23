"""Real fractional-width PDF rewrites with synthetic generation and local state."""
from __future__ import annotations

import json
from io import BytesIO

import pytest
from backend.app.domains.analysis import operations, tasks
from backend.app.models import AnalysisRun, ModelCallEvent, Resume, ResumeVersion, UsageEvent, User
from backend.app.services import llm_client, resume_layout
from backend.app.services.pdf_streams import PdfStreamError
from backend.app.services.resume_artifacts import render_resume_version
from backend.app.services.resume_layout import (
    ResumeLayoutError,
    apply_source_edits,
    extract_source_units,
)
from backend.tests.test_analysis_runs import _create_tailoring_run, _database
from pypdf import PdfReader, PdfWriter
from pypdf.generic import ArrayObject, FloatObject, NameObject, NumberObject
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

ORIGINALS = (
    "Created robust services with secure access.",
    "Created stable services with secure access.",
)
STREAM_ERROR = "The original PDF text stream cannot be updated safely."


def fractional_pdf_edits(*, third_passage=False):
    """A large trim exceeds 0.1pt cursor precision; a concise verb change fits."""
    output = BytesIO()
    canvas = Canvas(output, pagesize=(612, 792), invariant=True)
    canvas.setFont("Times-Roman", 11)
    passages = ORIGINALS + ("Created useful services with secure access.",) if third_passage else ORIGINALS
    for index, text in enumerate(passages):
        canvas.drawString(60, 650 - index * 30, text)
    canvas.save()
    writer = PdfWriter(clone_from=PdfReader(BytesIO(output.getvalue())))
    fonts = writer.pages[0]["/Resources"]["/Font"]
    font = next(value.get_object() for value in fonts.values()
                if value.get_object()["/BaseFont"] == "/Times-Roman")
    font[NameObject("/FirstChar")] = NumberObject(32)
    font[NameObject("/LastChar")] = NumberObject(126)
    font[NameObject("/Widths")] = ArrayObject([
        FloatObject(pdfmetrics.stringWidth(chr(code), "Times-Roman", 1000) + 0.5)
        for code in range(32, 127)
    ])
    result = BytesIO()
    writer.write(result)
    source = result.getvalue()
    units = {unit["text"].strip(): unit for unit in extract_source_units(source, "pdf")}
    assert set(units) == set(passages)

    def edit(index, replacement):
        unit = units[ORIGINALS[index]]
        return {"unit_id": unit["unit_id"], "original_text": unit["text"],
            "replacement_text": replacement, "evidence_ids": ["evd_approved"],
            "reason": "Use concise wording supported by the original approved experience."}

    bad = edit(0, "Built secure services.")
    safe = edit(1, "Built stable services with secure access.")
    second_bad = edit(1, "Built stable services.")
    first_safe = edit(0, "Built robust services with secure access.")
    return source, bad, safe, second_bad, first_safe


def test_real_fractional_width_refusal_isolated_from_safe_changed_pdf():
    source, bad, safe, _, _ = fractional_pdf_edits()
    for edits in ([bad], [bad, safe]):
        with pytest.raises(ResumeLayoutError, match="text stream cannot be updated safely") as failure:
            apply_source_edits(source, "pdf", edits)
        assert failure.value.unit_id is None
        assert isinstance(failure.value.__cause__, PdfStreamError)
        assert str(failure.value.__cause__) == "PDF fractional widths exceed the safe native cursor precision limit."
    result = apply_source_edits(source, "pdf", [safe])
    assert result != source
    text = PdfReader(BytesIO(result)).pages[0].extract_text()
    assert ORIGINALS[0] in text and safe["replacement_text"] in text
    assert ORIGINALS[1] not in text


def _configure_source(factory, source):
    with factory() as db:
        resume = db.get(Resume, 10)
        resume.source_document = source
        resume.source_format = "pdf"
        resume.original_filename = "fractional-width.pdf"
        db.commit()


def _proposal(monkeypatch, edits):
    calls = []
    def response(messages):
        calls.append(messages)
        return json.dumps({"source_edits": edits, "evidence_needed": []})
    monkeypatch.setattr(llm_client, "_chat", response)
    return calls


def test_operation_isolates_real_stream_refusal_saves_changed_partial_and_charges_once(monkeypatch):
    source, bad, safe, _, _ = fractional_pdf_edits()
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    calls = _proposal(monkeypatch, [bad, safe])
    native_calls = []
    real_apply = resume_layout.apply_source_edits
    def apply(data, kind, edits):
        native_calls.append(len(edits))
        return real_apply(data, kind, edits)
    monkeypatch.setattr(resume_layout, "apply_source_edits", apply)
    try:
        _configure_source(factory, source)
        run_id = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(run_id) == "succeeded"
        assert tasks.process_analysis_run(run_id) == "succeeded"
        assert len(calls) == 1
        # Initial batch, two bounded probes, then full retained-batch proof.
        assert native_calls == [2, 1, 1, 1]
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            resume = db.get(Resume, 10)
            version = db.query(ResumeVersion).one()
            assert run.usage_state == "committed" and run.committed_units == 2
            assert run.attempt_count == 1 and db.get(User, 1).ai_credits == 48
            assert version.generation_run_id == run_id
            assert version.structured_content["source_edits"] == [safe]
            assert version.structured_content["partial_tailoring"] is True
            assert version.structured_content["omitted_edits"] == 1
            assert resume.source_document == source
            artifact = render_resume_version(version, resume, "pdf")
            assert artifact.content != source
            text = PdfReader(BytesIO(artifact.content)).pages[0].extract_text()
            assert ORIGINALS[0] in text and safe["replacement_text"] in text
            assert ORIGINALS[1] not in text and bad["replacement_text"] not in text
            assert [(event.event_type, event.amount) for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                ("reserve", -2), ("commit", 0)]
            assert db.query(ModelCallEvent).one().status == "succeeded"
    finally:
        engine.dispose()


@pytest.mark.parametrize("failure", ["all_refused", "global_interaction"])
def test_unisolatable_stream_failure_stops_generation_and_refunds_once(monkeypatch, failure):
    source, bad, safe, second_bad, first_safe = fractional_pdf_edits()
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    proposals = [bad, second_bad] if failure == "all_refused" else [first_safe, safe]
    calls = _proposal(monkeypatch, proposals)
    native_calls = []
    real_apply = resume_layout.apply_source_edits
    def apply(data, kind, edits):
        native_calls.append(len(edits))
        if failure == "global_interaction" and len(edits) > 1:
            raise ResumeLayoutError(STREAM_ERROR)
        return real_apply(data, kind, edits)
    monkeypatch.setattr(resume_layout, "apply_source_edits", apply)
    try:
        _configure_source(factory, source)
        run_id = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(run_id) == "failed"
        assert tasks.process_analysis_run(run_id) == "failed"
        assert len(calls) == 1 and native_calls == [2, 1, 1]
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            assert run.usage_state == "released" and run.committed_units == 0
            assert run.attempt_count == 1 and run.result_payload is None
            assert db.query(ResumeVersion).count() == 0
            assert db.query(ModelCallEvent).count() == 0
            assert db.get(Resume, 10).source_document == source
            assert db.get(User, 1).ai_credits == 50
            assert [(event.event_type, event.amount) for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                ("reserve", -2), ("release", 2)]
    finally:
        engine.dispose()


def test_different_fidelity_failure_in_surviving_batch_cannot_request_generation(monkeypatch):
    source, bad, safe, _, _ = fractional_pdf_edits(third_passage=True)
    third = next(unit for unit in extract_source_units(source, "pdf") if "useful" in unit["text"])
    third_edit = {**safe, "unit_id": third["unit_id"], "original_text": third["text"],
                  "replacement_text": third["text"].replace("Created", "Built")}
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    calls = _proposal(monkeypatch, [bad, safe, third_edit])
    native_calls = []
    real_apply = resume_layout.apply_source_edits
    def apply(data, kind, edits):
        native_calls.append(len(edits))
        if [edit["unit_id"] for edit in edits] == [safe["unit_id"], third_edit["unit_id"]]:
            raise ResumeLayoutError("The PDF changed visually outside the accepted text slots.")
        return real_apply(data, kind, edits)
    monkeypatch.setattr(resume_layout, "apply_source_edits", apply)
    try:
        _configure_source(factory, source)
        run_id = _create_tailoring_run(factory)
        assert tasks.process_analysis_run(run_id) == "failed"
        assert tasks.process_analysis_run(run_id) == "failed"
        assert len(calls) == 1
        assert native_calls == [3, 1, 1, 1, 2]
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            assert run.usage_state == "released" and run.committed_units == 0
            assert run.attempt_count == 1 and run.result_payload is None
            assert db.query(ResumeVersion).count() == 0
            assert db.get(Resume, 10).source_document == source
            assert db.get(User, 1).ai_credits == 50
            assert [(event.event_type, event.amount) for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                ("reserve", -2), ("release", 2)]
    finally:
        engine.dispose()


@pytest.mark.parametrize("count", [1, 13])
def test_isolation_refuses_outside_bounded_model_proposal_count(monkeypatch, count):
    monkeypatch.setattr(resume_layout, "apply_source_edits", lambda *a: pytest.fail("unbounded native probes"))
    assert operations._isolate_pdf_stream_edits(b"unused", [{}] * count) is None


def test_isolation_checks_each_of_at_most_twelve_proposals(monkeypatch):
    edits = [{"probe": index} for index in range(12)]
    calls = []
    def apply(source, kind, selected):
        assert len(selected) == 1
        calls.append(selected[0]["probe"])
        if selected[0]["probe"] == 0:
            raise ResumeLayoutError(STREAM_ERROR)
        return b"changed-and-verified"
    monkeypatch.setattr(resume_layout, "apply_source_edits", apply)
    assert operations._isolate_pdf_stream_edits(b"source", edits) == edits[1:]
    assert calls == list(range(12))
