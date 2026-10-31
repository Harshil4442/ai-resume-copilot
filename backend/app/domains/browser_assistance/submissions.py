"""One-shot reviewed submit; only exact native employer acceptance settles credits."""

from __future__ import annotations

import copy
import os
from uuid import uuid4

FEE = 20


def enabled() -> bool:
    return os.getenv("BROWSER_ASSISTANCE_SUBMIT_ENABLED", "false").lower() == "true"


def authority():
    from ..recovery.browser_submit import production_submit_authority

    return production_submit_authority()


def prepare(db, app, posting, artifact, *, version=4):
    from ..credits import runtime
    from ..credits import submission as unified
    from ..employer import service as employer_service
    from . import service as s

    s.require(enabled() and s.typed_controls_enabled(), 503)
    # Configuration is structural only. No native/provider IO while SQL locks exist.
    authority()
    if version == 5:
        return unified.prepare(db, app, posting, artifact, now=s.now())
    s.require(version == 4 and not runtime.requires_unified_billing(db, app.user_id))
    s.require(app.application_mode in {"manual", "browser"} and app.batch_id is None)
    if app.application_mode == "manual":
        s.require(app.credit_cost == 0 and app.charged_credits == 0 and app.reservation_id is None)
        s.require(
            db.query(s.employer.EmployerApplicationAttempt.id)
            .filter_by(application_id=app.id)
            .first()
            is None
        )
        source = db.query(s.employer.EmployerSource).filter_by(id=posting.source_id).one()
        price = employer_service._reviewed_prices()
        s.require(price["apply_credits_per_job"] == FEE)
        app.application_mode, app.credit_cost = "browser", FEE
        employer_service._quote_application(app, posting, source)
        app.package_digest = employer_service._package_digest(app, posting, source, artifact)
        for review in (
            db.query(s.Review)
            .filter_by(application_id=app.id)
            .filter(s.Review.state.in_(["draft", "approved", "running"]))
        ):
            review.state, review.payload = "cancelled", {}
    s.require(app.credit_cost == FEE and app.pricing_snapshot["unit_price"] == FEE)


