"""Synthetic streamed HTTP only; no DNS, sockets, SQL, credentials or mail IO."""
from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import time
from dataclasses import replace

import httpx
import pytest

from app.domains.notifications import provider


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, delay=0, failure=None):
        self.chunks, self.delay, self.failure = chunks, delay, failure
        self.closed = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.delay:
                await asyncio.sleep(self.delay)
            yield chunk
        if self.failure is not None:
            raise self.failure

    async def aclose(self):
        self.closed += 1


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*_args, **_kwargs):
        raise AssertionError("Synthetic mail provider tests cannot reach a network")
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


def attempt(**changes):
    values = dict(api_key="synthetic-private-key", sender="synthetic-sender@example.invalid",
        recipient="synthetic-recipient@example.invalid", subject="Synthetic subject", text="Example <text>\nsecond line",
        idempotency_key="fixed-original-message-key")
    return provider.ResendAttempt(**(values | changes))


def wire(monkeypatch, *, status=200, chunks=None, headers=None, failure=None, delay=0):
    stream = Stream([b'{"id":"synthetic-receipt"}'] if chunks is None else chunks, delay=delay, failure=failure)
    requests, settings = [], []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=[("Content-Type", "application/json"), *(headers or [])], stream=stream)

    def transport(**kwargs):
        settings.append(kwargs)
        return httpx.MockTransport(handler)

    monkeypatch.setattr(provider.httpx, "AsyncHTTPTransport", transport)
    return requests, settings, stream


def test_fixed_tls_transport_ignores_proxy_ca_and_keylog_environment_and_preserves_request_key(monkeypatch, tmp_path):
    keylog = tmp_path / "must-not-exist"
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.setenv(name, "http://forbidden-proxy.invalid:3128")
    monkeypatch.setenv("SSL_CERT_FILE", "/nonexistent/forbidden-ca")
    monkeypatch.setenv("SSL_CERT_DIR", "/nonexistent/forbidden-ca-dir")
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    requests, settings, stream = wire(monkeypatch)
    callback = attempt()
    result = callback()
    assert result.outcome == "sent" and stream.closed == 1 and len(requests) == 1
    request = requests[0]
    assert request.method == "POST" and str(request.url) == provider.ENDPOINT
    assert request.headers["Authorization"] == "Bearer synthetic-private-key"
    assert request.headers["Idempotency-Key"] == "fixed-original-message-key"
    assert request.headers["Accept-Encoding"] == "identity"
    assert json.loads(request.content)["html"] == "<p>Example &lt;text&gt;<br>second line</p>"
    assert settings[0]["trust_env"] is False and settings[0]["retries"] == 0 and "proxy" not in settings[0]
    tls = settings[0]["verify"]
    assert type(tls) is ssl.SSLContext and tls.verify_mode == ssl.CERT_REQUIRED and tls.check_hostname
    assert tls.keylog_filename is None and not keylog.exists()
    assert callback.last_status_code == 200 and callback.last_receipt_sha256 == result.receipt_sha256
    assert result.receipt_sha256 == hashlib.sha256(b'{"id":"synthetic-receipt"}').hexdigest()
    assert "synthetic-private-key" not in repr(callback)


@pytest.mark.parametrize("status,outcome", [(200, "sent"), (201, "sent"), (202, "sent"),
    (302, "unknown"), (307, "unknown"), (400, "permanent"), (401, "permanent"), (403, "permanent"),
    (409, "permanent"), (422, "permanent"), (408, "unknown"), (429, "known_retryable"),
    (500, "unknown"), (503, "unknown")])
def test_complete_response_classification_has_one_post_and_no_redirect_or_retry(monkeypatch, status, outcome):
    requests, _, stream = wire(monkeypatch, status=status, headers=[("Location", "https://forbidden.invalid")])
    result = attempt()()
    assert result.outcome == outcome and result.status_code == status
    assert len(requests) == 1 and stream.closed == 1


@pytest.mark.parametrize("body", [b'{"id":"synthetic-receipt","id":"other-receipt"}', b'{"id":NaN}',
    b'{"id":true}', b'{"id":"short"}', b'{"id":"synthetic-receipt","extra":1}', b'[]', b'null', b'"id"',
    b'{"id":"synthetic-receipt"', b'\xff', b''])
