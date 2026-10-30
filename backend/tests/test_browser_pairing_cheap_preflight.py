"""Untrusted pairing transport fails before immutable metadata observations."""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.tests.fixtures.pairing_authority import jwk, sign
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from app.domains.recovery import browser_pairing_production as production
from app.domains.recovery.browser_pairing import GatewayVerifier, NativeBrowserPairing
from app.domains.recovery.contracts import canonical
from app.domains.recovery.device_transport_admission import DeviceTransportVerifier
from app.domains.recovery.store import GuardDenied

EXTENSION = "a" * 32
ORIGIN = f"chrome-extension://{EXTENSION}"
NOW = 1_700_000_000_000


@pytest.fixture
def transport():
    private = ec.generate_private_key(ec.SECP256R1())
    def make(operation="prepare_request", **changes):
        data = {"protocol_version": 2, "operation": operation, "key": jwk(private),
            "extension_id": EXTENSION, "revision": "companion_1", "request_id": str(uuid4()),
            "issued_at_ms": NOW, **changes}
        return {**data, "signature": sign(private, "transport-request", data)}
    return make


def configured(monkeypatch):
    browser = object.__new__(production.ConfiguredBrowserPairing)
    native = object.__new__(NativeBrowserPairing)
    native.coordinator = SimpleNamespace(now_ms=lambda: NOW)
    native.gateway = GatewayVerifier(key=b"a" * 32, issuer="gateway_1", origin="https://example.test")
    browser._gateway, browser._admission_owner = native.gateway, object()
    browser._gateway_now_ms = lambda: NOW
    browser._check_configuration = lambda: SimpleNamespace(extension_id=EXTENSION, executor_revision="companion_1")
    browser._device_transport = DeviceTransportVerifier(now_ms=lambda: NOW)
    browser._service = lambda: native
    browser._observations = lambda _: pytest.fail("untrusted transport entered metadata scope")
    monkeypatch.setattr(production, "configuration", lambda: SimpleNamespace(
        extension_id=EXTENSION, executor_revision="companion_1"))
    return browser


@pytest.mark.parametrize("encoded,signature", [("", ""), ("invalid", "a" * 64), ("abc", "wrong")])
def test_invalid_gateway_hmac_never_enters_metadata_scope(monkeypatch, encoded, signature):
    browser = configured(monkeypatch)
    with pytest.raises(GuardDenied):
        browser.candidate("challenge", b"{}", encoded, signature)


@pytest.mark.parametrize("operation,wire_operation", [("prepare", "prepare_request"), ("inspect-key", "key_status")])
@pytest.mark.parametrize("defect", ["signature", "key", "origin", "extension", "revision", "expired"])
def test_invalid_self_signed_transport_never_enters_metadata_scope(
    monkeypatch, transport, operation, wire_operation, defect,
):
    browser = configured(monkeypatch)
    changes = {"extension_id": "b" * 32} if defect == "extension" else {}
    if defect == "revision":
        changes["revision"] = "other_1"
    if defect == "expired":
        changes["issued_at_ms"] = NOW - 5_000
    body = transport(wire_operation, **changes)
    if defect == "signature":
        body["signature"] = "a" * 86
    if defect == "key":
        body["key"] = {"private": "rejected"}
    origin = f"chrome-extension://{'b' * 32}" if defect == "origin" else ORIGIN
    with pytest.raises((GuardDenied, ValidationError)):
        browser.device(operation, canonical(body).encode(), origin)


@pytest.mark.parametrize("operation", ["create", "complete", "refresh", "challenge", "status", "refresh-challenge", "unknown"])
def test_malformed_known_id_operations_fail_before_metadata(monkeypatch, operation):
    browser = configured(monkeypatch)
    with pytest.raises((GuardDenied, ValidationError)):
        browser.device(operation, b"{}", ORIGIN)


@pytest.mark.parametrize("operation,wire_operation", [("prepare", "prepare_request"), ("inspect-key", "key_status")])
def test_valid_self_signed_preflight_is_local(transport, operation, wire_operation):
    current = SimpleNamespace(extension_id=EXTENSION, executor_revision="companion_1")
    production._device_preflight(operation, canonical(transport(wire_operation)).encode(),
        ORIGIN, current, now_ms=NOW)
