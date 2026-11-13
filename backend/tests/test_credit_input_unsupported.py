"""Owned quote refusals distinguish supported inputs from service funding gates."""
import pytest
from backend.app.domains.credits import finance, service
from backend.app.domains.credits.models import (
    CreditExecutionHold,
    CreditReservation,
    CreditServiceQuote,
)
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.models import (
    AnalysisRun,
    EvidenceItem,
    ModelCallEvent,
    ModelCostLiability,
    Opportunity,
)
from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
from backend.tests.test_unified_analysis import request
from backend.tests.test_unified_credit_http import api, factory, setup
from backend.tests.test_unified_execution import route
from sqlalchemy import event

assert api and factory and setup


def configure(api, monkeypatch, selected):
    config = api[2].model_copy(update={"routes": (selected,)})
    reviewed_runtime(monkeypatch, config)
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    return config


def post_quote(api, payload=None, *, synchronous=False):
    client, factory, _ = api
    statements = []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement.split(None, 1)[0].upper())

    with factory() as db:
        engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", observe)
    try:
        response = client.post("/api/v1/credits/" + ("synchronous-service-quotes" if synchronous else "service-quotes"),
            json=(payload or request()).model_dump(), headers={"Idempotency-Key": "owned-unsupported-quote"})
    finally:
        event.remove(engine, "before_cursor_execute", observe)
    assert not {"INSERT", "UPDATE", "DELETE", "CREATE", "ALTER", "DROP"}.intersection(statements)
    with factory() as db:
        assert all(db.query(model).count() == 0 for model in (
            CreditServiceQuote, CreditReservation, CreditExecutionHold, AnalysisRun,
            DispatchOutbox, ModelCallEvent, ModelCostLiability,
        ))
        assert service.wallet(db, user_id=1).available_credits == 40
    return response


def unsupported(response):
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert set(detail) == {"code", "message", "run_created"}
    assert detail["code"] == "credit_service_input_unsupported" and detail["run_created"] is False
    assert isinstance(detail["message"], str) and detail["message"]
    return detail["message"]


def test_actual_http_owned_source_over_qualified_size_is_unsupported_without_writes(api, monkeypatch):
    selected = api[2].routes[0]
    configure(api, monkeypatch, selected.model_copy(update={
        "supported_limits": dict(selected.supported_limits, source_bytes=1),
    }))
    unsupported(post_quote(api))


@pytest.mark.parametrize("name", ["context_bytes", "evidence_items"])
def test_actual_http_other_measured_overflow_is_unsupported(api, monkeypatch, name):
    selected = api[2].routes[0]
    configure(api, monkeypatch, selected.model_copy(update={
        "supported_limits": dict(selected.supported_limits, **{name: 0}),
    }))
    unsupported(post_quote(api))


def tailoring(api, monkeypatch, *, formats=("docx",), tokens=32000, operation="resume_tailor"):
    selected = route(attempts=1).model_copy(update={
        "operation": operation, "credits": 5 if operation == "interview_questions" else 8,
        "supported_source_formats": formats,
        "supported_limits": {"context_bytes": 100_000, "source_bytes": 100_000, "evidence_items": 100},
        "model_quotes": tuple(model.model_copy(update={"max_input_tokens": tokens})
            for model in route(attempts=1).model_quotes),
    })
    from backend.app.services.delivery_contracts import current_output_contract

    selected = selected.model_copy(update={"output_contract_sha256": current_output_contract(operation)})
    configure(api, monkeypatch, selected)
    with api[1]() as db:
        db.add(Opportunity(id="supported-role", user_id=1, resume_id=1, title="Backend Engineer",
            job_description="Build reliable Python backend APIs and services.", job_snapshot={}))
        db.commit()
    from backend.app.domains.analysis.schemas import AnalysisRunCreate

    return AnalysisRunCreate(operation=operation, opportunity_id="supported-role",
        input={"mode": "enhanced"} if operation == "interview_questions" else {},
        idempotency_key="owned-unsupported-quote")


def test_actual_http_unsupported_source_format_refuses_before_preparation(api, monkeypatch):
    from backend.app.domains.analysis import operations

    payload = tailoring(api, monkeypatch, formats=("pdf",))
    monkeypatch.setattr(operations, "tailoring_source_units",
        lambda *_a, **_k: pytest.fail("Unsupported format cannot start source preparation"))
    assert "format" in unsupported(post_quote(api, payload))


@pytest.mark.parametrize("operation", ["resume_tailor", "interview_questions"])
def test_actual_http_complete_analysis_prompt_over_envelope_is_unsupported(api, monkeypatch, operation):
    payload = tailoring(api, monkeypatch, operation=operation, tokens=100)
    assert "context" in unsupported(post_quote(api, payload))


