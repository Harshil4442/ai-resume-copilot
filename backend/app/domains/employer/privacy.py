"""Immediate execution revocation and durable private-object cleanup."""
from __future__ import annotations

import logging
from datetime import UTC, timedelta

from sqlalchemy.orm import Session

from ..common import public_id, utcnow
from . import admissions, credits, models

log = logging.getLogger("hirewiz.artifact_cleanup")


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _purge_upload(db, upload, now=None):
    deletion = models.EmployerArtifactDeletion(
        id="purge_" + upload.id, gcs_object=upload.gcs_object,
        gcs_generation=upload.gcs_generation or "0", next_attempt_at=now or utcnow(),
    )
    db.add(deletion)
    db.delete(upload)
    return deletion


def abandon_upload(db, identity, object_uri, generation, *, upload_finished):
    """Retain/re-arm cleanup after a failed or late attachment, without network IO."""
    if db.query(models.SealedApplicationArtifact.id).filter_by(gcs_object=object_uri).first():
        return
    upload = db.query(models.EmployerArtifactUpload).filter_by(id=identity).with_for_update().first()
    if upload:
        if generation:
            upload.gcs_generation = generation
        if upload_finished:
            upload.lease_until = utcnow()
        if _aware(upload.lease_until) <= utcnow():
            _purge_upload(db, upload)
    else:
        deletion = db.query(models.EmployerArtifactDeletion).filter_by(id="purge_" + identity).with_for_update().first()
        if deletion:
            deletion.gcs_object = object_uri
            deletion.gcs_generation = generation or "0"
            deletion.completed_at = deletion.absence_confirmed_at = None
            deletion.next_attempt_at = utcnow() if upload_finished else utcnow() + timedelta(minutes=10)
            deletion.last_error_code = "late_upload_cleanup"
    db.flush()
    enqueue_artifact_cleanup(db)


def enqueue_artifact_cleanup(db: Session, limit: int = 100) -> int:
    """Maintenance recovers expired uploads and every unfinished purge indefinitely.

    Task deliveries remain bounded; new outbox events recover exhausted groups
    after the recorded backoff. Retry metadata remains visible to operators.
    """
    from ..dispatch.models import DispatchOutbox
    from ..dispatch.service import enqueue
    now = utcnow()
    uploads = db.query(models.EmployerArtifactUpload).filter(
        models.EmployerArtifactUpload.lease_until <= now,
    ).with_for_update(skip_locked=True).limit(min(limit, 500)).all()
    for upload in uploads:
        _purge_upload(db, upload, now)
    db.flush()
    due = db.query(models.EmployerArtifactDeletion).filter(
        models.EmployerArtifactDeletion.completed_at.is_(None),
        models.EmployerArtifactDeletion.next_attempt_at <= now,
    ).with_for_update(skip_locked=True).limit(min(limit, 500)).all()
    queued = 0
    for deletion in due:
        active = db.query(DispatchOutbox.id).filter_by(topic="employer.artifact-delete", aggregate_id=deletion.id).filter(
            DispatchOutbox.status.notin_(["failed", "completed"]),
        ).first()
        if active:
            continue
        enqueue(db, topic="employer.artifact-delete", aggregate_id=deletion.id,
                payload={"deletion_id": deletion.id},
                key=f"employer.artifact-delete:{deletion.id}:{deletion.attempt_count}:{int(now.timestamp()) // 300}")
        queued += 1
        if deletion.attempt_count:
            log.warning("Private artifact cleanup recovered deletion=%s attempts=%s error=%s",
                        deletion.id, deletion.attempt_count, deletion.last_error_code)
    db.flush()
    return queued