def review_payload(db, record, inspection, app, posting, artifact, mapping, *, review_id=None):
    from ..credits import runtime
    from ..credits import submission as unified
    from . import attachments
    from . import hosted_network as network
    from . import service as s

    version = inspection.get("review_protocol_version")
    fee = 3 if version == 5 else FEE
    s.require(type(version) is int and version in {4, 5})
    s.require(enabled() and app.application_mode == "browser" and app.credit_cost == fee, 503)
    s.require(version == 5 or not runtime.requires_unified_billing(db, app.user_id))
    s.require(app.reservation_id is None and app.receipt is None)
    s.require(str(posting.external_id) == posting.apply_url.rsplit("/", 1)[-1])
    s.require(
        db.query(s.employer.EmployerApplicationAttempt.id).filter_by(application_id=app.id).first()
        is None
    )
    values = inspection["assisted_values"]
    answers = [
        {**field, "value": values[field["id"]]} for field in mapping if field["type"] != "button"
    ]
    for field in answers:
        s.require(not field["required"] or bool(field["value"]))
        if field["type"] == "combobox":
            s.require(field["value"] in {option["value"] for option in field["options"]})
        if field["type"] == "checkbox":
            s.require(field["value"] in {"true", "false"})
    s.require(inspection.get("ready") is True and inspection.get("manual_fields") == [])
    upload = inspection.get("upload")
    s.require(
        isinstance(upload, dict) and set(upload) in (
            {"review_id", "action_id", "filename", "status"},
            {"review_id", "action_id", "filename", "status", "native_upload", "upload_plan_sha256"},
        )
    )
    s.require(
        upload["status"] == "portal_reports_uploaded" and upload["filename"] == artifact.filename
    )
    prior = (
        db.query(s.Review)
        .filter_by(
            id=upload["review_id"],
            device_id=record.id,
            user_id=record.user_id,
            application_id=app.id,
            review_protocol_version=3,
        )
        .first()
    )
    action = (
        db.query(s.RequestRecord)
        .filter_by(
            id=upload["action_id"],
            review_id=upload["review_id"],
            device_id=record.id,
            operation="attachment",
        )
        .first()
    )
    s.require(
        prior is not None
        and prior.artifact_sha256 == artifact.sha256
        and action is not None
        and action.response.get("delivery_claimed") is True
        and action.response.get("selection_reported") is True
        and action.response.get("selection_document_id") == inspection["document_id"]
        and action.response.get("selection_target_url") == posting.apply_url
        and action.response.get("selection_artifact_sha256") == artifact.sha256
    )
    continuity = inspection.get("upload_continuity")
    s.require(
        continuity
        == {
            "review_id": upload["review_id"],
            "action_id": upload["action_id"],
            "artifact_sha256": artifact.sha256,
            "document_id": inspection["document_id"],
            "target_url": posting.apply_url,
        }
    )
    descriptor = attachments.descriptor(app, artifact)
    payload = {
        "review_protocol_version": version,
        "application_id": app.id,
        "candidate_id": record.user_id,
        "package_digest": app.package_digest,
        "target_url": posting.apply_url,
        "company": posting.employer,
        "job": {
            "posting_id": posting.id,
            "external_id": posting.external_id,
            "title": posting.title,
            "content_sha256": posting.content_sha256,
        },
        "artifact": descriptor,
        "field_mapping": mapping,
        "assisted_values": values,
        "answers": answers,
        "reviewed_answers": {field["id"]: field["value"] for field in answers},
        "fields": [],
        "manual_fields": [],
        "context_digest": inspection["context_digest"],
        "document_id": inspection["document_id"],
        "frame_id": 0,
        "upload": upload,
        "upload_continuity": continuity,
        "submit_control": {
            "form_id": "application-form",
            "type": "submit",
            "label": "Submit application",
        },
        "allowed_actions": ["submit"],
        "fee_credits": fee,
        "pricing": copy.deepcopy(app.pricing_snapshot),
        "admission": copy.deepcopy(app.admission_snapshot),
        "disclosures": [
            "This separately approves one final Submit application click for the exact candidate, company, job, answers and saved resume shown here.",
            "Submission can disclose all reviewed information immediately. Complete required employer terms, login and CAPTCHA yourself. Cancellation cannot undo an already authorized click.",
            "20 application credits will be reserved when you approve. They are charged only after an independently verified complete employer receipt. An uncertain send keeps the hold for reconciliation; it is never automatically sent again.",
            "The portal reports the uploaded filename after the separately approved exact-file selection. This is browser-observed upload state, not an independently authenticated storage or employer receipt.",
            "A thank-you page, HTTP success or browser report alone cannot verify receipt or settle the 20 credits.",
        ],
    }
    payload["reviewed_answers_sha256"] = s.digest(
        {
            "context_digest": payload["context_digest"],
            "reviewed_answers": payload["reviewed_answers"],
        }
    )
    if "native_upload" in upload:
        s.require(network.enabled())
        s.require(action.response.get("native_upload") == upload["native_upload"])
        s.require(action.response.get("upload_plan_sha256") == upload["upload_plan_sha256"])
        s.require(upload["native_upload"]["artifact_sha256"] == artifact.sha256)
        s.require(upload["native_upload"]["document_id"] == inspection["document_id"])
        s.require(upload["native_upload"]["target_url"] == posting.apply_url)
        context = network.native_context(inspection.get("native_context"))
        s.require(context["tab_id"] == upload["native_upload"]["tab_id"])
        expected = network.expected_application(app, mapping, payload["reviewed_answers"], context, upload["native_upload"])
        payload.update(receipt_contract=network.RECEIPT_VERSION, native_context=context,
                       expected_application=expected, expected_application_sha256=s.digest(expected))
        payload["disclosures"][-2:] = [
            "The exact sealed resume was sent once as the companion's own immutable buffer to the separately approved storage recipient. Native Chrome events cross-check available raw bytes or approved nonfile form fields and the direct HTTPS acknowledgement; parsed form events do not expose native raw file bytes.",
            "Only a complete direct employer HTTPS 200 exchange for the exact reviewed application, observed under the browser's standard HTTPS validation, verifies acceptance. A thank-you page, HTTP 202, missing native network evidence or filename alone cannot settle the credits.",
        ]
    else:
        s.require("native_context" not in inspection)
    if version == 5:
        s.require(isinstance(review_id, str))
        return unified.issue(db, record, app, posting, artifact, payload,
                             review_id=review_id, now=s.now())
    return payload


