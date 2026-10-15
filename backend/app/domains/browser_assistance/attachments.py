"""One-shot candidate-approved sealed resume delivery, never a blob proxy."""
from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.orm import Session

MAX_BYTES = 10 * 1024 * 1024
MIMES = {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}


def enabled() -> bool:
    return os.getenv("BROWSER_ASSISTANCE_ATTACHMENT_ENABLED", "false").lower() == "true"


@dataclass(frozen=True)
class BinaryDelivery:
    content: bytes
    descriptor: dict


def descriptor(app, artifact) -> dict:
    from . import service as s

    s.require(type(artifact.size_bytes) is int and 0 < artifact.size_bytes <= MAX_BYTES)
    s.require(artifact.media_type in MIMES and re.fullmatch(r"[a-f0-9]{64}", artifact.sha256 or "") is not None)
    s.require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,199}", artifact.filename or "") is not None)
    s.require(app.resume_choice in {"original", "custom", "tailored"} and type(app.resume_id) is int and app.resume_id > 0)
    s.require((app.resume_choice == "tailored") == bool(app.resume_version_id))
    return {"artifact_id": artifact.id, "sha256": artifact.sha256, "filename": artifact.filename,
            "media_type": artifact.media_type, "size_bytes": artifact.size_bytes, "resume_id": app.resume_id,
            "resume_choice": app.resume_choice, "resume_version_id": app.resume_version_id}


def review_payload(inspection, app, posting, artifact, mapping) -> dict:
    from . import hosted_network as network
    from . import service as s

    s.require(enabled(), 503)
    target = inspection.get("attachment")
    s.require(isinstance(target, dict) and set(target) == {"input_id", "expected_empty"}
              and target["input_id"] == "resume" and target["expected_empty"] is True)
    manual = inspection.get("manual_fields", [])
    s.require(isinstance(manual, list) and len(manual) <= 128)
    s.require(all(isinstance(field, dict) and set(field) == {"id", "label", "required"}
                  and isinstance(field["id"], str) and len(field["id"]) <= 160
                  and isinstance(field["label"], str) and len(field["label"]) <= 256
                  and type(field["required"]) is bool for field in manual))
    answers: dict[str, str] = {}
    payload = {"review_protocol_version": 3, "application_id": app.id, "package_digest": app.package_digest,
            "target_url": posting.apply_url, "artifact": descriptor(app, artifact),
            "attachment": {"input_id": "resume", "expected_empty": True},
            "context_digest": inspection["context_digest"], "field_mapping": mapping,
            "document_id": inspection["document_id"], "frame_id": 0, "fields": [],
            "assisted_values": inspection["assisted_values"], "reviewed_answers": answers,
            "reviewed_answers_sha256": s.digest({"context_digest": inspection["context_digest"], "reviewed_answers": answers}),
            "manual_fields": [field for field in manual if field["id"] != "resume"], "allowed_actions": ["attach"], "fee_credits": 0,
            "disclosures": [
                "This approval authorizes delivery of the exact saved sealed resume bytes to this connected browser device and selection into this opening's empty Resume/CV input.",
                "Selecting the file can immediately upload or disclose its complete bytes to the employer before final submission. Earlier disclosure cannot be recalled.",
                "Cancellation or revocation stops future delivery/actions. An action already authorized may finish within its ten-second lease; the lease is never extended.",
                "Only local file selection can be observed here. Employer upload acceptance is unconfirmed; no employer-submission receipt or application fee is created.",
                "Country, employment, unsupported required controls, CAPTCHA/login and final submission remain yours."]}
    if "upload_plan" in inspection:
        payload["upload_plan"] = network.upload_plan(inspection["upload_plan"], now_ms=s._ms(s.now()))
        payload["native_context"] = network.native_context(inspection.get("native_context"))
        payload["allowed_actions"] = ["upload_resume"]
        payload["disclosures"] = [
            "This approval sends the exact sealed resume bytes once to the reviewed employer storage recipient and object key, then updates this opening's Resume/CV control only after a direct secure storage acknowledgement.",
            "The complete resume can be disclosed before final submission. Review the exact storage origin, filename and saved resume before approving. Earlier disclosure cannot be recalled.",
            "Only the approved, extension-owned complete multipart bytes may be uploaded. An uncertain upload or lost response is never automatically repeated.",
            "Cancellation or revocation stops future actions. An action already authorized may finish within its ten-second lease, which is never extended.",
            "This upload approval does not submit the application or charge application credits. Final submission needs a separate complete review.",
        ]
    else:
        s.require("native_context" not in inspection)
    return payload


