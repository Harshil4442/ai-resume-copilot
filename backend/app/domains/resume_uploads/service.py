"""Owner-bound durable admission without parsing, model calls or unit charges."""
from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from ... import models as core
from ... import schemas as core_schemas
from ..common import utcnow
from .models import ResumeUpload
from .schemas import MEDIA_TYPES, SourceAccess, UploadCreate, UploadIntent, UploadStatus
from .storage import ObjectStore, StorageUnavailable, valid_generation

ACTIVE_STATES = ("awaiting_upload", "queued", "inspecting")
UPLOAD_GRANT_SECONDS, ADMISSION_SECONDS, READ_GRANT_SECONDS = 300, 900, 60
ACTIVE_LIMIT, DAILY_LIMIT = 3, 20


def fail(status: int, code: str) -> None:
    raise HTTPException(status, {"code": code})


def enabled() -> None:
    if os.getenv("RESUME_DIRECT_UPLOAD_ENABLED", "false") != "true":
        fail(503, "direct_upload_unavailable")


def lock_owner(db: Session, owner_id: int):
    owner = db.query(core.User).filter_by(id=owner_id).populate_existing().with_for_update().one_or_none()
    if owner is None:
        fail(404, "resume_upload_not_found")
    return owner


def owned(db: Session, owner_id: int, upload_id: str, *, lock: bool = False):
    if not re.fullmatch(r"rup_[a-f0-9]{32}", upload_id):
        fail(404, "resume_upload_not_found")
    query = db.query(ResumeUpload).filter_by(id=upload_id, user_id=owner_id).populate_existing()
    row = (query.with_for_update() if lock else query).one_or_none()
    if row is None:
        fail(404, "resume_upload_not_found")
    return row


def status(db: Session, owner_id: int, upload_id: str) -> UploadStatus:
    enabled()
    row = owned(db, owner_id, upload_id)
    result = None
    if row.state == "released" and row.result_resume_id is not None:
        resume = db.query(core.Resume).filter_by(id=row.result_resume_id, user_id=owner_id).one_or_none()
        if resume is not None:
            result = core_schemas.ResumeParseResponse(
                resume_id=resume.id, skills=resume.skills or [], experience_years=resume.experience_years or 0.0,
                sections=resume.sections or {}, contact_info=core_schemas.ContactInfo(**(resume.contact_info or {})),
                source_available=bool(resume.source_available), source_format=resume.source_format,
                enrichment_state="unavailable" if row.enrich_skills else "not_requested",
                warnings=["Optional skill enrichment is unavailable; no units were charged."] if row.enrich_skills else [],
            )
    return UploadStatus(upload_id=row.id, state=row.state, error_code=row.last_error_code, resume=result)


