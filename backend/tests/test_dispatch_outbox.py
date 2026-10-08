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
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("ANALYSIS_TASKS_MODE", "cloud_tasks")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "synthetic-dispatch-test")
    monkeypatch.setenv("ANALYSIS_TASKS_LOCATION", "us-central1")
    monkeypatch.setenv("ANALYSIS_TASKS_SERVICE_ACCOUNT", "tasks@synthetic-dispatch-test.iam.gserviceaccount.com")
    for name in set(service.TASK_QUEUE_SETTINGS.values()):
        monkeypatch.setenv(name, "synthetic-test-queue")
    for name in set(service.TASK_WORKER_SETTINGS.values()):
        monkeypatch.setenv(name, "https://synthetic-worker.example")
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


@pytest.mark.parametrize("environment", ["production", "staging", None])
@pytest.mark.parametrize("mode", [None, "", "inline"])
def test_production_never_defaults_or_falls_back_to_inline_before_opening_database(factory, monkeypatch, environment, mode):
    event_id = _intent(factory)
    if environment is None:
        monkeypatch.delenv("APP_ENV", raising=False)
    else:
        monkeypatch.setenv("APP_ENV", environment)
    if mode is None:
        monkeypatch.delenv("ANALYSIS_TASKS_MODE", raising=False)
    else:
        monkeypatch.setenv("ANALYSIS_TASKS_MODE", mode)
    monkeypatch.setattr(service, "SessionLocal", lambda: pytest.fail("Invalid mode must fail before opening a database session"))
    with pytest.raises(service.DispatchConfigurationError):
        service.dispatch_pending()
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.dispatch_attempts, event.execution_attempts) == ("pending", 0, 0)
        assert event.task_name is event.lease_token is event.last_error is None


@pytest.mark.parametrize("name", [
    "GOOGLE_CLOUD_PROJECT", "ANALYSIS_TASKS_LOCATION", "ANALYSIS_TASKS_SERVICE_ACCOUNT",
    "ANALYSIS_TASKS_QUEUE", "ANALYSIS_WORKER_URL",
])
def test_missing_cloud_publisher_configuration_leaves_intent_untouched(factory, monkeypatch, name):
    event_id = _intent(factory)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv(name)
    monkeypatch.setattr(service, "_cloud_task", lambda *_: pytest.fail("Incomplete configuration cannot publish"))
    with pytest.raises(service.DispatchConfigurationError, match=name):
        service.dispatch_pending()
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.dispatch_attempts, event.execution_attempts) == ("pending", 0, 0)
        assert event.task_name is event.lease_token is event.last_error is None


@pytest.mark.parametrize("name", ["EMPLOYER_INGESTION_TASKS_QUEUE", "EMPLOYER_INGESTION_WORKER_URL"])
def test_missing_production_employer_route_cannot_fall_back_or_claim_any_selected_work(factory, monkeypatch, name):
    analysis_id = _intent(factory)
    with factory() as db:
        refresh = service.enqueue(db, topic="employer.refresh", aggregate_id="verified-source", payload={"source_id": "verified-source"}, key="verified-source-refresh")
        db.commit()
        refresh_id = refresh.id
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.delenv(name)
    monkeypatch.setattr(service, "_cloud_task", lambda *_: pytest.fail("Incomplete employer route cannot publish"))
    with pytest.raises(service.DispatchConfigurationError, match=name):
        service.dispatch_pending()
    with factory() as db:
        for identity in (analysis_id, refresh_id):
            event = db.get(DispatchOutbox, identity)
            assert (event.status, event.dispatch_attempts) == ("pending", 0)


def test_scoped_analysis_worker_can_publish_other_topics_without_executing_them(factory, monkeypatch):
    with factory() as db:
        event = service.enqueue(db, topic="employer.refresh", aggregate_id="verified-source", payload={"source_id": "verified-source"}, key="verified-source-refresh")
        db.commit()
        event_id = event.id
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("WORKER_ALLOWED_TOPICS", "analysis.run")
    monkeypatch.setattr(service, "_cloud_task", lambda event: f"cloud/task/{event.id}")
    monkeypatch.setattr(service, "_execute", lambda *_: pytest.fail("Publisher cannot execute employer workload"))
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.execution_attempts) == ("dispatched", 0)
        assert event.task_name == f"cloud/task/{event_id}"


def test_inline_scope_failure_records_retry_without_stranding_dispatched_local_event(factory, monkeypatch):
    with factory() as db:
        event = service.enqueue(db, topic="employer.refresh", aggregate_id="verified-source", payload={"source_id": "verified-source"}, key="verified-source-refresh")
        db.commit()
        event_id = event.id
    monkeypatch.setenv("ANALYSIS_TASKS_MODE", "inline")
    monkeypatch.setenv("WORKER_ALLOWED_TOPICS", "analysis.run")
    monkeypatch.setattr(service, "_execute", lambda *_: pytest.fail("Wrong workload must not execute"))
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 1}
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.dispatch_attempts, event.execution_attempts) == ("pending", 1, 0)
        assert event.task_name is event.lease_token is event.lease_until is None
        assert event.last_error == "WorkerScopeError" and event.available_at > utcnow().replace(tzinfo=None)
        event.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setenv("WORKER_ALLOWED_TOPICS", "employer.refresh")
    monkeypatch.setattr(service, "_execute", lambda *_: "healthy")
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.execution_attempts, event.last_error) == ("completed", 1, None)


def test_local_unset_mode_preserves_inline_completion_and_domain_failure_retry(factory, monkeypatch):
    event_id = _intent(factory)
    monkeypatch.delenv("ANALYSIS_TASKS_MODE")
    monkeypatch.setattr(service, "_execute", lambda *_: (_ for _ in ()).throw(RuntimeError("synthetic provider failure")))
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 1}
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        # The publisher must not erase the domain's claimed possible-send state.
        assert event.status == "retry" and event.last_error == "RuntimeError"
        assert event.execution_attempts == 1
        event.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setattr(service, "_execute", lambda *_: "unknown")
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).status == "completed"


def test_explicit_production_manual_mode_is_a_safe_pause(factory, monkeypatch):
    event_id = _intent(factory)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ANALYSIS_TASKS_MODE", "manual")
    monkeypatch.setattr(service, "SessionLocal", lambda: pytest.fail("Manual mode cannot claim work"))
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 0}
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).status == "pending"


@pytest.mark.parametrize("value", ["   ", "http://worker.example", "https://worker.example:notaport", "https://worker.example/path", "https://private:credential@worker.example"])
def test_invalid_cloud_worker_origin_fails_before_claim_and_does_not_expose_value(factory, monkeypatch, value):
    event_id = _intent(factory)
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ANALYSIS_WORKER_URL", value)
    with pytest.raises(service.DispatchConfigurationError) as err:
        service.dispatch_pending()
    assert "ANALYSIS_WORKER_URL" in str(err.value)
    assert value not in str(err.value)
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).dispatch_attempts == 0
