"""Release transport privacy boundaries; all commands and HTTP are offline fakes."""

from __future__ import annotations

import io
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import certifi
import pytest
from backend.tests.test_monetary_release_entrypoint import ROOT, release


@pytest.mark.parametrize("executable", ["gcloud", "gh", "pg_restore"])
def test_command_preserves_authentication_and_arguments_but_isolates_transport(
    monkeypatch, executable
):
    hostile = {
        "CLOUDSDK_CORE_LOG_HTTP": "true",
        "CLOUDSDK_CORE_LOG_HTTP_SHOW_REQUEST_BODY": "true",
        "CLOUDSDK_CORE_LOG_HTTP_STREAMING_BODY": "true",
        "CLOUDSDK_CORE_DISABLE_SSL_VALIDATION": "true",
        "CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE": "/untrusted/ca",
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_RUN": "https://foreign.invalid/",
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_SECRETMANAGER": "https://foreign.invalid/",
        "CLOUDSDK_API_ENDPOINT_OVERRIDES_UNKNOWN": "https://foreign.invalid/",
        "CLOUDSDK_PROXY_TYPE": "http",
        "CLOUDSDK_PROXY_ADDRESS": "foreign.invalid",
        "CLOUDSDK_PROXY_PORT": "8443",
        "HTTP_PROXY": "https://foreign.invalid/",
        "https_proxy": "https://foreign.invalid/",
        "ALL_PROXY": "https://foreign.invalid/",
        "grpc_proxy": "https://foreign.invalid/",
        "SSLKEYLOGFILE": "/untrusted/tls-log",
        "SSL_CERT_FILE": "/untrusted/ca",
        "SSL_CERT_DIR": "/untrusted/certs",
        "REQUESTS_CA_BUNDLE": "/untrusted/ca",
        "CURL_CA_BUNDLE": "/untrusted/ca",
        "GRPC_TRACE": "all",
        "GH_DEBUG": "api",
        "GIT_CONFIG": "/untrusted/git-config",
    }
    preserved = {"GH_TOKEN": "unit", "GOOGLE_APPLICATION_CREDENTIALS": "/unit/auth", "HOME": "/unit/home"}
    for name, value in hostile.items():
        monkeypatch.setenv(name, value)
    for name, value in preserved.items():
        monkeypatch.setenv(name, value)
    before = dict(os.environ)
    calls = []

    def command(arguments, **kwargs):
        calls.append((arguments, kwargs))
        return SimpleNamespace(stdout=b"bounded-native-result")

    monkeypatch.setattr(release.subprocess, "run", command)
    boundary = release.NativeBoundary(ROOT)
    arguments = ("unit-command", "unit-argument")
    assert boundary._command(executable, *arguments) == b"bounded-native-result"
    assert len(calls) == 1 and calls[0][0] == [executable, *arguments]
    kwargs = calls[0][1]
    assert kwargs["cwd"] == ROOT.resolve() and kwargs["timeout"] == 600
    assert kwargs["check"] is True and kwargs["stdout"] == subprocess.PIPE
    assert kwargs["stderr"] == subprocess.DEVNULL
    child = kwargs["env"]
    assert all(child[name] == value for name, value in preserved.items())
    assert child["GH_HOST"] == "github.com"
    assert child["CLOUDSDK_CORE_LOG_HTTP"] == "false"
    assert child["CLOUDSDK_CORE_VERBOSITY"] == "error"
    assert child["CLOUDSDK_CORE_DISABLE_SSL_VALIDATION"] == "false"
    assert child["CLOUDSDK_CORE_CUSTOM_CA_CERTS_FILE"] == certifi.where()
    assert child["CLOUDSDK_PROXY_ADDRESS"] == "" and child["CLOUDSDK_PROXY_PORT"] == "0"
    assert "CLOUDSDK_PROXY_TYPE" not in child
    assert child["NO_PROXY"] == child["NO_GRPC_PROXY"] == "*"
    for api in ("run", "secretmanager", "cloudbuild", "iam", "iamcredentials", "cloudtasks", "cloudscheduler", "cloudresourcemanager", "storage"):
        assert child["CLOUDSDK_API_ENDPOINT_OVERRIDES_" + api.upper()] == f"https://{api}.googleapis.com/"
    assert "CLOUDSDK_API_ENDPOINT_OVERRIDES_UNKNOWN" not in child
    for name in ("HTTP_PROXY", "https_proxy", "ALL_PROXY", "grpc_proxy", "SSLKEYLOGFILE", "SSL_CERT_DIR", "GRPC_TRACE", "GH_DEBUG", "GIT_CONFIG"):
        assert name not in child
    assert child["SSL_CERT_FILE"] == child["REQUESTS_CA_BUNDLE"] == child["CURL_CA_BUNDLE"] == certifi.where()
    assert os.environ == before


