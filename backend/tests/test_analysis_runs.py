from __future__ import annotations

from backend.app.database import Base
from backend.app.domains.analysis import schemas, tasks
from backend.app.domains.analysis import service as analysis_service
from backend.app.models import (
    AnalysisRun,
    EvidenceItem,
    Opportunity,
    Resume,
    ResumeVersion,
    UsageEvent,
    User,
)
from backend.app.services import llm_client
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def _database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with factory() as db:
        db.add_all(
            [
                User(id=1, email="owner@example.com", ai_credits=50, tier="free"),
                User(id=2, email="other@example.com", ai_credits=50, tier="free"),
                Resume(
                    id=10,
                    user_id=1,
                    original_filename="owner.pdf",
                    raw_text="Python engineer",
                    skills=["python"],
                    sections={"experience": "Built Python services"},
                ),
                Resume(
                    id=20,
                    user_id=2,
                    original_filename="other.pdf",
                    raw_text="Private resume",
                    skills=["java"],
                    sections={"experience": "Private"},
                ),
            ]
        )
        db.commit()
    return engine, factory


def _payload(*, resume_id: int = 10, title: str = "Platform Engineer"):
    return schemas.AnalysisRunCreate(
        operation="job_match",
        input={
            "resume_id": resume_id,
            "job_title": title,
            "company": "Example Co",
            "job_description": "Build and operate reliable Python services for global customers.",
        },
    )


def test_ownership_is_validated_before_usage_is_reserved():
    engine, factory = _database()
    try:
        with factory() as db:
            try:
                analysis_service.create_run(
                    db,
                    user_id=1,
                    payload=_payload(resume_id=20),
                    header_idempotency_key="ownership-check-001",
                )
                raise AssertionError("Expected ownership failure")
            except HTTPException as exc:
                assert exc.status_code == 404
        with factory() as db:
            assert db.get(User, 1).ai_credits == 50
            assert db.query(UsageEvent).count() == 0
            assert db.query(AnalysisRun).count() == 0
    finally:
        engine.dispose()


def test_create_run_is_idempotent_and_rejects_key_reuse_with_new_input():
    engine, factory = _database()
    try:
        with factory() as db:
            first, created = analysis_service.create_run(
                db,
                user_id=1,
                payload=_payload(),
                header_idempotency_key="match-create-001",
            )
            first_id = first.id
            assert created is True
        with factory() as db:
            duplicate, created = analysis_service.create_run(
                db,
                user_id=1,
                payload=_payload(),
                header_idempotency_key="match-create-001",
            )
            assert created is False
            assert duplicate.id == first_id
            assert db.get(User, 1).ai_credits == 49
            assert db.query(UsageEvent).count() == 1
        with factory() as db:
            try:
                analysis_service.create_run(
                    db,
                    user_id=1,
                    payload=_payload(title="Different role"),
                    header_idempotency_key="match-create-001",
                )
                raise AssertionError("Expected idempotency conflict")
            except HTTPException as exc:
                assert exc.status_code == 409
    finally:
        engine.dispose()


