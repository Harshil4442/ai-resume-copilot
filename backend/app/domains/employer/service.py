from __future__ import annotations

import copy
import os
import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import func, literal_column, or_, text
from sqlalchemy.orm import Session

from ... import models as core
from ...services.basic_matching import basic_match
from ..common import payload_fingerprint, public_id, utcnow
from . import artifacts, config, connectors, credits, models, schemas

LOCKED_APPLICATION_STATES = {"queued", "submitting", "confirmed", "unknown", "failed"}


def _utc(value):
    return value.replace(tzinfo=UTC) if value and value.tzinfo is None else value


def _require_enabled():
    if not config.discovery_enabled():
        raise HTTPException(503, "Employer discovery is not enabled yet")


def _owned(db, cls, identity, user_id, lock=False):
    query = db.query(cls).filter(cls.id == identity, cls.user_id == user_id)
    row = (query.with_for_update() if lock else query).first()
    if not row:
        raise HTTPException(404, "Not found")
    return row


def _posting(db, identity):
    row = db.query(models.EmployerPosting).filter(models.EmployerPosting.id == identity).first()
    if not row:
        raise HTTPException(404, "Job not found")
    return row


def _source(db, identity):
    row = db.query(models.EmployerSource).filter(models.EmployerSource.id == identity).first()
    if not row:
        raise HTTPException(404, "Employer source not found")
    return row


def _source_mode(source, form=None):
    if (source.enabled and config.submit_enabled() and source.platform == "greenhouse" and
            source.submission_enabled and source.form_parity_verified and source.submission_grant and
            source.receipt_contract and source.credential_env and os.getenv(source.credential_env) and
            (form is None or form.get("supported"))):
        return "api"
    return "manual"


def source_response(source):
    return {key: getattr(source, key) for key in (
        "id", "employer", "platform", "region", "careers_url", "enabled", "status", "verified_at", "last_success_at", "next_refresh_at", "last_error_code",
    )} | {"application_mode": _source_mode(source)}


def catalog(db, user_id):
    user = db.query(core.User).filter(core.User.id == user_id).one()
    sources = db.query(models.EmployerSource).filter(models.EmployerSource.enabled.is_(True)).order_by(models.EmployerSource.employer).limit(500).all()
    return config.prices() | {
        "balance": int(user.job_service_credits or 0), "enabled": config.discovery_enabled(),
        "auto_submit_enabled": config.submit_enabled(), "sources": [source_response(source) for source in sources],
        "credit_policy": "New unique verified jobs are charged once. Automatic applications are charged only after a verified complete receipt. Promotional analysis units and Premium are separate.",
        "coverage": {"scope": "Verified enabled employer sources", "worldwide_recall_verified": False},
    }


def posting_response(posting, source):
    return {key: getattr(posting, key) for key in (
        "id", "source_id", "external_id", "title", "employer", "location", "description", "canonical_url", "apply_url", "publication_at", "first_seen_at", "last_checked_at", "remote", "is_open", "content_sha256",
    )} | {"platform": source.platform, "application_mode": _source_mode(source),
          "publication_kind": "release_or_republication" if source.platform == "smartrecruiters" else "publication" if posting.publication_at else "unknown"}


def _json_posting(posting, source):
    result = posting_response(posting, source)
    return {key: value.isoformat() if isinstance(value, datetime) else value for key, value in result.items()}


