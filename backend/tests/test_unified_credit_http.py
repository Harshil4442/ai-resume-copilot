"""Owned quote and free reuse HTTP boundaries over real local ledgers."""
import pytest
from backend.app.database import Base, get_db
from backend.app.domains.analysis import service as analysis_service
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.credits import service
from backend.app.domains.credits.models import CreditReservation, CreditServiceQuote
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.models import AnalysisRun, EvidenceItem, Resume, User
from backend.app.routers import credits
from backend.app.routers.v1 import analysis as analysis_router
from backend.app.security import get_current_user
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from limits.storage import MemoryStorage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from test_unified_analysis import request, setup

assert setup


@pytest.fixture
def factory():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, autoflush=False)
    with maker() as db:
        db.add(User(id=1, email="http-credit@example.com", ai_credits=17, job_service_credits=13))
        db.commit()
    yield maker
    engine.dispose()


@pytest.fixture
def api(setup):
    factory, config = setup
    # Every case has a separate synthetic client/ledger. Retain the real limiter
    # while preventing unrelated test cases from sharing one minute's counters.
    assert type(credits.limiter._storage) is MemoryStorage
    credits.limiter._storage.reset()
    app = FastAPI()
    app.include_router(credits.router, prefix="/api")
    app.include_router(analysis_router.router, prefix="/api/v1")

    def database():
        with factory() as db:
            yield db

    def owner(db=Depends(get_db)):
        return db.get(User, 1)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    with TestClient(app) as client:
        yield client, factory, config


def test_quote_uses_owned_inputs_without_charge_or_dispatch(api):
    client, factory, _ = api
    response = client.post("/api/v1/credits/service-quotes", json=request().model_dump())
    assert response.status_code == 200, response.text
    quote = response.json()
    assert quote["operation"] == "enhanced_job_match" and quote["credits"] == 3
    assert "no-store" in response.headers["cache-control"]
    with factory() as db:
        assert db.query(CreditServiceQuote).count() == 1
        assert db.query(CreditReservation).count() == db.query(DispatchOutbox).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40
    changed = request().model_dump()
    changed["input"]["resume_id"] = 22
    with factory() as db:
        db.add(User(id=2, email="other-quote@example.com"))
        db.add(Resume(id=22, user_id=2, raw_text="Other owner's private resume"))
        db.commit()
    assert client.post("/api/v1/credits/service-quotes", json=changed).status_code == 404


def test_reuse_is_free_when_funding_paused_but_input_changes_do_not_reuse(api, monkeypatch):
    client, factory, _ = api
    payload = request().model_dump()
    quote = client.post("/api/v1/credits/service-quotes", json=payload).json()
    with factory() as db:
        run, _ = analysis_service.create_run(db, user_id=1,
            payload=request().model_copy(update={"accepted_service_quote_id": quote["quote_id"]}),
            header_idempotency_key=None)
        identity = run.id
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    reused = client.post("/api/v1/credits/analysis-reuse", json=payload)
    assert reused.status_code == 200, reused.text
    assert reused.json()["reused_run"]["id"] == identity
    assert reused.json()["reused_run"]["billing_currency"] == "credits"
    with factory() as db:
        assert db.query(CreditReservation).count() == 1
        db.get(Resume, 1).raw_text = "Materially changed candidate experience"
        db.commit()
    assert client.post("/api/v1/credits/analysis-reuse", json=payload).json() == {"reused_run": None}
    with factory() as db:
        assert db.query(AnalysisRun).count() == 1
        assert db.query(CreditReservation).count() == 1


def _funded_match(api):
    client, factory, _ = api
    quoted = client.post("/api/v1/credits/service-quotes", json=request().model_dump())
    assert quoted.status_code == 200, quoted.text
    with factory() as db:
        run, created = analysis_service.create_run(db, user_id=1,
            payload=request().model_copy(update={"accepted_service_quote_id": quoted.json()["quote_id"]}),
            header_idempotency_key=None)
        assert created
        return run.id


