"""Actual action/loader and HTTP code, fake persistence only; no DB/row-lock proof."""
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.database import get_db
from backend.app.domains.career import schemas, service
from backend.app.domains.career import transcription_review as review
from backend.app.routers.v1.career import require_career_workspace, router
from backend.app.security import get_current_user
from backend.app.services import source_transcription as core
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

ORIGINAL = "Developed clear docu-\nmented records."
CORRECTED = "Developed clear documented records."


class Query:
    def __init__(self, db, model):
        self.db, self.model, self.conditions = db, model, []
        self.load_options = []
        self.fresh = self.locked = False

    def filter(self, *conditions):
        self.conditions.extend(conditions)
        return self

    def options(self, *options):
        self.load_options.extend(options)
        return self

    def populate_existing(self):
        self.fresh = True
        return self

    def with_for_update(self):
        self.locked = True
        return self

    def all(self):
        statement = select(self.model).options(*self.load_options).where(*self.conditions)
        if self.locked:
            statement = statement.with_for_update()
        self.db.queries.append((str(statement.compile(dialect=postgresql.dialect())), self.fresh, self.locked))
        return [row for row in self.db.rows if isinstance(row, self.model)
            and all(getattr(row, condition.left.key) == condition.right.value for condition in self.conditions)]

    def first(self):
        return next(iter(self.all()), None)


class PersistencePort:
    """In-memory port; compiled SQL is inspected, never executed."""
    def __init__(self):
        now = datetime(2026, 10, 30, tzinfo=UTC)
        self.resume = models.Resume(id=3, user_id=1, original_filename="synthetic-source.pdf",
            source_document=b"immutable synthetic source", source_format="pdf",
            raw_text="CONTACT\nMorgan\nEXPERIENCE\n" + ORIGINAL + "\nEDUCATION\nNorth College",
            sections={"experience": ORIGINAL, "education": "North College"}, skills=[])
        self.item = models.EvidenceItem(id="evd_synthetic", user_id=1, resume_id=3,
            category="experience", title="Experience", evidence_text=ORIGINAL, metrics={}, skills=[],
            provenance="resume_extraction", source_ref=review.import_reference(3, "experience"),
            approval_state="approved", confidence="extracted", created_at=now, updated_at=now)
        self.rows = [self.resume, self.item]
        self.commits = self.rollbacks = 0
        self.queries = []

    def query(self, model):
        return Query(self, model)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def refresh(self, row):
        # Match the existing DateTime column's UTC-naive readback shape.
        row.updated_at = row.updated_at.astimezone(UTC).replace(tzinfo=None)

    def add(self, row):
        self.rows.append(row)


def payload(db, *, text=CORRECTED, action="correct_transcription", row=None, **changes):
    return schemas.TranscriptionReviewAction(**{
        "expected_fingerprint": schemas.evidence_snapshot_fingerprint(row if row is not None else db.item),
        "expected_assessment_fingerprint": review.assess_resume(db.resume, 1).fingerprint,
        "section": "experience", "action": action, "evidence_text": text, **changes})


def resolved(db):
    assessment = review.assess_resume(db.resume, 1)
    rows = [row for row in db.rows if isinstance(row, models.EvidenceItem)]
    records = review.load_explicit_reviews(assessment, rows)
    return core.resolve_transcription(assessment, rows, records)


def test_real_action_persists_explicit_review_and_trusted_loader_resolves_full_context():
    db = PersistencePort()
    old_source, old_raw = db.resume.source_document, db.resume.raw_text
    assert resolved(db).status == "needs_review"  # Old approved status is insufficient.
    shown = review.review_snapshot(db, 1, 3)
    assert shown.filename == "synthetic-source.pdf" and shown.sections[0].original_text == ORIGINAL
    assert not shown.sections[0].reviewed
    result = review.record_review(db, 1, db.item.id, payload(db))
    assert result.approval_state == "approved" and result.evidence_text == CORRECTED
    assert db.commits == 1 and "transcription-review:v1:" in result.source_ref
    assert db.resume.source_document == old_source and db.resume.raw_text == old_raw
    context = resolved(db)
    assert context.status == "ready" and context.raw_text == old_raw.replace(ORIGINAL, CORRECTED)
    assert not context.physical_edit_compatible
    assert review.review_snapshot(db, 1, 3).status == "reviewed"
    locked = [sql for sql, fresh, lock in db.queries if fresh and lock]
    assert any("evidence_items.user_id =" in sql and "evidence_items.id =" in sql and "FOR UPDATE" in sql for sql in locked)
    assert any("resumes.user_id =" in sql and "resumes.id =" in sql and "resumes.source_document" in sql and "FOR UPDATE" in sql for sql in locked)