@pytest.mark.parametrize("fault", ["timeout", "failed", "os_error", "bound"])
def test_command_error_is_fixed_and_does_not_repeat_or_disclose(monkeypatch, fault):
    calls = []

    def command(arguments, **kwargs):
        calls.append(arguments)
        if fault == "timeout":
            raise subprocess.TimeoutExpired(arguments, 600, output=b"private-command-diagnostic")
        if fault == "failed":
            raise subprocess.CalledProcessError(1, arguments, stderr=b"private-command-diagnostic")
        if fault == "os_error":
            raise OSError("private-command-diagnostic")
        return SimpleNamespace(stdout=b"x" * (release.MAX_JSON + 1))

    monkeypatch.setattr(release.subprocess, "run", command)
    reason = "observation_size_bound" if fault == "bound" else "cloud_operation_unavailable_or_outcome_unknown"
    with pytest.raises(release.ReleaseDenied, match="^" + reason + "$") as caught:
        release.NativeBoundary(ROOT)._command("gcloud", "unit-command")
    assert "private-command" not in str(caught.value)
    assert len(calls) == 1


def health_boundary(monkeypatch, service):
    boundary = release.NativeBoundary(ROOT)
    revision = service + "-00042-unit"
    plan = SimpleNamespace(release="a" * 40)
    monkeypatch.setattr(boundary, "_cloud", lambda *args: {
        "status": {"url": "https://unit-service.run.app", "traffic": [{
            "tag": "monetary-candidate", "revisionName": revision,
            "url": "https://unit-candidate.run.app",
        }]}
    })
    commands = []

    def token(audience):
        commands.append(audience)
        assert audience == "https://unit-service.run.app"
        return "unit-id"

    monkeypatch.setattr(boundary, "_health_id_token", token)
    monkeypatch.setattr(boundary, "_command", lambda *args: pytest.fail("no access-token impersonation command"))
    health = {"ok": True, "release": plan.release}
    if service == release.SERVICES[0]:
        health["revision"] = revision
    else:
        health["role"] = "analysis-worker" if service == release.SERVICES[1] else "employer-worker"
    return boundary, plan, revision, health, commands


@pytest.mark.parametrize("service", release.SERVICES)
def test_health_ignores_proxy_ca_and_tls_key_logging_without_changing_identity(
    monkeypatch, tmp_path, service
):
    keylog = tmp_path / "must-not-exist"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "untrusted-ca"))
    monkeypatch.setenv("HTTPS_PROXY", "https://foreign.invalid/")
    boundary, plan, revision, health, commands = health_boundary(monkeypatch, service)
    requests = []

    def opener(*handlers):
        proxies = [handler for handler in handlers if isinstance(handler, urllib.request.ProxyHandler)]
        https = [handler for handler in handlers if isinstance(handler, urllib.request.HTTPSHandler)]
        redirects = [handler for handler in handlers if isinstance(handler, urllib.request.HTTPRedirectHandler)]
        assert len(proxies) == 1 and proxies[0].proxies == {}
        assert len(https) == 1
        context = https[0]._context
        assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
        assert context.keylog_filename is None and context.get_ca_certs()
        assert len(redirects) == 1
        assert redirects[0].redirect_request(None, None, 302, "", {}, "https://foreign.invalid/") is None

        def get(request, timeout):
            requests.append(request)
            assert request.full_url == "https://unit-candidate.run.app/api/health"
            assert request.get_header("Authorization") == "Bearer unit-id"
            assert request.get_method() == "GET" and request.data is None and timeout == 30
            return io.BytesIO(json.dumps(health).encode())

        return SimpleNamespace(open=get)

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    boundary._verify_http_health(plan, service, revision)
    assert len(commands) == len(requests) == 1 and not keylog.exists()