def credential_context(db, user_id):
    from ... import models as core
    from ..candidate_accounts.service import revision_from_row
    from ..recovery.gcp_submit_contracts import SubmitContext
    from . import service as s

    user = db.query(core.User).filter_by(id=user_id).one()
    row = db.query(core.CandidatePasswordAccount).filter_by(user_id=user_id).first()
    s.require(row is not None, 409)
    revision = revision_from_row(row, user.password_hash)
    return SubmitContext(**revision.model_dump(mode="json"))


def approve(db, record, review, *, accepted_quote_id=None):
    from ..credits import runtime
    from ..credits import submission as unified
    from ..employer import admissions, credits
    from . import service as s

    version = review.review_protocol_version
    s.require(type(version) is int and version in {4, 5})
    fee = 3 if version == 5 else FEE
    s.require(enabled() and review.payload.get("fee_credits") == fee, 503)
    s.require(version == 5 or (accepted_quote_id is None
        and not runtime.requires_unified_billing(db, review.user_id)))
    authority()
    context = credential_context(db, review.user_id)
    app, posting, artifact = s._application(db, review, review.user_id)
    s.require(
        app.application_mode == "browser" and app.credit_cost == fee and app.reservation_id is None
    )
    s.require(
        app.pricing_snapshot == review.payload["pricing"]
        and app.admission_snapshot == review.payload["admission"]
    )
    source = db.query(s.employer.EmployerSource).filter_by(id=posting.source_id).one()
    admissions.reserve(db, app, source)
    if version == 5:
        unified.accept(db, record, review, app, posting, artifact, accepted_quote_id, now=s.now())
    else:
        reservation = credits.reserve(
            db, user_id=app.user_id, operation="job_application", source_id=app.id,
            unit_price=FEE, count=1, pricing_version=app.pricing_snapshot["pricing_version"])
        app.reservation_id = reservation.id
    db.add(
        s.RequestRecord(
            id=str(uuid4()),
            device_id=record.id,
            request_id=str(uuid4()),
            operation="submit_approval",
            request_sha256=review.review_digest,
            review_id=review.id,
            state="completed",
            response={"context": context.model_dump(mode="json")},
        )
    )


def _binding(db, record, review, app, artifact, issued, deadline):
    from ..recovery.gcp_submit_contracts import SubmitBinding
    from ..recovery.pairing_auth import key_fingerprint
    from . import service as s

    admission = db.query(s.employer.EmployerAdmission).filter_by(application_id=app.id).one()
    generation = artifact.gcs_generation
    s.require(
        isinstance(generation, str) and generation.isdecimal() and not generation.startswith("0")
    )
    revision = os.getenv("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION", "")
    return SubmitBinding(
        action="submit",
        candidate_id=app.user_id,
        device_id=record.id,
        device_key_sha256=key_fingerprint(record.public_key),
        device_key_generation=1,
        executor_revision=revision,
        review_id=review.id,
        review_digest=review.review_digest,
        application_id=app.id,
        target_url=review.payload["target_url"],
        employer_key=app.employer_key,
        tenant_id=s.TENANT,
        opening_key=app.opening_key,
        artifact_id=artifact.id,
        artifact_sha256=artifact.sha256,
        artifact_generation=generation,
        package_digest=app.package_digest,
        policy_sha256=s.digest(review.payload["admission"]),
        pricing_sha256=s.digest(review.payload["pricing"]),
        admission_id=admission.id,
        admission_digest=s.digest(
            {"id": admission.id, "policy": admission.policy_snapshot, "application_id": app.id}
        ),
        upload_evidence_sha256=s.digest(review.payload["upload"]),
        context_digest=review.context_digest,
        document_id=review.payload["document_id"],
        approved_at_ms=s._ms(review.approved_at),
        review_expires_at_ms=s._ms(review.expires_at),
        issued_at_ms=s._ms(issued),
        deadline_ms=s._ms(deadline),
    )


