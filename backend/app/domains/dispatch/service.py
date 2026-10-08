from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from google.api_core.exceptions import AlreadyExists
from google.cloud import tasks_v2
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from ...database import SessionLocal
from ..common import public_id, utcnow
from .models import DispatchOutbox

log = logging.getLogger("hirewiz.dispatch")
TOPICS = {"analysis.run", "employer.search", "employer.refresh", "employer.apply", "employer.artifact-delete"}
LOCAL_ENVIRONMENTS = {"development", "dev", "local", "test"}
TASK_QUEUE_SETTINGS = {
    "analysis.run": "ANALYSIS_TASKS_QUEUE",
    "employer.search": "EMPLOYER_SEARCH_TASKS_QUEUE",
    "employer.refresh": "EMPLOYER_INGESTION_TASKS_QUEUE",
    "employer.apply": "EMPLOYER_APPLICATION_TASKS_QUEUE",
    "employer.artifact-delete": "EMPLOYER_INGESTION_TASKS_QUEUE",
}
TASK_WORKER_SETTINGS = {
    "analysis.run": "ANALYSIS_WORKER_URL",
    "employer.search": "EMPLOYER_SEARCH_WORKER_URL",
    "employer.refresh": "EMPLOYER_INGESTION_WORKER_URL",
    "employer.apply": "EMPLOYER_APPLICATION_WORKER_URL",
    "employer.artifact-delete": "EMPLOYER_INGESTION_WORKER_URL",
}


class RetryableDispatchError(RuntimeError):
    pass


class WorkerScopeError(RuntimeError):
    pass


class DispatchConfigurationError(RuntimeError):
    pass


def _local_environment() -> bool:
    return (os.getenv("APP_ENV") or "production").strip().lower() in LOCAL_ENVIRONMENTS


def _setting(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        # Names are safe to expose; never include configuration values/tokens.
        raise DispatchConfigurationError(f"Dispatch requires {name}")
    return value


def _task_route(topic: str) -> tuple[str, str]:
    if topic not in TOPICS:
        raise DispatchConfigurationError("Unsupported dispatch topic")
    queue_name, worker_name = TASK_QUEUE_SETTINGS[topic], TASK_WORKER_SETTINGS[topic]
    if _local_environment():
        queue = (os.getenv(queue_name) or "").strip() or _setting("ANALYSIS_TASKS_QUEUE")
        worker_url = (os.getenv(worker_name) or "").strip() or _setting("ANALYSIS_WORKER_URL")
    else:
        # Production cannot silently route employer work to an analysis worker.
        queue, worker_url = _setting(queue_name), _setting(worker_name)
    worker_url = worker_url.rstrip("/")
    try:
        parsed = urlsplit(worker_url)
        port = parsed.port
    except ValueError:
        raise DispatchConfigurationError(f"Dispatch requires a service HTTPS origin in {worker_name}") from None
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path or port not in {None, 443}):
        raise DispatchConfigurationError(f"Dispatch requires a service HTTPS origin in {worker_name}")
    if any(character.isspace() for character in queue) or "/" in queue:
        raise DispatchConfigurationError(f"Dispatch requires a queue ID in {queue_name}")
    return queue, worker_url


def _dispatch_mode() -> str:
    mode = (os.getenv("ANALYSIS_TASKS_MODE") or "").strip().lower()
    if not mode:
        if not _local_environment():
            raise DispatchConfigurationError("Production dispatch requires explicit ANALYSIS_TASKS_MODE")
        mode = "inline"
    if mode not in {"manual", "inline", "cloud_tasks"}:
        raise DispatchConfigurationError("Unsupported dispatch mode")
    if mode == "inline" and not _local_environment():
        raise DispatchConfigurationError("Inline dispatch is restricted to local/test environments")
    if mode == "cloud_tasks":
        for name in ("GOOGLE_CLOUD_PROJECT", "ANALYSIS_TASKS_LOCATION", "ANALYSIS_TASKS_SERVICE_ACCOUNT"):
            _setting(name)
        _task_route("analysis.run")
    return mode


@dataclass(frozen=True)
class DispatchSnapshot:
    id: str
    topic: str
    execution_attempts: int


def enqueue(
    db: Session, *, topic: str, aggregate_id: str, payload: dict[str, Any], key: str
) -> DispatchOutbox:
    """Append intent inside the caller's transaction, without committing it."""
    if topic not in TOPICS or not aggregate_id or len(key) > 180:
        raise ValueError("Invalid dispatch intent")
    if set(payload) - {"id", "run_id", "source_id", "search_id", "application_id", "deletion_id"}:
        raise ValueError("Dispatch payloads may contain identifiers only")
    existing = db.query(DispatchOutbox).filter_by(idempotency_key=key).first()
    if existing:
        if existing.topic != topic or existing.aggregate_id != aggregate_id:
            raise ValueError("Dispatch key belongs to different work")
        return existing
    event = DispatchOutbox(
        id=public_id("dispatch"), topic=topic, aggregate_id=aggregate_id,
        payload=payload, idempotency_key=key, status="pending", available_at=utcnow(),
    )
    db.add(event)
    return event