def create_search(db: Session, user_id: int, payload: schemas.SearchCreate):
    _require_enabled()
    prices = config.prices()
    if payload.desired_count > int(prices["max_search_jobs"]):
        raise HTTPException(422, "Requested job count exceeds the current service limit")
    # Serialize balance and per-user delivery/idempotency checks in one short transaction.
    db.query(core.User).filter(core.User.id == user_id).with_for_update().one()
    fingerprint = payload_fingerprint(payload.model_dump(exclude={"idempotency_key"}))
    existing = db.query(models.EmployerSearch).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if existing:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with different search settings")
        return existing
    resume = _owned(db, core.Resume, payload.resume_id, user_id)
    stale_before = utcnow() - timedelta(hours=config.positive_int("EMPLOYER_MAX_SOURCE_AGE_HOURS", 24, 168))
    query = db.query(models.EmployerPosting, models.EmployerSource).join(models.EmployerSource, models.EmployerSource.id == models.EmployerPosting.source_id).filter(
        models.EmployerPosting.is_open.is_(True), models.EmployerSource.enabled.is_(True),
        models.EmployerPosting.last_checked_at >= stale_before,
    )
    if db.get_bind().dialect.name == "postgresql":
        document = func.to_tsvector(text("'simple'::regconfig"),
            func.coalesce(models.EmployerPosting.title, literal_column("''")) + literal_column("' '") +
            func.coalesce(models.EmployerPosting.description, literal_column("''")))
        query = query.filter(document.op("@@")(func.plainto_tsquery(text("'simple'::regconfig"), payload.role)))
    # Every significant role token must occur in title or description. Bound
    # candidates before local scoring; scope explicitly reports this limit.
    tokens = list(dict.fromkeys(re.findall(r"[\w+#.-]+", payload.role.lower())))[:12]
    for token in tokens:
        escaped = token.replace("%", "\\%").replace("_", "\\_")
        query = query.filter(or_(models.EmployerPosting.title.ilike(f"%{escaped}%", escape="\\"),
                                 models.EmployerPosting.description.ilike(f"%{escaped}%", escape="\\")))
    if payload.location:
        escaped = payload.location.replace("%", "\\%").replace("_", "\\_")
        query = query.filter(models.EmployerPosting.location.ilike(f"%{escaped}%", escape="\\"))
    if payload.remote_only:
        query = query.filter(models.EmployerPosting.remote.is_(True))
    if payload.published_within_days:
        query = query.filter(models.EmployerPosting.publication_at >= utcnow() - timedelta(days=payload.published_within_days))
    if payload.excluded_employers:
        query = query.filter(func.lower(models.EmployerPosting.employer).notin_([name.lower() for name in payload.excluded_employers]))
    candidates = query.order_by(models.EmployerPosting.last_checked_at.desc(), models.EmployerPosting.id).limit(1001).all()
    truncated = len(candidates) > 1000
    ranked = [(posting, source, basic_match(resume.skills or [], posting.description, posting.title)) for posting, source in candidates[:1000]]
    ranked.sort(key=lambda item: (-item[2]["score"], -int(payload.role.lower() in item[0].title.lower()), item[0].id))
    selected = ranked[:payload.desired_count]
    previous = {row.posting_id for row in db.query(models.EmployerJobDelivery).filter_by(user_id=user_id).filter(models.EmployerJobDelivery.posting_id.in_([item[0].id for item in selected])).all()}
    new_count = sum(posting.id not in previous for posting, _, _ in selected)
    search_id = public_id("search")
    reservation = credits.reserve(db, user_id=user_id, operation="job_search", source_id=search_id,
                                  unit_price=int(prices["search_credits_per_job"]), count=payload.desired_count if new_count else 0)
    items, deliveries = [], []
    for posting, source, fit in selected:
        amount = 0 if posting.id in previous else reservation.unit_price
        items.append({"posting": _json_posting(posting, source), "fit": fit, "charged_credits": amount})
        if amount:
            deliveries.append(models.EmployerJobDelivery(id=public_id("delivery"), user_id=user_id, posting_id=posting.id, search_id=search_id, charged_credits=amount))
    search = models.EmployerSearch(
        id=search_id, user_id=user_id, resume_id=resume.id, idempotency_key=payload.idempotency_key,
        input_fingerprint=fingerprint, query=payload.model_dump(exclude={"idempotency_key"}),
        desired_count=payload.desired_count, delivered_count=len(items), items=items,
        reserved_credits=reservation.reserved_amount, charged_credits=new_count * reservation.unit_price,
        refunded_credits=reservation.reserved_amount - new_count * reservation.unit_price,
        scope={"kind": "verified_employer_index", "candidate_limit": 1000, "candidate_limit_reached": truncated,
               "max_source_age_hours": config.positive_int("EMPLOYER_MAX_SOURCE_AGE_HOURS", 24, 168),
               "worldwide_recall_verified": False, "unknown_publication_dates_excluded": bool(payload.published_within_days)},
    )
    db.add(search)
    db.flush([search])
    db.add_all(deliveries)
    credits.settle(db, reservation, completed_count=new_count, reason="Unique useful employer jobs delivered")
    db.commit()
    return search


def search_response(row):
    return {key: getattr(row, key) for key in (
        "id", "status", "desired_count", "delivered_count", "reserved_credits", "charged_credits", "refunded_credits", "created_at", "query", "items", "scope",
    )}