def create(db: Session, owner_id: int, body: UploadCreate, key: str, store: ObjectStore) -> UploadIntent:
    enabled()
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
        fail(400, "idempotency_key_required")
    now = utcnow()
    kind = body.filename.rsplit(".", 1)[1].lower()
    digest = hashlib.sha256(json.dumps(body.model_dump(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    key_digest = hashlib.sha256(key.encode()).hexdigest()
    lock_owner(db, owner_id)
    row = db.query(ResumeUpload).filter_by(user_id=owner_id, idempotency_sha256=key_digest).one_or_none()
    if row is not None:
        if row.request_sha256 != digest:
            fail(409, "idempotency_metadata_conflict")
    else:
        active = db.query(ResumeUpload).filter(ResumeUpload.user_id == owner_id, ResumeUpload.state.in_(ACTIVE_STATES), ResumeUpload.expires_at > now).count()
        daily = db.query(ResumeUpload).filter(ResumeUpload.user_id == owner_id, ResumeUpload.created_at >= now - timedelta(days=1)).count()
        if active >= ACTIVE_LIMIT or daily >= DAILY_LIMIT:
            fail(429, "resume_upload_limit")
        identifier = "rup_" + uuid4().hex
        row = ResumeUpload(id=identifier, user_id=owner_id, idempotency_sha256=key_digest, request_sha256=digest,
                           original_filename=body.filename, source_format=kind, media_type=MEDIA_TYPES[kind],
                           size_bytes=body.size_bytes, source_sha256=body.sha256,
                           quarantine_bucket=store.config.quarantine_bucket, quarantine_name=f"quarantine/{identifier}/source",
                           clean_bucket=store.config.clean_bucket, clean_name=f"clean/{identifier}/source", enrich_skills=body.enrich_skills,
                           state="awaiting_upload", expires_at=now + timedelta(seconds=ADMISSION_SECONDS),
                           upload_grant_expires_at=now + timedelta(seconds=UPLOAD_GRANT_SECONDS), next_attempt_at=now, attempt_count=0)
        db.add(row)
    # Every fixed object target exists durably before issuing a capability.
    db.commit()
    grant = None
    headers: dict[str, str] = {}
    if row.state == "awaiting_upload" and row.upload_grant_expires_at > now and row.expires_at > now:
        try:
            grant, headers = store.sign_upload(row, row.upload_grant_expires_at)
        except StorageUnavailable:
            fail(503, "direct_upload_unavailable")
    return UploadIntent(upload_id=row.id, state=row.state, upload_url=grant, headers=headers,
                        upload_grant_expires_at=row.upload_grant_expires_at, expires_at=row.expires_at)


def complete(db: Session, owner_id: int, upload_id: str) -> UploadStatus:
    from .privacy import ensure_cleanup
    enabled()
    lock_owner(db, owner_id)
    row = owned(db, owner_id, upload_id, lock=True)
    if row.state == "awaiting_upload":
        if row.expires_at <= utcnow():
            row.state = "expired"
            ensure_cleanup(db, row)
        else:
            row.state, row.next_attempt_at = "queued", utcnow()
    db.commit()
    return status(db, owner_id, upload_id)


def cancel(db: Session, owner_id: int, upload_id: str) -> UploadStatus:
    from .privacy import ensure_cleanup
    enabled()
    lock_owner(db, owner_id)
    row = owned(db, owner_id, upload_id, lock=True)
    if row.state == "released" and row.result_resume_id is not None:
        fail(409, "saved_resume_requires_deletion")
    row.state, row.lease_token, row.lease_until = "cancelled", None, None
    ensure_cleanup(db, row)
    db.commit()
    return status(db, owner_id, upload_id)


def source_access(db: Session, owner_id: int, resume_id: int, store: ObjectStore) -> SourceAccess:
    enabled()
    lock_owner(db, owner_id)
    resume = db.query(core.Resume.id).filter_by(id=resume_id, user_id=owner_id).one_or_none()
    if resume is None:
        fail(404, "resume_not_found")
    row = db.query(ResumeUpload).filter_by(result_resume_id=resume_id).with_for_update().one_or_none()
    if row is None:
        # Only an existing owner-bound legacy resume permits the older source
        # route. A broken direct binding must never bypass its inspection state.
        fail(404, "direct_source_not_found")
    if row.user_id != owner_id or row.state != "released" or not valid_generation(row.clean_generation):
        fail(409, "clean_source_unavailable")
    receipt = row.scan_receipt or {}
    if not isinstance(receipt, dict):
        fail(409, "clean_source_unavailable")
    if receipt.get("sha256") != row.source_sha256 or receipt.get("size_bytes") != row.size_bytes or receipt.get("source_format") != row.source_format:
        fail(409, "clean_source_unavailable")
    expires = utcnow() + timedelta(seconds=READ_GRANT_SECONDS)
    try:
        url = store.sign_read(row, expires)
    except StorageUnavailable:
        fail(503, "clean_source_unavailable")
    result = SourceAccess(url=url, expires_at=expires, sha256=row.source_sha256, size_bytes=row.size_bytes,
                          filename=row.original_filename, media_type=row.media_type)
    db.commit()
    return result
