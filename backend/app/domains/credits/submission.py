"""Funded protocol-5 browser submission; no network, signing or employer dispatch.

The existing native submit controller and employer receipt validators still own
one-shot execution. This adapter owns only immutable quotes and unified money.
"""
from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import HTTPException

from ..common import payload_fingerprint
from ..employer import admissions
from . import admission, execution, finance, pricing, runtime, service_quotes
from . import submission_contract as contract
from .models import CreditExecutionHold, CreditReservation, CreditServiceQuote

# Actual finite execution/receipt/price sources. A declarative route name cannot
# qualify a different implementation, companion or native receipt validator.
DELIVERY_SOURCES = (
    "backend/app/domains/credits/submission.py",
    "backend/app/domains/credits/submission_contract.py",
    "backend/app/domains/credits/admission.py",
    "backend/app/domains/credits/execution.py",
    "backend/app/domains/credits/service_quotes.py",
    "backend/app/domains/credits/service.py",
    "backend/app/domains/credits/runtime.py",
    "backend/app/domains/credits/finance.py",
    "backend/app/domains/credits/pricing.py",
    "backend/app/domains/credits/availability.py",
    "backend/app/domains/browser_assistance/submissions.py",
    "backend/app/domains/browser_assistance/service.py",
    "backend/app/domains/browser_assistance/hosted_network.py",
    "backend/app/domains/recovery/gcp_submit_contracts.py",
    "backend/app/domains/recovery/browser_submit.py",
    "browser-companion/extension/assistance-runtime.js",
    "browser-companion/extension/assistance-transport.js",
    "browser-companion/extension/razorpay-adapter.js",
    "browser-companion/extension/greenhouse-native-network.js",
    "browser-companion/extension/greenhouse-owned-upload.js",
    "browser-companion/extension/greenhouse-resume-component.js",
    "browser-companion/extension/local-adapter.js",
    "browser-companion/extension/background.js",
    "browser-companion/extension/config.js",
    "browser-companion/extension/device.js",
    "browser-companion/extension/lost-key-recovery.js",
    "browser-companion/extension/manifest.json",
    "browser-companion/extension/pairing-runtime.js",
    "browser-companion/extension/pairing-transport.js",
    "browser-companion/extension/popup.css",
    "browser-companion/extension/popup.html",
    "browser-companion/extension/popup.js",
    "browser-companion/extension/protocol.js",
    "browser-companion/extension/startup-diagnostics.js",
    "browser-companion/extension/transport.js",
    "frontend/components/BrowserAssistanceReview.tsx",
    "frontend/lib/browserAssistanceGateway.ts",
)


def enabled() -> bool:
    return os.getenv("UNIFIED_BROWSER_SUBMISSION_ENABLED", "false").lower() == "true"


def reject() -> HTTPException:
    return HTTPException(409, {"code": "submission_quote_requires_review",
        "message": "Review a current three-credit submission quote. Accepted work is retained; no submission was retried."})


def delivery_contract() -> str:
    root = Path(__file__).resolve().parents[4]
    try:
        sources = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                   for name in DELIVERY_SOURCES}
    except OSError:
        raise finance.FundingUnavailable("Submission source qualification is unavailable") from None
    return contract.digest({"protocol": contract.PROTOCOL, "billing": contract.BILLING_VERSION,
        "receipt": "greenhouse_hosted_browser_https_200_v2", "sources": sources})


def configured(db, user_id, *, now):
    if not enabled():
        raise reject()
    try:
        current = runtime.current_runtime(user_id, db=db, now=now)
        route = current.resolve_route(contract.OPERATION, {})
        required = service_quotes.require_route(route, current.policy, now=now)
        if (route.mode != "native" or route.model_quotes or route.credits != contract.CREDITS
                or route.output_contract_sha256 != delivery_contract()
                or required > contract.MAX_EXECUTION_MINOR):
            raise reject()
        return current, route
    except (finance.FundingUnavailable, pricing.CreditContractError):
        raise reject() from None


def is_unified(app) -> bool:
    snapshot = app.pricing_snapshot or {}
    return (snapshot.get("schema_version") == contract.BILLING_VERSION
            and snapshot.get("billing_currency") == "credits")


def reservation(db, app):
    return (db.query(CreditReservation).filter_by(user_id=app.user_id,
        operation=contract.OPERATION, source_id=app.id).populate_existing()
        .with_for_update().one_or_none())


def price_core(route, policy) -> dict:
    return contract.PriceCore(billing_currency="credits", operation=contract.OPERATION,
        credits=contract.CREDITS, tariff_version=contract.TARIFF_VERSION,
        route_id=route.id, route_version=route.version,
        delivery_contract_sha256=route.output_contract_sha256,
        financial_policy_sha256=payload_fingerprint(policy.model_dump(mode="json"))).model_dump(mode="json")


