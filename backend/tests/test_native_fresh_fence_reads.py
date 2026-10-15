"""Inert stock-session GET controls; no SDK, account, socket or provider calls."""

from __future__ import annotations

import importlib
import json
import os
import ssl
import sys
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests.exceptions as errors
from google.auth.exceptions import RefreshError, TransportError
from requests.structures import CaseInsensitiveDict
from urllib3.exceptions import MaxRetryError
from urllib3.exceptions import SSLError as Urllib3SSLError

ROOT = Path(os.environ.get("HIREWIZ_FENCE_TEST_ROOT", str(Path(__file__).resolve().parents[2])))
sys.path[:0] = [str(ROOT / "infra/gcp"), str(ROOT / "backend")]
module = importlib.import_module("native_fresh_fence")
PATH = "projects/157225724590/locations/global/keys/inert-synthetic-key"


def reply(status=200, raw=b'{"native":"current"}', headers=None):
    return SimpleNamespace(status_code=status, content=raw, headers=CaseInsensitiveDict(headers or {}))


@pytest.fixture
def reader(monkeypatch):
    clock, calls, sleeps = [1.0], [], []
    outcomes = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs.copy()))
        result = outcomes.pop(0)
        if callable(result):
            result = result()
        if isinstance(result, Exception):
            raise result
        return result

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    http = SimpleNamespace(session=SimpleNamespace(request=request))
    http.request = lambda *_args, **_kwargs: pytest.fail("shared mutation transport must remain unused")
    reads = module.SDKGoogleReads(Path("/inert/public-sdk"), "inert@example.com")
    monkeypatch.setattr(reads, "_http", lambda: http)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", sleep)
    return reads, outcomes, calls, sleeps, clock, http


@pytest.mark.parametrize("first", [
    reply(429), reply(502), reply(503), reply(504),
    errors.ConnectTimeout("synthetic-private-connect"),
    errors.ReadTimeout("synthetic-private-timeout"),
    errors.Timeout("synthetic-private-timeout"),
    errors.ConnectionError("synthetic-private-connection"),
])
def test_temporary_read_then_success_uses_identical_stock_get(reader, first, capsys):
    reads, outcomes, calls, sleeps, _, http = reader
    normal_request = http.session.request
    outcomes.extend((first, reply(raw=b'{"native":"final-server-response"}')))
    assert reads.get("keys", PATH, {}) == {"native": "final-server-response"}
    assert len(calls) == 2 and sleeps == [0.1]
    for method, url, kwargs in calls:
        assert method == "GET" and url == "https://apikeys.googleapis.com/v2/" + PATH
        assert kwargs == {
            "data": None, "headers": {"Cache-Control": "no-cache"}, "stream": False,
            "hooks": {}, "timeout": kwargs["timeout"], "allow_redirects": False,
        }
    assert calls[0][2]["timeout"] == 3.0
    assert calls[1][2]["timeout"] == pytest.approx(2.9)
    assert http.session.request is normal_request
    assert capsys.readouterr() == ("", "")


def test_lookup_retry_keeps_exact_encoded_opaque_parameter(reader):
    reads, outcomes, calls, _, _, _ = reader
    outcomes.extend((reply(503), reply()))
    value = "AQ.synthetic+opaque/private?value=1&x=2"
    reads.get("keys", "keys:lookupKey", {"keyString": value})
    assert calls[0][1] == calls[1][1]
    assert calls[0][1].endswith("?keyString=AQ.synthetic%2Bopaque%2Fprivate%3Fvalue%3D1%26x%3D2")