def _historical_match(factory, *, mode="enhanced"):
    """Explicit synthetic old read record, never a provider/quality receipt."""
    payload = request().model_copy(update={"input": request().input | {"mode": mode}})
    normalized = dict(payload.input)
    with factory() as db:
        analysis_service.validate_analysis_input(db, user_id=1, operation=payload.operation,
            opportunity_id=payload.opportunity_id, payload=normalized)
        dependencies = analysis_service._dependency_snapshot(db, 1, payload.operation,
            payload.opportunity_id, normalized, paid_context=False)
        fingerprint = payload_fingerprint({"operation": payload.operation,
            "opportunity_id": payload.opportunity_id, "input": normalized, "dependencies": dependencies})
        run = AnalysisRun(id=f"run_synthetic_historical_{mode}", user_id=1, operation="job_match",
            status="succeeded", idempotency_key=f"synthetic-historical-{mode}",
            input_fingerprint=fingerprint, input_payload=normalized,
            prompt_version="match-mega-v2" if mode == "enhanced" else None,
            estimated_units=1 if mode == "enhanced" else 0,
            committed_units=1 if mode == "enhanced" else 0, usage_state="committed")
        db.add(run)
        db.commit()
        return payload.model_dump(), run.id


def test_old_mega_match_is_not_current_reuse_but_remains_an_explicit_owned_legacy_read(api, monkeypatch):
    client, factory, _ = api
    payload, identity = _historical_match(factory)
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    for suffix in ("", "?include_legacy=true"):
        reused = client.post("/api/v1/credits/analysis-reuse" + suffix, json=payload)
        assert reused.status_code == 200 and reused.json() == {"reused_run": None}
    stored = client.get(f"/api/v1/analysis-runs/{identity}")
    assert stored.status_code == 200, stored.text
    assert stored.json()["id"] == identity
    assert stored.json()["prompt_version"] == "match-mega-v2"
    assert stored.json()["billing_currency"] == "legacy_ai_units"
    with factory() as db:
        assert db.query(CreditReservation).count() == db.query(CreditServiceQuote).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40
        db.get(AnalysisRun, identity).user_id = 2
        db.commit()
    assert client.get(f"/api/v1/analysis-runs/{identity}").status_code == 404


@pytest.mark.parametrize("include_legacy", [False, True])
def test_basic_matching_keeps_existing_non_paid_reuse_context(api, include_legacy):
    client, factory, _ = api
    payload, identity = _historical_match(factory, mode="basic")
    reused = client.post(f"/api/v1/credits/analysis-reuse?include_legacy={str(include_legacy).lower()}", json=payload)
    assert reused.status_code == 200, reused.text
    assert reused.json()["reused_run"]["id"] == identity
    assert reused.json()["reused_run"]["billing_currency"] == "none"


@pytest.mark.parametrize("change", ["text", "approval", "source_ref", "corrected_at"])
def test_funded_complete_matching_reuse_tracks_current_approved_facts(api, monkeypatch, change):
    client, factory, _ = api
    _funded_match(api)
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    with factory() as db:
        item = db.get(EvidenceItem, "matching-proof")
        if change == "text":
            item.evidence_text = "Corrected candidate fact: developed Python APIs."
        elif change == "approval":
            item.approval_state = "rejected"
        elif change == "source_ref":
            item.source_ref = "synthetic-corrected-source"
        else:
            from datetime import timedelta
            item.updated_at = item.updated_at + timedelta(seconds=1)
        db.commit()
    reused = client.post("/api/v1/credits/analysis-reuse?include_legacy=true", json=request().model_dump())
    assert reused.status_code == 200 and reused.json() == {"reused_run": None}
    with factory() as db:
        assert db.query(AnalysisRun).count() == db.query(CreditReservation).count() == 1
        assert service.wallet(db, user_id=1).available_credits == 37


def test_current_complete_output_contract_drift_cannot_reuse_prior_paid_match(api, monkeypatch):
    from backend.app.services import delivery_contracts

    client, factory, _ = api
    _funded_match(api)
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    original = delivery_contracts.current_output_contract
    def changed(operation):
        return "f" * 64 if operation in {"job_match", "enhanced_job_match"} else original(operation)
    monkeypatch.setattr(delivery_contracts, "current_output_contract", changed)
    reused = client.post("/api/v1/credits/analysis-reuse", json=request().model_dump())
    assert reused.status_code == 200 and reused.json() == {"reused_run": None}
    with factory() as db:
        assert db.query(AnalysisRun).count() == db.query(CreditReservation).count() == 1
        assert service.wallet(db, user_id=1).available_credits == 37