def list_searches(db, user_id, limit=30):
    return [search_response(row) for row in db.query(models.EmployerSearch).filter_by(user_id=user_id).order_by(models.EmployerSearch.created_at.desc()).limit(limit).all()]


def get_search(db, user_id, identity):
    return search_response(_owned(db, models.EmployerSearch, identity, user_id))


def _selection_snapshot(db, user_id, selection):
    resume = _owned(db, core.Resume, selection.resume_id, user_id)
    resume_snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(resume, key)) for key in ("id", "source_document", "source_format", "original_filename")})
    version_snapshot = None
    if selection.resume_choice == "tailored":
        version = _owned(db, core.ResumeVersion, selection.resume_version_id, user_id)
        if version.resume_id != resume.id or version.approval_state != "approved":
            raise HTTPException(422, "Select an approved tailored version of this resume")
        content = version.structured_content or {}
        if not content.get("source_edits"):
            raise HTTPException(422, "An unchanged source snapshot is not a tailored resume")
        for identity in version.evidence_ids or []:
            if not db.query(core.EvidenceItem.id).filter_by(id=identity, user_id=user_id, approval_state="approved").first():
                raise HTTPException(409, "Tailored resume evidence needs approval")
        version_snapshot = SimpleNamespace(**{key: copy.deepcopy(getattr(version, key)) for key in ("structured_content", "label", "version_number")})
    return resume_snapshot, version_snapshot


def _artifact_row(sealed, user_id, selection):
    return models.SealedApplicationArtifact(
        id=public_id("artifact"), user_id=user_id, resume_id=selection.resume_id,
        resume_version_id=selection.resume_version_id, sha256=sealed.sha256,
        filename=sealed.filename, media_type=sealed.media_type, size_bytes=len(sealed.content),
        content=None if sealed.gcs_object else sealed.content,
        gcs_object=sealed.gcs_object, gcs_generation=sealed.gcs_generation,
    )


def _package_digest(application, posting, source, artifact):
    return payload_fingerprint({
        "employer": source.employer, "source_id": source.id, "platform": source.platform,
        "tenant": source.board_token, "region": source.region, "recipient_hosts": source.allowed_hosts,
        "submission_grant": source.submission_grant, "receipt_contract": source.receipt_contract,
        "credential_reference": source.credential_env,
        "posting_id": posting.id, "job_sha256": application.job_content_sha256,
        "destination": posting.apply_url, "form_version": application.form["version"],
        "artifact_sha256": artifact.sha256, "resume_choice": application.resume_choice,
        "answers": application.answers, "consents": application.consents,
        "credit_cost": application.credit_cost, "application_mode": application.application_mode,
    })


def missing_fields(application):
    answers = application.answers or {}
    form = application.form or {}
    provided = {key for key, value in answers.items() if value not in ("", [], None)} | {"resume"}
    missing = [field["id"] for field in form.get("fields", []) if field.get("required") and field["id"] not in provided]
    for group in form.get("required_groups", []):
        if not any(identity in provided for identity in group):
            missing.append(group[0] if group else "unknown-required-question")
    for consent in form.get("consents", []):
        if consent.get("required") and (application.consents or {}).get(consent["id"]) is not True:
            missing.append(consent["id"])
    return list(dict.fromkeys(missing))


def _validate_answers(form, answers, consents):
    fields = {field["id"]: field for field in form.get("fields", [])}
    if set(answers) - set(fields):
        raise HTTPException(422, "Answers include questions that are not in the current form")
    if set(consents) - {item["id"] for item in form.get("consents", [])}:
        raise HTTPException(422, "Consent choices do not match the current employer policies")
    for identity, value in answers.items():
        field = fields[identity]
        if field["type"] in {"file", "hidden"}:
            raise HTTPException(422, "File and hidden fields cannot be supplied as text answers")
        if field["type"] == "multi_select":
            if not isinstance(value, list):
                raise HTTPException(422, f"{field['label']} requires a list of selections")
        elif not isinstance(value, str):
            raise HTTPException(422, f"{field['label']} requires one text value")
        if field["type"] in {"single_select", "multi_select"}:
            valid = {option["value"] for option in field.get("options", [])}
            if any(item not in valid for item in (value if isinstance(value, list) else [value])):
                raise HTTPException(422, f"Choose a valid option for {field['label']}")
        if field["type"] == "email" and value and not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise HTTPException(422, "Enter a valid email address")
        if identity in {"first_name", "last_name", "email", "phone"} and len(value) > 255:
            raise HTTPException(422, f"{field['label']} exceeds the employer's maximum length")


