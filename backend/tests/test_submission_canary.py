"""Owned SQL/crypto canary fixtures; no employer, provider or real rollout proof."""
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.app import models as core
from backend.app.domains.browser_assistance import models as browser_models
from backend.app.domains.browser_assistance import service as browser
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.credits import canary, finance, service, submission
from backend.app.domains.credits.models import (
    CreditExecutionHold,
    CreditReservation,
    CreditServiceQuote,
)
from backend.app.domains.employer import models as employer
from backend.tests.browser_submission_unified_fixture import approve, configure, review
from backend.tests.browser_submit_owned import OwnedSubmit
from backend.tests.test_canary_admission import baseline, prepare
from fastapi import HTTPException


@pytest.fixture
def realm(monkeypatch):
    owned = OwnedSubmit(monkeypatch)
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "true")
    config = configure(owned, monkeypatch)
    now = browser.now()
    config, audience, digest = prepare(owned.db, monkeypatch, now=now, config=config)
    canary.choose(owned.db, user_id=1, review_id=audience.id, review_sha256=digest,
        notice_sha256=audience.notice_sha256, choice="opt_in", idempotency_key="owned-submission-opt-in",
        expected_sequence=0, runtime=config, now=now)
    owned.db.commit()
    try:
        yield owned, config, audience, digest, now
    finally:
        owned.close()


def real_baseline(owned, *, count=19):
    baseline(owned.db, count=count)
    owned.db.query(core.AnalysisRun).update({core.AnalysisRun.created_at: browser.now() - timedelta(hours=2)})
    owned.db.commit()


def accepted_audits(owned):
    return owned.db.query(core.AdminAuditEvent).filter_by(action=canary.ACCEPT_ACTION).all()


def test_actual_quote_approval_binds_canary_to_current_quote_not_application_creation(realm):
    owned, _, audience, _, _ = realm
    real_baseline(owned)
    value = review(owned)
    assert value["billing"]["input_fingerprint"] != owned.app.input_fingerprint
    approve(owned, value)
    row, hold = submission.held(owned.db, owned.app)
    proof = hold.snapshot["canary"]
    assert proof["source_kind"] == "application" and proof["family"] == "submission"
    assert proof["source_id"] == owned.app.id and proof["quote_id"] == value["billing"]["quote_id"]
    assert proof["input_fingerprint"] == row.input_fingerprint == value["billing"]["input_fingerprint"]
    assert proof["business_key"] == canary._application_key(1, owned.app.opening_key)[2]
    assert (proof["canary_acceptances"], proof["eligible_business_acceptances"]) == (1, 20)
    assert service.wallet(owned.db, user_id=1).available_credits == 37
    assert owned.audit()["credits"] == 100 and owned.audit()["native_calls"] == 0
    assert owned.app.reservation_id is None and len(accepted_audits(owned)) == 1
    assert canary._business_keys(owned.db, audience, now=browser.now(), exclude=("application", "other")) == (
        {(40, "skill_roi", payload_fingerprint(number)) for number in range(19)}
        | {canary._application_key(1, owned.app.opening_key)})
    canary.require_accepted_hold(owned.db, hold)
    # Exact accepted work survives an explicit stop and missing runtime pointer.
    canary.choose(owned.db, user_id=1, review_id=audience.id,
        review_sha256=proof["review_sha256"], notice_sha256=audience.notice_sha256,
        choice="revoke", idempotency_key="owned-submission-stop", expected_sequence=1,
        runtime=None, now=browser.now())
    canary.require_accepted_hold(owned.db, hold)


@pytest.mark.parametrize("count", [0, 18])
def test_drafts_requotes_and_low_real_traffic_never_open_canary_slot(realm, count):
    owned, _, _, _, _ = realm
    real_baseline(owned, count=count)
    first = review(owned)
    second = review(owned)
    assert first["billing"]["quote_id"] != second["billing"]["quote_id"]
    with pytest.raises(HTTPException):
        approve(owned, second)
    owned.db.rollback()
    assert len(accepted_audits(owned)) == 0
    assert owned.db.query(CreditReservation).count() == owned.db.query(CreditExecutionHold).count() == 0
    assert service.wallet(owned.db, user_id=1).available_credits == 40


@pytest.mark.parametrize("change", ["old_fingerprint", "foreign_quote", "expired_quote", "quote_operation",
    "cancelled_review", "billing_drift", "review_digest", "wrong_application"])
