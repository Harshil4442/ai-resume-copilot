from __future__ import annotations

import copy
from datetime import timedelta

import pytest
from backend.app import models as core
from backend.app.domains.common import utcnow
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import (
    admissions,
    config,
    connectors,
    models,
    schemas,
    service,
    tasks,
)
from backend.tests import test_employer_services as original
from fastapi import HTTPException


@pytest.fixture
def context(monkeypatch):
    return original.context.__wrapped__(monkeypatch)


def _queue(client, app):
    return client.post(f"/api/v1/employer-jobs/applications/{app['id']}/execute",
                       json={"package_digest": app["package_digest"]})


def _batch(client, apps, *, maximum=10, key="reviewed-batch-1"):
    return client.post("/api/v1/employer-jobs/application-batches", json={
        "items": [{"application_id": app["id"], "package_digest": app["package_digest"],
                   "allowed_actions": ["submit", "upload"]} for app in apps],
        "max_total_credits": maximum, "idempotency_key": key,
    })


def _batch_action(client, batch, action):
    return client.post(f"/api/v1/employer-jobs/application-batches/{batch['id']}/{action}",
                       json={"package_digest": batch["package_digest"]})


def test_daily_pending_limit_is_atomic_and_queue_replay_does_not_reserve_twice(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    factory, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    assert _queue(client, first).status_code == 200
    assert _queue(client, first).status_code == 200
    denied = _queue(client, second)
    assert denied.status_code == 429 and denied.json()["detail"]["code"] == "candidate_daily_limit"
    with factory() as db:
        assert db.query(models.EmployerAdmission).count() == 1
        assert db.query(models.ServiceCreditReservation).count() == 1
        assert db.query(DispatchOutbox).filter_by(topic="employer.apply").count() == 1
        assert db.get(core.User, 1).job_service_credits == 45
        assert db.get(models.EmployerApplication, second["id"]).status == "approved"


def test_execution_keeps_the_exact_approved_pricing_version_after_release_changes(context, monkeypatch):
    factory, client, _ = context
    import json

    from backend.tests.expense_policy_fixtures import synthetic_expense_policy
    policy = synthetic_expense_policy() | {"version": "synthetic-reviewed-price-v1"}
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy))
    application = original._prepare(context)
    assert application["pricing_snapshot"]["pricing_version"] == "synthetic-reviewed-price-v1"
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(policy | {"version": "synthetic-current-price-v2"}))
    assert _queue(client, application).status_code == 200
    with factory() as db:
        reservation = db.query(models.ServiceCreditReservation).one()
        assert reservation.pricing_version == "synthetic-reviewed-price-v1"
        assert reservation.unit_price == 5 and reservation.reserved_amount == 5


