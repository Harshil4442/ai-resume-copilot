"""Tailoring accepts displayed product units before any new paid admission."""
from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.app.billing import catalog
from backend.app.domains.analysis import schemas
from backend.app.domains.analysis import service as analysis
from backend.app.domains.analysis.models import AnalysisRequestKey
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.usage.service import commit_run_usage
from backend.app.models import (
    AnalysisRun,
    EvidenceItem,
    ModelCostLiability,
    Opportunity,
    UsageEvent,
    User,
)
from backend.app.services import generation_gate
from backend.tests.test_analysis_runs import SOURCE_EXPERIENCE, _database, _payload
from fastapi import HTTPException
from pydantic import ValidationError


@pytest.fixture
def tailoring():
    engine, factory = _database()
    with factory() as db:
        db.add_all([
            Opportunity(id="quote_opp", user_id=1, resume_id=10,
                title="Platform Engineer", company="Example Co",
                job_description="Build reliable Python services for global customers.", job_snapshot={}),
            EvidenceItem(id="quote_evidence", user_id=1, resume_id=10,
                category="experience", title="Platform work", evidence_text=SOURCE_EXPERIENCE,
                skills=["python"], metrics={}, approval_state="approved"),
        ])
        db.commit()
    try:
        yield factory
    finally:
        engine.dispose()


def accepted_quote():
    return {"pricing_version": catalog.ANALYSIS_PRICING_VERSION,
            "units": catalog.ANALYSIS_OPERATION_UNITS["resume_tailor"]}


def request(quote, *, data=None):
    return schemas.AnalysisRunCreate(operation="resume_tailor", opportunity_id="quote_opp",
        input=data or {}, accepted_quote=quote)


def test_public_catalog_and_admission_share_canonical_units():
    public = catalog.public_catalog(checkout_enabled=False)
    assert public["analysis_pricing_version"] == catalog.ANALYSIS_PRICING_VERSION
    assert public["analysis_operation_units"] == analysis.OPERATION_UNITS
    assert analysis.OPERATION_UNITS is catalog.ANALYSIS_OPERATION_UNITS
    public["analysis_operation_units"]["resume_tailor"] = 99
    assert analysis.OPERATION_UNITS["resume_tailor"] == 2


@pytest.mark.parametrize("quote", [None,
    {"pricing_version": "stale-analysis-price", "units": 2},
    {"pricing_version": catalog.ANALYSIS_PRICING_VERSION, "units": 1},
    {"pricing_version": catalog.ANALYSIS_PRICING_VERSION, "units": 0},
])
def test_new_tailoring_requires_exact_quote_before_generation_or_durable_work(tailoring, monkeypatch, quote):
    def unexpected(*_args, **_kwargs):
        pytest.fail("A refused product quote cannot reach generation admission")
    monkeypatch.setattr(generation_gate, "check_generation_admission", unexpected)
    with tailoring() as db, pytest.raises(HTTPException) as error:
        analysis.create_run(db, user_id=1, payload=request(quote),
                            header_idempotency_key="quote-refused-request")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "analysis_quote_requires_review"
    with tailoring() as db:
        assert db.get(User, 1).ai_credits == 50
        for model in (AnalysisRun, AnalysisRequestKey, UsageEvent, DispatchOutbox, ModelCostLiability):
            assert db.query(model).count() == 0


@pytest.mark.parametrize("quote", [
    {"pricing_version": "v1", "units": True},
    {"pricing_version": "v1", "units": "2"},
    {"pricing_version": "v1", "units": 2.0},
    {"pricing_version": "v1", "units": -1},
    {"pricing_version": "v1", "units": 2, "free": True},
    {"pricing_version": 123, "units": 2},
    {"pricing_version": "v1\nforged", "units": 2},
])
def test_quote_request_rejects_coercion_and_extra_fields(quote):
    with pytest.raises(ValidationError):
        request(quote)


def test_client_cannot_supply_server_accepted_metadata(tailoring):
    with tailoring() as db, pytest.raises(HTTPException) as error:
        analysis.create_run(db, user_id=1, payload=request(accepted_quote(),
            data={"_accepted_analysis_quote": accepted_quote()}),
            header_idempotency_key="quote-spoofed-private")
    assert error.value.status_code == 422
    with tailoring() as db:
        assert db.query(AnalysisRun).count() == db.query(UsageEvent).count() == 0


