"""Closed optional features refuse before authentication/SQL/native setup."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.database import get_db
from app.domains.notifications import native
from app.feature_flags import decisions_for_user
from app.routers import notification_preferences as routes
from app.security import get_current_user


@pytest.fixture
def client(monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Closed digest preferences must not acquire accounts, SQL or native clients")

    app = FastAPI()
    app.include_router(routes.router, prefix="/api")
    app.dependency_overrides[get_current_user] = forbidden
    app.dependency_overrides[get_db] = forbidden
    monkeypatch.setattr(native, "production_candidate_accounts", forbidden)
    with TestClient(app) as client:
        yield client, app


@pytest.mark.parametrize("method", ["GET", "PUT"])
@pytest.mark.parametrize("digest,lifecycle", [(None, None), ("false", "true"), ("true", "false"), ("1", "true")])
def test_closed_or_malformed_flags_refuse_before_expensive_authentication(client, monkeypatch, method, digest, lifecycle):
    transport, _ = client
    for name, value in (("NOTIFICATION_DIGESTS_ENABLED", digest), ("LIFECYCLE_EMAILS_ENABLED", lifecycle)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    payload = {"expected_generation": 0, "weekly_digest": True, "explicit_confirmation": True}
    response = transport.request(method, "/api/v1/notifications/preferences",
                                 **({"json": payload} if method == "PUT" else {}))
    monkeypatch.setenv("FEATURE_WEEKLY_DIGEST_ENABLED", "true")
    monkeypatch.setenv("FEATURE_WEEKLY_DIGEST_ROLLOUT_PERCENT", "100")
    assert decisions_for_user(user_id=1, email="candidate@example.invalid")["weekly_digest"]["enabled"] is False
    assert response.status_code == 503
    assert "no-store" in response.headers["Cache-Control"]
    assert response.json() == {"detail": "Notification settings are temporarily unavailable. Check current settings before retrying a change."}


def test_enabled_flags_still_require_candidate_authentication(client, monkeypatch):
    transport, app = client
    called = []

    def unauthenticated():
        called.append("candidate-auth")
        raise HTTPException(401, "Sign in required")

    app.dependency_overrides[get_current_user] = unauthenticated
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "true")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    assert decisions_for_user(user_id=1, email="candidate@example.invalid")["weekly_digest"]["enabled"] is True
    assert transport.get("/api/v1/notifications/preferences").status_code == 401
    assert called == ["candidate-auth"]
