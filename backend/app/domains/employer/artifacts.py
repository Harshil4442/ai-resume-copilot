"""Seal once, preview and transmit the same bytes; private immutable GCS."""
from __future__ import annotations

import hashlib
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta

from fastapi import HTTPException

from ...services.resume_artifacts import ResumeArtifactError, render_resume_version
from ...services.resume_layout import ResumeLayoutError
from ..common import public_id, utcnow

UPLOAD_LEASE_SECONDS = 600


@dataclass(frozen=True)
class SealedBytes:
    content: bytes
    sha256: str
    filename: str
    media_type: str
    gcs_object: str | None = None
    gcs_generation: str | None = None


def materialize(resume, version=None) -> SealedBytes:
    if not resume.source_document or resume.source_format not in {"pdf", "docx", "tex", "texzip"}:
        raise HTTPException(422, "Upload the original PDF, DOCX or supported native TeX project to preserve its format")
    if version:
        artifact = render_resume_version(version, resume, "pdf" if resume.source_format in {"tex", "texzip"} else resume.source_format)
        content, filename, media_type = artifact.content, artifact.filename, artifact.media_type
    else:
        content = bytes(resume.source_document)
        filename = re.sub(r"[^A-Za-z0-9._-]", "-", resume.original_filename or f"resume.{resume.source_format}")[:200]
        media_type = "application/pdf" if resume.source_format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if version is None and resume.source_format in {"tex", "texzip"}:
        from ...services.native_tex import prepare_artifact, sealed_bytes
        try:
            seal = prepare_artifact(content, resume.source_format, [])
            content = sealed_bytes(seal, content, resume.source_format, [], "pdf")
        except ResumeLayoutError as exc:
            raise HTTPException(422, str(exc)) from exc
        filename = re.sub(r"\.(?:tex|zip)$", ".pdf", filename, flags=re.I)
        media_type = "application/pdf"
    if not 0 < len(content) <= 10 * 1024 * 1024:
        raise HTTPException(422, "Application resume must be nonempty and at most 10 MB")
    return SealedBytes(content, hashlib.sha256(content).hexdigest(), filename, media_type)


def materialize_for_application(resume, version=None) -> SealedBytes:
    """Translate document refusal at the application boundary without changing it."""
    try:
        return materialize(resume, version)
    except (ResumeArtifactError, ResumeLayoutError) as exc:
        raise HTTPException(409, str(exc) + " Choose your original/custom uploaded resume, or generate a new version and review it again.") from exc


def store(sealed: SealedBytes, *, user_id: int, object_name: str | None = None) -> SealedBytes:
    bucket_name = (os.getenv("EMPLOYER_ARTIFACT_BUCKET") or "").strip()
    if not bucket_name:
        if (os.getenv("APP_ENV") or "production").lower() not in {"development", "dev", "local", "test"}:
            raise HTTPException(503, "Private application artifact storage is not configured")
        return sealed
    from google.api_core.exceptions import PreconditionFailed
    from google.cloud import storage
    from google.cloud.storage.retry import DEFAULT_RETRY

    client = storage.Client()
    blob = client.bucket(bucket_name).blob(object_name or f"applications/{user_id}/{sealed.sha256}")
    blob.metadata = {"sha256": sealed.sha256}
    # The cleanup lease exceeds this bounded request/retry window.
    request_options = {"timeout": 30, "retry": DEFAULT_RETRY.with_deadline(60)}
    try:
        blob.upload_from_string(sealed.content, content_type=sealed.media_type, if_generation_match=0, **request_options)
    except PreconditionFailed:
        blob.reload(**request_options)
        existing = blob.download_as_bytes(if_generation_match=int(blob.generation), **request_options)
        if hashlib.sha256(existing).hexdigest() != sealed.sha256:
            raise HTTPException(503, "Application artifact integrity verification failed") from None
    return SealedBytes(sealed.content, sealed.sha256, sealed.filename, sealed.media_type,
                       f"gs://{bucket_name}/{blob.name}", str(blob.generation))


@contextmanager
def guarded_storage(db, sealed: SealedBytes, *, user_id: int):
    """Keep cleanup durable through crashes, rejected attachments and owner deletion.

    New uploads have distinct paths. Legacy hash-deduplicated objects are still
    protected by a reference check in the purge worker.
    """
    bucket = (os.getenv("EMPLOYER_ARTIFACT_BUCKET") or "").strip()
    if not bucket:
        yield store(sealed, user_id=user_id), None
        return
    from ...models import User
    from .models import EmployerArtifactUpload
    from .privacy import abandon_upload

    owner = db.query(User).filter_by(id=user_id).with_for_update().first()
    if not owner:
        raise HTTPException(404, "Account no longer exists")
    identity = public_id("upload")
    object_name = f"applications/{user_id}/{identity}/{sealed.sha256}"
    object_uri = f"gs://{bucket}/{object_name}"
    guard = EmployerArtifactUpload(id=identity, user_id=user_id, gcs_object=object_uri,
                                  lease_until=utcnow() + timedelta(seconds=UPLOAD_LEASE_SECONDS))
    db.add(guard)
    db.commit()  # The target is discoverable even if the process dies inside GCS.
    stored = None
    try:
        guard = db.query(EmployerArtifactUpload).filter_by(id=identity, user_id=user_id).first()
        if not guard or _aware(guard.lease_until) <= utcnow():
            raise HTTPException(409, "Application upload was revoked or expired")
        db.rollback()
        stored = store(sealed, user_id=user_id, object_name=object_name)
        yield stored, identity
    finally:
        db.rollback()
        # A late upload can re-arm a purge even after an earlier missing-object check.
        abandon_upload(db, identity, object_uri, stored.gcs_generation if stored else None,
                       upload_finished=stored is not None)
        db.commit()


def _aware(value):
    from datetime import UTC
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def attach_upload(db, identity: str | None, *, user_id: int) -> None:
    if identity is None:
        return
    from ...models import User
    from .models import EmployerArtifactUpload
    owner = db.query(User).filter_by(id=user_id).with_for_update().first()
    guard = db.query(EmployerArtifactUpload).filter_by(id=identity, user_id=user_id).with_for_update().first()
    if not owner or not guard or _aware(guard.lease_until) <= utcnow():
        raise HTTPException(409, "Account or application upload was revoked or expired")
    db.delete(guard)  # Consumed in the same transaction that attaches the artifact.


def read(artifact) -> bytes:
    if artifact.gcs_object:
        from google.cloud import storage
        bucket_name, object_name = artifact.gcs_object[5:].split("/", 1)
        blob = storage.Client().bucket(bucket_name).blob(object_name, generation=int(artifact.gcs_generation))
        content = blob.download_as_bytes(if_generation_match=int(artifact.gcs_generation))
    else:
        content = bytes(artifact.content or b"")
    if len(content) != artifact.size_bytes or hashlib.sha256(content).hexdigest() != artifact.sha256:
        raise HTTPException(409, "The saved application resume failed integrity verification")
    return content