def begin(db: Session, record, review, body) -> dict:
    from . import hosted_network as network
    from . import service as s

    s.require(enabled() and review.review_protocol_version == 3, 503)
    s.require(review.state == "approved" and review.approved_at is not None and review.next_index == 0)
    s.require(set(body) == {"review_id", "review_digest", "index", "context_digest", "document_id"})
    s.require(type(body["index"]) is int and body["index"] == 0)
    s.require(body["review_digest"] == review.review_digest and body["context_digest"] == review.context_digest
              and body["document_id"] == review.payload["document_id"])
    app, _, artifact = s._application(db, review, record.user_id)
    artifact = db.query(s.employer.SealedApplicationArtifact).filter_by(id=artifact.id, user_id=record.user_id).populate_existing().with_for_update(read=True).one()
    s.require(descriptor(app, artifact) == review.payload["artifact"])
    s.require(db.query(s.RequestRecord.id).filter_by(review_id=review.id, field_index=0).first() is None)
    identity = str(uuid4())
    issued = s.now()
    s.require(s.enabled() and enabled(), 503)
    s.require(s._aware(review.expires_at) > issued)
    expires = min(issued + s.timedelta(milliseconds=s.LEASE_MS), s._aware(review.expires_at))
    payload = {"protocol_version": 1, "review_protocol_version": 3, "action_id": identity,
               "review_id": review.id, "device_id": record.id, "application_id": app.id,
               "package_digest": app.package_digest, "artifact_sha256": artifact.sha256,
               "artifact": review.payload["artifact"], "target_url": review.payload["target_url"],
               "context_digest": review.context_digest, "document_id": review.payload["document_id"], "frame_id": 0,
               "review_digest": review.review_digest, "field_id": "resume", "control_type": "file",
               "attachment": review.payload["attachment"], "allowed_actions": ["attach"],
               "issued_at_ms": s._ms(issued), "deadline": s._ms(expires)}
    response = {}
    if "upload_plan" in review.payload:
        s.require(network.enabled() and review.payload["upload_plan"]["expires_at_ms"] > payload["deadline"], 503)
        payload.update(upload_plan=review.payload["upload_plan"], native_context=review.payload["native_context"],
                       allowed_actions=["upload_resume"])
        response = {"upload_lease": {"issued_at_ms": payload["issued_at_ms"], "deadline": payload["deadline"]},
                    "upload_binding": {key: copy.deepcopy(review.payload[key]) for key in (
                        "upload_plan", "native_context", "target_url", "document_id", "artifact")}}
        response["upload_binding"]["review_digest"] = review.review_digest
    db.add(s.RequestRecord(id=identity, device_id=record.id, request_id=identity, operation="attachment",
                          request_sha256=s.digest(payload), review_id=review.id, field_index=0,
                          state="begun", response=response, lease_expires_at=expires))
    review.state = "running"
    return {"state": "begun", "lease": s._signature(payload)}


