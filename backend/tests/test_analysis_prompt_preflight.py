"""Quote capacity checks use the same full evidence and source-unit prompts as execution."""

from __future__ import annotations

import hashlib
import inspect
import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.domains.analysis import operations, schemas
from backend.app.domains.analysis import service as analysis_service
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.credits import analysis, finance, runtime
from backend.app.domains.credits.models import CreditReservation
from backend.app.routers.credits import analysis_reuse
from backend.app.services import llm_client
from backend.app.services.analysis_prompt_preflight import (
    interview_prompt_bound,
    tailoring_prompt_bound,
)
from backend.app.services.interview_contract import InterviewContextUnavailable
from backend.app.services.model_cost_policy import input_token_estimate
from backend.app.services.prompt_privacy import redact_messages
from backend.tests.test_analysis_runs import _database
from fastapi import Response
from reportlab.pdfgen.canvas import Canvas
from starlette.requests import Request

_TAILORING_INPUT = {
    "job_title": "Backend Engineer",
    "jd_text": "Build reliable Python APIs with PostgreSQL.",
    "approved_evidence": [
        {
            "id": "ev_1", "title": "API work", "text": "Built a Python API",
            "metrics": {}, "skills": ["Python"],
        }
    ],
    "source_units": [
        {
            "unit_id": "pdf:1", "text": "Built a Python API for customers.",
            "kind": "text_object", "section": "Projects", "generation_priority": 1,
        }
    ],
}