@pytest.mark.parametrize("failure", [reply(503), errors.ReadTimeout("private")])
def test_attempt_cap_stops_without_fourth_get(reader, failure):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((failure, failure, failure, reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_unavailable$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 3 and sleeps == [0.1, 0.2] and len(outcomes) == 1


@pytest.mark.parametrize("status", [301, 302, 307, 308, 400, 401, 403, 404, 409, 500, 501])
def test_permanent_or_redirect_status_never_retries(reader, status):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(status), reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_unavailable$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("failure", [
    errors.SSLError("private-certificate"), errors.ProxyError("private-proxy"),
    errors.InvalidURL("private-url"), errors.TooManyRedirects("private-redirect"),
    errors.InvalidHeader("private-header"), errors.ChunkedEncodingError("private-body"),
    TransportError("private-auth-transport"), RefreshError("private-auth-refresh"),
    RuntimeError("private-credentials"), ValueError("private-configuration"),
    errors.ConnectionError(ssl.SSLError("private-nested-tls")),
    errors.ConnectionError(MaxRetryError(None, "private-url", reason=Urllib3SSLError("private-tls"))),
    module.release.ReleaseDenied("fence_normal_manager_configuration_refused"),
])
def test_unsafe_or_untyped_exception_never_retries_or_escapes(reader, failure, capsys):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((failure, reply()))
    with pytest.raises(module.release.ReleaseDenied) as error:
        reads.get("keys", PATH, {})
    assert "private" not in str(error.value)
    assert len(calls) == 1 and sleeps == []
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("status", [200, 503])
@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"same":1,"same":2}', b"x" * (module.MAX_BYTES + 1), "not-bytes"])
def test_invalid_or_oversized_json_never_retries(reader, status, raw):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(status, raw=raw), reply()))
    with pytest.raises(module.release.ReleaseDenied):
        reads.get("keys", PATH, {})
    assert len(calls) == 1 and sleeps == []


def test_retry_after_seconds_is_not_shortened(reader):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(429, headers={"retry-after": "1"}), reply()))
    reads.get("keys", PATH, {})
    assert len(calls) == 2 and sleeps == [1.0] and calls[1][2]["timeout"] == 2.0


def test_retry_after_http_date_is_not_shortened(reader, monkeypatch):
    reads, outcomes, calls, sleeps, _, _ = reader
    now = datetime(2026, 10, 15, 1, 2, 3, tzinfo=UTC)

    class Clock(datetime):
        @classmethod
        def now(cls, _tz):
            return now

    monkeypatch.setattr(module, "datetime", Clock)
    header = format_datetime(now + timedelta(seconds=2), usegmt=True)
    outcomes.extend((reply(503, headers={"Retry-After": header}), reply()))
    reads.get("keys", PATH, {})
    assert len(calls) == 2 and sleeps == [2.0] and calls[1][2]["timeout"] == 1.0


@pytest.mark.parametrize("header", ["3", "9999999999", "-1", "0.1", "nonsense", "", "x" * 129])
def test_retry_after_outside_budget_or_invalid_fails_without_retry(reader, header):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(503, headers={"Retry-After": header}), reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_unavailable$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("successful", [True, False])
def test_three_second_logical_budget_does_not_restart_after_slow_attempt(reader, successful):
    reads, outcomes, calls, sleeps, clock, _ = reader

    def slow():
        clock[0] += 3
        return reply() if successful else errors.ReadTimeout("private-slow")

    outcomes.extend((slow, reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_unavailable$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1 and sleeps == []


def test_no_new_request_after_retry_sleep_overshoots_deadline(reader, monkeypatch):
    reads, outcomes, calls, _, clock, _ = reader
    outcomes.extend((reply(503), reply()))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: clock.__setitem__(0, clock[0] + 3))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_unavailable$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1


def test_success_after_slow_retry_is_not_accepted_after_original_get_deadline(reader):
    reads, outcomes, calls, sleeps, clock, _ = reader

    def late_success():
        clock[0] += 2.9
        return reply()

    outcomes.extend((reply(503), late_success))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_unavailable$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 2 and sleeps == [0.1] and calls[1][2]["timeout"] == pytest.approx(2.9)


def test_configuration_refusal_has_no_physical_attempt(reader, monkeypatch):
    reads, _, calls, sleeps, _, _ = reader

    def unavailable():
        raise module.release.ReleaseDenied("fence_normal_manager_configuration_refused")

    monkeypatch.setattr(reads, "_http", unavailable)
    with pytest.raises(module.release.ReleaseDenied, match="^fence_normal_manager_configuration_refused$"):
        reads.get("keys", PATH, {})
    assert calls == sleeps == []


def test_denied_route_never_reaches_managed_transport(reader):
    reads, _, calls, sleeps, _, _ = reader
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_route_refused$"):
        reads.get("keys", PATH + ":delete", {})
    assert calls == sleeps == []


def test_final_result_is_parsed_from_server_not_transient_payload(reader):
    reads, outcomes, _, _, _, _ = reader
    outcomes.extend((reply(503, raw=json.dumps({"name": "stale"}).encode()), reply(raw=b'{}')))
    assert reads.get("keys", PATH, {}) == {}