def prepare(db, app, posting, artifact, *, now):
    """Select a currency before review, without quoting, debiting or dispatching."""
    from ..browser_assistance import service as browser
    from ..employer import service as employer

    configured(db, app.user_id, now=now)
    browser.require(app.batch_id is None and app.reservation_id is None
        and app.charged_credits == 0 and not app.cancel_requested)
    browser.require(reservation(db, app) is None)
    # Even a terminal legacy obligation belongs to its original currency/source.
    # A missing legacy FK must never make a retained legacy hold convertible.
    browser.require(db.query(browser.employer.ServiceCreditReservation.id).filter_by(
        user_id=app.user_id, operation="job_application", source_id=app.id).first() is None)
    browser.require(db.query(browser.employer.EmployerApplicationAttempt.id)
        .filter_by(application_id=app.id).first() is None)
    browser.require(app.application_mode in {"manual", "browser"})
    if not is_unified(app):
        browser.require((app.application_mode == "manual" and app.credit_cost == 0)
            or (app.application_mode == "browser" and app.credit_cost == 20
                and (app.pricing_snapshot or {}).get("unit_price") == 20))
        for old in db.query(browser.Review).filter_by(application_id=app.id).filter(
                browser.Review.review_protocol_version.in_([4, 5]),
                browser.Review.state.in_(["draft", "approved", "running"])):
            browser.require(old.state == "draft")
            old.state, old.payload = "cancelled", {}
        source = db.query(browser.employer.EmployerSource).filter_by(id=posting.source_id).one()
        app.application_mode, app.credit_cost = "browser", contract.CREDITS
        app.approved_at = app.approved_digest = app.approval_expires_at = None
        app.employer_key, app.opening_key = admissions.employer_identity(source), admissions.opening_identity(posting, source)
        app.admission_snapshot = admissions.quote(source, posting, now=now)
        app.pricing_snapshot = {"schema_version": contract.BILLING_VERSION,
            "billing_currency": "credits", "quote_state": "unquoted"}
        app.package_digest = employer._package_digest(app, posting, source, artifact)


def input_snapshot(db, record, app, posting, artifact, payload, review_id, price):
    from ..browser_assistance import submissions
    from ..employer import service as employer
    from ..recovery.pairing_auth import key_fingerprint

    # Use the existing COMPLETE package serializer, excluding only its generated
    # price/quote. The actual source grants, recipient, consent and admission
    # fields stay bound without a quote/package/review hash cycle.
    unpriced = SimpleNamespace(**{key: copy.deepcopy(getattr(app, key)) for key in (
        "job_content_sha256", "form", "resume_choice", "answers", "consents",
        "credit_cost", "application_mode", "admission_snapshot")}, pricing_snapshot=None)
    source = db.query(employer.models.EmployerSource).filter_by(id=posting.source_id).one()
    return contract.SubmissionInput(
        user_id=app.user_id, application_id=app.id, review_id=review_id,
        device_id=record.id, device_key_sha256=key_fingerprint(record.public_key),
        credential_revision_sha256=contract.digest(submissions.credential_context(db, app.user_id).model_dump(mode="json")),
        opening_key=app.opening_key, employer_key=app.employer_key,
        job_content_sha256=posting.content_sha256, form_sha256=contract.digest(app.form),
        package_inputs_sha256=employer._package_digest(unpriced, posting, source, artifact),
        target_url=posting.apply_url, resume_id=app.resume_id, resume_choice=app.resume_choice,
        resume_version_id=app.resume_version_id, artifact_id=artifact.id,
        artifact_sha256=artifact.sha256, artifact_generation=artifact.gcs_generation,
        reviewed_answers_sha256=payload["reviewed_answers_sha256"],
        upload_evidence_sha256=contract.digest(payload["upload"]),
        expected_application_sha256=payload["expected_application_sha256"],
        native_context_sha256=contract.digest(payload["native_context"]),
        context_digest=payload["context_digest"], document_id=payload["document_id"], price=price)


def quote_billing(saved) -> dict:
    route = service_quotes.QualifiedRoute.model_validate(saved.snapshot["route"])
    from ..browser_assistance.service import _ms

    return contract.SubmissionBilling(**price_core(route, finance.FinancialPolicy.model_validate(saved.snapshot["policy"])),
        schema_version=contract.BILLING_VERSION, quote_id=saved.id,
        input_fingerprint=saved.input_fingerprint, quote_contract_sha256=saved.contract_fingerprint,
        quote_expires_at_ms=_ms(saved.expires_at)).model_dump(mode="json")