@pytest.mark.parametrize("response_delay,fault,reason", [
    (12.415923, None, None),
    (29.5, None, None),
    (30.5, None, "exact_candidate_http_health_unavailable"),
    (12.415923, "wrong_release", "exact_candidate_http_health_identity_mismatch"),
    (12.415923, "wrong_revision", "exact_candidate_http_health_identity_mismatch"),
])
def test_slow_health_response_has_bounded_time_to_prove_exact_identity(
    monkeypatch, response_delay, fault, reason
):
    boundary, plan, revision, health, commands = health_boundary(monkeypatch, release.SERVICES[0])
    calls = []
    if fault == "wrong_release":
        health["release"] = "b" * 40
    elif fault == "wrong_revision":
        health["revision"] = revision + "-other"

    def get(request, timeout):
        calls.append(request)
        # Model the socket's deadline without sleeping or opening a connection.
        # The first delay matches a real healthy revision's observed response.
        if response_delay > timeout:
            raise TimeoutError("private delayed health response")
        return io.BytesIO(json.dumps(health).encode())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: SimpleNamespace(open=get))
    if reason is None:
        boundary._verify_http_health(plan, release.SERVICES[0], revision)
    else:
        with pytest.raises(release.ReleaseDenied, match="^" + reason + "$"):
            boundary._verify_http_health(plan, release.SERVICES[0], revision)
    assert len(commands) == len(calls) == 1


@pytest.mark.parametrize("fault", ["redirect", "tls", "invalid_json", "bound", "wrong_release"])
def test_health_failures_never_disclose_transport_diagnostics_or_retry(monkeypatch, fault):
    boundary, plan, revision, health, commands = health_boundary(monkeypatch, release.SERVICES[0])
    calls = []

    def get(request, timeout):
        calls.append(request)
        if fault == "redirect":
            raise urllib.error.HTTPError(request.full_url, 302, "private-http-diagnostic", {}, None)
        if fault == "tls":
            raise ssl.SSLError("private-http-diagnostic")
        if fault == "invalid_json":
            return io.BytesIO(b"private-http-diagnostic")
        if fault == "bound":
            return io.BytesIO(b"x" * (release.MAX_JSON + 1))
        health["release"] = "b" * 40
        return io.BytesIO(json.dumps(health).encode())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: SimpleNamespace(open=get))
    reason = ("observation_size_bound" if fault == "bound" else
              "exact_candidate_http_health_identity_mismatch" if fault == "wrong_release" else
              "exact_candidate_http_health_unavailable")
    with pytest.raises(release.ReleaseDenied, match="^" + reason + "$") as caught:
        boundary._verify_http_health(plan, release.SERVICES[0], revision)
    assert "private-http" not in str(caught.value)
    assert len(commands) == len(calls) == 1


def test_missing_trusted_ca_refuses_before_acquiring_or_sending_health_token(monkeypatch, tmp_path):
    boundary, plan, revision, _, commands = health_boundary(monkeypatch, release.SERVICES[0])
    monkeypatch.setattr(certifi, "where", lambda: str(tmp_path / "missing-ca"))
    monkeypatch.setattr(urllib.request, "build_opener", lambda *args: pytest.fail("no HTTP may occur"))
    with pytest.raises(release.ReleaseDenied, match="^exact_candidate_http_health_unavailable$"):
        boundary._verify_http_health(plan, release.SERVICES[0], revision)
    assert commands == []


def test_global_fence_refusal_does_not_dispatch_hardened_transport(monkeypatch):
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_command", lambda *args: pytest.fail("no command may occur"))
    monkeypatch.setattr(boundary, "_cloud", lambda *args: pytest.fail("no cloud may occur"))
    with pytest.raises(release.ReleaseDenied, match="^native_external_consumer_and_provider_fence_verifier_missing$"):
        boundary.verify_fences(SimpleNamespace())