def _cloud_task(event: DispatchOutbox | DispatchSnapshot) -> str:
    project = _setting("GOOGLE_CLOUD_PROJECT")
    location = _setting("ANALYSIS_TASKS_LOCATION")
    service_account = _setting("ANALYSIS_TASKS_SERVICE_ACCOUNT")
    queue, worker_url = _task_route(str(event.topic))
    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(project, location, queue)
    # The stable task name recovers a crash after creation but before DB marking.
    name = f"{parent}/tasks/{event.id}-{event.execution_attempts}"
    headers = {"Content-Type": "application/json"}
    token = os.getenv("ANALYSIS_TASK_TOKEN")
    if token:
        headers["X-HireWiz-Task-Token"] = token
    task = {
        "name": name,
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{worker_url}/internal/tasks/dispatch/{event.id}",
            "headers": headers,
            "body": json.dumps({"event_id": event.id}).encode(),
            "oidc_token": {
                "service_account_email": service_account,
                "audience": worker_url,
            },
        },
        "dispatch_deadline": {"seconds": 900},
    }
    try:
        return str(client.create_task(parent=parent, task=tasks_v2.Task(task)).name)
    except AlreadyExists:
        return name


def dispatch_pending(*, limit: int = 100, topics: set[str] | None = None) -> dict[str, int]:
    """Publish with short DB claims, leaving failed dispatches durable for a sweep."""
    mode = _dispatch_mode()
    if mode == "manual":
        return {"dispatched": 0, "failed": 0}
    if topics and not topics.issubset(TOPICS):
        raise DispatchConfigurationError("Unsupported dispatch topic")
    if mode == "cloud_tasks" and topics:
        for topic in topics:
            _task_route(topic)
    db = SessionLocal()
    sent = failed = 0
    try:
        now = utcnow()
        query = db.query(DispatchOutbox.id, DispatchOutbox.topic).filter(
            DispatchOutbox.available_at <= now,
            or_(
                DispatchOutbox.status == "pending",
                and_(DispatchOutbox.status == "publishing", DispatchOutbox.lease_until < now),
                and_(DispatchOutbox.status == "retry", DispatchOutbox.available_at <= now),
                and_(DispatchOutbox.status == "running", DispatchOutbox.lease_until < now),
                and_(DispatchOutbox.status == "dispatched", DispatchOutbox.updated_at < now - timedelta(hours=1)),
            ),
        )
        if topics:
            query = query.filter(DispatchOutbox.topic.in_(topics))
        events = query.order_by(DispatchOutbox.created_at).limit(limit).all()
        # Validate every selected route before claiming even the first event.
        if mode == "cloud_tasks":
            for topic in {row[1] for row in events}:
                _task_route(topic)
        for event_id, _ in events:
            token = uuid.uuid4().hex
            claimed = db.query(DispatchOutbox).filter(
                DispatchOutbox.id == event_id,
                or_(
                    DispatchOutbox.status == "pending",
                    and_(DispatchOutbox.status == "publishing", DispatchOutbox.lease_until < now),
                    and_(DispatchOutbox.status == "retry", DispatchOutbox.available_at <= now),
                    and_(DispatchOutbox.status == "running", DispatchOutbox.lease_until < now),
                    and_(DispatchOutbox.status == "dispatched", DispatchOutbox.updated_at < now - timedelta(hours=1)),
                ),
            ).update({
                "status": "publishing", "lease_token": token,
                "lease_until": now + timedelta(minutes=2), "updated_at": now,
                "dispatch_attempts": DispatchOutbox.dispatch_attempts + 1,
                "task_name": "local" if mode == "inline" else DispatchOutbox.task_name,
            }, synchronize_session=False)
            db.commit()
            if not claimed:
                continue
            event = db.get(DispatchOutbox, event_id)
            if event is None:
                continue
            snapshot = DispatchSnapshot(str(event.id), str(event.topic), int(event.execution_attempts or 0))
            db.rollback()
            try:
                if mode == "inline":
                    # Keep the publishing claim until the worker accepts it.
                    # Scope rejection occurs before the worker's running claim,
                    # so the publisher can record and retry that failure safely.
                    process_event(event_id)
                else:
                    task_name = _cloud_task(snapshot)
                    db.query(DispatchOutbox).filter_by(id=event_id, lease_token=token, status="publishing").update(
                        {"status": "dispatched", "task_name": task_name, "lease_token": None,
                         "lease_until": None, "last_error": None, "updated_at": utcnow()},
                        synchronize_session=False,
                    )
                    db.commit()
                sent += 1
            except Exception as exc:
                db.rollback()
                db.query(DispatchOutbox).filter_by(id=event_id, lease_token=token, status="publishing").update(
                    {"status": "pending", "task_name": None, "lease_token": None, "lease_until": None,
                     "available_at": utcnow() + timedelta(seconds=30),
                     "last_error": type(exc).__name__, "updated_at": utcnow()},
                    synchronize_session=False,
                )
                db.commit()
                failed += 1
                log.warning("Dispatch deferred event=%s error=%s", event_id, type(exc).__name__)
        return {"dispatched": sent, "failed": failed}
    finally:
        db.close()


