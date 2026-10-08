from datetime import timedelta

import pytest
from backend.app import models  # noqa: F401
from backend.app.database import Base
from backend.app.domains.common import utcnow
from backend.app.domains.dispatch import service
from backend.app.domains.dispatch.models import DispatchOutbox
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture()
def factory(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setenv("ANALYSIS_TASKS_MODE", "cloud_tasks")
    yield factory
    engine.dispose()


def _intent(factory):
    with factory() as db:
        event = service.enqueue(db, topic="analysis.run", aggregate_id="run_test", payload={"run_id": "run_test"}, key="analysis:run_test")
        db.commit()
        return event.id


def test_intent_rollback_does_not_dispatch_uncommitted_work(factory):
    with factory() as db:
        service.enqueue(db, topic="analysis.run", aggregate_id="run_test", payload={"run_id": "run_test"}, key="analysis:run_test")
        db.flush()
        db.rollback()
    with factory() as db:
        assert db.query(DispatchOutbox).count() == 0


def test_durable_publish_failure_is_recovered_without_recreating_work(factory, monkeypatch):
    event_id = _intent(factory)
    def fail(event):
        raise RuntimeError("queue unavailable")
    monkeypatch.setattr(service, "_cloud_task", fail)
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 1}
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert event.status == "pending"
        event.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(service, "_cloud_task", lambda event: f"task/{event.id}")
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 0}


def test_duplicate_task_delivery_executes_domain_only_once(factory, monkeypatch):
    event_id = _intent(factory)
    calls = []
    monkeypatch.setattr(service, "_execute", lambda topic, aggregate_id: calls.append(aggregate_id) or "succeeded")
    assert service.process_event(event_id) == "succeeded"
    assert service.process_event(event_id) == "completed"
    assert calls == ["run_test"]


def test_stale_publisher_mark_cannot_overwrite_completed_task(factory, monkeypatch):
    event_id = _intent(factory)
    monkeypatch.setattr(service, "_execute", lambda *_: "succeeded")
    def fast_worker(event):
        assert service.process_event(event.id) == "succeeded"
        return "task/fast"
    monkeypatch.setattr(service, "_cloud_task", fast_worker)
    assert service.dispatch_pending()["dispatched"] == 1
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).status == "completed"


def test_live_worker_lease_does_not_execute_twice(factory, monkeypatch):
    event_id = _intent(factory)
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        event.status = "running"
        event.lease_token = "live"
        event.lease_until = utcnow() + timedelta(minutes=1)
        db.commit()
    monkeypatch.setattr(service, "_execute", lambda *_: pytest.fail("duplicate execution"))
    with pytest.raises(service.RetryableDispatchError):
        service.process_event(event_id)


def test_failed_domain_execution_preserves_retry_state(factory, monkeypatch):
    event_id = _intent(factory)
    def fail(*_):
        raise RuntimeError("provider timeout")
    monkeypatch.setattr(service, "_execute", fail)
    with pytest.raises(service.RetryableDispatchError):
        service.process_event(event_id)
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).status == "retry"
    monkeypatch.setattr(service, "_execute", lambda *_: "unknown")
    assert service.process_event(event_id) == "unknown"


def test_task_payload_never_contains_candidate_data(factory):
    with factory() as db, pytest.raises(ValueError, match="identifiers only"):
        service.enqueue(db, topic="employer.apply", aggregate_id="app_test", payload={"resume": "private"}, key="test")


def test_artifact_deletion_intent_contains_only_record_identity(factory):
    with factory() as db:
        event = service.enqueue(db, topic="employer.artifact-delete", aggregate_id="del_test", payload={"deletion_id": "del_test"}, key="delete:del_test")
        db.commit()
        assert event.payload == {"deletion_id": "del_test"}


def test_worker_scope_rejects_other_work_before_claiming(factory, monkeypatch):
    event_id = _intent(factory)
    monkeypatch.setenv("WORKER_ALLOWED_TOPICS", "employer.search,employer.apply")
    monkeypatch.setattr(service, "_execute", lambda *_: pytest.fail("wrong workload executed"))
    with pytest.raises(service.WorkerScopeError):
        service.process_event(event_id)
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert event.status == "pending"
        assert event.execution_attempts == 0
