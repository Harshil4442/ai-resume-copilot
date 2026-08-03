from __future__ import annotations

from datetime import timedelta

from backend.app import models
from backend.app.database import Base, get_db
from backend.app.domains.analysis import operations
from backend.app.domains.common import utcnow
from backend.app.domains.notifications import service as notification_service
from backend.app.domains.operations import run_maintenance
from backend.app.routers.v1 import router as v1_router
from backend.app.security import get_current_user
from fastapi import FastAPI
from fastapi.testclient import TestClient
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
    return engine, sessionmaker(autocommit=False, autoflush=False, bind=engine)


def test_notification_outbox_is_idempotent_and_marks_delivery(monkeypatch):
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "test-key")
    monkeypatch.setenv("EMAIL_FROM", "HireWiz <updates@example.com>")
    engine, factory = _database()
    try:
        with factory() as db:
            user = models.User(id=1, email="owner@example.com", ai_credits=50, tier="free")
            db.add(user)
            db.commit()
            first = notification_service.enqueue_notification(
                db,
                user_id=1,
                notification_type="welcome",
                recipient="owner@example.com",
                payload={},
                idempotency_key="user:1:welcome",
            )
            second = notification_service.enqueue_notification(
                db,
                user_id=1,
                notification_type="welcome",
                recipient="owner@example.com",
                payload={},
                idempotency_key="user:1:welcome",
            )
            db.commit()
            assert first is not None and second is not None
            assert first.id == second.id
            assert db.query(models.NotificationOutbox).count() == 1

            delivered: list[str] = []
            monkeypatch.setattr(
                notification_service,
                "_send",
                lambda item: delivered.append(item.id),
            )
            summary = notification_service.deliver_pending_notifications(db)
            assert summary.sent == 1
            assert delivered == [first.id]
            assert db.get(models.NotificationOutbox, first.id).status == "sent"
    finally:
        engine.dispose()


def test_maintenance_purges_sensitive_payloads_and_old_model_telemetry(monkeypatch):
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "false")
    monkeypatch.setenv("ANALYSIS_INPUT_RETENTION_DAYS", "1")
    monkeypatch.setenv("ANALYSIS_RESULT_RETENTION_DAYS", "1")
    monkeypatch.setenv("MODEL_TELEMETRY_RETENTION_DAYS", "1")
    engine, factory = _database()
    try:
        old = utcnow() - timedelta(days=3)
        with factory() as db:
            db.add(models.User(id=1, email="owner@example.com", ai_credits=50, tier="free"))
            run = models.AnalysisRun(
                id="run_old",
                user_id=1,
                operation="job_match",
                status="succeeded",
                idempotency_key="old-analysis",
                input_fingerprint="a" * 64,
                input_payload={"resume_text": "career-sensitive"},
                result_payload={"result": "career-sensitive"},
                estimated_units=1,
                committed_units=1,
                usage_state="committed",
                attempt_count=1,
                cancel_requested=False,
                created_at=old,
                updated_at=old,
                completed_at=old,
            )
            db.add(run)
            db.flush()
            db.add(
                models.ModelCallEvent(
                    id="mdl_old",
                    analysis_run_id=run.id,
                    user_id=1,
                    provider="test",
                    model="test",
                    prompt_version="v1",
                    input_tokens=1,
                    output_tokens=1,
                    latency_ms=1,
                    estimated_cost_micros=1,
                    status="succeeded",
                    created_at=old,
                )
            )
            db.commit()
            summary = run_maintenance(db)
            db.refresh(run)
            assert summary.analysis_inputs_purged == 1
            assert summary.analysis_results_purged == 1
            assert summary.model_events_deleted == 1
            assert run.input_payload == {"purged": True}
            assert run.result_payload is None
            assert run.input_purged_at is not None
            assert run.result_purged_at is not None
    finally:
        engine.dispose()


def test_model_call_cost_uses_configured_rates(monkeypatch):
    monkeypatch.setenv("LLM_INPUT_COST_MICROS_PER_MILLION", "1000000")
    monkeypatch.setenv("LLM_OUTPUT_COST_MICROS_PER_MILLION", "2000000")
    engine, factory = _database()
    try:
        with factory() as db:
            db.add(models.User(id=1, email="owner@example.com", ai_credits=50, tier="free"))
            run = models.AnalysisRun(
                id="run_cost",
                user_id=1,
                operation="job_match",
                status="running",
                idempotency_key="cost-analysis",
                input_fingerprint="b" * 64,
                input_payload={},
                estimated_units=1,
                committed_units=0,
                usage_state="reserved",
                attempt_count=1,
                cancel_requested=False,
                created_at=utcnow(),
                updated_at=utcnow(),
            )
            db.add(run)
            db.flush()
            operations._record_model_call(
                db,
                run=run,
                prompt_version="match-mega-v1",
                input_payload={"text": "a" * 4000},
                output_payload={"text": "b" * 2000},
                latency_ms=25,
                status="succeeded",
            )
            db.commit()
            event = db.query(models.ModelCallEvent).one()
            assert event.input_tokens > 900
            assert event.output_tokens > 400
            assert event.estimated_cost_micros > 1_500
    finally:
        engine.dispose()


def test_admin_usage_adjustment_is_audited_and_idempotent(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "admin@example.com")
    engine, factory = _database()
    try:
        with factory() as db:
            db.add_all(
                [
                    models.User(id=1, email="admin@example.com", ai_credits=50, tier="free"),
                    models.User(id=2, email="customer@example.com", ai_credits=10, tier="free"),
                ]
            )
            db.commit()

        app = FastAPI()
        app.include_router(v1_router, prefix="/api")

        def override_db():
            db = factory()
            try:
                yield db
            finally:
                db.close()

        def override_user():
            with factory() as db:
                return db.get(models.User, 1)

        app.dependency_overrides[get_db] = override_db
        app.dependency_overrides[get_current_user] = override_user
        with TestClient(app) as client:
            first = client.post(
                "/api/v1/admin/usage-adjustments",
                headers={"Idempotency-Key": "support-case-123"},
                json={"user_id": 2, "amount": 5, "reason": "Restore units after verified provider failure"},
            )
            assert first.status_code == 200, first.text
            assert first.json()["balance"] == 15
            replay = client.post(
                "/api/v1/admin/usage-adjustments",
                headers={"Idempotency-Key": "support-case-123"},
                json={"user_id": 2, "amount": 5, "reason": "Restore units after verified provider failure"},
            )
            assert replay.status_code == 200
            assert replay.json()["idempotent_replay"] is True

        with factory() as db:
            assert db.get(models.User, 2).ai_credits == 15
            assert db.query(models.UsageEvent).count() == 1
            audit = db.query(models.AdminAuditEvent).one()
            assert audit.before_state == {"analysis_units": 10}
            assert audit.after_state["analysis_units"] == 15
    finally:
        engine.dispose()