def _authorize_delivery(db, origin, request):
    from . import hosted_network as network
    from . import service as s

    s.require(s.enabled() and enabled(), 503)
    s.require(origin == "chrome-extension://" + s.EXTENSION_ID, 403)
    s.require(len(s.canonical(request.model_dump(mode="json"))) <= 65_536, 422)
    body = request.body
    s.require(set(body) == {"review_id", "action_id", "review_digest"})
    record = db.query(s.Device).filter_by(id=str(request.device_id)).first()
    s.require(record is not None and record.user_id is not None, 403)
    request_sha = s._verify(request, record.public_key)
    owner_id = record.user_id
    s._owner_lock(db, owner_id)
    record = db.query(s.Device).filter_by(id=str(request.device_id)).populate_existing().with_for_update().first()
    s.require(record is not None and record.user_id == owner_id and record.state == "active" and record.extension_id == s.EXTENSION_ID, 403)
    s.require(db.query(s.RequestRecord.id).filter_by(device_id=record.id, request_id=str(request.request_id)).first() is None)
    review = db.query(s.Review).filter_by(id=body["review_id"], device_id=record.id,
                                       user_id=record.user_id).populate_existing().with_for_update().first()
    s.require(review is not None, 404)
    s._expire(review)
    s.require(review.review_protocol_version == 3 and review.state == "running" and review.approved_at is not None and bool(review.payload))
    s.require(review.review_digest == body["review_digest"])
    app, _, artifact = s._application(db, review, record.user_id)
    artifact = db.query(s.employer.SealedApplicationArtifact).filter_by(id=artifact.id, user_id=record.user_id).populate_existing().with_for_update(read=True).one()
    s.require(descriptor(app, artifact) == review.payload["artifact"])
    action = db.query(s.RequestRecord).filter_by(id=body["action_id"], device_id=record.id,
        review_id=review.id, operation="attachment", field_index=0).populate_existing().with_for_update().first()
    s.require(action is not None and action.state == "begun"
              and (set(action.response) == {"upload_lease", "upload_binding"} if "upload_plan" in review.payload else not action.response)
              and action.lease_expires_at is not None and s._aware(action.lease_expires_at) > s.now())
    if "upload_plan" in review.payload:
        s.require(network.enabled() and review.payload["upload_plan"]["expires_at_ms"] > s._ms(action.lease_expires_at), 503)
    return record, review, action, artifact, request_sha


def deliver(db: Session, origin, request) -> BinaryDelivery:
    from ..employer import artifacts
    from . import service as s

    _, review, _, artifact, _ = _authorize_delivery(db, origin, request)
    expected = copy.deepcopy(review.payload["artifact"])
    snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(artifact, key)) for key in (
        "sha256", "size_bytes", "gcs_object", "gcs_generation", "content", "filename", "media_type")})
    db.rollback()  # Never retain owner/application locks across storage I/O.
    content = artifacts.read(snapshot, max_bytes=MAX_BYTES)
    # Reacquire every lock and freshly check flags, approval, source and deadline
    # after storage I/O and immediately before the irreversible disclosure claim.
    record, review, action, artifact, request_sha = _authorize_delivery(db, origin, request)
    s.require(review.payload["artifact"] == expected)
    s.require(len(content) == expected["size_bytes"] and s.hashlib.sha256(content).hexdigest() == expected["sha256"])
    claim_time = s.now()
    s.require(s.enabled() and enabled(), 503)
    s.require(s._aware(review.expires_at) > claim_time and s._aware(action.lease_expires_at) > claim_time)
    deadline = s._aware(action.lease_expires_at)
    action.response = {**action.response, "delivery_claimed": True}
    db.add(s.RequestRecord(id=str(uuid4()), device_id=record.id, request_id=str(request.request_id),
                          operation="artifact", request_sha256=request_sha, review_id=review.id,
                          response={}, state="completed"))
    db.commit()  # Lost/unknown binary responses can never be redelivered.
    s.require(s.now() < deadline)  # An expired commit still consumes the claim, never sends late bytes.
    return BinaryDelivery(content, expected)


