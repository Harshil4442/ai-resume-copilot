from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
import pytest
from backend.app import catalog_timing as timing
from backend.app import main
from backend.app.database import Base, get_db
from backend.app.domains.employer import models
from backend.app.models import User
from backend.app.observability import JsonFormatter, correlation_id_var
from backend.app.security import create_access_token, get_current_user
from fastapi import HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session


def timing_events(caplog):
    return [record.catalog_timing.payload() for record in caplog.records
            if record.name == "catalog_latency"]


@pytest.fixture
def application(tmp_path, monkeypatch):
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add_all([User(id=i, email=f"synthetic-{i}@example.invalid", job_service_credits=i * 11)
                    for i in (1, 2)])
        db.add(models.EmployerSource(
            id="synthetic-source", employer="Synthetic", platform="greenhouse",
            board_token="synthetic", enabled=True, careers_url="https://example.invalid/fixture",
            verification_url="https://example.invalid/fixture", verification_note="Synthetic",
        ))
        db.commit()

    def database():
        with Session(engine, autoflush=False) as db:
            yield db

    previous = dict(main.app.dependency_overrides)
    main.app.dependency_overrides[get_db] = database
    try:
        yield main.app, engine
    finally:
        main.app.dependency_overrides = previous
        engine.dispose()


def test_actual_catalog_concurrency_ownership_query_count_and_thread_context(application, caplog):
    app, engine = application
    caplog.set_level(logging.INFO, logger="catalog_latency")
    count = 0
    lock = threading.Lock()

    def record(_conn, _cursor, _statement, _parameters, _context, _many):
        # Count all existing executes without inspecting or storing SQL/parameters.
        nonlocal count
        with lock:
            count += 1

    event.listen(engine, "before_cursor_execute", record)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
            owners = [1, 2] * 4
            responses = await asyncio.gather(*[
                client.get(timing.CATALOG_PATH + "?sql=PRIVATE_SQL", headers={
                    "Authorization": f"Bearer {create_access_token(subject=str(owner))}",
                    "X-Correlation-ID": "PRIVATE_USER_TOKEN_URL_COOKIE",
                    "Cookie": "private=PRIVATE_COOKIE",
                }) for owner in owners
            ])
            return owners, responses

    try:
        owners, responses = asyncio.run(run())
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert count == len(responses) * 2  # Existing auth SELECT + existing source SELECT.
    events = timing_events(caplog)
    assert len(events) == len(responses)
    assert len({item["request_id"] for item in events}) == len(events)
    by_id = {item["request_id"]: item for item in events}
    for owner, response in zip(owners, responses, strict=True):
        assert response.status_code == 200
        assert response.json()["balance"] == owner * 11
        assert response.headers["cache-control"] == "no-store, private"
        assert response.headers["pragma"] == "no-cache"
        identity = response.headers["x-correlation-id"]
        assert re.fullmatch(r"[0-9a-f]{32}", identity)
        item = by_id[identity]
        assert item["outcome"] == "complete"
        assert item["db_connect_ms"] is None and item["db_ping_ms"] is None
        assert item["acquisition_measurement"] == "composite"
        assert all(item[f"{phase}_ms"] is not None for phase in timing.PHASES)
        assert "backend_body_send" not in response.headers["server-timing"]
        assert "backend_cleanup" not in response.headers["server-timing"]
        assert "app;dur" not in response.headers["server-timing"]
    assert "PRIVATE" not in json.dumps(events)
    assert timing.current_catalog_timing() is None


@pytest.mark.parametrize("token", [None, "PRIVATE_INVALID_TOKEN", create_access_token(subject="not-an-integer")])
def test_middleware_rejection_does_not_acquire_or_select(application, caplog, token):
    app, engine = application
    caplog.set_level(logging.INFO, logger="catalog_latency")
    executed = Mock()
    event.listen(engine, "before_cursor_execute", executed)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
            return await client.get(timing.CATALOG_PATH, headers={} if token is None else {"Authorization": f"Bearer {token}"})

    try:
        response = asyncio.run(run())
    finally:
        event.remove(engine, "before_cursor_execute", executed)
    assert response.status_code == 401
    executed.assert_not_called()
    item, = timing_events(caplog)
    assert item["middleware_jwt_ms"] is not None
    assert item["db_acquire_ms"] is None
    assert item["auth_lookup_ms"] is None


