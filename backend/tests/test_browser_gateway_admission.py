"""Actual gateway/core methods with synthetic clocks/current-identity/registry IO.

No owner data, provider SDK, native server or deployed configuration is read.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Lock
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.tests.fixtures.pairing_authority import NOW, Scenario

from app.domains.recovery import browser_pairing as transport
from app.domains.recovery import browser_pairing_production as production
from app.domains.recovery.browser_pairing import (
    GatewayVerifier,
    NativeBrowserPairing,
    VerifiedGatewayAdmission,
)
from app.domains.recovery.browser_pairing_production import ConfiguredBrowserPairing
from app.domains.recovery.contracts import canonical
from app.domains.recovery.store import GuardDenied

KEY = b"synthetic-gateway-admission-key-not-live"
ORIGIN = "https://candidate.example.invalid"


@pytest.fixture
def case(tmp_path, monkeypatch):
    core = Scenario(tmp_path)
    requested = core.requested()
    clock = SimpleNamespace(wall=NOW, mono=100.0)
    monkeypatch.setattr(transport, "monotonic", lambda: clock.mono)
    delays, records, commands, core_replies = {}, {}, [], []
    account = {"active": True, "issued": NOW - 1000, "expires": NOW + 3_600_000, "identity_reads": 0}
    context = {"candidate_id": 1, "account_binding_id": str(uuid4()), "session_id": core.session}
    principal = core.store.transact(lambda tx: tx.get("subjects", core.subject))["principal_sha256"]
    session_registration = str(uuid4())
    session_overrides, read_delays = {}, {}

    def advance(ms):
        clock.wall += ms
        clock.mono += ms / 1000

    def delayed(phase):
        advance(delays.pop(phase, 0))

    lock = Lock()
    class Tx:
        def get(self, namespace, key):
            if namespace == "pairing_web_sessions" and key == context["session_id"]:
                value = {"session_id": core.session, "account_binding_id": context["account_binding_id"],
                    "subject_uuid": core.subject, "principal_sha256": principal, "auth_generation": 1,
                    "registration_event_id": session_registration, "issued_at_ms": account["issued"],
                    "expires_at_ms": account["expires"], "active": account["active"], **session_overrides}
            elif namespace == "browser_gateway_requests":
                value = records.get((namespace, key))
            else:
                value = core.store.transact(lambda tx: tx.get(namespace, key))
            advance(read_delays.pop((namespace, key), 0))
            return value
        def put(self, namespace, key, value, **kwargs):
            records[namespace, key] = value
    class Registry:
        def run(self, operation, before_attempt):
            before_attempt()
            with lock:
                return operation(Tx())
    def identity(tx, value, pairing, now):
        account["identity_reads"] += 1
        if not account["active"] or now >= account["expires"] or value.model_dump(mode="json") != context:
            raise GuardDenied("Synthetic current identity denied")
        answer = {"subject_uuid": core.subject, "session_id": core.session,
            "principal_sha256": principal, "auth_generation": 1}
        delayed("identity")
        if account.pop("disable_after_read", False):
            account["active"] = False
        return answer
    def scoped(value):
        delayed("scoped")
    coordinator = SimpleNamespace(now_ms=lambda: clock.wall, registry=Registry(),
        _control=lambda tx: delayed("control"), core=core.service, pin=object(),
        fence=SimpleNamespace(check=lambda pin: delayed("fence")))
    native = object.__new__(NativeBrowserPairing)
    native.coordinator, native.gateway = coordinator, GatewayVerifier(key=KEY, issuer="hirewiz_bff_v1", origin=ORIGIN)
    native.paired_devices = SimpleNamespace(scoped=SimpleNamespace(resolve=scoped))
    native.identity = SimpleNamespace(resolve=identity)
    def execute(command, parameters):
        commands.append(command)
        delayed("command")
        core.now = clock.wall
        if command == "candidate_challenge":
            result = core.service.candidate_challenge(**parameters)
            core_replies.append(result)
            return result
        raise AssertionError("Unexpected synthetic command")
    native._execute = execute
    native.password = SimpleNamespace(execute=lambda *args: (_ for _ in ()).throw(GuardDenied("Synthetic password boundary denied")))
    browser = object.__new__(ConfiguredBrowserPairing)
    pins = {"synthetic_configuration": "original"}
    browser._raw = canonical(pins)
    browser._gateway_digest = hashlib.sha256(KEY.hex().encode()).digest()
    browser._origin = ORIGIN
    browser._gateway, browser._admission_owner = native.gateway, object()
    browser._gateway_now_ms = lambda: clock.wall
    monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", KEY.hex())
    monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", ORIGIN)
    monkeypatch.setattr(production, "configuration", lambda: SimpleNamespace(model_dump=lambda **kw: dict(pins)))
    browser._service = lambda: native
    @contextmanager
    def observations(value):
        delayed("observation")
        yield
        delayed("exit")
    browser._observations = observations
    return SimpleNamespace(core=core, request=requested, clock=clock, advance=advance, delays=delays,
        records=records, commands=commands, account=account, context=context, native=native, browser=browser,
        core_replies=core_replies, pins=pins, tx=Tx, session_overrides=session_overrides,
        read_delays=read_delays)


def wire(case, *, operation="challenge", body=None, **changes):
    raw = json.dumps(body if body is not None else {"pairing_id": case.request["pairing_id"]}).encode()
    data = {"version": 1, "issuer": "hirewiz_bff_v1", "audience": "hirewiz:browser-pairing-candidate",
        "request_id": str(uuid4()), "method": "POST", "path": f"/api/v1/browser-pairing/candidate/{operation}",
        "origin": ORIGIN, "body_sha256": hashlib.sha256(raw).hexdigest(), "issued_at_ms": case.clock.wall,
        "expires_at_ms": case.clock.wall + 5000, "context": case.context, **changes}
    payload = json.dumps(data, separators=(",", ":")).encode()
    return raw, base64.urlsafe_b64encode(payload).rstrip(b"=").decode(), hmac.new(KEY,
        b"hirewiz.browser-gateway.v1\n" + payload, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("phase", ["observation", "scoped", "fence", "control", "identity", "command", "exit"])
@pytest.mark.parametrize("delay", [4999, 5001])
def test_admitted_gateway_survives_each_bounded_native_phase_without_extending_review(case, phase, delay):
    raw, assertion, signature = wire(case)
    case.delays[phase] = delay
    result = case.browser.candidate("challenge", raw, assertion, signature)
    assert result["operation"] == "confirm_pairing" and result["pairing_id"] == case.request["pairing_id"]
    assert len(case.records) == 1 and case.commands == ["candidate_challenge"]
    assert next(iter(case.records.values())) == {"expires_at_ms": NOW + 5000 + transport.PROCESSING_MS}
    assert result["expires_at_ms"] <= case.request["expires_at_ms"]
    assert result["expires_at_ms"] <= case.core.now + 60_000


@pytest.mark.parametrize("phase", ["observation", "scoped", "fence", "control", "identity", "command", "exit"])
def test_processing_lease_over_30_seconds_never_returns_a_grant(case, phase):
    raw, assertion, signature = wire(case)
    case.delays[phase] = 30_001
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)


@pytest.mark.parametrize("change", ["old", "future", "wall_rollback", "monotonic_rollback", "wall_only", "mono_only"])
def test_old_future_rollback_or_either_processing_deadline_is_closed(case, change):
    raw, assertion, signature = wire(case,
        **({"issued_at_ms": NOW + 1, "expires_at_ms": NOW + 5001} if change == "future" else {}))
    if change == "old":
        case.advance(5000)
        with pytest.raises(GuardDenied):
            case.native.admit_candidate("challenge", raw, assertion, signature)
        return
    if change == "future":
        with pytest.raises(GuardDenied):
            case.native.admit_candidate("challenge", raw, assertion, signature)
        return
    admission = case.native.admit_candidate("challenge", raw, assertion, signature)
    if change == "wall_rollback":
        case.clock.wall -= 1
    elif change == "monotonic_rollback":
        case.clock.mono -= .001
    elif change == "wall_only":
        case.clock.wall += 30_000
    else:
        case.clock.mono += 30
    with pytest.raises(GuardDenied):
        case.native.candidate_admitted("challenge", raw, admission)
    assert not case.records and not case.commands


def test_receipt_is_opaque_verifier_owner_body_operation_and_once_bound(case):
    raw, assertion, signature = wire(case)
    with pytest.raises(TypeError):
        VerifiedGatewayAdmission()
    for forged in (True, object.__new__(VerifiedGatewayAdmission)):
        with pytest.raises(GuardDenied):
            case.native.candidate_admitted("challenge", raw, forged)
    for change in ("body", "operation", "owner", "verifier"):
        admission = case.native.admit_candidate("challenge", raw, assertion, signature)
        target = case.native
        if change in {"owner", "verifier"}:
            target = object.__new__(NativeBrowserPairing)
            target.coordinator = case.native.coordinator
            target.gateway = case.native.gateway if change == "owner" else GatewayVerifier(key=KEY, issuer="hirewiz_bff_v1", origin=ORIGIN)
        with pytest.raises(GuardDenied):
            target.candidate_admitted("confirm" if change == "operation" else "challenge",
                b"{}" if change == "body" else raw, admission)
    admission = case.native.admit_candidate("challenge", raw, assertion, signature)
    case.native.candidate_admitted("challenge", raw, admission)
    with pytest.raises(GuardDenied):
        case.native.candidate_admitted("challenge", raw, admission)
    assert len(case.records) == 1


def test_receipt_cannot_cross_worker_thread_and_same_assertion_nonce_is_atomic(case):
    raw, assertion, signature = wire(case)
    admission = case.native.admit_candidate("challenge", raw, assertion, signature)
    def other_thread():
        with pytest.raises(GuardDenied):
            case.native.candidate_admitted("challenge", raw, admission)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(other_thread).result(timeout=2)
    def concurrent():
        try:
            case.native.candidate("challenge", raw, assertion, signature)
            return "success"
        except GuardDenied:
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: concurrent(), range(2)))
    assert sorted(results) == ["denied", "success"]
    assert len(case.records) == 1 and len(case.commands) == 1


@pytest.mark.parametrize("change", ["disabled", "session_expired_during_reads", "disabled_during_reads", "wrong_session"])
def test_admission_never_substitutes_for_current_identity(case, change):
    raw, assertion, signature = wire(case)
    if change == "disabled":
        case.account["active"] = False
    elif change == "session_expired_during_reads":
        case.account["expires"] = NOW + 1000
        case.delays["identity"] = 5001
    elif change == "disabled_during_reads":
        case.delays["identity"] = 1
        case.account["disable_after_read"] = True
    else:
        case.context["session_id"] = str(uuid4())
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    assert not case.commands and not case.records


def test_actual_pending_pairing_expiry_remains_2_minutes(case):
    case.advance(110_000)
    raw, assertion, signature = wire(case)
    case.delays["scoped"] = 10_001
    with pytest.raises(GuardDenied, match="Pairing request expired"):
        case.browser.candidate("challenge", raw, assertion, signature)


@pytest.mark.parametrize("delay", [4999, 5001])
def test_cold_composition_is_inside_same_processing_lease(case, delay):
    raw, assertion, signature = wire(case)
    first = True
    def cold_service():
        nonlocal first
        if first:
            case.advance(delay)
            first = False
        return case.native
    case.browser._service = cold_service
    result = case.browser.candidate("challenge", raw, assertion, signature)
    assert result["operation"] == "confirm_pairing" and len(case.records) == 1


@pytest.mark.parametrize("change", ["expired", "future", "signature"])
def test_invalid_incoming_assertion_never_constructs_native_service(case, change):
    raw, assertion, signature = wire(case,
        **({"issued_at_ms": NOW + 1, "expires_at_ms": NOW + 5001} if change == "future" else {}))
    if change == "expired":
        case.advance(5000)
    elif change == "signature":
        signature = "0" * 64
    case.browser._service = lambda: pytest.fail("invalid gateway input constructed native service")
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    assert not case.records and not case.commands and not case.native.gateway._admissions


@pytest.mark.parametrize("failure", ["expired", "configuration", "key", "origin", "verifier"])
def test_expired_or_changed_composition_never_binds_authority(case, monkeypatch, failure):
    raw, assertion, signature = wire(case)
    def changed_service():
        case.advance(30_001 if failure == "expired" else 5001)
        if failure == "configuration":
            case.pins["synthetic_configuration"] = "changed"
        elif failure == "key":
            monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", "ab" * 32)
        elif failure == "origin":
            monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", "https://other.example.invalid")
        elif failure == "verifier":
            foreign = object.__new__(NativeBrowserPairing)
            foreign.gateway = GatewayVerifier(key=KEY, issuer="hirewiz_bff_v1", origin=ORIGIN)
            return foreign
        return case.native
    case.browser._service = changed_service
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    assert not case.records and not case.commands and not case.native.gateway._admissions


@pytest.mark.parametrize("failure", ["configuration", "key", "origin"])
def test_configuration_drift_during_observation_exit_refuses_export(case, monkeypatch, failure):
    raw, assertion, signature = wire(case)
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
            monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", "ab" * 32)
        else:
            monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", "https://changed.example.invalid")
    case.browser._observations = changed_exit
    with pytest.raises(GuardDenied, match="configuration changed"):
        case.browser.candidate("challenge", raw, assertion, signature)
    # A prior internal command is not replayed/adopted after output is withheld.
    assert len(case.records) == 1 and case.commands == ["candidate_challenge"]
    assert not case.native.gateway._admissions


def test_construction_slot_can_bind_only_once_to_exact_native_verifier(case):
    raw, assertion, signature = wire(case)
    gateway, slot = case.native.gateway, object()
    path = "/api/v1/browser-pairing/candidate/challenge"
    admission = gateway.admit_composition(assertion, signature, owner=slot, path=path, raw=raw, now_ms=NOW)
    with pytest.raises(GuardDenied):
        gateway.bind_composed(admission, construction_owner=object(), native=case.native, path=path, raw=raw, now_ms=NOW)
    gateway.bind_composed(admission, construction_owner=slot, native=case.native, path=path, raw=raw, now_ms=NOW)
    with pytest.raises(GuardDenied):
        gateway.bind_composed(admission, construction_owner=slot, native=case.native, path=path, raw=raw, now_ms=NOW)
    assert case.native.candidate_admitted("challenge", raw, admission)["operation"] == "confirm_pairing"


def test_existing_public_verifier_freshness_semantics_remain_unchanged(case):
    raw, assertion, signature = wire(case)
    path = "/api/v1/browser-pairing/candidate/challenge"
    assert case.native.gateway.verify(assertion, signature, path=path, raw=raw, now_ms=NOW)
    with pytest.raises(GuardDenied):
        case.native.gateway.verify(assertion, signature, path=path, raw=raw, now_ms=NOW + 5000)


def test_admitted_processing_never_allows_an_expired_new_http_entry(case):
    raw, assertion, signature = wire(case)
    admission = case.native.admit_candidate("challenge", raw, assertion, signature)
    case.advance(5001)
    case.native.candidate_admitted("challenge", raw, admission)
    with pytest.raises(GuardDenied):
        case.native.candidate("challenge", raw, assertion, signature)
    assert len(case.records) == 1


def test_wall_high_water_mark_rejects_rollback_inside_lease(case):
    raw, assertion, signature = wire(case)
    admission = case.native.admit_candidate("challenge", raw, assertion, signature)
    path = "/api/v1/browser-pairing/candidate/challenge"
    case.native.gateway.admitted(admission, owner=case.native, path=path, raw=raw, now_ms=case.clock.wall, start=True)
    case.advance(5001)
    case.native.gateway.admitted(admission, owner=case.native, path=path, raw=raw, now_ms=case.clock.wall)
    case.clock.wall -= 1
    with pytest.raises(GuardDenied):
        case.native.gateway.admitted(admission, owner=case.native, path=path, raw=raw, now_ms=case.clock.wall)


def test_receipt_cannot_cross_async_task(case):
    raw, assertion, signature = wire(case)
    async def issued():
        return case.native.admit_candidate("challenge", raw, assertion, signature)
    async def other():
        admission = await asyncio.create_task(issued())
        with pytest.raises(GuardDenied):
            case.native.candidate_admitted("challenge", raw, admission)
    asyncio.run(other())
    assert not case.records


def test_actual_human_review_expiry_still_denies_fresh_candidate_assertion(case):
    raw, assertion, signature = wire(case)
    case.delays["scoped"] = 5001
    case.browser.candidate("challenge", raw, assertion, signature)
    challenge = case.core_replies[-1]
    case.core.now = challenge["payload"]["expires_at_ms"]
    with pytest.raises(GuardDenied):
        case.core.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"],
            case.core.assertion(challenge))


@pytest.mark.parametrize("operation", ["confirm", "revoke"])
def test_gateway_receipt_cannot_skip_existing_password_boundary(case, operation):
    body = {"operation": "confirm_pairing" if operation == "confirm" else "revoke_device"}
    raw, assertion, signature = wire(case, operation=operation, body=body)
    with pytest.raises(GuardDenied, match="password boundary denied"):
        case.browser.candidate(operation, raw, assertion, signature)
    assert not case.commands


def test_session_expiry_during_second_identity_resolution_refuses_before_nonce(case):
    raw, assertion, signature = wire(case)
    case.account["expires"] = NOW + 1000
    original, calls = case.native.identity.resolve, []
    def delayed_identity(*args):
        # The second underlying BufferedTransaction read is normally cached;
        # this is scheduling/CPU movement, not a claim of physical read IO.
        case.delays["identity"] = 1 if not calls else 1000
        calls.append(True)
        return original(*args)
    case.native.identity.resolve = delayed_identity
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    assert len(calls) == 2 and not case.records and not case.commands


@pytest.mark.parametrize("phase", ["commit", "fence", "command", "reply", "exit"])
def test_session_expiry_after_validated_identity_never_exports_or_erases_nonce(case, phase):
    raw, assertion, signature = wire(case)
    case.account["expires"] = NOW + 1000
    if phase in {"commit", "fence"}:
        original = case.native.coordinator.registry.run
        def completed(*args, **kwargs):
            result = original(*args, **kwargs)
            if phase == "commit":
                case.advance(1001)
            else:
                case.delays["fence"] = 1001
            return result
        case.native.coordinator.registry.run = completed
    elif phase == "reply":
        original_reply = case.native._candidate_reply
        def replied(value):
            result = original_reply(value)
            case.advance(1001)
            return result
        case.native._candidate_reply = replied
    else:
        case.delays[phase] = 1001
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    expected_commands = [] if phase in {"commit", "fence"} else ["candidate_challenge"]
    assert case.commands == expected_commands and len(case.records) == 1
    assert next(iter(case.records.values())) == {"expires_at_ms": NOW + 35_000}
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    assert case.commands == expected_commands and len(case.records) == 1


@pytest.mark.parametrize("failure", ["future", "expired", "session", "binding", "subject", "principal", "generation", "method"])
def test_session_snapshot_must_match_current_resolved_identity_and_original_time(case, failure):
    raw, assertion, signature = wire(case)
    if failure == "future":
        case.account["issued"] = NOW + 1
    elif failure == "expired":
        case.account["expires"] = NOW
    elif failure in {"session", "binding", "subject"}:
        field = {"session": "session_id", "binding": "account_binding_id", "subject": "subject_uuid"}[failure]
        case.session_overrides[field] = str(uuid4())
    elif failure == "principal":
        case.session_overrides["principal_sha256"] = "e" * 64
    elif failure == "generation":
        case.session_overrides["auth_generation"] = 2
    else:
        case.session_overrides.update(authentication_method="google", verified_proof_sha256="e" * 64)
    with pytest.raises(GuardDenied):
        case.browser.candidate("challenge", raw, assertion, signature)
    assert not case.commands and not case.records
