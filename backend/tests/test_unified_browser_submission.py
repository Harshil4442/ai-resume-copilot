"""Real local SQLite wallet/quote lifecycle; synthetic ports, no native acceptance."""
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from backend.app import models as core
from backend.app.domains.browser_assistance import models
from backend.app.domains.browser_assistance import service as browser
from backend.app.domains.credits import privacy, runtime, service, submission
from backend.app.domains.credits.models import (
    CreditExecutionHold,
    CreditReservation,
    CreditServiceQuote,
)
from backend.app.domains.credits.pricing import aware
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import models as employer
from backend.app.domains.employer import service as employer_service
from backend.tests.browser_submission_unified_fixture import approve, configure, review
from backend.tests.browser_submit_owned import OwnedSubmit
from backend.tests.test_browser_hosted_network import exchange
from fastapi import HTTPException


@pytest.fixture
def owned(monkeypatch):
    fixture = OwnedSubmit(monkeypatch)
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "true")
    try:
        yield fixture
    finally:
        fixture.close()


def balance(owned):
    owned.db.expire_all()
    return service.wallet(owned.db, user_id=1).available_credits


@pytest.mark.parametrize("name", ["browser-companion/extension/submission-billing.js",
    "frontend/lib/browserSubmissionBilling.ts", "frontend/lib/browserHostedNetworkReview.ts"])
def test_actual_quote_validator_drift_changes_contract_and_refuses_before_quote_or_spend(owned, monkeypatch, name):
    configure(owned, monkeypatch)
    before = submission.delivery_contract()
    path = Path(submission.__file__).resolve().parents[4] / name
    original = Path.read_bytes
    reads = []

    def changed(current):
        if current == path:
            reads.append(current)
            return original(current) + b"\n// different quote/receipt validation source\n"
        return original(current)

    monkeypatch.setattr(Path, "read_bytes", changed)
    assert submission.delivery_contract() != before and reads
    with pytest.raises(HTTPException) as error:
        review(owned)
    assert error.value.status_code == 409
    owned.db.rollback()
    assert balance(owned) == 40 and owned.audit()["credits"] == 100
    assert owned.db.query(CreditServiceQuote).count() == owned.db.query(CreditReservation).count() == 0
    assert owned.db.query(CreditExecutionHold).count() == 0 and owned.audit()["native_calls"] == 0