def test_success_requires_strict_duplicate_free_exact_id_json(monkeypatch, body):
    _, _, stream = wire(monkeypatch, chunks=[body])
    assert attempt()().outcome == "unknown" and stream.closed == 1


@pytest.mark.parametrize("status", [400, 429, 503])
def test_malformed_error_body_cannot_supply_a_definite_provider_result(monkeypatch, status):
    _, _, stream = wire(monkeypatch, status=status, chunks=[b'{"error":"truncated"'], headers=[("Retry-After", "1")])
    callback = attempt()
    assert callback().outcome == "unknown" and callback.retry_after_seconds is None and stream.closed == 1


@pytest.mark.parametrize("headers,chunks", [
    ([("Content-Encoding", "gzip")], [b'{"id":"synthetic-receipt"}']),
    ([("Content-Encoding", "br")], [b'{"id":"synthetic-receipt"}']),
    ([("Content-Type", "text/plain")], [b'{"id":"synthetic-receipt"}']),
    ([("Content-Length", "99999")], [b'{}']),
    ([("Content-Length", "3")], [b'{}']),
    ([("Content-Length", "2"), ("Content-Length", "3")], [b'{}']),
    ([], [b'x' * (provider.MAX_RESPONSE_BYTES+1)]),
])
def test_wrong_encoding_type_oversize_duplicate_or_truncated_length_is_unknown(monkeypatch, headers, chunks):
    requests, _, stream = wire(monkeypatch, headers=headers, chunks=chunks)
    assert attempt()().outcome == "unknown" and len(requests) == 1 and stream.closed == 1


def test_partial_429_body_failure_never_enables_retry_and_closes(monkeypatch):
    _, _, stream = wire(monkeypatch, status=429, chunks=[b'{"error":'], headers=[("Retry-After", "1")],
        failure=httpx.ReadError("private provider error must not escape"))
    callback = attempt()
    result = callback()
    assert result.outcome == "unknown" and callback.retry_after_seconds is None and stream.closed == 1
    with pytest.raises(provider.RetryUnavailable, match="^provider_mail_retry_after_unavailable$"):
        callback.before_retry(result)


def test_trickling_response_has_a_total_deadline_even_when_each_chunk_arrives_promptly(monkeypatch):
    monkeypatch.setattr(provider, "HTTP_DEADLINE_SECONDS", 0.03)
    requests, _, stream = wire(monkeypatch, chunks=[b' ']*100, delay=0.01)
    started = time.monotonic()
    result = attempt()()
    assert time.monotonic()-started < 0.7
    assert result.outcome == "unknown" and len(requests) == 1 and stream.closed == 1


def test_header_wait_is_inside_total_deadline_and_never_retried(monkeypatch):
    monkeypatch.setattr(provider, "HTTP_DEADLINE_SECONDS", 0.02)
    calls = []

    async def handler(request):
        calls.append(request)
        await asyncio.sleep(1)
        raise AssertionError("Total deadline must cancel header wait")

    monkeypatch.setattr(provider.httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(handler))
    assert attempt()().outcome == "unknown" and len(calls) == 1


@pytest.mark.parametrize("retry_after", [None, "", "-1", "+1", "01", "1.5", "61", "NaN"])
def test_missing_invalid_or_excessive_retry_after_stops_chain_without_guessing(monkeypatch, retry_after):
    headers = [] if retry_after is None else [("Retry-After", retry_after)]
    requests, _, _ = wire(monkeypatch, status=429, chunks=[b'{}'], headers=headers)
    callback = attempt()
    result = callback()
    assert result.outcome == "known_retryable" and callback.retry_after_seconds is None
    with pytest.raises(provider.RetryUnavailable):
        callback.before_retry(result)
    assert len(requests) == 1


@pytest.mark.parametrize("headers,expected", [([("Retry-After", "0")], 0), ([("Retry-After", "5")], 5),
    ([("Date", "Thu, 05 Nov 2026 12:00:00 GMT"), ("Retry-After", "Thu, 05 Nov 2026 12:00:05 GMT")], 5)])