@pytest.mark.parametrize(
    ("repair_note", "digest"),
    [
        ("", "4ecc7fa0e6265b4ebf2deb7e0ae063504b21511e622dfcb1b99a060fbf0ab936"),
        (
            "Source unit pdf:1: The replacement exceeds its slot.",
            "8ed77204c1b457c1d4615a66f2c614a8ae1caf0425cc172378ee341da6ca381c",
        ),
    ],
)
def test_tailoring_builder_keeps_pre_refactor_prompt_bytes(repair_note, digest):
    messages = llm_client.build_tailoring_messages(**_TAILORING_INPUT, repair_note=repair_note)
    serialized = json.dumps(messages, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert hashlib.sha256(serialized.encode()).hexdigest() == digest


def test_legacy_v6_source_checksum_and_full_v7_context_are_distinct(monkeypatch):
    assert hashlib.sha256(
        inspect.getsource(llm_client.tailor_resume_from_evidence).encode()
    ).hexdigest() == "7fa5f456719dfabbb5adbeeb72a39afe7080f586607d9362bd674194d70a70f9"
    extended = {
        **_TAILORING_INPUT,
        "jd_text": "J" * 7_000 + "JOB_SUFFIX",
        "approved_evidence": [
            {**_TAILORING_INPUT["approved_evidence"][0],
             "text": "E" * 4_000 + "EVIDENCE_SUFFIX"}
        ],
    }
    legacy = llm_client.build_tailoring_messages(**extended)[1]["content"]
    full = llm_client.build_tailoring_messages(
        **extended, complete_context=True
    )[1]["content"]
    assert "JOB_SUFFIX" not in legacy and "EVIDENCE_SUFFIX" not in legacy
    assert "JOB_SUFFIX" in full and "EVIDENCE_SUFFIX" in full
    assert tailoring_prompt_bound(**extended, complete_context=True) > tailoring_prompt_bound(**extended)

    seen = []

    def capture(messages):
        seen.append(messages)
        raise RuntimeError("synthetic model stop")

    monkeypatch.setattr(llm_client, "_chat", capture)
    with pytest.raises(RuntimeError, match="synthetic model stop"):
        llm_client.tailor_resume_from_evidence_full(**extended)
    assert seen[0][1]["content"] == full
    assert not llm_client.full_tailoring_context_enabled()


def test_interview_quote_bound_covers_actual_single_repair(monkeypatch):
    seen = []

    def invalid_response(messages):
        seen.append(messages)
        return "[]"

    monkeypatch.setattr(llm_client, "_chat", invalid_response)
    with pytest.raises(llm_client.InterviewOutputError):
        llm_client.generate_interview_questions(
            "Backend Engineer", "Build reliable Python APIs with PostgreSQL.",
            approved_evidence=_TAILORING_INPUT["approved_evidence"],
        )
    assert len(seen) == 2
    assert len(seen[1]) == 3
    assert input_token_estimate(redact_messages(seen[1])) <= interview_prompt_bound(
        "Backend Engineer", "Build reliable Python APIs with PostgreSQL.",
        _TAILORING_INPUT["approved_evidence"],
    )


def test_interview_near_context_limit_fails_before_quote():
    with pytest.raises(InterviewContextUnavailable, match="full interview repair"):
        interview_prompt_bound("Backend Engineer", "x" * 60_000, [])


def test_tailoring_repair_above_quoted_envelope_refuses_before_model(monkeypatch):
    monkeypatch.setattr(llm_client, "_chat", lambda messages: pytest.fail("Model was called"))
    with pytest.raises(ValueError, match="quoted prompt envelope"):
        llm_client.tailor_resume_from_evidence(
            **_TAILORING_INPUT, repair_note="dob:a\n" * 500,
        )


@pytest.mark.parametrize("operation", ["interview_questions", "resume_tailor"])
def test_quote_rejects_oversized_prompt_before_credit_reservation(monkeypatch, operation):
    engine, factory = _database()
    try:
        with factory() as db:
            db.add_all([
                models.Opportunity(
                    id="opp_preflight", user_id=1, resume_id=10,
                    title="Platform Engineer", company="Example Co",
                    job_description="Build and operate reliable Python services for global customers.",
                    job_snapshot={},
                ),
                models.EvidenceItem(
                    id="ev_preflight", user_id=1, resume_id=10,
                    category="experience", title="Platform work",
                    evidence_text="Developed reliable Python services for customers.",
                    skills=["Python"], metrics={}, approval_state="approved",
                ),
            ])
            db.commit()
            route = SimpleNamespace(
                supported_source_formats=("docx",),
                model_quotes=(
                    SimpleNamespace(max_input_tokens=1_000_000),
                    SimpleNamespace(max_input_tokens=1),
                ),
            )
            config = SimpleNamespace(resolve_route=lambda *args, **kwargs: route)
            monkeypatch.setattr(runtime, "current_runtime", lambda user_id: config)
            request = schemas.AnalysisRunCreate(
                operation=operation, opportunity_id="opp_preflight",
                input={"mode": "enhanced", "num_questions": 8}
                if operation == "interview_questions" else {},
            )
            with pytest.raises(finance.FundingUnavailable, match="complete prompt"):
                analysis.prepare_quote_context(db, user_id=1, payload=request)
            assert db.query(CreditReservation).count() == 0
            assert db.query(models.AnalysisRun).count() == 0
            # A route that covers the full bounded prompt can pass admission;
            # this does not activate a paid route or grant quality approval.
            route.model_quotes = (
                SimpleNamespace(max_input_tokens=1_000_000),
                SimpleNamespace(max_input_tokens=1_000_000),
            )
            analysis.prepare_quote_context(db, user_id=1, payload=request)
            if operation == "resume_tailor":
                opportunity = db.get(models.Opportunity, "opp_preflight")
                opportunity.job_description = "Build Python services. " + "x" * 7_000
                db.commit()
                analysis.prepare_quote_context(db, user_id=1, payload=request)
                assert db.query(CreditReservation).count() == 0
    finally:
        engine.dispose()


def test_repair_bound_counts_complete_source_context():
    compact = tailoring_prompt_bound(**_TAILORING_INPUT)
    expanded = {
        **_TAILORING_INPUT,
        "source_units": [
            *_TAILORING_INPUT["source_units"],
            {"unit_id": "pdf:2", "text": "Complex distributed systems " * 100,
             "kind": "text_object", "section": "Experience", "generation_priority": 1},
        ],
    }
    assert tailoring_prompt_bound(**expanded) > compact


def test_paid_source_selection_preserves_fragmented_whole_units(monkeypatch):
    from backend.app.services import resume_layout

    units = [
        {"unit_id": f"pdf:{index}", "text": "A useful experience passage.",
         "generation_priority": 1}
        for index in range(101)
    ]
    monkeypatch.setattr(resume_layout, "extract_source_units", lambda *args: units)
    resume = SimpleNamespace(source_document=b"synthetic", source_format="pdf")
    assert len(operations.tailoring_source_units(resume)) == 100
    assert operations.tailoring_source_units(resume, require_complete=True) == units


def test_fragmented_pdf_quote_uses_actual_route_capacity(monkeypatch):
    output = BytesIO()
    pdf = Canvas(output, pagesize=(612, 792))
    pdf.setFont("Helvetica", 8)
    for index in range(101):
        if index and index % 45 == 0:
            pdf.showPage()
            pdf.setFont("Helvetica", 8)
        pdf.drawString(
            72, 720 - (index % 45) * 14,
            f"Built reliable Python API service for customers {index}.",
        )
    pdf.save()
    engine, factory = _database()
    try:
        with factory() as db:
            resume = db.get(models.Resume, 10)
            resume.source_document = output.getvalue()
            resume.source_format = "pdf"
            db.add_all([
                models.Opportunity(
                    id="opp_fragments", user_id=1, resume_id=10,
                    title="Platform Engineer", company="Example Co",
                    job_description="Build reliable Python services for global customers.",
                    job_snapshot={},
                ),
                models.EvidenceItem(
                    id="ev_fragments", user_id=1, resume_id=10,
                    category="experience", title="Platform work",
                    evidence_text="Built reliable Python services.",
                    skills=["Python"], metrics={}, approval_state="approved",
                ),
            ])
            db.commit()
            evidence = operations.tailoring_evidence_payload(
                db, 1, 10, complete_context=True
            )
            units = operations.tailoring_source_units(resume, require_complete=True)
            assert len(units) == 101
            needed = tailoring_prompt_bound(
                job_title="Platform Engineer",
                jd_text="Build reliable Python services for global customers.",
                approved_evidence=evidence,
                source_units=units,
                complete_context=True,
            )
            route = SimpleNamespace(
                supported_source_formats=("pdf",),
                model_quotes=(SimpleNamespace(max_input_tokens=needed),),
            )
            monkeypatch.setattr(runtime, "current_runtime", lambda user_id:
                                SimpleNamespace(resolve_route=lambda *args, **kwargs: route))
            request = schemas.AnalysisRunCreate(
                operation="resume_tailor", opportunity_id="opp_fragments", input={}
            )
            analysis.prepare_quote_context(db, user_id=1, payload=request)
            route.model_quotes = (SimpleNamespace(max_input_tokens=needed - 1),)
            with pytest.raises(finance.FundingUnavailable, match="complete prompt"):
                analysis.prepare_quote_context(db, user_id=1, payload=request)
            assert db.query(CreditReservation).count() == 0
            assert db.query(models.AnalysisRun).count() == 0
    finally:
        engine.dispose()


def _paid_tailoring_run(*, paid_context: bool = True):
    engine, factory = _database()
    with factory() as db:
        db.add_all([
            models.Opportunity(
                id="opp_race", user_id=1, resume_id=10,
                title="Platform Engineer", company="Example Co",
                job_description="Build reliable Python services for global customers.",
                job_snapshot={},
            ),
            models.EvidenceItem(
                id="ev_race", user_id=1, resume_id=10,
                category="experience", title="Platform work",
                evidence_text="Developed reliable Python services for customers.",
                skills=["Python"], metrics={}, approval_state="approved",
            ),
        ])
        db.commit()
        normalized = {"resume_id": 10}
        dependencies = analysis_service._dependency_snapshot(
            db, 1, "resume_tailor", "opp_race", normalized,
            paid_context=paid_context,
        )
        if not paid_context:
            assert dependencies["generation"]["prompt"] == "resume-source-v6"
            assert "context_selection" not in dependencies["generation"]
        fingerprint = payload_fingerprint({
            "operation": "resume_tailor", "opportunity_id": "opp_race",
            "input": normalized,
            "dependencies": dependencies,
        })
        db.add(models.AnalysisRun(
            id="run_race", user_id=1, opportunity_id="opp_race",
            operation="resume_tailor", status="running" if paid_context else "succeeded",
            idempotency_key="race-test-001", input_fingerprint=fingerprint,
            input_payload=normalized,
            model_cost_quote={"billing_currency": "credits"} if paid_context else None,
        ))
        db.commit()
    return engine, factory


def test_legacy_v6_saved_result_does_not_short_circuit_v7_quote():
    engine, factory = _paid_tailoring_run(paid_context=False)
    try:
        with factory() as db:
            request = schemas.AnalysisRunCreate(
                operation="resume_tailor", opportunity_id="opp_race", input={}
            )
            web_request = Request({"type": "http", "method": "POST", "path": "/v1/credits/analysis-reuse"})
            owner = db.get(models.User, 1)
            current = analysis_reuse(
                request, web_request, Response(), db, owner,
                include_legacy=False,
            )
            assert current["reused_run"] is None
            saved = analysis_reuse(
                request, web_request, Response(), db, owner,
                include_legacy=True,
            )
            assert saved["reused_run"]["id"] == "run_race"
    finally:
        engine.dispose()


def test_paid_tailoring_refuses_changed_job_before_model(monkeypatch):
    engine, factory = _paid_tailoring_run()
    try:
        with factory() as db:
            db.get(models.Opportunity, "opp_race").job_description += " Changed after quote."
            db.commit()
        monkeypatch.setattr(
            llm_client, "tailor_resume_from_evidence_full",
            lambda **kwargs: pytest.fail("Model called on changed input"),
        )
        with factory() as db:
            run = db.get(models.AnalysisRun, "run_race")
            with pytest.raises(analysis_service.TailoringInputsChanged):
                operations.execute_resume_tailor(
                    db, user_id=1, payload=run.input_payload, run=run,
                )
            assert db.query(models.ResumeVersion).count() == 0
    finally:
        engine.dispose()


def test_paid_tailoring_refuses_job_mutation_during_generation(monkeypatch):
    from backend.app.services import docx_artifact

    engine, factory = _paid_tailoring_run()
    try:
        def synthetic_generation(**kwargs):
            with factory() as other:
                other.get(models.Opportunity, "opp_race").job_description += " Changed during generation."
                other.commit()
            unit = kwargs["source_units"][0]
            return {"source_edits": [{
                "unit_id": unit["unit_id"], "original_text": unit["text"],
                "replacement_text": "Built reliable Python services.",
                "evidence_ids": ["ev_race"], "reason": "Clearer action verb",
            }]}

        monkeypatch.setattr(llm_client, "tailor_resume_from_evidence_full", synthetic_generation)
        monkeypatch.setattr(docx_artifact, "prepare_artifact", lambda *args: {"sealed": True})
        monkeypatch.setattr(
            operations, "validate_evidence_output",
            lambda *args: SimpleNamespace(passed=True, errors=[]),
        )
        monkeypatch.setattr(operations, "_record_model_call", lambda *args, **kwargs: None)
        with factory() as db:
            run = db.get(models.AnalysisRun, "run_race")
            with pytest.raises(analysis_service.TailoringInputsChanged):
                operations.execute_resume_tailor(
                    db, user_id=1, payload=run.input_payload, run=run,
                )
            assert db.query(models.ResumeVersion).count() == 0
    finally:
        engine.dispose()