def export_account_data(db: Session, user_id: int) -> dict:
    from .service import application_response, search_response
    applications = db.query(models.EmployerApplication).filter_by(user_id=user_id).all()
    searches = db.query(models.EmployerSearch).filter_by(user_id=user_id).all()
    events = db.query(models.ServiceCreditEvent).filter_by(user_id=user_id).all()
    return {
        "employer_searches": [search_response(row) for row in searches],
        "employer_applications": [application_response(db, row) for row in applications],
        "service_credit_events": [{key: getattr(row, key) for key in (
            "id", "event_type", "amount", "balance_after", "source_type", "source_id", "reason", "created_at",
        )} for row in events],
        "employer_application_batches": [{key: getattr(row, key) for key in (
            "id", "status", "package_digest", "items", "admission_snapshot", "quoted_credits", "max_total_credits", "created_at", "expires_at",
        )} for row in db.query(models.EmployerApplicationBatch).filter_by(user_id=user_id).all()],
        "employer_admissions": [{key: getattr(row, key) for key in (
            "id", "application_id", "state", "policy_snapshot", "admitted_at", "possible_send_at", "settled_at",
        )} for row in db.query(models.EmployerAdmission).filter_by(user_id=user_id).all()],
    }


def delete_account_data(db: Session, user_id: int) -> None:
    """Within the caller's transaction, fence later launches and remove PII.

    A launch already committed is in flight and cannot be recalled. The
    caller must commit this revocation before acknowledging account deletion.
    Financial credit movements remain with the customer identity removed.
    """
    from ..dispatch.service import enqueue
    admissions.lock_application_set(db, user_id)
    applications = db.query(models.EmployerApplication).filter_by(user_id=user_id).order_by(models.EmployerApplication.id).with_for_update().all()
    from ...models import User
    db.query(User).filter_by(id=user_id).with_for_update().first()
    ids = [row.id for row in applications]
    for application in applications:
        application.cancel_requested = True
        application.approved_digest = None
        if application.status not in {"submitting", "unknown", "confirmed"}:
            admissions.finish(db, application, "account_deleted")
        if application.reservation_id and application.status not in {"submitting", "unknown", "confirmed"}:
            reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
            credits.settle(db, reservation, completed_count=0, reason="Account deleted before application launch")
    db.flush()
    for artifact in db.query(models.SealedApplicationArtifact).filter_by(user_id=user_id).all():
        if artifact.gcs_object:
            deletion = models.EmployerArtifactDeletion(id=public_id("purge"), gcs_object=artifact.gcs_object,
                                                     gcs_generation=artifact.gcs_generation)
            db.add(deletion)
            enqueue(db, topic="employer.artifact-delete", aggregate_id=deletion.id,
                    payload={"deletion_id": deletion.id}, key=f"employer.artifact-delete:{deletion.id}")
    if ids:
        db.query(models.EmployerApplicationApproval).filter(models.EmployerApplicationApproval.application_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.EmployerApplicationAttempt).filter(models.EmployerApplicationAttempt.application_id.in_(ids)).delete(synchronize_session=False)
        db.query(models.EmployerAdmission).filter(models.EmployerAdmission.application_id.in_(ids)).update(
            {"application_id": None, "user_id": None, "active_key": None}, synchronize_session=False)
    db.query(models.EmployerApplication).filter_by(user_id=user_id).delete(synchronize_session=False)
    db.query(models.EmployerApplicationBatch).filter_by(user_id=user_id).delete(synchronize_session=False)
    db.query(models.SealedApplicationArtifact).filter_by(user_id=user_id).delete(synchronize_session=False)
    db.query(models.EmployerJobDelivery).filter_by(user_id=user_id).delete(synchronize_session=False)
    db.query(models.EmployerSearch).filter_by(user_id=user_id).delete(synchronize_session=False)
    db.query(models.ServiceCreditReservation).filter_by(user_id=user_id).update({"user_id": None}, synchronize_session=False)
    db.query(models.ServiceCreditEvent).filter_by(user_id=user_id).update({"user_id": None}, synchronize_session=False)
    db.query(models.EmployerSource).filter_by(verified_by=user_id).update({"verified_by": None}, synchronize_session=False)
    # Preserve guards for uploads already in flight. Their bounded lease gives
    # the write time to finish; attachment is fenced by removed ownership.
    db.query(models.EmployerArtifactUpload).filter_by(user_id=user_id).update({"user_id": None}, synchronize_session=False)
    db.flush()