def test_duplicate_task_delivery_commits_usage_once(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(
        tasks,
        "execute_operation",
        lambda db, run: {"match_id": 123, "match_score": 88},
    )
    try:
        with factory() as db:
            run, _ = analysis_service.create_run(
                db,
                user_id=1,
                payload=_payload(),
                header_idempotency_key="duplicate-task-001",
            )
            run_id = run.id

        assert tasks.process_analysis_run(run_id) == "succeeded"
        assert tasks.process_analysis_run(run_id) == "succeeded"

        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            assert run.status == "succeeded"
            assert run.committed_units == 1
            assert db.get(User, 1).ai_credits == 49
            assert [event.event_type for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                "reserve",
                "commit",
            ]
    finally:
        engine.dispose()


def test_terminal_failure_releases_reserved_usage(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)

    def fail(db, run):
        raise ValueError("non-retryable malformed provider response")

    monkeypatch.setattr(tasks, "execute_operation", fail)
    try:
        with factory() as db:
            run, _ = analysis_service.create_run(
                db,
                user_id=1,
                payload=_payload(),
                header_idempotency_key="failed-task-001",
            )
            run_id = run.id

        assert tasks.process_analysis_run(run_id) == "failed"
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            assert run.status == "failed"
            assert run.usage_state == "released"
            assert db.get(User, 1).ai_credits == 50
            assert [event.event_type for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                "reserve",
                "release",
            ]
    finally:
        engine.dispose()


def test_queued_cancellation_releases_usage():
    engine, factory = _database()
    try:
        with factory() as db:
            run, _ = analysis_service.create_run(
                db,
                user_id=1,
                payload=_payload(),
                header_idempotency_key="cancel-task-001",
            )
            run_id = run.id
        with factory() as db:
            cancelled = analysis_service.cancel_run(db, 1, run_id)
            assert cancelled.status == "cancelled"
        with factory() as db:
            assert db.get(User, 1).ai_credits == 50
            assert [event.event_type for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                "reserve",
                "release",
            ]
    finally:
        engine.dispose()


def test_active_premium_run_is_audited_without_deduction():
    engine, factory = _database()
    try:
        with factory() as db:
            user = db.get(User, 1)
            user.tier = "premium"
            user.premium_until = None
            db.commit()
        with factory() as db:
            run, _ = analysis_service.create_run(
                db,
                user_id=1,
                payload=_payload(),
                header_idempotency_key="premium-task-001",
            )
            assert run.usage_state == "waived"
            assert db.get(User, 1).ai_credits == 50
            assert db.query(UsageEvent).one().event_type == "waive"
    finally:
        engine.dispose()


def test_evidence_tailoring_creates_a_traceable_version_and_commits_once(monkeypatch):
    engine, factory = _database()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(
        llm_client,
        "tailor_resume_from_evidence",
        lambda **kwargs: {
            "summary_items": [
                {"text": "Built reliable Python services", "evidence_ids": ["evd_approved"]}
            ],
            "bullets": [
                {"text": "Built Python services", "evidence_ids": ["evd_approved"]}
            ],
            "skills": ["python"],
            "evidence_needed": [],
            "evidence_policy": "approved_only",
        },
    )
    try:
        with factory() as db:
            db.add_all(
                [
                    Opportunity(
                        id="opp_tailor",
                        user_id=1,
                        resume_id=10,
                        title="Platform Engineer",
                        company="Example Co",
                        job_description="Build and operate reliable Python services for global customers.",
                        job_snapshot={},
                    ),
                    EvidenceItem(
                        id="evd_approved",
                        user_id=1,
                        resume_id=10,
                        category="experience",
                        title="Platform work",
                        evidence_text="Built reliable Python services",
                        skills=["python"],
                        metrics={},
                        approval_state="approved",
                    ),
                ]
            )
            db.commit()
        with factory() as db:
            run, _ = analysis_service.create_run(
                db,
                user_id=1,
                payload=schemas.AnalysisRunCreate(
                    operation="resume_tailor",
                    opportunity_id="opp_tailor",
                    input={},
                ),
                header_idempotency_key="test-request",
            )
            run_id = run.id

        assert tasks.process_analysis_run(run_id) == "succeeded"
        assert tasks.process_analysis_run(run_id) == "succeeded"
        with factory() as db:
            run = db.get(AnalysisRun, run_id)
            version = db.query(ResumeVersion).one()
            assert run.committed_units == 10
            assert db.get(User, 1).ai_credits == 40
            assert version.generation_run_id == run_id
            assert version.evidence_ids == ["evd_approved"]
            assert version.structured_content["evidence_policy"] == "approved_only"
            assert [event.event_type for event in db.query(UsageEvent).order_by(UsageEvent.created_at)] == [
                "reserve",
                "commit",
            ]
    finally:
        engine.dispose()


def test_evidence_tailoring_validates_approval_before_reservation():
    engine, factory = _database()
    try:
        with factory() as db:
            db.add(
                Opportunity(
                    id="opp_no_evidence",
                    user_id=1,
                    resume_id=10,
                    title="Platform Engineer",
                    company="Example Co",
                    job_description="Build and operate reliable Python services for global customers.",
                    job_snapshot={},
                )
            )
            db.commit()
        with factory() as db:
            try:
                analysis_service.create_run(
                    db,
                    user_id=1,
                    payload=schemas.AnalysisRunCreate(
                        operation="resume_tailor",
                        opportunity_id="opp_no_evidence",
                        input={},
                    ),
                    header_idempotency_key="test-request",
                )
                raise AssertionError("Expected evidence validation failure")
            except HTTPException as exc:
                assert exc.status_code == 422
        with factory() as db:
            assert db.get(User, 1).ai_credits == 50
            assert db.query(AnalysisRun).count() == 0
            assert db.query(UsageEvent).count() == 0
    finally:
        engine.dispose()