def test_three_credit_quote_is_inert_and_acceptance_never_debits_legacy_wallet(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    assert value["fee_credits"] == 3 and value["review_protocol_version"] == 5
    assert value["billing"] == value["payload"]["billing"] == value["payload"]["pricing"]
    assert balance(owned) == 40 and owned.audit()["credits"] == 100
    assert owned.db.query(CreditExecutionHold).count() == owned.db.query(CreditReservation).count() == 0
    approve(owned, value)
    assert balance(owned) == 37 and owned.audit()["credits"] == 100
    row, hold = submission.held(owned.db, owned.app, pending=True)
    assert (row.reserved_credits, hold.native_reserved_minor, hold.reserved_minor) == (3, 13, 13)
    assert hold.snapshot["submission"] == {"schema_version": "unified-browser-submission-v1",
        "quote_id": value["billing"]["quote_id"], "billing_sha256": browser.digest(value["billing"]),
        "billing_currency": "credits", "credits": 3, "funding_currency": "INR"}
    assert hold.delivery_state == "pending" and hold.native_settled_minor is None
    assert owned.app.reservation_id is None and owned.db.query(employer.ServiceCreditReservation).count() == 0
    assert owned.db.query(DispatchOutbox).count() == 0
    dto = employer_service.application_response(owned.db, owned.app)
    assert dto["reserved_credits"] == 3 and dto["billing_currency"] == "credits" and dto["billing"] == value["billing"]
    assert owned.audit()["native_calls"] == 0


@pytest.mark.parametrize("quantity", [0, 2])
def test_no_fallback_to_large_legacy_balance_when_unified_balance_is_insufficient(owned, monkeypatch, quantity):
    configure(owned, monkeypatch, quantity=quantity)
    value = review(owned)
    with pytest.raises((HTTPException, ValueError)):
        approve(owned, value)
    owned.db.rollback()
    assert owned.audit()["credits"] == 100
    assert owned.db.query(CreditReservation).count() == owned.db.query(CreditExecutionHold).count() == 0


@pytest.mark.parametrize("change", ["missing", "wrong", "foreign", "expired", "equality", "route", "credential", "answers", "artifact_filename"])
def test_approval_revalidates_quote_owner_expiry_current_inputs_and_terms(owned, monkeypatch, change):
    config = configure(owned, monkeypatch)
    value = review(owned)
    quote = owned.db.get(CreditServiceQuote, value["billing"]["quote_id"])
    kwargs = dict(confirmed=True, review_digest=value["review_digest"], accepted_service_quote_id=quote.id)
    if change == "missing":
        kwargs.pop("accepted_service_quote_id")
    elif change == "wrong":
        kwargs["accepted_service_quote_id"] = "csq_" + "0" * 32
    elif change == "foreign":
        quote.user_id = 2
    elif change in {"expired", "equality"}:
        stamp = aware(quote.expires_at) + (timedelta(milliseconds=1) if change == "expired" else timedelta())
        monkeypatch.setattr(browser, "now", lambda: stamp)
    elif change == "route":
        changed = config.model_copy(update={"routes": (config.routes[0].model_copy(update={"version": "different"}),)})
        from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
        reviewed_runtime(monkeypatch, changed)
        monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", changed.model_dump_json())
    elif change == "credential":
        owned.db.get(core.User, 1).password_hash = "changed-credential"
    elif change == "artifact_filename":
        owned.db.get(employer.SealedApplicationArtifact, owned.app.artifact_id).filename = "Changed.pdf"
    else:
        owned.app.answers = {**owned.app.answers, "first_name": "New exact answer"}
    owned.db.commit()
    with pytest.raises((HTTPException, ValueError)):
        owned.candidate("approve", value["id"], **kwargs)
    owned.db.rollback()
    assert owned.db.query(CreditReservation).count() == 0 and owned.audit()["credits"] == 100


def test_replaced_draft_keeps_old_quote_but_old_cancel_cannot_touch_new_hold(owned, monkeypatch):
    configure(owned, monkeypatch)
    old = review(owned)
    new = review(owned)
    assert old["billing"]["quote_id"] != new["billing"]["quote_id"]
    assert owned.db.query(CreditServiceQuote).count() == 2
    with pytest.raises(HTTPException):
        approve(owned, old)
    owned.db.rollback()
    approve(owned, new)
    owned.candidate("cancel", old["id"], confirmed=True)
    assert balance(owned) == 37
    assert submission.held(owned.db, owned.app)[1].delivery_state == "pending"


def test_expired_unaccepted_display_never_requotes_until_new_explicit_inspection(owned, monkeypatch):
    configure(owned, monkeypatch)
    old = review(owned)
    stamp = browser._aware(owned.db.get(models.BrowserAssistanceReview, old["id"]).expires_at)
    monkeypatch.setattr(browser, "now", lambda: stamp)
    response = owned.candidate("review", old["id"])
    assert response["state"] == "expired" and response["billing"] == old["billing"]
    assert owned.db.query(CreditServiceQuote).count() == 1
    assert owned.db.query(CreditReservation).count() == 0 and balance(owned) == 40
    new = review(owned)
    assert new["state"] == "draft" and new["billing"]["quote_id"] != old["billing"]["quote_id"]
    assert owned.db.query(CreditServiceQuote).count() == 2
    assert owned.db.query(CreditReservation).count() == 0


def test_expired_accepted_display_refunds_once_and_requires_new_application_identity(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    stamp = browser._aware(owned.db.get(models.BrowserAssistanceReview, value["id"]).expires_at)
    monkeypatch.setattr(browser, "now", lambda: stamp)
    assert owned.candidate("review", value["id"])["state"] == "expired"
    assert balance(owned) == 40
    assert submission.held(owned.db, owned.app)[0].released_credits == 3
    with pytest.raises(HTTPException):
        review(owned)


def test_preclaim_cancel_refunds_once_retains_cash_and_same_source_cannot_reprice(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    owned.candidate("cancel", value["id"], confirmed=True)
    owned.candidate("cancel", value["id"], confirmed=True)
    assert balance(owned) == 40 and owned.audit()["credits"] == 100
    row, hold = submission.held(owned.db, owned.app)
    assert row.released_credits == 3 and row.committed_credits == 0
    assert hold.delivery_state == "failed" and hold.native_settled_minor is None and hold.reserved_minor == 13
    with pytest.raises(HTTPException):
        review(owned)
    assert owned.db.query(CreditReservation).count() == 1


def test_unknown_native_ack_never_refunds_requotes_or_repeats_send(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    owned.native.fail = "unknown"
    request = owned.begin_request(value)
    assert browser.device(owned.db, owned.origin, request) == {"state": "unknown"}
    assert browser.device(owned.db, owned.origin, request)["state"] == "unknown"
    owned.candidate("cancel", value["id"], confirmed=True)
    assert balance(owned) == 37 and owned.audit()["native_calls"] == 1
    row, hold = submission.held(owned.db, owned.app, pending=True)
    assert row.committed_credits == row.released_credits == 0 and hold.native_settled_minor is None


def test_lease_binds_exact_quote_and_wallet_hold_without_worker_dispatch(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    result = browser.device(owned.db, owned.origin, owned.begin_request(value))
    lease = result["lease"]["payload"]
    assert lease["review_protocol_version"] == 5 and lease["fee_credits"] == 3
    assert lease["billing"] == value["billing"]
    assert lease["deadline"] <= value["billing"]["quote_expires_at_ms"]
    assert owned.native.calls[0][1].pricing_sha256 == browser.digest(value["billing"])
    assert owned.audit()["native_calls"] == 1 and balance(owned) == 37
    assert owned.db.query(DispatchOutbox).count() == 0


def test_unified_activation_refuses_new_legacy_review_but_accepted_twenty_stays_recoverable(owned, monkeypatch):
    accepted = owned.approved()
    configure(owned, monkeypatch)
    with pytest.raises(HTTPException):
        owned.device("application", {"application_id": owned.app.id, "review_protocol_version": 5})
    owned.db.rollback()
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("UNIFIED_BROWSER_SUBMISSION_ENABLED", "false")
    lease = browser.device(owned.db, owned.origin, owned.begin_request(accepted))["lease"]["payload"]
    assert lease["review_protocol_version"] == 4 and lease["fee_credits"] == 20 and "billing" not in lease
    assert owned.audit()["credits"] == 80 and owned.db.query(CreditReservation).count() == 0


@pytest.mark.parametrize("outcome", ["receipt", "cancel"])
def test_accepted_legacy_twenty_settlement_and_refund_do_not_convert_after_pause(owned, monkeypatch, outcome):
    if outcome == "receipt":
        from backend.tests.test_browser_hosted_network import final_review
        value, _, body = final_review(owned)
    else:
        value = owned.approved()
    configure(owned, monkeypatch)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("UNIFIED_BROWSER_SUBMISSION_ENABLED", "false")
    if outcome == "receipt":
        assert owned.device("outcome", body)["submission_status"] == "confirmed"
        assert owned.audit()["charged_credits"] == 20 and owned.audit()["credits"] == 80
    else:
        owned.candidate("cancel", value["id"], confirmed=True)
        assert owned.audit()["charged_credits"] == 0 and owned.audit()["credits"] == 100
    assert balance(owned) == 40 and owned.db.query(CreditReservation).count() == 0


def test_accepted_unified_history_blocks_legacy_fallback_after_pause(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    owned.candidate("cancel", value["id"], confirmed=True)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("UNIFIED_BROWSER_SUBMISSION_ENABLED", "false")
    assert runtime.requires_unified_billing(owned.db, 1)
    assert runtime.has_unified_contract(owned.db, 1)


def test_new_legacy_approval_for_unified_owner_is_refused_without_hold(owned, monkeypatch):
    old = owned.review()
    configure(owned, monkeypatch)
    with pytest.raises(HTTPException):
        owned.candidate("approve", old["id"], confirmed=True, review_digest=old["review_digest"])
    owned.db.rollback()
    assert owned.audit()["credits"] == 100 and owned.db.query(CreditReservation).count() == 0


def test_over_budget_or_disabled_route_creates_no_quote_hold_or_native_call(owned, monkeypatch):
    configure(owned, monkeypatch, cost=136)
    with pytest.raises(HTTPException):
        review(owned)
    owned.db.rollback()
    assert owned.db.query(CreditServiceQuote).count() == owned.db.query(CreditReservation).count() == 0
    assert owned.audit()["native_calls"] == 0


def test_owner_erasure_releases_unstarted_but_retains_unknown_money_detached(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    privacy.erase_owner_credit_links(owned.db, user_id=1)
    owned.db.commit()
    row = owned.db.query(CreditReservation).one()
    hold = owned.db.query(CreditExecutionHold).one()
    assert row.user_id is hold.user_id is None
    assert row.released_credits == 3 and hold.delivery_state == "failed"
    assert hold.native_settled_minor is None and hold.reserved_minor == 13


def test_unknown_owner_erasure_never_turns_possible_send_into_a_refund(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    owned.native.fail = "unknown"
    browser.device(owned.db, owned.origin, owned.begin_request(value))
    privacy.erase_owner_credit_links(owned.db, user_id=1)
    owned.db.commit()
    row, hold = owned.db.query(CreditReservation).one(), owned.db.query(CreditExecutionHold).one()
    assert row.user_id is hold.user_id is None
    assert row.committed_credits == row.released_credits == 0
    assert hold.delivery_state == "pending" and hold.native_settled_minor is None


def test_generic_cancel_projects_refund_and_accepted_package_cannot_be_edited(owned, monkeypatch):
    from backend.app.domains.employer import artifacts, schemas

    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    monkeypatch.setattr(artifacts, "materialize_for_application", lambda *_: pytest.fail("Accepted quote must refuse before rendering"))
    payload = schemas.PackageUpdate(resume_id=owned.app.resume_id, resume_choice="original", answers=owned.values)
    with pytest.raises(HTTPException):
        employer_service.update_package(owned.db, 1, owned.app.id, payload)
    owned.db.rollback()
    app = employer_service.cancel_application(owned.db, 1, owned.app.id)
    dto = employer_service.application_response(owned.db, app)
    assert dto["reserved_credits"] == 0 and dto["billing"] == value["billing"]
    assert balance(owned) == 40


def test_unaccepted_legacy_draft_requires_fresh_five_quote_and_no_twenty_hold(owned, monkeypatch):
    old = review(owned, version=4)
    configure(owned, monkeypatch)
    value = review(owned)
    assert value["review_protocol_version"] == 5 and value["fee_credits"] == 3
    assert owned.db.get(models.BrowserAssistanceReview, old["id"]).state == "cancelled"
    approve(owned, value)
    assert balance(owned) == 37 and owned.audit()["credits"] == 100
    assert owned.db.query(employer.ServiceCreditReservation).count() == 0


@pytest.mark.parametrize("code", [200, 202])
def test_synthetic_receipt_validation_charges_three_once_only_for_exact_accepted_exchange(owned, monkeypatch, code):
    # Format/validator control only: this is NOT a real employer/native receipt.
    from backend.app.domains.browser_assistance import hosted_network as network

    configure(owned, monkeypatch)
    value = review(owned)
    approve(owned, value)
    lease = browser.device(owned.db, owned.origin, owned.begin_request(value))["lease"]["payload"]
    native = exchange("submit", owned.target.replace(browser.ORIGIN, "https://boards.greenhouse.io"),
        lease["issued_at_ms"], context=value["payload"]["native_context"], document="owned_document")
    native.update(status_code=code, application_sha256=lease["expected_application_sha256"])
    body = {"review_id": value["id"], "action_id": lease["action_id"],
        "receipt": {"version": network.RECEIPT_VERSION, "review_id": value["id"],
            "action_id": lease["action_id"], "review_digest": value["review_digest"],
            "application_sha256": lease["expected_application_sha256"], "native": native},
        "observation": {"status": "click_dispatched", "url": owned.target, "document_id": "owned_document"}}
    if code == 202:
        with pytest.raises(HTTPException):
            owned.device("outcome", body)
        owned.db.rollback()
        assert submission.held(owned.db, owned.app)[0].committed_credits == 0
    else:
        assert owned.device("outcome", body)["submission_status"] == "confirmed"
        assert owned.device("reconcile", body)["submission_status"] == "confirmed"
        row, hold = submission.held(owned.db, owned.app)
        assert row.committed_credits == 3 and hold.delivery_state == "delivered"
        assert hold.native_settled_minor is None and hold.reserved_minor == 13
        assert employer_service.application_response(owned.db, owned.app)["charged_credits"] == 3
    assert owned.audit()["credits"] == 100 and owned.audit()["native_calls"] == 1


def test_public_billing_is_closed_and_does_not_expose_internal_funding_or_credentials(owned, monkeypatch):
    configure(owned, monkeypatch)
    value = review(owned)
    price = deepcopy(value["billing"])
    assert len(price) == 13 and price["billing_currency"] == "credits"
    assert not {"native_reserved_minor", "budgets", "policy", "context", "password_hash"} & price.keys()
    assert browser.availability()["submit_protocol_versions"] == [4, 5]


def test_expiry_during_real_hold_admission_rolls_back_without_accepting_or_repricing(owned, monkeypatch):
    from backend.app.domains.credits import admission

    configure(owned, monkeypatch)
    value = review(owned)
    original = admission.admit
    deadline = browser._aware(owned.db.get(models.BrowserAssistanceReview, value["id"]).expires_at)

    def late(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(browser, "now", lambda: deadline)
        return result

    monkeypatch.setattr(admission, "admit", late)
    with pytest.raises(HTTPException):
        approve(owned, value)
    owned.db.rollback()
    assert owned.db.query(CreditReservation).count() == owned.db.query(CreditExecutionHold).count() == 0
    assert owned.db.get(CreditServiceQuote, value["billing"]["quote_id"]).accepted_reservation_id is None
    assert balance(owned) == 40 and owned.audit()["credits"] == 100


@pytest.mark.parametrize("flag", [None, "BROWSER_ASSISTANCE_ENABLED", "BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED",
    "BROWSER_ASSISTANCE_ATTACHMENT_ENABLED", "BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED",
    "BROWSER_ASSISTANCE_SUBMIT_ENABLED", "BROWSER_SUBMIT_NATIVE_ENABLED", "UNIFIED_BROWSER_SUBMISSION_ENABLED"])
def test_availability_is_only_advisory_and_every_product_gate_remains_required(owned, monkeypatch, flag):
    from backend.app.domains.credits.availability import route_available

    configured_runtime = configure(owned, monkeypatch)
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_ENABLED", "true")
    if flag:
        monkeypatch.setenv(flag, "false")
    assert route_available(configured_runtime.routes[0], 1) is (flag is None)
    assert owned.db.query(CreditReservation).count() == owned.audit()["native_calls"] == 0