def _execute(topic: str, aggregate_id: str) -> str:
    if topic == "analysis.run":
        from ..analysis.tasks import process_analysis_run
        return process_analysis_run(aggregate_id)
    from ..employer import service
    handler = {
        "employer.search": service.execute_search,
        "employer.refresh": service.refresh_source,
        "employer.apply": service.execute_application,
        "employer.artifact-delete": service.delete_artifact,
    }[topic]
    return str(handler(aggregate_id))


def _settle_exhausted(topic: str, aggregate_id: str) -> None:
    if topic != "analysis.run":
        from ..employer.service import fail_dispatched_work
        fail_dispatched_work(topic, aggregate_id)
        return
    from ... import models
    from ..usage import release_run_usage
    with SessionLocal() as db:
        run = db.query(models.AnalysisRun).filter_by(id=aggregate_id).with_for_update().first()
        if run and run.status not in {"succeeded", "failed", "cancelled"}:
            run.status = "failed"
            run.error_code = "dispatch_delivery_exhausted"
            run.error_message = "Work could not complete after bounded delivery attempts."
            run.completed_at = run.updated_at = utcnow()
            release_run_usage(db, run, reason="Worker delivery attempts exhausted")
            db.commit()


def process_event(event_id: str) -> str:
    db = SessionLocal()
    token = uuid.uuid4().hex
    try:
        allowed = {part.strip() for part in os.getenv("WORKER_ALLOWED_TOPICS", "").split(",") if part.strip()}
        if allowed:
            event = db.get(DispatchOutbox, event_id)
            if event and event.topic not in allowed:
                raise WorkerScopeError("This worker cannot execute the requested workload")
        now = utcnow()
        claimed = db.query(DispatchOutbox).filter(
            DispatchOutbox.id == event_id,
            or_(
                DispatchOutbox.status.in_(["pending", "publishing", "dispatched", "retry"]),
                and_(DispatchOutbox.status == "running", DispatchOutbox.lease_until < now),
            ),
        ).update({
            "status": "running", "lease_token": token, "lease_until": now + timedelta(minutes=16),
            "execution_attempts": DispatchOutbox.execution_attempts + 1, "updated_at": now,
        }, synchronize_session=False)
        db.commit()
        event = db.get(DispatchOutbox, event_id)
        if not event:
            return "missing"
        if not claimed:
            if event.status == "running":
                raise RetryableDispatchError("Work has a live lease")
            return str(event.status)
        topic, aggregate_id = str(event.topic), str(event.aggregate_id)
        attempts = int(event.execution_attempts or 0)
        # No transaction or row lock is retained while a provider is called.
        db.rollback()
        try:
            if attempts > 6:
                _settle_exhausted(topic, aggregate_id)
                db.query(DispatchOutbox).filter_by(id=event_id, lease_token=token, status="running").update(
                    {"status": "failed", "completed_at": utcnow(), "lease_token": None,
                     "lease_until": None, "last_error": "delivery_exhausted", "updated_at": utcnow()},
                    synchronize_session=False,
                )
                db.commit()
                return "failed"
            result = _execute(topic, aggregate_id)
        except Exception as exc:
            db.rollback()
            db.query(DispatchOutbox).filter_by(id=event_id, lease_token=token, status="running").update(
                {"status": "retry", "lease_token": None, "lease_until": None,
                 "available_at": utcnow() + timedelta(hours=1),
                 "last_error": type(exc).__name__, "updated_at": utcnow()},
                synchronize_session=False,
            )
            db.commit()
            # Domain handlers own terminal settlement and possible-sent states.
            # A task retry re-reads those states; it never directly repeats POST.
            raise RetryableDispatchError(type(exc).__name__) from exc
        db.query(DispatchOutbox).filter_by(id=event_id, lease_token=token, status="running").update(
            {"status": "completed", "completed_at": utcnow(), "lease_token": None,
             "lease_until": None, "last_error": None, "updated_at": utcnow()},
            synchronize_session=False,
        )
        db.commit()
        return result
    finally:
        db.close()