def outcome(db, record, review, body):
    from . import hosted_network as network
    from . import service as s

    s.require({"review_id", "action_id", "outcome"} <= set(body)
              and set(body) <= {"review_id", "action_id", "outcome", "selection_reported", "upload_evidence"})
    s.require("selection_reported" not in body or type(body["selection_reported"]) is bool)
    s.require(body["outcome"] in {"selected", "blocked", "unknown"})
    action = db.query(s.RequestRecord).filter_by(id=body["action_id"], device_id=record.id,
        review_id=review.id, operation="attachment", field_index=0).with_for_update().first()
    s.require(action is not None and action.state == "begun" and review.next_index == 0)
    native = "upload_plan" in review.payload
    expected = {"upload_lease", "upload_binding", "delivery_claimed"} if native else {"delivery_claimed"}
    s.require(body["outcome"] != "selected" or set(action.response) == expected and action.response.get("delivery_claimed") is True)
    if "upload_evidence" in body:
        s.require(native and body["outcome"] == "selected" and body.get("selection_reported") is True)
        app, _, artifact = s._application(db, review, record.user_id)
        evidence = network.upload_evidence(body["upload_evidence"], review=review, action=action,
                                           artifact=artifact, lease=action.response["upload_lease"])
        action.response = {**action.response, "native_upload": evidence,
                           "upload_plan_sha256": s.digest(review.payload["upload_plan"])}
    if native and body["outcome"] == "selected":
        s.require("native_upload" in action.response)
    if body.get("selection_reported") is True:
        s.require(body["outcome"] == "selected" and action.response.get("delivery_claimed") is True)
        action.response = {**action.response, "selection_reported": True, "selection_document_id":review.payload["document_id"],
                           "selection_target_url":review.payload["target_url"], "selection_artifact_sha256":review.artifact_sha256}
    action.state = "filled" if body["outcome"] == "selected" else body["outcome"]
    review.state = "filled" if body["outcome"] == "selected" else "cancelled" if body["outcome"] == "blocked" else "unknown"
    if body["outcome"] == "selected":
        review.next_index = 1
    return status(db, review)


def reconcile(db, record, review, body):
    """Record original captured upload proof only; never redeliver or upload again."""
    from . import hosted_network as network
    from . import service as s

    s.require(set(body) == {"review_id", "action_id", "upload_evidence", "selection_reported"}
              and body["selection_reported"] is True and review.review_protocol_version == 3)
    action = db.query(s.RequestRecord).filter_by(id=body["action_id"], device_id=record.id,
        review_id=review.id, operation="attachment", field_index=0).with_for_update().one_or_none()
    s.require(action is not None and action.response.get("delivery_claimed") is True)
    binding = action.response.get("upload_binding")
    s.require(isinstance(binding, dict) and action.response.get("upload_lease"))
    app, _, artifact = s._application(db, review.application_id, record.user_id)
    s.require(descriptor(app, artifact) == binding["artifact"]
              and artifact.sha256 == review.artifact_sha256 and binding["review_digest"] == review.review_digest)
    original = SimpleNamespace(id=review.id, review_digest=review.review_digest,
                               payload={key: binding[key] for key in (
                                   "upload_plan", "native_context", "target_url", "document_id")})
    evidence = network.upload_evidence(body["upload_evidence"], review=original, action=action,
                                       artifact=artifact, lease=action.response["upload_lease"])
    if "native_upload" in action.response:
        s.require(action.response["native_upload"] == evidence)
    action.response = {**action.response, "native_upload": evidence,
                       "upload_plan_sha256": s.digest(binding["upload_plan"]), "selection_reported": True,
                       "selection_document_id": binding["document_id"], "selection_target_url": binding["target_url"],
                       "selection_artifact_sha256": artifact.sha256}
    # Erased review payloads stay erased; no approval or future action is created.
    action.state, review.state, review.next_index = "filled", "filled", 1
    return status(db, review)


def status(db, review):
    from . import service as s
    result = s._review_response(review)
    if review.state == "filled":
        action = db.query(s.RequestRecord).filter_by(review_id=review.id,
            device_id=review.device_id, operation="attachment", field_index=0).first()
        if action and action.response.get("native_upload") and action.response.get("selection_reported") is True:
            result["attachment_status"] = "uploaded_native_verified"
    return result