def test_actual_http_complete_matching_context_over_envelope_is_unsupported(api):
    payload = request()
    payload.input["job_description"] += "x" * 20000
    assert "context" in unsupported(post_quote(api, payload))


def test_actual_http_absent_approved_fact_is_explicit_user_refusal(api):
    with api[1]() as db:
        db.query(EvidenceItem).one().approval_state = "rejected"
        db.commit()
    assert "Approve" in unsupported(post_quote(api))


@pytest.mark.parametrize("service_outage", [False, True])
def test_actual_http_source_refusal_is_fixed_safe_but_processing_outage_stays503(
        api, monkeypatch, service_outage):
    from backend.app.domains.analysis import operations
    from backend.app.services.resume_layout import ResumeLayoutError, ResumeProcessingUnavailable

    payload = tailoring(api, monkeypatch)

    def refuse(*_args, **_kwargs):
        error = ResumeProcessingUnavailable if service_outage else ResumeLayoutError
        raise error("PRIVATE_SOURCE_TEXT /Users/private/source.tex provider-request-123")

    monkeypatch.setattr(operations, "tailoring_source_units", refuse)
    response = post_quote(api, payload)
    if service_outage:
        assert response.status_code == 503
        assert response.json() == {"detail": "This service is not ready for a new paid request."}
    else:
        assert "layout" in unsupported(response)
    assert all(value not in response.text for value in ("PRIVATE_SOURCE_TEXT", "/Users/", "provider-request-123"))


def test_actual_http_invalid_route_arity_is_service_pause_not_unsupported(api, monkeypatch):
    from backend.app.domains.analysis import operations

    selected = api[2].routes[0]
    configure(api, monkeypatch, selected.model_copy(update={"model_quotes": selected.model_quotes * 2}))
    monkeypatch.setattr(operations, "complete_matching_context",
        lambda *_a, **_k: pytest.fail("Malformed route cannot inspect complete matching context"))
    assert post_quote(api).status_code == 503


def test_actual_http_missing_measurement_bound_is_service_pause(api, monkeypatch):
    selected = api[2].routes[0]
    configure(api, monkeypatch, selected.model_copy(update={
        "supported_limits": {key: value for key, value in selected.supported_limits.items() if key != "source_bytes"},
    }))
    assert post_quote(api).status_code == 503


def test_actual_http_expired_qualification_remains503_even_with_oversized_source(api, monkeypatch):
    from datetime import UTC, datetime, timedelta

    selected = api[2].routes[0]
    configure(api, monkeypatch, selected.model_copy(update={
        "qualified_from": datetime.now(UTC) - timedelta(days=2),
        "qualified_until": datetime.now(UTC) - timedelta(days=1),
        "supported_limits": dict(selected.supported_limits, source_bytes=1),
    }))
    assert post_quote(api).status_code == 503


def test_actual_http_disabled_runtime_refuses_before_source_inspection(api, monkeypatch):
    from backend.app.domains.credits import analysis

    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setattr(analysis, "prepare_quote_context",
        lambda *_a, **_k: pytest.fail("Disabled runtime must not inspect source inputs"))
    assert post_quote(api).status_code == 503


@pytest.mark.parametrize("value", [True, -1, 1.5, None])
def test_invalid_server_measurement_is_not_a_user_input_error(api, value):
    with pytest.raises(finance.FundingUnavailable) as caught:
        api[2].resolve_route("job_match", {"mode": "enhanced"}, observed_limits={"source_bytes": value})
    assert not isinstance(caught.value, finance.CreditServiceInputUnsupported)


def test_actual_http_cross_owner_input_still_returns404_without_writes(api):
    payload = request()
    payload.input["resume_id"] = 999999
    assert post_quote(api, payload).status_code == 404


@pytest.mark.parametrize("synchronous", [False, True])
def test_actual_http_product_pause_precedes_unsupported_advice_for_both_quote_families(
        api, monkeypatch, synchronous):
    if synchronous:
        from backend.app.domains.credits.synchronous import SynchronousServiceRequest
        from backend.app.services.delivery_contracts import current_output_contract

        selected = api[2].routes[0].model_copy(update={
            "operation": "resume_enrichment", "credits": 2,
            "output_contract_sha256": current_output_contract("resume_enrichment"),
            "supported_limits": {"context_bytes": 1, "evidence_items": 100},
        })
        payload = SynchronousServiceRequest(operation="resume_enrichment", input={"resume_id": 1})
    else:
        selected = api[2].routes[0].model_copy(update={
            "supported_limits": dict(api[2].routes[0].supported_limits, source_bytes=1),
        })
        payload = request()
    configure(api, monkeypatch, selected)
    unsupported(post_quote(api, payload, synchronous=synchronous))
    monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "false")
    paused = post_quote(api, payload, synchronous=synchronous)
    assert paused.status_code == 503, paused.text
    assert paused.json() == {"detail": "This service is not ready for a new paid request."}