def test_prelaunch_cancel_releases_slot_and_money_for_another_reviewed_job(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    factory, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    assert _queue(client, first).status_code == 200
    assert client.post(f"/api/v1/employer-jobs/applications/{first['id']}/cancel").status_code == 200
    assert _queue(client, second).status_code == 200
    with factory() as db:
        released = db.query(models.EmployerAdmission).filter_by(application_id=first["id"]).one()
        assert released.state == "released" and released.active_key is None
        assert db.query(models.EmployerApplicationAttempt).count() == 0
        assert db.get(core.User, 1).job_service_credits == 45


def test_unknown_holds_admission_across_windows_and_cancellation(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    factory, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    assert _queue(client, first).status_code == 200
    calls = []
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: calls.append(1) or (200, None))
    assert tasks.execute_application(first["id"]) == "unknown"
    with factory() as db:
        row = db.query(models.EmployerAdmission).one()
        row.admitted_at = row.possible_send_at = utcnow() - timedelta(days=400)
        db.commit()
    assert client.post(f"/api/v1/employer-jobs/applications/{first['id']}/cancel").status_code == 200
    assert _queue(client, second).status_code == 429
    assert tasks.execute_application(first["id"]) == "unknown"
    with factory() as db:
        assert db.query(models.EmployerAdmission).one().state == "unknown"
        assert db.get(core.User, 1).job_service_credits == 45
    assert calls == [1]


def test_rejected_http_attempt_refunds_money_but_does_not_reset_safety_budget(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    factory, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    _queue(client, first)
    def reject(*_a, **_k):
        raise connectors.ConnectorError("submission_rejected_422", safe_to_retry=False)
    monkeypatch.setattr(connectors, "submit_greenhouse", reject)
    assert tasks.execute_application(first["id"]) == "failed"
    assert _queue(client, second).status_code == 429
    with factory() as db:
        row = db.query(models.EmployerAdmission).one()
        assert row.state == "consumed" and row.possible_send_at is not None
        assert row.active_key is None and db.get(core.User, 1).job_service_credits == 50


def test_rolling_budget_counts_prior_days_while_daily_budget_resets(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_ROLLING_LIMIT", "1")
    factory, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    _queue(client, first)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: (201, {"application_id": "verified", "completion_verified": True}))
    assert tasks.execute_application(first["id"]) == "confirmed"
    with factory() as db:
        db.query(models.EmployerAdmission).one().possible_send_at = utcnow() - timedelta(days=2)
        db.commit()
    denied = _queue(client, second)
    assert denied.status_code == 429 and denied.json()["detail"]["code"] == "candidate_rolling_limit"


@pytest.mark.parametrize(("name", "code"), [
    ("EMPLOYER_CANDIDATE_PENDING_LIMIT", "candidate_pending_limit"),
    ("EMPLOYER_CANDIDATE_DAILY_CREDIT_LIMIT", "candidate_daily_credit_limit"),
    ("EMPLOYER_CANDIDATE_ROLLING_CREDIT_LIMIT", "candidate_rolling_credit_limit"),
    ("EMPLOYER_PER_EMPLOYER_DAILY_LIMIT", "employer_daily_limit"),
    ("EMPLOYER_PER_EMPLOYER_ROLLING_LIMIT", "employer_rolling_limit"),
])
def test_distinct_candidate_credit_and_employer_limits(context, monkeypatch, name, code):
    monkeypatch.setenv(name, "5" if "CREDIT" in name else "1")
    _, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    assert _queue(client, first).status_code == 200
    denied = _queue(client, second)
    assert denied.status_code == 429 and denied.json()["detail"]["code"] == code


def test_tighter_current_policy_is_checked_again_immediately_before_send(context, monkeypatch):
    factory, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    _queue(client, first)
    _queue(client, second)
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: pytest.fail("over-budget launch"))
    assert tasks.execute_application(first["id"]) == "failed"
    with factory() as db:
        app = db.get(models.EmployerApplication, first["id"])
        assert app.error_code == "candidate_daily_limit"
        assert db.query(models.EmployerAdmission).filter_by(application_id=first["id"]).one().state == "released"
        assert db.query(models.EmployerApplicationAttempt).count() == 0


def test_requisition_aliases_deduplicate_discovery_billing_and_preparation(context):
    factory, client, _ = context
    with factory() as db:
        for row in db.query(models.EmployerPosting):
            row.requisition_id = "public-req-123"
        db.commit()
    result = client.post("/api/v1/employer-jobs/searches", json={"resume_id": 10, "role": "Python", "desired_count": 2, "idempotency_key": "canonical-search-1"}).json()
    assert (result["delivered_count"], result["charged_credits"]) == (1, 1)
    first = original._prepare(context)
    denied = client.post("/api/v1/employer-jobs/applications", json={"posting_id": "job_2", "resume_id": 10, "idempotency_key": "locale-alias-app"})
    assert denied.status_code == 409 and denied.json()["detail"]["code"] == "canonical_opening_conflict"
    assert first["opening_key"] == result["items"][0]["posting"]["opening_key"]
    with factory() as db:
        assert db.query(models.EmployerJobDelivery).count() == 1


def test_verified_employer_group_and_exact_requisition_merge_cross_source_only(context):
    factory, _, _ = context
    with factory() as db:
        source = db.get(models.EmployerSource, "source_test")
        first, second = db.get(models.EmployerPosting, "job_1"), db.get(models.EmployerPosting, "job_2")
        clone = models.EmployerSource(**{c.name: copy.deepcopy(getattr(source, c.name)) for c in source.__table__.columns if c.name != "id"})
        clone.id, clone.platform, clone.board_token = "source_other", "lever", "other-verified"
        first.requisition_id = second.requisition_id = "verified-shared-requisition"
        assert admissions.opening_identity(first, source) != admissions.opening_identity(second, clone)
        source.employer_key = clone.employer_key = "operator-verified-employer-group"
        assert admissions.opening_identity(first, source) == admissions.opening_identity(second, clone)
        second.requisition_id = "different-verified-requisition"
        assert admissions.opening_identity(first, source) != admissions.opening_identity(second, clone)


def test_source_identity_change_stops_launch_and_same_posting_cannot_evade_existing_claim(context, monkeypatch):
    factory, client, _ = context
    app = original._prepare(context)
    _queue(client, app)
    with factory() as db:
        db.get(models.EmployerPosting, "job_1").requisition_id = "changed-after-approval"
        db.commit()
    denied = client.post("/api/v1/employer-jobs/applications", json={"posting_id": "job_1", "resume_id": 10, "idempotency_key": "changed-requisition"})
    assert denied.status_code == 409
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: pytest.fail("stale opening must not send"))
    assert tasks.execute_application(app["id"]) == "failed"


