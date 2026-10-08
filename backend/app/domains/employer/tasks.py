"""Durable bounded workers with no transaction held across external calls."""
from __future__ import annotations

import copy
import logging
import os
from datetime import timedelta
from types import SimpleNamespace

from ...database import SessionLocal
from ...services.market.skill_extractor import extract_skills_from_text
from ..common import public_id, utcnow
from . import artifacts, config, connectors, credits, models, service

log = logging.getLogger("hirewiz.artifact_cleanup")


def execute_search(search_id: str) -> str:
    # Search is one short atomic index transaction; no dispatch is necessary.
    with SessionLocal() as db:
        search = db.query(models.EmployerSearch).filter_by(id=search_id).first()
        return search.status if search else "missing"


def refresh_source(source_id: str) -> str:
    with SessionLocal() as db:
        source = db.query(models.EmployerSource).filter_by(id=source_id).with_for_update().first()
        if not source:
            return "missing"
        if not source.enabled or not config.discovery_enabled():
            return "disabled"
        if source.scan_token and source.scan_started_at and service._utc(source.scan_started_at) > utcnow() - timedelta(minutes=5):
            return "running"
        token = public_id("scan")
        source.scan_token, source.scan_started_at = token, utcnow()
        contract = connectors.source_contract(source)
        db.commit()
    try:
        postings = connectors.fetch_postings(contract)
    except connectors.ConnectorError as exc:
        with SessionLocal() as db:
            source = db.query(models.EmployerSource).filter_by(id=source_id, scan_token=token).with_for_update().first()
            if source:
                source.status = "unavailable"
                source.last_error_code = exc.code
                source.next_refresh_at = utcnow() + timedelta(minutes=15)
                source.scan_token, source.scan_started_at = None, None
                db.commit()
        raise
    with SessionLocal() as db:
        source = db.query(models.EmployerSource).filter_by(id=source_id, scan_token=token).with_for_update().first()
        if not source or not source.enabled:
            return "superseded"
        checked_at = utcnow()
        existing = {row.external_id: row for row in db.query(models.EmployerPosting).filter_by(source_id=source_id).all()}
        seen = set()
        for data in postings:
            identity = data["external_id"]
            seen.add(identity)
            row = existing.get(identity)
            if not row:
                row = models.EmployerPosting(id=public_id("job"), source_id=source_id, **data)
                db.add(row)
            else:
                for key, value in data.items():
                    # A list endpoint may omit a publication date present in a detail endpoint.
                    if key == "publication_at" and value is None:
                        continue
                    setattr(row, key, value)
            row.skills = sorted(extract_skills_from_text(data["description"])[0])
            row.is_open, row.closed_at, row.last_checked_at = True, None, checked_at
        # Only a fully parsed, bounded, completed provider scan can close jobs.
        for identity, row in existing.items():
            if identity not in seen and row.is_open:
                row.is_open, row.closed_at, row.last_checked_at = False, checked_at, checked_at
        source.status, source.last_error_code = "healthy", None
        source.last_success_at = checked_at
        source.next_refresh_at = checked_at + timedelta(hours=6)
        source.scan_token, source.scan_started_at = None, None
        db.commit()
        return "completed"


def _settle_failure(db, application, code, message):
    application.status = "failed"
    application.error_code, application.error_message = code, message
    application.active_key = None
    application.approved_digest = None
    if application.reservation_id:
        reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
        credits.settle(db, reservation, completed_count=0, reason=message)


def _mark_unknown(db, application, code="submission_outcome_unknown"):
    application.status = "unknown"
    application.error_code = code
    application.error_message = "The employer may have received this application. Credits remain reserved while the outcome is verified; no automatic retry will send it again."