def test_visible_text_confirmation_requires_an_explicit_action_and_keeps_literal_source():
    db = PersistencePort()
    review.record_review(db, 1, db.item.id, payload(db, text=ORIGINAL, action="confirm_visible_text"))
    value = resolved(db)
    assert value.status == "ready" and value.raw_text == db.resume.raw_text
    assert not value.physical_edit_compatible


def test_owned_persistence_loader_rereads_current_rows_and_invalidates_old_context():
    db = PersistencePort()
    assert review.load_effective_transcription(db, 1, 3).status == "needs_review"
    review.record_review(db, 1, db.item.id, payload(db))
    shown = review.load_effective_transcription(db, 1, 3)
    assert shown.status == "ready"
    assert review.load_effective_transcription(db, 1, 3,
        expected_context_fingerprint=shown.context_fingerprint) == shown
    db.item.updated_at += timedelta(microseconds=1)
    assert review.load_effective_transcription(db, 1, 3).status == "needs_review"
    with pytest.raises(core.SourceTranscriptionChanged):
        review.load_effective_transcription(db, 1, 3, expected_context_fingerprint=shown.context_fingerprint)
    with pytest.raises(HTTPException) as error:
        review.load_effective_transcription(db, 2, 3)
    assert error.value.status_code == 404


@pytest.mark.parametrize("change", ["missing_cas", "stale_cas", "missing_assessment", "stale_assessment", "changed_source", "changed_outside_section"])
def test_stale_or_missing_tokens_and_source_refuse_before_mutation(change):
    db = PersistencePort()
    request = payload(db)
    if change == "changed_source":
        db.resume.source_document = b"different immutable source"
    elif change == "changed_outside_section":
        db.resume.raw_text += "\nUnseen outside text."
    else:
        field = "expected_fingerprint" if "cas" in change else "expected_assessment_fingerprint"
        request = request.model_copy(update={field: None if change.startswith("missing") else "0" * 64})
    with pytest.raises(HTTPException) as error:
        review.record_review(db, 1, db.item.id, request)
    assert error.value.status_code == 409 and error.value.detail["code"] == "evidence_refresh_required"
    assert db.commits == 0 and db.rollbacks == 1 and db.item.evidence_text == ORIGINAL
    assert resolved(db).status == "needs_review"


def test_successful_review_cannot_be_replayed_as_new_consent():
    db = PersistencePort()
    request = payload(db)
    review.record_review(db, 1, db.item.id, request)
    with pytest.raises(HTTPException) as error:
        review.record_review(db, 1, db.item.id, request)
    assert error.value.status_code == 409 and db.commits == 1


def test_foreign_ownership_is_404_before_fingerprint_or_source_disclosure(monkeypatch):
    db = PersistencePort()
    request = payload(db)
    def forbidden(*args):
        raise AssertionError("foreign evidence reached fingerprint check")
    monkeypatch.setattr(schemas, "evidence_snapshot_fingerprint", forbidden)
    with pytest.raises(HTTPException) as error:
        review.record_review(db, 2, db.item.id, request)
    assert error.value.status_code == 404 and db.commits == 0
    with pytest.raises(HTTPException) as error:
        review.review_snapshot(db, 2, 3)
    assert error.value.status_code == 404


@pytest.mark.parametrize("changes", [
    {"text": ORIGINAL}, {"text": CORRECTED, "action": "confirm_visible_text"},
    {"text": "Still ambig-\nuous."}, {"text": "New boundary fragment-"}, {"section": "education"},
])
def test_action_never_approves_implicit_join_wrong_section_or_new_boundary_ambiguity(changes):
    db = PersistencePort()
    with pytest.raises(HTTPException) as error:
        review.record_review(db, 1, db.item.id, payload(db, **changes))
    assert error.value.status_code == 422 and db.commits == 0 and db.item.evidence_text == ORIGINAL


@pytest.mark.parametrize("change", ["text", "timestamp", "source", "approval", "provenance", "owner", "resume", "malformed_stamp"])
def test_trusted_loader_rejects_stale_tampered_and_user_issued_stamps(change):
    db = PersistencePort()
    review.record_review(db, 1, db.item.id, payload(db))
    if change == "text":
        db.item.evidence_text += " Unreviewed fact."
    if change == "timestamp":
        db.item.updated_at += timedelta(microseconds=1)
    if change == "source":
        db.resume.source_document += b"changed"
    if change == "approval":
        db.item.approval_state = "pending"
    if change == "provenance":
        db.item.provenance = "user"
    if change == "owner":
        db.item.user_id = 2
    if change == "resume":
        db.item.resume_id = 4
    if change == "malformed_stamp":
        db.item.source_ref += " "
    assert resolved(db).status == "needs_review"


def test_generic_edit_or_fact_approval_revokes_the_explicit_review_without_reimport_duplicates():
    db = PersistencePort()
    review.record_review(db, 1, db.item.id, payload(db))
    created, skipped = service.import_resume_evidence(db, 1, 3)
    assert len(created) == 1 and skipped == 1  # Only education was absent.
    service.update_evidence(db, 1, db.item.id, schemas.EvidenceUpdate(
        expected_fingerprint=schemas.evidence_snapshot_fingerprint(db.item), approval_state="approved"))
    assert db.item.source_ref == review.import_reference(3, "experience")
    assert resolved(db).status == "needs_review"


