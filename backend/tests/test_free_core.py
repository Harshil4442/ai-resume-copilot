"""Bounded free processing uses real admission/ledgers, synthetic expense authority."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.domains.analysis import schemas, tasks
from backend.app.domains.analysis import service as analysis
from backend.app.domains.credits import admission as money
from backend.app.domains.credits.finance import FundingUnavailable
from backend.app.domains.free_core import policy, service
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.models import AnalysisRun, EvidenceItem, ModelCallEvent, UsageEvent, User
from backend.app.services import llm_client
from backend.tests.test_ai_efficiency import _database
from fastapi import HTTPException


def synthetic_free_policy(now=None, **changes):
    stamp = now or datetime.now(UTC)
    operations = {name: {"route_id": f"free.{name}.v1", "reserved_minor": 20,
        "max_input_bytes": 8 * 1024 * 1024, "max_source_records": 1000, "max_attempts": 3, "per_account_30_days": limit}
        for name, limit in (("resume_parse", 1), ("basic_match", 10), ("curated_interview", 5), ("skill_roi", 5))}
    return {
        "version": "synthetic-free-v1", "provenance": "synthetic_test", "funding_period_id": "synthetic-funded-period",
        "evidence_sha256": "f" * 64, "valid_from": (stamp - timedelta(hours=1)).isoformat(),
        "expires_at": (stamp + timedelta(days=1)).isoformat(),
        "shared_period_funded_minor": 10_000, "free_allocation_minor": 10_000,
        "global_30_day_operations": 1000,
        "financial_policy": {"version": "synthetic-finance-v1", "provenance": "synthetic_test", "evidence_sha256": "e" * 64,
            "valid_from": (stamp - timedelta(hours=1)).isoformat(), "expires_at": (stamp + timedelta(days=1)).isoformat()},
        "execution_budgets": {"version": "synthetic-budget-v1", "evidence_sha256": "a" * 64,
            "valid_from": (stamp - timedelta(hours=1)).isoformat(), "expires_at": (stamp + timedelta(days=1)).isoformat(),
            "daily_global_minor": 100_000, "monthly_global_minor": 100_000,
            "monthly_account_minor": 50_000, "monthly_incident_minor": 50_000,
            "monthly_route_minor": {**{item["route_id"]: 50_000 for item in operations.values()}, "paid.synthetic.v1": 50_000},
            "legacy_search_cost_minor": 1, "legacy_application_cost_minor": 1,
            "legacy_preparation_minor": 1, "legacy_provider_attempt_overhead_minor": 1},
        "operations": operations,
    } | changes


@pytest.fixture
def funded(monkeypatch):
    values = synthetic_free_policy()
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(values))
    return values


@pytest.fixture
def database(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    try:
        yield factory
    finally:
        engine.dispose()


def create(factory, *, key="free-basic-key", operation="job_match", mode=None, force=False):
    payload = {"resume_id": 1, "job_description": "Build Python and Docker services for our customers."} if operation == "job_match" else {}
    if mode:
        payload["mode"] = mode
    if force:
        payload["force_new"] = True
    with factory() as db:
        run, created = analysis.create_run(db, user_id=1, payload=schemas.AnalysisRunCreate(
            operation=operation, opportunity_id="opp_test", input=payload), header_idempotency_key=key)
        return run.id, created


def reserve(db, *, key="request-1", owner=1, operation="basic_match", now=None):
    return service.admit(db, user_id=owner, operation=operation, source_type="synthetic",
        source_id=key, key=key, fingerprint="a" * 64, input_bytes=100, attempts=3, now=now)


@pytest.mark.parametrize("operation", ["job_match", "interview_questions", "skill_roi"])
def test_deterministic_routes_are_zero_credit_and_funded_without_ai_policy(database, funded, monkeypatch, operation):
    monkeypatch.delenv("LLM_MODEL_COST_POLICY_JSON", raising=False)
    monkeypatch.setattr(llm_client, "_chat", lambda _: pytest.fail("Deterministic route cannot generate"))
    run_id, created = create(database, operation=operation)
    assert created and tasks.process_analysis_run(run_id) == "succeeded"
    with database() as db:
        run = db.get(AnalysisRun, run_id)
        assert run.estimated_units == run.committed_units == run.generation_attempt_count == 0
        assert run.usage_state == "waived" and db.get(User, 1).ai_credits == 100
        assert db.query(ModelCallEvent).count() == 0
        assert db.query(UsageEvent).one().event_type == "waive"
        expense = db.query(FreeCoreReservation).one()
        assert expense.reserved_minor == 20 and expense.state == "reserved" and expense.settled_minor is None


def test_rollout_disabled_preserves_existing_tariff(database, monkeypatch):
    monkeypatch.delenv("FREE_CORE_ENABLED", raising=False)
    monkeypatch.delenv("FREE_CORE_POLICY_JSON", raising=False)
    run_id, _ = create(database)
    with database() as db:
        assert db.get(AnalysisRun, run_id).estimated_units == 1
        assert db.get(User, 1).ai_credits == 99
        assert db.query(FreeCoreReservation).count() == 0


def test_missing_funding_policy_refuses_new_processing_without_charge(database, funded, monkeypatch):
    monkeypatch.delenv("FREE_CORE_POLICY_JSON", raising=False)
    with pytest.raises(HTTPException) as exc:
        create(database)
    assert exc.value.status_code == 503
    with database() as db:
        assert db.query(AnalysisRun).count() == db.query(FreeCoreReservation).count() == 0
        assert db.get(User, 1).ai_credits == 100


def test_same_input_reuse_remains_available_after_free_allowance_is_paused(database, funded, monkeypatch):
    original, _ = create(database)
    assert tasks.process_analysis_run(original) == "succeeded"
    monkeypatch.delenv("FREE_CORE_POLICY_JSON", raising=False)
    assert create(database, key="free-replay-key") == (original, False)
    with database() as db:
        assert db.query(FreeCoreReservation).count() == 1
        assert db.get(User, 1).ai_credits == 100


def test_ten_comparisons_are_rolling_thirty_days_and_force_new_cannot_bypass(database, funded):
    for index in range(10):
        create(database, key=f"comparison-{index:02d}", force=True)
    with pytest.raises(HTTPException) as exc:
        create(database, key="comparison-eleven", force=True)
    assert exc.value.status_code == 429
    with database() as db:
        assert db.query(FreeCoreReservation).count() == 10
        assert db.get(User, 1).ai_credits == 100


def test_unknown_expense_survives_month_boundary_failure_and_request_deletion(database, funded, monkeypatch):
    stamp = datetime.now(UTC)
    with database() as db:
        row, _ = reserve(db)
        expense = db.get(FreeCoreReservation, row.reservation_id)
        expense.created_at = stamp - timedelta(days=40)
        db.delete(row)
        db.commit()
    settings = synthetic_free_policy(stamp, free_allocation_minor=20)
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(settings))
    with database() as db:
        with pytest.raises(HTTPException) as exc:
            reserve(db, key="new-period-request", now=stamp)
        assert exc.value.status_code == 503
        assert service.expense_usage(db, start=stamp) == 20


def test_current_shared_money_guard_counts_free_holds_once(database, funded):
    with database() as db:
        reserve(db)
        db.commit()
        current = policy.current_policy()
        assert money._hold_usage(db, start=datetime.now(UTC) - timedelta(days=1)) == 20
        tight = current.execution_budgets.model_copy(update={"daily_global_minor": 30})
        with pytest.raises(FundingUnavailable, match="global execution budget"):
            money.assert_headroom(db, user_id=1, route_id="paid.synthetic.v1", required_minor=11,
                policy=current.financial_policy, budgets=tight, now=datetime.now(UTC))


def test_shared_free_processing_does_not_consume_paid_incident_pool(database, funded, monkeypatch):
    funded["execution_budgets"]["monthly_incident_minor"] = 1
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(funded))
    with database() as db:
        reserve(db)
        db.commit()
        assert money._hold_usage(db, start=datetime.now(UTC) - timedelta(days=1), incident=True) == 0


def test_global_operation_limit_applies_across_owners(database, funded, monkeypatch):
    funded["global_30_day_operations"] = 1
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(funded))
    with database() as db:
        db.add(User(id=2, email="other-free@example.com"))
        reserve(db)
        db.commit()
    with database() as db:
        with pytest.raises(HTTPException) as exc:
            reserve(db, key="second-owner", owner=2)
        assert exc.value.status_code == 503


def test_expense_settlement_requires_receipt_and_never_follows_delivery_automatically(database, funded):
    with database() as db:
        row, _ = reserve(db)
        service.complete(db, user_id=1, source_type="synthetic", source_id="request-1", result_id="result-1")
        db.commit()
        expense = db.get(FreeCoreReservation, row.reservation_id)
        assert expense.state == "reserved"
        with pytest.raises(ValueError):
            service.settle_measured_expense(db, reservation_id=expense.id, actual_minor=0, receipt_sha256="")
        service.settle_measured_expense(db, reservation_id=expense.id, actual_minor=8, receipt_sha256="b" * 64)
        db.commit()
        service.settle_measured_expense(db, reservation_id=expense.id, actual_minor=8, receipt_sha256="b" * 64)
        assert service.expense_usage(db, start=datetime.now(UTC) - timedelta(days=1)) == 8
        with pytest.raises(ValueError):
            service.settle_measured_expense(db, reservation_id=expense.id, actual_minor=0, receipt_sha256="c" * 64)


def test_exact_replay_preserves_original_allowance_when_config_changes(database, funded, monkeypatch):
    with database() as db:
        original, _ = reserve(db)
        original_id = original.id
        db.commit()
    monkeypatch.delenv("FREE_CORE_POLICY_JSON", raising=False)
    with database() as db:
        replay, created = reserve(db)
        assert not created and replay.id == original_id
        assert db.query(FreeCoreReservation).count() == 1


def test_measured_overrun_pauses_new_work_under_unchanged_cost_review(database, funded):
    with database() as db:
        row, _ = reserve(db)
        service.settle_measured_expense(db, reservation_id=row.reservation_id, actual_minor=21, receipt_sha256="b" * 64)
        db.commit()
    with database() as db:
        with pytest.raises(HTTPException) as exc:
            reserve(db, key="after-overrun")
        assert exc.value.status_code == 503 and "overrun" in exc.value.detail["message"]


def test_envelope_refusal_never_shortens_input_or_creates_a_hold(database, funded):
    with database() as db:
        with pytest.raises(HTTPException) as exc:
            service.admit(db, user_id=1, operation="basic_match", source_type="synthetic", source_id="oversize",
                key="oversize", fingerprint="a" * 64, input_bytes=9 * 1024 * 1024, attempts=3)
        assert exc.value.status_code == 503 and "No content was shortened" in exc.value.detail["message"]
        assert db.query(FreeCoreReservation).count() == 0


def test_skill_roi_refuses_a_workspace_outside_its_complete_record_envelope(database, funded, monkeypatch):
    funded["operations"]["skill_roi"]["max_source_records"] = 1
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(funded))
    with database() as db:
        db.add(EvidenceItem(id="approved", user_id=1, resume_id=1, category="experience", title="Verified",
                            evidence_text="Built Python services", approval_state="approved"))
        db.commit()
    with pytest.raises(HTTPException) as exc:
        create(database, operation="skill_roi")
    assert "No records were omitted" in exc.value.detail["message"]
    with database() as db:
        assert db.query(FreeCoreReservation).count() == db.query(AnalysisRun).count() == 0


def test_synthetic_expense_policy_cannot_authorize_production_free_processing(funded, monkeypatch):
    funded["provenance"] = "invoice_reconciled"
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(funded))
    monkeypatch.setenv("APP_ENV", "production")
    with pytest.raises(policy.FreeCoreUnavailable):
        policy.current_policy()


def test_free_and_paid_routes_cannot_use_different_global_budget_authorities(funded, monkeypatch):
    from backend.app.domains.credits.runtime import RuntimeConfig
    from backend.tests.test_unified_execution import route

    free = policy.current_policy()
    paid = RuntimeConfig(policy=free.financial_policy, budgets=free.execution_budgets,
        routes=(route(attempts=1).model_copy(update={"id": "paid.synthetic.v1"}),))
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", paid.model_dump_json())
    assert policy.current_policy() == free
    changed = paid.model_copy(update={"budgets": paid.budgets.model_copy(update={"daily_global_minor": 99_999})})
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", changed.model_dump_json())
    with pytest.raises(policy.FreeCoreUnavailable):
        policy.current_policy()


def test_legacy_upload_reserves_before_scan_and_replays_without_a_second_scan(funded, monkeypatch):
    from backend.app.routers import resume as resume_router
    from backend.tests.test_resume_source import _client, _source_bytes

    calls = []
    def inspect(source, *, source_format):
        calls.append(source)
        return "Synthetic approved resume", {"summary": "Approved summary"}, ["Python"], 1.0, {}
    monkeypatch.setattr(resume_router, "inspect_resume_document", inspect)
    engine, factory, client = _client()
    try:
        source = _source_bytes("pdf")
        request = dict(files={"file": ("resume.pdf", source, "application/pdf")}, headers={"Idempotency-Key": "legacy-parse-one"})
        first = client.post("/api/resume/parse", **request)
        assert first.status_code == 200, first.text
        second = client.post("/api/resume/parse", **request)
        assert second.status_code == 200 and second.json()["resume_id"] == first.json()["resume_id"]
        assert len(calls) == 1
        blocked = client.post("/api/resume/parse", files=request["files"], headers={"Idempotency-Key": "another-source-key"})
        assert blocked.status_code == 429 and len(calls) == 1
        with factory() as db:
            assert db.query(FreeCoreReservation).count() == 1
            assert db.query(FreeCoreRequest).one().state == "completed"
    finally:
        engine.dispose()
