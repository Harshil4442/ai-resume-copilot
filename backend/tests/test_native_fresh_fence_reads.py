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
from types import ModuleType, SimpleNamespace

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


@pytest.fixture
def normal_manager(monkeypatch):
    """Exercise the manager's configuration boundary without loading credentials."""
    def prop(value):
        return SimpleNamespace(Get=lambda: value, GetBool=lambda: value)

    values = SimpleNamespace(
        core=SimpleNamespace(account=prop("inert@example.com"), project=prop(module.release.PROJECT),
            universe_domain=prop("googleapis.com"), disable_file_logging=prop(True),
            log_http=prop(False), custom_ca_certs_file=prop(None)),
        billing=SimpleNamespace(quota_project=prop(module.release.PROJECT)),
        auth=SimpleNamespace(disable_ssl_validation=prop(False), disable_credentials=prop(False),
            credential_file_override=prop(None), access_token_file=prop(None),
            token_host=prop("https://oauth2.googleapis.com/token"), impersonate_service_account=prop(None)),
        proxy=SimpleNamespace(address=prop(None), port=prop(None), proxy_type=prop(None)),
    )
    calls = []
    http = SimpleNamespace(session=SimpleNamespace(max_redirects=5, trust_env=True))

    def factory(**kwargs):
        calls.append(kwargs)
        return http

    packages = {name: ModuleType(name) for name in (
        "googlecloudsdk", "googlecloudsdk.core", "googlecloudsdk.core.credentials",
        "googlecloudsdk.core.properties", "googlecloudsdk.core.credentials.transports",
    )}
    for package in packages.values():
        package.__path__ = []
    packages["googlecloudsdk.core.properties"].VALUES = values
    packages["googlecloudsdk.core.credentials.transports"].GetApitoolsTransport = factory
    for name, package in packages.items():
        monkeypatch.setitem(sys.modules, name, package)
    monkeypatch.setattr(sys, "path", sys.path.copy())
    return values, prop, calls, http


def test_normal_manager_uses_only_exact_hirewiz_quota_with_original_transport_controls(normal_manager):
    _, _, calls, http = normal_manager
    reads = module.SDKGoogleReads(Path("/inert/public-sdk"), "inert@example.com")
    assert reads._http() is http
    assert reads._http() is http
    assert calls == [{"timeout": 3, "enable_resource_quota": True,
        "allow_account_impersonation": False,
        "redact_request_body_reason": "native HireWiz retirement metadata"}]
    assert http.session.max_redirects == 0 and http.session.trust_env is False


@pytest.mark.parametrize("quota", [None, "", "CURRENT_PROJECT", "CURRENT_PROJECT_WITH_FALLBACK", "LEGACY", "foreign-project"])
def test_nonexact_quota_refuses_before_loading_any_managed_transport(normal_manager, quota):
    values, prop, calls, _ = normal_manager
    values.billing.quota_project = prop(quota)
    reads = module.SDKGoogleReads(Path("/inert/public-sdk"), "inert@example.com")
    with pytest.raises(module.release.ReleaseDenied, match="^fence_normal_manager_configuration_refused$"):
        reads._http()
    assert calls == []