def create_application(db: Session, user_id: int, payload: schemas.ApplicationCreate):
    _require_enabled()
    fingerprint = payload_fingerprint(payload.model_dump(exclude={"idempotency_key"}))
    existing = db.query(models.EmployerApplication).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
    if existing:
        if existing.input_fingerprint != fingerprint:
            raise HTTPException(409, "This request key was already used with another application")
        return existing
    posting = _posting(db, payload.posting_id)
    source = _source(db, posting.source_id)
    if not posting.is_open or not source.enabled:
        raise HTTPException(409, "This employer job is no longer available")
    source_snapshot = connectors.source_contract(source)
    posting_external_id = posting.external_id
    resume_snapshot, version_snapshot = _selection_snapshot(db, user_id, payload)
    db.rollback()  # No rendering/storage/provider call while holding a transaction.
    try:
        form = connectors.load_form(source_snapshot, posting_external_id)
        prepared = artifacts.materialize(resume_snapshot, version_snapshot)
    except connectors.ConnectorError as exc:
        raise HTTPException(503 if exc.safe_to_retry else 409, "Employer form could not be verified: " + exc.code) from exc
    with artifacts.guarded_storage(db, prepared, user_id=user_id) as (sealed, upload_id):
        if not db.query(core.User).filter(core.User.id == user_id).with_for_update().first():
            raise HTTPException(409, "Account was deleted while the application was being prepared")
        existing = db.query(models.EmployerApplication).filter_by(user_id=user_id, idempotency_key=payload.idempotency_key).first()
        if existing:
            if existing.input_fingerprint != fingerprint:
                raise HTTPException(409, "This request key was already used with another application")
            return existing
        active_key = f"{user_id}:{payload.posting_id}"
        if db.query(models.EmployerApplication.id).filter_by(active_key=active_key).first():
            raise HTTPException(409, "An application already exists for this job. Review its status before creating another")
        open_count = db.query(models.EmployerApplication.id).filter_by(user_id=user_id).filter(
            models.EmployerApplication.status.notin_(["confirmed", "failed", "cancelled"]),
        ).count()
        if open_count >= config.positive_int("EMPLOYER_MAX_OPEN_APPLICATIONS", 100, 1000):
            raise HTTPException(429, "Complete or cancel existing prepared applications before preparing more")
        posting = _posting(db, payload.posting_id)
        source = _source(db, posting.source_id)
        if not posting.is_open or not source.enabled:
            raise HTTPException(409, "This employer job was closed or disabled while the application was being prepared")
        # Candidate-owned selections and evidence may have changed during rendering.
        _selection_snapshot(db, user_id, payload)
        artifact = _artifact_row(sealed, user_id, payload)
        db.add(artifact)
        db.flush([artifact])
        mode = _source_mode(source, form)
        application = models.EmployerApplication(
            id=public_id("apply"), user_id=user_id, posting_id=posting.id, active_key=active_key,
            idempotency_key=payload.idempotency_key, input_fingerprint=fingerprint,
            resume_id=payload.resume_id, resume_choice=payload.resume_choice, resume_version_id=payload.resume_version_id,
            artifact_id=artifact.id, form=form, answers={}, consents={}, package_digest="",
            job_content_sha256=posting.content_sha256, application_mode=mode,
            status="needs_action" if form["fields"] else "ready", credit_cost=int(config.prices()["apply_credits_per_job"]),
        )
        application.package_digest = _package_digest(application, posting, source, artifact)
        db.add(application)
        artifacts.attach_upload(db, upload_id, user_id=user_id)
        db.commit()
        return application


