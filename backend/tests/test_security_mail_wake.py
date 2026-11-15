from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from backend.app.domains.common import utcnow
from backend.app.domains.notifications import wake
from backend.app.models import NotificationOutbox
from google.api_core.exceptions import AlreadyExists, DeadlineExceeded
from google.cloud import tasks_v2
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

IDENTIFIER = "ntf_" + "2" * 32
ORIGIN = "https://hirewiz-security-mail-worker-example.us-central1.run.app"
PARENT = "projects/synthetic-project/locations/us-central1/queues/hirewiz-security-mail-wake"


@pytest.fixture
def task_client(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "synthetic-project")
    monkeypatch.setenv("SECURITY_MAIL_TASK_LOCATION", "us-central1")
    monkeypatch.setenv("SECURITY_MAIL_TASK_QUEUE", "hirewiz-security-mail-wake")
    monkeypatch.setenv("SECURITY_MAIL_WORKER_AUDIENCE", ORIGIN)
    monkeypatch.setenv("SECURITY_MAIL_TASK_WAKE_ENABLED", "true")
    constructor = MagicMock()
    monkeypatch.setattr(tasks_v2, "CloudTasksClient", constructor)
    client = constructor.return_value.__enter__.return_value
    client.create_task.side_effect = lambda *, request, **_: tasks_v2.Task(name=request.task.name)
    return constructor, client


@pytest.fixture
def outbox():
    engine = create_engine("sqlite://", hide_parameters=True)
    NotificationOutbox.__table__.create(engine)
    with sessionmaker(bind=engine)() as db:
        yield db
    engine.dispose()


def add_link(db, now, *, category="password_reset", status="pending", expiry=None, channel="email", commit=True):
    db.add(NotificationOutbox(
        id=IDENTIFIER, notification_type=category, status=status, channel=channel,
        available_at=now, recipient="synthetic@example.invalid", idempotency_key="synthetic-proof",
        payload={"expires_at_ms": expiry if expiry is not None else int((now + timedelta(minutes=10)).timestamp() * 1000),
                 "ciphertext": "never-export-this-field"},
    ))
    if commit:
        db.commit()


@pytest.mark.parametrize(("category", "status"), [("password_reset", "pending"), ("privacy_erasure", "erasure_pending")])
def test_committed_link_confirms_only_exact_bodyless_task_with_no_database_transaction(
    task_client, outbox, monkeypatch, category, status,
):
    now = utcnow()
    monkeypatch.setattr(wake, "utcnow", lambda: now)
    add_link(outbox, now, category=category, status=status)
    _, client = task_client

    def confirmed(*, request, retry, timeout):
        assert not outbox.in_transaction()
        assert request.parent == PARENT
        assert request.response_view == tasks_v2.Task.View.BASIC
        assert retry is None and timeout == 2
        return tasks_v2.Task(name=request.task.name)

    client.create_task.side_effect = confirmed
    assert wake.publish_link_wake(outbox, IDENTIFIER)
    task = client.create_task.call_args.kwargs["request"].task
    assert task.name == f"{PARENT}/tasks/security-mail-{IDENTIFIER}"
    assert task.schedule_time == now + timedelta(seconds=30)
    assert task.dispatch_deadline == timedelta(seconds=60)
    assert task.http_request.http_method == tasks_v2.HttpMethod.POST
    assert task.http_request.url == f"{ORIGIN}/internal/security-mail/wake/{IDENTIFIER}"
    assert not task.http_request.body and not task.http_request.headers
    assert not task.http_request.oidc_token and not task.http_request.oauth_token
    client.get_task.assert_not_called()
    assert outbox.get(NotificationOutbox, IDENTIFIER).status == status


@pytest.mark.parametrize("outcome", [AlreadyExists, DeadlineExceeded, None])
def test_unconfirmed_task_uses_same_immediate_delivery_with_no_alternate_publication(
    task_client, outbox, monkeypatch, outcome,
):
    add_link(outbox, utcnow())
    _, client = task_client
    client.create_task.side_effect = outcome("synthetic") if outcome else None
    client.create_task.return_value = tasks_v2.Task(name="wrong-name")
    result = wake.SecurityEmailRetry("sent")
    deliver = MagicMock(return_value=result)
    monkeypatch.setattr(wake, "retry_security_email", deliver)
    assert wake.deliver_link_with_wake(outbox, IDENTIFIER) == result
    deliver.assert_called_once_with(outbox, IDENTIFIER)
    assert client.create_task.call_count == 1
    client.get_task.assert_not_called()


@pytest.mark.parametrize(("category", "status", "expiry", "channel"), [
    ("welcome", "pending", None, "email"),
    ("password_changed", "pending", None, "email"),
    ("password_reset", "mail_unknown", None, "email"),
    ("privacy_erasure", "erasure_processing", None, "email"),
    ("password_reset", "pending", 1, "email"),
    ("password_reset", "pending", "not-an-integer", "email"),
    ("password_reset", "pending", None, "other"),
])
def test_wrong_kind_state_channel_or_expiry_never_enters_wake(task_client, outbox, category, status, expiry, channel):
    add_link(outbox, utcnow(), category=category, status=status, expiry=expiry, channel=channel)
    assert not wake.publish_link_wake(outbox, IDENTIFIER)
    task_client[0].assert_not_called()
    assert not outbox.in_transaction()


@pytest.mark.parametrize(("name", "value"), [
    ("SECURITY_MAIL_TASK_WAKE_ENABLED", "false"),
    ("SECURITY_MAIL_TASK_WAKE_ENABLED", "TRUE"),
    ("SECURITY_MAIL_TASK_QUEUE", "hirewiz-resume-wake"),
    ("SECURITY_MAIL_WORKER_AUDIENCE", ORIGIN + "/wrong"),
    ("SECURITY_MAIL_WORKER_AUDIENCE", "https://hirewiz-resume-coordinator-example.run.app"),
])
def test_disabled_or_misbound_publication_uses_no_database_or_cloud(monkeypatch, task_client, name, value):
    monkeypatch.setenv(name, value)
    db = MagicMock()
    db.in_transaction.return_value = False
    assert not wake.publish_link_wake(db, IDENTIFIER)
    db.query.assert_not_called()
    task_client[0].assert_not_called()


def test_uncommitted_link_never_enters_queue_and_retains_callers_transaction(task_client, outbox):
    add_link(outbox, utcnow(), commit=False)
    outbox.flush()
    assert not wake.publish_link_wake(outbox, IDENTIFIER)
    task_client[0].assert_not_called()
    assert outbox.in_transaction()
    outbox.commit()
    assert wake.publish_link_wake(outbox, IDENTIFIER)
