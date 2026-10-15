"""Actual ASGI -> native Firestore -> password/signature boundaries, synthetic identities.

The owned emulator and SQLite credential fixture use no ADC or real candidates.
Storage SDK responses are explicit synthetic generation-zero objects, not cloud proof.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Lock
from uuid import uuid4

import pytest
from backend.tests.fixtures.pairing_authority import RELEASE, jwk, sign
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import Aborted, DeadlineExceeded
from test_gcp_pairing_emulator import case as case
from test_gcp_password_reauth_emulator import PASSWORD, invalidate
from test_gcp_password_reauth_emulator import native as native

from app.domains.recovery.browser_pairing import GatewayVerifier, NativeBrowserPairing, exact_json
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.pairing_auth import verify_signature
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.routers.v1.browser_pairing import get_browser_pairing, router

PREFIX = "/api/v1/browser-pairing"
KEY = b"synthetic-dedicated-gateway-key-0000"
ORIGIN = "https://candidate.hirewiz.test"
EXTENSION = "a" * 32


def gateway(native, operation, raw, **changed):
    payload = {"version": 1, "issuer": "hirewiz_bff_v1", "audience": "hirewiz:browser-pairing-candidate",
        "request_id": str(uuid4()), "method": "POST", "path": f"{PREFIX}/candidate/{operation}",
        "origin": ORIGIN, "body_sha256": hashlib.sha256(raw).hexdigest(), "issued_at_ms": native.case.now,
        "expires_at_ms": native.case.now + 5_000, "context": native.context.model_dump(mode="json"), **changed}
    body = json.dumps(payload, separators=(",", ":")).encode()
    return {"Content-Type": "application/json", "X-Hirewiz-Gateway-Assertion": base64.urlsafe_b64encode(body).rstrip(b"=").decode(),
        "X-Hirewiz-Gateway-Signature": hmac.new(KEY, b"hirewiz.browser-gateway.v1\n" + body, hashlib.sha256).hexdigest()}


@pytest.fixture
def client(native):
    def register_extension(tx):
        key = f"{EXTENSION}:{RELEASE}"
        tx.get("pairing_extensions", key)
        tx.put("pairing_extensions", key, {"extension_id": EXTENSION,
            "executor_revision": RELEASE, "protocol_version": 2, "active": True}, immutable=True)
    native.case.registry.run(register_extension)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    service = NativeBrowserPairing(native.case.coordinator, native.service(),
        GatewayVerifier(key=KEY, issuer="hirewiz_bff_v1", origin=ORIGIN))
    app.dependency_overrides[get_browser_pairing] = lambda: service
    with TestClient(app) as client:
        yield client


def post(client, operation, data):
    return client.post(f"{PREFIX}/device/{operation}", content=json.dumps(data).encode(),
                       headers={"Content-Type": "application/json", "Origin": f"chrome-extension://{EXTENSION}"})


def candidate(client, native, operation, data, **changed):
    raw = json.dumps(data).encode()
    return client.post(f"{PREFIX}/candidate/{operation}", content=raw,
                       headers=gateway(native, operation, raw, **changed))


def prepare(client, native, records=None):
    payload = {"protocol_version": 2, "operation": "prepare_request", "key": jwk(native.case.device_key),
        "extension_id": EXTENSION, "revision": RELEASE, "request_id": str(uuid4()), "issued_at_ms": native.case.now}
    response = post(client, "prepare", {**payload, "signature": sign(native.case.device_key, "transport-request", payload)})
    assert response.status_code == 200, response.json()
    prepared = response.json()
    if records is not None:
        records.append({"channel": "device", "operation": "prepare", "value": prepared})
    request = prepared["request"]
    created = post(client, "create", {"pairing_id": request["pairing_id"], "nonce": prepared["nonce"],
        "signature": sign(native.case.device_key, "request", request)})
    assert created.status_code == 200 and created.json()["status"] == "REQUESTED"
    if records is not None:
        records.append({"channel": "device", "operation": "create", "value": created.json()})
    return request


def lookup(native, operation, identity, key=None):
    payload = {"protocol_version": 2, "operation": operation, "identity": identity,
        "request_id": str(uuid4()), "issued_at_ms": native.case.now}
    return {**payload, "signature": sign(key or native.case.device_key, "transport-request", payload)}


def confirm(client, native, request, records=None):
    response = candidate(client, native, "challenge", {"pairing_id": request["pairing_id"]})
    assert response.status_code == 200, response.json()
    challenge = response.json()
    if records is not None:
        records.append({"channel": "candidate", "operation": "challenge", "value": challenge})
    response = candidate(client, native, "confirm", {"protocol_version": 2, "operation": "confirm_pairing",
        "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"],
        "password": PASSWORD, "confirmed": True})
    assert response.status_code == 200 and response.json()["status"] == "CANDIDATE_CONFIRMED"
    if records is not None:
        records.append({"channel": "candidate", "operation": "confirm", "value": response.json()})
    return challenge


def test_http_complete_refresh_revoke_actual_native_password_and_keys(client, native):
    records = []
    request = prepare(client, native, records)
    confirm(client, native, request, records)
    response = post(client, "challenge", lookup(native, "device_challenge", request["pairing_id"]))
    assert response.status_code == 200
    challenge = response.json()
    records.append({"channel": "device", "operation": "challenge", "value": challenge})
    response = post(client, "complete", {"challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"],
        "signature": sign(native.case.device_key, "device-challenge", challenge["payload"])})
    assert response.status_code == 200 and response.json()["status"] == "COMPLETED"
    records.append({"channel": "device", "operation": "complete", "value": response.json()})
    claim = response.json()["device_claim"]
    verify_signature(jwk(native.case.claim_key), "device-claim", claim["payload"], claim["signature"])
    assert claim["payload"]["audience"] == "hirewiz:pairing-only"
    assert claim["payload"]["operation"] == "device_identity"
    assert native.case.registry.read("devices", request["device_id"])["active"] is True
    response = post(client, "refresh-challenge", lookup(native, "refresh_challenge", request["device_id"]))
    assert response.status_code == 200
    challenge = response.json()
    records.append({"channel": "device", "operation": "refresh-challenge", "value": challenge})
    refreshed = post(client, "refresh", {"challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"],
        "signature": sign(native.case.device_key, "device-challenge", challenge["payload"])})
    assert refreshed.status_code == 200 and refreshed.json()["status"] == "IDENTIFIED"
    records.append({"channel": "device", "operation": "refresh", "value": refreshed.json()})
    response = candidate(client, native, "revoke-challenge", {"device_id": request["device_id"]})
    assert response.status_code == 200
    challenge = response.json()
    records.append({"channel": "candidate", "operation": "revoke-challenge", "value": challenge})
    revoked = candidate(client, native, "revoke", {"protocol_version": 2, "operation": "revoke_device",
        "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"], "password": PASSWORD, "confirmed": True})
    assert revoked.status_code == 200 and revoked.json()["status"] == "REVOKED"
    records.append({"channel": "candidate", "operation": "revoke", "value": revoked.json()})
    assert post(client, "refresh-challenge", lookup(native, "refresh_challenge", request["device_id"])).status_code == 403
    assert not native.case.active.any()
    assert all(PASSWORD not in json.dumps(intent.model_dump(mode="json")) and native.encoded not in json.dumps(intent.model_dump(mode="json"))
               for intent in native.case.http.intents.values())
    assert len(native.signer.calls) == 2
    # Actual native outputs enter the actual TS BFF validators through an in-memory
    # pipe. No nonce, claim, password, CSRF or gateway assertion is written to disk.
    module = Path(__file__).resolve().parents[2] / "frontend/lib/browserPairingGateway.ts"
    check = subprocess.run(["node", "--experimental-strip-types", "--input-type=module", "-e",
        'import {readFileSync} from "node:fs"; const {deviceReply,candidateReply}=await import(process.argv[1]); '
        'const records=JSON.parse(readFileSync(0,"utf8")); for(const record of records){const result='
        'record.channel==="device"?deviceReply(record.operation,record.value):candidateReply(record.operation,record.value);'
        'if(JSON.stringify(result)!==JSON.stringify(record.value))throw new Error("Signed reply values changed");}'
        'const original=records.find(r=>r.operation==="complete").value;let rejected=0;'
        'for(const fault of [{key_generation:"1"},{password:"synthetic-unexpected-private-field"}]){'
        'const malformed=structuredClone(original);Object.assign(malformed.device_claim.payload,fault);'
        'try{deviceReply("complete",malformed);}catch{rejected++;}}'
        'if(rejected!==2)throw new Error("Malformed signed claim accepted");'
        'process.stdout.write(JSON.stringify({validated_records:records.length}));', module.as_uri()],
        input=json.dumps(records), text=True, capture_output=True, timeout=10, check=False)
    assert check.returncode == 0, "Native outputs failed the BFF contract validator"
    assert json.loads(check.stdout) == {"validated_records": 10}


@pytest.mark.parametrize("fault", ["body", "origin", "session", "signature", "expired", "missing"])
def test_candidate_gateway_rejects_changed_exact_request_and_retained_identity(client, native, fault):
    request = prepare(client, native)
    raw = json.dumps({"pairing_id": request["pairing_id"]}).encode()
    changes = {}
    if fault == "origin":
        changes["origin"] = "https://sibling.hirewiz.test"
    if fault == "session":
        changes["context"] = {**native.context.model_dump(mode="json"), "session_id": str(uuid4())}
    if fault == "expired":
        changes["issued_at_ms"], changes["expires_at_ms"] = native.case.now - 5_000, native.case.now
    headers = gateway(native, "challenge", raw, **changes)
    if fault == "body":
        raw += b" "
    if fault == "signature":
        headers["X-Hirewiz-Gateway-Signature"] = "0" * 64
    if fault == "missing":
        headers.pop("X-Hirewiz-Gateway-Assertion")
    response = client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers)
    assert response.status_code == 403
    assert response.json() == {"detail": "Browser pairing request was rejected"}
    assert native.signer.calls == []
    assert native.case.registry.read("pairing_requests", request["pairing_id"])["state"] == "REQUESTED"


def test_gateway_and_device_replay_are_consumed_in_native_transactions(client, native):
    request = prepare(client, native)
    raw = json.dumps({"pairing_id": request["pairing_id"]}).encode()
    headers = gateway(native, "challenge", raw)
    first = client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers)
    second = client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers)
    assert first.status_code == 200 and second.status_code == 403
    proof = lookup(native, "status", request["pairing_id"])
    assert post(client, "status", proof).status_code == 200
    assert post(client, "status", proof).status_code == 403
    assert all("signature" not in json.dumps(write.raw.decode()) for writes in native.case.commits for write in writes
               if b"browser_gateway_requests" in write.raw or b"browser_device_requests" in write.raw)


@pytest.mark.parametrize("definite_abort_budget", [False, True])
def test_concurrent_same_gateway_request_has_only_one_native_winner(client, native, monkeypatch, definite_abort_budget):
    request = prepare(client, native)
    raw = json.dumps({"pairing_id": request["pairing_id"]}).encode()
    headers = gateway(native, "challenge", raw)
    request_id = json.loads(base64.urlsafe_b64decode(headers["X-Hirewiz-Gateway-Assertion"] + "=="))["request_id"]
    before = native.case.registry.read("head", "global")["sequence"]
    unavailable, lock = [], Lock()
    consume = NativeBrowserPairing._consume
    def observed_consume(self, *args, **kwargs):
        try:
            return consume(self, *args, **kwargs)
        except GuardUnavailable as exc:
            # Record fixed classifications only. No exception text or request material
            # enters assertion output, reports or logs.
            known = {"Definite-abort retry budget exhausted": "abort_budget",
                     "Transaction retry deadline exceeded": "retry_deadline",
                     "Transaction deadline exceeded before Commit": "commit_deadline"}
            reason = known.get(str(exc))
            if reason is None and isinstance(exc.__cause__, (Aborted, DeadlineExceeded)):
                reason = "native_abort_or_deadline"
            if reason is None:
                raise AssertionError("Unclassified native consumption failure") from None
            with lock:
                unavailable.append(reason)
            raise
    monkeypatch.setattr(NativeBrowserPairing, "_consume", observed_consume)
    if definite_abort_budget:
        # Exercise a documented loser path explicitly rather than depend on emulator
        # scheduling. The winner still commits through the actual native SDK.
        native.case.registry.max_attempts = 1
        commit = native.case.rpc.commit
        aborted = []
        def one_definite_abort(transaction, writes):
            with lock:
                if not aborted and any(b"browser_gateway_requests" in write.raw for write in writes):
                    aborted.append(True)
                    raise Aborted("Owned definite abort before Commit send")
            return commit(transaction, writes)
        monkeypatch.setattr(native.case.rpc, "commit", one_definite_abort)
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers), range(2)))
    statuses = sorted(reply.status_code for reply in replies)
    assert statuses in ([200, 403], [200, 503])
    assert len(unavailable) == statuses.count(503)
    if definite_abort_budget:
        assert statuses == [200, 503] and unavailable == ["abort_budget"]
    # The race outcome is one retained consumption and one native challenge event,
    # regardless of whether the rejected loser sees replay or bounded unavailability.
    assert native.case.registry.read("browser_gateway_requests", request_id) == {"expires_at_ms": native.case.now + 5_000}
    assert native.case.registry.read("head", "global")["sequence"] == before + 1
    assert native.case.registry.read("pairing_requests", request["pairing_id"])["state"] == "REQUESTED"
    assert client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers).status_code == 403
    assert native.signer.calls == []
    for namespace in ("devices", "actors", "grants"):
        assert native.case.registry.read(namespace, request["device_id"]) is None
    assert not native.case.active.any()


def test_lost_gateway_consumption_ack_is_not_a_fresh_authenticated_invocation(client, native, monkeypatch):
    request = prepare(client, native)
    raw = json.dumps({"pairing_id": request["pairing_id"]}).encode()
    headers = gateway(native, "challenge", raw)
    commit = native.case.rpc.commit
    def lose_ack(transaction, writes):
        result = commit(transaction, writes)
        if any(b"browser_gateway_requests" in write.raw for write in writes):
            raise AmbiguousCommit("Synthetic consumed request Commit reply loss")
        return result
    monkeypatch.setattr(native.case.rpc, "commit", lose_ack)
    assert client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers).status_code == 503
    assert client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=headers).status_code == 403
    assert native.signer.calls == []
    assert native.case.registry.read("pairing_requests", request["pairing_id"])["state"] == "REQUESTED"


def test_closed_independent_restore_fence_rejects_pairing_before_native_write_or_signing(client, native):
    request = prepare(client, native)
    raw = json.dumps({"pairing_id": request["pairing_id"]}).encode()
    before = len(native.case.commits)
    native.case.fence.closed = True
    response = client.post(f"{PREFIX}/candidate/challenge", content=raw, headers=gateway(native, "challenge", raw))
    assert response.status_code == 503
    assert len(native.case.commits) == before and native.signer.calls == []


@pytest.mark.parametrize("fault", ["password", "session", "generation"])
def test_fresh_password_and_current_session_are_checked_before_actual_signing(client, native, fault):
    request = prepare(client, native)
    response = candidate(client, native, "challenge", {"pairing_id": request["pairing_id"]})
    challenge = response.json()
    if fault != "password":
        invalidate(native, fault)
    response = candidate(client, native, "confirm", {"protocol_version": 2, "operation": "confirm_pairing",
        "challenge_id": challenge["challenge_id"], "nonce": challenge["nonce"],
        "password": "synthetic-wrong-secret" if fault == "password" else PASSWORD, "confirmed": True})
    assert response.status_code == 403 and native.signer.calls == []
    assert "synthetic-wrong-secret" not in response.text and PASSWORD not in response.text


def test_signed_device_lookup_does_not_accept_another_key(client, native):
    request = prepare(client, native)
    assert post(client, "status", lookup(native, "status", request["pairing_id"], native.case.auth_key)).status_code == 403
    assert post(client, "status", lookup(native, "status", request["pairing_id"])).status_code == 200


def test_device_origin_matches_retained_extension_not_only_supplied_signature(client, native):
    request = prepare(client, native)
    proof = lookup(native, "status", request["pairing_id"])
    rejected = client.post(f"{PREFIX}/device/status", content=json.dumps(proof),
        headers={"Content-Type": "application/json", "Origin": f"chrome-extension://{'b' * 32}"})
    assert rejected.status_code == 403
    assert post(client, "status", proof).status_code == 200


@pytest.mark.parametrize("raw", [b'{"password":"synthetic-private-body","password":"other"}',
    b'{"password":{"nested":"synthetic-private-body"}}', b'{"password":"synthetic-private-body","confirmed":1}',
    b'{"password":"synthetic-private-body","extra":NaN}', b'{"password":"' + b'z' * 8_192 + b'"}'])
def test_malformed_sensitive_http_body_is_fixed_error_without_input_echo(client, native, raw, caplog):
    response = client.post(f"{PREFIX}/candidate/confirm", content=raw, headers=gateway(native, "confirm", raw))
    assert response.status_code == 403
    assert response.json() == {"detail": "Browser pairing request was rejected"}
    assert "synthetic-private-body" not in response.text and "synthetic-private-body" not in caplog.text
    assert native.signer.calls == []


def test_default_http_dependency_is_unavailable_and_no_action_routes_exist():
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    with TestClient(app) as client:
        response = post(client, "prepare", {})
        assert response.status_code == 503
        assert post(client, "fill", {}).status_code == 422
        assert post(client, "upload", {}).status_code == 422
        assert post(client, "send", {}).status_code == 422
        assert post(client, "prepare", {},).headers["cache-control"] == "private, no-store, max-age=0"


@pytest.mark.parametrize("raw", [b'{"password":"a","password":"b"}', b'{"x":1.0}', b'[]', b'{"x":NaN}', b'{"x":"' + b'a' * 8_192 + b'"}'])
def test_strict_parser_never_echoes_supplied_secret(raw):
    with pytest.raises(GuardDenied) as error:
        exact_json(raw)
    assert str(error.value) == "Browser pairing request was rejected"