def execute_application(application_id: str) -> str:
    with SessionLocal() as db:
        application = db.query(models.EmployerApplication).filter_by(id=application_id).with_for_update().first()
        if not application:
            return "missing"
        if application.status == "submitting":
            # Redelivery after a lost worker cannot establish whether the POST happened.
            _mark_unknown(db, application)
            db.commit()
            return "unknown"
        if application.status != "queued":
            return application.status
        posting, source = service._posting(db, application.posting_id), None
        source = service._source(db, posting.source_id)
        artifact = service._owned(db, models.SealedApplicationArtifact, application.artifact_id, application.user_id)
        if application.cancel_requested or service._source_mode(source, application.form) != "api":
            _settle_failure(db, application, "execution_not_authorized", "Submission was stopped before sending; reserved credits were returned.")
            db.commit()
            return "failed"
        contract = connectors.source_contract(source)
        external_id = posting.external_id
        digest = application.package_digest
        artifact_snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(artifact, key)) for key in
                                              ("sha256", "size_bytes", "gcs_object", "gcs_generation", "content", "filename", "media_type")})
        db.rollback()
    try:
        current_form = connectors.load_form(contract, external_id)
        content = artifacts.read(artifact_snapshot)
    except Exception as exc:
        with SessionLocal() as db:
            application = db.query(models.EmployerApplication).filter_by(id=application_id).with_for_update().first()
            if application and application.status == "queued":
                _settle_failure(db, application, getattr(exc, "code", "preflight_failed"), "Could not verify the employer form or saved resume before sending; credits were returned.")
                db.commit()
                return "failed"
        return "superseded"
    with SessionLocal() as db:
        application = db.query(models.EmployerApplication).filter_by(id=application_id).with_for_update().first()
        if not application or application.status != "queued":
            return application.status if application else "missing"
        posting = service._posting(db, application.posting_id)
        source = service._source(db, posting.source_id)
        artifact = service._owned(db, models.SealedApplicationArtifact, application.artifact_id, application.user_id)
        valid = (
            config.discovery_enabled() and config.submit_enabled() and
            application.package_digest == digest and application.approved_digest == digest and
            not application.cancel_requested and posting.is_open and source.enabled and
            application.approval_expires_at and service._utc(application.approval_expires_at) > utcnow() and
            {"upload", "submit"}.issubset(application.allowed_actions or []) and
            service._source_mode(source, application.form) == "api" and
            posting.content_sha256 == application.job_content_sha256 and
            current_form["version"] == application.form["version"] and current_form.get("supported") and
            (not current_form.get("job_content_sha256") or current_form["job_content_sha256"] == application.job_content_sha256) and
            service._package_digest(application, posting, source, artifact) == digest and
            not service.missing_fields(application)
        )
        if not valid:
            _settle_failure(db, application, "review_expired_or_changed", "The job, form, permission or approved package changed before sending. Review a new application; credits were returned.")
            db.commit()
            return "failed"
        # Tailored facts must still be approved. This reads only; no generation/rendering.
        try:
            selection = SimpleNamespace(resume_id=application.resume_id, resume_choice=application.resume_choice,
                                        resume_version_id=application.resume_version_id)
            service._selection_snapshot(db, application.user_id, selection)
            service._validate_answers(application.form, application.answers, application.consents)
        except Exception:
            _settle_failure(db, application, "candidate_data_changed", "Candidate resume or evidence approval changed before sending; credits were returned.")
            db.commit()
            return "failed"
        credential = os.getenv(source.credential_env, "")
        if not credential:
            _settle_failure(db, application, "credential_unavailable", "The employer submission credential is unavailable; credits were returned.")
            db.commit()
            return "failed"
        attempt = models.EmployerApplicationAttempt(id=public_id("attempt"), application_id=application.id,
            launch_token=public_id("launch"), package_digest=digest)
        db.add(attempt)
        application.status = "submitting"
        attempt_id = attempt.id
        answers = copy.deepcopy(application.answers)
        form_snapshot = copy.deepcopy(application.form)
        db.commit()  # Persist the launch permit before the only external POST.
    status, receipt, failure = None, None, None
    try:
        status, receipt = connectors.submit_greenhouse(contract, external_id, credential=credential,
            answers=answers, content=content, filename=artifact_snapshot.filename, media_type=artifact_snapshot.media_type,
            form=form_snapshot)
    except connectors.ConnectorError as exc:
        failure = exc.code
    except Exception:
        failure = "submission_outcome_unknown"
    with SessionLocal() as db:
        application = db.query(models.EmployerApplication).filter_by(id=application_id).with_for_update().first()
        attempt = db.query(models.EmployerApplicationAttempt).filter_by(id=attempt_id).first()
        if not application or not attempt:
            return "missing"
        attempt.response_status, attempt.completed_at = status, utcnow()
        if receipt:
            # A verified late receipt can resolve an uncertain/cancelled-in-flight send.
            application.status = "confirmed"
            application.receipt, attempt.receipt = receipt, receipt
            application.error_code, application.error_message = None, None
            attempt.state = "confirmed"
            reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
            credits.settle(db, reservation, completed_count=1, reason="Verified complete employer application")
            application.charged_credits = reservation.committed_amount
        elif failure and failure.startswith("submission_rejected_"):
            attempt.state, attempt.error_code = "failed", failure
            _settle_failure(db, application, failure, "The employer rejected the request before application completion; credits were returned.")
        else:
            attempt.state, attempt.error_code = "unknown", failure or "receipt_not_verified"
            _mark_unknown(db, application, failure or "receipt_not_verified")
        db.commit()
        return application.status


def fail_dispatched_work(topic: str, identity: str) -> None:
    with SessionLocal() as db:
        if topic == "employer.refresh":
            source = db.query(models.EmployerSource).filter_by(id=identity).with_for_update().first()
            if source:
                source.status, source.last_error_code = "unavailable", "dispatch_attempts_exhausted"
                source.scan_token, source.scan_started_at = None, None
                source.next_refresh_at = utcnow() + timedelta(minutes=30)
        elif topic == "employer.apply":
            application = db.query(models.EmployerApplication).filter_by(id=identity).with_for_update().first()
            if application:
                has_launch = db.query(models.EmployerApplicationAttempt.id).filter_by(application_id=identity).first()
                if application.status in {"queued", "submitting"}:
                    if has_launch:
                        _mark_unknown(db, application)
                    else:
                        _settle_failure(db, application, "dispatch_attempts_exhausted", "Submission could not be started; credits were returned.")
        elif topic == "employer.artifact-delete":
            deletion = db.query(models.EmployerArtifactDeletion).filter_by(id=identity).with_for_update().first()
            if deletion and not deletion.completed_at:
                deletion.last_error_code = "task_delivery_exhausted"
                deletion.next_attempt_at = max(service._utc(deletion.next_attempt_at), utcnow() + timedelta(minutes=5))
                log.warning("Private artifact cleanup delivery exhausted deletion=%s attempts=%s", identity, deletion.attempt_count)
        db.commit()