def begin(db, record, review, request, request_sha):
    from ..credits import submission as unified
    from ..employer import admissions
    from ..recovery.gcp_submit_contracts import SubmitContext
    from . import service as s

    body = request.body
    s.require(
        s.enabled()
        and s.typed_controls_enabled()
        and enabled()
        and s._aware(review.expires_at) > s.now()
        and review.state == "approved"
        and review.approved_at is not None
        and review.next_index == 0,
        503,
    )
    s.require(
        set(body) == {"review_id", "review_digest", "index", "context_digest", "document_id"}
        and type(body["index"]) is int
        and body["index"] == 0
    )
    s.require(
        body["review_digest"] == review.review_digest
        and body["context_digest"] == review.context_digest
        and body["document_id"] == review.payload["document_id"]
    )
    s.require(
        db.query(s.RequestRecord.id).filter_by(review_id=review.id, field_index=0).first() is None
    )
    app, posting, artifact = s._application(db, review, record.user_id)
    s.require(app.application_mode == "browser")
    if review.review_protocol_version == 5:
        unified.require_launch(db, app, now=s.now())
        s.require(app.credit_cost == 3 and review.payload["billing"] == app.pricing_snapshot)
    else:
        s.require(app.reservation_id is not None and app.credit_cost == FEE)
        reservation = (db.query(s.employer.ServiceCreditReservation).filter_by(
            id=app.reservation_id, user_id=record.user_id, source_id=app.id,
            operation="job_application", state="reserved", unit_price=FEE,
            reserved_amount=FEE).with_for_update().first())
        s.require(reservation is not None)
    saved = (
        db.query(s.RequestRecord).filter_by(review_id=review.id, operation="submit_approval").one()
    )
    context = SubmitContext.model_validate(saved.response["context"])
    s.require(credential_context(db, record.user_id) == context)
    issued = s.now()
    deadline = min(issued + s.timedelta(milliseconds=s.LEASE_MS), s._aware(review.expires_at))
    identity = str(uuid4())
    binding = _binding(db, record, review, app, artifact, issued, deadline)
    source = db.query(s.employer.EmployerSource).filter_by(id=posting.source_id).one()
    admissions.launch(db, app, source)
    attempt = s.employer.EmployerApplicationAttempt(
        id=str(uuid4()),
        application_id=app.id,
        launch_token=identity,
        package_digest=app.package_digest,
        state="submitting",
    )
    db.add(attempt)
    action = s.RequestRecord(
        id=identity,
        device_id=record.id,
        request_id=identity,
        operation="submit",
        review_id=review.id,
        field_index=0,
        state="begun",
        request_sha256=s.digest(binding.model_dump(mode="json")),
        lease_expires_at=deadline,
        response={
            "attempt_id": attempt.id,
            "binding": binding.model_dump(mode="json"),
            "lease_exported": False,
        },
    )
    if review.payload.get("receipt_contract"):
        from . import hosted_network as network
        s.require(network.enabled() and review.payload["receipt_contract"] == network.RECEIPT_VERSION, 503)
        action.response = {**action.response, "hosted_receipt_binding": {
            "receipt_contract": network.RECEIPT_VERSION,
            "native_context": copy.deepcopy(review.payload["native_context"]),
            "expected_application_sha256": review.payload["expected_application_sha256"],
            "upload_evidence_sha256": s.digest(review.payload["upload"]["native_upload"]),
        }}
    db.add(action)
    db.add(
        s.RequestRecord(
            id=str(uuid4()),
            device_id=record.id,
            request_id=str(request.request_id),
            operation="begin",
            request_sha256=request_sha,
            state="completed",
            review_id=review.id,
            response={},
        )
    )
    app.status, review.state = "submitting", "running"
    db.commit()  # Durable possible-send claim precedes independent IO and browser dispatch.
    try:
        gate = authority()
        ack = gate.reserve(context, binding)
        if ack is None:
            return _unknown(db, record.user_id, review.id, identity)
        proof = gate.consume_ack(ack, binding)
    except Exception:
        # The native write may have committed. Neither another begin nor a
        # second native reserve is a safe recovery strategy after this point.
        db.rollback()
        return _unknown(db, record.user_id, review.id, identity)
    try:
        s._owner_lock(db, record.user_id)
        device = (
            db.query(s.Device)
            .filter_by(id=record.id, user_id=record.user_id, state="active")
            .populate_existing()
            .with_for_update()
            .first()
        )
        current = (
            db.query(s.Review)
            .filter_by(id=review.id, device_id=record.id, user_id=record.user_id)
            .populate_existing()
            .with_for_update()
            .one()
        )
        app = (
            db.query(s.employer.EmployerApplication)
            .filter_by(id=app.id, user_id=record.user_id)
            .populate_existing()
            .with_for_update()
            .one()
        )
        action = (
            db.query(s.RequestRecord)
            .filter_by(id=identity)
            .populate_existing()
            .with_for_update()
            .one()
        )
        # No SDK IO or await after the final current SQL authority check through export.
        s.require(
            device is not None
            and enabled()
            and s.enabled()
            and s.typed_controls_enabled()
            and s.now() < deadline
            and s.now() < s._aware(current.expires_at)
            and current.state == "running"
            and current.review_digest == binding.review_digest
            and bool(current.payload)
            and not app.cancel_requested
            and app.status == "submitting"
            and app.package_digest == binding.package_digest
            and not action.response["lease_exported"]
        )
        app, posting, artifact = s._application(db, current, record.user_id, allow_submitting=True)
        s.require(
            credential_context(db, app.user_id) == context
            and current.payload["artifact"] == s.attachments.descriptor(app, artifact)
            and artifact.gcs_generation == binding.artifact_generation
        )
        if current.review_protocol_version == 5:
            unified.require_launch(db, app, now=s.now())
            s.require(current.payload["billing"] == app.pricing_snapshot)
        lease = {
            "protocol_version": 1,
            "review_protocol_version": current.review_protocol_version,
            "action_id": identity,
            "review_id": review.id,
            "device_id": record.id,
            "application_id": app.id,
            "package_digest": app.package_digest,
            "artifact_sha256": artifact.sha256,
            "target_url": binding.target_url,
            "context_digest": binding.context_digest,
            "document_id": binding.document_id,
            "frame_id": 0,
            "review_digest": binding.review_digest,
            "reviewed_answers_sha256": current.payload["reviewed_answers_sha256"],
            "field_id": "final-submit",
            "control_type": "submit",
            "allowed_actions": ["submit"],
            "fee_credits": 3 if current.review_protocol_version == 5 else FEE,
            "native_proof_sha256": s.digest(proof),
            "upload": current.payload["upload"],
            "upload_continuity": current.payload["upload_continuity"],
            "submit_control": current.payload["submit_control"],
            "issued_at_ms": binding.issued_at_ms,
            "deadline": binding.deadline_ms,
        }
        if current.review_protocol_version == 5:
            lease["billing"] = copy.deepcopy(current.payload["billing"])
        if current.payload.get("receipt_contract"):
            from . import hosted_network as network
            s.require(network.enabled(), 503)
            lease.update({key: copy.deepcopy(current.payload[key]) for key in (
                "receipt_contract", "expected_application", "expected_application_sha256", "native_context")})
        action.response = {**action.response, "lease_exported": True, "native_proof": proof}
        db.commit()  # A lost lease response cannot re-export or resubmit.
        s.require(s.now() < deadline)
        return {"state": "begun", "lease": s._signature(lease)}
    except Exception:
        db.rollback()
        return _unknown(db, record.user_id, review.id, identity)


