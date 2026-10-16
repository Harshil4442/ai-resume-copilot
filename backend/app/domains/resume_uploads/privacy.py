"""Durable exact-target cleanup and retained closure markers against late writes."""
from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..common import utcnow
from .models import ResumeUpload, ResumeUploadCleanup
from .storage import ObjectStore, StorageMismatch, StorageUnavailable, valid_generation


def _closed_marker(store: ObjectStore, bucket: str, name: str, generation: str, upload_id: str) -> None:
    """Fresh native facts, never a saved cleanup flag or an upload ACK alone."""
    info = store.inspect_object(bucket, name)
    if (not valid_generation(generation) or info is None or info.generation != generation
            or type(info.size_bytes) is not int or info.size_bytes != 0
            or info.retired is not True or info.retired_upload_id != upload_id
            or info.retired_metadata_only is not True
            or info.media_type != "application/octet-stream" or info.encoding is not None):
        raise StorageMismatch("cleanup_closure_unverified")


def ensure_cleanup(db: Session, row: ResumeUpload) -> ResumeUploadCleanup:
    # Callers serialize on the upload row; uniqueness is an additional guard.
    work = db.query(ResumeUploadCleanup).filter_by(upload_id=row.id).one_or_none()
    if work is None:
        work = ResumeUploadCleanup(id="ruc_" + row.id[4:], upload_id=row.id, next_attempt_at=utcnow())
        db.add(work)
    else:
        work.next_attempt_at, work.payload_retired_at = utcnow(), None
    return work


def sweep(db: Session, *, limit: int = 100) -> int:
    if not 1 <= limit <= 100:
        raise ValueError("cleanup_batch_limit")
    now = utcnow()
    rows = db.query(ResumeUpload).outerjoin(ResumeUploadCleanup, ResumeUploadCleanup.upload_id == ResumeUpload.id).filter(
        or_(ResumeUpload.user_id.is_(None),
            (ResumeUpload.state != "released") & (ResumeUpload.expires_at <= now),
            (ResumeUpload.state == "released") & ResumeUpload.result_resume_id.is_(None),
            ResumeUpload.state.in_(("cancelled", "expired", "failed", "rejected", "released"))),
        or_(ResumeUploadCleanup.id.is_(None), ResumeUploadCleanup.quarantine_closed_generation.is_(None),
            (ResumeUpload.state != "released") & ResumeUploadCleanup.clean_closed_generation.is_(None))
    ).order_by(ResumeUpload.created_at).with_for_update(of=ResumeUpload, skip_locked=True).limit(limit).all()
    for row in rows:
        if row.user_id is None or (row.state == "released" and row.result_resume_id is None):
            row.state, row.original_filename, row.enrich_skills, row.scan_receipt = "cancelled", None, False, None
            row.lease_token = row.lease_until = None
        elif row.state not in ("released", "cancelled", "expired", "failed", "rejected"):
            row.state, row.lease_token, row.lease_until = "expired", None, None
        # Existing failed work keeps its retry delay; this sweep never spins it.
        if db.query(ResumeUploadCleanup).filter_by(upload_id=row.id).one_or_none() is None:
            ensure_cleanup(db, row)
    db.commit()
    return len(rows)


def run_cleanup(factory, upload_id: str, store: ObjectStore) -> bool:
    token = uuid4().hex
    with factory() as db:
        row = db.query(ResumeUpload).filter_by(id=upload_id).with_for_update().one_or_none()
        work = db.query(ResumeUploadCleanup).filter_by(upload_id=upload_id).with_for_update().one_or_none()
        now = utcnow()
        if row is None or work is None or work.next_attempt_at > now or (work.lease_until and work.lease_until > now):
            return False
        live = row.state == "released" and row.user_id is not None and row.result_resume_id is not None
        orphaned = row.user_id is None
        if row.state in ("awaiting_upload", "queued", "inspecting") and row.user_id is not None and row.expires_at > now:
            return False
        work.lease_token, work.lease_until = token, now + timedelta(seconds=300)
        work.attempt_count = min(work.attempt_count + 1, 1000000)
        target = SimpleNamespace(id=row.id, quarantine_bucket=row.quarantine_bucket, quarantine_name=row.quarantine_name,
                                 clean_bucket=row.clean_bucket, clean_name=row.clean_name)
        db.commit()
    try:
        quarantine = store.retire(target.quarantine_bucket, target.quarantine_name, target.id)
        clean = None if live else store.retire(target.clean_bucket, target.clean_name, target.id)
        if orphaned:
            if clean is None:
                raise StorageMismatch("cleanup_closure_unverified")
            _closed_marker(store, target.quarantine_bucket, target.quarantine_name, quarantine, target.id)
            _closed_marker(store, target.clean_bucket, target.clean_name, clean, target.id)
    except (StorageUnavailable, StorageMismatch):
        with factory() as db:
            work = db.query(ResumeUploadCleanup).filter_by(upload_id=upload_id, lease_token=token).with_for_update().one_or_none()
            if work:
                work.lease_token = work.lease_until = None
                work.last_error_code, work.next_attempt_at = "cleanup_unavailable", utcnow() + timedelta(seconds=60)
                db.commit()
        return False
    with factory() as db:
        # Same Upload->Cleanup order as admission/unlink. Deletion during the
        # external quarantine write may have rearmed immediate clean cleanup.
        row = db.query(ResumeUpload).filter_by(id=upload_id).with_for_update().one_or_none()
        work = db.query(ResumeUploadCleanup).filter_by(upload_id=upload_id, lease_token=token).with_for_update().one_or_none()
        if row is None or work is None:
            return False
        still_live = row.state == "released" and row.user_id is not None and row.result_resume_id is not None
        if orphaned and row.user_id is None:
            # Markers remain live at both original names. The SQL rows are no
            # longer needed to block old generation0 capabilities, and retaining
            # their document/request fingerprints serves no account purpose.
            db.delete(work)
            db.delete(row)
            db.commit()
            return True
        work.quarantine_closed_generation = quarantine
        if clean is not None:
            work.clean_closed_generation, work.payload_retired_at = clean, utcnow()
        work.last_error_code = None
        work.lease_token = work.lease_until = None
        # Deletion may win during the external calls. A later tick must obtain
        # fresh proof for the newly orphaned row before purging its fingerprints.
        work.next_attempt_at = utcnow() if row.user_id is None or (clean is None and not still_live) else utcnow() + timedelta(hours=24)
        db.commit()
    return True


def cleanup_due(factory, store: ObjectStore, *, limit: int = 100) -> int:
    if not 1 <= limit <= 100:
        raise ValueError("cleanup_batch_limit")
    with factory() as db:
        now = utcnow()
        identifiers = [row.upload_id for row in db.query(ResumeUploadCleanup).filter(
            ResumeUploadCleanup.next_attempt_at <= now,
            or_(ResumeUploadCleanup.lease_until.is_(None), ResumeUploadCleanup.lease_until <= now)
        ).order_by(ResumeUploadCleanup.next_attempt_at).limit(limit).all()]
    return sum(run_cleanup(factory, identifier, store) for identifier in identifiers)
