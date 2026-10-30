"""Exact methods/local core; synthetic clocks/registry/command/protected delegates.

No HTTP/provider/emulator/owner records or native custody are exercised here.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.tests.fixtures import pairing_authority as fixture
from cryptography.hazmat.primitives.asymmetric import ec

from app.domains.recovery import browser_pairing_production as production
from app.domains.recovery import device_transport_admission as transport
from app.domains.recovery.browser_pairing import NativeBrowserPairing
from app.domains.recovery.browser_pairing_production import ConfiguredBrowserPairing
from app.domains.recovery.contracts import canonical
from app.domains.recovery.device_transport_admission import (
    DeviceIngressTiming,
    DeviceTransportVerifier,
    VerifiedDeviceAdmission,
)
from app.domains.recovery.store import GuardDenied

NOW, EXTENSION, RELEASE = fixture.NOW, "a" * 32, fixture.RELEASE
ORIGIN = f"chrome-extension://{EXTENSION}"
KEY = "ab" * 32
OPERATIONS = sorted(transport.SIGNED_DEVICE_OPERATIONS)


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setattr(fixture, "EXTENSION", EXTENSION)
    core = fixture.Scenario(tmp_path)
    clock = SimpleNamespace(wall=NOW, mono=100.0)
    monkeypatch.setattr(transport, "monotonic", lambda: clock.mono)
    delays, nonces, commands, overrides = {}, {}, [], {}
    consuming = [False]
    def advance(ms):
        clock.wall += ms
        clock.mono += ms / 1000
        core.now = clock.wall
    def delayed(phase):
        advance(delays.pop(phase, 0))
    lock = Lock()
    class Tx:
        def get(self, namespace, key):
            if namespace == "browser_device_requests":
                return nonces.get((namespace, key))
            if (namespace, key) in overrides:
                return overrides[namespace, key]
            return core.store.transact(lambda tx: tx.get(namespace, key))
        def put(self, namespace, key, value, **kwargs):
            nonces[namespace, key] = value
    class Registry:
        def run(self, operation, before_attempt):
            before_attempt()
            with lock:
                return operation(Tx())
    def control(tx):
        delayed("consume_control" if consuming[0] else "lookup_control")
    def status(pairing_id):
        delayed("post_consume")
        return core.service.status(pairing_id)
    native = object.__new__(NativeBrowserPairing)
    native.coordinator = SimpleNamespace(now_ms=lambda: clock.wall, registry=Registry(),
        _control=control, pin=object(), fence=SimpleNamespace(check=lambda pin: delayed("fence")), pairing_status=status)
    native.device_transport = DeviceTransportVerifier(now_ms=lambda: clock.wall)
    native.paired_devices = SimpleNamespace(key_status=lambda *args, **kwargs: (delayed("post_consume") or "UNCLAIMED"))
    original_consume = native._consume
    def consume(*args, **kwargs):
        consuming[0] = True
        try:
            return original_consume(*args, **kwargs)
        finally:
            consuming[0] = False
    native._consume = consume
    original_origin, original_lookup = native._device_origin, native._lookup_key
    def origin(*args, **kwargs):
        delayed("origin")
        return original_origin(*args, **kwargs)
    def lookup(*args, **kwargs):
        delayed("lookup")
        return original_lookup(*args, **kwargs)
    native._device_origin, native._lookup_key = origin, lookup
    def execute(command, parameters):
        commands.append(command)
        delayed("post_consume")
        return getattr(core.service, command)(**parameters)
    native._execute = execute
    browser = object.__new__(ConfiguredBrowserPairing)
    pins = {"synthetic_configuration": "original"}
    current = SimpleNamespace(extension_id=EXTENSION, executor_revision=RELEASE, model_dump=lambda **kw: dict(pins))
    browser._raw, browser._gateway_digest, browser._origin = canonical(pins), hashlib.sha256(KEY.encode()).digest(), "https://candidate.example.invalid"
    browser._device_transport, browser._admission_owner = native.device_transport, object()
    browser._gateway_now_ms = lambda: clock.wall
    monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", KEY)
    monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", browser._origin)
    monkeypatch.setattr(production, "configuration", lambda: current)
    def service():
        delayed("composition")
        return native
    browser._service = service
    @contextmanager
    def observations(value):
        delayed("observation")
        yield
        delayed("exit")
    browser._observations = observations
    return SimpleNamespace(core=core, clock=clock, advance=advance, delays=delays, nonces=nonces,
        commands=commands, overrides=overrides, native=native, browser=browser, pins=pins, tx=Tx)


def wire(case, operation, **changes):
    if operation in {"prepare", "inspect-key"}:
        data = {"protocol_version": 2, "operation": "prepare_request" if operation == "prepare" else "key_status",
            "key": fixture.jwk(case.core.device_key), "extension_id": EXTENSION, "revision": RELEASE,
            "request_id": str(uuid4()), "issued_at_ms": case.clock.wall, **changes}
    else:
        if operation == "status":
            request = case.core.requested()
        elif operation == "challenge":
            request = case.core.confirmed()
        else:
            request, _ = case.core.complete()
        data = {"protocol_version": 2, "operation": {"status": "status", "challenge": "device_challenge",
            "refresh-challenge": "refresh_challenge"}[operation], "identity": request["device_id"] if operation == "refresh-challenge" else request["pairing_id"],
            "request_id": str(uuid4()), "issued_at_ms": case.clock.wall, **changes}
    return json.dumps({**data, "signature": fixture.sign(case.core.device_key, "transport-request", data)}).encode()


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("phase", ["composition", "observation", "origin", "consume_control", "post_consume", "exit"])
@pytest.mark.parametrize("delay", [4999, 5001])
def test_signed_device_bound_processing_survives_native_and_cold_phases(case, operation, phase, delay):
    raw = wire(case, operation)
    case.delays[phase] = delay
    case.browser.device(operation, raw, ORIGIN)
    assert len(case.nonces) == 1
    assert next(iter(case.nonces.values())) == {"expires_at_ms": NOW + 35_000}
    assert not case.native.device_transport._timings and not case.native.device_transport._admissions


@pytest.mark.parametrize("operation", ["status", "challenge", "refresh-challenge"])
@pytest.mark.parametrize("delay", [4999, 5001])
def test_known_id_key_lookup_still_verifies_actual_signature_after_delay(case, operation, delay):
    raw = wire(case, operation)
    case.delays["lookup"] = delay
    case.browser.device(operation, raw, ORIGIN)
    assert len(case.nonces) == 1


@pytest.mark.parametrize("phase", ["composition", "observation", "origin", "lookup", "consume_control", "post_consume", "exit"])
def test_device_processing_at_30_seconds_refuses_output(case, phase):
    raw = wire(case, "status")
    case.delays[phase] = 30_000
    with pytest.raises(GuardDenied):
        case.browser.device("status", raw, ORIGIN)
    assert not case.native.device_transport._timings and not case.native.device_transport._admissions


@pytest.mark.parametrize("operation", OPERATIONS)
@pytest.mark.parametrize("failure", ["expired", "future"])
def test_original_incoming_five_second_window_is_closed_before_composition(case, operation, failure):
    raw = wire(case, operation, issued_at_ms=NOW - 5000 if failure == "expired" else NOW + 1)
    case.browser._service = lambda: pytest.fail("expired/future transport composed native service")
    with pytest.raises(GuardDenied):
        case.browser.device(operation, raw, ORIGIN)
    assert not case.nonces and not case.commands


@pytest.mark.parametrize("operation", ["prepare", "inspect-key"])
@pytest.mark.parametrize("failure", ["signature", "origin", "key", "revision", "malformed"])
def test_body_key_transport_rejects_bad_input_before_any_composition(case, operation, failure):
    raw, origin = wire(case, operation), ORIGIN
    data = json.loads(raw)
    if failure == "signature":
        data["signature"] = "a" * 86
    elif failure == "origin":
        origin = "chrome-extension://" + "b" * 32
    elif failure == "key":
        data["key"] = {"private": "denied"}
    elif failure == "revision":
        data["revision"] = "other_revision"
    else:
        data = {}
    case.browser._service = lambda: pytest.fail("invalid input composed native service")
    from pydantic import ValidationError
    with pytest.raises((GuardDenied, ValidationError)):
        case.browser.device(operation, json.dumps(data).encode(), origin)
    assert not case.nonces and not case.commands


def test_unknown_id_signature_is_never_replaced_by_timing_capture(case):
    raw = wire(case, "status")
    data = json.loads(raw)
    data["signature"] = fixture.sign(ec.generate_private_key(ec.SECP256R1()), "transport-request", {key: value for key, value in data.items() if key != "signature"})
    case.delays["lookup"] = 5001
    with pytest.raises(GuardDenied):
        case.browser.device("status", json.dumps(data).encode(), ORIGIN)
    assert not case.nonces and not case.commands


@pytest.mark.parametrize("failure", ["configuration", "key", "origin", "verifier"])
def test_mid_composition_drift_never_binds_device_admission(case, monkeypatch, failure):
    raw = wire(case, "status")
    def changed():
        case.advance(5001)
        if failure == "configuration":
            case.pins["synthetic_configuration"] = "changed"
        elif failure == "key":
            monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", "cd" * 32)
        elif failure == "origin":
            monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", "https://changed.example.invalid")
        else:
            other = object.__new__(NativeBrowserPairing)
            other.device_transport = DeviceTransportVerifier(now_ms=lambda: case.clock.wall)
            return other
        return case.native
    case.browser._service = changed
    with pytest.raises(GuardDenied):
        case.browser.device("status", raw, ORIGIN)
    assert not case.nonces and not case.commands


@pytest.mark.parametrize("failure", ["configuration", "key", "origin"])
def test_device_configuration_drift_at_observation_exit_refuses_export(case, monkeypatch, failure):
    raw = wire(case, "status")
    def checked_service():
        case.browser._check_configuration()
        return case.native
    case.browser._service = checked_service
    @contextmanager
    def changed_exit(value):
        yield
        if failure == "configuration":
            case.pins["synthetic_configuration"] = "changed"
        elif failure == "key":
            monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", "cd" * 32)
        else:
            monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", "https://changed.example.invalid")
    case.browser._observations = changed_exit
    with pytest.raises(GuardDenied, match="configuration changed"):
        case.browser.device("status", raw, ORIGIN)
    assert len(case.nonces) == 1 and not case.native.device_transport._timings


@pytest.mark.parametrize("operation", ["status", "challenge", "refresh-challenge"])
@pytest.mark.parametrize("failure", ["key", "origin", "absent"])
def test_verified_native_key_or_origin_drift_before_nonce_refuses(case, operation, failure):
    raw = wire(case, operation)
    proof = json.loads(raw)
    namespace = "devices" if operation == "refresh-challenge" else "pairing_requests"
    row = deepcopy(case.tx().get(namespace, proof["identity"]))
    original_consume = case.native._consume
    def changed(*args, **kwargs):
        if failure == "absent":
            row_value = None
        else:
            current = row if namespace == "devices" else row["request"]
            current["public_key" if failure == "key" else "extension_id"] = fixture.jwk(ec.generate_private_key(ec.SECP256R1())) if failure == "key" else "b" * 32
            row_value = row
        case.overrides[namespace, proof["identity"]] = row_value
        return original_consume(*args, **kwargs)
    case.native._consume = changed
    with pytest.raises(GuardDenied):
        case.browser.device(operation, raw, ORIGIN)
    assert not case.nonces and not case.commands


def test_replay_and_concurrent_signed_nonce_have_one_winner(case):
    raw = wire(case, "status")
    def one():
        try:
            case.native.device("status", raw, ORIGIN)
            return "success"
        except GuardDenied:
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda _: one(), range(2)))
    assert sorted(result) == ["denied", "success"] and len(case.nonces) == 1
    with pytest.raises(GuardDenied):
        case.native.device("status", raw, ORIGIN)


def test_existing_capture_can_finish_but_old_request_cannot_enter_again(case):
    raw = wire(case, "status")
    native, verifier = case.native, case.native.device_transport
    ingress = verifier.capture("status", raw, ORIGIN, owner=native)
    case.advance(5001)
    native.device_captured("status", raw, ORIGIN, ingress)
    with pytest.raises(GuardDenied):
        native.device("status", raw, ORIGIN)
    assert len(case.nonces) == 1


@pytest.mark.parametrize("failure", ["wall_deadline", "mono_deadline", "wall_high_water", "mono_high_water"])
def test_either_deadline_or_clock_high_water_refuses_before_key_use(case, failure):
    raw = wire(case, "status")
    native, verifier = case.native, case.native.device_transport
    ingress = verifier.capture("status", raw, ORIGIN, owner=native)
    if failure.endswith("high_water"):
        case.advance(5001)
        verifier.check_timing(ingress, native=native, operation="status", raw=raw, origin=ORIGIN)
    if failure == "wall_deadline":
        case.clock.wall += 30000
    elif failure == "mono_deadline":
        case.clock.mono += 30
    elif failure == "wall_high_water":
        case.clock.wall -= 1
    else:
        case.clock.mono -= .001
    native._lookup_key = lambda proof: pytest.fail("expired/rollback capture used a native key")
    with pytest.raises(GuardDenied):
        native.device_captured("status", raw, ORIGIN, ingress)
    assert not case.nonces


def test_construction_slot_binds_once_without_caller_timestamp_or_allow_flag(case):
    raw = wire(case, "status")
    native, verifier, slot = case.native, case.native.device_transport, object()
    with pytest.raises(TypeError):
        verifier.capture("status", raw, ORIGIN, owner=native, now_ms=NOW)
    ingress = verifier.capture_composition("status", raw, ORIGIN, owner=slot)
    with pytest.raises(GuardDenied):
        verifier.verify(ingress, native=native, operation="status", raw=raw, origin=ORIGIN)
    with pytest.raises(GuardDenied):
        verifier.bind_composed(ingress, construction_owner=object(), native=native, operation="status", raw=raw, origin=ORIGIN)
    verifier.bind_composed(ingress, construction_owner=slot, native=native, operation="status", raw=raw, origin=ORIGIN)
    with pytest.raises(GuardDenied):
        verifier.bind_composed(ingress, construction_owner=slot, native=native, operation="status", raw=raw, origin=ORIGIN)
    native.device_captured("status", raw, ORIGIN, ingress)
    assert len(case.nonces) == 1


def test_capture_opaque_not_admission_or_reusable_verification(case):
    raw = wire(case, "status")
    verifier, native = case.native.device_transport, case.native
    for cls in (DeviceIngressTiming, VerifiedDeviceAdmission):
        with pytest.raises(TypeError):
            cls()
    for forged in (True, object.__new__(DeviceIngressTiming), object.__new__(VerifiedDeviceAdmission)):
        with pytest.raises(GuardDenied):
            verifier.check(forged, native=native, operation="status", raw=raw, origin=ORIGIN)
    ingress = verifier.capture("status", raw, ORIGIN, owner=native)
    with pytest.raises(GuardDenied):
        verifier.check(ingress, native=native, operation="status", raw=raw, origin=ORIGIN)
    admission = verifier.verify(ingress, native=native, operation="status", raw=raw, origin=ORIGIN)
    with pytest.raises(GuardDenied):
        verifier.verify(ingress, native=native, operation="status", raw=raw, origin=ORIGIN)
    verifier.finish(ingress, owner=native)
    with pytest.raises(GuardDenied):
        verifier.check(admission, native=native, operation="status", raw=raw, origin=ORIGIN)
    assert not case.nonces


@pytest.mark.parametrize("failure", ["raw", "operation", "origin", "owner", "verifier", "wall", "monotonic", "thread", "task"])
def test_capture_is_request_native_execution_and_clock_bound(case, failure):
    raw = wire(case, "status")
    verifier, native = case.native.device_transport, case.native
    ingress = verifier.capture("status", raw, ORIGIN, owner=native)
    target, actual_raw, operation, origin = native, raw, "status", ORIGIN
    if failure == "raw":
        actual_raw = b"{}"
    elif failure == "operation":
        operation = "challenge"
    elif failure == "origin":
        origin = "chrome-extension://" + "b" * 32
    elif failure in {"owner", "verifier"}:
        target = object.__new__(NativeBrowserPairing)
        target.coordinator = native.coordinator
        target.device_transport = verifier if failure == "owner" else DeviceTransportVerifier(now_ms=lambda: case.clock.wall)
    elif failure == "wall":
        case.clock.wall -= 1
    elif failure == "monotonic":
        case.clock.mono -= .001
    def denied():
        with pytest.raises(GuardDenied):
            verifier.verify(ingress, native=target, operation=operation, raw=actual_raw, origin=origin)
    if failure == "thread":
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(denied).result(timeout=2)
    elif failure == "task":
        async def separate():
            denied()
        asyncio.run(separate())
    else:
        denied()
    assert not case.nonces and not case.commands


@pytest.mark.parametrize("operation", ["create", "complete", "refresh"])
@pytest.mark.parametrize("delay", [5001, 10000])
def test_unsigned_proof_operations_keep_actual_core_ten_second_expiry(case, operation, delay):
    if operation == "create":
        prepared = case.core.service.prepare_request(fixture.jwk(case.core.device_key), EXTENSION, RELEASE)
        data = {"pairing_id": prepared["request"]["pairing_id"], "nonce": prepared["nonce"],
            "signature": fixture.sign(case.core.device_key, "request", prepared["request"])}
    elif operation == "complete":
        data = case.core.proof()
    else:
        request, _ = case.core.complete()
        proof = case.core.service.refresh_challenge(request["device_id"])
        data = {"challenge_id": proof["payload"]["challenge_id"], "nonce": proof["nonce"],
            "signature": fixture.sign(case.core.device_key, "device-challenge", proof["payload"])}
    # No claim/native activation assertion is made by this explicit core delegate.
    case.native.paired_devices.check_claim = lambda value: None
    case.delays["observation"] = delay
    if delay == 10000:
        with pytest.raises(GuardDenied):
            case.browser.device(operation, json.dumps(data).encode(), ORIGIN)
    else:
        case.browser.device(operation, json.dumps(data).encode(), ORIGIN)
    assert not case.nonces and not case.native.device_transport._timings