@pytest.mark.parametrize("change", ["failed", "cancelled", "cancel_requested", "input_purged", "result_purged", "foreign_owner"])
def test_current_matching_reuse_preserves_status_privacy_and_owner_filters(api, change):
    client, factory, _ = api
    identity = _funded_match(api)
    with factory() as db:
        # Synthetic retained-row read guard; no settlement/erasure procedure is qualified here.
        run = db.get(AnalysisRun, identity)
        if change in {"failed", "cancelled"}:
            run.status = change
        elif change == "cancel_requested":
            run.cancel_requested = True
        elif change == "input_purged":
            run.input_purged_at = utcnow()
        elif change == "result_purged":
            run.result_purged_at = utcnow()
        else:
            run.user_id = 2
        db.commit()
    reused = client.post("/api/v1/credits/analysis-reuse", json=request().model_dump())
    assert reused.status_code == 200 and reused.json() == {"reused_run": None}


def test_reuse_cannot_include_a_payment_acceptance(api):
    client, _, _ = api
    payload = request().model_dump() | {"accepted_service_quote_id": "quote_unknown"}
    assert client.post("/api/v1/credits/analysis-reuse", json=payload).status_code == 422


def test_synchronous_quote_and_reuse_are_owned_and_never_dispatch(api, monkeypatch):
    from test_unified_synchronous import configure
    client, factory, _ = api
    configure(monkeypatch, "resume_enrichment")
    payload = {"operation": "resume_enrichment", "input": {"resume_id": 1}}
    assert client.post("/api/v1/credits/synchronous-service-quotes", json=payload).status_code == 422
    response = client.post("/api/v1/credits/synchronous-service-quotes", json=payload,
        headers={"Idempotency-Key": "enrichment-quote-review"})
    assert response.status_code == 200, response.text
    assert response.json()["operation"] == "resume_enrichment" and response.json()["credits"] == 2
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    reused = client.post("/api/v1/credits/synchronous-reuse", json=payload)
    assert reused.status_code == 200, reused.text
    assert reused.json() == {"reused_run": None, "result": None}
    with factory() as db:
        assert db.query(CreditReservation).count() == db.query(DispatchOutbox).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40
    other = {"operation": "resume_enrichment", "input": {"resume_id": 912}}
    assert client.post("/api/v1/credits/synchronous-reuse", json=other).status_code == 404


@pytest.fixture
def discovery_api(api, monkeypatch):
    from backend.app.domains.common import payload_fingerprint, utcnow
    from backend.app.domains.employer import models
    from test_unified_discovery import configure

    client, factory, _ = api
    configure(monkeypatch)
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    with factory() as db:
        db.add(User(id=2, email="other-discovery@example.invalid"))
        db.add(Resume(id=2, user_id=2, skills=["Python"]))
        db.get(Resume, 1).skills = ["Python", "PostgreSQL"]
        db.add(models.EmployerSource(id="http-source", employer="Synthetic Employer", platform="greenhouse",
            board_token="synthetic", enabled=True, region="global",
            careers_url="https://employer.example/careers", verification_url="https://employer.example/careers",
            verification_note="Synthetic reviewed-origin fixture", status="healthy"))
        db.commit()
        db.add(models.EmployerPosting(id="http-job", source_id="http-source", external_id="1",
            requisition_id="http-req", title="Python Engineer", employer="Synthetic Employer",
            location="Bengaluru", description="Python PostgreSQL Engineer",
            canonical_url="https://job-boards.greenhouse.io/synthetic/jobs/1",
            apply_url="https://job-boards.greenhouse.io/synthetic/jobs/1",
            content_sha256=payload_fingerprint("http-job"), last_checked_at=utcnow()))
        db.commit()
    return client, factory


