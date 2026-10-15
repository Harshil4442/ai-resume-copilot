"""Owned synthetic SQL/crypto proofs only; never an external employer submission."""
from copy import deepcopy

import pytest
from backend.app.domains.browser_assistance import hosted_network as network
from backend.app.domains.browser_assistance import models, service
from backend.tests.browser_submit_owned import OwnedSubmit
from fastapi import HTTPException


@pytest.fixture
def owned(monkeypatch):
    fixture = OwnedSubmit(monkeypatch)
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "true")
    try:
        yield fixture
    finally:
        fixture.close()


def exchange(kind, url, start, *, body_sha="a" * 64, size=500, context=None, document=None):
    return {
        "request_id": "owned-native-" + kind, "method": "GET" if kind == "presign" else "POST",
        "url": url, "tab_id": context["tab_id"] if kind == "submit" else -1,
        "frame_id": 0 if kind == "submit" else -1, "document_id": document if kind == "submit" else None,
        "initiator": service.ORIGIN if kind == "submit" else "chrome-extension://" + service.EXTENSION_ID,
        "started_at_ms": start, "completed_at_ms": start, "browser_major": 151,
        "status_code": 200, "https_validation": "browser_default",
        "request_body_sha256": None if kind == "presign" else body_sha,
        "request_body_size": 0 if kind == "presign" else size,
        "from_cache": False, "redirected": False, "ip": "192.0.2.5",
    }


def plan():
    current = service._ms(service.now())
    return {"transport": network.TRANSPORT, "presign_url": network.PRESIGN_URL,
            "storage_origin": "https://owned-fixture.s3.amazonaws.com", "storage_key": "resume/owned.pdf",
            "policy_sha256": "c" * 64, "fields_sha256": "d" * 64,
            "presign_response_sha256": "e" * 64, "expires_at_ms": current + 295_000,
            "resume_component_sha256": "f" * 64,
            "presign_evidence": exchange("presign", network.PRESIGN_URL, current - 100)}


def native_upload(owned, *, report=True):
    owned.connect()
    application = owned.device("application", {"application_id": owned.app.id, "review_protocol_version": 3})
    assert application["native_upload_available"] is True
    reviewed_plan = plan()
    context = {"tab_id": 11, "time_zone": "Asia/Kolkata"}
    review = owned.device("inspect", {"application_id": owned.app.id, "inspection": {
        "review_protocol_version": 3, "outcome": "inspected", "origin": service.ORIGIN,
        "url": owned.target, "frame_id": 0, "document_id": "owned_document", "context_digest": "e" * 64,
        "assisted_values": owned.values, "manual_fields": [],
        "attachment": {"input_id": "resume", "expected_empty": True},
        "native_context": context, "upload_plan": reviewed_plan,
    }})
    owned.candidate("approve", review["id"], confirmed=True, review_digest=review["review_digest"])
    lease = owned.device("begin", owned.begin_request(review).body)["lease"]["payload"]
    assert lease["allowed_actions"] == ["upload_resume"] and lease["upload_plan"] == reviewed_plan
    delivered = owned.device("artifact", {"review_id": review["id"], "action_id": lease["action_id"],
                                         "review_digest": review["review_digest"]})
    assert delivered.content == owned.content
    evidence = {"version": network.UPLOAD_VERSION, "review_id": review["id"], "action_id": lease["action_id"],
                "review_digest": review["review_digest"], "artifact_sha256": owned.artifact_sha,
                "artifact_size_bytes": len(owned.content), "filename": "Owned.pdf", "target_url": owned.target,
                "document_id": "owned_document", "tab_id": 11, "uploaded_url": network.uploaded_url(reviewed_plan),
                "multipart_sha256": "a" * 64, "multipart_size": len(owned.content) + 500,
                "native": exchange("upload", reviewed_plan["storage_origin"] + "/", lease["issued_at_ms"], size=len(owned.content) + 500)}
    outcome = {"review_id": review["id"], "action_id": lease["action_id"], "outcome": "selected",
               "selection_reported": True, "upload_evidence": evidence}
    if report:
        assert owned.device("outcome", outcome)["state"] == "filled"
    return evidence, context