def test_application_canary_refuses_inexact_quote_review_and_owner_binding(realm, change):
    owned, config, _, _, _ = realm
    real_baseline(owned)
    value = review(owned)
    quote = owned.db.get(CreditServiceQuote, value["billing"]["quote_id"])
    candidate = owned.db.get(browser_models.BrowserAssistanceReview, value["id"])
    fingerprint = quote.input_fingerprint
    if change == "old_fingerprint":
        fingerprint = owned.app.input_fingerprint
    elif change == "foreign_quote":
        quote.user_id = 2
    elif change == "expired_quote":
        quote.expires_at = browser.now()
    elif change == "quote_operation":
        quote.operation = "resume_tailor"
    elif change == "cancelled_review":
        candidate.state = "cancelled"
    elif change == "billing_drift":
        owned.app.pricing_snapshot = {**owned.app.pricing_snapshot, "credits": 20}
    elif change == "review_digest":
        candidate.review_digest = "0" * 64
    else:
        legacy_accepted(owned, identity="different-application", opening=payload_fingerprint("other"), paid=False)
        candidate.application_id = "different-application"
    owned.db.flush()
    with pytest.raises(finance.FundingUnavailable):
        canary.accept(owned.db, user_id=1, runtime=config, operation="verified_submission",
            source_id=owned.app.id, input_fingerprint=fingerprint, now=browser.now())
    owned.db.rollback()
    assert not accepted_audits(owned) and owned.db.query(CreditReservation).count() == 0


def legacy_accepted(owned, *, identity, opening, approved=True, paid=True):
    values = {column.name: deepcopy(getattr(owned.app, column.name))
        for column in employer.EmployerApplication.__table__.columns}
    values.update(id=identity, idempotency_key=identity, active_key=None,
        application_mode="browser" if paid else "manual", credit_cost=20 if paid else 0,
        pricing_snapshot={"unit_price": 20} if paid else None,
        opening_key=opening, approved_at=browser.now() - timedelta(hours=1) if approved else None,
        approved_digest=owned.app.package_digest, reservation_id=None)
    if paid:
        ledger = employer.ServiceCreditReservation(id="legacy-" + identity, user_id=1,
            operation="job_application", source_id=identity, unit_price=20,
            requested_count=1, reserved_amount=20, pricing_version="retained-legacy-v1")
        owned.db.add(ledger)
        owned.db.flush()
        values["reservation_id"] = ledger.id
    owned.db.add(employer.EmployerApplication(**values))
    owned.db.flush()
    if paid and approved:
        owned.db.add(browser_models.BrowserAssistanceReview(id=identity, user_id=1,
            application_id=identity, device_id=owned.device_id, package_digest=values["package_digest"],
            artifact_sha256=owned.artifact_sha, context_digest="e" * 64, review_digest="d" * 64,
            review_protocol_version=4, payload={}, state="cancelled", approved_at=browser.now() - timedelta(hours=1),
            expires_at=browser.now() + timedelta(minutes=1)))
        owned.db.flush()


def test_same_opening_repeated_applications_do_not_inflate_business_denominator(realm):
    owned, _, audience, _, _ = realm
    value = review(owned)
    for number in range(19):
        legacy_accepted(owned, identity=f"duplicate-{number}", opening=owned.app.opening_key)
    keys = canary._business_keys(owned.db, audience, now=browser.now(), exclude=("application", owned.app.id))
    assert len(keys) == 1
    with pytest.raises(HTTPException):
        approve(owned, value)
    owned.db.rollback()
    assert not accepted_audits(owned)


def test_genuine_distinct_legacy_acceptances_count_but_manual_and_drafts_do_not(realm):
    owned, _, audience, _, _ = realm
    value = review(owned)
    for number in range(19):
        legacy_accepted(owned, identity=f"distinct-{number}", opening=payload_fingerprint(number))
        legacy_accepted(owned, identity=f"manual-{number}", opening=payload_fingerprint(["manual", number]), paid=False)
        legacy_accepted(owned, identity=f"draft-{number}", opening=payload_fingerprint(["draft", number]), approved=False)
    owned.db.commit()
    keys = canary._business_keys(owned.db, audience, now=browser.now(), exclude=("application", owned.app.id))
    assert len(keys) == 19
    approve(owned, value)
    assert submission.held(owned.db, owned.app)[1].snapshot["canary"]["eligible_business_acceptances"] == 20


@pytest.mark.parametrize("change", ["kind", "quote", "fingerprint"])
def test_accepted_submission_hold_rejects_cross_kind_quote_and_fingerprint_proofs(realm, change):
    owned, _, _, _, _ = realm
    real_baseline(owned)
    approve(owned, review(owned))
    row, hold = submission.held(owned.db, owned.app)
    if change == "fingerprint":
        row.input_fingerprint = "f" * 64
    else:
        proof = deepcopy(hold.snapshot["canary"])
        proof["source_kind" if change == "kind" else "quote_id"] = "other"
        # Even a forged copied snapshot must match immutable audit provenance.
        hold = SimpleNamespace(snapshot={"canary": proof}, user_id=1, reservation_id=row.id)
    with pytest.raises(finance.FundingUnavailable):
        canary.require_accepted_hold(owned.db, hold)