def billing_for_review(db, review):
    saved = db.query(CreditServiceQuote).filter_by(user_id=review.user_id,
        operation=contract.OPERATION, idempotency_key="browser-submit-quote:" + review.id).one_or_none()
    if saved is None:
        raise reject()
    return quote_billing(saved)


def issue(db, record, app, posting, artifact, payload, *, review_id, now):
    from ..browser_assistance import service as browser
    from ..employer import service as employer

    current, route = configured(db, app.user_id, now=now)
    for key, value in {"artifact_bytes": artifact.size_bytes, "answer_count": len(payload["answers"])}.items():
        browser.require(type(route.supported_limits.get(key)) is int
            and value <= route.supported_limits[key])
    browser.require(is_unified(app) and reservation(db, app) is None)
    browser.require(payload.get("receipt_contract") == "greenhouse_hosted_browser_https_200_v2"
        and "native_upload" in payload.get("upload", {}) and app.reservation_id is None)
    # An accepted review cannot be replaced; unaccepted quotes remain immutable
    # history even though the stale display is cancelled and erased normally.
    for old in db.query(browser.Review).filter_by(application_id=app.id,
            review_protocol_version=contract.PROTOCOL).filter(browser.Review.state.in_(["draft", "approved", "running"])):
        browser.require(old.state == "draft")
        old.state, old.payload = "cancelled", {}
    source = db.query(browser.employer.EmployerSource).filter_by(id=posting.source_id).one()
    app.admission_snapshot = admissions.quote(source, posting, now=now)
    payload = {**payload, "admission": copy.deepcopy(app.admission_snapshot)}
    snapshot = input_snapshot(db, record, app, posting, artifact, payload, review_id, price_core(route, current.policy))
    try:
        saved = service_quotes.issue_service_quote(db, user_id=app.user_id,
            input_fingerprint=contract.input_fingerprint(snapshot),
            idempotency_key="browser-submit-quote:" + review_id, route=route,
            policy=current.policy, now=now)
    except (finance.FundingUnavailable, pricing.CreditContractError):
        raise reject() from None
    billing = quote_billing(saved)
    app.pricing_snapshot = copy.deepcopy(billing)
    app.package_digest = employer._package_digest(app, posting, source, artifact)
    result = {**payload, "review_protocol_version": contract.PROTOCOL,
        "fee_credits": contract.CREDITS, "pricing": copy.deepcopy(billing),
        "billing": copy.deepcopy(billing), "package_digest": app.package_digest,
        "disclosures": [
            "This separately approves one final Submit application click for the exact candidate, company, job, answers and saved resume shown here.",
            "Three unified credits are reserved only after you accept this exact service quote. They are charged only after the complete independently verified employer receipt.",
            "Cancellation before a possible send releases the credits once. An uncertain send keeps the hold for reconciliation and is never automatically sent again.",
            *payload["disclosures"][-2:]]}
    browser.require(type(route.supported_limits.get("review_bytes")) is int
        and len(contract.canonical(result)) <= route.supported_limits["review_bytes"])
    return result


def accept(db, record, review, app, posting, artifact, quote_id, *, now):
    from ..browser_assistance import service as browser

    current, route = configured(db, app.user_id, now=now)
    browser.require(app.reservation_id is None and app.credit_cost == contract.CREDITS)
    billing = contract.SubmissionBilling.model_validate(review.payload["billing"]).model_dump(mode="json")
    browser.require(quote_id == billing["quote_id"] and app.pricing_snapshot == billing
        and review.payload["pricing"] == billing and review.payload["fee_credits"] == contract.CREDITS
        and review.payload["artifact"] == browser.attachments.descriptor(app, artifact)
        and browser.digest(review.payload) == review.review_digest)
    saved = db.query(CreditServiceQuote).filter_by(id=quote_id, user_id=app.user_id,
        operation=contract.OPERATION).populate_existing().with_for_update().one_or_none()
    browser.require(saved is not None and quote_billing(saved) == billing)
    snapshot = input_snapshot(db, record, app, posting, artifact, review.payload, review.id, price_core(route, current.policy))
    browser.require(contract.input_fingerprint(snapshot) == billing["input_fingerprint"])
    try:
        hold = admission.admit(db, user_id=app.user_id, quote_id=quote_id,
            input_fingerprint=billing["input_fingerprint"], idempotency_key="browser-submit:" + app.id,
            source_id=app.id, route=route, policy=current.policy, budgets=current.budgets,
            inline=True, now=now)
    except (finance.FundingUnavailable, pricing.CreditContractError):
        raise reject() from None
    final = browser.now()
    browser.require(now <= final < pricing.aware(saved.expires_at)
        and final < browser._aware(review.expires_at)
        and final < route.qualified_until and final < current.policy.expires_at
        and final < current.budgets.expires_at)
    hold.snapshot = {**hold.snapshot, "submission": {
        "schema_version": contract.BILLING_VERSION, "quote_id": saved.id,
        "billing_sha256": contract.billing_digest(contract.SubmissionBilling.model_validate(billing)),
        "billing_currency": "credits", "credits": contract.CREDITS,
        "funding_currency": contract.FUNDING_CURRENCY}}
    db.flush([hold])  # Subsequent fresh locked reads must observe this exact binding.
    app.approved_at = final  # Durable currency history; not a native action grant.
    return hold