def test_actual_retry_delay_is_consumed_once_and_hook_cannot_accept_a_copied_result(monkeypatch, headers, expected):
    requests, _, _ = wire(monkeypatch, status=429, chunks=[b'{}'], headers=headers)
    callback = attempt()
    result = callback()
    ticks, sleeps = [100.0], []

    def sleep(seconds):
        sleeps.append(seconds)
        ticks[0] += seconds

    monkeypatch.setattr(provider.time, "monotonic", lambda: ticks[0])
    monkeypatch.setattr(provider.time, "sleep", sleep)
    with pytest.raises(provider.RetryUnavailable):
        callback.before_retry(replace(result))
    callback.before_retry(result)
    assert sum(sleeps) == expected and callback.retry_after_seconds == expected
    with pytest.raises(provider.RetryUnavailable):
        callback.before_retry(result)
    assert len(requests) == 1


@pytest.mark.parametrize("headers", [[("Retry-After", "Thu, 05 Nov 2026 12:00:05 GMT")],
    [("Retry-After", "1"), ("Retry-After", "2")],
    [("Date", "Thu, 05 Nov 2026 12:00:00 GMT"), ("Retry-After", "Thu, 05 Nov 2026 12:02:00 GMT")]])
def test_unbounded_date_or_duplicate_retry_header_refuses_backoff(monkeypatch, headers):
    wire(monkeypatch, status=429, chunks=[b'{}'], headers=headers)
    callback = attempt()
    with pytest.raises(provider.RetryUnavailable):
        callback.before_retry(callback())


def test_sync_callback_inside_existing_loop_never_starts_a_hidden_request(monkeypatch):
    requests, _, _ = wire(monkeypatch)

    async def call():
        return attempt()()

    assert asyncio.run(call()).outcome == "unknown" and requests == []


def test_retry_hook_without_an_original_completed_attempt_refuses_every_candidate(monkeypatch):
    requests, _, _ = wire(monkeypatch)
    callback = attempt()
    for value in (None, provider.ProviderResult("known_retryable", "a"*64, 429)):
        with pytest.raises(provider.RetryUnavailable):
            callback.before_retry(value)
    assert requests == []


@pytest.mark.parametrize("changes", [{"sender": "other-sender@example.invalid"},
    {"recipient": "other-recipient@example.invalid"}, {"subject": "Other subject"},
    {"text": "Other body\n<html>"}, {"idempotency_key": "other-original-message-key"}])
def test_message_hash_changes_for_every_posted_message_or_key_drift(changes):
    assert attempt(**changes).message_sha256 != attempt().message_sha256


def test_frozen_request_bytes_match_native_message_hash_and_ignore_later_unused_body_mutation(monkeypatch):
    requests, _, _ = wire(monkeypatch)
    callback = attempt(subject="Résumé \U0001f4bc")
    original = callback.message_sha256
    callback._body = {"to": ["forbidden-drift@example.invalid"]}
    callback._idempotency_key = "unused-mutated-key"
    with pytest.raises(AttributeError):
        callback._message.body = b'{"to":["drift"]}'
    with pytest.raises(AttributeError):
        callback.message_sha256 = "a"*64
    assert callback().outcome == "sent"
    request = requests[0]
    value = json.loads(request.content)
    assert value["subject"] == "Résumé \U0001f4bc" and value["to"] == ["synthetic-recipient@example.invalid"]
    assert request.headers["Content-Type"] == "application/json"
    assert request.content == json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
    parts = (str(request.url).encode("ascii"), request.headers["Idempotency-Key"].encode("utf-8"), request.content)
    binding = b"hirewiz:resend-message:v1\0" + b"".join(len(part).to_bytes(8, "big") + part for part in parts)
    assert hashlib.sha256(binding).hexdigest() == original == callback.message_sha256


def test_message_hash_is_stable_across_reconstruction_and_api_credential_rotation(monkeypatch):
    requests, _, _ = wire(monkeypatch)
    first, second = attempt(), attempt(api_key="rotated-synthetic-private-key")
    assert first.message_sha256 == second.message_sha256
    assert first().outcome == second().outcome == "sent"
    assert requests[0].content == requests[1].content
    assert requests[0].headers["Idempotency-Key"] == requests[1].headers["Idempotency-Key"]
    assert requests[0].headers["Authorization"] != requests[1].headers["Authorization"]
