"""Actual auth route guards precede dependencies/native lifecycle and body errors."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from backend.tests.fixtures.candidate_ingress import create, signed_headers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from test_candidate_scoped_lifecycle_emulator import service as service
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_scoped_authority_emulator import scoped as scoped

from app.database import get_db
from app.domains.candidate_accounts import service as lifecycle
from app.domains.candidate_ingress import replay
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.models import CandidatePasswordAccount, User
from app.rate_limiter import limiter
from app.routers import auth, candidate_accounts
from app.security import create_access_token

PASSWORD = "synthetic-ingress-http-password-only"
EMAIL = "http-ingress@example.com"
REGISTER = {"email": EMAIL, "password": PASSWORD, "accepted_terms": True, "confirmed_age_18": True}


def app_for(c=None):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth.router, prefix="/api")
    app.include_router(candidate_accounts.router, prefix="/api")
    if c is not None:

        def database():
            with c.account_service.sessions() as db:
                yield db

        app.dependency_overrides[get_db] = database
    return app


OPERATIONS = [
    ("GET", "availability", None),
    ("GET", "session", None),
    ("POST", "register", REGISTER),
    ("POST", "login", {"email": EMAIL, "password": PASSWORD}),
    ("POST", "registration-status", {"email": EMAIL, "password": PASSWORD}),
    ("POST", "logout", None),
    ("POST", "web-logout", {"operation_id": str(uuid4())}),
    ("POST", "password", {"current_password": PASSWORD, "new_password": PASSWORD + "2"}),
]


@pytest.mark.parametrize("method,suffix,value", OPERATIONS)
@pytest.mark.parametrize("header", [None, "ordinary-public-header"])
def test_all_native_operations_reject_public_input_before_lifecycle_or_db(
    method, suffix, value, header, monkeypatch
):
    calls = []

    def forbidden():
        calls.append(True)
        raise AssertionError("Native lifecycle or auth dependency entered by public caller")

    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", forbidden)
    app = app_for()
    app.dependency_overrides[get_db] = forbidden
    raw = json.dumps(value).encode() if value is not None else b""
    headers = {"Content-Type": "application/json"}
    if header is not None:
        headers["x-hirewiz-candidate-ingress"] = header
    # An ordinary header cannot unlock the explicitly configured actual ingress.
    ingress = create()
    monkeypatch.setattr(replay, "production_candidate_ingress", lambda: ingress.ingress)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            result = client.request(
                method, "/api/auth/candidate/v1/" + suffix, content=raw, headers=headers
            )
        assert result.status_code == 403 and calls == []
        assert result.json() == {"detail": "Private candidate transport authentication failed"}
        assert result.headers["cache-control"] == "private, no-store"
        assert PASSWORD not in result.text
    finally:
        ingress.channel.close()


@pytest.mark.parametrize("persisted", [False, True])
def test_actual_http_unknown_replay_commit_has_no_native_lifecycle_call(persisted, monkeypatch):
    calls = []

    def forbidden():
        calls.append(True)
        raise AssertionError("Unacknowledged ingress reached native lifecycle")

    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", forbidden)
    ingress = create()
    monkeypatch.setattr(replay, "production_candidate_ingress", lambda: ingress.ingress)
    commit = ingress.rpc.commit

    def lose(tx, writes):
        if persisted:
            commit(tx, writes)
        else:
            ingress.rpc.rollback(tx)
        raise AmbiguousCommit("Owned synthetic ingress acknowledgement loss")

    monkeypatch.setattr(ingress.rpc, "commit", lose)
    path, raw = "/api/auth/candidate/v1/register", json.dumps(REGISTER).encode()
    try:
        with TestClient(app_for(), base_url="https://testserver") as client:
            result = client.post(path, content=raw, headers=signed_headers(ingress, path, raw))
        assert result.status_code == 503 and calls == []
        assert result.json() == {"detail": "Private candidate transport is unavailable"}
    finally:
        ingress.channel.close()


@pytest.mark.parametrize("variation", ["http", "query", "encoded"])
def test_valid_server_assertion_cannot_change_physical_request(variation, monkeypatch):
    ingress = create()
    monkeypatch.setattr(replay, "production_candidate_ingress", lambda: ingress.ingress)
    path, raw = (
        "/api/auth/candidate/v1/login",
        json.dumps({"email": EMAIL, "password": PASSWORD}).encode(),
    )
    try:
        with TestClient(
            app_for(), base_url="http://testserver" if variation == "http" else "https://testserver"
        ) as client:
            actual = (
                path + "?extra=1"
                if variation == "query"
                else path.replace("/auth/", "/%61uth/")
                if variation == "encoded"
                else path
            )
            result = client.post(actual, content=raw, headers=signed_headers(ingress, path, raw))
        assert result.status_code == 403
    finally:
        ingress.channel.close()


def test_real_scoped_http_enrollment_mapped_legacy_guard_and_no_public_deletion(
    service, monkeypatch
):
    c = service
    limiter._storage.reset()
    monkeypatch.setattr(lifecycle, "production_candidate_accounts", lambda: c.account_service)
    monkeypatch.setattr(
        candidate_accounts, "production_candidate_accounts", lambda: c.account_service
    )
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "true")
    ingress = create(action_pins=(c.pin, c.v3.resource.pin.authority.registry))
    monkeypatch.setattr(replay, "production_candidate_ingress", lambda: ingress.ingress)
    app = app_for(c)
    try:
        with TestClient(app, base_url="https://testserver") as client:
            path, raw = "/api/auth/register", json.dumps(REGISTER).encode()
            before = len(c.commits), len(c.http.objects)
            assert (
                client.post(
                    path, content=raw, headers={"Content-Type": "application/json"}
                ).status_code
                == 403
            )
            with c.account_service.sessions() as db:
                assert (
                    db.query(User).count() == 0 and db.query(CandidatePasswordAccount).count() == 0
                )
            assert before == (len(c.commits), len(c.http.objects))
            registered = client.post(path, content=raw, headers=signed_headers(ingress, path, raw))
            assert registered.status_code == 200
            path, raw = (
                "/api/auth/login",
                json.dumps({"email": EMAIL, "password": PASSWORD}).encode(),
            )
            before = len(c.commits), len(c.http.objects)
            rejected = client.post(path, content=raw, headers={"Content-Type": "application/json"})
            assert rejected.status_code == 403 and "access_token" not in rejected.text
            assert before == (len(c.commits), len(c.http.objects))
            logged = client.post(path, content=raw, headers=signed_headers(ingress, path, raw))
            assert (
                logged.status_code == 200
                and logged.json()["browser_pairing_session"]["candidate_id"]
                == registered.json()["id"]
            )
            bearer = "Bearer " + logged.json()["access_token"]
            before = len(c.commits), len(c.http.objects)
            rejected = client.post("/api/auth/delete-account", headers={"Authorization": bearer})
            assert rejected.status_code == 403 and before == (len(c.commits), len(c.http.objects))
            # Numeric legacy JWT has no native context and cannot downgrade.
            numeric = create_access_token(subject=str(registered.json()["id"]))
            assert (
                client.post(
                    "/api/auth/delete-account", headers={"Authorization": "Bearer " + numeric}
                ).status_code
                == 401
            )
            with c.account_service.sessions() as db:
                assert db.query(CandidatePasswordAccount).one().state == "ACTIVE"
    finally:
        ingress.channel.close()