def test_dependency_rejection_and_acquisition_failure_do_not_query(caplog):
    caplog.set_level(logging.INFO, logger="catalog_latency")
    for valid in (False, True):
        db = Mock()
        db.connection.side_effect = RuntimeError("PRIVATE_DB_URL_SQL_PARAMS")

        async def inner(_scope, _receive, _send, valid=valid, db=db):
            get_current_user(token=create_access_token(subject="1") if valid else "PRIVATE_TOKEN", db=db)

        with pytest.raises(RuntimeError if valid else HTTPException):
            asyncio.run(invoke(inner))
        db.query.assert_not_called()
        assert db.connection.call_count == int(valid)
    assert "PRIVATE" not in json.dumps(timing_events(caplog))


def test_missing_authenticated_owner_preserves_401_with_no_source_query(application, caplog):
    app, engine = application
    caplog.set_level(logging.INFO, logger="catalog_latency")
    executed = Mock()
    event.listen(engine, "before_cursor_execute", executed)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.invalid") as client:
            return await client.get(timing.CATALOG_PATH, headers={"Authorization": f"Bearer {create_access_token(subject='999')}"})

    try:
        response = asyncio.run(run())
    finally:
        event.remove(engine, "before_cursor_execute", executed)
    assert response.status_code == 401
    assert response.json() == {"detail": "Not authenticated"}
    assert executed.call_count == 1
    item, = timing_events(caplog)
    assert item["auth_lookup_ms"] is not None and item["catalog_sources_ms"] is None


def test_framework_500_body_is_observed_without_exception_text(application, caplog):
    app, _engine = application
    caplog.set_level(logging.INFO, logger="catalog_latency")
    db = Mock()
    db.query.return_value.filter.return_value.first.side_effect = RuntimeError("PRIVATE_SQL_PARAMS_TOKEN_URL")
    app.dependency_overrides[get_db] = lambda: db

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://synthetic.invalid") as client:
            return await client.get(timing.CATALOG_PATH, headers={"Authorization": f"Bearer {create_access_token(subject='1')}"})

    response = asyncio.run(run())
    assert response.status_code == 500
    assert response.json()["detail"] == "Internal server error"
    item, = timing_events(caplog)
    assert item["status_class"] == "5xx" and item["outcome"] == "error"
    assert item["error_phase"] == "auth_lookup"
    assert item["backend_body_send_ms"] is not None and item["backend_cleanup_ms"] is not None
    assert response.headers["x-correlation-id"] == item["request_id"]
    assert "PRIVATE" not in json.dumps(item)
    db.connection.assert_called_once()


async def invoke(inner, send=None, path=timing.CATALOG_PATH):
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages = []

    async def capture(message):
        messages.append(message)
        if send is not None:
            await send(message)

    await timing.CatalogTimingMiddleware(inner)({"type": "http", "method": "GET", "path": path}, receive, capture)
    return messages