def identity_transport(monkeypatch, *, fault=None, payload=None):
    """Exercise real request preparation; replace only SDK identity and wire IO."""
    import document_refresh_auth as auth
    import document_refresh_probe as probe
    import native_fresh_fence as fence
    import requests

    session = requests.Session()
    calls, managers = [], []
    destination = ("https://iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/"
                   f"hirewiz-tasks@{release.PROJECT}.iam.gserviceaccount.com:generateIdToken")

    class Body(io.BytesIO):
        def read(self, size=-1, *, decode_content=False):
            assert decode_content is True
            return super().read(size)

    def send(request, **kwargs):
        calls.append((request, kwargs))
        assert request.url == destination and request.method == "POST"
        assert json.loads(request.body) == {"audience": "https://unit-service.run.app", "includeEmail": True}
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert kwargs["timeout"] == (5, 15) and kwargs["verify"] == certifi.where()
        assert session.trust_env is False and session.max_redirects == 0
        assert all(adapter.max_retries.total == 0 for adapter in session.adapters.values())
        if fault == "timeout":
            raise TimeoutError("private-access-token-or-provider-diagnostic")
        response = requests.Response()
        response.status_code = {"redirect": 302, "forbidden": 403}.get(fault, 200)
        response.headers["Location"] = "https://foreign.invalid/private-token"
        response.raw = Body(payload if payload is not None else b'{"token":"unit.id.token"}')
        return response

    session.send = send
    request = session.request

    def request_fault(method, uri, **kwargs):
        if fault == "wrong_endpoint":
            uri = uri.replace(":generateIdToken", ":generateAccessToken")
        if fault == "changed_audience":
            kwargs["data"] = b'{"audience":"https://foreign.invalid","includeEmail":true}'
        response = request(method, uri, **kwargs)
        # The real retained SDK materializes a streamed response in its response
        # handler. Reading raw again afterwards loses the returned token JSON.
        _ = response.content
        if fault == "replay":
            request(method, uri, **kwargs)
        return response

    monkeypatch.setattr(session, "request", request_fault)
    monkeypatch.setattr(probe, "sdk_library", lambda: Path("/unit/sdk/lib"))

    def manager(sdk, account):
        managers.append((sdk, account))
        assert sdk == Path("/unit/sdk/lib") and account == auth.RELEASE_ACCOUNT
        return SimpleNamespace(_http=lambda: SimpleNamespace(session=session))

    monkeypatch.setattr(fence, "SDKGoogleReads", manager)
    return calls, managers


def test_health_identity_uses_only_fixed_generate_id_token_and_observed_audience(monkeypatch, capsys, caplog):
    monkeypatch.setenv("HTTPS_PROXY", "https://foreign.invalid/")
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", "/untrusted/ca")
    calls, managers = identity_transport(monkeypatch)
    boundary = release.NativeBoundary(ROOT)
    monkeypatch.setattr(boundary, "_command", lambda *args: pytest.fail("no token CLI or access-token impersonation"))
    assert boundary._health_id_token("https://unit-service.run.app") == "unit.id.token"
    assert len(calls) == len(managers) == 1
    assert capsys.readouterr() == ("", "") and caplog.text == ""


@pytest.mark.parametrize("fault,payload,physical_posts", [
    ("timeout", None, 1),
    ("redirect", None, 1),
    ("forbidden", None, 1),
    ("replay", None, 1),
    ("wrong_endpoint", None, 0),
    ("changed_audience", None, 0),
    (None, b"private-token-not-json", 1),
    (None, b"x" * (release.MAX_JSON + 1), 1),
    (None, b'{"token":"unit.id.token","unexpected":"private-token"}', 1),
    (None, b'{"token":"line\\r\\nbreak"}', 1),
    (None, b'{"token":19}', 1),
    (None, b'{"token":""}', 1),
])
def test_health_identity_failure_is_private_bounded_and_never_replays(
    monkeypatch, capsys, caplog, fault, payload, physical_posts
):
    calls, managers = identity_transport(monkeypatch, fault=fault, payload=payload)
    with pytest.raises(release.ReleaseDenied, match="^exact_candidate_id_token_unavailable$") as caught:
        release.NativeBoundary(ROOT)._health_id_token("https://unit-service.run.app")
    assert len(managers) == 1 and len(calls) == physical_posts
    assert "private" not in str(caught.value) and caught.value.__suppress_context__
    assert capsys.readouterr() == ("", "") and caplog.text == ""


@pytest.mark.parametrize("audience", [
    "http://unit-service.run.app", "https://foreign.invalid", "https://user@unit-service.run.app",
    "https://unit-service.run.app/path", "https://unit-service.run.app?token=private",
    "https://unit-service.run.app#private",
])
def test_health_identity_refuses_foreign_audiences_before_sdk_access(monkeypatch, audience):
    calls, managers = identity_transport(monkeypatch)
    with pytest.raises(release.ReleaseDenied, match="^exact_candidate_id_token_unavailable$"):
        release.NativeBoundary(ROOT)._health_id_token(audience)
    assert calls == managers == []


def test_health_identity_refuses_tls_key_logging_before_authentication(monkeypatch, tmp_path):
    calls, managers = identity_transport(monkeypatch)
    keylog = tmp_path / "must-not-contain-token-session-keys"
    monkeypatch.setenv("SSLKEYLOGFILE", str(keylog))
    with pytest.raises(release.ReleaseDenied, match="^exact_candidate_id_token_unavailable$"):
        release.NativeBoundary(ROOT)._health_id_token("https://unit-service.run.app")
    assert calls == managers == [] and not keylog.exists()
