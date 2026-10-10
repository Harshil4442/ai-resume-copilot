from __future__ import annotations

import asyncio
import multiprocessing
import os
import subprocess
import time
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

import httpx
import pytest
import redis
from backend.app.database import Base
from backend.app.domains.common import utcnow
from backend.app.domains.employer import connectors, host_pacing, models, tasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

HOST = "api.lever.co"
SOURCE = connectors.SourceContract(
    id="source_synthetic", employer="Synthetic Employer", platform="lever",
    board_token="synthetic", region="global", allowed_hosts=("careers.example.com",),
    careers_url="https://careers.example.com/jobs",
)


def row(identity: int) -> dict:
    return {"id": str(identity), "text": "Python Engineer", "categories": {"location": "India"},
            "descriptionPlain": "Build Python services", "hostedUrl": f"https://jobs.lever.co/synthetic/{identity}"}


@pytest.fixture
def local_gate(monkeypatch):
    coordinator = host_pacing.LocalCoordinator(lease_seconds=1, request_seconds=.1,
                                               close_seconds=.05, cooldown_seconds=.04)
    monkeypatch.setattr(host_pacing, "coordinator", lambda: coordinator)
    monkeypatch.setattr(host_pacing, "REQUEST_SECONDS", .1)
    monkeypatch.setattr(host_pacing, "CLOSE_SECONDS", .05)
    return coordinator


def scan(handler, source=SOURCE, *, fetch=None):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                              headers={"Authorization": "synthetic-secret", "Cookie": "synthetic-cookie", "X-SmartToken": "synthetic-secret"},
                              cookies={"session": "synthetic"}, auth=httpx.BasicAuth("synthetic", "secret"),
                              follow_redirects=True)
    try:
        return (fetch or connectors.fetch_postings)(source, client)
    finally:
        asyncio.run(client.aclose())


def test_pages_and_transient_retries_share_host_cooldown_and_strip_credentials(local_gate):
    calls = []

    def handler(request):
        assert request.method == "GET" and request.url.host == HOST
        assert not any(name in request.headers for name in ("Authorization", "Cookie", "X-SmartToken"))
        calls.append((int(request.url.params["skip"]), time.monotonic()))
        if len(calls) == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json=[row(i) for i in range(100)] if int(request.url.params["skip"]) == 0 else [row(100)])

    assert len(scan(handler)) == 101
    assert [skip for skip, _ in calls] == [0, 0, 100]
    assert all(after[1] - before[1] >= .035 for before, after in zip(calls, calls[1:], strict=False))


def test_cooldown_survives_new_reader_and_other_tenant(local_gate):
    calls = []

    def handler(_request):
        calls.append(time.monotonic())
        return httpx.Response(200, json=[])

    scan(handler)
    other = connectors.SourceContract(**{**SOURCE.__dict__, "id": "other", "board_token": "other"})
    scan(handler, other)
    assert calls[1] - calls[0] >= .035


