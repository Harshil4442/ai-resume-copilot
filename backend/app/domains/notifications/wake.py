"""Optional exact-ID fallback for committed security link email."""
from __future__ import annotations

import logging
import os
import re
from datetime import UTC, datetime, timedelta

from google.api_core.exceptions import GoogleAPICallError
from google.auth.exceptions import GoogleAuthError
from google.cloud import tasks_v2
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from ...models import NotificationOutbox
from ..common import utcnow
from .privacy_erasure import PENDING as ERASURE_PENDING
from .service import SecurityEmailRetry, retry_security_email

QUEUE = "hirewiz-security-mail-wake"
PATH = "/internal/security-mail/wake"
RPC_TIMEOUT_SECONDS = 2.0
WAKE_DELAY = timedelta(seconds=30)
DISPATCH_DEADLINE = timedelta(seconds=60)
PENDING_STATUS = {"password_reset": "pending", "privacy_erasure": ERASURE_PENDING}

_logger = logging.getLogger(__name__)


def _target() -> tuple[str, str] | None:
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("SECURITY_MAIL_TASK_LOCATION", "")
    queue = os.getenv("SECURITY_MAIL_TASK_QUEUE", "")
    origin = os.getenv("SECURITY_MAIL_WORKER_AUDIENCE", "")
    if (
        not re.fullmatch(r"[a-z][a-z0-9-]{4,28}[a-z0-9]", project)
        or not re.fullmatch(r"[a-z]+-[a-z]+[1-9][0-9]*", location)
        or queue != QUEUE
        or not re.fullmatch(
            r"https://hirewiz-security-mail-worker-[a-z0-9-]+(?:\.[a-z]+-[a-z]+[1-9][0-9]*)?\.run\.app", origin
        )
    ):
        return None
    return f"projects/{project}/locations/{location}/queues/{queue}", origin


def _schedule_window(db: Session, notification_id: str) -> tuple[datetime, datetime] | None:
    table = NotificationOutbox
    try:
        row = db.query(table.notification_type, table.status, table.available_at, table.payload["expires_at_ms"]).filter(
            table.id == notification_id, table.channel == "email", table.notification_type.in_(PENDING_STATUS)
        ).one_or_none()
    finally:
        # Callers have already committed. Never retain a database transaction over a provider call.
        db.rollback()
    if row is None:
        return None
    notification_type, status, available, expiry_ms = row
    if status != PENDING_STATUS[notification_type] or type(expiry_ms) is not int:
        return None
    try:
        expires = datetime.fromtimestamp(expiry_ms / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    scheduled = max(utcnow() + WAKE_DELAY, available.replace(tzinfo=UTC))
    return (scheduled, expires) if scheduled < expires else None


def _confirm(parent: str, origin: str, notification_id: str, scheduled: datetime, expires: datetime) -> bool:
    # Queue-level OIDC binds its distinct invoker to the private receiver. No
    # task-level identity means the publisher does not need invoker actAs.
    task = tasks_v2.Task(
        name=f"{parent}/tasks/security-mail-{notification_id}",
        http_request=tasks_v2.HttpRequest(
            http_method=tasks_v2.HttpMethod.POST, url=f"{origin}{PATH}/{notification_id}",
        ),
        schedule_time=scheduled,
        dispatch_deadline=DISPATCH_DEADLINE,
    )
    try:
        with tasks_v2.CloudTasksClient() as client:
            created = client.create_task(
                request=tasks_v2.CreateTaskRequest(parent=parent, task=task, response_view=tasks_v2.Task.View.BASIC),
                retry=None, timeout=RPC_TIMEOUT_SECONDS,
            )
    except (GoogleAPICallError, GoogleAuthError):
        # Also covers AlreadyExists: a recently deleted name is not an acknowledgement.
        return False
    return created.name == task.name and utcnow() < expires


def publish_link_wake(db: Session, notification_id: str) -> bool:
    """Return a fresh acknowledgement only for a bounded, exact security link task."""
    if os.getenv("SECURITY_MAIL_TASK_WAKE_ENABLED", "false") != "true":
        return False
    if not re.fullmatch(r"ntf_[a-f0-9]{32}", notification_id) or db.in_transaction():
        return False
    target = _target()
    if target is None:
        _logger.warning("Security email task configuration unavailable")
        return False
    try:
        window = _schedule_window(db, notification_id)
    except SQLAlchemyError:
        _logger.warning("Security email task schedule unavailable")
        return False
    if window is None:
        return False
    if not _confirm(*target, notification_id, *window):
        _logger.warning("Security email task acknowledgement unavailable")
        return False
    return True


def deliver_link_with_wake(db: Session, notification_id: str) -> SecurityEmailRetry:
    # Keep immediate delivery, including when queue acknowledgement is unavailable.
    # The existing periodic sweep remains the safety net for unpublished work.
    publish_link_wake(db, notification_id)
    return retry_security_email(db, notification_id)
