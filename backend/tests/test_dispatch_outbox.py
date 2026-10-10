from datetime import timedelta
from types import SimpleNamespace

import pytest
from backend.app import models  # noqa: F401
from backend.app.database import Base
from backend.app.domains.analysis import tasks as analysis_tasks
from backend.app.domains.common import utcnow
from backend.app.domains.dispatch import service
from backend.app.domains.dispatch.models import DispatchOutbox
from google.api_core.exceptions import AlreadyExists
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


@pytest.mark.parametrize("fault", ["ambiguous_response", "publisher_crash"])
def test_physical_task_name_is_stored_before_rpc_and_reused_after_publisher_uncertainty(factory, monkeypatch, fault):
    event_id = _intent(factory)
    names, created = [], set()

    class SyntheticPublisherCrash(BaseException):
        pass

    class SyntheticTasksClient:
        def queue_path(self, project, location, queue):
            return f"projects/{project}/locations/{location}/queues/{queue}"

        def create_task(self, *, parent, task):
            names.append(task.name)
            with factory() as db:
                event = db.get(DispatchOutbox, event_id)
                assert event.status == "publishing" and event.task_name == task.name
                assert task.name.startswith(f"{parent}/tasks/")
            if task.name in created:
                raise AlreadyExists("Synthetic task-name tombstone")
            created.add(task.name)
            if fault == "publisher_crash":
                raise SyntheticPublisherCrash()
            raise RuntimeError("Synthetic creation response lost")

    monkeypatch.setattr(service.tasks_v2, "CloudTasksClient", SyntheticTasksClient)
    if fault == "publisher_crash":
        with pytest.raises(SyntheticPublisherCrash):
            service.dispatch_pending()
    else:
        assert service.dispatch_pending() == {"dispatched": 0, "failed": 1}
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert event.status == ("publishing" if fault == "publisher_crash" else "pending")
        assert event.task_name == names[0] and event.execution_attempts == 0
        event.lease_until = event.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    assert names == [names[0], names[0]]
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert event.status == "dispatched" and event.dispatch_attempts == 2


def test_busy_domain_recovery_uses_new_physical_task_name_after_prior_task_tombstone(factory, monkeypatch):
    event_id = _intent(factory)
    names, created = [], set()

    class SyntheticTasksClient:
        def queue_path(self, project, location, queue):
            return f"projects/{project}/locations/{location}/queues/{queue}"

        def create_task(self, *, parent, task):
            names.append(task.name)
            if task.name in created:
                raise AlreadyExists("Synthetic exhausted task-name tombstone")
            created.add(task.name)
            return SimpleNamespace(name=task.name)

    monkeypatch.setattr(service.tasks_v2, "CloudTasksClient", SyntheticTasksClient)
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    monkeypatch.setattr(service, "_execute", lambda *_: "running")
    with pytest.raises(service.RetryableDispatchError, match="still in progress"):
        service.process_event(event_id)
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert event.execution_attempts == 0 and event.status == "retry"
        event.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    assert len(created) == 2 and names[0] != names[1]
    monkeypatch.setattr(service, "_execute", lambda *_: "succeeded")
    assert service.process_event(event_id) == "succeeded"
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.execution_attempts, event.dispatch_attempts) == ("completed", 1, 2)


def test_early_worker_unknown_outcome_cannot_be_overwritten_by_ambiguous_publisher_failure(factory, monkeypatch):
    event_id = _intent(factory)
    calls = []
    monkeypatch.setattr(service, "_execute", lambda *_: calls.append("unknown") or "unknown")

    def created_but_response_lost(event):
        assert service.process_event(event.id) == "unknown"
        raise RuntimeError("Synthetic creation response lost after worker completed")

    monkeypatch.setattr(service, "_cloud_task", created_but_response_lost)
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 1}
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).status == "completed"
    assert service.dispatch_pending() == {"dispatched": 0, "failed": 0}
    assert calls == ["unknown"]


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


@pytest.mark.parametrize("result", ["running", "queued", "pending"])
def test_nonterminal_domain_result_keeps_recovery_without_exhausting_execution_budget(factory, monkeypatch, result):
    event_id = _intent(factory)
    calls = []
    monkeypatch.setattr(service, "_execute", lambda *_: calls.append(result) or result)
    # Cloud Tasks may retry sooner than the sweep's available_at. Observing a
    # live domain lease repeatedly must not settle it as delivery exhausted.
    for _ in range(8):
        with pytest.raises(service.RetryableDispatchError, match="still in progress"):
            service.process_event(event_id)
        with factory() as db:
            event = db.get(DispatchOutbox, event_id)
            assert event.status == "retry" and event.execution_attempts == 0
            assert event.completed_at is event.lease_token is event.lease_until is None
            assert event.last_error == "domain_work_in_progress"
            assert event.available_at > utcnow().replace(tzinfo=None)
    assert calls == [result] * 8
    monkeypatch.setattr(service, "_execute", lambda *_: "succeeded")
    assert service.process_event(event_id) == "succeeded"
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert event.status == "completed" and event.execution_attempts == 1


