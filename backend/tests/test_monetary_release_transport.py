"""Release transport privacy boundaries; all commands and HTTP are offline fakes."""

from __future__ import annotations

import io
import json
import os
import ssl
import subprocess
import urllib.error
import urllib.request
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

    def token(*args):
        commands.append(args)
        assert args == ("gcloud", "auth", "print-identity-token", "--impersonate-service-account",
                        f"hirewiz-tasks@{release.PROJECT}.iam.gserviceaccount.com",
                        "--audiences", "https://unit-service.run.app", "--quiet")
        return b"unit-id"

    monkeypatch.setattr(boundary, "_command", token)
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
            assert request.get_method() == "GET" and request.data is None and timeout == 10
            return io.BytesIO(json.dumps(health).encode())

        return SimpleNamespace(open=get)

    monkeypatch.setattr(urllib.request, "build_opener", opener)
    boundary._verify_http_health(plan, service, revision)
    assert len(commands) == len(requests) == 1 and not keylog.exists()


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
