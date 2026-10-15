"""Immutable enumerated batch review; no blanket approval of future openings."""
from __future__ import annotations

import copy
from datetime import timedelta

from fastapi import HTTPException

from ..common import payload_fingerprint, public_id, utcnow
from . import admissions, config, models, schemas, service


def _owned(db, user_id, identity, *, lock=False):
    return service._owned(db, models.EmployerApplicationBatch, identity, user_id, lock=lock)


def _applications(db, user_id, items):
    # Stable lock ordering prevents two overlapping batches from cycling locks.
    return {identity: service._owned(db, models.EmployerApplication, identity, user_id, lock=True)
            for identity in sorted(item["application_id"] for item in items)}


def _locked_batch(db, user_id, identity):
    batch = _owned(db, user_id, identity)
    applications = _applications(db, user_id, batch.items)
    admissions._owner_lock(db, user_id)
    locked = _owned(db, user_id, identity, lock=True)
    # The initial unlocked read may be cached across another transaction's
    # cancellation. Refresh only the locked batch, preserving pending app bindings.
    db.refresh(locked)
    return locked, applications


def _check_limits(items, quoted_credits, max_total_credits, snapshot):
    current = config.admission_limits()
    if len(items) > min(snapshot["batch_limit"], current["batch_limit"]):
        raise HTTPException(422, {"code": "batch_count_limit", "message": "This batch exceeds the current reviewed application count limit."})
    if quoted_credits > min(max_total_credits, snapshot["batch_credit_limit"], current["batch_credit_limit"]):
        raise HTTPException(422, {"code": "batch_credit_limit", "message": "This batch exceeds its explicit credit budget or the current safety ceiling. Create a smaller reviewed batch."})


def _valid_batch(batch, digest):
    if batch.package_digest != digest:
        raise HTTPException(409, {"code": "batch_digest_changed", "message": "Review and approve this exact enumerated batch."})
    if batch.cancelled_at or admissions.aware(batch.expires_at) <= utcnow():
        raise HTTPException(409, {"code": "batch_expired_or_cancelled", "message": "This batch expired or was cancelled. Prepare a new review."})
    _check_limits(batch.items, batch.quoted_credits, batch.max_total_credits, batch.admission_snapshot)


def validate_binding(db, application):
    if not application.batch_id:
        return
    batch = _owned(db, application.user_id, application.batch_id)
    _valid_batch(batch, batch.package_digest)
    item = next((item for item in batch.items if item["application_id"] == application.id), None)
    if (batch.status not in {"approved", "queued"} or not item or
            item["package_digest"] != application.package_digest or
            item["credit_cost"] != application.credit_cost or
            item["opening_key"] != application.opening_key or
            item["allowed_actions"] != sorted(application.allowed_actions or [])):
        raise HTTPException(409, {"code": "batch_package_changed", "message": "This application no longer matches its approved batch. Save and review it again."})