def test_accepted_quote_reserves_once_and_survives_price_change_retry_and_commit(tailoring, monkeypatch):
    original = accepted_quote()
    with tailoring() as db:
        first, created = analysis.create_run(db, user_id=1, payload=request(original),
                                             header_idempotency_key="quote-original-request")
        assert created and first.estimated_units == 2
        first_id = first.id
        assert first.input_payload["_accepted_analysis_quote"] == original
        reserved_reason = db.query(UsageEvent).one().reason
        assert original["pricing_version"] in reserved_reason and "2 analysis units" in reserved_reason
        assert len(reserved_reason) <= 240
    monkeypatch.setattr(catalog, "ANALYSIS_PRICING_VERSION", "analysis-price-changed-v2")
    monkeypatch.setitem(catalog.ANALYSIS_OPERATION_UNITS, "resume_tailor", 3)
    def unexpected(*_args, **_kwargs):
        pytest.fail("A matching accepted run must not create more generation work")
    monkeypatch.setattr(generation_gate, "check_generation_admission", unexpected)
    for key, quote in (("quote-original-request", original),
                       ("quote-original-request", None),
                       ("quote-equivalent-retry", accepted_quote())):
        with tailoring() as db:
            retried, created = analysis.create_run(db, user_id=1, payload=request(quote),
                                                   header_idempotency_key=key)
            assert not created and retried.id == first_id and retried.estimated_units == 2
            assert retried.input_payload["_accepted_analysis_quote"] == original
            assert db.get(User, 1).ai_credits == 48
            assert db.query(UsageEvent).count() == db.query(DispatchOutbox).count() == 1
    with tailoring() as db:
        run = db.get(AnalysisRun, first_id)
        commit_run_usage(db, run)
        db.commit()
        assert run.committed_units == 2 and db.get(User, 1).ai_credits == 48


def test_new_variant_needs_fresh_quote_and_does_not_reprice_existing_run(tailoring, monkeypatch):
    original = accepted_quote()
    with tailoring() as db:
        first, _ = analysis.create_run(db, user_id=1, payload=request(original),
                                       header_idempotency_key="quote-before-change")
        first_id = first.id
    monkeypatch.setattr(catalog, "ANALYSIS_PRICING_VERSION", "analysis-price-changed-v2")
    monkeypatch.setitem(catalog.ANALYSIS_OPERATION_UNITS, "resume_tailor", 3)
    with tailoring() as db, pytest.raises(HTTPException) as error:
        analysis.create_run(db, user_id=1, payload=request(original, data={"force_new": True}),
                            header_idempotency_key="quote-new-variant-stale")
    assert error.value.status_code == 409
    assert error.value.detail["code"] == "analysis_quote_requires_review"
    with tailoring() as db:
        current, created = analysis.create_run(db, user_id=1,
            payload=request(accepted_quote(), data={"force_new": True}),
            header_idempotency_key="quote-new-variant-reviewed")
        assert created and current.id != first_id and current.estimated_units == 3
        assert db.get(AnalysisRun, first_id).estimated_units == 2
        assert db.get(User, 1).ai_credits == 45
        assert db.query(UsageEvent).count() == 2


def test_financial_quote_history_survives_input_purge_without_retention_extension(tailoring):
    original = accepted_quote()
    with tailoring() as db:
        run, _ = analysis.create_run(db, user_id=1, payload=request(original),
                                     header_idempotency_key="quote-retention-history")
        run_id = run.id
        reason = db.query(UsageEvent).one().reason
        run.input_payload = {}
        run.input_purged_at = datetime.now(UTC)
        db.commit()
    with tailoring() as db:
        run = db.get(AnalysisRun, run_id)
        response = schemas.AnalysisRunResponse.model_validate(run)
        assert "accepted_quote" not in response.model_dump() and response.estimated_units == 2
        assert db.query(UsageEvent).one().reason == reason
        assert original["pricing_version"] in reason and "2 analysis units" in reason


def test_historical_runs_without_quote_stay_readable_and_other_operations_need_no_quote(tailoring):
    with tailoring() as db:
        run, created = analysis.create_run(db, user_id=1, payload=_payload(),
                                            header_idempotency_key="quote-unaffected-match")
        assert created and run.estimated_units == 1
        assert "accepted_quote" not in schemas.AnalysisRunResponse.model_validate(run).model_dump()


def test_historical_client_metadata_cannot_become_a_public_accepted_quote(tailoring):
    with tailoring() as db:
        run, _ = analysis.create_run(db, user_id=1, payload=_payload(),
                                     header_idempotency_key="quote-historical-private-input")
        # Before this reserved key existed, historical inputs were arbitrary
        # dictionaries. Even valid-looking metadata must not become a receipt.
        run.input_payload = {"_accepted_analysis_quote": {
            "pricing_version": catalog.ANALYSIS_PRICING_VERSION,
            "units": run.estimated_units,
        }}
        db.commit()
        response = schemas.AnalysisRunResponse.model_validate(run).model_dump()
        assert "accepted_quote" not in response
