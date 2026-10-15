import importlib.util
from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from limits.errors import StorageError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "rate_limiter.py"
SPEC = importlib.util.spec_from_file_location("isolated_auth_rate_limiter", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def application(path: str, handler_error: Exception | None = None):
    app = FastAPI()
    admission = type(module.limiter)(key_func=get_remote_address, storage_uri="memory://")
    app.state.limiter = admission
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    called = []

    @app.post(path)
    @admission.limit("10/minute")
    def endpoint(request: Request):
        called.append(True)
        if handler_error is not None:
            raise handler_error
        return {"ok": True}

    return TestClient(app, raise_server_exceptions=False), admission, called


ERROR_FACTORIES: list[Callable[[], Exception]] = [
    lambda: RedisConnectionError("synthetic-private-host:14843"),
    lambda: RedisTimeoutError("synthetic-private-timeout"),
    lambda: StorageError(RedisConnectionError("synthetic-private-wrapped-host")),
    lambda: StorageError(RedisTimeoutError("synthetic-private-wrapped-timeout")),
]


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/google-login"])
@pytest.mark.parametrize("error_factory", ERROR_FACTORIES)
def test_storage_failure_denies_auth_before_handler_with_retryable_public_error(
    monkeypatch, path, error_factory
):
    client, admission, called = application(path)
    storage_calls = []

    def unavailable(*_args, **_kwargs):
        storage_calls.append(True)
        raise error_factory()

    monkeypatch.setattr(admission._storage, "incr", unavailable)
    for _ in range(2):
        response = client.post(path)
        assert response.status_code == 503
        assert response.json() == {
            "detail": {
                "code": "AUTH_DEPENDENCY_UNAVAILABLE",
                "message": "Sign-in is temporarily unavailable. Please try again later.",
            }
        }
        assert response.headers["Retry-After"] == "30"
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "Set-Cookie" not in response.headers
        assert "synthetic-private" not in response.text
    assert len(storage_calls) == 2
    assert called == []
    assert admission._in_memory_fallback_enabled is False
    assert admission._fallback_limiter is None
    assert admission._storage_dead is False
    assert admission._swallow_errors is False


@pytest.mark.parametrize("path", ["/api/auth/login", "/api/auth/google-login"])
def test_healthy_admission_still_applies_actual_rate_limit(path):
    client, _admission, called = application(path)
    for _ in range(10):
        assert client.post(path).status_code == 200
    response = client.post(path)
    assert response.status_code == 429
    assert len(called) == 10
    assert response.headers.get("Retry-After") != "30"


def test_storage_failure_on_unrelated_route_is_not_reclassified(monkeypatch):
    client, admission, called = application("/api/resumes")

    def unavailable(*_args, **_kwargs):
        raise RedisConnectionError("synthetic-private-host")

    monkeypatch.setattr(admission._storage, "incr", unavailable)
    response = client.post("/api/resumes")
    assert response.status_code == 500
    assert "Retry-After" not in response.headers
    assert called == []


def test_unexpected_rate_check_bug_is_not_reclassified(monkeypatch):
    client, admission, called = application("/api/auth/login")

    def unexpected(*_args, **_kwargs):
        raise ValueError("synthetic-private-programming-error")

    monkeypatch.setattr(admission._storage, "incr", unexpected)
    response = client.post("/api/auth/login")
    assert response.status_code == 500
    assert "Retry-After" not in response.headers
    assert called == []


def test_auth_handler_redis_failure_is_not_mistaken_for_rate_admission_failure():
    client, _admission, called = application(
        "/api/auth/google-login", RedisConnectionError("synthetic-private-handler-error")
    )
    response = client.post("/api/auth/google-login")
    assert response.status_code == 500
    assert "Retry-After" not in response.headers
    assert len(called) == 1