@pytest.mark.parametrize("status,code", [(302, "source_http_302"), (404, "posting_or_source_closed")])
def test_no_redirect_or_closed_source_retry(local_gate, status, code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(status, headers={"Location": "https://untrusted.example/"})

    with pytest.raises(connectors.ConnectorError, match=code):
        scan(handler)
    assert len(calls) == 1


def test_long_retry_after_is_not_ignored(local_gate):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "60"})

    with pytest.raises(connectors.ConnectorError, match="source_http_429"):
        scan(handler)
    assert len(calls) == 1


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_absolute_deadline_cancels_trickled_headers_or_body(local_gate, phase):
    cancelled, closed = [], []

    class Trickle(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                while True:
                    await asyncio.sleep(.01)
                    yield b" "
            finally:
                cancelled.append(True)

        async def aclose(self):
            closed.append(True)

    async def handler(_request):
        if phase == "headers":
            try:
                await asyncio.sleep(5)
            finally:
                cancelled.append(True)
        return httpx.Response(200, stream=Trickle())

    started = time.monotonic()
    with pytest.raises(connectors.ConnectorError, match="source_request_time_limit_exceeded"):
        scan(handler)
    assert .08 <= time.monotonic() - started < .5
    assert cancelled
    assert phase == "headers" or closed
    with pytest.raises(host_pacing.PacingError, match="wait_timeout"):
        local_gate.acquire(HOST, time.monotonic() + .02)
    permit = local_gate.acquire(HOST, time.monotonic() + 2)
    local_gate.verify(permit)
    local_gate.release(permit)


def test_coordination_loss_before_send_prevents_any_get(local_gate, monkeypatch):
    monkeypatch.setattr(local_gate, "verify", lambda _permit: (_ for _ in ()).throw(host_pacing.PacingError("host_pacing_ownership_lost")))
    calls = []
    with pytest.raises(connectors.ConnectorError, match="host_pacing_ownership_lost"):
        scan(lambda request: calls.append(request) or httpx.Response(200, json=[]))
    assert calls == []


def test_response_release_failure_rejects_scan(local_gate, monkeypatch):
    monkeypatch.setattr(local_gate, "release", lambda _permit: (_ for _ in ()).throw(host_pacing.PacingError("host_pacing_unavailable")))
    with pytest.raises(connectors.ConnectorError, match="host_pacing_unavailable"):
        scan(lambda _: httpx.Response(200, json=[row(1)]))


@pytest.mark.parametrize("failure", ["timeout", "io_error"])
def test_ambiguous_stream_close_does_not_release_permit(local_gate, monkeypatch, failure):
    releases = []
    monkeypatch.setattr(local_gate, "release", lambda permit: releases.append(permit))

    class BrokenClose(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"[]"

        async def aclose(self):
            if failure == "timeout":
                await asyncio.sleep(5)
            else:
                raise httpx.ReadError("synthetic close failure")

    monkeypatch.setattr(connectors, "LEVER_READ_ATTEMPTS", 1)
    with pytest.raises(connectors.ConnectorError, match="source_request_time_limit_exceeded|source_unavailable"):
        scan(lambda _: httpx.Response(200, stream=BrokenClose()))
    assert releases == []


def test_transport_failure_before_response_retains_lease_without_retry(local_gate, monkeypatch):
    calls, releases = [], []
    monkeypatch.setattr(local_gate, "release", lambda permit: releases.append(permit))

    async def handler(request):
        calls.append(request)
        raise httpx.ReadError("synthetic missing response")

    with pytest.raises(connectors.ConnectorError, match="source_unavailable"):
        scan(handler)
    assert len(calls) == 1 and releases == []


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_real_loopback_http_socket_is_cancelled_at_total_deadline(local_gate, phase):
    async def scenario():
        disconnected = asyncio.Event()

        async def serve(reader, writer):
            try:
                request = await reader.readuntil(b"\r\n\r\n")
                assert b"Authorization:" not in request and b"Cookie:" not in request
                if phase == "body":
                    writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n")
                while True:
                    # Every read stays below HTTPX's inactivity timeout; only a
                    # total deadline can stop this never-completing response.
                    writer.write(b"1\r\n \r\n" if phase == "body" else b"H")
                    await writer.drain()
                    await asyncio.sleep(.01)
            except (ConnectionError, asyncio.IncompleteReadError):
                disconnected.set()
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except ConnectionError:
                    pass

        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]

        class LoopbackOnly(httpx.AsyncBaseTransport):
            def __init__(self):
                self.transport = httpx.AsyncHTTPTransport()

            async def handle_async_request(self, request):
                assert request.url.host == HOST
                local = httpx.Request(request.method, f"http://127.0.0.1:{port}/synthetic",
                                      headers=request.headers, extensions=request.extensions)
                return await self.transport.handle_async_request(local)

            async def aclose(self):
                await self.transport.aclose()

        try:
            async with httpx.AsyncClient(transport=LoopbackOnly()) as client:
                reader = connectors._LeverRead(client, local_gate)
                started = time.monotonic()
                with pytest.raises(connectors.ConnectorError, match="source_request_time_limit_exceeded"):
                    await reader.get("https://api.lever.co/v0/postings/synthetic", {})
                assert .08 <= time.monotonic() - started < .5
                await asyncio.wait_for(disconnected.wait(), timeout=1)
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("environment", ["production", "staging", "", "unexpected"])
def test_production_never_falls_back_to_local(monkeypatch, environment):
    monkeypatch.setenv("APP_ENV", environment)
    monkeypatch.setenv("EMPLOYER_HOST_PACING_MODE", "local")
    with pytest.raises(host_pacing.PacingError, match="configuration_invalid"):
        host_pacing.coordinator()


def test_missing_redis_is_retryable_and_never_dispatches(monkeypatch):
    monkeypatch.delenv("EMPLOYER_HOST_PACING_MODE", raising=False)
    monkeypatch.delenv("EMPLOYER_HOST_PACING_REDIS_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("RATE_LIMIT_STORAGE_URL", raising=False)
    calls = []
    with pytest.raises(connectors.ConnectorError, match="host_pacing_unavailable") as failure:
        scan(lambda request: calls.append(request) or httpx.Response(200, json=[]))
    assert failure.value.safe_to_retry and calls == []


def test_dedicated_authority_does_not_change_cache_or_limiter(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("EMPLOYER_HOST_PACING_MODE", "redis")
    monkeypatch.setenv("EMPLOYER_HOST_PACING_REDIS_URL", "redis://127.0.0.1:56379/0")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:56380/1")
    monkeypatch.setenv("RATE_LIMIT_STORAGE_URL", "redis://127.0.0.1:56381/2")
    selected = []
    synthetic_client = object()
    monkeypatch.setattr(host_pacing.redis.Redis, "from_url",
                        lambda url, **kwargs: selected.append(url) or synthetic_client)
    gate = host_pacing.coordinator()
    assert gate.client is synthetic_client
    assert selected == ["redis://127.0.0.1:56379/0"]


def test_invalid_dedicated_authority_never_falls_back(monkeypatch):
    monkeypatch.setenv("EMPLOYER_HOST_PACING_MODE", "redis")
    monkeypatch.setenv("EMPLOYER_HOST_PACING_REDIS_URL", "https://127.0.0.1:56379")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:56380/1")
    selected = []
    monkeypatch.setattr(host_pacing.redis.Redis, "from_url",
                        lambda url, **kwargs: selected.append(url))
    with pytest.raises(host_pacing.PacingError, match="host_pacing_unavailable"):
        host_pacing.coordinator()
    assert selected == []


def test_failed_partial_scan_preserves_jobs_retries_soon_and_has_no_sql_lock(monkeypatch, local_gate):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    sessions = []

    def open_session():
        session = factory()
        sessions.append(session)
        return session

    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setattr(tasks, "SessionLocal", open_session)
    with factory() as db:
        db.add(models.EmployerSource(**SOURCE.__dict__, enabled=True,
            verification_url=SOURCE.careers_url, verification_note="Synthetic tenant evidence"))
        db.commit()
        db.add(models.EmployerPosting(id="job_preserve", source_id=SOURCE.id, external_id="old",
            title="Existing", employer=SOURCE.employer, location="India", description="Existing job",
            canonical_url="https://jobs.lever.co/synthetic/old", apply_url="https://jobs.lever.co/synthetic/old",
            content_sha256="a" * 64, last_checked_at=utcnow(), skills=[]))
        db.commit()

    def handler(request):
        assert all(not session.in_transaction() for session in sessions)
        if request.url.params["skip"] == "0":
            return httpx.Response(200, json=[row(i) for i in range(100)])
        raise host_pacing.PacingError("host_pacing_unavailable")

    original = connectors.fetch_postings
    monkeypatch.setattr(connectors, "fetch_postings", lambda source: scan(handler, source, fetch=original))
    before = utcnow()
    with pytest.raises(connectors.ConnectorError, match="host_pacing_unavailable"):
        tasks.refresh_source(SOURCE.id)
    with factory() as db:
        source = db.get(models.EmployerSource, SOURCE.id)
        assert source.status == "unavailable" and source.last_error_code == "host_pacing_unavailable"
        assert source.scan_token is None
        assert source.next_refresh_at.replace(tzinfo=before.tzinfo) <= before + timedelta(minutes=16)
        existing = db.get(models.EmployerPosting, "job_preserve")
        assert existing.is_open and existing.closed_at is None and db.query(models.EmployerPosting).count() == 1
    monkeypatch.setattr(connectors, "fetch_postings", original)
    engine.dispose()


def _redis_worker(url, prefix, output):
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=.5)
    gate = host_pacing.RedisCoordinator(client, prefix=prefix)
    permit = gate.acquire(HOST, time.monotonic() + 10)
    gate.verify(permit)
    started = time.monotonic()
    time.sleep(.05)
    finished = time.monotonic()
    gate.release(permit)
    output.put((started, finished))
    gate.close()


@pytest.fixture
def redis_gate():
    url = os.getenv("EMPLOYER_PACING_TEST_REDIS_URL", "")
    if not url:
        pytest.skip("requires explicitly disposable loopback Redis")
    parsed = urlsplit(url)
    assert parsed.hostname == "127.0.0.1" and parsed.port == 56379 and parsed.path == "/0"
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=.5,
                                  retry=host_pacing.Retry(host_pacing.NoBackoff(), 0))
    assert client.ping()
    prefix = "hirewiz-synthetic-test:" + uuid.uuid4().hex + ":"
    gate = host_pacing.RedisCoordinator(client, prefix=prefix, lease_seconds=.3,
        request_seconds=.03, close_seconds=.01, cooldown_seconds=.03)
    yield url, gate
    keys = list(client.scan_iter(match=prefix + "*"))
    if keys:
        client.delete(*keys)
    gate.close()


def test_actual_redis_cross_process_global_host_serialization(redis_gate):
    url, gate = redis_gate
    # Prime only the synthetic namespace, keeping production 1-second cooldown.
    permit = gate.acquire(HOST, time.monotonic() + 2)
    gate.release(permit)
    context = multiprocessing.get_context("spawn")
    output = context.Queue()
    workers = [context.Process(target=_redis_worker, args=(url, gate.prefix, output)) for _ in range(3)]
    for worker in workers:
        worker.start()
    times = sorted(output.get(timeout=15) for _ in workers)
    for worker in workers:
        worker.join(timeout=5)
        assert worker.exitcode == 0
    assert all(after[0] - before[1] >= .98 for before, after in zip(times, times[1:], strict=False))


def test_actual_redis_expired_or_stolen_permit_fails_closed(redis_gate):
    _, gate = redis_gate
    permit = gate.acquire(HOST, time.monotonic() + 2)
    time.sleep(.31)
    with pytest.raises(host_pacing.PacingError, match="ownership_lost"):
        gate.verify(permit)
    with pytest.raises(host_pacing.PacingError, match="ownership_lost"):
        gate.release(permit)
    next_permit = gate.acquire(HOST, time.monotonic() + 2)
    assert next_permit.token != permit.token
    gate.release(next_permit)


def test_actual_redis_bounded_wait_and_server_cooldown(redis_gate):
    _, gate = redis_gate
    permit = gate.acquire(HOST, time.monotonic() + 2)
    started = time.monotonic()
    with pytest.raises(host_pacing.PacingError, match="wait_timeout"):
        gate.acquire(HOST, started + .04)
    assert time.monotonic() - started < .2
    gate.release(permit)
    before = gate.client.time()
    next_at = int(gate.client.hget(gate.prefix + HOST, "next"))
    now_ms = before[0] * 1000 + before[1] // 1000
    assert 0 < next_at - now_ms <= 30


def test_actual_redis_restart_and_deleted_state_invalidate_owner(redis_gate):
    _, gate = redis_gate
    permit = gate.acquire(HOST, time.monotonic() + 2)
    old_run = gate.client.info("server")["run_id"]
    # Only the disposable container created for this task is ever restarted.
    subprocess.run(["docker", "restart", "hirewiz-lever-pacing-20261008"], check=True, capture_output=True, timeout=10)
    for _ in range(30):
        try:
            if gate.client.ping():
                break
        except redis.RedisError:
            time.sleep(.1)
    assert gate.client.info("server")["run_id"] != old_run
    with pytest.raises(host_pacing.PacingError, match="ownership_lost"):
        gate.verify(permit)
    # No request may use the old grant; a new grant waits the derived quarantine.
    started = time.monotonic()
    new = gate.acquire(HOST, started + 2)
    assert time.monotonic() - started >= .06
    gate.client.delete(gate.prefix + HOST)
    with pytest.raises(host_pacing.PacingError, match="ownership_lost"):
        gate.verify(new)


def test_actual_redis_connection_failure_has_no_local_fallback(redis_gate):
    _, gate = redis_gate
    failed = host_pacing.RedisCoordinator(redis.Redis(host="127.0.0.1", port=56378,
        socket_connect_timeout=.05, socket_timeout=.05, retry=host_pacing.Retry(host_pacing.NoBackoff(), 0)))
    started = time.monotonic()
    with pytest.raises(host_pacing.PacingError, match="unavailable"):
        failed.acquire(HOST, started + 1)
    assert time.monotonic() - started < .5
    failed.close()


def test_actual_redis_coordination_timeout_rejects_ambiguous_acquisition(redis_gate):
    url, gate = redis_gate
    permit = gate.acquire(HOST, time.monotonic() + 2)
    gate.release(permit)
    time.sleep(.04)
    client = redis.Redis.from_url(url, decode_responses=True, socket_timeout=.05,
                                  retry=host_pacing.Retry(host_pacing.NoBackoff(), 0))
    short = host_pacing.RedisCoordinator(client, prefix=gate.prefix, lease_seconds=.3,
        request_seconds=.03, close_seconds=.01, cooldown_seconds=.03)
    gate.client.execute_command("CLIENT", "PAUSE", 200, "ALL")
    started = time.monotonic()
    with pytest.raises(host_pacing.PacingError, match="unavailable"):
        short.acquire(HOST, started + 1)
    assert time.monotonic() - started < .3
    time.sleep(.25)
    short.close()


def test_actual_redis_evicting_configuration_fails_closed(redis_gate):
    _, gate = redis_gate
    try:
        gate.client.config_set("maxmemory-policy", "allkeys-lru")
        with pytest.raises(host_pacing.PacingError, match="configuration_invalid"):
            gate.acquire(HOST, time.monotonic() + 1)
    finally:
        gate.client.config_set("maxmemory-policy", "noeviction")


def test_actual_redis_expired_grant_immediately_before_dispatch_makes_no_get(redis_gate, monkeypatch):
    _, gate = redis_gate
    verify = gate.verify

    def delayed_verify(permit):
        time.sleep(.31)
        verify(permit)

    monkeypatch.setattr(gate, "verify", delayed_verify)
    monkeypatch.setattr(host_pacing, "coordinator", lambda: gate)
    calls = []
    with pytest.raises(connectors.ConnectorError, match="ownership_lost"):
        scan(lambda request: calls.append(request) or httpx.Response(200, json=[]))
    assert calls == []
