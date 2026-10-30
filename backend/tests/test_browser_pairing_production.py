"""Actual SDK-intercepted normal enrollment/pairing path; no provider calls."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import google.auth
import pytest
from backend.tests.fixtures.pairing_authority import jwk, sign
from cryptography.hazmat.primitives.asymmetric import ec
from test_candidate_production_factory import NOW, PASSWORD, configured  # noqa: F401
from test_pairing_kms import native as kms  # noqa: F401

from app.domains.recovery.browser_pairing import production_browser_pairing
from app.domains.recovery.contracts import canonical
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows

EXTENSION = "pnjmegppacleipajnifjmbcopjjffklb"
REVISION = "companion_0_5_0"
SECRET = "a" * 64
ORIGIN = "https://candidate.example.test"


@pytest.fixture
def pairing(configured, kms, monkeypatch):  # noqa: F811 -- shared intercepted fixture ports
    c = configured
    value = {**c.config.model_dump(mode="json"), "assertion_issuer":"candidate_password_v1",
        "assertion_key": {"version_name":kms.pin.version_name, "endpoint":kms.pin.endpoint,
            "protection_level":kms.pin.protection_level, "public_key":kms.pin.public_key},
        "claim_key": {"version_name":kms.pin.version_name, "endpoint":kms.pin.endpoint,
            "protection_level":kms.pin.protection_level, "public_key":kms.pin.public_key},
        "extension_id":EXTENSION, "executor_revision":REVISION}
    monkeypatch.setenv("BROWSER_PAIRING_NATIVE_ENABLED", "true")
    monkeypatch.setenv("BROWSER_PAIRING_NATIVE_CONFIG", canonical(value))
    monkeypatch.setenv("BROWSER_PAIRING_GATEWAY_SECRET", SECRET)
    monkeypatch.setenv("BROWSER_PAIRING_WEBSITE_ORIGIN", ORIGIN)
    def credentials(**kwargs):
        assert not c.sql_active and not c.native.views
        assert kwargs == {"scopes":["https://www.googleapis.com/auth/cloudkms"]}
        return SimpleNamespace(valid=True, token="fixture-token",
            expiry=datetime.now(UTC)+timedelta(minutes=5)), "hirewiz-local-authority"
    monkeypatch.setattr(google.auth, "default", credentials)
    c.factory_config = value
    c.browser = production_browser_pairing()
    c.private = ec.generate_private_key(ec.SECP256R1())
    c.key = jwk(c.private)
    c.kms = kms
    c.native.seed(c.execution_registry, "pairing_extensions", f"{EXTENSION}:{REVISION}",
        {"extension_id":EXTENSION, "executor_revision":REVISION, "protocol_version":2, "active":True})
    return c


def device(c, operation, value):
    return c.browser.device(operation, canonical(value).encode(), f"chrome-extension://{EXTENSION}")


def transport(c, operation, identity=None):
    value = {"protocol_version":2, "operation":operation, "request_id":str(uuid4()), "issued_at_ms":NOW}
    if identity is not None:
        value["identity"] = identity
    else:
        value.update(key=c.key, extension_id=EXTENSION, revision=REVISION)
    return {**value,"signature":sign(c.private, "transport-request", value)}


def candidate(c, context, operation, body):
    raw = canonical(body).encode()
    envelope = {"version":1,"issuer":"hirewiz_bff_v1", "audience":"hirewiz:browser-pairing-candidate",
        "request_id":str(uuid4()),"method":"POST","path":f"/api/v1/browser-pairing/candidate/{operation}",
        "origin":ORIGIN,"body_sha256":hashlib.sha256(raw).hexdigest(),"issued_at_ms":NOW,"expires_at_ms":NOW+5000,
        "context":context.model_dump(mode="json")}
    encoded = canonical(envelope).encode()
    mac = hmac.new(bytes.fromhex(SECRET), b"hirewiz.browser-gateway.v1\n"+encoded, hashlib.sha256).hexdigest()
    return c.browser.candidate(operation,raw,base64.urlsafe_b64encode(encoded).rstrip(b"=").decode(),mac)


def start(c, *, google=False):
    uid = c.accounts.register(email=f"candidate-{uuid4()}@example.test", password=PASSWORD, policy_version="2026-10-08")
    with c.accounts.sessions() as db:
        from app.models import User
        email = db.get(User, uid).email
    context = c.accounts.login(email=email,password=PASSWORD).context
    if google:
        from app.domains.candidate_accounts.service import revision_from_row
        from app.models import CandidatePasswordAccount, User
        with c.accounts.sessions() as db:
            revision = revision_from_row(db.get(CandidatePasswordAccount,uid),db.get(User,uid).password_hash)
        context = c.accounts.retention.issue_oauth(revision, principal_sha256=revision.principal_sha256,
            verified_proof_sha256="e"*64, expires_at_ms=NOW+300_000)
        assert context is not None and context.authentication_method == "google"
    prepared = device(c,"prepare",transport(c,"prepare_request"))
    device(c,"create",{"pairing_id":prepared["request"]["pairing_id"],"nonce":prepared["nonce"],
        "signature":sign(c.private,"request",prepared["request"])})
    challenge = candidate(c,context,"challenge",{"pairing_id":prepared["request"]["pairing_id"]})
    return context, prepared, challenge


def confirm(c,context,challenge):
    return candidate(c,context,"confirm",{"protocol_version":2,"operation":"confirm_pairing","method":"password_reauth",
        "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":PASSWORD,"confirmed":True})


def complete(c,prepared):
    pairing_id = prepared["request"]["pairing_id"]
    challenge = device(c,"challenge",transport(c,"device_challenge",pairing_id))
    return device(c,"complete",{"challenge_id":challenge["payload"]["challenge_id"],"nonce":challenge["nonce"],
        "signature":sign(c.private,"device-challenge",challenge["payload"])})


def rows(c):
    return {(json.loads(raw)["namespace"],json.loads(raw)["key"]):json.loads(raw)["value"]
        for path,(raw,_) in c.native.data.items() if path.startswith(c.protected.database+"/")}


def test_genuine_registration_pairing_protected_device_no_operator_grant(pairing):
    c = pairing
    context, prepared, challenge = start(c)
    assert confirm(c,context,challenge)["status"] == "CANDIDATE_CONFIRMED"
    result = complete(c,prepared)
    assert result["status"] == "COMPLETED"
    claim = result["device_claim"]["payload"]
    all_rows = rows(c)
    registered = all_rows["v3_submit_devices",claim["device_id"]]
    assert registered["source"] == "native_pairing"
    assert registered["grant"]["context"]["candidate_id"] == context.candidate_id
    assert registered["grant"]["key_sha256"] == claim["key_sha256"]
    assert "grant_pin" not in registered
    assert len([key for key in all_rows if key[0] == "v3_pairing_completions"]) == 1
    validate_scoped_rows(all_rows)
    assert len(c.kms.calls) == 4
    assert device(c, "inspect-key", transport(c, "key_status"))["status"] == "CLAIMED"


@pytest.mark.parametrize("phase", ["last_identity_read", "nonce_commit", "command", "observation_exit"])
def test_registered_native_session_expiry_is_retained_through_final_export(pairing, monkeypatch, phase):
    """Actual registered session/native adapters; SDK network remains intercepted."""
    from app.domains.candidate_accounts import production
    from app.domains.candidate_accounts.service import revision_from_row
    from app.domains.recovery.gcp_buffer import BufferedTransaction
    from app.domains.recovery.store import GuardDenied
    from app.models import CandidatePasswordAccount, User

    c = pairing
    context, prepared, _ = start(c)
    with c.accounts.sessions() as db:
        revision = revision_from_row(db.get(CandidatePasswordAccount, context.candidate_id),
            db.get(User, context.candidate_id).password_hash)
    short = c.accounts.retention.issue(revision, expires_at_ms=NOW + 1000)
    assert short is not None
    native = c.browser._service()
    # These facts came from the original enrollment/session writers and their
    # actual SDK-intercepted ACK flow, not a fabricated resolver return value.
    session = c.execution_registry.read("pairing_web_sessions", str(short.session_id))
    assert session["expires_at_ms"] == NOW + 1000
    clock, fired = [NOW], []
    monkeypatch.setattr(production.time, "time_ns", lambda: clock[0] * 1_000_000)
    def expire():
        if not fired:
            fired.append(True)
            clock[0] = NOW + 1001
    if phase == "last_identity_read":
        path = BufferedTransaction(c.execution_registry.rpc, b"synthetic-path-only").path("pairing_sessions", str(short.session_id))
        sdk_read = c.native.sdk.client.transport.batch_get_documents
        original_read = sdk_read.side_effect
        def delayed_read(request, **kwargs):
            result = original_read(request, **kwargs)
            if path in request.documents:
                expire()
            return result
        monkeypatch.setattr(sdk_read, "side_effect", delayed_read)
    elif phase == "nonce_commit":
        original_run = native.coordinator.registry.run
        def committed(*args, **kwargs):
            before = len(c.native.calls)
            result = original_run(*args, **kwargs)
            if any("browser_gateway_requests" in names for names in c.native.calls[before:]):
                expire()
            return result
        monkeypatch.setattr(native.coordinator.registry, "run", committed)
    elif phase == "command":
        original_execute = native._execute
        def completed(*args, **kwargs):
            result = original_execute(*args, **kwargs)
            expire()
            return result
        monkeypatch.setattr(native, "_execute", completed)
    else:
        original_scope = c.browser._observations
        @contextmanager
        def exited(value):
            with original_scope(value):
                yield
            expire()
        monkeypatch.setattr(c.browser, "_observations", exited)
    before_nonce = {path for path, (raw, _) in c.native.data.items()
        if json.loads(raw)["namespace"] == "browser_gateway_requests"}
    with pytest.raises(GuardDenied):
        candidate(c, short, "challenge", {"pairing_id": prepared["request"]["pairing_id"]})
    after_nonce = {path for path, (raw, _) in c.native.data.items()
        if json.loads(raw)["namespace"] == "browser_gateway_requests"}
    assert fired and len(after_nonce - before_nonce) == (0 if phase == "last_identity_read" else 1)
    assert not c.native.views


def test_factory_is_structural_and_resource_mismatch_closed(pairing,monkeypatch):
    c = pairing
    assert all(client.call_count == 0 for client in c.clients)
    production_browser_pairing()
    assert all(client.call_count == 0 for client in c.clients)
    changed = dict(c.factory_config)
    changed["epoch_generation"] = 2
    monkeypatch.setenv("BROWSER_PAIRING_NATIVE_CONFIG",canonical(changed))
    from app.domains.recovery.store import GuardUnavailable
    with pytest.raises(GuardUnavailable):
        production_browser_pairing()
    assert all(client.call_count == 0 for client in c.clients)


def test_wrong_password_never_begins_or_signs(pairing):
    c = pairing
    context,_,challenge = start(c)
    from app.domains.recovery.store import GuardDenied
    with pytest.raises(GuardDenied):
        candidate(c,context,"confirm",{"protocol_version":2,"operation":"confirm_pairing",
            "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":"wrong", "confirmed":True})
    assert not c.kms.calls
    assert not any(key[0] == "v3_begins" for key in rows(c))


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_protected_signing_begin_never_signs_or_replays(pairing,persist):
    c = pairing
    context,_,challenge = start(c)
    def fault(names):
        if "v3_begins" in names:
            return "persist_loss" if persist else "no_persist_loss"
    c.native.fault = fault
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    with pytest.raises(GuardUnavailable):
        confirm(c,context,challenge)
    assert not c.kms.calls
    c.native.fault = None
    if persist:
        with pytest.raises((GuardUnavailable,GuardDenied)):
            confirm(c,context,challenge)
        assert not c.kms.calls


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_native_completion_withholds_protected_activation_and_claim(pairing,persist):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    c.native.fault = lambda names: ("persist_loss" if persist else "no_persist_loss") if "devices" in names else None
    outcome = complete(c,prepared)
    assert outcome["status"] == "UNKNOWN"
    assert not any(key[0] == "v3_submit_devices" for key in rows(c))
    assert len(c.kms.calls) == 2
    c.native.fault = None
    assert not c.browser._service().coordinator._owned_pairing
    assert device(c, "inspect-key", transport(c, "key_status"))["status"] == ("CLAIMED" if persist else "UNCLAIMED")


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_protected_activation_never_exports_or_adopts(pairing,persist):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    c.native.fault = lambda names: ("persist_loss" if persist else "no_persist_loss") if "v3_submit_devices" in names else None
    from app.domains.recovery.store import GuardUnavailable
    with pytest.raises(GuardUnavailable):
        complete(c,prepared)
    assert not c.browser._service().coordinator._owned_pairing
    c.native.fault = None
    assert device(c, "inspect-key", transport(c, "key_status"))["status"] == "CLAIMED"
    if persist:
        validate_scoped_rows(rows(c))
    result = device(c,"status",transport(c,"status",prepared["request"]["pairing_id"]))
    assert result["status"] == "COMPLETED"
    from app.domains.recovery.gcp_pairing_contracts import PairingExecution
    from app.domains.recovery.store import GuardDenied
    forged = PairingExecution(status="COMMITTED",operation_id=uuid4(),intent_sha256="a"*64)
    with pytest.raises(GuardDenied):
        c.browser._service().coordinator.take_owned_ack(forged)


def test_normal_revocation_denies_protected_device_and_preserves_full_census(pairing):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    completed = complete(c,prepared)
    claim = completed["device_claim"]["payload"]
    challenge = candidate(c,context,"revoke-challenge",{"device_id":claim["device_id"]})
    result = candidate(c,context,"revoke",{"protocol_version":2,"operation":"revoke_device",
        "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":PASSWORD,"confirmed":True})
    assert result["status"] == "REVOKED"
    all_rows = rows(c)
    assert ("v3_submit_device_tombstones",claim["device_id"]) in all_rows
    assert len([key for key in all_rows if key[0] == "v3_pairing_revocations"]) == 1
    validate_scoped_rows(all_rows)
    from app.domains.recovery.store import GuardDenied
    with pytest.raises(GuardDenied):
        c.browser._service().paired_devices.check_claim(claim)
    assert device(c, "inspect-key", transport(c, "key_status"))["status"] == "CLAIMED"


def test_key_inspection_is_exact_signed_read_with_only_replay_consume(pairing):
    from app.domains.recovery.pairing_auth import key_fingerprint
    from app.domains.recovery.store import GuardDenied
    c = pairing
    before = set(c.native.data)
    request = transport(c, "key_status")
    result = device(c, "inspect-key", request)
    assert result == {"protocol_version": 2, "key_sha256": key_fingerprint(c.key),
        "extension_id": EXTENSION, "executor_revision": REVISION, "status": "UNCLAIMED"}
    added = [json.loads(c.native.data[path][0]) for path in set(c.native.data) - before]
    assert len(added) == 1 and added[0]["namespace"] == "browser_device_requests"
    # Retain replay consumption through the final possible bounded processing
    # lease; old public signatures still cannot enter a new HTTP invocation.
    assert added[0]["value"] == {"expires_at_ms": NOW + 35_000}
    assert c.native.calls == [{"browser_device_requests"}]
    assert not c.kms.calls and not c.native.views
    assert not c.browser._service().coordinator._issued
    with pytest.raises(GuardDenied):
        device(c, "inspect-key", request)


def test_key_inspection_http_reply_and_authority_failure_are_bounded(pairing):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers.v1.browser_pairing import get_browser_pairing, router
    c = pairing
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_browser_pairing] = lambda: c.browser
    headers = {"Content-Type": "application/json", "Origin": f"chrome-extension://{EXTENSION}"}
    with TestClient(app) as client:
        response = client.post("/api/v1/browser-pairing/device/inspect-key",
            content=canonical(transport(c, "key_status")).encode(), headers=headers)
        assert response.status_code == 200 and response.json()["status"] == "UNCLAIMED"
        assert set(response.json()) == {"protocol_version", "key_sha256", "extension_id", "executor_revision", "status"}
        c.native.seed(c.protected_registry, "v3_root", "current", {"invalid": "synthetic-private-value"})
        response = client.post("/api/v1/browser-pairing/device/inspect-key",
            content=canonical(transport(c, "key_status")).encode(), headers=headers)
        assert response.status_code == 503
        assert response.json() == {"detail": "Browser pairing is currently unavailable"}
        assert "no-store" in response.headers["cache-control"]


def test_key_inspection_does_not_adopt_or_replay_an_unproven_allocation(pairing):
    c = pairing
    prepared = device(c, "prepare", transport(c, "prepare_request"))
    before = len(c.native.calls)
    assert device(c, "inspect-key", transport(c, "key_status"))["status"] == "UNCLAIMED"
    assert c.execution_registry.read("pairing_requests", prepared["request"]["pairing_id"])["state"] == "UNPROVEN"
    assert c.native.calls[before:] == [{"browser_device_requests"}]
    assert not c.kms.calls


def test_key_inspection_rechecks_protected_absence_after_execution_read(pairing, monkeypatch):
    from app.domains.recovery.gcp_buffer import BufferedTransaction
    from app.domains.recovery.pairing_auth import key_fingerprint
    from app.domains.recovery.store import GuardUnavailable
    c = pairing
    key_sha = key_fingerprint(c.key)
    path = BufferedTransaction(c.execution_registry.rpc, b"test").path("pairing_key_owners", key_sha)
    original = c.native.read
    def changed(request, **kwargs):
        result = original(request, **kwargs)
        if path in request.documents:
            c.native.seed(c.protected_registry, "v3_submit_key_owners", key_sha,
                {"device_id": str(uuid4()), "subject_uuid": str(uuid4()), "registration_operation": str(uuid4())})
        return result
    monkeypatch.setattr(c.native.sdk.client.transport.batch_get_documents, "side_effect", changed)
    with pytest.raises(GuardUnavailable):
        device(c, "inspect-key", transport(c, "key_status"))
    assert c.native.calls == [{"browser_device_requests"}]


@pytest.mark.parametrize("fault", ["origin", "signature", "key", "extension", "revision", "expired", "version", "extra", "private_key"])
def test_key_inspection_rejects_wrong_device_proof_and_freshness(pairing, fault):
    from app.domains.recovery.store import GuardDenied
    c = pairing
    request = transport(c, "key_status")
    origin = f"chrome-extension://{EXTENSION}"
    if fault == "origin":
        origin = "chrome-extension://" + "a" * 32
    elif fault == "signature":
        request["signature"] = "A" * 86
    elif fault == "key":
        request["key"] = jwk(ec.generate_private_key(ec.SECP256R1()))
    else:
        request.pop("signature")
        if fault == "extension":
            request["extension_id"] = "a" * 32
        elif fault == "revision":
            request["revision"] = "unapproved_revision"
        elif fault == "expired":
            request["issued_at_ms"] = NOW - 5000
        elif fault == "version":
            request["protocol_version"] = True
        elif fault == "extra":
            request["device_id"] = str(uuid4())
        else:
            request["key"] = {**request["key"], "d": "A" * 43}
        request["signature"] = sign(c.private, "transport-request", request)
    with pytest.raises((GuardDenied, ValueError)):
        c.browser.device("inspect-key", canonical(request).encode(), origin)
    assert not c.kms.calls and not c.native.calls


@pytest.mark.parametrize("fault", ["execution_uid", "protected_uid", "execution_control", "protected_root", "approved_extension", "witness", "resource", "corrupt_owner", "permission", "timeout"])
def test_key_inspection_never_reports_absence_when_authority_is_unavailable(pairing, monkeypatch, fault):
    from google.api_core.exceptions import DeadlineExceeded, PermissionDenied

    from app.domains.recovery.gcp_buffer import BufferedTransaction
    from app.domains.recovery.pairing_auth import key_fingerprint
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    key_sha = key_fingerprint(c.key)
    if fault == "execution_uid":
        c.native.identities[c.execution.database] = str(uuid4())
    elif fault == "protected_uid":
        c.native.identities[c.protected.database] = str(uuid4())
    elif fault == "execution_control":
        c.native.seed(c.execution_registry, "pairing_control", "current", {})
    elif fault == "protected_root":
        c.native.seed(c.protected_registry, "v3_root", "current", {
            "pin": c.config.publication_resource.pin.model_dump(mode="json"), "phase": "CLOSED", "close_id": str(uuid4())})
    elif fault == "approved_extension":
        c.native.seed(c.execution_registry, "pairing_extensions", f"{EXTENSION}:{REVISION}", {})
    elif fault == "witness":
        c.objects[c.config.execution_witness_pin.path] = b"{}", "999"
    elif fault == "resource":
        c.objects[c.config.publication_resource.path] = b"{}", "999"
    elif fault == "corrupt_owner":
        c.native.seed(c.protected_registry, "v3_submit_key_owners", key_sha, {"invalid": "synthetic-private-value"})
    else:
        path = BufferedTransaction(c.protected_registry.rpc, b"test").path("v3_submit_key_owners", key_sha)
        original = c.native.read
        def unavailable(request, **kwargs):
            if path in request.documents:
                raise (PermissionDenied if fault == "permission" else DeadlineExceeded)("synthetic-private-upstream")
            return original(request, **kwargs)
        monkeypatch.setattr(c.native.sdk.client.transport.batch_get_documents, "side_effect", unavailable)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        device(c, "inspect-key", transport(c, "key_status"))
    assert not c.kms.calls and not c.native.views
    assert all(names == {"browser_device_requests"} for names in c.native.calls)


def test_key_inspection_blocks_a_protected_owner_when_execution_was_restored(pairing):
    from app.domains.recovery.gcp_buffer import BufferedTransaction
    from app.domains.recovery.pairing_auth import key_fingerprint
    c = pairing
    context, prepared, challenge = start(c)
    confirm(c, context, challenge)
    complete(c, prepared)
    path = BufferedTransaction(c.execution_registry.rpc, b"test").path("pairing_key_owners", key_fingerprint(c.key))
    del c.native.data[path]
    assert device(c, "inspect-key", transport(c, "key_status"))["status"] == "CLAIMED"


def test_key_inspection_detects_disagreeing_retained_owners(pairing):
    from app.domains.recovery.pairing_auth import key_fingerprint
    from app.domains.recovery.store import GuardUnavailable
    c = pairing
    context, prepared, challenge = start(c)
    confirm(c, context, challenge)
    complete(c, prepared)
    c.native.seed(c.execution_registry, "pairing_key_owners", key_fingerprint(c.key),
                  {"device_id": str(uuid4()), "subject_uuid": str(uuid4())})
    with pytest.raises(GuardUnavailable):
        device(c, "inspect-key", transport(c, "key_status"))


def test_paired_device_enters_real_submit_authority_and_sql_projection(pairing,monkeypatch):
    from test_browser_submit_native import binding

    from app.domains.browser_assistance.models import BrowserAssistanceDevice
    from app.domains.browser_assistance.service import digest
    from app.domains.recovery.browser_submit import production_submit_authority
    from app.domains.recovery.gcp_submit_contracts import SubmitDeviceGrant
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    grant = SubmitDeviceGrant.model_validate(rows(c)["v3_submit_devices",claim["device_id"]]["grant"])
    with c.accounts.sessions() as db:
        row = db.get(BrowserAssistanceDevice,claim["device_id"])
        assert row.state == "active" and row.user_id == context.candidate_id
        assert row.key_sha256 == digest(grant.public_key) and row.key_sha256 != grant.key_sha256
    config = {**c.config.model_dump(mode="json"),"device_enrollment":"paired","executor_revision":REVISION}
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_ENABLED","true")
    monkeypatch.setenv("BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION",REVISION)
    monkeypatch.setenv("BROWSER_SUBMIT_NATIVE_CONFIG",canonical(config))
    authority = production_submit_authority()
    action = binding(SimpleNamespace(grant=grant),candidate_id=context.candidate_id)
    ack = authority.reserve(grant.context,action)
    assert ack is not None
    proof = authority.consume_ack(ack,action)
    assert proof["employer_receipt"] is False and proof["settlement_authorized"] is False
    from app.domains.recovery.store import GuardDenied
    with pytest.raises(GuardDenied):
        authority.consume_ack(ack,action)
    validate_scoped_rows(rows(c))


def test_denial_during_kms_signing_withholds_native_confirmation(pairing):
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,_,challenge = start(c)
    def closed(reply):
        c.native.seed(c.protected_registry,"v3_root","current", {
            "pin":c.config.publication_resource.pin.model_dump(mode="json"),"phase":"CLOSED","close_id":str(uuid4())})
    c.kms.sign_hook = closed
    with pytest.raises((GuardDenied,GuardUnavailable)):
        confirm(c,context,challenge)
    assert len(c.kms.calls) == 2
    assert not any("pairing_confirmations" in names for names in c.native.calls)


@pytest.mark.parametrize("persist",[False,True])
def test_unknown_native_revocation_keeps_sql_and_protected_device_denied(pairing,persist):
    from app.domains.browser_assistance.models import BrowserAssistanceDevice
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    challenge = candidate(c,context,"revoke-challenge",{"device_id":claim["device_id"]})
    c.native.fault = lambda names: ("persist_loss" if persist else "no_persist_loss") if "pairing_device_tombstones" in names else None
    with pytest.raises(GuardUnavailable):
        candidate(c,context,"revoke",{"protocol_version":2,"operation":"revoke_device",
            "challenge_id":challenge["challenge_id"],"nonce":challenge["nonce"],"password":PASSWORD,"confirmed":True})
    c.native.fault = None
    with c.accounts.sessions() as db:
        assert db.get(BrowserAssistanceDevice,claim["device_id"]).state == "revoked"
    assert ("v3_submit_device_tombstones",claim["device_id"]) in rows(c)
    with pytest.raises(GuardDenied):
        c.browser._service().paired_devices.check_claim(claim)
    validate_scoped_rows(rows(c))


@pytest.mark.parametrize("name,value",[("BROWSER_PAIRING_GATEWAY_SECRET","b"*64),
    ("BROWSER_PAIRING_WEBSITE_ORIGIN","https://changed.example.test")])
def test_existing_factory_refuses_rotated_gateway_pin(pairing,monkeypatch,name,value):
    from app.domains.recovery.store import GuardDenied
    c = pairing
    monkeypatch.setenv(name,value)
    with pytest.raises(GuardDenied):
        c.browser._service()
    assert all(client.call_count == 0 for client in c.clients)


def test_sql_activation_failure_withholds_claim_and_does_not_adopt(pairing,monkeypatch):
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    bridge = c.browser._service().paired_devices
    def unavailable(*args):
        raise GuardUnavailable("Synthetic SQL activation failure")
    monkeypatch.setattr(bridge,"activated",unavailable)
    with pytest.raises(GuardUnavailable):
        complete(c,prepared)
    assert len([key for key in rows(c) if key[0] == "v3_submit_devices"]) == 1
    assert not c.browser._service().coordinator._owned_pairing
    with pytest.raises(GuardDenied):
        complete(c,prepared)


def test_protected_uid_change_prevents_signing_even_with_native_session(pairing):
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,_,challenge = start(c)
    c.native.identities[c.protected.database] = str(uuid4())
    with pytest.raises((GuardDenied,GuardUnavailable)):
        confirm(c,context,challenge)
    assert not c.kms.calls


def test_confirmation_metadata_budget_keeps_fresh_transaction_fences(pairing):
    c = pairing
    context, _, challenge = start(c)
    metadata = c.native.sdk.admin.transport.get_database
    before = metadata.call_count
    assert confirm(c, context, challenge)["status"] == "CANDIDATE_CONFIRMED"
    # Actual SDK path: explicit operation entry/exit plus both sides of KMS
    # refresh both database identities. Other immutable metadata observations
    # are reused only inside this bounded operation; authority reads stay fresh.
    # Historical nested/same-pin implementations required 68/54 metadata calls.
    assert metadata.call_count - before == 8
    before = metadata.call_count
    c.accounts.validate(context, auth_generation=1)
    assert metadata.call_count - before == 4


@pytest.mark.parametrize("marker", ["attempt", "password"])
@pytest.mark.parametrize("fault", ["uid", "closure", "expiry", "marker_unavailable"])
def test_compound_marker_reads_never_export_after_dependency_loss(pairing, monkeypatch, marker, fault):
    from app.domains.recovery.gcp_closure_contracts import closure_path
    from app.domains.recovery.store import GuardDenied, GuardUnavailable

    c = pairing
    context, _, challenge = start(c)
    existing_invocations = {path: raw for path, (raw, _) in c.native.data.items()
                            if json.loads(raw)["namespace"] == "pairing_invocations"}
    coordinator = c.browser._service().coordinator
    original_group = coordinator._before_claim
    checking, fired, signing_at_loss = [], [], []

    def group(intent, claim):
        checking.append(True)
        try:
            return original_group(intent, claim)
        finally:
            checking.pop()

    monkeypatch.setattr(coordinator, "_before_claim", group)
    dependency = coordinator.attempts if marker == "attempt" else coordinator.password_signing
    original_verify = dependency.verify

    def changed(*args):
        result = original_verify(*args)
        if checking and not fired:
            fired.append(True)
            signing_at_loss.append(len(c.kms.calls))
            if fault == "uid":
                c.native.identities[c.execution.database] = str(uuid4())
            elif fault == "closure":
                c.objects[closure_path(c.execution)] = b"synthetic-close", "999"
            elif fault == "expiry":
                monkeypatch.setattr(coordinator, "now_ms", lambda: NOW + 60_000)
            else:
                raise GuardUnavailable("Synthetic marker bytes unavailable")
        return result

    monkeypatch.setattr(dependency, "verify", changed)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        confirm(c, context, challenge)
    assert fired == [True]
    execution_rows = [json.loads(raw)["namespace"] for path, (raw, _) in c.native.data.items()
                      if path.startswith(c.execution.database + "/")]
    if fault == "uid":
        # The explicit operation observation changes private-write timing:
        # internal rows may be quarantined before the final UID refusal. No
        # subsequent signature or authority may escape, and failed ownership
        # is discarded. Closure/expiry/marker failures remain immediate below.
        assert len(c.kms.calls) == signing_at_loss[0]
        assert not coordinator._password_admitted
        assert not c.browser._service().paired_devices.scoped._begins
        assert not any(key[0] == "v3_submit_devices" for key in rows(c))
    else:
        assert "pairing_confirmations" not in execution_rows
        assert {path: raw for path, (raw, _) in c.native.data.items()
                if json.loads(raw)["namespace"] == "pairing_invocations"} == existing_invocations
    assert not coordinator._owned_pairing
    assert not c.native.views


def test_compound_marker_fences_are_fresh_after_definite_abort(pairing):
    c = pairing
    context, _, challenge = start(c)
    aborted = []

    def abort_once(names):
        if "pairing_invocations" in names and not aborted:
            aborted.append(True)
            return "abort"
        return None

    c.native.fault = abort_once
    metadata = c.native.sdk.admin.transport.get_database
    before = metadata.call_count
    assert confirm(c, context, challenge)["status"] == "CANDIDATE_CONFIRMED"
    assert aborted == [True]
    # A definite abort invalidates the complete operation observation. Both
    # independent identities are freshly renewed before the next attempt.
    assert metadata.call_count - before == 10
    assert not c.native.views



def test_google_native_session_still_requires_separate_current_password_pairing(pairing):
    c = pairing
    context,prepared,challenge = start(c,google=True)
    assert context.authentication_method == "google"
    assert confirm(c,context,challenge)["status"] == "CANDIDATE_CONFIRMED"
    result = complete(c,prepared)
    assert result["status"] == "COMPLETED"
    all_rows = rows(c)
    registration = all_rows["v3_submit_devices",result["device_claim"]["payload"]["device_id"]]
    full = all_rows["v3_records",registration["operation_id"]]
    assert full["intent"]["command"]["session"]["authentication_method"] == "google"
    assert full["intent"]["command"]["evidence"]["confirmation"]["assertion"]["method"] == "password_reauth"
    validate_scoped_rows(all_rows)

@pytest.mark.parametrize('mutation',['closed','generation','absent'])
def test_paired_submit_rechecks_independent_execution_control_before_any_new_claim(pairing, monkeypatch, mutation):
    from test_browser_submit_native import binding

    from app.domains.recovery.browser_submit import production_submit_authority
    from app.domains.recovery.gcp_pairing import pairing_control_record
    from app.domains.recovery.gcp_submit_contracts import SubmitDeviceGrant
    from app.domains.recovery.store import GuardUnavailable
    c=pairing
    context,prepared,challenge=start(c)
    confirm(c,context,challenge)
    claim=complete(c,prepared)['device_claim']['payload']
    grant=SubmitDeviceGrant.model_validate(rows(c)['v3_submit_devices',claim['device_id']]['grant'])
    config={**c.config.model_dump(mode='json'),'device_enrollment':'paired','executor_revision':REVISION}
    monkeypatch.setenv('BROWSER_SUBMIT_NATIVE_ENABLED','true')
    monkeypatch.setenv('BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION',REVISION)
    monkeypatch.setenv('BROWSER_SUBMIT_NATIVE_CONFIG',canonical(config))
    authority=production_submit_authority()
    action=binding(SimpleNamespace(grant=grant),candidate_id=context.candidate_id)
    control=pairing_control_record(c.execution,1)
    if mutation=='closed':
        control['state']='CLOSED'
    if mutation=='generation':
        control['generation']=2
    if mutation=='absent':
        control={}
    c.native.seed(c.execution_registry,'pairing_control','current',control)
    before=len([key for key in rows(c) if key[0]=='v3_submit_openings'])
    with pytest.raises(GuardUnavailable):
        authority.reserve(grant.context,action)
    assert len([key for key in rows(c) if key[0]=='v3_submit_openings'])==before


@pytest.mark.parametrize("fault",["none","protected_denied","epoch_generation","execution_closed","wrong_key","missing_activation"])
def test_assistance_context_requires_fresh_independent_device_and_epoch(pairing,monkeypatch,fault):
    from app.domains.recovery.gcp_pairing import pairing_control_record
    from app.domains.recovery.store import GuardDenied, GuardUnavailable
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED","true")
    key = c.key
    if fault == "protected_denied":
        c.native.seed(c.protected_registry,"v3_submit_device_tombstones",claim["device_id"],{"denied":True})
    elif fault == "epoch_generation":
        c.native.seed(c.execution_registry,"pairing_control","current",pairing_control_record(c.execution,2))
    elif fault == "execution_closed":
        c.native.seed(c.execution_registry,"control","meta",{"state":"CLOSED"})
    elif fault == "wrong_key":
        key = jwk(ec.generate_private_key(ec.SECP256R1()))
    elif fault == "missing_activation":
        device = rows(c)["v3_submit_devices",claim["device_id"]]
        c.native.seed(c.protected_registry,"v3_records",device["operation_id"],{"missing":True})
    if fault == "none":
        actual = c.browser.assistance_context(claim["device_id"],key)
        assert actual.candidate_id == context.candidate_id and actual.account_binding_id == context.account_binding_id
    else:
        from pydantic import ValidationError
        with pytest.raises((GuardDenied,GuardUnavailable,ValidationError)):
            c.browser.assistance_context(claim["device_id"],key)


def test_native_assistance_precheck_runs_after_sql_context_is_released(pairing,monkeypatch):
    c = pairing
    context,prepared,challenge = start(c)
    confirm(c,context,challenge)
    claim = complete(c,prepared)["device_claim"]["payload"]
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED","true")
    with c.accounts.sessions() as db:
        from app.models import User
        db.get(User,context.candidate_id)
        assert c.sql_active
        db.rollback()
        assert not c.sql_active
        actual = c.browser.assistance_context(claim["device_id"],c.key)
        assert actual.candidate_id == context.candidate_id
    assert c.browser.assistance_context(claim["device_id"],c.key).candidate_id == context.candidate_id
