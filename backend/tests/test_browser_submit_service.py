"""Synthetic SQL/crypto/20-credit lifecycle; no provider calls or real data."""

from copy import deepcopy

import pytest
from backend.app.domains.browser_assistance import models, service
from backend.app.domains.employer import credits
from backend.app.domains.employer import models as employer
from backend.tests.browser_submit_owned import OwnedSubmit
from fastapi import HTTPException


@pytest.fixture
def owned(monkeypatch):
    fixture = OwnedSubmit(monkeypatch)
    try:
        yield fixture
    finally:
        fixture.close()


def launch(owned, review):
    return service.device(owned.db, owned.origin, owned.begin_request(review))


def test_final_review_exact_complete_package_and_explicit_twenty_credit_reservation(owned):
    review = owned.review()
    payload = review["payload"]
    assert (
        payload["review_protocol_version"],
        payload["allowed_actions"],
        payload["fee_credits"],
    ) == (4, ["submit"], 20)
    assert payload["reviewed_answers"] == owned.values
    assert [answer["value"] for answer in payload["answers"]] == list(owned.values.values())
    assert (
        payload["candidate_id"] == 1
        and payload["company"] == "Razorpay"
        and payload["job"]["external_id"] == "4740333005"
    )
    assert (
        payload["artifact"]["resume_choice"] == "original"
        and payload["artifact"]["sha256"] == owned.artifact_sha
    )
    assert (
        payload["target_url"] == owned.target
        and payload["upload"]["status"] == "portal_reports_uploaded"
    )
    assert owned.audit()["credits"] == 100 and not owned.audit()["reservations"]
    for values in [
        {"confirmed": False, "review_digest": review["review_digest"]},
        {"confirmed": True, "review_digest": "0" * 64},
    ]:
        with pytest.raises(HTTPException):
            owned.candidate("approve", review["id"], **values)
        owned.db.rollback()
        assert owned.audit()["credits"] == 100
    owned.candidate("approve", review["id"], confirmed=True, review_digest=review["review_digest"])
    assert owned.audit()["reservations"] == [
        {"state": "reserved", "reserved": 20, "committed": 0, "released": 0}
    ]
    assert owned.audit()["credits"] == 80 and owned.audit()["native_calls"] == 0


def test_durable_one_shot_distinct_lease_lost_ack_and_no_new_native_reserve(owned):
    review = owned.approved()
    request = owned.begin_request(review)
    result = service.device(owned.db, owned.origin, request)
    assert result["state"] == "begun"
    lease = result["lease"]["payload"]
    assert (lease["review_protocol_version"], lease["allowed_actions"], lease["fee_credits"]) == (
        4,
        ["submit"],
        20,
    )
    assert (
        lease["field_id"] == "final-submit"
        and lease["control_type"] == "submit"
        and lease["upload"] == review["payload"]["upload"]
    )
    assert lease["deadline"] - lease["issued_at_ms"] <= 10000
    assert (
        owned.audit()["attempts"]
        == owned.audit()["submit_actions"]
        == owned.audit()["native_calls"]
        == 1
    )
    assert service.device(owned.db, owned.origin, request)["state"] == "unknown"
    with pytest.raises(HTTPException):
        launch(owned, review)
    owned.db.rollback()
    assert (
        owned.audit()["native_calls"] == 1
        and owned.audit()["charged_credits"] == 0
        and owned.audit()["receipt"] is None
    )


@pytest.mark.parametrize("failure", ["unknown", "exception"])
def test_native_uncertainty_is_durable_unknown_no_replay_no_refund(owned, failure):
    review = owned.approved()
    owned.native.fail = failure
    assert launch(owned, review) == {"state": "unknown"}
    assert owned.audit()["application_status"] == "unknown" and owned.audit()["credits"] == 80
    assert owned.audit()["reservations"][0]["state"] == "reserved"
    with pytest.raises(HTTPException):
        launch(owned, review)
    owned.db.rollback()
    assert owned.audit()["native_calls"] == 1