def test_legacy_queue_without_admission_quote_cannot_send(context, monkeypatch):
    factory, client, _ = context
    app = original._prepare(context)
    _queue(client, app)
    with factory() as db:
        db.get(models.EmployerApplication, app["id"]).admission_snapshot = None
        db.commit()
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: pytest.fail("legacy queue needs fresh review"))
    assert tasks.execute_application(app["id"]) == "failed"
    with factory() as db:
        assert db.query(models.EmployerAdmission).one().state == "released"
        assert db.get(core.User, 1).job_service_credits == 50


def test_batch_quote_is_exact_immutable_and_idempotent_then_atomic(context):
    factory, client, _ = context
    apps = [original._prepare(context), original._prepare(context, job="job_2")]
    result = _batch(client, apps)
    assert result.status_code == 201, result.text
    batch = result.json()
    assert _batch(client, apps).json()["id"] == batch["id"]
    assert batch["quoted_credits"] == 10 and len(batch["items"]) == 2
    assert _batch_action(client, batch, "execute").status_code == 409
    assert _batch_action(client, batch, "approve").status_code == 200
    assert _batch_action(client, batch, "execute").status_code == 200
    assert _batch_action(client, batch, "execute").status_code == 200
    with factory() as db:
        assert db.query(models.EmployerAdmission).count() == 2
        assert db.query(models.ServiceCreditReservation).count() == 2
        assert db.query(DispatchOutbox).filter_by(topic="employer.apply").count() == 2
        assert db.get(core.User, 1).job_service_credits == 40
        for approval in db.query(models.EmployerApplicationApproval).filter(models.EmployerApplicationApproval.review_snapshot.is_not(None)).all():
            assert "admission_snapshot" in approval.review_snapshot


def test_batch_admission_failure_rolls_back_every_slot_credit_and_outbox(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    factory, client, _ = context
    batch = _batch(client, [original._prepare(context), original._prepare(context, job="job_2")]).json()
    assert _batch_action(client, batch, "approve").status_code == 200
    denied = _batch_action(client, batch, "execute")
    assert denied.status_code == 429, denied.text
    with factory() as db:
        assert db.query(models.EmployerAdmission).count() == 0
        assert db.query(models.ServiceCreditReservation).count() == 0
        assert db.query(DispatchOutbox).filter_by(topic="employer.apply").count() == 0
        assert db.get(core.User, 1).job_service_credits == 50
        assert {r.status for r in db.query(models.EmployerApplication)} == {"approved"}


def test_batch_count_price_budget_and_changed_package_cannot_be_bypassed(context, monkeypatch):
    _, client, _ = context
    apps = [original._prepare(context), original._prepare(context, job="job_2")]
    assert _batch(client, apps, maximum=9).status_code == 422
    monkeypatch.setenv("EMPLOYER_BATCH_LIMIT", "1")
    assert _batch(client, apps).status_code == 422
    monkeypatch.setenv("EMPLOYER_BATCH_LIMIT", "10")
    batch = _batch(client, apps).json()
    edited = client.put(f"/api/v1/employer-jobs/applications/{apps[0]['id']}/package", json={"resume_id": 10, "resume_choice": "original", "answers": original.ANSWERS | {"first_name": "Updated"}, "consents": {}})
    assert edited.status_code == 200
    assert _batch_action(client, batch, "approve").status_code == 409


def test_cancelled_batch_releases_only_unstarted_work_and_cannot_replay(context, monkeypatch):
    factory, client, _ = context
    apps = [original._prepare(context), original._prepare(context, job="job_2")]
    batch = _batch(client, apps).json()
    _batch_action(client, batch, "approve")
    _batch_action(client, batch, "execute")
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: (200, None))
    assert tasks.execute_application(apps[0]["id"]) == "unknown"
    assert _batch_action(client, batch, "cancel").status_code == 200
    assert _batch_action(client, batch, "execute").status_code == 409
    assert tasks.execute_application(apps[1]["id"]) == "cancelled"
    with factory() as db:
        assert db.query(models.EmployerAdmission).filter_by(application_id=apps[0]["id"]).one().state == "unknown"
        assert db.query(models.EmployerAdmission).filter_by(application_id=apps[1]["id"]).one().state == "released"
        assert db.get(core.User, 1).job_service_credits == 45