def final_review(owned):
    evidence, context = native_upload(owned)
    application = owned.device("application", {"application_id": owned.app.id, "review_protocol_version": 4})
    upload = application["upload"]
    assert upload["native_upload"] == evidence
    review = owned.device("inspect", {"application_id": owned.app.id, "inspection": {
        "review_protocol_version": 4, "outcome": "inspected", "origin": service.ORIGIN,
        "url": owned.target, "frame_id": 0, "document_id": "owned_document", "context_digest": "e" * 64,
        "assisted_values": owned.values, "manual_fields": [], "ready": True,
        "upload": upload, "native_context": context,
        "upload_continuity": {"review_id": upload["review_id"], "action_id": upload["action_id"],
                              "artifact_sha256": owned.artifact_sha, "document_id": "owned_document", "target_url": owned.target},
    }})
    assert review["payload"]["expected_application"]["resume_url"] == evidence["uploaded_url"]
    owned.candidate("approve", review["id"], confirmed=True, review_digest=review["review_digest"])
    lease = service.device(owned.db, owned.origin, owned.begin_request(review))["lease"]["payload"]
    receipt = {"version": network.RECEIPT_VERSION, "review_id": review["id"], "action_id": lease["action_id"],
               "review_digest": review["review_digest"], "application_sha256": lease["expected_application_sha256"],
               "native": {**exchange("submit", owned.target.replace(service.ORIGIN, "https://boards.greenhouse.io"),
                                      lease["issued_at_ms"], context=context, document="owned_document"),
                          "application_sha256": lease["expected_application_sha256"]}}
    body = {"review_id": review["id"], "action_id": lease["action_id"], "receipt": receipt,
            "observation": {"status": "click_dispatched", "url": owned.target, "document_id": "owned_document"}}
    return review, lease, body


def test_exact_native_upload_and_direct_secure200_settle_once_without_inventing_application_id(owned):
    review, lease, body = final_review(owned)
    assert owned.audit()["reservations"][0]["state"] == "reserved"
    assert owned.device("outcome", body)["submission_status"] == "confirmed"
    audit = owned.audit()
    assert audit["charged_credits"] == 20 and audit["credits"] == 80
    assert audit["receipt"]["contract"] == network.RECEIPT_VERSION and "application_id" not in audit["receipt"]
    assert audit["receipt"]["network"]["request_id"] == body["receipt"]["native"]["request_id"]
    assert owned.device("reconcile", body)["submission_status"] == "confirmed"
    assert owned.audit()["charged_credits"] == 20 and owned.audit()["attempts"] == 1
    assert lease["expected_application_sha256"] == service.digest(review["payload"]["expected_application"])


@pytest.mark.parametrize("field,value", [
    ("status_code", 202), ("from_cache", True), ("redirected", True), ("https_validation", "insecure"),
    ("certificate_sha256", "bad"), ("ip", "not-an-IP"), ("method", "GET"), ("tab_id", 12),
    ("frame_id", 1), ("document_id", "other_document"), ("browser_major", 143),
    ("url", "https://boards.greenhouse.io/other/jobs/4740333005"), ("application_sha256", "0" * 64),
    ("request_body_sha256", None), ("request_body_size", 0),
])
def test_incomplete_or_changed_native_exchange_never_settles_or_sends_again(owned, field, value):
    _, _, body = final_review(owned)
    body["receipt"]["native"][field] = value
    with pytest.raises(HTTPException):
        owned.device("outcome", body)
    owned.db.rollback()
    audit = owned.audit()
    assert audit["charged_credits"] == 0 and audit["receipt"] is None
    assert audit["reservations"][0]["state"] == "reserved" and audit["attempts"] == 1
    assert len(owned.native.calls) == 1


def test_captured_original_receipt_reconciles_after_review_erasure_without_extending_send_lease(owned, monkeypatch):
    review, _, body = final_review(owned)
    row = owned.db.get(models.BrowserAssistanceReview, review["id"])
    row.payload, row.state = {}, "expired"
    owned.db.commit()
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "false")
    assert owned.device("reconcile", body)["submission_status"] == "confirmed"
    assert owned.audit()["charged_credits"] == 20 and len(owned.native.calls) == 1


def test_original_upload_proof_reports_after_lost_outcome_and_erased_review_without_uploading_again(owned, monkeypatch):
    evidence, _ = native_upload(owned, report=False)
    row = owned.db.get(models.BrowserAssistanceReview, evidence["review_id"])
    row.payload, row.state = {}, "expired"
    owned.db.commit()
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "false")
    report = {"review_id": evidence["review_id"], "action_id": evidence["action_id"],
              "upload_evidence": evidence, "selection_reported": True}
    assert owned.device("reconcile", report)["state"] == "filled"
    assert owned.device("reconcile", report)["state"] == "filled"
    owned.db.expire_all()
    row = owned.db.get(models.BrowserAssistanceReview, evidence["review_id"])
    action = owned.db.get(models.BrowserAssistanceRequest, evidence["action_id"])
    assert row.payload == {} and action.response["native_upload"] == evidence
    assert owned.audit()["attempts"] == 0 and owned.audit()["charged_credits"] == 0
    with pytest.raises(HTTPException):
        owned.device("artifact", {"review_id": row.id, "action_id": action.id, "review_digest": row.review_digest})


@pytest.mark.parametrize("field,value", [
    ("multipart_sha256", "f" * 64), ("multipart_size", 1), ("artifact_sha256", "f" * 64),
    ("uploaded_url", "https://other.s3.amazonaws.com/resume/owned.pdf"), ("document_id", "another_document"),
    ("tab_id", 12), ("filename", "another.pdf"),
])
def test_filename_or_partial_upload_proof_cannot_be_adopted(owned, field, value):
    evidence, _ = native_upload(owned, report=False)
    evidence[field] = value
    with pytest.raises(HTTPException):
        owned.device("reconcile", {"review_id": evidence["review_id"], "action_id": evidence["action_id"],
                                   "upload_evidence": evidence, "selection_reported": True})
    owned.db.rollback()
    action = owned.db.get(models.BrowserAssistanceRequest, evidence["action_id"])
    assert "native_upload" not in action.response and owned.audit()["charged_credits"] == 0