@pytest.mark.parametrize(
    "fault",
    ["cancel", "revoke", "expired", "flag", "typed_flag", "source", "credential", "artifact"],
)
def test_fresh_sql_checks_after_unlocked_native_io_prevent_export(owned, monkeypatch, fault):
    review = owned.approved()

    def mutate():
        if fault == "cancel":
            owned.candidate("cancel", review["id"], confirmed=True)
        elif fault == "revoke":
            owned.candidate("revoke", owned.device_id, confirmed=True)
        elif fault == "expired":
            owned.db.get(models.BrowserAssistanceReview, review["id"]).expires_at = (
                service.now() - service.timedelta(seconds=1)
            )
        elif fault == "flag":
            monkeypatch.setenv("BROWSER_ASSISTANCE_SUBMIT_ENABLED", "false")
        elif fault == "typed_flag":
            monkeypatch.setenv("BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED", "false")
        elif fault == "source":
            owned.db.get(employer.EmployerSource, "source_test").enabled = False
        elif fault == "credential":
            from backend.app.models import CandidatePasswordAccount

            owned.db.get(CandidatePasswordAccount, 1).auth_generation = 2
        else:
            owned.db.get(employer.SealedApplicationArtifact, "artifact_owned").gcs_generation = "2"
        owned.db.commit()

    owned.native.on_reserve = mutate
    assert launch(owned, review) == {"state": "unknown"}
    assert owned.audit()["application_status"] == "unknown" and owned.audit()["credits"] == 80
    assert (
        owned.db.query(models.BrowserAssistanceRequest)
        .filter_by(operation="submit")
        .one()
        .response["lease_exported"]
        is False
    )
    assert owned.audit()["receipt"] is None and owned.audit()["charged_credits"] == 0


@pytest.mark.parametrize(
    "path", ["candidate", "device", "revoke", "status_expired", "maintenance_expired"]
)
def test_unclaimed_cancel_or_expiry_refunds_exactly_once(owned, path):
    review = owned.approved()
    if path == "candidate":
        owned.candidate("cancel", review["id"], confirmed=True)
        owned.candidate("cancel", review["id"], confirmed=True)
    elif path == "device":
        owned.device("cancel", {"review_id": review["id"]})
    elif path == "revoke":
        owned.candidate("revoke", owned.device_id, confirmed=True)
    else:
        owned.db.get(models.BrowserAssistanceReview, review["id"]).expires_at = (
            service.now() - service.timedelta(seconds=1)
        )
        owned.db.commit()
        if path == "status_expired":
            owned.device("status", {"review_id": review["id"]})
        else:
            service.expire_reviews(owned.db)
    audit = owned.audit()
    assert audit["credits"] == 100 and audit["attempts"] == audit["native_calls"] == 0
    assert owned.db.get(employer.EmployerApplication, owned.app.id).active_key is None
    assert audit["reservations"] == [
        {"state": "settled", "reserved": 20, "committed": 0, "released": 20}
    ]
    assert owned.db.query(employer.EmployerAdmission).one().state == "released"
    from backend.app.domains.employer import schemas as employer_schemas, service as employer_service
    fresh=employer_service.create_application(owned.db,1,employer_schemas.ApplicationCreate(posting_id="job_1",resume_id=10,
        resume_choice="original",idempotency_key="fresh_after_safe_"+path))
    assert fresh.id!=owned.app.id and fresh.active_key is not None
    assert owned.audit()["credits"]==100


def test_confirmation_observations_never_verify_receipt_or_settle(owned):
    review = owned.approved()
    lease = launch(owned, review)["lease"]["payload"]
    for status in [
        "click_dispatched",
        "confirmation_observed",
        "validation_error",
        "waiting_user",
        "unknown",
    ]:
        result = owned.device(
            "reconcile",
            {
                "review_id": review["id"],
                "action_id": lease["action_id"],
                "observation": {
                    "status": status,
                    "url": owned.target + "/confirmation",
                    "document_id": "new_document",
                },
            },
        )
        assert result["employer_confirmed"] is False and result["submission_status"] == "unknown"
    audit = owned.audit()
    assert (
        audit["credits"] == 80
        and audit["charged_credits"] == 0
        and audit["receipt"] is None
        and audit["native_calls"] == 1
    )
    assert audit["reservations"][0]["state"] == "reserved"