def test_busy_observation_preserves_prior_real_failure_budget(factory, monkeypatch):
    event_id = _intent(factory)
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        event.execution_attempts = 5
        db.commit()
    monkeypatch.setattr(service, "_execute", lambda *_: "running")
    with pytest.raises(service.RetryableDispatchError, match="still in progress"):
        service.process_event(event_id)
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).execution_attempts == 5
    calls, exhausted = [], []

    def real_failure(*_):
        calls.append("provider_failure")
        raise RuntimeError("Synthetic provider failure")

    monkeypatch.setattr(service, "_execute", real_failure)
    monkeypatch.setattr(service, "_settle_exhausted", lambda topic, aggregate_id: exhausted.append(aggregate_id))
    with pytest.raises(service.RetryableDispatchError):
        service.process_event(event_id)
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).execution_attempts == 6
    assert service.process_event(event_id) == "failed"
    assert calls == ["provider_failure"] and exhausted == ["run_test"]


def test_expired_dispatch_lease_preserves_analysis_until_its_domain_lease_can_be_reclaimed(factory, monkeypatch):
    monkeypatch.setattr(analysis_tasks, "SessionLocal", factory)
    calls = []
    monkeypatch.setattr(analysis_tasks, "execute_operation", lambda *_: calls.append("executed") or {"fixture": "completed"})
    with factory() as db:
        db.add(models.User(id=1, email="lease-recovery@example.test", ai_credits=4))
        run = models.AnalysisRun(id="run_test", user_id=1, operation="job_match", status="running",
            idempotency_key="synthetic-lease-recovery", input_fingerprint="synthetic", input_payload={},
            started_at=utcnow() - timedelta(minutes=17), attempt_count=1, estimated_units=1, usage_state="reserved")
        db.add(run)
        event = service.enqueue(db, topic="analysis.run", aggregate_id=run.id, payload={"run_id": run.id}, key="analysis:run_test")
        db.commit()
        event_id = event.id
        event.status, event.lease_token = "running", "expired-synthetic-worker"
        event.lease_until = utcnow() - timedelta(seconds=1)
        event.execution_attempts = 1
        db.commit()
    with pytest.raises(service.RetryableDispatchError, match="still in progress"):
        service.process_event(event_id)
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        run = db.get(models.AnalysisRun, "run_test")
        assert (event.status, event.execution_attempts) == ("retry", 1)
        assert (run.status, run.usage_state, run.attempt_count) == ("running", "reserved", 1)
        assert calls == []
        run.started_at = utcnow() - timedelta(minutes=21)
        event.available_at = utcnow() - timedelta(seconds=1)
        db.commit()
    monkeypatch.setenv("ANALYSIS_TASKS_MODE", "inline")
    assert service.dispatch_pending() == {"dispatched": 1, "failed": 0}
    with factory() as db:
        assert db.get(DispatchOutbox, event_id).status == "completed"
        run = db.get(models.AnalysisRun, "run_test")
        assert (run.status, run.usage_state, run.attempt_count) == ("succeeded", "committed", 2)
        assert calls == ["executed"]


def test_refresh_live_domain_lease_defers_dispatch_without_fetching_provider(factory, monkeypatch):
    from backend.app.domains.employer import config, tasks
    from backend.app.domains.employer import models as employer_models
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(config, "discovery_enabled", lambda: True)
    monkeypatch.setattr(tasks.connectors, "fetch_postings", lambda *_: pytest.fail("Live source lease cannot fetch again"))
    with factory() as db:
        source = employer_models.EmployerSource(id="source_test", employer="Synthetic employer", platform="greenhouse",
            board_token="synthetic", careers_url="https://employer.example.test/careers", enabled=True,
            verification_url="https://employer.example.test/careers", verification_note="Synthetic verified fixture",
            scan_token="live-synthetic-scan", scan_started_at=utcnow())
        db.add(source)
        event = service.enqueue(db, topic="employer.refresh", aggregate_id=source.id, payload={"source_id": source.id}, key="refresh:source_test")
        db.commit()
        event_id = event.id
    with pytest.raises(service.RetryableDispatchError, match="still in progress"):
        service.process_event(event_id)
    with factory() as db:
        event = db.get(DispatchOutbox, event_id)
        assert (event.status, event.execution_attempts) == ("retry", 0)
        assert db.get(employer_models.EmployerSource, "source_test").scan_token == "live-synthetic-scan"


@pytest.mark.parametrize("result", ["unknown", "deferred"])
def test_unknown_send_and_separate_deferred_cleanup_remain_completed_handoffs(factory, monkeypatch, result):
    event_id = _intent(factory)
    calls = []
    monkeypatch.setattr(service, "_execute", lambda *_: calls.append(result) or result)
    assert service.process_event(event_id) == result
    assert service.process_event(event_id) == "completed"
    assert calls == [result]


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