def test_legacy_filename_observation_approval_never_acquires_native_receipt_permission(owned):
    review = owned.approved()
    lease = service.device(owned.db, owned.origin, owned.begin_request(review))["lease"]["payload"]
    assert "receipt_contract" not in lease
    body = {"review_id": review["id"], "action_id": lease["action_id"],
            "observation": {"status": "confirmation_observed", "url": owned.target, "document_id": "owned_document"},
            "receipt": {"version": network.RECEIPT_VERSION, "review_id": review["id"], "action_id": lease["action_id"],
                        "review_digest": review["review_digest"], "application_sha256": "a" * 64, "native": {}}}
    with pytest.raises(HTTPException):
        owned.device("outcome", body)
    owned.db.rollback()
    assert owned.audit()["receipt"] is None and owned.audit()["charged_credits"] == 0


@pytest.mark.parametrize("change", ["invalid_origin", "non_storage_origin", "secret_fields", "stale_presign", "expired", "component_changed"])
def test_native_upload_plan_refuses_unbound_or_stale_storage_metadata(owned, change):
    value = plan()
    current = service._ms(service.now())
    if change == "invalid_origin":
        value["storage_origin"] = "https://[invalid"
    elif change == "non_storage_origin":
        value["storage_origin"] = "https://unrelated.example"
    elif change == "secret_fields":
        value["policy"] = "must-never-be-retained-in-a-review"
    elif change == "stale_presign":
        value["presign_evidence"]["started_at_ms"] = current - 30_001
    elif change == "expired":
        value["expires_at_ms"] = current
    else:
        value["resume_component_sha256"] = "bad"
    with pytest.raises(HTTPException):
        network.upload_plan(value, now_ms=current)


def test_final_exchange_cannot_extend_original_send_deadline(owned):
    _, lease, body = final_review(owned)
    body["receipt"]["native"]["completed_at_ms"] = lease["deadline"] + 1
    with pytest.raises(HTTPException):
        owned.device("reconcile", body)
    owned.db.rollback()
    assert owned.audit()["charged_credits"] == 0 and owned.audit()["receipt"] is None


def test_certificate_contract_approval_cannot_be_retrofitted_to_browser_https(owned):
    _, _, body = final_review(owned)
    body["receipt"]["version"] = "greenhouse_hosted_direct_200_v1"
    body["receipt"]["native"].pop("https_validation")
    body["receipt"]["native"].update(tls_state="secure", certificate_sha256="b" * 64)
    with pytest.raises(HTTPException):
        owned.device("reconcile", body)
    owned.db.rollback()
    assert owned.audit()["charged_credits"] == 0 and owned.audit()["receipt"] is None


def test_serializer_matches_feed_order_boolean_choice_employment_and_ignores_phone_country():
    from types import SimpleNamespace
    fields = [{"id": "question_20", "type": "single_select"}, {"id": "first_name", "type": "text"},
              {"id": "question_10", "type": "text"}, {"id": "last_name", "type": "text"},
              {"id": "email", "type": "email"}, {"id": "phone", "type": "tel"}]
    answers = {"first_name": "Owned", "last_name": "Candidate", "email": "owned@example.test", "phone": "+910000000000",
               "question_10": "Text", "question_20": "1", "country": "IN", "company-name-0": "Owned employer",
               "title-0": "Engineer", "start-date-month-0": "July", "start-date-year-0": "2025", "current-role-0_1": "true"}
    mapping = [{"id": key, "type": "combobox" if key in {"question_20", "country", "start-date-month-0"} else "text"} for key in answers]
    result = network.expected_application(SimpleNamespace(form={"fields": fields}), mapping, answers,
                                          {"tab_id": 11, "time_zone": "Asia/Kolkata"}, {"uploaded_url": "https://owned.s3.amazonaws.com/r.pdf", "filename": "Owned.pdf"})
    assert result["answers_attributes"] == {"20": {"question_id": "20", "priority": 0, "boolean_value": 1},
                                             "10": {"question_id": "10", "priority": 1, "text_value": "Text"}}
    assert "country_short_name" not in result and "end_date" not in result["employments"][0]
    changed = deepcopy(answers)
    changed["hidden-consent"] = "true"
    with pytest.raises(HTTPException):
        network.expected_application(SimpleNamespace(form={"fields": fields}), mapping + [{"id": "hidden-consent", "type": "checkbox"}], changed,
                                     {"tab_id": 11, "time_zone": "Asia/Kolkata"}, {"uploaded_url": "https://owned.s3.amazonaws.com/r.pdf", "filename": "Owned.pdf"})