def reconciled(db, review, app=None):
    """Browser reports cannot overwrite an independently adjudicated terminal state."""
    from ..credits import submission as unified
    from . import service as s

    app = (
        app
        or db.query(s.employer.EmployerApplication)
        .filter_by(id=review.application_id, user_id=review.user_id)
        .with_for_update()
        .one()
    )
    if review.review_protocol_version == 5:
        if review.package_digest != app.package_digest or unified.reservation(db, app) is None:
            return None
        reservation, hold = unified.held(db, app)
        committed, fee = reservation.committed_credits, 3
        if reservation.state != "settled":
            return None
        s.require(hold.delivery_state in {"delivered", "failed"})
    elif app.reservation_id is None:
        return None
    else:
        reservation = (db.query(s.employer.ServiceCreditReservation)
            .filter_by(id=app.reservation_id, user_id=review.user_id).with_for_update().one())
        if reservation.state != "settled":
            return None
        committed, fee = reservation.committed_amount, FEE
    if (
        app.status == "confirmed"
        and app.receipt
        and app.receipt.get("completion_verified") is True
        and app.charged_credits == fee
        and committed == fee
    ):
        review.state, review.next_index = "filled", 1
        return "confirmed"
    if (
        app.status == "failed"
        and app.error_code == "verified_not_submitted"
        and committed == 0
    ):
        review.state, review.payload = "cancelled", {}
        return "verified_not_submitted"
    return None


def status(db, review):
    from . import service as s

    terminal = reconciled(db, review)
    result = s._review_response(review)
    if review.review_protocol_version == 5:
        from ..credits import submission as unified
        result["billing"] = unified.billing_for_review(db, review)
    if terminal:
        result.update(submission_status=terminal, employer_confirmed=terminal == "confirmed")
    elif (
        db.query(s.RequestRecord.id).filter_by(review_id=review.id, operation="submit").first()
        is None
    ):
        result.update(submission_status="not_launched", employer_confirmed=False)
    return result