def create(db, user_id, payload: schemas.BatchCreate):
    service._require_enabled()
    fingerprint = payload_fingerprint(payload.model_dump(exclude={"idempotency_key"}))
    existing = db.query(models.EmployerApplicationBatch).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if existing:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key belongs to another batch")
        return existing
    items = [item.model_dump() for item in payload.items]
    applications = _applications(db, user_id, items)
    admissions._owner_lock(db, user_id)
    existing = db.query(models.EmployerApplicationBatch).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if existing:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key belongs to another batch")
        return existing
    snapshot, total, openings = config.admission_limits(), 0, set()
    for item in items:
        app = applications[item["application_id"]]
        if (app.status not in {"ready", "approved"} or app.application_mode != "api" or
                app.package_digest != item["package_digest"] or service.missing_fields(app)):
            raise HTTPException(409, {"code": "batch_application_not_ready", "message": "Every batch item must be a complete, unchanged application supported by a permissioned API. Unsupported portals use individual manual handoff."})
        if not {"upload", "submit"}.issubset(item["allowed_actions"]):
            raise HTTPException(422, "Each automatic batch item requires explicit upload and submit approval")
        posting = service._posting(db, app.posting_id)
        source = service._source(db, posting.source_id)
        admissions._policies(app, source, utcnow())
        if app.opening_key in openings:
            raise HTTPException(409, {"code": "canonical_opening_conflict", "message": "A batch cannot include multiple representations of the same opening."})
        openings.add(app.opening_key)
        item.update({"credit_cost": app.credit_cost, "opening_key": app.opening_key,
                     "employer_key": app.employer_key, "pricing_snapshot": copy.deepcopy(app.pricing_snapshot),
                     "admission_snapshot": copy.deepcopy(app.admission_snapshot)})
        item["allowed_actions"] = sorted(set(item["allowed_actions"]))
        total += app.credit_cost
    _check_limits(items, total, payload.max_total_credits, snapshot)
    batch = models.EmployerApplicationBatch(
        id=public_id("batch"), user_id=user_id, idempotency_key=payload.idempotency_key,
        input_fingerprint=fingerprint, items=items, admission_snapshot=snapshot,
        quoted_credits=total, max_total_credits=payload.max_total_credits, status="quoted",
        expires_at=utcnow() + timedelta(hours=snapshot["quote_hours"]),
        package_digest=payload_fingerprint({"items": items, "admission_snapshot": snapshot,
                                           "max_total_credits": payload.max_total_credits}),
    )
    db.add(batch)
    db.commit()
    return batch


def approve(db, user_id, identity, payload: schemas.ExecuteCreate):
    batch, applications = _locked_batch(db, user_id, identity)
    _valid_batch(batch, payload.package_digest)
    if batch.status in {"approved", "queued"}:
        return batch
    if batch.status != "quoted":
        raise HTTPException(409, "This batch can no longer be approved")
    try:
        for item in batch.items:
            app = applications[item["application_id"]]
            if app.batch_id and app.batch_id != batch.id:
                raise HTTPException(409, "An application is already bound to another reviewed batch")
            app.batch_id = batch.id
            service.approve_application(db, user_id, app.id, schemas.ApprovalCreate(
                package_digest=item["package_digest"], allowed_actions=item["allowed_actions"]), commit=False)
        batch.status, batch.approved_at = "approved", utcnow()
        db.commit()
        return batch
    except Exception:
        db.rollback()
        raise


def execute(db, user_id, identity, payload: schemas.ExecuteCreate):
    batch, applications = _locked_batch(db, user_id, identity)
    _valid_batch(batch, payload.package_digest)
    if batch.status == "queued":
        return batch
    if batch.status != "approved":
        raise HTTPException(409, "Approve this exact batch before execution")
    try:
        for item in batch.items:
            app = applications[item["application_id"]]
            if app.batch_id != batch.id:
                raise HTTPException(409, "A batch application was changed; review a new batch")
            service.request_execution(db, user_id, app.id,
                                      schemas.ExecuteCreate(package_digest=item["package_digest"]), commit=False)
        batch.status = "queued"
        db.commit()
        return batch
    except Exception:
        db.rollback()  # No partial credit/admission/outbox acceptance.
        raise


def cancel(db, user_id, identity):
    batch, applications = _locked_batch(db, user_id, identity)
    for app in applications.values():
        if app.batch_id == batch.id and app.status != "confirmed":
            service.cancel_application(db, user_id, app.id, commit=False)
    batch.status, batch.cancelled_at = "cancelled", utcnow()
    db.commit()
    return batch


def response(db, batch):
    result = {key: getattr(batch, key) for key in (
        "id", "status", "package_digest", "items", "admission_snapshot", "quoted_credits",
        "max_total_credits", "created_at", "expires_at", "approved_at", "cancelled_at",
    )}
    result["application_statuses"] = {item["application_id"]: service._owned(
        db, models.EmployerApplication, item["application_id"], batch.user_id).status for item in batch.items}
    return result
