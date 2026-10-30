"""Server-owned explicit review persistence; ordinary fact approval is separate.

The stamp lives only on server-imported evidence. Client-created source_ref values
have provenance=user and cannot issue authority. No metrics or admin journal is used.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy.orm import Session, undefer

from ... import models
from ...services import source_transcription as core
from ..common import public_id, utcnow
from . import schemas

_MARKER = "\ntranscription-review:v1:"
_MAX_EVIDENCE = 1000
_KEYS = {"record_id", "user_id", "resume_id", "evidence_id", "source_sha256", "section",
         "original_section_sha256", "approved_text_sha256", "assessment_fingerprint", "reviewed_at", "action"}


class TranscriptionUnavailable(HTTPException):
    # A source authorization refusal must not replay a whole worker/model run,
    # even if an opaque numeric resume ID contains a retry-looking status code.
    retryable = False
    budget_exhausted = True


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _time(value: datetime) -> str:
    # Existing DateTime columns reload UTC timestamps without tzinfo.
    return (value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)).isoformat(timespec="microseconds")


def import_reference(resume_id: int, section: str) -> str:
    return f"resume:{resume_id}:section:{section}"


def original_reference(item: object) -> str | None:
    value = getattr(item, "source_ref", None)
    return value.split(_MARKER, 1)[0] if isinstance(value, str) else None


def clear_review(item: models.EvidenceItem) -> None:
    if item.provenance == "resume_extraction" and isinstance(item.source_ref, str):
        item.source_ref = original_reference(item)


def assess_resume(resume: models.Resume, user_id: int) -> core.Assessment:
    try:
        return core.assess_source(user_id=user_id, resume_id=resume.id,
            source_bytes=resume.source_document, source_format=resume.source_format,
            raw_text=resume.raw_text, sections=resume.sections or {})
    except (ValueError, TypeError, AttributeError):
        raise TranscriptionUnavailable(422, {"code": "transcription_source_required", "message":
            "The complete original source and extracted sections are required for transcription review."}) from None


def load_explicit_reviews(assessment: core.Assessment, evidence: list[object]) -> tuple[core.ExplicitTranscriptionReview, ...]:
    """Validate stamps matching fresh owned source and rows.

    A changed row timestamp invalidates the action, including ordinary approval.
    Compute the final public CAS fingerprint only after checking the stamp, so it
    never has to contain its own source_ref-dependent fingerprint. Consumers use
    load_effective_transcription to read actual persistence; record_review also
    validates its explicitly authorized transaction draft before committing it.
    """
    required = {row.section: row for row in assessment.affected_sections}
    result = []
    for row in evidence:
        raw = getattr(row, "source_ref", None)
        text = getattr(row, "evidence_text", None)
        updated = getattr(row, "updated_at", None)
        identifier = getattr(row, "id", None)
        if (getattr(row, "provenance", None) != "resume_extraction" or not isinstance(raw, str)
                or len(raw.encode()) > 2000 or raw.count(_MARKER) != 1
                or not isinstance(text, str) or not isinstance(updated, datetime)
                or not isinstance(identifier, str)):
            continue
        base, encoded = raw.split(_MARKER)
        try:
            stamp = json.loads(encoded)
            if (type(stamp) is not dict or set(stamp) != _KEYS
                    or json.dumps(stamp, sort_keys=True, separators=(",", ":"), ensure_ascii=False) != encoded):
                continue
            section = stamp["section"]
            if (type(section) is not str or section not in required
                    or base != import_reference(assessment.resume_id, section)
                    or type(stamp["user_id"]) is not int or stamp["user_id"] != assessment.user_id
                    or type(stamp["resume_id"]) is not int or stamp["resume_id"] != assessment.resume_id
                    or getattr(row, "user_id", None) != assessment.user_id
                    or getattr(row, "resume_id", None) != assessment.resume_id
                    or stamp["evidence_id"] != identifier
                    or stamp["source_sha256"] != assessment.source_sha256
                    or stamp["assessment_fingerprint"] != assessment.fingerprint
                    or stamp["original_section_sha256"] != required[section].original_sha256
                    or getattr(row, "approval_state", None) != "approved"
                    or stamp["approved_text_sha256"] != _sha(text)
                    or stamp["reviewed_at"] != _time(updated)
                    or stamp["action"] not in {"correct_transcription", "confirm_visible_text"}
                    or not isinstance(stamp["record_id"], str) or len(stamp["record_id"]) != 36
                    or not stamp["record_id"].startswith("trr_")
                    or any(char not in "0123456789abcdef" for char in stamp["record_id"][4:])):
                continue
            result.append(core.ExplicitTranscriptionReview(record_id=stamp["record_id"],
                user_id=assessment.user_id, resume_id=assessment.resume_id,
                source_sha256=assessment.source_sha256, section=section,
                original_section_sha256=required[section].original_sha256,
                approved_text_sha256=stamp["approved_text_sha256"], evidence_id=identifier,
                evidence_fingerprint=schemas.evidence_snapshot_fingerprint(row), action=stamp["action"]))
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
    return tuple(result)


def _owned_resume(db: Session, user_id: int, resume_id: int, *, locked: bool = False) -> models.Resume:
    query = db.query(models.Resume).options(undefer(models.Resume.source_document)).filter(
        models.Resume.id == resume_id, models.Resume.user_id == user_id).populate_existing()
    if locked:
        query = query.with_for_update()
    resume = query.first()
    if resume is None:
        raise HTTPException(404, "Resume not found")
    return resume


def _bounded_evidence(db: Session, user_id: int, resume_id: int) -> list[models.EvidenceItem]:
    # Fetch one sentinel beyond the core's finite scope, never an accepted
    # prefix. Refuse before decoding stamps, constructing context or responding.
    rows = db.query(models.EvidenceItem).filter(models.EvidenceItem.user_id == user_id,
        models.EvidenceItem.resume_id == resume_id).populate_existing().limit(_MAX_EVIDENCE + 1).all()
    if len(rows) > _MAX_EVIDENCE:
        raise TranscriptionUnavailable(422, {"code": "transcription_evidence_scope_exceeded", "message":
            "This resume exceeds the complete transcription review evidence limit."})
    return rows


def owned_evidence(db: Session, user_id: int, resume_id: int) -> list[models.EvidenceItem]:
    """Complete bounded fresh rows; callers may filter approvals after the cap."""
    return _bounded_evidence(db, user_id, resume_id)


def load_effective_transcription(db: Session, user_id: int, resume_id: int, *,
                                 expected_context_fingerprint: str | None = None) -> core.ResolvedTranscription:
    """Fresh owned persistence entrypoint for later quote/dispatch/commit wiring.

    This grants no paid admission or physical editing authority. Missing/stale
    explicit actions stay needs_review; an expected old context fails closed.
    """
    assessment = assess_resume(_owned_resume(db, user_id, resume_id), user_id)
    rows = _bounded_evidence(db, user_id, resume_id)
    return core.resolve_transcription(assessment, rows, load_explicit_reviews(assessment, rows),
        expected_context_fingerprint=expected_context_fingerprint)


def require_effective_transcription(db: Session, user_id: int, resume_id: int) -> core.ResolvedTranscription:
    value = load_effective_transcription(db, user_id, resume_id)
    if value.status != "ready":
        raise TranscriptionUnavailable(422, {"code": "transcription_review_required", "resume_id": resume_id,
            "message": "Compare the complete extracted sections with your original file in Resume & evidence, then explicitly review their transcription before optional AI work."})
    return value


def context_binding(value: core.ResolvedTranscription) -> dict[str, str | None]:
    if value.status != "ready" or value.raw_text is None:
        raise ValueError("transcription_review_required")
    return {"assessment": value.assessment_fingerprint, "review_version": value.review_version_fingerprint,
            "context": value.context_fingerprint}


def review_snapshot(db: Session, user_id: int, resume_id: int) -> schemas.TranscriptionReviewResponse:
    resume = _owned_resume(db, user_id, resume_id)
    assessment = assess_resume(resume, user_id)
    evidence = _bounded_evidence(db, user_id, resume_id)
    records = load_explicit_reviews(assessment, evidence)
    resolved = core.resolve_transcription(assessment, evidence, records)
    sections = []
    for binding in assessment.affected_sections:
        rows = [row for row in evidence if row.provenance == "resume_extraction"
                and original_reference(row) == import_reference(resume_id, binding.section)]
        item = rows[0] if len(rows) == 1 else None
        sections.append(schemas.TranscriptionSectionReview(section=binding.section,
            original_text=dict(assessment.sections)[binding.section],
            evidence_id=item.id if item is not None else None,
            evidence_fingerprint=schemas.evidence_snapshot_fingerprint(item) if item is not None else None,
            reviewed=any(record.section == binding.section for record in records)))
    status = ("editable_source_required" if assessment.editable_source_required else
        "not_required" if assessment.issue_count == 0 else "reviewed" if resolved.status == "ready" else "needs_review")
    return schemas.TranscriptionReviewResponse(resume_id=resume_id, filename=resume.original_filename,
        source_format=assessment.source_format, source_sha256=assessment.source_sha256,
        assessment_fingerprint=assessment.fingerprint, status=status, sections=sections,
        unmapped_text=assessment.raw_text if assessment.editable_source_required else None)


def _conflict(db: Session) -> None:
    db.rollback()
    raise HTTPException(409, {"code": "evidence_refresh_required", "message":
        "The source or evidence changed. Refresh and review the complete transcription before approving."})


def record_review(db: Session, user_id: int, evidence_id: str,
                  payload: schemas.TranscriptionReviewAction) -> models.EvidenceItem:
    item = db.query(models.EvidenceItem).filter(models.EvidenceItem.id == evidence_id,
        models.EvidenceItem.user_id == user_id).populate_existing().with_for_update().first()
    if item is None:
        raise HTTPException(404, "Evidence item not found")
    if payload.expected_fingerprint != schemas.evidence_snapshot_fingerprint(item):
        _conflict(db)
    if item.resume_id is None or item.provenance != "resume_extraction":
        raise HTTPException(422, "Import the original resume section before reviewing its transcription.")
    resume = _owned_resume(db, user_id, item.resume_id, locked=True)
    assessment = assess_resume(resume, user_id)
    if payload.expected_assessment_fingerprint != assessment.fingerprint:
        _conflict(db)
    rows = _bounded_evidence(db, user_id, item.resume_id)
    affected = {binding.section: binding for binding in assessment.affected_sections}
    if (assessment.editable_source_required or payload.section not in affected
            or original_reference(item) != import_reference(item.resume_id, payload.section)):
        raise HTTPException(422, "This section cannot be reviewed exactly. Keep the original and provide a compatible editable source.")
    original = dict(assessment.sections)[payload.section]
    if ((payload.action == "confirm_visible_text" and payload.evidence_text != original)
            or (payload.action == "correct_transcription" and
                (payload.evidence_text == original
                 or core.correction_creates_ambiguity(assessment, payload.section, payload.evidence_text)))):
        raise HTTPException(422, "Confirm the exact visible text, or explicitly correct every ambiguous fragment in this section.")
    now = utcnow()
    stamp = {"record_id": public_id("trr"), "user_id": user_id, "resume_id": item.resume_id,
        "evidence_id": item.id, "source_sha256": assessment.source_sha256,
        "section": payload.section, "original_section_sha256": affected[payload.section].original_sha256,
        "approved_text_sha256": _sha(payload.evidence_text), "assessment_fingerprint": assessment.fingerprint,
        "reviewed_at": _time(now), "action": payload.action}
    reference = import_reference(item.resume_id, payload.section) + _MARKER + json.dumps(
        stamp, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(reference.encode()) > 2000:
        raise HTTPException(422, "This source section cannot carry an exact transcription review.")
    # Validate this explicitly authorized transaction draft against any other
    # current reviews before writing. It is never returned as consumer authority
    # until the stamp commits; missing other section reviews may remain pending.
    values = schemas.EvidenceResponse.model_validate(item).model_dump(exclude={"snapshot_fingerprint"})
    draft = SimpleNamespace(**{**values, "user_id": user_id, "evidence_text": payload.evidence_text,
        "approval_state": "approved", "source_ref": reference, "updated_at": now})
    prospective = [draft if row.id == item.id else row for row in rows]
    check = core.resolve_transcription(assessment, prospective, load_explicit_reviews(assessment, prospective))
    if check.status != "ready" and check.reason != "explicit_transcription_review_required":
        raise HTTPException(422, "This exact correction cannot form a complete reviewed context. Review the complete sections or provide an editable source.")
    item.evidence_text = payload.evidence_text
    item.approval_state = "approved"
    item.source_ref = reference
    item.updated_at = now
    db.commit()
    db.refresh(item)
    return item
