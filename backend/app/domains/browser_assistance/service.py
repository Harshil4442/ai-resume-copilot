"""Explicit reviewed form assistance on Razorpay's official Greenhouse portal.

PostgreSQL owns revocation, review expiry and one-shot field outcomes. TLS and a
nonextractable device key identify the caller; signed leases bind exact actions.
This service never uploads, submits, confirms an application or settles credits.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    decode_dss_signature,
    encode_dss_signature,
)
from fastapi import HTTPException
from sqlalchemy import Text, cast
from sqlalchemy.orm import Session

from ... import models as core
from ..employer import models as employer
from ..employer.service import _package_digest
from .models import BrowserAssistanceDevice as Device
from .models import BrowserAssistanceRequest as RequestRecord
from .models import BrowserAssistanceReview as Review
from .schemas import CandidateRequest, DeviceRequest

ORIGIN = "https://job-boards.greenhouse.io"
TENANT = "razorpaysoftwareprivatelimited"
EXTENSION_ID = "pnjmegppacleipajnifjmbcopjjffklb"
FIELDS = (
    ("first_name", "First Name"), ("last_name", "Last Name"), ("email", "Email"),
    ("question_8970454005", "LinkedIn Profile"), ("question_8970455005", "Website"),
    ("question_8970456005", "Total Years of Experience"), ("question_8970461005", "Current Location"),
)
FIELD_IDS = frozenset(field[0] for field in FIELDS)
FIELD_LABELS = frozenset(field[1] for field in FIELDS)
CHOICE_LABELS = ("Current Career Stage", "Gender", "Current Industry", "Current Designation")
MAX_REVIEW_FIELDS = 12


def typed_controls_enabled() -> bool:
    # Keep the new widget contract separate from the established text pilot.
    # A native read-only portal compatibility check precedes production rollout.
    return os.getenv("BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED", "false").lower() == "true"


REVIEW_MS = 300_000
LEASE_MS = 10_000
P256_ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
ERROR = "Browser assistance request could not be verified. No new field was authorized."


def now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _ms(value: datetime) -> int:
    return int(_aware(value).timestamp() * 1000)


def canonical(value: object) -> bytes:
    # Match the companion's canonical JSON; reject invalid Unicode/NaN.
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode("utf8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def require(condition: bool, status: int = 409) -> None:
    if not condition:
        raise HTTPException(status, ERROR)


def enabled() -> bool:
    return os.getenv("BROWSER_ASSISTANCE_ENABLED", "false").lower() == "true"


def _signing_key():
    secret = os.getenv("BROWSER_ASSISTANCE_SIGNING_SECRET", "")
    if len(secret.encode()) < 32:
        raise HTTPException(503, "Reviewed browser assistance is currently unavailable.")
    number = int.from_bytes(hashlib.sha256(b"hirewiz.browser-assistance.signer.v1\n" + secret.encode()).digest(), "big")
    return ec.derive_private_key(number % (P256_ORDER - 1) + 1, ec.SECP256R1())


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def public_signer() -> dict:
    numbers = _signing_key().public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": _b64(numbers.x.to_bytes(32, "big")), "y": _b64(numbers.y.to_bytes(32, "big"))}


def _signature(payload: dict) -> dict:
    raw = b"hirewiz.browser-assistance.lease.v1\n" + canonical(payload)
    first, second = decode_dss_signature(_signing_key().sign(raw, ec.ECDSA(hashes.SHA256())))
    return {"payload": payload, "signature": _b64(first.to_bytes(32, "big") + second.to_bytes(32, "big"))}


def availability() -> dict:
    if not enabled():
        return {"enabled": False, "allowed_actions": [], "fee_credits": 0}
    public_signer()
    return {"enabled": True, "allowed_actions": ["fill"], "fee_credits": 0, "portal_origin": ORIGIN, "tenant": TENANT}


def _public_key(value: object) -> tuple[dict, ec.EllipticCurvePublicKey]:
    require(isinstance(value, dict) and set(value) == {"kty", "crv", "x", "y"}, 403)
    assert isinstance(value, dict)
    require(value.get("kty") == "EC" and value.get("crv") == "P-256", 403)
    require(all(isinstance(value.get(key), str) and re.fullmatch(r"[A-Za-z0-9_-]{43}", value[key]) for key in ("x", "y")), 403)
    try:
        numbers = ec.EllipticCurvePublicNumbers(*(int.from_bytes(base64.urlsafe_b64decode(value[key] + "="), "big") for key in ("x", "y")), ec.SECP256R1())
        return value, numbers.public_key()
    except (ValueError, TypeError):
        raise HTTPException(403, ERROR) from None


def _verify(request: DeviceRequest, public_key: dict) -> str:
    unsigned = request.model_dump(mode="json", exclude={"signature"})
    current = _ms(now())
    require(abs(current - request.issued_at_ms) <= 30_000, 403)
    _, key = _public_key(public_key)
    try:
        raw = base64.urlsafe_b64decode(request.signature + "=" * (-len(request.signature) % 4))
        require(len(raw) == 64, 403)
        key.verify(encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")),
                   b"hirewiz.browser-assistance.request.v1\n" + canonical(unsigned), ec.ECDSA(hashes.SHA256()))
    except (ValueError, InvalidSignature):
        raise HTTPException(403, ERROR) from None
    return digest(unsigned)


def _owner_lock(db: Session, user_id: int) -> None:
    require(db.query(core.User.id).filter_by(id=user_id).with_for_update().first() is not None, 401)


def _device_response(device: Device) -> dict:
    return {"id": device.id, "state": device.state, "key_sha256": device.key_sha256,
            "extension_id": device.extension_id, "expires_at_ms": _ms(device.pairing_expires_at),
            "authority_key": public_signer()}


def _review_response(review: Review, *, include_payload: bool = True) -> dict:
    return {"id": review.id, "state": review.state, "expires_at_ms": _ms(review.expires_at),
            "review_digest": review.review_digest, "filled_count": review.next_index,
            "payload": review.payload if include_payload else None}


def _expire(review: Review) -> None:
    if _aware(review.expires_at) <= now():
        if review.state not in {"filled", "unknown", "cancelled"}:
            review.state = "expired"
        review.payload = {}


def expire_reviews(db: Session, limit: int = 100) -> int:
    """Bounded maintenance; retain outcome metadata and erase expired answers."""
    require(type(limit) is int and 1 <= limit <= 100)
    rows = db.query(Review).filter(Review.expires_at <= now(), cast(Review.payload, Text) != "{}").order_by(Review.expires_at).limit(limit).with_for_update(skip_locked=True).all()
    for review in rows:
        _expire(review)
    # Request receipts need only status/identity after the original review TTL.
    records = db.query(RequestRecord).filter(RequestRecord.created_at <= now() - timedelta(minutes=5), cast(RequestRecord.response, Text) != "{}").order_by(RequestRecord.created_at).limit(limit).with_for_update(skip_locked=True).all()
    for record in records:
        record.response = {}
    db.commit()
    return len(rows) + len(records)


def _application(db: Session, review_or_id, user_id: int):
    identity = review_or_id.application_id if isinstance(review_or_id, Review) else review_or_id
    app = db.query(employer.EmployerApplication).filter_by(id=identity, user_id=user_id).populate_existing().with_for_update().first()
    require(app is not None, 404)
    require(app.status in {"needs_action", "ready", "approved", "manual_handoff"} and not app.cancel_requested)
    if app.resume_choice == "tailored":
        version = db.query(core.ResumeVersion).filter_by(id=app.resume_version_id, user_id=user_id, resume_id=app.resume_id, approval_state="approved").first()
        require(version is not None and bool((version.structured_content or {}).get("source_edits")))
        evidence_ids = version.evidence_ids or []
        if evidence_ids:
            approved = db.query(core.EvidenceItem.id).filter(core.EvidenceItem.id.in_(evidence_ids), core.EvidenceItem.user_id == user_id,
                core.EvidenceItem.approval_state == "approved").all()
            require({identity for identity, in approved} == set(evidence_ids))
    source_identity = db.query(employer.EmployerPosting.source_id).filter_by(id=app.posting_id).first()
    require(source_identity is not None)
    source = db.query(employer.EmployerSource).filter_by(id=source_identity[0]).populate_existing().with_for_update(read=True).first()
    posting = db.query(employer.EmployerPosting).filter_by(id=app.posting_id).populate_existing().with_for_update(read=True).first()
    require(posting is not None and posting.is_open)
    require(source is not None and source.enabled and source.platform == "greenhouse" and source.board_token == TENANT)
    require(posting.content_sha256 == app.job_content_sha256)
    require(posting.last_checked_at is not None and _aware(posting.last_checked_at) >= now() - timedelta(hours=24))
    artifact = db.query(employer.SealedApplicationArtifact).filter_by(id=app.artifact_id, user_id=user_id).first()
    require(artifact is not None)
    require(artifact.resume_id == app.resume_id and artifact.resume_version_id == app.resume_version_id)
    require(_package_digest(app, posting, source, artifact) == app.package_digest)
    require(artifact.media_type in {"application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"})
    require(re.fullmatch(r"https://job-boards\.greenhouse\.io/razorpaysoftwareprivatelimited/jobs/[0-9]+", posting.apply_url or "") is not None)
    if isinstance(review_or_id, Review):
        require(app.package_digest == review_or_id.package_digest and artifact.sha256 == review_or_id.artifact_sha256)
    return app, posting, artifact


def field_mapping(app, review_protocol_version: int = 1) -> list[dict]:
    """Exact job-specific semantics; v1 approvals never acquire v2 controls."""
    require(type(review_protocol_version) is int and review_protocol_version in {1, 2})
    require(review_protocol_version == 1 or typed_controls_enabled(), 503)
    fields = app.form.get("fields", [])
    require(isinstance(fields, list) and len(fields) <= 200)
    mapping = []
    labels = set()
    identities = set()
    base = {"First Name": "first_name", "Last Name": "last_name", "Email": "email"}
    for field in fields:
        if not isinstance(field, dict):
            continue
        label = re.sub(r"\s+", " ", str(field.get("label", ""))).strip().rstrip("*").strip()
        kind = "text"
        options = None
        if review_protocol_version == 2 and label == "Phone" and field.get("id") == "phone" and field.get("type") in {"text", "tel"}:
            kind = "tel"
        elif review_protocol_version == 2 and label in CHOICE_LABELS and field.get("type") == "single_select":
            kind = "combobox"
            options = field.get("options")
            require(isinstance(options, list) and 1 <= len(options) <= 64)
            require(all(isinstance(option, dict) and set(option) == {"value", "label"}
                        and all(isinstance(option[key], str) and 1 <= len(option[key]) <= 255 for key in ("value", "label"))
                        for option in options))
            require(len({option["value"] for option in options}) == len(options)
                    and len({option["label"] for option in options}) == len(options))
        elif label not in FIELD_LABELS or field.get("type") not in {"text", "email"}:
            continue
        identity = field.get("id")
        require(isinstance(identity, str) and label not in labels and identity not in identities)
        assert isinstance(identity, str)
        require(identity == base[label] if label in base else identity == "phone" if kind == "tel"
                else re.fullmatch(r"question_[0-9]{1,20}", identity) is not None)
        labels.add(label)
        identities.add(identity)
        descriptor = {"id": identity, "label": label, "type": kind, "required": bool(field.get("required"))}
        if options is not None:
            descriptor["options"] = options
        mapping.append(descriptor)
    require(set(base) <= labels and 3 <= len(mapping) <= (7 if review_protocol_version == 1 else MAX_REVIEW_FIELDS))
    order = {label: index for index, label in enumerate([*(label for _, label in FIELDS), "Phone", *CHOICE_LABELS])}
    return sorted(mapping, key=lambda field: order[field["label"]])


def candidate(db: Session, user_id: int, operation: str, request: CandidateRequest) -> dict:
    require(enabled(), 503)
    _owner_lock(db, user_id)
    identity = str(request.identity)
    if operation in {"device", "confirm", "revoke"}:
        device = db.query(Device).filter_by(id=identity).with_for_update().first()
        require(device is not None, 404)
        require(device.user_id in {None, user_id}, 404)
        if operation == "confirm":
            require(request.confirmed is True and request.key_sha256 == device.key_sha256)
            require(device.state == "pending" and _aware(device.pairing_expires_at) > now())
            device.user_id, device.state, device.confirmed_at = user_id, "active", now()
        elif operation == "revoke":
            require(device.user_id == user_id and request.confirmed is True)
            device.state, device.revoked_at = "revoked", now()
            for review in db.query(Review).filter_by(device_id=device.id, user_id=user_id).filter(Review.state.in_(["draft", "approved", "running"])):
                review.state, review.payload = "cancelled", {}
        response = _device_response(device)
    else:
        review = db.query(Review).filter_by(id=identity, user_id=user_id).with_for_update().first()
        require(review is not None, 404)
        _expire(review)
        if operation == "approve":
            device = db.query(Device).filter_by(id=review.device_id, user_id=user_id, state="active").first()
            require(device is not None and review.state == "draft" and bool(review.payload))
            _application(db, review, user_id)
            require(review.payload.get("review_protocol_version", 1) == 1 or typed_controls_enabled(), 503)
            require(request.confirmed is True and request.review_digest == review.review_digest)
            review.state, review.approved_at = "approved", now()
        elif operation == "cancel":
            require(request.confirmed is True)
            review.state, review.payload = "cancelled", {}
        else:
            require(operation == "review", 404)
        response = _review_response(review)
    db.commit()
    return response


def _inspection(body: dict, app, posting, artifact) -> dict:
    require(set(body) == {"application_id", "inspection"})
    inspection = body["inspection"]
    require(isinstance(inspection, dict))
    values = inspection.get("assisted_values")
    version = inspection.get("review_protocol_version", 1)
    mapping = field_mapping(app, version)
    identities = {field["id"] for field in mapping}
    require(isinstance(values, dict) and set(values) == identities)
    require(inspection.get("outcome") == "inspected" and inspection.get("origin") == ORIGIN
            and inspection.get("url") == posting.apply_url and type(inspection.get("frame_id")) is int and inspection.get("frame_id") == 0)
    require(isinstance(inspection.get("document_id"), str) and 1 <= len(inspection["document_id"]) <= 160)
    require(isinstance(inspection.get("context_digest"), str) and re.fullmatch(r"[a-f0-9]{64}", inspection["context_digest"]) is not None)
    if "context" in inspection:
        require(isinstance(inspection["context"], dict) and inspection["context_digest"] == digest(inspection["context"]))
    require(all(isinstance(values[field], str) and len(values[field]) <= 255 for field in identities))
    answers = {field["id"]: app.answers[field["id"]] for field in mapping if isinstance(app.answers.get(field["id"]), str) and app.answers[field["id"]]}
    require(bool(answers) and all(len(value) <= 255 for value in answers.values()))
    fields = []
    for field in mapping:
        option_labels = {option["value"]: option["label"] for option in field.get("options", [])}
        if field["type"] == "combobox":
            require(values[field["id"]] == "" or values[field["id"]] in option_labels)
            require(field["id"] not in answers or answers[field["id"]] in option_labels)
        if field["id"] not in answers or answers[field["id"]] == values[field["id"]]:
            continue
        entry = {**field, "value": answers[field["id"]], "expected_before": values[field["id"]]}
        if field["type"] == "combobox":
            entry.update(value_label=option_labels[entry["value"]], expected_before_label=option_labels.get(entry["expected_before"], ""))
        fields.append(entry)
    require(bool(fields))
    descriptor = {"sha256": artifact.sha256, "filename": artifact.filename, "media_type": artifact.media_type, "size_bytes": artifact.size_bytes}
    approved_answers = {field["id"]: field["value"] for field in fields}
    payload = {"application_id": app.id, "package_digest": app.package_digest, "target_url": posting.apply_url,
               "artifact": descriptor, "context_digest": inspection["context_digest"], "field_mapping": mapping,
               "document_id": inspection["document_id"], "frame_id": 0, "reviewed_answers": approved_answers,
               "reviewed_answers_sha256": digest({"context_digest": inspection["context_digest"], "reviewed_answers": approved_answers}),
               "fields": fields, "manual_fields": inspection.get("manual_fields", []), "allowed_actions": ["fill"], "fee_credits": 0,
               "disclosures": ["Filling can disclose or autosave the exact approved answers before submission.",
                   "Email blur can send the approved email to https://email-address-validator.us.greenhouse.io/address/validate.",
                   "Country, phone, employment, custom choices, sensitive answers, file attachment, CAPTCHA and final submission remain yours."]}
    if version == 2:
        payload["review_protocol_version"] = 2
        payload["disclosures"][-1] = "Only your explicitly saved phone and supported choices can be filled. Gender and other personal answers are never inferred. Country, employment, unknown choices, file attachment, CAPTCHA and final submission remain yours."
        payload["disclosures"].append("Opening a reviewed dropdown and selecting its exact option can disclose or autosave that choice before final submission.")
        payload["manual_fields"] = [*payload["manual_fields"], *[
            {key: field[key] for key in ("id", "label", "required")} for field in mapping
            if field["required"] and field["id"] not in answers and not values[field["id"]]]]
    require(isinstance(payload["manual_fields"], list) and len(payload["manual_fields"]) <= 128)
    require(all(isinstance(field, dict) and set(field) == {"id", "label", "required"}
                and isinstance(field["id"], str) and len(field["id"]) <= 160
                and isinstance(field["label"], str) and len(field["label"]) <= 256
                and type(field["required"]) is bool for field in payload["manual_fields"]))
    return payload


def device(db: Session, origin: str, request: DeviceRequest) -> dict:
    require(enabled(), 503)
    require(re.fullmatch(r"chrome-extension://[a-p]{32}", origin or "") is not None, 403)
    require(len(canonical(request.model_dump(mode="json"))) <= 65_536, 422)
    identity, request_id = str(request.device_id), str(request.request_id)
    if request.operation == "prepare":
        require(set(request.body) == {"public_key", "extension_id"}, 422)
        public_key, _ = _public_key(request.body["public_key"])
        require(request.body["extension_id"] == EXTENSION_ID and origin == "chrome-extension://" + EXTENSION_ID, 403)
        request_sha = _verify(request, public_key)
        record = db.query(Device).filter_by(id=identity).with_for_update().first()
        if record is None:
            key_sha = digest(public_key)
            require(db.query(Device.id).filter_by(key_sha256=key_sha).first() is None)
            record = Device(id=identity, extension_id=request.body["extension_id"], public_key=public_key,
                            key_sha256=key_sha, state="pending", pairing_expires_at=now() + timedelta(minutes=5))
            db.add(record)
            db.flush()
        require(record.public_key == public_key and record.extension_id == request.body["extension_id"])
        require(record.state in {"pending", "active"})
        if record.state == "pending" and _aware(record.pairing_expires_at) <= now():
            record.created_at, record.pairing_expires_at = now(), now() + timedelta(minutes=5)
        response = _device_response(record)
        db.commit()
        return response
    record = db.query(Device).filter_by(id=identity).first()
    require(record is not None, 403)
    request_sha = _verify(request, record.public_key)
    require(origin == "chrome-extension://" + record.extension_id, 403)
    if record.user_id is not None:
        _owner_lock(db, record.user_id)
    record = db.query(Device).filter_by(id=identity).populate_existing().with_for_update().first()
    require(record is not None)
    original = db.query(RequestRecord).filter_by(device_id=identity, request_id=request_id).first() if request.operation != "status" else None
    if original is not None:
        require(original.request_sha256 == request_sha and original.operation == request.operation)
        require(record.state == "active", 403)
        # A begin reply may already have caused a field event. Never reissue it.
        if original.operation == "begin":
            response = {"state": "unknown", "request_id": request_id}
        else:
            response = original.response or {"state": "expired", "request_id": request_id}
        db.rollback()
        return response
    if request.operation == "status":
        require(set(request.body) <= {"review_id"})
        response = _device_response(record)
        if request.body.get("review_id"):
            review = db.query(Review).filter_by(id=request.body["review_id"], device_id=identity, user_id=record.user_id).first()
            require(review is not None, 404)
            _expire(review)
            response["review"] = _review_response(review)
    else:
        require(record.state == "active" and record.user_id is not None, 403)
        if request.operation == "application":
            require(set(request.body) <= {"application_id", "review_protocol_version"} and isinstance(request.body.get("application_id"), str))
            requested_version = request.body.get("review_protocol_version", 1)
            require(type(requested_version) is int and requested_version in {1, 2})
            version = 2 if requested_version == 2 and typed_controls_enabled() else 1
            app, posting, _ = _application(db, request.body["application_id"], record.user_id)
            response = {"application_id": app.id, "target_url": posting.apply_url, "field_mapping": field_mapping(app, version)}
            if version == 2:
                response["review_protocol_version"] = 2
        elif request.operation == "inspect":
            require(isinstance(request.body.get("application_id"), str))
            app, posting, artifact = _application(db, request.body["application_id"], record.user_id)
            payload = _inspection(request.body, app, posting, artifact)
            review = Review(id=str(uuid4()), user_id=record.user_id, device_id=identity, application_id=app.id,
                            package_digest=app.package_digest, artifact_sha256=artifact.sha256, context_digest=payload["context_digest"],
                            review_digest=digest(payload), payload=payload, state="draft", next_index=0,
                            expires_at=now() + timedelta(minutes=5))
            db.add(review)
            db.flush()
            response = _review_response(review)
        else:
            response = _action(db, record, request)
    if request.operation != "status":
        db.add(RequestRecord(id=str(uuid4()), device_id=identity, request_id=request_id, operation=request.operation,
                            request_sha256=request_sha, response=response, state="completed"))
    db.commit()
    return response


def _action(db: Session, record: Device, request: DeviceRequest) -> dict:
    body = request.body
    require(isinstance(body.get("review_id"), str))
    review = db.query(Review).filter_by(id=body["review_id"], device_id=record.id, user_id=record.user_id).with_for_update().first()
    require(review is not None, 404)
    _expire(review)
    if request.operation == "cancel":
        require(set(body) == {"review_id"})
        review.state, review.payload = "cancelled", {}
        return _review_response(review)
    require(review.state in {"approved", "running"} and bool(review.payload))
    _application(db, review, record.user_id)
    version = review.payload.get("review_protocol_version", 1)
    require(version == 1 or typed_controls_enabled(), 503)
    if request.operation == "begin":
        require(set(body) == {"review_id", "review_digest", "index", "context_digest", "document_id"})
        index = body["index"]
        require(type(index) is int and index == review.next_index and 0 <= index < len(review.payload["fields"]))
        require(body["review_digest"] == review.review_digest and body["context_digest"] == review.context_digest
                and body["document_id"] == review.payload["document_id"])
        require(db.query(RequestRecord.id).filter_by(review_id=review.id, field_index=index).first() is None)
        action_id = str(uuid4())
        expires = now() + timedelta(milliseconds=LEASE_MS)
        field = review.payload["fields"][index]
        payload = {"protocol_version": 1, "action_id": action_id, "review_id": review.id, "device_id": record.id,
                   "application_id": review.application_id, "package_digest": review.package_digest,
                   "artifact_sha256": review.artifact_sha256, "target_url": review.payload["target_url"],
                   "context_digest": review.context_digest, "document_id": body["document_id"], "frame_id": 0,
                   "review_digest": review.review_digest, "reviewed_answers_sha256": review.payload["reviewed_answers_sha256"],
                   "field_id": field["id"], "value": field["value"], "expected_before": field["expected_before"],
                   "issued_at_ms": _ms(now()), "deadline": _ms(expires), "allowed_actions": ["fill"]}
        if version == 2:
            payload["review_protocol_version"] = 2
            payload["control_type"] = field["type"]
            if field["type"] == "combobox":
                payload["option_label"] = field["value_label"]
        if field["id"] == "email":
            payload["email_blur_disclosure"] = {"acknowledged": True, "event": "blur",
                "recipient": "https://email-address-validator.us.greenhouse.io/address/validate", "context_digest": review.context_digest}
        lease = _signature(payload)
        db.add(RequestRecord(id=action_id, device_id=record.id, request_id=action_id, operation="field",
            request_sha256=digest(payload), review_id=review.id, field_index=index, state="begun", response={}, lease_expires_at=expires))
        review.state = "running"
        return {"state": "begun", "lease": lease}
    require(request.operation == "outcome" and set(body) == {"review_id", "action_id", "outcome"})
    require(body["outcome"] in {"filled", "blocked", "unknown"})
    action = db.query(RequestRecord).filter_by(id=body["action_id"], device_id=record.id, review_id=review.id).with_for_update().first()
    require(action is not None and action.state == "begun" and action.field_index == review.next_index)
    action.state = body["outcome"]
    if body["outcome"] == "filled":
        review.next_index += 1
        review.state = "filled" if review.next_index == len(review.payload["fields"]) else "approved"
    else:
        review.state = "unknown" if body["outcome"] == "unknown" else "cancelled"
    return _review_response(review)
