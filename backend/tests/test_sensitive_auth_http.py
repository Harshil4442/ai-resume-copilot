"""Actual FastAPI/ASGI privacy and bounded-read probes, all synthetic inputs."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.models import User
from app.rate_limiter import limiter
from app.routers import auth, candidate_accounts, sensitive_auth
from app.security import get_current_user

PRIVATE = "synthetic-private-auth-value"
VALID = {"email": "candidate@example.com", "password": "synthetic-password-123"}
CONSENT = {"accepted_terms": True, "confirmed_age_18": True}


def privacy_app():
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth.router, prefix="/api")
    app.include_router(candidate_accounts.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: None
    # This fixture tests parsing only; it does not issue or prove any identity.
    app.dependency_overrides[get_current_user] = lambda: User(id=1)
    return app


@pytest.mark.parametrize("prefix", ["/api/auth", "/api/auth/candidate/v1"])
@pytest.mark.parametrize("route,body", [
    ("register", {**VALID, **CONSENT, "password": "secret"}),
    ("register", {**VALID, **CONSENT, "email": PRIVATE}),
    ("login", {**VALID, "private_unknown": PRIVATE}),
    ("login", {**VALID, "password": {"private_nested": PRIVATE}}),
])
def test_validation_never_echoes_password_private_fields_or_email(prefix, route, body, caplog):
    limiter._storage.reset()
    with TestClient(privacy_app()) as client:
        result = client.post(prefix + "/" + route, json=body)
    assert result.status_code == 422
    assert result.json() == {"detail": sensitive_auth.AUTH_INPUT_ERROR}
    assert PRIVATE not in result.text and "secret" not in result.text
    assert PRIVATE not in caplog.text and "synthetic-password-123" not in caplog.text
    assert result.headers["cache-control"] == "no-store, private"


@pytest.mark.parametrize("body", [
    {"id_token": PRIVATE},
    {"id_token": "x" * 200, "private_extra": PRIVATE},
    {"id_token": {"nested": PRIVATE}},
])
def test_google_token_validation_is_nonreflecting(body, caplog):
    with TestClient(privacy_app()) as client:
        result = client.post("/api/auth/google-login", json=body)
    assert result.status_code == 422
    assert result.json() == {"detail": sensitive_auth.AUTH_INPUT_ERROR}
    assert PRIVATE not in result.text + caplog.text


def test_password_change_validation_hides_both_passwords_and_header(caplog):
    with TestClient(privacy_app()) as client:
        result = client.post("/api/auth/candidate/v1/password", json={
            "current_password": PRIVATE, "new_password": "short"},
            headers={"authorization": "Bearer synthetic-private-bearer"})
    assert result.status_code == 422
    assert result.json() == {"detail": sensitive_auth.AUTH_INPUT_ERROR}
    assert PRIVATE not in result.text + caplog.text
    assert "synthetic-private-bearer" not in result.text + caplog.text


def test_unexpected_google_provider_exception_never_logs_token_or_raw_error(monkeypatch, caplog):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-client")
    token = PRIVATE * 10
    def fail(*args):
        raise RuntimeError(token + " private provider response")
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", fail)
    with TestClient(privacy_app()) as client:
        result = client.post("/api/auth/google-login", json={"id_token": token})
    assert result.status_code == 500
    assert result.json() == {"detail": sensitive_auth.AUTH_INPUT_ERROR}
    assert "Sensitive authentication handler failed" in caplog.text
    assert PRIVATE not in result.text + caplog.text and "private provider response" not in caplog.text


async def asgi_request(chunks, *, headers=(), delayed=None):
    app = privacy_app()
    calls, sent = [], []
    pending = iter(chunks)
    cancelled = []
    async def receive():
        calls.append(True)
        if delayed is not None:
            try:
                await asyncio.sleep(delayed)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return next(pending)
    async def send(message):
        sent.append(message)
    scope = {"type": "http", "asgi": {"version": "3.0"}, "method": "POST",
             "path": "/api/auth/candidate/v1/login", "raw_path": b"/api/auth/candidate/v1/login",
             "query_string": b"", "root_path": "", "scheme": "http", "http_version": "1.1",
             "headers": [(b"content-type", b"application/json"), *headers],
             "client": ("127.0.0.1", 12345), "server": ("localhost", 80)}
    await app(scope, receive, send)
    status = next(x["status"] for x in sent if x["type"] == "http.response.start")
    body = b"".join(x.get("body", b"") for x in sent if x["type"] == "http.response.body")
    assert PRIVATE.encode() not in body
    return status, len(calls), cancelled


def chunk(body, more=False):
    return {"type": "http.request", "body": body, "more_body": more}


@pytest.mark.parametrize("headers,expected", [
    ([(b"content-length", b"8193")], 413),
    ([(b"content-length", b"-1")], 400),
    ([(b"content-length", b"999999999999999999999999999999999999")], 400),
    ([(b"content-length", b"10"), (b"content-length", b"10")], 400),
    ([(b"content-length", b"10"), (b"transfer-encoding", b"chunked")], 400),
    ([(b"content-encoding", b"gzip")], 415),
])
def test_invalid_metadata_rejected_before_any_body_receive(headers, expected):
    status, calls, _ = asyncio.run(asgi_request([chunk(PRIVATE.encode())], headers=headers))
    assert status == expected and calls == 0


@pytest.mark.parametrize("chunks,expected_reads", [
    ([chunk(b"x" * 8193, True), chunk(PRIVATE.encode())], 1),
    ([chunk(b"x" * 4096, True), chunk(b"x" * 4097, True), chunk(PRIVATE.encode())], 2),
])
def test_chunked_body_limit_stops_without_reading_private_tail(chunks, expected_reads):
    status, calls, _ = asyncio.run(asgi_request(chunks))
    assert status == 413 and calls == expected_reads


def test_one_total_deadline_cancels_slow_body_receives(monkeypatch):
    monkeypatch.setattr(sensitive_auth, "AUTH_JSON_READ_DEADLINE_SECONDS", 0.100)
    status, calls, cancelled = asyncio.run(asgi_request(
        [chunk(b"{", True), chunk(b" ", True), chunk(b"}")], delayed=0.040))
    assert status == 408 and calls == 3 and cancelled == [True]


@pytest.mark.parametrize("raw", [
    b'{"email":"first@example.com","email":"candidate@example.com","password":"private"}',
    b'{"email":"candidate@example.com","password":{"x":1,"x":2}}',
    b'{"email":"candidate@example.com","password":NaN}',
    b'{"email":"candidate@example.com","password":Infinity}',
    b'{"email":"candidate@example.com","password":1e9999}',
    b'{"unknown-private-key":"synthetic-private-auth-value"}',
    b'["synthetic-private-auth-value"]',
    b'{"password":"\xff"}',
    b'{"password":',
    b'[' * 1200 + b'0' + b']' * 1200,
])
def test_strict_json_rejects_duplicate_nonfinite_unknown_and_malformed_inputs(raw):
    status, calls, _ = asyncio.run(asgi_request([chunk(raw)]))
    assert status == 422 and calls == 1


def test_content_length_mismatch_is_not_parsed():
    status, calls, _ = asyncio.run(asgi_request([chunk(b"{}")], headers=[(b"content-length", b"3")]))
    assert status == 400 and calls == 1


def test_exact_body_budget_still_preserves_legitimate_login_contract():
    raw = json.dumps(VALID).encode()
    raw += b" " * (sensitive_auth.AUTH_JSON_MAX_BYTES - len(raw))
    status, calls, _ = asyncio.run(asgi_request([chunk(raw)]))
    # Valid candidate login reaches the deliberately unavailable production
    # lifecycle factory; it is not misclassified as a parsing error.
    assert status == 503 and calls == 1


def test_openapi_documents_actual_fixed_errors_only_on_sensitive_auth_routes():
    app = privacy_app()
    document = app.openapi()
    for path in ("/api/auth/register", "/api/auth/login", "/api/auth/google-login",
                 "/api/auth/candidate/v1/register", "/api/auth/candidate/v1/login",
                 "/api/auth/candidate/v1/password"):
        response = document["paths"][path]["post"]["responses"]["422"]
        schema = response["content"]["application/json"]["schema"]
        assert schema["properties"]["detail"]["const"] == sensitive_auth.AUTH_INPUT_ERROR
        assert schema["additionalProperties"] is False
    assert "413" not in document["paths"]["/api/auth/profile"]["put"]["responses"]