def observe(db, record, review, body):
    from . import service as s

    s.require(set(body) in ({"review_id", "action_id", "observation"},
                           {"review_id", "action_id", "observation", "receipt"}))
    action = (
        db.query(s.RequestRecord)
        .filter_by(
            id=body["action_id"], review_id=review.id, device_id=record.id, operation="submit"
        )
        .with_for_update()
        .first()
    )
    s.require(action is not None and action.response.get("lease_exported") is True)
    observation = body["observation"]
    s.require(
        isinstance(observation, dict) and set(observation) == {"status", "url", "document_id"}
    )
    s.require(
        observation["status"]
        in {
            "click_dispatched",
            "confirmation_observed",
            "validation_error",
            "waiting_user",
            "unknown",
        }
    )
    expected = action.response["binding"]["target_url"]
    s.require(
        observation["url"] in {expected, expected + "/confirmation"}
        and isinstance(observation["document_id"], str)
        and 1 <= len(observation["document_id"]) <= 160
    )
    attempt = (
        db.query(s.employer.EmployerApplicationAttempt)
        .filter_by(id=action.response["attempt_id"], application_id=review.application_id)
        .with_for_update()
        .one()
    )
    app = (
        db.query(s.employer.EmployerApplication)
        .filter_by(id=review.application_id, user_id=record.user_id)
        .with_for_update()
        .one()
    )
    if reconciled(db, review, app):
        return status(db, review)
    if "receipt" in body:
        from ..employer import admissions, credits
        from . import hosted_network as network
        # The original immutable action binding survives review TTL/cancellation.
        # This only adjudicates an already captured exchange; it never sends.
        receipt = network.submit_receipt(body["receipt"], review=review, action=action)
        if review.review_protocol_version == 5:
            from ..credits import submission as unified
            unified.held(db, app, pending=True)
        else:
            reservation = db.query(s.employer.ServiceCreditReservation).filter_by(
                id=app.reservation_id, user_id=record.user_id, source_id=app.id,
                operation="job_application").with_for_update().one()
            s.require(reservation.state == "reserved" and reservation.unit_price == FEE
                      and reservation.reserved_amount == FEE and reservation.requested_count == 1)
        app.status, attempt.state, action.state = "confirmed", "confirmed", "filled"
        app.receipt = attempt.receipt = receipt
        attempt.response_status, attempt.completed_at, attempt.error_code = 200, s.now(), None
        app.error_code = app.error_message = None
        if review.review_protocol_version == 5:
            row = unified.settle(db, app, delivered=True, now=s.now())
            app.charged_credits = row.committed_credits
        else:
            credits.settle(db, reservation, completed_count=1, reason="Exact reviewed Greenhouse hosted application accepted over a direct secure native exchange")
            app.charged_credits = reservation.committed_amount
        admissions.finish(db, app, "confirmed")
        action.response = {**action.response, "observation": observation,
                           "employer_confirmed": True, "receipt_sha256": s.digest(receipt)}
        review.state, review.next_index = "filled", 1
        return status(db, review)
    attempt.state, app.status, review.state = "unknown", "unknown", "unknown"
    attempt.completed_at = s.now()
    attempt.error_code = "browser_observation_unverified"
    from ..employer import admissions

    admissions.finish(db, app, "unknown")
    # This JSON is explicitly an observation, never app.receipt/provider receipt.
    action.response = {**action.response, "observation": observation, "employer_confirmed": False}
    app.error_code = "browser_submission_unverified"
    app.error_message = "The browser may have submitted the application. Portal observations cannot verify employer receipt; the accepted credits remain held. No automatic resend is allowed."
    return status(db, review)


