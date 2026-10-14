"""Owner reports are history, never employer receipts or paid execution proof."""
from fastapi import HTTPException
from sqlalchemy.orm import Session

from ... import models as core
from ..browser_assistance.models import BrowserAssistanceReview
from ..common import utcnow
from . import models, schemas


def report(db: Session, user_id: int, identity: str, payload: schemas.ManualSubmissionReport):
    if db.query(core.User.id).filter_by(id=user_id).with_for_update().first() is None:
        raise HTTPException(401, "Not authenticated")
    application = db.query(models.EmployerApplication).filter_by(id=identity, user_id=user_id).populate_existing().with_for_update().first()
    if application is None:
        raise HTTPException(404, "Not found")
    if payload.package_digest != application.package_digest or payload.confirmed is not True:
        raise HTTPException(409, "Review the current saved package before reporting your manual submission.")
    if (application.application_mode != "manual" or application.credit_cost != 0 or application.charged_credits != 0
            or application.reservation_id is not None or application.batch_id is not None):
        raise HTTPException(409, "Only a manual application without an execution charge or reservation can be reported here.")
    if db.query(models.ServiceCreditReservation.id).filter_by(user_id=user_id, source_id=identity, operation="job_application").first() is not None:
        raise HTTPException(409, "An execution reservation must be reconciled through its original workflow.")
    if application.status == "user_reported":
        if (not isinstance(application.receipt, dict) or application.receipt.get("source") != "candidate_report"
                or application.receipt.get("saved_package_digest") != payload.package_digest):
            raise HTTPException(409, "The existing report could not be verified.")
        db.commit()
        return application
    if application.status not in {"needs_action", "ready", "approved", "manual_handoff"} or application.cancel_requested or application.receipt:
        raise HTTPException(409, "This application cannot be reported in its current state.")
    current = utcnow()
    application.receipt = {"source": "candidate_report", "reported_at": current.isoformat(), "actor_user_id": user_id,
                           "saved_package_digest": payload.package_digest, "employer_confirmed": False, "used_file_verified": False}
    application.status, application.cancel_requested = "user_reported", True
    application.approved_digest, application.allowed_actions = None, []
    application.error_code, application.error_message = None, None
    # Retain active_key: a finished opening cannot silently prepare another copy.
    db.query(models.EmployerApplicationApproval).filter_by(application_id=identity, revoked_at=None).update({"revoked_at": current}, synchronize_session=False)
    for review in db.query(BrowserAssistanceReview).filter_by(application_id=identity, user_id=user_id).filter(
            BrowserAssistanceReview.state.in_(["draft", "approved", "running"])).populate_existing().with_for_update():
        review.state, review.payload = "cancelled", {}
    db.commit()
    return application