def test_user_created_row_cannot_turn_a_copied_server_stamp_into_authority():
    db = PersistencePort()
    review.record_review(db, 1, db.item.id, payload(db))
    db.item.provenance = "user"
    assert resolved(db).status == "needs_review"
    with pytest.raises(HTTPException) as error:
        review.record_review(db, 1, db.item.id, payload(db))
    assert error.value.status_code == 422


def test_user_supplied_source_reference_cannot_prevent_a_real_server_section_import():
    db = PersistencePort()
    db.item.provenance = "user"
    created, skipped = service.import_resume_evidence(db, 1, 3)
    assert skipped == 0 and len(created) == 2
    assert any(row.provenance == "resume_extraction" and row.evidence_text == ORIGINAL for row in created)
    assert resolved(db).status == "needs_review"


def test_two_sections_complete_in_separate_explicit_reviews_without_losing_outside_text():
    db = PersistencePort()
    second_text = "Built inde-\npendent tools."
    db.resume.raw_text += "\nPROJECTS\n" + second_text
    db.resume.sections = {**db.resume.sections, "projects": second_text}
    second = models.EvidenceItem(**{column.name: getattr(db.item, column.name)
        for column in models.EvidenceItem.__table__.columns})
    second.id, second.category, second.evidence_text = "evd_projects", "projects", second_text
    second.source_ref = review.import_reference(3, "projects")
    db.rows.append(second)
    raw = db.resume.raw_text
    review.record_review(db, 1, db.item.id, payload(db))
    assert review.review_snapshot(db, 1, 3).status == "needs_review"
    review.record_review(db, 1, second.id, payload(db, row=second, section="projects", text="Built independent tools."))
    assert resolved(db).raw_text == raw.replace(ORIGINAL, CORRECTED).replace(second_text, "Built independent tools.")
    assert resolved(db).status == "ready" and db.resume.raw_text == raw


def test_action_refuses_full_context_overflow_before_persisting_an_unusable_review():
    db = PersistencePort()
    db.resume.raw_text = "P" * 250000 + "\n" + ORIGINAL
    db.resume.sections = {"experience": ORIGINAL}
    with pytest.raises(HTTPException) as error:
        review.record_review(db, 1, db.item.id, payload(db, text="A complete correction. " + "X" * 19000))
    assert error.value.status_code == 422 and db.commits == 0
    assert db.item.evidence_text == ORIGINAL and resolved(db).status == "needs_review"


@pytest.mark.parametrize("field,value", [("source_sha256", "0" * 64), ("user_id", 2),
    ("resume_id", 4), ("evidence_id", "evd_different"), ("section", "education"),
    ("original_section_sha256", "0" * 64), ("approved_text_sha256", "0" * 64),
    ("assessment_fingerprint", "0" * 64), ("reviewed_at", "2026-01-01T00:00:00.000000+00:00"),
    ("action", "ordinary_fact_approval"), ("record_id", "self-issued"), ("unknown", True)])
def test_persisted_stamp_components_are_closed_and_exact(field, value):
    db = PersistencePort()
    review.record_review(db, 1, db.item.id, payload(db))
    base, encoded = db.item.source_ref.split("\ntranscription-review:v1:")
    stamp = json.loads(encoded)
    stamp[field] = value
    db.item.source_ref = base + "\ntranscription-review:v1:" + json.dumps(stamp, sort_keys=True, separators=(",", ":"))
    assert resolved(db).status == "needs_review"


def test_actual_additive_http_routes_and_missing_version_conflict_without_database():
    db = PersistencePort()
    app = FastAPI()
    app.include_router(router, prefix="/v1")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=1)
    app.dependency_overrides[require_career_workspace] = lambda: None
    with TestClient(app) as client:
        shown = client.get("/v1/evidence-items/transcription-review/3")
        assert shown.status_code == 200 and shown.json()["sections"][0]["original_text"] == ORIGINAL
        body = payload(db).model_dump()
        refused = client.post(f"/v1/evidence-items/{db.item.id}/transcription-review", json={**body, "expected_fingerprint": None})
        assert refused.status_code == 409 and db.commits == 0
        response = client.post(f"/v1/evidence-items/{db.item.id}/transcription-review", json=body)
        assert response.status_code == 200 and response.json()["snapshot_fingerprint"]
        assert client.get("/v1/evidence-items/transcription-review/3").json()["status"] == "reviewed"
    operation = app.openapi()["paths"]["/v1/evidence-items/{evidence_id}/transcription-review"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"]["$ref"].endswith("/TranscriptionReviewAction")