def update_package(db, user_id, identity, payload: schemas.PackageUpdate):
    application = _owned(db, models.EmployerApplication, identity, user_id)
    if application.status in LOCKED_APPLICATION_STATES or application.status == "cancelled":
        raise HTTPException(409, "This application can no longer be edited")
    _validate_answers(application.form, payload.answers, payload.consents)
    resume_snapshot, version_snapshot = _selection_snapshot(db, user_id, payload)
    db.rollback()
    prepared = artifacts.materialize(resume_snapshot, version_snapshot)
    with artifacts.guarded_storage(db, prepared, user_id=user_id) as (sealed, upload_id):
        application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
        if application.status in LOCKED_APPLICATION_STATES or application.status == "cancelled":
            raise HTTPException(409, "Application execution started while this package was being edited")
        _selection_snapshot(db, user_id, payload)
        artifact = _artifact_row(sealed, user_id, payload)
        db.add(artifact)
        db.flush([artifact])
        application.artifact_id = artifact.id
        application.resume_id, application.resume_choice = payload.resume_id, payload.resume_choice
        application.resume_version_id = payload.resume_version_id
        application.answers, application.consents = payload.answers, payload.consents
        application.approved_at, application.approved_digest, application.approval_expires_at = None, None, None
        db.query(models.EmployerApplicationApproval).filter_by(application_id=application.id, revoked_at=None).update({"revoked_at": utcnow()}, synchronize_session=False)
        application.allowed_actions = []
        application.cancel_requested = False
        application.error_code, application.error_message = None, None
        application.status = "needs_action" if missing_fields(application) else "ready"
        posting = _posting(db, application.posting_id)
        source = _source(db, posting.source_id)
        application.package_digest = _package_digest(application, posting, source, artifact)
        artifacts.attach_upload(db, upload_id, user_id=user_id)
        db.commit()
        return application


def application_response(db, application):
    posting = _posting(db, application.posting_id)
    source = _source(db, posting.source_id)
    artifact = _owned(db, models.SealedApplicationArtifact, application.artifact_id, application.user_id)
    result = {key: getattr(application, key) for key in (
        "id", "resume_id", "resume_choice", "resume_version_id", "status", "application_mode", "form", "package_digest", "answers", "consents", "credit_cost", "charged_credits", "receipt", "approval_expires_at", "cancel_requested", "created_at", "updated_at",
    )}
    result.update({
        "posting": posting_response(posting, source), "missing_fields": missing_fields(application),
        "artifact": {"sha256": artifact.sha256, "filename": artifact.filename, "size_bytes": artifact.size_bytes,
                     "media_type": artifact.media_type, "preview_url": f"/api/v1/employer-jobs/applications/{application.id}/artifact"},
        "requires_user_action": application.status in {"needs_action", "unknown", "manual_handoff", "failed"},
        "error": {"code": application.error_code, "message": application.error_message} if application.error_code else None,
        "handoff_url": posting.apply_url if application.application_mode == "manual" else None,
        "reserved_credits": 0,
    })
    if application.reservation_id:
        reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).one()
        result["reserved_credits"] = reservation.reserved_amount if reservation.state == "reserved" else 0
    return result


def approve_application(db, user_id, identity, payload: schemas.ApprovalCreate):
    application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
    if application.status in LOCKED_APPLICATION_STATES or application.status == "cancelled":
        raise HTTPException(409, "This application cannot be approved in its current state")
    if payload.package_digest != application.package_digest:
        raise HTTPException(409, "The reviewed application has changed; review it again")
    if missing_fields(application):
        raise HTTPException(422, "Complete every required employer question before approval")
    _validate_answers(application.form, application.answers, application.consents)
    if application.application_mode == "api" and not {"upload", "submit"}.issubset(set(payload.allowed_actions)):
        raise HTTPException(422, "Automatic submission requires explicit resume upload and submit approval")
    application.allowed_actions = sorted(set(payload.allowed_actions))
    application.approved_digest = payload.package_digest
    application.approved_at = utcnow()
    application.approval_expires_at = utcnow() + timedelta(hours=24)
    application.status = "approved"
    posting = _posting(db, application.posting_id)
    source = _source(db, posting.source_id)
    artifact = _owned(db, models.SealedApplicationArtifact, application.artifact_id, user_id)
    db.add(models.EmployerApplicationApproval(
        id=public_id("approval"), application_id=application.id, package_digest=application.package_digest,
        review_snapshot={"employer": source.employer, "tenant": source.board_token, "source_id": source.id,
                        "job": _json_posting(posting, source), "form": copy.deepcopy(application.form),
                        "answers": copy.deepcopy(application.answers), "consents": copy.deepcopy(application.consents),
                        "artifact_sha256": artifact.sha256, "artifact_id": artifact.id, "filename": artifact.filename,
                        "credit_cost": application.credit_cost, "resume_choice": application.resume_choice,
                        "receipt_contract": copy.deepcopy(source.receipt_contract), "submission_grant": source.submission_grant},
        allowed_actions=application.allowed_actions, approved_at=application.approved_at,
        expires_at=application.approval_expires_at,
    ))
    db.commit()
    return application