def test_streaming_and_cleanup_have_distinct_boundaries(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="catalog_latency")
    clock = SimpleNamespace(now=0)
    monkeypatch.setattr(timing.time, "perf_counter_ns", lambda: clock.now * 1_000_000)
    observed = []

    async def send(message):
        observed.append((message["type"], clock.now))
        if message["type"] == "http.response.body":
            clock.now += 2

    async def inner(_scope, _receive, send):
        with timing.catalog_span("auth_lookup"):
            clock.now += 3
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"first", "more_body": True})
        clock.now += 5
        await send({"type": "http.response.body", "body": b"last", "more_body": False})
        clock.now += 7

    messages = asyncio.run(invoke(inner, send))
    assert [message.get("body") for message in messages[1:]] == [b"first", b"last"]
    assert observed == [("http.response.start", 3), ("http.response.body", 3), ("http.response.body", 10)]
    item, = timing_events(caplog)
    assert item["backend_headers_ms"] == 3
    assert item["backend_body_send_ms"] == 9
    assert item["backend_cleanup_ms"] == 7
    assert item["backend_total_ms"] == 19


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE_EXCEPTION"), asyncio.CancelledError("PRIVATE_EXCEPTION")])
def test_send_failure_or_cancellation_has_no_false_completed_body(failure, caplog):
    caplog.set_level(logging.INFO, logger="catalog_latency")

    async def inner(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"fixture", "more_body": False})

    async def send(message):
        if message["type"] == "http.response.body":
            raise failure

    with pytest.raises(type(failure)):
        asyncio.run(invoke(inner, send))
    item, = timing_events(caplog)
    assert item["outcome"] == ("cancelled" if isinstance(failure, asyncio.CancelledError) else "error")
    assert item["backend_body_send_ms"] is None and item["backend_cleanup_ms"] is None
    assert "PRIVATE" not in json.dumps(item)
    assert timing.current_catalog_timing() is None


def test_cancelled_worker_cannot_change_frozen_event(caplog):
    caplog.set_level(logging.INFO, logger="catalog_latency")
    entered, release = threading.Event(), threading.Event()

    def worker():
        with timing.catalog_span("auth_lookup"):
            entered.set()
            assert release.wait(5)

    async def run():
        task = None
        future = None

        async def inner(_scope, _receive, _send):
            nonlocal future
            context = contextvars.copy_context()
            future = asyncio.get_running_loop().run_in_executor(None, context.run, worker)
            await asyncio.shield(future)

        task = asyncio.create_task(invoke(inner))
        try:
            await asyncio.to_thread(entered.wait, 5)
            assert entered.is_set()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            before = timing_events(caplog)
        finally:
            release.set()
            if future is not None:
                await future
        assert timing_events(caplog) == before

    asyncio.run(run())
    assert timing_events(caplog)[0]["auth_lookup_ms"] is None


def test_formatter_rebuilds_only_fixed_schema_and_ignores_private_base_fields():
    private = "PRIVATE_SQL_PARAMS_USER_TOKEN_URL_COOKIE_EXCEPTION"
    record = logging.LogRecord(private, logging.ERROR, private, 1, private, (), (ValueError, ValueError(private), None))
    record.arbitrary_extra = private
    record.catalog_timing = timing.CatalogTimingEvent("a" * 32, private, private, private, {
        "auth_lookup_ms": 3.5, "db_acquire_ms": float("nan"), "backend_headers_ms": float("inf"),
        "catalog_sources_ms": -1, "db_connect_ms": 50, private: private,
    })
    token = correlation_id_var.set(private)
    try:
        output = JsonFormatter().format(record)
    finally:
        correlation_id_var.reset(token)
    assert private not in output
    item = json.loads(output)
    assert item["auth_lookup_ms"] == 3.5
    assert item["db_acquire_ms"] is None and item["db_connect_ms"] is None
    assert item["status_class"] == "not_started" and item["error_phase"] is None
    assert set(item) == timing.DURATION_FIELDS | {
        "event", "request_id", "outcome", "status_class", "error_phase", "acquisition_measurement",
        "connect_measurement", "ping_measurement", "db_connect_ms", "db_ping_ms",
    }
    assert timing.finite_duration(10 ** 400) is None
    assert timing.finite_duration(True) is None


def test_logging_failure_and_non_catalog_requests_preserve_response(monkeypatch):
    log = Mock(side_effect=RuntimeError("PRIVATE_LOGGING_EXCEPTION"))
    monkeypatch.setattr(timing._log, "info", log)

    async def inner(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"same-body", "more_body": False})

    assert asyncio.run(invoke(inner))[-1]["body"] == b"same-body"
    log.reset_mock()
    messages = asyncio.run(invoke(inner, path="/api/health"))
    assert messages[0]["headers"] == []
    log.assert_not_called()