@pytest.mark.parametrize(("section", "name", "value"), [
    ("core", "account", "foreign@example.com"), ("core", "project", "foreign-project"),
    ("auth", "disable_ssl_validation", True), ("auth", "disable_credentials", True),
    ("auth", "credential_file_override", "/inert/foreign-credentials"),
    ("auth", "impersonate_service_account", "foreign@project.iam.gserviceaccount.com"),
    ("proxy", "address", "inert-proxy"),
])
def test_exact_quota_does_not_relax_existing_normal_manager_authority(normal_manager, section, name, value):
    values, prop, calls, _ = normal_manager
    setattr(getattr(values, section), name, prop(value))
    reads = module.SDKGoogleReads(Path("/inert/public-sdk"), "inert@example.com")
    with pytest.raises(module.release.ReleaseDenied, match="^fence_normal_manager_configuration_refused$"):
        reads._http()
    assert calls == []


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
    assert calls[1][2]["timeout"] == 3.0
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
    reason = "fence_native_http_retry_exhausted" if not isinstance(failure, Exception) else "fence_native_read_timeout"
    with pytest.raises(module.release.ReleaseDenied, match="^" + reason + "$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 3 and sleeps == [0.1, 0.2] and len(outcomes) == 1


@pytest.mark.parametrize(("status", "reason"), [
    *((status, "fence_native_http_refused") for status in (301, 302, 307, 308, 400, 409, 500, 501)),
    (401, "fence_native_http_unauthorized"),
    (403, "fence_native_http_permission_denied"),
    (404, "fence_native_http_not_found"),
])
def test_permanent_or_redirect_status_never_retries(reader, status, reason):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(status), reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^" + reason + "$"):
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
    assert len(calls) == 2 and sleeps == [1.0] and calls[1][2]["timeout"] == 3.0


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
    assert len(calls) == 2 and sleeps == [2.0] and calls[1][2]["timeout"] == 3.0


@pytest.mark.parametrize("header", ["10", "9999999999", "-1", "0.1", "nonsense", "", "x" * 129])
def test_retry_after_outside_budget_or_invalid_fails_without_retry(reader, header):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(503, headers={"Retry-After": header}), reply()))
    reason = "fence_native_read_budget_expired" if header.isdigit() else "fence_native_retry_after_invalid"
    with pytest.raises(module.release.ReleaseDenied, match="^" + reason + "$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1 and sleeps == []


@pytest.mark.parametrize("successful", [True, False])
def test_ten_second_logical_budget_does_not_restart_after_slow_attempt(reader, successful):
    reads, outcomes, calls, sleeps, clock, _ = reader

    def slow():
        clock[0] += 10
        return reply() if successful else errors.ReadTimeout("private-slow")

    outcomes.extend((slow, reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_budget_expired$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1 and sleeps == []


def test_no_new_request_after_retry_sleep_overshoots_deadline(reader, monkeypatch):
    reads, outcomes, calls, _, clock, _ = reader
    outcomes.extend((reply(503), reply()))
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: clock.__setitem__(0, clock[0] + 10))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_budget_expired$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 1


def test_success_after_slow_retry_is_not_accepted_after_original_get_deadline(reader):
    reads, outcomes, calls, sleeps, clock, _ = reader

    def late_success():
        clock[0] += 9.9
        return reply()

    outcomes.extend((reply(503), late_success))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_budget_expired$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 2 and sleeps == [0.1] and calls[1][2]["timeout"] == 3.0


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


def test_actual_three_second_timeout_can_recover_without_resetting_budget(reader):
    reads, outcomes, calls, sleeps, clock, _ = reader

    def timed_out():
        clock[0] += 3.045  # Actual native diagnostic: one full socket timeout.
        return errors.ReadTimeout("https://private-url/?keyString=private-key")

    outcomes.extend((timed_out, reply(raw=b'{"native":"fresh-retry"}')))
    assert reads.get("keys", PATH, {}) == {"native": "fresh-retry"}
    assert len(calls) == 2 and sleeps == [0.1]
    assert clock[0] == pytest.approx(4.145)
    assert all(call[2]["timeout"] == 3.0 for call in calls)


def test_three_full_socket_timeouts_exhaust_attempts_without_fourth_request(reader):
    reads, outcomes, calls, sleeps, clock, _ = reader

    def timed_out():
        clock[0] += 3
        return errors.ReadTimeout("private-key")

    outcomes.extend((timed_out, timed_out, timed_out, reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_timeout$"):
        reads.get("keys", PATH, {})
    assert len(calls) == 3 and sleeps == [0.1, 0.2]
    assert clock[0] == pytest.approx(10.3)


def test_retry_after_three_seconds_now_fits_logical_budget(reader):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(429, headers={"Retry-After": "3"}), reply()))
    assert reads.get("keys", PATH, {}) == {"native": "current"}
    assert len(calls) == 2 and sleeps == [3.0]
    assert calls[1][2]["timeout"] == 3.0


def test_caller_deadline_caps_socket_and_success_cannot_outlive_it(reader):
    reads, outcomes, calls, _, clock, _ = reader

    def late():
        clock[0] += 0.5
        return reply()

    outcomes.append(late)
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_budget_expired$"):
        reads.get("keys", PATH, {}, deadline=1.5)
    assert len(calls) == 1 and calls[0][2]["timeout"] == 0.5


def test_expired_caller_deadline_never_initializes_http(reader, monkeypatch):
    reads, outcomes, calls, sleeps, clock, _ = reader
    monkeypatch.setattr(reads, "_http", lambda: pytest.fail("expired read initialized transport"))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_budget_expired$"):
        reads.get("keys", PATH, {}, deadline=clock[0])
    assert outcomes == calls == sleeps == []


@pytest.mark.parametrize("deadline", [True, "private-key", float("nan"), float("inf"), 10 ** 1000])
def test_invalid_caller_deadline_refuses_without_transport(reader, deadline):
    reads, _, calls, sleeps, _, _ = reader
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_deadline_invalid$"):
        reads.get("keys", PATH, {}, deadline=deadline)
    assert calls == sleeps == []


def test_retry_after_cannot_shorten_or_reset_caller_deadline(reader):
    reads, outcomes, calls, sleeps, _, _ = reader
    outcomes.extend((reply(429, headers={"Retry-After": "3"}), reply()))
    with pytest.raises(module.release.ReleaseDenied, match="^fence_native_read_budget_expired$"):
        reads.get("keys", PATH, {}, deadline=3.0)
    assert len(calls) == 1 and calls[0][2]["timeout"] == 2.0 and sleeps == []


@pytest.mark.parametrize(("failure", "reason"), [
    (errors.ReadTimeout("https://private-url/?keyString=private-key"), "fence_native_read_timeout"),
    (RefreshError("private-refresh-token"), "fence_native_authentication_unavailable"),
    (TransportError("private-auth-body"), "fence_native_authentication_unavailable"),
    (errors.SSLError("private-tls"), "fence_native_transport_unavailable"),
    (reply(403, b'{"error":{"message":"private-key"}}'), "fence_native_http_permission_denied"),
    (reply(503), "fence_native_http_retry_exhausted"),
    (reply(200, b'private-key'), "fence_native_response_invalid"),
])
def test_fixed_failure_categories_pass_existing_stopped_projection_without_reflection(reader, failure, reason, capsys):
    reads, outcomes, _, _, _, _ = reader
    outcomes.extend((failure, failure, failure))
    with pytest.raises(module.release.ReleaseDenied) as error:
        reads.get("keys", PATH, {})
    assert error.value.args == (reason,)
    execute = importlib.import_module("fresh_cutover_execute")
    codes = execute.literal_codes(ROOT)
    stopped = execute.stopped_event(error.value, "source_quiescence", codes, (module.release.ReleaseDenied,))
    assert stopped["fixed_code"] == reason
    assert execute.stopped_projection(stopped, codes) == stopped
    assert "private" not in json.dumps(stopped)
    assert capsys.readouterr() == ("", "")