def held(db, app, *, pending=False):
    """Exact existing-table join; never put unified IDs in the legacy FK."""
    from ..browser_assistance import service as browser

    row = reservation(db, app)
    browser.require(row is not None and is_unified(app) and app.reservation_id is None
        and row.reserved_credits == contract.CREDITS)
    hold = db.query(CreditExecutionHold).filter_by(user_id=app.user_id,
        reservation_id=row.id).populate_existing().with_for_update().one_or_none()
    saved = db.query(CreditServiceQuote).filter_by(user_id=app.user_id,
        accepted_reservation_id=row.id, operation=contract.OPERATION).one_or_none()
    browser.require(hold is not None and saved is not None and quote_billing(saved) == app.pricing_snapshot
        and saved.input_fingerprint == row.input_fingerprint
        and row.quote_snapshot.get("service_quote_id") == saved.id
        and hold.snapshot.get("execution_mode") == "inline"
        and hold.snapshot.get("route") == saved.snapshot["route"]
        and hold.snapshot.get("policy") == saved.snapshot["policy"]
        and hold.snapshot.get("submission") == {
            "schema_version": contract.BILLING_VERSION, "quote_id": saved.id,
            "billing_sha256": contract.digest(app.pricing_snapshot),
            "billing_currency": "credits", "credits": contract.CREDITS,
            "funding_currency": contract.FUNDING_CURRENCY})
    if pending:
        browser.require(row.state == "reserved" and hold.delivery_state == "pending")
    return row, hold


def settle(db, app, *, delivered, now):
    _, hold = held(db, app)
    execution.settle_delivery(db, user_id=app.user_id, hold_id=hold.id, delivered=delivered, now=now)
    # No fabricated zero native cost: its original exposure remains reserved.
    return reservation(db, app)


def require_launch(db, app, *, now):
    current, route = configured(db, app.user_id, now=now)
    row, hold = held(db, app, pending=True)
    if (hold.snapshot["route"] != route.model_dump(mode="json")
            or hold.snapshot["policy"] != current.policy.model_dump(mode="json")):
        raise reject()
    return row


def require_editable(db, app):
    if reservation(db, app) is not None:
        raise reject()  # A terminal reservation still requires a new application ID.


def reset_unquoted(db, app, posting, source):
    from ..browser_assistance import service as browser

    require_editable(db, app)
    app.employer_key, app.opening_key = admissions.employer_identity(source), admissions.opening_identity(posting, source)
    app.admission_snapshot = admissions.quote(source, posting)
    app.pricing_snapshot = {"schema_version": contract.BILLING_VERSION,
        "billing_currency": "credits", "quote_state": "unquoted"}
    for old in db.query(browser.Review).filter_by(application_id=app.id, review_protocol_version=5).filter(
            browser.Review.state.in_(["draft", "approved", "running"])):
        browser.require(old.state == "draft")
        old.state, old.payload = "cancelled", {}


def cancel_application(db, app, *, now):
    from ..browser_assistance import service as browser

    claims = db.query(browser.RequestRecord.id).filter(browser.RequestRecord.review_id.in_(
        db.query(browser.Review.id).filter_by(application_id=app.id)),
        browser.RequestRecord.operation == "submit").first()
    if claims is not None or app.status in {"submitting", "unknown"}:
        app.status = "unknown"
        app.cancel_requested = True
        admissions.finish(db, app, "unknown")
        return
    if reservation(db, app) is not None:
        settle(db, app, delivered=False, now=now)
    app.status, app.active_key, app.cancel_requested = "cancelled", None, True
    app.cancelled_at, app.approved_digest = now, None
    admissions.finish(db, app, "cancelled", proven_not_submitted=True)


def erase_unstarted(db, user_id):
    """Before owner detachment; never refund a possible-send application."""
    from ..employer.models import EmployerApplication
    from .service import _stamp

    for app in db.query(EmployerApplication).filter_by(user_id=user_id).order_by(
            EmployerApplication.id).with_for_update().all():
        if is_unified(app) and app.status not in {"submitting", "unknown", "confirmed"}:
            cancel_application(db, app, now=_stamp(None))
