"""Connected real PairingService/native Firestore, synthetic pinned Storage HTTP.

Only a fresh uniquely named database on the explicitly authorized localhost
emulator is written. No database reset/delete, credentials, cloud channel or
real identity is used. This proves connected data behavior, not production
UID/IAM, KMS, retention, restore completeness or candidate authentication.
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Event, Lock, get_ident
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, unquote, urlsplit
from uuid import uuid4

import google.auth
import grpc
import pytest
import requests
from backend.tests.fixtures.pairing_authority import EXTENSION, NOW, RELEASE, jwk, sign
from cryptography.hazmat.primitives.asymmetric import ec
from google.api_core.exceptions import Aborted
from google.auth.credentials import AnonymousCredentials
from google.cloud.firestore_v1.services.firestore import FirestoreClient
from google.cloud.firestore_v1.services.firestore.transports.grpc import FirestoreGrpcTransport
from google.cloud.firestore_v1.types import Document, Write
from google.cloud.storage import Client
from test_gcp_journal_sdk import response

from app.domains.recovery.contracts import canonical, fingerprint
from app.domains.recovery.gcp_buffer import BufferedRegistry, BufferedTransaction
from app.domains.recovery.gcp_contracts import AmbiguousCommit, RegistryPin
from app.domains.recovery.gcp_journal import GcsJournal
from app.domains.recovery.gcp_pairing import (
    GcpPairingCoordinator,
    PairingCommandPlan,
    pairing_control_record,
)
from app.domains.recovery.gcp_pairing_attempts import GcsPairingAttempts
from app.domains.recovery.gcp_pairing_contracts import PairingAttemptMarker, PairingJournalIntent
from app.domains.recovery.gcp_rpc import FirestoreRpc
from app.domains.recovery.gcp_service import control_record
from app.domains.recovery.pairing_auth import PinnedAssertions, verify_signature
from app.domains.recovery.pairing_contracts import CandidateAssertion, DeviceClaim
from app.domains.recovery.pairing_service import PairingService
from app.domains.recovery.store import (
    GuardDenied,
    GuardUnavailable,
    UnavailableStore,
    production_store,
)

ENDPOINT = "127.0.0.1:58877"
PROJECT = "hirewiz-local-authority"


def is_admission(writes):
    return len(writes) == 1 and json.loads(writes[0].raw)["namespace"] == "pairing_invocations"


class LocalTransactions:
    """Prove IO is outside its own transaction even with concurrent replicas."""

    def __init__(self):
        self.owners, self.lock = {}, Lock()

    def add(self, transaction):
        with self.lock:
            self.owners[transaction] = get_ident()

    def discard(self, transaction):
        with self.lock:
            self.owners.pop(transaction, None)

    def __bool__(self):
        with self.lock:
            return get_ident() in self.owners.values()

    def any(self):
        with self.lock:
            return bool(self.owners)


class SyntheticJournalHttp:
    def __init__(self, active):
        self.active, self.allowed, self.objects, self.calls, self.intents = active, {}, {}, [], {}

    def allow(self, intent):
        path, raw = GcsJournal._intent_bytes(intent)
        self.allowed[path] = raw
        self.intents[str(intent.operation_id)] = intent

    def request(self, method, url, **kwargs):
        assert not self.active, "Storage SDK IO entered a native transaction"
        assert kwargs["timeout"] == 2.0
        assert kwargs.get("allow_redirects", False) is False
        parsed, query = urlsplit(url), parse_qs(urlsplit(url).query)
        self.calls.append((method, query))
        if method == "POST":
            metadata = kwargs["data"].split(b"\r\n\r\n", 1)[1].split(b"\r\n--", 1)[0]
            path = json.loads(metadata)["name"]
            assert query["ifGenerationMatch"] == ["0"] and query["uploadType"] == ["multipart"]
            if path.startswith("authority-pairing-attempts/"):
                raw = kwargs["data"].split(b"\r\n\r\n")[2].rsplit(b"\r\n--", 1)[0]
                marker = PairingAttemptMarker.model_validate(json.loads(raw))
                intent = self.intents[str(marker.operation_id)]
                intent_path, intent_raw = GcsJournal._intent_bytes(intent)
                assert marker.pin == intent.pin and marker.epoch_generation == intent.epoch_generation
                assert marker.intent_sha256 == intent.digest
                assert marker.journal.bucket == "synthetic-pairing-journal"
                assert marker.journal.path == intent_path and marker.journal.sha256 == hashlib.sha256(intent_raw).hexdigest()
                assert marker.journal.generation == self.objects[intent_path][1]
                assert GcsPairingAttempts._bytes(marker) == (path, raw)
                self.allowed[path] = raw
            assert self.allowed[path] in kwargs["data"]
            if path in self.objects:
                return response(b'{"error":{"code":412,"message":"synthetic existing object"}}', status=412)
            self.objects[path] = (self.allowed[path], str(41 + len(self.objects)))
        else:
            assert method == "GET"
            path = unquote(parsed.path.split("/o/", 1)[1])
        raw, generation = self.objects[path]
        if "generation" in query:
            assert query["generation"] == query["ifGenerationMatch"] == [generation]
        if query.get("alt") == ["media"]:
            assert kwargs["headers"]["range"] == f"bytes=0-{len(raw)}"
            assert kwargs["stream"] is True
            return response(raw)
        return response(json.dumps({"name": path, "bucket": "synthetic-pairing-journal",
                                   "generation": generation, "size": str(len(raw))}).encode())


class SyntheticFence:
    def __init__(self, active, pin):
        self.active, self.pin, self.calls, self.closed = active, pin, 0, False

    def check(self, pin):
        assert not self.active, "Fresh fence IO entered a native transaction"
        assert pin == self.pin
        self.calls += 1
        if self.closed:
            raise GuardUnavailable("Synthetic restore fence closed")


class FixtureClaims:
    def __init__(self, private):
        self.private, self.calls = private, 0

    def sign(self, payload):
        self.calls += 1
        return {"payload": payload, "signature": sign(self.private, "device-claim", payload)}


@pytest.fixture
def case(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Ambient credentials or cloud/secure transport is forbidden")
    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(grpc, "secure_channel", forbidden)
    monkeypatch.setenv("DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA", "true")
    database = f"projects/{PROJECT}/databases/pairing-bridge-{uuid4().hex[:12]}"
    channel = grpc.insecure_channel(ENDPOINT)
    try:
        grpc.channel_ready_future(channel).result(timeout=3.0)
    except grpc.FutureTimeoutError:
        channel.close()
        pytest.fail("Authorized root-owned localhost pairing emulator is required")
    client = FirestoreClient(transport=FirestoreGrpcTransport(host=ENDPOINT, channel=channel,
                             credentials=AnonymousCredentials()))
    rpc = FirestoreRpc(client, SimpleNamespace(), database_resource=database, rpc_timeout=5.0)
    registry = BufferedRegistry(rpc, deadline_seconds=10.0)
    pin = RegistryPin(database=database, database_uid=uuid4(), authority_id=uuid4(), incarnation=1, epoch_id=uuid4())
    active, commits = LocalTransactions(), []
    real_begin, real_commit, real_rollback = rpc.begin, rpc.commit, rpc.rollback
    def begin():
        transaction = real_begin()
        active.add(transaction)
        return transaction
    def commit(transaction, writes):
        commits.append(writes)
        try:
            return real_commit(transaction, writes)
        finally:
            active.discard(transaction)
    def rollback(transaction):
        try:
            return real_rollback(transaction)
        finally:
            active.discard(transaction)
    monkeypatch.setattr(rpc, "begin", begin)
    monkeypatch.setattr(rpc, "commit", commit)
    monkeypatch.setattr(rpc, "rollback", rollback)
    subject, session, registration = str(uuid4()), str(uuid4()), str(uuid4())
    principal = fingerprint({"synthetic_subject": subject})
    event = {"sequence": 1, "previous": "0" * 64, "event_id": registration,
             "kind": "SUBJECT_REGISTERED", "payload": {"subject_uuid": subject}}
    event["digest"] = hashlib.sha256(canonical(event).encode()).hexdigest()
    seeded = [
        ("control", "meta", control_record(pin)), ("pairing_control", "current", pairing_control_record(pin, 1)),
        ("pairing_clock", "observed", {"now_ms": NOW}),
        ("head", "global", {"sequence": 1, "digest": event["digest"]}), ("events", registration, event),
        ("pairing_extensions", f"{EXTENSION}:{RELEASE}", {"extension_id": EXTENSION,
            "executor_revision": RELEASE, "protocol_version": 2, "active": True}),
        ("subjects", subject, {"active": True, "principal_sha256": principal, "auth_generation": 1,
                               "registration_event_id": registration}),
        ("pairing_sessions", session, {"subject_uuid": subject, "principal_sha256": principal,
                                       "auth_generation": 1, "active": True}),
    ]
    writes = [Write(update=Document(name=BufferedTransaction(rpc, b"path-only").path(namespace, key),
              fields={"body": {"bytes_value": canonical({"namespace": namespace, "key": key, "value": value}).encode()}}),
              current_document={"exists": False}) for namespace, key, value in seeded]
    seeded_reply = client.commit(request={"database": database, "writes": writes}, retry=None, timeout=5.0)
    assert len(seeded_reply.write_results) == len(writes)
    http = SyntheticJournalHttp(active)
    http_session = MagicMock(spec=requests.Session)
    http_session.is_mtls = False
    http_session.request.side_effect = http.request
    storage = Client(project=PROJECT, credentials=AnonymousCredentials(), _http=http_session)
    journal = GcsJournal(storage.bucket("synthetic-pairing-journal"))
    attempts = GcsPairingAttempts(storage.bucket("synthetic-pairing-journal"))
    fence = SyntheticFence(active, pin)
    auth_key, device_key, claim_key = [ec.generate_private_key(ec.SECP256R1()) for _ in range(3)]
    claims = FixtureClaims(claim_key)
    value = SimpleNamespace(rpc=rpc, registry=registry, pin=pin, journal=journal, attempts=attempts, fence=fence, http=http,
        active=active, commits=commits, subject=subject, session=session, principal=principal,
        auth_key=auth_key, device_key=device_key, claim_key=claim_key, claims=claims, now=NOW)
    value.coordinator = GcpPairingCoordinator(registry, journal, pin, epoch_generation=1, fence=fence, attempts=attempts,
        assertions=PinnedAssertions(issuer="fixture_auth", public_key=jwk(auth_key)), claims=claims,
        now_ms=lambda: value.now)
    def operator_replace(namespace, key, changed):
        path = BufferedTransaction(rpc, b"path-only").path(namespace, key)
        transaction = rpc.begin()
        original = rpc.read(transaction, (path,))[path]
        rpc.rollback(transaction)
        changed_reply = client.commit(request={"database": database, "writes": [Write(
            update=Document(name=path, fields={"body": {"bytes_value": canonical({
                "namespace": namespace, "key": key, "value": changed}).encode()}}),
            current_document={"update_time": original.version})]}, retry=None, timeout=5.0)
        assert len(changed_reply.write_results) == 1
    value.operator_replace = operator_replace
    try:
        yield value
    finally:
        assert not active.any(), "Native transaction cleanup leaked in another thread"
        channel.close()
        storage.close()


def execute(case, command, **parameters):
    intent = case.coordinator.allocate(command, parameters)
    case.http.allow(intent.intent)
    response = case.coordinator.execute(intent, parameters)
    assert response.status == "COMMITTED" and response.result is not None
    return response.result, intent


def assertion(case, challenge):
    payload = challenge["payload"]
    value = CandidateAssertion(protocol_version=2, assertion_id=uuid4(), issuer="fixture_auth",
        audience="hirewiz:pairing-only", operation=payload["operation"], subject_uuid=payload["subject_uuid"],
        auth_generation=payload["auth_generation"], principal_sha256=payload["principal_sha256"],
        session_id=payload["session_id"], method="password_reauth", authenticated_at_ms=case.now,
        issued_at_ms=case.now, expires_at_ms=case.now + 60_000, binding_sha256=challenge["binding_sha256"],
        confirmed=True).model_dump(mode="json")
    return {"payload": value, "signature": sign(case.auth_key, "candidate-assertion", value)}


def confirmed(case):
    prepared, _ = execute(case, "prepare_request", key=jwk(case.device_key), extension_id=EXTENSION, revision=RELEASE)
    request = prepared["request"]
    execute(case, "create_request", pairing_id=request["pairing_id"], nonce=prepared["nonce"],
            signature=sign(case.device_key, "request", request))
    candidate, _ = execute(case, "candidate_challenge", pairing_id=request["pairing_id"],
                           subject_id=case.subject, session_id=case.session)
    execute(case, "confirm_candidate", challenge_id=candidate["payload"]["challenge_id"],
            nonce=candidate["nonce"], envelope=assertion(case, candidate))
    challenge, _ = execute(case, "device_challenge", pairing_id=request["pairing_id"])
    proof = {"challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"],
             "signature": sign(case.device_key, "device-challenge", challenge["payload"])}
    return request, proof


def test_connected_actual_core_lifecycle_refresh_revoke_keeps_permanent_key_owner(case):
    assert type(case.coordinator.core) is PairingService
    request, proof = confirmed(case)
    completed, intent = execute(case, "complete_device", **proof)
    claim = DeviceClaim.model_validate(completed["device_claim"]["payload"])
    verify_signature(jwk(case.claim_key), "device-claim", completed["device_claim"]["payload"], completed["device_claim"]["signature"])
    assert str(claim.subject_uuid) == case.subject and claim.operation == "device_identity"
    assert set(case.coordinator.pairing_status(request["pairing_id"])) == {"pairing_id", "device_id", "status"}
    assert case.coordinator.status(intent.intent).status == "COMMITTED"
    refreshed, _ = execute(case, "refresh_challenge", device_id=request["device_id"])
    identified, _ = execute(case, "refresh_claim", challenge_id=refreshed["payload"]["challenge_id"],
        nonce=refreshed["nonce"], signature=sign(case.device_key, "device-challenge", refreshed["payload"]))
    assert identified["status"] == "IDENTIFIED" and case.claims.calls == 2
    revoke, _ = execute(case, "revocation_challenge", device_id=request["device_id"], subject_id=case.subject, session_id=case.session)
    result, _ = execute(case, "revoke_device", challenge_id=revoke["payload"]["challenge_id"], nonce=revoke["nonce"], envelope=assertion(case, revoke))
    assert result["status"] == "REVOKED"
    assert case.registry.read("pairing_key_owners", request["key_sha256"]) == {"device_id": request["device_id"], "subject_uuid": case.subject}
    assert case.coordinator.pairing_status(request["pairing_id"])["status"] == "REVOKED"
    assert case.registry.read("actors", request["device_id"]) is None
    assert case.registry.read("grants", request["device_id"]) is None
    # A fresh request/device/operation cannot namespace around permanent key ownership.
    _, next_proof = confirmed(case)
    next_plan = case.coordinator.allocate("complete_device", next_proof)
    case.http.allow(next_plan.intent)
    with pytest.raises(GuardDenied, match="permanently claimed"):
        case.coordinator.execute(next_plan, next_proof)
    assert case.claims.calls == 2
    assert not case.active


def test_definite_aborted_reuses_preallocated_ids_and_fresh_fence(case, monkeypatch):
    parameters = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    intent = case.coordinator.allocate("prepare_request", parameters)
    case.http.allow(intent.intent)
    real_commit, attempts = case.rpc.commit, []
    def abort_once(transaction, writes):
        if is_admission(writes):
            return real_commit(transaction, writes)
        attempts.append(tuple(write.raw for write in writes))
        if len(attempts) == 1:
            parameters["key"]["x"] = jwk(ec.generate_private_key(ec.SECP256R1()))["x"]
            raise Aborted("Synthetic definite abort before native commit")
        return real_commit(transaction, writes)
    monkeypatch.setattr(case.rpc, "commit", abort_once)
    def mint_forbidden():
        pytest.fail("Retry must not allocate a new UUID")
    monkeypatch.setattr("app.domains.recovery.pairing_service.uuid4", mint_forbidden)
    result = case.coordinator.execute(intent, parameters)
    assert result.status == "COMMITTED" and attempts[0] == attempts[1]
    assert len(case.commits) == 2 and sum(map(is_admission, case.commits)) == 1 and case.fence.calls >= 5
    assert result.result["request"]["challenge_id"] == str(intent.intent.allocated_ids[3])
    assert result.result["request"]["public_key"] == jwk(case.device_key)


def test_ambiguous_no_retained_commit_is_status_only_for_cloned_operation(case, monkeypatch):
    _, proof = confirmed(case)
    plan = case.coordinator.allocate("complete_device", proof)
    case.http.allow(plan.intent)
    attempts = []
    real_commit = case.rpc.commit
    def unknown_without_retained_row(transaction, writes):
        if is_admission(writes):
            return real_commit(transaction, writes)
        attempts.append(writes)
        case.active.discard(transaction)
        raise AmbiguousCommit("Synthetic sent Commit with no known retained outcome")
    monkeypatch.setattr(case.rpc, "commit", unknown_without_retained_row)
    initial = case.coordinator.execute(plan, proof)
    posts = len([method for method, _ in case.http.calls if method == "POST"])
    assert initial.status == "UNKNOWN" and initial.result is initial.journal is None
    for equivalent in (plan, replace(plan), PairingCommandPlan(plan.intent, plan._allocated_ids, plan._issuer)):
        response = case.coordinator.execute(equivalent, proof)
        assert response.status == "UNKNOWN" and response.result is response.journal is None
    assert len(attempts) == 1 and case.claims.calls == 0
    assert len([method for method, _ in case.http.calls if method == "POST"]) == posts
    fresh = GcpPairingCoordinator(case.registry, case.journal, case.pin, epoch_generation=1,
        fence=case.fence, now_ms=lambda: case.now)
    with pytest.raises(GuardDenied):
        fresh.execute(plan, proof)
    with pytest.raises(GuardDenied):
        case.coordinator.execute(PairingCommandPlan(plan.intent, plan._allocated_ids), proof)
    assert len(attempts) == 1


def test_concurrent_cloned_plan_never_runs_a_second_core_mutation(case, monkeypatch):
    parameters = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    plan = case.coordinator.allocate("prepare_request", parameters)
    case.http.allow(plan.intent)
    entered, release = Event(), Event()
    real_write = case.attempts.write_intent
    def wait_before_write(intent):
        entered.set()
        assert release.wait(3.0)
        return real_write(intent)
    monkeypatch.setattr(case.attempts, "write_intent", wait_before_write)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(case.coordinator.execute, plan, parameters)
        assert entered.wait(3.0)
        try:
            loser = case.coordinator.execute(replace(plan), parameters)
            assert loser.status == "UNKNOWN" and loser.result is loser.journal is None
        finally:
            release.set()
        winner = pending.result(timeout=5.0)
    assert winner.status == "COMMITTED" and winner.result is not None
    assert len(case.commits) == 2 and sum(map(is_admission, case.commits)) == 1
    assert len([method for method, _ in case.http.calls if method == "POST"]) == 2  # Intent + consumption marker.


def test_journal_hook_cannot_change_bound_nested_key(case, monkeypatch):
    parameters = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    plan = case.coordinator.allocate("prepare_request", parameters)
    case.http.allow(plan.intent)
    original = jwk(case.device_key)
    real_write = case.attempts.write_intent
    def mutate_input(intent):
        parameters["key"].update(jwk(ec.generate_private_key(ec.SECP256R1())))
        return real_write(intent)
    monkeypatch.setattr(case.attempts, "write_intent", mutate_input)
    result = case.coordinator.execute(plan, parameters)
    assert result.status == "COMMITTED" and result.result["request"]["public_key"] == original
    assert parameters["key"] != original
    assert case.coordinator._input("prepare_request", {**parameters, "key": original}) == plan.intent.input_sha256


def test_journal_hook_cannot_change_bound_candidate_assertion(case, monkeypatch):
    prepared, _ = execute(case, "prepare_request", key=jwk(case.device_key), extension_id=EXTENSION, revision=RELEASE)
    request = prepared["request"]
    execute(case, "create_request", pairing_id=request["pairing_id"], nonce=prepared["nonce"],
            signature=sign(case.device_key, "request", request))
    challenge, _ = execute(case, "candidate_challenge", pairing_id=request["pairing_id"],
                           subject_id=case.subject, session_id=case.session)
    parameters = {"challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"],
                  "envelope": assertion(case, challenge)}
    plan = case.coordinator.allocate("confirm_candidate", parameters)
    case.http.allow(plan.intent)
    real_write = case.attempts.write_intent
    def mutate_input(intent):
        parameters["envelope"]["payload"]["subject_uuid"] = str(uuid4())
        parameters["envelope"]["signature"] = "invalid_mutated_signature"
        return real_write(intent)
    monkeypatch.setattr(case.attempts, "write_intent", mutate_input)
    result = case.coordinator.execute(plan, parameters)
    assert result.status == "COMMITTED" and result.result["status"] == "CANDIDATE_CONFIRMED"
    stored = case.registry.read("pairing_confirmations", request["pairing_id"])
    assert stored["assertion"]["subject_uuid"] == case.subject


def test_real_native_commit_response_loss_never_signs_or_remints_claim(case, monkeypatch):
    request, proof = confirmed(case)
    intent = case.coordinator.allocate("complete_device", proof)
    case.http.allow(intent.intent)
    real_commit = case.rpc.commit
    before = len(case.commits)
    def lose_reply(transaction, writes):
        committed = real_commit(transaction, writes)
        if is_admission(writes):
            return committed
        raise AmbiguousCommit("Synthetic loss after actual successful native commit")
    monkeypatch.setattr(case.rpc, "commit", lose_reply)
    result = case.coordinator.execute(intent, proof)
    assert result.status == "UNKNOWN" and result.result is result.journal is None
    assert case.claims.calls == 0 and len(case.commits) == before + 2
    assert case.coordinator.status(intent.intent).status == "COMMITTED"
    monkeypatch.setattr(case.rpc, "commit", real_commit)
    replay = case.coordinator.execute(intent, proof)
    assert replay.status == "COMMITTED" and replay.result is None and case.claims.calls == 0
    assert len(case.commits) == before + 2
    assert case.registry.read("pairing_key_owners", request["key_sha256"]) is not None
    replacement = case.coordinator.allocate("complete_device", proof)
    case.http.allow(replacement.intent)
    with pytest.raises(GuardDenied):
        case.coordinator.execute(replacement, proof)
    assert case.claims.calls == 0


def test_gate_defaults_and_operation_scopes_never_bootstrap_or_emit_authority(case):
    assert isinstance(production_store(), UnavailableStore)
    absent = GcpPairingCoordinator(case.registry, case.journal, case.pin, epoch_generation=1, now_ms=lambda: case.now)
    params = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    intent = absent.allocate("prepare_request", params)
    with pytest.raises(GuardUnavailable):
        absent.execute(intent, params)
    assert case.http.calls == []
    with pytest.raises(GuardUnavailable):
        case.coordinator.core.store.transact(lambda tx: tx.put("control", "meta", {}))
    with pytest.raises(GuardDenied):
        case.coordinator.allocate("grant_employer", {})
    with pytest.raises(GuardDenied):
        case.coordinator.execute(intent, {**params, "subject_id": case.subject})
    assert case.commits == []


def test_prepare_replay_is_receipt_only_and_changed_parameters_are_denied(case):
    params = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    initial, intent = execute(case, "prepare_request", **params)
    before = len(case.commits)
    replay = case.coordinator.execute(intent, params)
    assert replay.result is None and replay.status == "COMMITTED" and len(case.commits) == before
    assert initial["nonce"] not in json.dumps(intent.intent.model_dump(mode="json"))
    assert "nonce" not in case.coordinator.status(intent.intent).model_dump()
    with pytest.raises(GuardDenied):
        case.coordinator.execute(intent, {**params, "revision": "different_release"})


def test_backward_monotonic_clock_and_closed_fence_block_all_writes(case):
    params = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    intent = case.coordinator.allocate("prepare_request", params)
    case.http.allow(intent.intent)
    case.now -= 1
    with pytest.raises(GuardDenied):
        case.coordinator.execute(intent, params)
    assert case.commits == case.http.calls == []
    case.now += 1
    case.fence.closed = True
    with pytest.raises(GuardUnavailable):
        case.coordinator.execute(intent, params)
    assert case.commits == case.http.calls == []


def test_typed_plan_rejects_extra_ids_and_unknown_commands(case):
    intent = case.coordinator.allocate("prepare_request", {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE})
    with pytest.raises(ValueError):
        PairingJournalIntent.model_validate({**intent.intent.model_dump(mode="json"), "allocated_ids": [str(uuid4())]})
    with pytest.raises(ValueError):
        PairingJournalIntent.model_validate({**intent.intent.model_dump(mode="json"), "command": "release_owner"})


@pytest.mark.parametrize("fault", ["fence", "expiry"])
def test_post_commit_fence_or_lifetime_loss_is_unknown_without_claim(case, monkeypatch, fault):
    request, proof = confirmed(case)
    plan = case.coordinator.allocate("complete_device", proof)
    case.http.allow(plan.intent)
    real_commit = case.rpc.commit
    before = len(case.commits)
    def close_after_commit(transaction, writes):
        result = real_commit(transaction, writes)
        if is_admission(writes):
            return result
        if fault == "fence":
            case.fence.closed = True
        else:
            case.now = plan.intent.deadline_ms
        return result
    monkeypatch.setattr(case.rpc, "commit", close_after_commit)
    result = case.coordinator.execute(plan, proof)
    assert result.status == "UNKNOWN" and result.result is result.journal is None
    assert case.claims.calls == 0 and len(case.commits) == before + 2
    assert case.registry.read("pairing_key_owners", request["key_sha256"]) is not None
    case.fence.closed = False
    assert case.coordinator.status(plan.intent).status == "COMMITTED"
    assert case.claims.calls == 0


@pytest.mark.parametrize("fault", ["native_bool", "pairing_bool", "clock_bool", "clock_backward"])
def test_exact_operator_controls_and_monotonic_clock_are_not_runtime_repaired(case, fault):
    if fault == "native_bool":
        case.operator_replace("control", "meta", {**control_record(case.pin), "incarnation": True})
    elif fault == "pairing_bool":
        case.operator_replace("pairing_control", "current", {**pairing_control_record(case.pin, 1), "generation": True})
    elif fault == "clock_bool":
        case.operator_replace("pairing_clock", "observed", {"now_ms": True})
    else:
        case.now = NOW - 1  # Intent itself is fresh; retained authority clock is ahead.
    params = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    plan = case.coordinator.allocate("prepare_request", params)
    case.http.allow(plan.intent)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        case.coordinator.execute(plan, params)
    assert case.commits == []
    assert case.registry.read("pairing_operations", str(plan.intent.operation_id)) is None


def test_definite_abort_rechecks_fence_before_next_transaction(case, monkeypatch):
    params = {"key": jwk(case.device_key), "extension_id": EXTENSION, "revision": RELEASE}
    plan = case.coordinator.allocate("prepare_request", params)
    case.http.allow(plan.intent)
    def abort_then_close(transaction, writes):
        case.fence.closed = True
        raise Aborted("Synthetic definite abort")
    monkeypatch.setattr(case.rpc, "commit", abort_then_close)
    with pytest.raises(GuardUnavailable):
        case.coordinator.execute(plan, params)
    assert case.commits == [] and not case.active
    assert case.registry.read("pairing_operations", str(plan.intent.operation_id)) is None