def test_account_deletion_removes_batch_pii_and_fences_reserved_slots(context):
    factory, client, _ = context
    apps = [original._prepare(context), original._prepare(context, job="job_2")]
    batch = _batch(client, apps).json()
    _batch_action(client, batch, "approve")
    _batch_action(client, batch, "execute")
    with factory() as db:
        service.delete_account_data(db, 1)
        db.commit()
        assert db.query(models.EmployerApplicationBatch).count() == 0
        assert db.query(models.EmployerApplication).count() == 0
        assert all(row.user_id is None and row.application_id is None and row.active_key is None and row.state == "released" for row in db.query(models.EmployerAdmission))
    assert tasks.execute_application(apps[0]["id"]) == "missing"


def test_batch_nested_ownership_is_not_disclosed(context):
    factory, client, _ = context
    batch = _batch(client, [original._prepare(context)]).json()
    with factory() as db:
        from backend.app.domains.employer import batches
        with pytest.raises(HTTPException) as err:
            batches._owned(db, 2, batch["id"])
        assert err.value.status_code == 404


@pytest.mark.parametrize("invalid", ["0", "-1", "not-an-int", "1001"])
def test_invalid_budget_configuration_fails_closed(monkeypatch, invalid):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", invalid)
    with pytest.raises(RuntimeError):
        config.admission_limits()


def test_employer_policy_requires_positive_limits_and_explicit_evidence():
    with pytest.raises(ValueError):
        schemas.EmployerAdmissionPolicy(version="fixture-v1", daily_limit=0, rolling_limit=5,
            rolling_days=30, evidence_url="https://employer.example/policy", evidence_note="Verified employer limits for this fixture.")


def test_known_employer_cap_cannot_be_loosened_by_an_operator_policy(context):
    factory, _, _ = context
    with factory() as db:
        source = db.get(models.EmployerSource, "source_test")
        source.board_token = "figma"
        source.admission_policy = schemas.EmployerAdmissionPolicy(
            version="operator-fixture", daily_limit=20, rolling_limit=100, rolling_days=7,
            evidence_url="https://employer.example/policy", evidence_note="Operator fixture whose limits are intentionally looser.",
        ).model_dump()
        effective = config.employer_limits(source)
        assert (effective["daily_limit"], effective["rolling_limit"], effective["rolling_days"]) == (5, 5, 30)
        assert effective["operator_policy"]["rolling_limit"] == 100
        source.admission_policy = dict(source.admission_policy, rolling_limit=2, rolling_days=60)
        effective = config.employer_limits(source)
        assert (effective["daily_limit"], effective["rolling_limit"], effective["rolling_days"]) == (5, 2, 60)


def test_pending_daily_limit_does_not_promise_midnight_will_release_unknown_work(context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    _, client, _ = context
    first, second = original._prepare(context), original._prepare(context, job="job_2")
    assert _queue(client, first).status_code == 200
    denied = _queue(client, second)
    assert denied.status_code == 429
    assert denied.json()["detail"]["retry_at"] is None
