"""Offline exact-provider capability controls; no real keys or network calls."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "infra/gcp"))
module = importlib.import_module("native_model_auth_denial")


def credential(provider="openai"):
    value = "gsk_synthetic_groq_value" if provider == "groq" else "sk-synthetic-openai-value"
    return module.ForeignModelCredential(provider, value, ("synthetic-retained-origin",))


class ResponseTransport:
    def __init__(self, status=401, body=None):
        self.status = status
        self.body = body if body is not None else b'{"error":{"code":"invalid_api_key"}}'
        self.calls = []

    def get(self, item, deadline):
        self.calls.append((item.provider, item.value, deadline))
        return self.status, self.body


def test_exact_openai_denial_accepts_retained_bytes_without_secret_output(capsys):
    item = credential()
    transport = ResponseTransport(body=json.dumps({"error": {
        "code": "invalid_api_key", "message": item.value}}).encode())
    assert module.observe((item,), transport=transport) is None
    assert transport.calls[0][:2] == (item.provider, item.value)
    assert item.value not in repr(item) and capsys.readouterr() == ("", "")


def test_groq_uses_same_exact_denial_predicate_without_acceptance_toggle():
    transport = ResponseTransport()
    module.observe((credential("groq"),), transport=transport)
    assert transport.calls[0][0] == "groq"
    assert not hasattr(module, "GROQ_INVALID_API_KEY_CONTRACT_VERIFIED")


def test_all_foreign_credentials_participate_in_parallel_denial():
    items = (credential(), credential("groq"), module.ForeignModelCredential(
        "openai", "sk-second-synthetic-openai-value", ("another-retained-origin",)))
    transport = ResponseTransport()
    module.observe(items, transport=transport)
    assert {(provider, value) for provider, value, _ in transport.calls} == {
        (item.provider, item.value) for item in items}


@pytest.mark.parametrize("status", [200, 301, 400, 403, 404, 429, 500, 503])
def test_non401_response_never_establishes_authentication_retirement(status):
    with pytest.raises(module.DenialUnavailable, match="provider_exact_invalid_key_denial_unavailable"):
        module.observe((credential(),), transport=ResponseTransport(status=status))


@pytest.mark.parametrize("body", [
    b'{}', b'{"error":{"code":"ip_not_authorized"}}',
    b'{"error":{"code":"organization_membership_required"}}',
    b'{"error":{"message":"Invalid API key"}}', b'not JSON',
    b'{"error":{"code":"invalid_api_key","code":"invalid_api_key"}}',
    b'x' * (module.MAX_RESPONSE_BYTES + 1),
])
def test_wrong_missing_or_ambiguous_error_code_refuses(body):
    with pytest.raises(module.DenialUnavailable):
        module.observe((credential(),), transport=ResponseTransport(body=body))


def test_admin_issuance_capability_and_provider_mismatch_refuse_before_request():
    transport = ResponseTransport()
    for item in (module.ForeignModelCredential("openai", "sk-admin-synthetic-value", ("origin",)),
                 module.ForeignModelCredential("groq", "sk-synthetic-value", ("origin",))):
        with pytest.raises(module.DenialUnavailable):
            module.observe((item,), transport=transport)
    assert transport.calls == []


def test_outer_oldest_deadline_is_not_restarted(monkeypatch):
    monkeypatch.setattr(module.time, "monotonic", lambda: 31.0)
    transport = ResponseTransport()
    with pytest.raises(module.DenialUnavailable, match="provider_observation_expired"):
        module.observe((credential(),), deadline=30.0, transport=transport)
    assert transport.calls == []


def mock_native(monkeypatch, handler):
    configuration = []
    def transport(**kwargs):
        configuration.append(kwargs)
        return module.httpx.MockTransport(handler)
    monkeypatch.setattr(module.httpx, "HTTPTransport", transport)
    return configuration


def native_response(status=401, headers=None, body=None):
    return module.httpx.Response(status, headers=headers or {"Content-Type": "application/json"},
        stream=module.httpx.ByteStream(body or b'{"error":{"code":"invalid_api_key"}}'))


def test_real_transport_uses_only_fixed_origin_bearer_no_scope_headers(monkeypatch):
    calls = []
    response = native_response()
    def open_request(request):
        calls.append(request)
        return response
    configuration = mock_native(monkeypatch, open_request)
    item = credential()
    status, body = module.NativeTransport().get(item, module.time.monotonic() + 10)
    request = calls[0]
    assert status == 401 and json.loads(body)["error"]["code"] == "invalid_api_key"
    assert str(request.url) == module.ENDPOINTS["openai"] and request.method == "GET"
    assert request.headers["Authorization"] == "Bearer " + item.value
    assert not any(name.lower() in {"openai-organization", "openai-project"} for name in request.headers)
    assert request.headers["User-Agent"].startswith("python-httpx/")
    assert configuration[0]["retries"] == 0 and configuration[0]["trust_env"] is False
    assert configuration[0]["verify"].check_hostname is True
    assert 0 < request.extensions["timeout"]["read"] <= 5 and response.is_closed


@pytest.mark.parametrize("defect", ["redirect", "html", "compressed", "oversized"])
def test_native_transport_refuses_origin_type_encoding_and_size_drift(monkeypatch, defect):
    response = native_response(
        status=301 if defect == "redirect" else 401,
        headers={"Content-Type": "text/html" if defect == "html" else "application/json",
                 "Content-Encoding": "gzip" if defect == "compressed" else "identity",
                 "Location": "https://untrusted.example/models"},
        body=b'x' * (module.MAX_RESPONSE_BYTES + 1) if defect == "oversized" else None)
    calls = []
    mock_native(monkeypatch, lambda request: calls.append(request) or response)
    with pytest.raises(module.DenialUnavailable):
        module.NativeTransport().get(credential(), module.time.monotonic() + 10)
    assert response.is_closed and len(calls) == 1


def test_native_401_response_is_read_but_network_exception_is_sanitized(monkeypatch):
    item = credential()
    mock_native(monkeypatch, lambda request: native_response())
    assert module.NativeTransport().get(item, module.time.monotonic() + 10)[0] == 401
    def unknown(*args, **kwargs):
        raise RuntimeError(item.value)
    mock_native(monkeypatch, unknown)
    with pytest.raises(module.DenialUnavailable) as denied_error:
        module.NativeTransport().get(item, module.time.monotonic() + 10)
    assert item.value not in str(denied_error.value)