def _unknown(db, user_id, review_id, action_id):
    """Persist uncertainty even if no lease was exported; never refund a send claim."""
    from ..employer import admissions
    from . import service as s

    s._owner_lock(db, user_id)
    review = (
        db.query(s.Review)
        .filter_by(id=review_id, user_id=user_id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    action = (
        db.query(s.RequestRecord)
        .filter_by(id=action_id, review_id=review.id, operation="submit")
        .populate_existing()
        .with_for_update()
        .one()
    )
    app = (
        db.query(s.employer.EmployerApplication)
        .filter_by(id=review.application_id, user_id=user_id)
        .populate_existing()
        .with_for_update()
        .one()
    )
    attempt = (
        db.query(s.employer.EmployerApplicationAttempt)
        .filter_by(id=action.response["attempt_id"], application_id=app.id)
        .with_for_update()
        .one()
    )
    if reconciled(db, review, app):
        db.commit()
        return {"state": "unknown"}
    review.state = app.status = attempt.state = "unknown"
    attempt.completed_at = s.now()
    attempt.error_code = "browser_submit_authority_unknown"
    admissions.finish(db, app, "unknown")
    app.error_code = "browser_submit_authority_unknown"
    app.error_message = "Submission authority has an uncertain outcome. No automatic resend is allowed; the accepted application credits remain held for reconciliation."
    db.commit()
    return {"state": "unknown"}


def cancel(db, review, *, expired=False):
    """Refund only before any durable possible-send claim, otherwise retain hold."""
    from ..credits import submission as unified
    from ..employer import admissions, credits
    from . import service as s

    app = (
        db.query(s.employer.EmployerApplication)
        .filter_by(id=review.application_id, user_id=review.user_id)
        .with_for_update()
        .one()
    )
    if review.review_protocol_version == 5 and review.package_digest != app.package_digest:
        # A cancelled/replaced display cannot cancel a newer accepted quote.
        review.state, review.payload = "cancelled", {}
        return
    if reconciled(db, review, app):
        return
    claimed = (
        db.query(s.RequestRecord)
        .filter(
            s.RequestRecord.review_id.in_(db.query(s.Review.id).filter_by(application_id=app.id)),
            s.RequestRecord.operation == "submit",
        )
        .with_for_update()
        .first()
    )
    if claimed is None:
        if review.review_protocol_version == 5 and expired and unified.reservation(db, app) is None:
            # Expiring an unaccepted display consumes no source identity or
            # application attempt. Only a NEW explicit inspect may issue a quote.
            review.state, review.payload = "expired", {}
            return
        if review.review_protocol_version == 5 and unified.reservation(db, app) is not None:
            unified.settle(db, app, delivered=False, now=s.now())
        elif app.reservation_id:
            reservation = (
                db.query(s.employer.ServiceCreditReservation)
                .filter_by(id=app.reservation_id, user_id=review.user_id)
                .with_for_update()
                .one()
            )
            credits.settle(
                db,
                reservation,
                completed_count=0,
                reason="Final submission cancelled before a possible-send claim",
            )
        admissions.finish(db, app, "cancelled", proven_not_submitted=True)
        app.status, app.active_key = "cancelled", None
    else:
        app.status = "unknown"
        admissions.finish(db, app, "unknown")
        attempt = (
            db.query(s.employer.EmployerApplicationAttempt)
            .filter_by(id=claimed.response["attempt_id"], application_id=app.id)
            .with_for_update()
            .one()
        )
        attempt.state = "unknown"
        attempt.error_code = "browser_submit_cancelled_after_claim"
        attempt.completed_at = s.now()
        app.error_code = "browser_submit_cancelled_after_claim"
        app.error_message = "Cancellation stops future actions but cannot undo an authorized click. The accepted credits remain held; inspect and reconcile without resubmitting."
    app.cancel_requested = True
    review.state, review.payload = "cancelled", {}


def upload_provenance(db, record, app, artifact):
    """Small device-observed provenance only; never expose file bytes or a receipt."""
    from . import service as s

    for prior in (
        db.query(s.Review)
        .filter_by(
            device_id=record.id,
            user_id=record.user_id,
            application_id=app.id,
            artifact_sha256=artifact.sha256,
            review_protocol_version=3,
        )
        .order_by(s.Review.created_at.desc())
        .limit(20)
    ):
        action = (
            db.query(s.RequestRecord)
            .filter_by(review_id=prior.id, device_id=record.id, operation="attachment")
            .first()
        )
        if (
            action
            and action.response.get("delivery_claimed") is True
            and action.response.get("selection_reported") is True
        ):
            result = {
                "review_id": prior.id,
                "action_id": action.id,
                "filename": artifact.filename,
                "status": "portal_reports_uploaded",
            }
            if action.response.get("native_upload"):
                result.update(native_upload=copy.deepcopy(action.response["native_upload"]),
                              upload_plan_sha256=action.response["upload_plan_sha256"])
            return result
    return None