def request_execution(db, user_id, identity, payload: schemas.ExecuteCreate):
    application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
    if application.status in {"queued", "submitting", "confirmed", "unknown"}:
        if payload.package_digest != application.package_digest:
            raise HTTPException(409, "The application package does not match")
        return application
    if application.status != "approved" or application.approved_digest != payload.package_digest:
        raise HTTPException(409, "Review and approve this exact application before execution")
    if application.cancel_requested or not application.approval_expires_at or _utc(application.approval_expires_at) <= utcnow():
        raise HTTPException(409, "Application approval has expired or was cancelled")
    if application.application_mode == "manual":
        application.status = "manual_handoff"
        db.commit()
        return application
    posting, source = _posting(db, application.posting_id), None
    source = _source(db, posting.source_id)
    if _source_mode(source, application.form) != "api":
        raise HTTPException(409, "Automatic submission is no longer enabled for this employer")
    reservation = credits.reserve(db, user_id=user_id, operation="job_application", source_id=application.id,
                                  unit_price=application.credit_cost, count=1)
    db.flush([reservation])
    application.reservation_id = reservation.id
    application.status = "queued"
    from ..dispatch.service import enqueue
    enqueue(db, topic="employer.apply", aggregate_id=application.id, payload={"application_id": application.id},
            key=f"employer.apply:{application.id}:{application.package_digest}")
    db.commit()
    return application


def cancel_application(db, user_id, identity):
    application = _owned(db, models.EmployerApplication, identity, user_id, lock=True)
    if application.status == "confirmed":
        raise HTTPException(409, "The employer already received this application; future work can be stopped but it cannot be recalled here")
    if application.status in {"submitting", "unknown"}:
        application.cancel_requested = True
        application.approved_digest = None
        application.error_message = "Cancellation requested. Data may already have reached the employer; outcome must be reconciled."
    else:
        application.status = "cancelled"
        application.cancel_requested = True
        application.cancelled_at = utcnow()
        application.approved_digest = None
        application.active_key = None
        if application.reservation_id:
            reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
            credits.settle(db, reservation, completed_count=0, reason="Cancelled before automatic submission")
    db.query(models.EmployerApplicationApproval).filter_by(application_id=application.id, revoked_at=None).update({"revoked_at": utcnow()}, synchronize_session=False)
    db.commit()
    return application


def create_source(db, admin_id, payload: schemas.SourceCreate):
    for value in (payload.careers_url, payload.verification_url):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.port not in {None, 443}:
            raise HTTPException(422, "Employer verification and careers URLs must use HTTPS")
    allowed = []
    for host in payload.allowed_hosts:
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]+\.[a-z]{2,}", host) or host.startswith("."):
            raise HTTPException(422, "Only explicit DNS hostnames may be allowed")
        allowed.append(host.lower())
    if urlsplit(payload.careers_url).hostname not in allowed:
        raise HTTPException(422, "The verified employer careers hostname must be explicitly allowed")
    source = models.EmployerSource(id=public_id("source"), verified_by=admin_id,
                                   **payload.model_dump(exclude={"allowed_hosts"}), allowed_hosts=allowed)
    db.add(source)
    db.add(core.AdminAuditEvent(id=public_id("audit"), actor_user_id=admin_id,
        actor_email=db.query(core.User.email).filter_by(id=admin_id).scalar(), action="verify_employer_source",
        target_type="employer_source", target_id=source.id, reason=payload.verification_note,
        after_state={"employer": source.employer, "platform": source.platform, "submission_enabled": source.submission_enabled}))
    db.commit()
    return source


def enqueue_source_refresh(db, identity):
    source = _source(db, identity)
    if not source.enabled:
        raise HTTPException(409, "This source is disabled")
    from ..dispatch.service import enqueue
    # One pending source refresh per five-minute scheduling window.
    window = int(utcnow().timestamp()) // 300
    event = enqueue(db, topic="employer.refresh", aggregate_id=source.id, payload={"source_id": source.id},
                    key=f"employer.refresh:{source.id}:{window}")
    db.commit()
    return event


