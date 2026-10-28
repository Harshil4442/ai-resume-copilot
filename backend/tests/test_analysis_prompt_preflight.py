"""Quote capacity checks use the same full evidence and source-unit prompts as execution."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.domains.analysis import operations, schemas
from backend.app.domains.credits import analysis, finance, runtime
from backend.app.domains.credits.models import CreditReservation
from backend.app.services import llm_client
from backend.app.services.analysis_prompt_preflight import (
    interview_prompt_bound,
    tailoring_prompt_bound,
)
from backend.app.services.interview_contract import InterviewContextUnavailable
from backend.app.services.model_cost_policy import input_token_estimate
from backend.app.services.prompt_privacy import redact_messages
from backend.app.services.resume_layout import ResumeLayoutError
from backend.tests.test_analysis_runs import _database

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
                with pytest.raises(finance.FundingUnavailable, match="complete job"):
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


def test_paid_source_selection_refuses_omitted_whole_units(monkeypatch):
    from backend.app.services import resume_layout

    units = [
        {"unit_id": f"pdf:{index}", "text": "A useful experience passage.",
         "generation_priority": 1}
        for index in range(101)
    ]
    monkeypatch.setattr(resume_layout, "extract_source_units", lambda *args: units)
    resume = SimpleNamespace(source_document=b"synthetic", source_format="pdf")
    assert len(operations.tailoring_source_units(resume)) == 100
    with pytest.raises(ResumeLayoutError, match="complete tailoring prompt"):
        operations.tailoring_source_units(resume, require_complete=True)
