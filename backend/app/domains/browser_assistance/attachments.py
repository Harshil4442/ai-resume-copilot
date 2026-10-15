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
    return {"review_protocol_version": 3, "application_id": app.id, "package_digest": app.package_digest,
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


def begin(db: Session, record, review, body) -> dict:
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
    db.add(s.RequestRecord(id=identity, device_id=record.id, request_id=identity, operation="attachment",
                          request_sha256=s.digest(payload), review_id=review.id, field_index=0,
                          state="begun", response={}, lease_expires_at=expires))
    review.state = "running"
    return {"state": "begun", "lease": s._signature(payload)}


def _authorize_delivery(db, origin, request):
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
    s.require(action is not None and action.state == "begun" and not action.response
              and action.lease_expires_at is not None and s._aware(action.lease_expires_at) > s.now())
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
    action.response = {"delivery_claimed": True}
    db.add(s.RequestRecord(id=str(uuid4()), device_id=record.id, request_id=str(request.request_id),
                          operation="artifact", request_sha256=request_sha, review_id=review.id,
                          response={}, state="completed"))
    db.commit()  # Lost/unknown binary responses can never be redelivered.
    s.require(s.now() < deadline)  # An expired commit still consumes the claim, never sends late bytes.
    return BinaryDelivery(content, expected)


def outcome(db, record, review, body):
    from . import service as s

    s.require(set(body) == {"review_id", "action_id", "outcome"})
    s.require(body["outcome"] in {"selected", "blocked", "unknown"})
    action = db.query(s.RequestRecord).filter_by(id=body["action_id"], device_id=record.id,
        review_id=review.id, operation="attachment", field_index=0).with_for_update().first()
    s.require(action is not None and action.state == "begun" and review.next_index == 0)
    s.require(body["outcome"] != "selected" or action.response == {"delivery_claimed": True})
    action.state = "filled" if body["outcome"] == "selected" else body["outcome"]
    review.state = "filled" if body["outcome"] == "selected" else "cancelled" if body["outcome"] == "blocked" else "unknown"
    if body["outcome"] == "selected":
        review.next_index = 1
    return s._review_response(review)
