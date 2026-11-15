from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from backend.app import database, resume_upload_worker
from backend.app import security_mail_worker as worker
from backend.app.domains.common import utcnow
from backend.app.domains.notifications import password_reset
from backend.app.models import NotificationOutbox
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

IDENTITY = "ntf_" + "2" * 32
PATH = worker.ROUTE + "/" + IDENTITY
PROJECT = "synthetic-mail"
ORIGIN = "https://hirewiz-security-mail-worker-synthetic.us-central1.run.app"
EMAIL = f"hirewiz-security-mail-task@{PROJECT}.iam.gserviceaccount.com"
SUBJECT = "123456789012345678901"
HEADERS = {"Authorization": "Bearer aaaa.bbbb.cccc"}
CLAIMS = {"iss": "https://accounts.google.com", "aud": ORIGIN, "email": EMAIL, "sub": SUBJECT, "email_verified": True}


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("SERVICE_ROLE", "security-mail-worker")
    monkeypatch.setenv("SECURITY_MAIL_WORKER_ENABLED", "true")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", PROJECT)
    monkeypatch.setenv("SECURITY_MAIL_WORKER_AUDIENCE", ORIGIN)
    monkeypatch.setenv("SECURITY_MAIL_WORKER_CALLER_EMAIL", EMAIL)
    monkeypatch.setenv("SECURITY_MAIL_WORKER_CALLER_SUBJECT", SUBJECT)
    verify = MagicMock(return_value=CLAIMS)
    monkeypatch.setattr(resume_upload_worker, "_verified_claims", verify)
    with TestClient(worker.app, follow_redirects=False) as client:
        yield client, verify


@pytest.mark.parametrize(("outcome", "status"), [("sent", 204), ("finished", 204), ("retry", 409), ("unavailable", 503)])
def test_only_terminal_states_acknowledge_bound_task(transport, monkeypatch, outcome, status):
    client, verify = transport
    execute = MagicMock(return_value=outcome)
    monkeypatch.setattr(worker, "execute", execute)
    response = client.post(PATH, headers=HEADERS)
    assert response.status_code == status and response.headers["Cache-Control"] == "private, no-store"
    verify.assert_called_once_with("aaaa.bbbb.cccc", ORIGIN)
    execute.assert_called_once_with(IDENTITY)
    if status == 204:
        assert not response.content


@pytest.mark.parametrize(("claim", "value"), [
    ("sub", "223456789012345678901"),
    ("iss", "https://example.invalid"),
    ("aud", "https://hirewiz-resume-coordinator-synthetic.run.app"),
    ("email", f"other-worker@{PROJECT}.iam.gserviceaccount.com"),
    ("email_verified", False),
])
def test_every_caller_binding_is_checked_before_database(transport, monkeypatch, claim, value):
    client, verify = transport
    verify.return_value = {**CLAIMS, claim: value}
    execute = MagicMock()
    monkeypatch.setattr(worker, "execute", execute)
    assert client.post(PATH, headers=HEADERS).status_code == 403
    execute.assert_not_called()


@pytest.mark.parametrize(("setting", "value"), [
    ("SECURITY_MAIL_WORKER_ENABLED", "false"),
    ("SECURITY_MAIL_WORKER_ENABLED", "TRUE"),
    ("SERVICE_ROLE", "worker"),
    ("SECURITY_MAIL_WORKER_CALLER_EMAIL", f"other-worker@{PROJECT}.iam.gserviceaccount.com"),
    ("SECURITY_MAIL_WORKER_CALLER_SUBJECT", ""),
    ("SECURITY_MAIL_WORKER_AUDIENCE", "https://hirewiz-resume-coordinator-synthetic.run.app"),
    ("SECURITY_MAIL_WORKER_AUDIENCE", "https://hirewiz-security-mail-worker---other-synthetic.run.app"),
])
def test_disabled_or_misconfigured_receiver_does_not_verify_or_execute(transport, monkeypatch, setting, value):
    client, verify = transport
    monkeypatch.setenv(setting, value)
    execute = MagicMock()
    monkeypatch.setattr(worker, "execute", execute)
    assert client.post(PATH, headers=HEADERS).status_code == 503
    verify.assert_not_called()
    execute.assert_not_called()


@pytest.mark.parametrize(("method", "path", "body", "status"), [
    ("POST", PATH + "?query=value", "", 400),
    ("POST", PATH.replace("ntf_", "old_"), "", 400),
    ("POST", PATH.replace(IDENTITY, "ntf_%32" + "2" * 31), "", 400),
    ("POST", PATH, '{"forbidden":"body"}', 400),
    ("POST", PATH + "/", "", 404),
    ("GET", PATH, "", 405),
    ("POST", worker.ROUTE + "/extra/" + IDENTITY, "", 404),
])
def test_route_refusals_never_execute(transport, monkeypatch, method, path, body, status):
    client, _ = transport
    execute = MagicMock()
    monkeypatch.setattr(worker, "execute", execute)
    assert client.request(method, path, headers=HEADERS, content=body).status_code == status
    execute.assert_not_called()


def test_task_headers_are_not_authority_and_private_error_stays_redacted(transport, monkeypatch, caplog):
    client, verify = transport
    execute = MagicMock(side_effect=RuntimeError("synthetic-private-error"))
    monkeypatch.setattr(worker, "execute", execute)
    for headers in ({"X-CloudTasks-TaskName": "forged"}, [*HEADERS.items(), *HEADERS.items()]):
        assert client.post(PATH, headers=headers).status_code == 401
    verify.assert_not_called()
    execute.assert_not_called()

    response = client.post(PATH, headers=HEADERS)
    assert response.status_code == 503
    assert "synthetic-private-error" not in response.text and "synthetic-private-error" not in caplog.text
    execute.assert_called_once_with(IDENTITY)


def test_exact_worker_scope_cannot_send_a_password_change_notice(transport, monkeypatch):
    client, _ = transport
    engine = create_engine("sqlite://", hide_parameters=True, poolclass=StaticPool, connect_args={"check_same_thread": False})
    NotificationOutbox.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    send = MagicMock()
    monkeypatch.setattr(password_reset, "deliver", send)
    with factory() as db:
        db.add(NotificationOutbox(id=IDENTITY, notification_type="password_changed", status="pending",
            recipient="synthetic@example.invalid", idempotency_key="synthetic-unapproved-notice",
            available_at=utcnow() - timedelta(minutes=1)))
        db.commit()
    assert client.post(PATH, headers=HEADERS).status_code == 204
    send.assert_not_called()
    with factory() as db:
        assert db.get(NotificationOutbox, IDENTITY).status == "pending"
    engine.dispose()