def test_discovery_http_quote_accept_replay_cancel_and_owner_boundary(discovery_api, monkeypatch):
    client, factory = discovery_api
    payload = {"resume_id": 1, "role": "Python Engineer", "desired_count": 5}
    headers = {"Idempotency-Key": "http-discovery-quote"}
    quoted = client.post("/api/v1/credits/discovery-quotes", json=payload, headers=headers)
    assert quoted.status_code == 200, quoted.text
    assert quoted.json()["credits"] == 1 and quoted.json()["operation"] == "job_discovery_batch"
    assert "no-store" in quoted.headers["cache-control"]
    with factory() as db:
        assert db.query(CreditReservation).count() == db.query(DispatchOutbox).count() == 0
    accepted = payload | {"accepted_service_quote_id": quoted.json()["quote_id"], "idempotency_key": "http-discovery-batch"}
    created = client.post("/api/v1/credits/discovery-batches", json=accepted)
    assert created.status_code == 200, created.text
    assert created.json()["reserved_credits"] == 1 and created.json()["items"] == []
    search_id = created.json()["id"]
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    assert client.post("/api/v1/credits/discovery-batches", json=accepted).json() == created.json()
    assert client.get(f"/api/v1/credits/discovery-batches/{search_id}").json() == created.json()
    cancelled = client.post(f"/api/v1/credits/discovery-batches/{search_id}/cancel")
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["refunded_credits"] == 1 and cancelled.json()["charged_credits"] == 0
    assert client.post(f"/api/v1/credits/discovery-batches/{search_id}/cancel").json() == cancelled.json()
    with factory() as db:
        assert db.query(CreditReservation).count() == 1
        assert service.wallet(db, user_id=1).available_credits == 40
    def other_owner(db=Depends(get_db)):
        return db.get(User, 2)
    client.app.dependency_overrides[get_current_user] = other_owner
    assert client.get(f"/api/v1/credits/discovery-batches/{search_id}").status_code == 404
    assert client.post(f"/api/v1/credits/discovery-batches/{search_id}/cancel").status_code == 404


def test_discovery_export_and_erasure_preserve_accounting_without_owner(discovery_api):
    from datetime import timedelta

    from backend.app.domains.common import utcnow
    from backend.app.domains.credits import discovery, runtime, shadow
    from backend.app.domains.credits.discovery_models import (
        CreditDiscoveryBatch,
        CreditDiscoveryDelivery,
    )
    from backend.app.domains.credits.models import CreditExecutionHold
    from backend.app.domains.credits.privacy import erase_owner_credit_links, export_account_data
    from backend.app.domains.employer.service import delete_account_data

    client, factory = discovery_api
    payload = {"resume_id": 1, "role": "Python Engineer", "desired_count": 5}
    quote = client.post("/api/v1/credits/discovery-quotes", json=payload,
        headers={"Idempotency-Key": "export-discovery-quote"}).json()
    accepted = client.post("/api/v1/credits/discovery-batches", json=payload | {
        "accepted_service_quote_id": quote["quote_id"], "idempotency_key": "export-discovery-batch"}).json()
    with factory() as db:
        result = discovery.execute(db, user_id=1, search_id=accepted["id"])
        assert result.status == "completed" and result.delivered_count == 1
        exported = export_account_data(db, 1)
        assert exported["discovery_batches"][0]["search_id"] == result.id
        assert exported["discovery_deliveries"][0]["posting_id"] == "http-job"
        assert "identity_proof" not in str(exported)
        now = utcnow()
        report = shadow.report(db, config=runtime.current_runtime(1), start=now-timedelta(hours=1), end=now, now=now)
        assert report["unified_discovery_batches"] == [{"status": "completed", "batches": 1,
            "delivered_jobs": 1, "recorded_charged_credits": 1, "recorded_refunded_credits": 0}]
        assert report["release_qualified"] is False and "http-job" not in str(report)
        erase_owner_credit_links(db, user_id=1)
        delete_account_data(db, 1)
        db.commit()
        assert db.query(CreditDiscoveryBatch).count() == db.query(CreditDiscoveryDelivery).count() == 0
        hold = db.query(CreditExecutionHold).one()
        assert hold.user_id is None and hold.delivery_state == "delivered"
        assert hold.native_settled_minor is None and hold.reserved_minor > 0
        assert db.query(CreditReservation).one().user_id is None