def enqueue_due_sources(db: Session, limit: int = 100) -> int:
    if not config.discovery_enabled():
        return 0
    from ..dispatch.service import enqueue
    due = db.query(models.EmployerSource).filter(
        models.EmployerSource.enabled.is_(True), models.EmployerSource.next_refresh_at <= utcnow(),
    ).order_by(models.EmployerSource.next_refresh_at).with_for_update(skip_locked=True).limit(min(limit, 500)).all()
    window = int(utcnow().timestamp()) // 300
    for source in due:
        enqueue(db, topic="employer.refresh", aggregate_id=source.id, payload={"source_id": source.id},
                key=f"employer.refresh:{source.id}:{window}")
        source.next_refresh_at = utcnow() + timedelta(minutes=15)
    return len(due)


def reconcile_application(db: Session, identity: str, admin, payload: schemas.ReconciliationCreate):
    application = db.query(models.EmployerApplication).filter_by(id=identity).with_for_update().first()
    if not application:
        raise HTTPException(404, "Application not found")
    if application.status != "unknown":
        raise HTTPException(409, "Only an unknown application needs this reconciliation")
    posting = _posting(db, application.posting_id)
    if payload.employer_posting_id != posting.external_id:
        raise HTTPException(422, "The verified employer receipt does not match this posting")
    reservation = db.query(models.ServiceCreditReservation).filter_by(id=application.reservation_id).with_for_update().one()
    source = _source(db, posting.source_id)
    before = {"status": application.status, "held_credits": reservation.reserved_amount}
    if payload.outcome == "confirmed":
        approval = db.query(models.EmployerApplicationApproval).filter_by(
            application_id=application.id, package_digest=application.package_digest,
        ).order_by(models.EmployerApplicationApproval.approved_at.desc()).first()
        contract = (approval.review_snapshot.get("receipt_contract") if approval else source.receipt_contract) or {}
        provider_receipt = payload.provider_receipt or {}
        receipt_id = provider_receipt.get(contract.get("receipt_id_field", ""))
        complete = provider_receipt.get(contract.get("completion_field", ""))
        if not receipt_id or "completion_value" not in contract or type(complete) is not type(contract["completion_value"]) or complete != contract["completion_value"] or provider_receipt.get("errors"):
            raise HTTPException(422, "Provider receipt does not prove a complete application under this employer's verified contract")
        application.receipt = {"provider": source.platform, "application_id": str(receipt_id),
            "completion_verified": True, "received_at": utcnow().isoformat(), "reconciliation_proof": payload.proof_reference,
            "response_sha256": payload_fingerprint(provider_receipt)}
        credits.settle(db, reservation, completed_count=1, reason="Complete employer application independently verified")
        application.status, application.charged_credits = "confirmed", reservation.committed_amount
        application.error_code, application.error_message = None, None
    else:
        credits.settle(db, reservation, completed_count=0, reason="Provider verified no application was submitted")
        application.status, application.active_key = "failed", None
        application.approved_digest = None
        application.error_code = "verified_not_submitted"
        application.error_message = "The employer independently verified that no application was submitted; reserved credits were returned."
    db.add(core.AdminAuditEvent(id=public_id("audit"), actor_user_id=admin.id, actor_email=admin.email,
        action="reconcile_employer_application", target_type="employer_application", target_id=application.id,
        reason=payload.reason, before_state=before,
        after_state={"status": application.status, "charged_credits": application.charged_credits,
                     "proof_kind": payload.proof_kind, "proof_reference": payload.proof_reference}))
    db.commit()
    return application


def execute_search(identity: str) -> str:
    from .tasks import execute_search as execute
    return execute(identity)


def refresh_source(identity: str) -> str:
    from .tasks import refresh_source as execute
    return execute(identity)


def execute_application(identity: str) -> str:
    from .tasks import execute_application as execute
    return execute(identity)


def fail_dispatched_work(topic: str, identity: str) -> None:
    from .tasks import fail_dispatched_work as fail
    fail(topic, identity)


def delete_artifact(identity: str) -> str:
    from .tasks import delete_artifact as execute
    return execute(identity)


def delete_account_data(db, user_id):
    from .privacy import delete_account_data as delete
    delete(db, user_id)


def export_account_data(db, user_id):
    from .privacy import export_account_data as export
    return export(db, user_id)