def delete_artifact(deletion_id: str) -> str:
    with SessionLocal() as db:
        deletion = db.query(models.EmployerArtifactDeletion).filter_by(id=deletion_id).with_for_update().first()
        if not deletion:
            return "missing"
        if deletion.completed_at:
            return "completed"
        if service._utc(deletion.next_attempt_at) > utcnow():
            return "deferred"
        object_uri, generation = deletion.gcs_object, deletion.gcs_generation
        deletion.attempt_count += 1
        deletion.next_attempt_at = utcnow() + timedelta(seconds=min(60 * 2 ** min(deletion.attempt_count - 1, 9), 21600))
        db.commit()
    from google.api_core.exceptions import NotFound
    from google.cloud import storage
    from google.cloud.storage.retry import DEFAULT_RETRY
    request_options = {"timeout": 30, "retry": DEFAULT_RETRY.with_deadline(60)}
    try:
        if not object_uri.startswith("gs://"):
            raise ValueError("invalid_artifact_object")
        bucket, path = object_uri[5:].split("/", 1)
        segments = path.split("/")
        if len(segments) < 3 or segments[0] != "applications" or not segments[1].isdigit() or any(part in {"", ".", ".."} for part in segments):
            raise ValueError("invalid_artifact_object")
        if _artifact_referenced(object_uri, generation):
            return _complete_deletion(deletion_id, object_uri, generation, retained=True)
        if generation == "0":
            blob = storage.Client().bucket(bucket).blob(path)
            try:
                blob.reload(**request_options)
            except NotFound:
                return _confirm_absence(deletion_id, object_uri, generation)
            found_generation = str(blob.generation)
            with SessionLocal() as db:
                row = db.query(models.EmployerArtifactDeletion).filter_by(id=deletion_id).with_for_update().first()
                if not row or row.completed_at or row.gcs_object != object_uri or row.gcs_generation != generation:
                    return "superseded"
                row.gcs_generation = found_generation
                db.commit()
            generation = found_generation
        if _artifact_referenced(object_uri, generation):
            return _complete_deletion(deletion_id, object_uri, generation, retained=True)
        try:
            storage.Client().bucket(bucket).blob(path, generation=int(generation)).delete(if_generation_match=int(generation), **request_options)
        except NotFound:
            pass  # This exact generation is already absent.
    except Exception as exc:
        with SessionLocal() as db:
            deletion = db.query(models.EmployerArtifactDeletion).filter_by(id=deletion_id).with_for_update().first()
            if deletion and not deletion.completed_at:
                deletion.last_error_code = type(exc).__name__[:80]
                db.commit()
        log.warning("Private artifact cleanup deferred deletion=%s error=%s", deletion_id, type(exc).__name__)
        raise
    return _complete_deletion(deletion_id, object_uri, generation)


def _artifact_referenced(object_uri, generation):
    with SessionLocal() as db:
        query = db.query(models.SealedApplicationArtifact.id).filter_by(gcs_object=object_uri)
        if generation != "0":
            query = query.filter_by(gcs_generation=generation)
        return query.first() is not None


def _complete_deletion(deletion_id, object_uri, generation, *, retained=False):
    with SessionLocal() as db:
        deletion = db.query(models.EmployerArtifactDeletion).filter_by(id=deletion_id).with_for_update().first()
        if not deletion or deletion.gcs_object != object_uri or deletion.gcs_generation != generation:
            return "superseded"  # A late upload re-armed a different generation.
        deletion.completed_at = utcnow()
        deletion.last_error_code = "retained_active_reference" if retained else None
        deletion.gcs_object, deletion.gcs_generation = "retained" if retained else "deleted", "0"
        db.commit()
    return "completed"


def _confirm_absence(deletion_id, object_uri, generation):
    with SessionLocal() as db:
        deletion = db.query(models.EmployerArtifactDeletion).filter_by(id=deletion_id).with_for_update().first()
        if not deletion or deletion.completed_at or deletion.gcs_object != object_uri or deletion.gcs_generation != generation:
            return "superseded"
        if not deletion.absence_confirmed_at:
            deletion.absence_confirmed_at = utcnow()
            deletion.next_attempt_at = utcnow() + timedelta(minutes=15)
            deletion.last_error_code = "object_absence_unconfirmed"
            db.commit()
            return "deferred"
        if service._utc(deletion.absence_confirmed_at) + timedelta(minutes=15) > utcnow():
            return "deferred"
    return _complete_deletion(deletion_id, object_uri, generation)
