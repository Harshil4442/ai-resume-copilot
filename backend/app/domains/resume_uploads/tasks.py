"""Lease-based durable scan coordinator; the private worker alone parses bytes."""
from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from ... import models as core
from ..common import utcnow
from . import scanner
from .models import ResumeUpload
from .privacy import ensure_cleanup
from .storage import ObjectStore, StorageMismatch, StorageUnavailable, valid_generation

MAX_ATTEMPTS, LEASE_SECONDS = 3, 300


def _current(db, identifier, token):
    row = db.query(ResumeUpload).filter_by(id=identifier).populate_existing().with_for_update().one_or_none()
    if row is None or row.lease_token != token or row.state != "inspecting" or row.user_id is None:
        return None
    now = utcnow()
    if row.expires_at <= now or row.lease_until is None or row.lease_until <= now:
        return None
    return row


def _lost(factory, identifier):
    with factory() as db:
        row = db.query(ResumeUpload).filter_by(id=identifier).with_for_update().one_or_none()
        if row is not None:
            ensure_cleanup(db, row)
            db.commit()


def _failure(factory, identifier, token, *, refused=False):
    with factory() as db:
        row = db.query(ResumeUpload).filter_by(id=identifier).with_for_update().one_or_none()
        if row is None:
            return
        if row.lease_token == token and row.state == "inspecting":
            if row.user_id is None:
                row.state = "cancelled"
            elif row.expires_at <= utcnow():
                row.state = "expired"
            elif refused:
                row.state = "rejected"
            elif row.attempt_count >= MAX_ATTEMPTS:
                row.state = "failed"
            else:
                row.state, row.next_attempt_at = "queued", utcnow() + timedelta(seconds=30)
            row.last_error_code = "resume_document_refused" if refused else "resume_inspection_unavailable"
            row.lease_token = row.lease_until = None
        if row.state not in ("queued", "inspecting", "awaiting_upload"):
            ensure_cleanup(db, row)
        db.commit()


def process(factory, identifier: str, store: ObjectStore) -> bool:
    if os.getenv("RESUME_DIRECT_UPLOAD_ENABLED", "false") != "true":
        return False
    token = uuid4().hex
    with factory() as db:
        row = db.query(ResumeUpload).filter_by(id=identifier).with_for_update().one_or_none()
        now = utcnow()
        if row is None or row.user_id is None or row.expires_at <= now or row.next_attempt_at > now:
            return False
        if row.state not in ("queued", "inspecting") or (row.state == "inspecting" and row.lease_until and row.lease_until > now):
            return False
        if row.attempt_count >= MAX_ATTEMPTS:
            row.state = "failed"
            ensure_cleanup(db, row)
            db.commit()
            return False
        row.state, row.lease_token, row.lease_until = "inspecting", token, now + timedelta(seconds=LEASE_SECONDS)
        row.attempt_count += 1
        target = SimpleNamespace(**{column.name: getattr(row, column.name) for column in ResumeUpload.__table__.columns})
        db.commit()
    try:
        info = store.inspect_object(target.quarantine_bucket, target.quarantine_name)
        if info is None:
            raise StorageUnavailable("upload_not_completed")
        if (info.retired or not valid_generation(info.generation) or info.size_bytes != target.size_bytes
                or info.media_type != target.media_type or info.encoding):
            raise StorageMismatch("source_binding_mismatch")
        with factory() as db:
            current = _current(db, identifier, token)
            if current is None:
                db.rollback()
                _lost(factory, identifier)
                return False
            if current.quarantine_generation is not None and current.quarantine_generation != info.generation:
                raise StorageMismatch("source_generation_mismatch")
            current.quarantine_generation = info.generation
            db.commit()
        content = store.read(target.quarantine_bucket, target.quarantine_name, info.generation, target.size_bytes)
        if len(content) != target.size_bytes or hashlib.sha256(content).hexdigest() != target.source_sha256:
            raise StorageMismatch("source_binding_mismatch")
        inspected = scanner.inspect(content, source_format=target.source_format)
        with factory() as db:
            current = _current(db, identifier, token)
            if current is None:
                db.rollback()
                _lost(factory, identifier)
                return False
            db.commit()
        # Targets were recorded before this write. Cleanup replaces either target
        # by a retained live generation, so a late generation0 write cannot resurrect it.
        generation = store.put_clean(target, content)
        if not valid_generation(generation):
            raise StorageMismatch("clean_generation_mismatch")
        with factory() as db:
            owner = db.query(core.User).filter_by(id=target.user_id).populate_existing().with_for_update().one_or_none()
            current = _current(db, identifier, token)
            if owner is None or current is None:
                db.rollback()
                _lost(factory, identifier)
                return False
            if current.quarantine_generation != info.generation:
                raise StorageMismatch("source_generation_mismatch")
            raw, sections, skills, years, contact = inspected.parsed
            resume = core.Resume(user_id=current.user_id, original_filename=current.original_filename,
                                 source_document=content, source_format=current.source_format, raw_text=raw,
                                 sections=sections, skills=skills, experience_years=years, contact_info=contact)
            db.add(resume)
            db.flush()
            current.result_resume_id, current.clean_generation, current.scan_receipt = resume.id, generation, inspected.receipt()
            current.state, current.completed_at, current.last_error_code = "released", utcnow(), None
            current.lease_token = current.lease_until = None
            ensure_cleanup(db, current)
            db.commit()
        return True
    except (scanner.ScanRefused, StorageMismatch):
        _failure(factory, identifier, token, refused=True)
    except (scanner.ScanUnavailable, StorageUnavailable):
        _failure(factory, identifier, token)
    return False


def process_due(factory, store: ObjectStore, *, limit: int = 10) -> int:
    if not 1 <= limit <= 10:
        raise ValueError("inspection_batch_limit")
    now = utcnow()
    with factory() as db:
        identifiers = [row.id for row in db.query(ResumeUpload).filter(
            ResumeUpload.state.in_(("queued", "inspecting")), ResumeUpload.next_attempt_at <= now,
            ResumeUpload.expires_at > now).order_by(ResumeUpload.next_attempt_at).limit(limit).all()]
    return sum(process(factory, identifier, store) for identifier in identifiers)