def test_minimal_selection_provenance_survives_expired_answer_payload(owned):
    owned.connect()
    owned.seed_selection()
    owned.db.query(models.BrowserAssistanceReview).one().expires_at = (
        service.now() - service.timedelta(seconds=1)
    )
    action = owned.db.query(models.BrowserAssistanceRequest).filter_by(operation="attachment").one()
    action.created_at = service.now() - service.timedelta(minutes=10)
    owned.db.commit()
    service.expire_reviews(owned.db)
    app = owned.device(
        "application", {"application_id": owned.app.id, "review_protocol_version": 4}
    )
    assert app["upload"]["action_id"] == action.id
    assert (
        owned.db.get(models.BrowserAssistanceRequest, action.id).response["selection_document_id"]
        == "owned_document"
    )


@pytest.mark.parametrize(
    "fault",
    [
        "cross_document",
        "selection_url",
        "current_url",
        "artifact_hash",
        "missing_document",
        "missing_url",
        "missing_artifact",
        "missing_reported",
    ],
)
def test_final_review_refuses_cross_document_destination_or_missing_selection_provenance(
    owned, fault
):
    owned.connect()
    upload = owned.seed_selection()
    owned.device("application", {"application_id": owned.app.id, "review_protocol_version": 4})
    action = owned.db.get(models.BrowserAssistanceRequest, upload["action_id"])
    provenance = dict(action.response)
    if fault == "selection_url":
        provenance["selection_target_url"] = owned.target.rsplit("/", 1)[0] + "/4740333006"
    if fault == "artifact_hash":
        provenance["selection_artifact_sha256"] = "0" * 64
    missing = {
        "missing_document": "selection_document_id",
        "missing_url": "selection_target_url",
        "missing_artifact": "selection_artifact_sha256",
        "missing_reported": "selection_reported",
    }
    if fault in missing:
        provenance.pop(missing[fault])
    action.response = provenance
    owned.db.commit()
    inspection = {
        "review_protocol_version": 4,
        "outcome": "inspected",
        "origin": service.ORIGIN,
        "url": owned.target,
        "frame_id": 0,
        "document_id": "owned_document",
        "context_digest": "e" * 64,
        "assisted_values": owned.values,
        "manual_fields": [],
        "ready": True,
        "upload": upload,
    }
    if fault == "cross_document":
        inspection["document_id"] = "another_chrome_document"
    if fault == "current_url":
        inspection["url"] = owned.target.rsplit("/", 1)[0] + "/4740333006"
    inspection["upload_continuity"] = {
        "review_id": upload["review_id"],
        "action_id": upload["action_id"],
        "artifact_sha256": owned.artifact_sha,
        "document_id": inspection["document_id"],
        "target_url": inspection["url"],
    }
    with pytest.raises(HTTPException) as refused:
        owned.device("inspect", {"application_id": owned.app.id, "inspection": inspection})
    assert refused.value.status_code == 409
    owned.db.rollback()
    assert (
        owned.db.query(models.BrowserAssistanceReview).filter_by(review_protocol_version=4).count()
        == 0
    )
    audit = owned.audit()
    assert audit["credits"] == 100 and not audit["reservations"]
    assert audit["attempts"] == audit["native_calls"] == audit["submit_actions"] == 0
    assert audit["charged_credits"] == 0 and audit["receipt"] is None


@pytest.mark.parametrize("terminal", ["confirmed", "verified_not_submitted"])
def test_late_browser_report_and_cancel_preserve_independent_terminal_reconciliation(
    owned, terminal
):
    review = owned.approved()
    lease = launch(owned, review)["lease"]["payload"]
    app = owned.db.get(employer.EmployerApplication, owned.app.id)
    reservation = owned.db.get(employer.ServiceCreditReservation, app.reservation_id)
    # Owned terminal state fixture tests monotonicity, not provider authenticity.
    credits.settle(
        owned.db,
        reservation,
        completed_count=1 if terminal == "confirmed" else 0,
        reason="Owned independent adjudication fixture",
    )
    if terminal == "confirmed":
        app.status, app.charged_credits, app.receipt = (
            "confirmed",
            20,
            {"completion_verified": True, "application_id": "owned_fixture_receipt"},
        )
    else:
        app.status, app.error_code = "failed", "verified_not_submitted"
    owned.db.commit()
    before = deepcopy(owned.audit())
    result = owned.device(
        "reconcile",
        {
            "review_id": review["id"],
            "action_id": lease["action_id"],
            "observation": {
                "status": "unknown",
                "url": owned.target,
                "document_id": "owned_document",
            },
        },
    )
    assert result["submission_status"] == terminal
    owned.candidate("cancel", review["id"], confirmed=True)
    assert owned.audit() == before
