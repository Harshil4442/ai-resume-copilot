"""Real lock races for normal CI's owned disposable PostgreSQL; not local proof."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from backend.app import models
from backend.app.database import Base
from backend.app.domains.career import schemas, service
from backend.app.domains.career import transcription_review as review
from backend.app.services import source_transcription as core
from backend.tests.test_evidence_review_postgres import pg_engine as owned_pg_engine
from backend.tests.test_source_transcription_review import CORRECTED, ORIGINAL, PersistencePort
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def pg_engine():
    if os.getenv("CI") == "true":
        assert os.getenv("HIREWIZ_TEST_POSTGRES_URL"), "CI transcription review requires its owned PostgreSQL URL"
    yield from owned_pg_engine.__wrapped__()


def test_ci_cannot_silently_skip_missing_native_transcription_postgres(monkeypatch):
    monkeypatch.setenv("CI", "true")
    monkeypatch.delenv("HIREWIZ_TEST_POSTGRES_URL", raising=False)
    with pytest.raises(AssertionError, match="requires its owned PostgreSQL URL"):
        next(pg_engine.__wrapped__())


def seeded_factory(pg_engine):
    Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    synthetic = PersistencePort()
    with factory() as db:
        db.add(models.User(id=1, email="transcription-race@example.invalid"))
        db.commit()
        for row in (synthetic.resume, synthetic.item):
            db.add(type(row)(**{column.name: getattr(row, column.name) for column in row.__table__.columns}))
            db.commit()
    return factory


@pytest.mark.parametrize("competitor", ["edit", "ordinary_approval", "explicit_confirmation"])
def test_one_shown_snapshot_authorizes_at_most_one_competing_action(pg_engine, competitor):
    factory = seeded_factory(pg_engine)
    with factory() as db:
        item = db.get(models.EvidenceItem, "evd_synthetic")
        resume = db.get(models.Resume, 3)
        shown = schemas.evidence_snapshot_fingerprint(item)
        assessment = review.assess_resume(resume, 1).fingerprint
    barrier = Barrier(2)
    def write(kind):
        with factory() as db:
            # Both sessions cache the shown row and source before either lock.
            assert schemas.evidence_snapshot_fingerprint(db.get(models.EvidenceItem, "evd_synthetic")) == shown
            assert review.assess_resume(db.get(models.Resume, 3), 1).fingerprint == assessment
            barrier.wait(timeout=5)
            try:
                if kind in {"correction", "explicit_confirmation"}:
                    result = review.record_review(db, 1, "evd_synthetic", schemas.TranscriptionReviewAction(
                        expected_fingerprint=shown, expected_assessment_fingerprint=assessment,
                        section="experience", action="correct_transcription" if kind == "correction" else "confirm_visible_text",
                        evidence_text=CORRECTED if kind == "correction" else ORIGINAL))
                else:
                    result = service.update_evidence(db, 1, "evd_synthetic", schemas.EvidenceUpdate(
                        expected_fingerprint=shown, **({"evidence_text": "Competing edited fact."}
                            if kind == "edit" else {"approval_state": "approved"})))
                return 200, kind, result.evidence_text
            except HTTPException as error:
                db.rollback()
                assert error.detail["code"] == "evidence_refresh_required"
                return error.status_code, kind, None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ["correction", competitor]))
    assert sorted(result[0] for result in results) == [200, 409]
    winner = next(result for result in results if result[0] == 200)
    with factory() as db:
        row = db.get(models.EvidenceItem, "evd_synthetic")
        assessment = review.assess_resume(db.get(models.Resume, 3), 1)
        records = review.load_explicit_reviews(assessment, [row])
        context = core.resolve_transcription(assessment, [row], records)
        assert row.evidence_text == winner[2]
        assert context.status == ("ready" if winner[1] in {"correction", "explicit_confirmation"} else "needs_review")


def test_concurrent_imports_produce_one_server_item_per_exact_source_section(pg_engine):
    factory = seeded_factory(pg_engine)
    with factory() as db:
        db.delete(db.get(models.EvidenceItem, "evd_synthetic"))
        db.commit()
    barrier = Barrier(2)
    def import_sections(_):
        with factory() as db:
            barrier.wait(timeout=5)
            created, skipped = service.import_resume_evidence(db, 1, 3)
            return len(created), skipped
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(import_sections, range(2)))
    assert sorted(results) == [(0, 2), (2, 0)]
    with factory() as db:
        assert db.query(models.EvidenceItem).filter_by(user_id=1, resume_id=3).count() == 2


def test_cached_deferred_source_cannot_hide_rotation_from_context_or_locked_action(pg_engine):
    factory = seeded_factory(pg_engine)
    with factory() as db:
        item, resume = db.get(models.EvidenceItem, "evd_synthetic"), db.get(models.Resume, 3)
        review.record_review(db, 1, item.id, schemas.TranscriptionReviewAction(
            expected_fingerprint=schemas.evidence_snapshot_fingerprint(item),
            expected_assessment_fingerprint=review.assess_resume(resume, 1).fingerprint,
            section="experience", action="correct_transcription", evidence_text=CORRECTED))
    with factory() as context_reader, factory() as action_reader, factory() as writer:
        context_source = context_reader.get(models.Resume, 3)
        context_bytes = context_source.source_document
        shown = review.load_effective_transcription(context_reader, 1, 3)
        item = action_reader.get(models.EvidenceItem, "evd_synthetic")
        resume = action_reader.get(models.Resume, 3)
        old_source = resume.source_document  # Explicitly cache the deferred bytes.
        request = schemas.TranscriptionReviewAction(expected_fingerprint=schemas.evidence_snapshot_fingerprint(item),
            expected_assessment_fingerprint=review.assess_resume(resume, 1).fingerprint,
            section="experience", action="correct_transcription", evidence_text=CORRECTED)
        writer.get(models.Resume, 3).source_document = old_source + b"changed"
        writer.commit()
        assert context_source.source_document == context_bytes == old_source
        with pytest.raises(core.SourceTranscriptionChanged):
            review.load_effective_transcription(context_reader, 1, 3,
                expected_context_fingerprint=shown.context_fingerprint)
        with pytest.raises(HTTPException) as error:
            review.record_review(action_reader, 1, "evd_synthetic", request)
        assert error.value.status_code == 409 and error.value.detail["code"] == "evidence_refresh_required"
