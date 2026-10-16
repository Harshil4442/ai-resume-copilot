"""Controlled in-flight sender/reconciliation races; synthetic employer transport."""
from __future__ import annotations

from datetime import timedelta

import pytest
from backend.app import models as core
from backend.app.domains.common import utcnow
from backend.app.domains.employer import connectors, models, tasks
from backend.tests import test_employer_services as original

context = original.context


def _proof(outcome):
    value = {"outcome": outcome, "proof_kind": "provider_lookup",
             "proof_reference": "Synthetic provider support case receipt-123",
             "reason": "Support independently checked this exact employer application request.",
             "employer_posting_id": "1"}
    if outcome == "confirmed":
        value["provider_receipt"] = {"application_id": "independent-receipt", "completed": True}
    return value


def _queue(context, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "ada@example.com")
    monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "20")
    application = original._prepare(context)
    original._execute(context[1], application)
    return application, f"/api/v1/employer-jobs/applications/{application['id']}/reconcile"


def test_negative_reconciliation_cannot_refund_original_sender_still_in_flight(context, monkeypatch):
    factory, client, _ = context
    application, path = _queue(context, monkeypatch)
    calls = []

    def send(*_args, **_kwargs):
        calls.append(1)
        assert tasks.execute_application(application["id"]) == "unknown"
        response = client.post(path, json=_proof("not_submitted"))
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "application_sender_outcome_unresolved"
        with factory() as db:
            hold = db.query(models.ServiceCreditReservation).one()
            admission = db.query(models.EmployerAdmission).one()
            attempt = db.query(models.EmployerApplicationAttempt).one()
            assert (hold.state, hold.reserved_amount, hold.committed_amount, hold.released_amount) == ("reserved", 20, 0, 0)
            assert admission.state == "unknown" and admission.active_key is not None
            assert attempt.state == "submitting" and attempt.completed_at is None
            assert db.get(core.User, 1).job_service_credits == 30
        # Accepted work retains its original price even if the next quote changes.
        monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "7")
        return 201, {"application_id": "late-original-receipt", "completion_verified": True}

    monkeypatch.setattr(connectors, "submit_greenhouse", send)
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert calls == [1]
    with factory() as db:
        app = db.get(models.EmployerApplication, application["id"])
        hold = db.query(models.ServiceCreditReservation).one()
        assert app.charged_credits == hold.committed_amount == hold.unit_price == 20
        assert hold.released_amount == 0 and hold.state == "settled"
        assert db.get(core.User, 1).job_service_credits == 30
        assert db.query(models.ServiceCreditEvent).filter_by(event_type="commit").count() == 1
        assert db.query(models.ServiceCreditEvent).filter_by(event_type="release").count() == 0
        assert db.query(models.EmployerAdmission).one().active_key is not None


@pytest.mark.parametrize("late", ["rejection", "unknown", "exception", "receipt"])
def test_independent_confirmation_is_not_overwritten_by_late_original_sender(context, monkeypatch, late):
    factory, client, _ = context
    application, path = _queue(context, monkeypatch)
    independently_saved = {}
    calls = []

    def send(*_args, **_kwargs):
        calls.append(1)
        assert tasks.execute_application(application["id"]) == "unknown"
        result = client.post(path, json=_proof("confirmed"))
        assert result.status_code == 200, result.text
        assert result.json()["status"] == "confirmed" and result.json()["charged_credits"] == 20
        independently_saved.update(result.json()["receipt"])
        if late == "rejection":
            raise connectors.ConnectorError("submission_rejected_422", safe_to_retry=False)
        if late == "exception":
            raise RuntimeError("Synthetic interrupted response")
        if late == "receipt":
            return 201, {"application_id": "original-late-receipt", "completion_verified": True}
        return 503, None

    monkeypatch.setattr(connectors, "submit_greenhouse", send)
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert calls == [1]
    with factory() as db:
        app = db.get(models.EmployerApplication, application["id"])
        hold = db.query(models.ServiceCreditReservation).one()
        attempt = db.query(models.EmployerApplicationAttempt).one()
        admission = db.query(models.EmployerAdmission).one()
        assert app.status == "confirmed" and app.receipt == independently_saved
        assert app.error_code is app.error_message is None
        assert app.charged_credits == hold.committed_amount == 20 and hold.released_amount == 0
        assert db.get(core.User, 1).job_service_credits == 30
        assert admission.state == "consumed" and admission.active_key is not None
        assert attempt.completed_at is not None
        assert attempt.state == ("failed" if late == "rejection" else "confirmed" if late == "receipt" else "unknown")
        if late == "receipt":
            assert attempt.receipt["application_id"] == "original-late-receipt"
        else:
            assert attempt.error_code == ("submission_rejected_422" if late == "rejection"
                                          else "submission_outcome_unknown" if late == "exception" else "receipt_not_verified")
        assert db.query(models.ServiceCreditEvent).filter_by(event_type="commit").count() == 1
        assert db.query(models.ServiceCreditEvent).filter_by(event_type="release").count() == 0


@pytest.mark.parametrize("unfinished", ["no_attempt", "no_completion", "old_start_without_completion"])
def test_unknown_without_known_finished_sender_keeps_credits_and_admission(context, monkeypatch, unfinished):
    factory, client, _ = context
    application, path = _queue(context, monkeypatch)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: (503, None))
    assert tasks.execute_application(application["id"]) == "unknown"
    with factory() as db:
        attempt = db.query(models.EmployerApplicationAttempt).one()
        if unfinished == "no_attempt":
            db.delete(attempt)
        else:
            attempt.completed_at = None
            if unfinished == "old_start_without_completion":
                attempt.started_at = utcnow() - timedelta(days=30)
        db.commit()
    response = client.post(path, json=_proof("not_submitted"))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "application_sender_outcome_unresolved"
    with factory() as db:
        hold = db.query(models.ServiceCreditReservation).one()
        assert hold.state == "reserved" and hold.released_amount == 0
        assert db.get(core.User, 1).job_service_credits == 30
        admission = db.query(models.EmployerAdmission).one()
        assert admission.state == "unknown" and admission.active_key is not None


def test_finished_unknown_api_request_can_still_be_independently_refunded(context, monkeypatch):
    factory, client, _ = context
    application, path = _queue(context, monkeypatch)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: (503, None))
    assert tasks.execute_application(application["id"]) == "unknown"
    monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "7")
    result = client.post(path, json=_proof("not_submitted"))
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "failed"
    with factory() as db:
        hold = db.query(models.ServiceCreditReservation).one()
        assert hold.state == "settled" and hold.committed_amount == 0 and hold.released_amount == 20
        assert db.get(core.User, 1).job_service_credits == 50
        admission = db.query(models.EmployerAdmission).one()
        assert admission.state == "released" and admission.active_key is None


@pytest.mark.parametrize("outcome", ["not_submitted", "confirmed"])
def test_generic_api_reconciliation_cannot_adjudicate_browser_submission(context, monkeypatch, outcome):
    factory, client, _ = context
    application, path = _queue(context, monkeypatch)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: (503, None))
    assert tasks.execute_application(application["id"]) == "unknown"
    with factory() as db:
        db.get(models.EmployerApplication, application["id"]).application_mode = "browser"
        # A native browser action is not evidence that an API sender finished.
        db.delete(db.query(models.EmployerApplicationAttempt).one())
        db.commit()
    response = client.post(path, json=_proof(outcome))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "native_browser_reconciliation_required"
    with factory() as db:
        hold = db.query(models.ServiceCreditReservation).one()
        assert hold.state == "reserved" and hold.released_amount == 0
        assert db.get(core.User, 1).job_service_credits == 30
