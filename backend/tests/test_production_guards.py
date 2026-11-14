import os
import subprocess
import sys
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _database_import(*, app_env: str, database_url: str | None):
    env = os.environ.copy()
    env.pop("PYTEST_CURRENT_TEST", None)
    env["APP_ENV"] = app_env
    if database_url is None:
        env.pop("DATABASE_URL", None)
    else:
        env["DATABASE_URL"] = database_url
    return subprocess.run(
        [sys.executable, "-c", "import backend.app.database"],
        env=env,
        cwd=REPOSITORY_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_production_requires_durable_database_configuration():
    missing = _database_import(app_env="production", database_url=None)
    assert missing.returncode != 0
    assert "DATABASE_URL must be configured" in (missing.stderr + missing.stdout)

    sqlite = _database_import(app_env="production", database_url="sqlite:///./prod.db")
    assert sqlite.returncode != 0
    assert "SQLite is not supported" in (sqlite.stderr + sqlite.stdout)


def test_development_may_use_local_sqlite():
    development = _database_import(app_env="development", database_url="sqlite:///./dev.db")
    assert development.returncode == 0, development.stderr


@pytest.mark.parametrize("code,invalidated,expected", [
    ("53000", False, 503), ("08006", False, 503), (None, True, 503),
    ("28P01", False, 500), ("40001", False, 500), (None, False, 500),
])
def test_owned_api_classifies_only_database_unavailability(monkeypatch, caplog, code, invalidated, expected):
    from backend.app.database import get_db
    from backend.app.main import app
    from backend.app.security import create_access_token

    driver_error = psycopg.errors.lookup(code) if code else psycopg.OperationalError
    private = "SYNTHETIC_PRIVATE_DRIVER_DETAIL"
    def database():
        raise OperationalError(None, None, driver_error(private), connection_invalidated=invalidated)

    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/auth/bootstrap", headers={
            "Authorization": f"Bearer {create_access_token(subject='1')}",
        })
    assert response.status_code == expected
    assert private not in response.text and private not in caplog.text
    assert "no-store" in response.headers["cache-control"]
    if expected == 503:
        assert response.json()["detail"] == {
            "code": "database_unavailable", "message": "Saved information is temporarily unavailable. Please try again shortly.",
        }
        assert response.headers["retry-after"] == "60"
    else:
        assert response.json()["detail"] == "Internal server error"
        assert "retry-after" not in response.headers


def test_connect_failure_without_sqlstate_is_classified_at_checkout(monkeypatch):
    from backend.app.database import get_db, mark_database_connect_failure
    from backend.app.main import app
    from backend.app.security import create_access_token
    from sqlalchemy import event

    attempts = []
    def connect():
        attempts.append(1)
        raise psycopg.OperationalError("SYNTHETIC_REFUSED_CONNECTION")
    engine = create_engine("postgresql+psycopg://", creator=connect)
    event.listen(engine, "handle_error", mark_database_connect_failure)
    def database():
        with Session(engine) as session:
            yield session
    monkeypatch.setitem(app.dependency_overrides, get_db, database)
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/api/auth/bootstrap", headers={
                "Authorization": f"Bearer {create_access_token(subject='1')}",
            })
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "database_unavailable"
        assert attempts == [1]
    finally:
        engine.dispose()


def test_database_outage_preserves_signed_razorpay_callback_failure(monkeypatch):
    from backend.app.main import app
    from backend.app.routers import billing
    from backend.tests.test_razorpay_billing import TEST_WEBHOOK_SECRET, _signature

    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RAZORPAY_MODE", "test")
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    calls = []
    def refuse(_db, **_kwargs):
        calls.append(1)
        raise OperationalError(None, None, psycopg.errors.InsufficientResources("SYNTHETIC_WEBHOOK_DATABASE_ERROR"))
    monkeypatch.setattr(billing, "process_razorpay_webhook", refuse)
    raw = b'{"event":"payment.captured"}'
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/billing/webhooks/razorpay", content=raw, headers={
            "x-razorpay-signature": _signature(raw), "x-razorpay-event-id": "synthetic-event",
        })
    assert calls == [1]
    assert response.status_code == 500 and response.json()["detail"] == "Internal server error"
    assert "retry-after" not in response.headers
