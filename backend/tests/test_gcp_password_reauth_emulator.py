"""Connected typed auth/native lifecycle in NEW authorized localhost databases only.

Operator fixtures are deliberately synthetic and are not production provisioning.
Actual Firestore and Storage SDK boundaries; no ADC, cloud channels or KMS calls.
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from backend.tests.fixtures.pairing_authority import EXTENSION, RELEASE, jwk, sign
from google.api_core.exceptions import Aborted
from sqlalchemy import create_engine
from test_gcp_pairing_emulator import case as case
from test_gcp_pairing_emulator import execute

from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.gcp_pairing import (
    GcpPairingCoordinator,
    _PairingTransaction,
    _PasswordReadTransaction,
)
from app.domains.recovery.gcp_password import GcpPasswordCandidateBoundary
from app.domains.recovery.gcp_password_attempts import GcsPasswordSigning
from app.domains.recovery.password_reauth import (
    CandidateChallengeProof,
    CandidateWebSession,
    PasswordAccountBinding,
    RetainedWebSession,
    SqlPasswordCredentials,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.security import hash_password

PASSWORD = "synthetic-native-password"


class SyntheticSigner:
    def __init__(self, case):
        self.case, self.calls, self.hook = case, [], None

    def sign(self, payload):
        assert not self.case.active
        self.calls.append(payload)
        if self.hook:
            self.hook()
        return {"payload": payload, "signature": sign(self.case.auth_key, "candidate-assertion", payload)}


def namespaces(writes):
    return {json.loads(write.raw)["namespace"] for write in writes}


def restarted_service(native):
    from app.domains.recovery.pairing_auth import PinnedAssertions
    c = native.case
    fresh = GcpPairingCoordinator(c.registry, c.journal, c.pin, epoch_generation=1,
        fence=c.fence, attempts=c.attempts, password_signing=native.signing,
        assertions=PinnedAssertions(issuer="fixture_auth", public_key=jwk(c.auth_key)), claims=c.claims,
        now_ms=lambda: c.now)
    assert fresh._issued == fresh._password_admitted == {}
    return GcpPasswordCandidateBoundary(fresh).service(credentials=native.credentials,
        signer=native.signer, issuer="fixture_auth")


@pytest.fixture
def native(case, tmp_path, monkeypatch):
    account_id, account_event, session_event = uuid4(), uuid4(), uuid4()
    context = CandidateWebSession(candidate_id=1, account_binding_id=account_id, session_id=case.session)
    binding = PasswordAccountBinding(account_binding_id=account_id, candidate_id=1, subject_uuid=case.subject,
        principal_sha256=case.principal, registration_event_id=account_event).model_dump(mode="json")
    session = RetainedWebSession(session_id=case.session, account_binding_id=account_id,
        subject_uuid=case.subject, principal_sha256=case.principal, auth_generation=1,
        registration_event_id=session_event, issued_at_ms=case.now, expires_at_ms=case.now + 600_000,
        active=True).model_dump(mode="json")
    # New unique database only. Fixture registrations carry real native events,
    # but have no protected lifetime journal and establish no deployment proof.
    def register(tx):
        tx.get("pairing_account_bindings", str(account_id))
        tx.get("pairing_web_sessions", case.session)
        tx.event(str(account_event))
        tx.event(str(session_event))
        tx.head()
        tx.append(str(account_event), "PASSWORD_ACCOUNT_BOUND", {k: v for k, v in binding.items() if k != "registration_event_id"})
        tx.append(str(session_event), "PASSWORD_WEB_SESSION_CREATED", {k: v for k, v in session.items() if k != "registration_event_id"})
        tx.put("pairing_account_bindings", str(account_id), binding, immutable=True)
        tx.put("pairing_web_sessions", case.session, session, immutable=True)
    case.registry.run(register)
    engine = create_engine(f"sqlite:///{tmp_path / 'synthetic-password.sqlite'}")
    encoded = hash_password(PASSWORD)
    with engine.begin() as db:
        db.exec_driver_sql("CREATE TABLE users (id INTEGER PRIMARY KEY, password_hash TEXT)")
        db.exec_driver_sql("INSERT INTO users VALUES (?, ?)", (1, encoded))
    credentials = SqlPasswordCredentials(engine, dummy_hash=hash_password("synthetic-dummy"))
    signing = GcsPasswordSigning(case.journal.bucket)
    original_consume = signing.consume
    def allow_signing(marker):
        path, raw = signing._bytes(marker)
        case.http.allowed[path] = raw
        return original_consume(marker)
    monkeypatch.setattr(signing, "consume", allow_signing)
    case.coordinator.password_signing = signing
    original_write = case.attempts.write_intent
    def allow_intent(intent):
        case.http.allow(intent)
        return original_write(intent)
    monkeypatch.setattr(case.attempts, "write_intent", allow_intent)
    boundary = GcpPasswordCandidateBoundary(case.coordinator)
    signer = SyntheticSigner(case)
    def service():
        return boundary.service(credentials=credentials, signer=signer, issuer="fixture_auth")
    value = SimpleNamespace(case=case, context=context, binding=binding, session=session, signer=signer,
        boundary=boundary, service=service, signing=signing, encoded=encoded, credentials=credentials)
    try:
        yield value
    finally:
        credentials.close()
        engine.dispose()


def candidate(native):
    c = native.case
    prepared, _ = execute(c, "prepare_request", key=jwk(c.device_key), extension_id=EXTENSION, revision=RELEASE)
    request = prepared["request"]
    execute(c, "create_request", pairing_id=request["pairing_id"], nonce=prepared["nonce"],
            signature=sign(c.device_key, "request", request))
    challenge, _ = execute(c, "candidate_challenge", pairing_id=request["pairing_id"], subject_id=c.subject, session_id=c.session)
    return request, challenge, {"protocol_version": 2, "operation": "confirm_pairing", "confirmed": True,
        "challenge_id": challenge["payload"]["challenge_id"], "nonce": challenge["nonce"], "password": PASSWORD}


def invalidate(native, fault):
    c = native.case
    if fault == "session":
        c.operator_replace("pairing_sessions", c.session, {"subject_uuid": c.subject, "principal_sha256": c.principal,
            "auth_generation": 1, "active": False})
    elif fault == "generation":
        subject = c.registry.read("subjects", c.subject)
        c.operator_replace("subjects", c.subject, {**subject, "auth_generation": 2})
    elif fault == "account":
        c.operator_replace("pairing_account_bindings", str(native.context.account_binding_id),
            {**native.binding, "candidate_id": 2})
    elif fault == "owner":
        subject = c.registry.read("subjects", c.subject)
        c.operator_replace("subjects", c.subject, {**subject, "principal_sha256": "0" * 64})
    else:
        raise AssertionError(fault)


def test_actual_password_native_core_confirm_complete_revoke_and_secret_free_evidence(native):
    c = native.case
    request, challenge, raw = candidate(native)
    result = native.service().execute(native.context, raw)
    assert result == {"pairing_id": request["pairing_id"], "device_id": request["device_id"], "status": "CANDIDATE_CONFIRMED"}
    device_challenge, _ = execute(c, "device_challenge", pairing_id=request["pairing_id"])
    execute(c, "complete_device", challenge_id=device_challenge["payload"]["challenge_id"], nonce=device_challenge["nonce"],
        signature=sign(c.device_key, "device-challenge", device_challenge["payload"]))
    revoke, _ = execute(c, "revocation_challenge", device_id=request["device_id"], subject_id=c.subject, session_id=c.session)
    revoked = native.service().execute(native.context, {**raw, "operation": "revoke_device",
        "challenge_id": revoke["payload"]["challenge_id"], "nonce": revoke["nonce"]})
    assert revoked["status"] == "REVOKED" and len(native.signer.calls) == 2
    assert c.registry.read("pairing_key_owners", request["key_sha256"]) is not None
    retained = c.registry.read("pairing_password_signing_attempts", raw["challenge_id"])
    assert retained["marker"]["context"] == native.context.model_dump(mode="json")
    protected = b" ".join(raw for raw, _ in c.http.objects.values()).decode()
    for secret in (PASSWORD, native.encoded, challenge["nonce"], revoke["nonce"]):
        assert secret not in protected and secret not in json.dumps(result)
    assert '"signature"' not in protected
    intents = [i for i in c.http.intents.values() if i.password_signing is not None]
    assert len(intents) == 2 and all(c.coordinator.status(i).status == "COMMITTED" for i in intents)


@pytest.mark.parametrize("fault", ["session", "generation", "account", "owner"])
@pytest.mark.parametrize("phase", ["before_read", "after_protected_ack", "after_sign"])
def test_identity_rechecked_before_signing_and_each_consumption(native, monkeypatch, fault, phase):
    c = native.case
    request, _, raw = candidate(native)
    before = len(c.commits)
    if phase == "before_read":
        invalidate(native, fault)
    elif phase == "after_sign":
        native.signer.hook = lambda: invalidate(native, fault)
    else:
        consume = native.signing.consume
        def change_after_ack(marker):
            receipt = consume(marker)
            invalidate(native, fault)
            return receipt
        monkeypatch.setattr(native.signing, "consume", change_after_ack)
    with pytest.raises(GuardDenied):
        native.service().execute(native.context, raw)
    assert len(native.signer.calls) == (1 if phase == "after_sign" else 0)
    assert c.registry.read("pairing_confirmations", request["pairing_id"]) is None
    assert all("pairing_confirmations" not in namespaces(writes) for writes in c.commits[before:])


@pytest.mark.parametrize("phase", ["signing_claim", "core"])
@pytest.mark.parametrize("fault", ["session", "generation", "account", "owner"])
def test_definite_abort_rechecks_identity_before_retry(native, monkeypatch, phase, fault):
    c = native.case
    request, _, raw = candidate(native)
    original, fired = c.rpc.commit, []
    def abort_and_change(transaction, writes):
        relevant = ("pairing_password_signing_attempts" if phase == "signing_claim" else "pairing_confirmations")
        if relevant in namespaces(writes) and not fired:
            fired.append(True)
            c.rpc.rollback(transaction)
            invalidate(native, fault)
            raise Aborted("Synthetic definite abort with identity change")
        return original(transaction, writes)
    monkeypatch.setattr(c.rpc, "commit", abort_and_change)
    with pytest.raises(GuardDenied):
        native.service().execute(native.context, raw)
    assert fired == [True] and len(native.signer.calls) == (1 if phase == "core" else 0)
    assert c.registry.read("pairing_confirmations", request["pairing_id"]) is None


@pytest.mark.parametrize("phase", ["signing_claim", "core"])
@pytest.mark.parametrize("persisted", [True, False])
def test_unknown_native_commit_never_adopted_or_signed_again_after_restart(native, monkeypatch, phase, persisted):
    c = native.case
    request, _, raw = candidate(native)
    original, fired = c.rpc.commit, []
    def uncertain(transaction, writes):
        relevant = "pairing_password_signing_attempts" if phase == "signing_claim" else "pairing_confirmations"
        if relevant in namespaces(writes) and not fired:
            fired.append(True)
            if persisted:
                original(transaction, writes)
            else:
                c.rpc.rollback(transaction)
            raise AmbiguousCommit("Synthetic unknown native reply")
        return original(transaction, writes)
    monkeypatch.setattr(c.rpc, "commit", uncertain)
    with pytest.raises(GuardUnavailable):
        native.service().execute(native.context, raw)
    assert fired == [True]
    signed = len(native.signer.calls)
    assert signed == (1 if phase == "core" else 0)
    monkeypatch.setattr(c.rpc, "commit", original)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        restarted_service(native).execute(native.context, raw)  # New coordinator AND service, no local latches.
    assert len(native.signer.calls) == signed
    assert (c.registry.read("pairing_confirmations", request["pairing_id"]) is not None) == (phase == "core" and persisted)


@pytest.mark.parametrize("persisted", [True, False])
def test_unknown_protected_create_phase_distinguishes_no_possible_signer(native, monkeypatch, persisted):
    _, _, raw = candidate(native)
    original = native.signing._creator._fresh_create
    fired = []
    def uncertain(path, data):
        if not fired:
            fired.append(True)
            if persisted:
                original(path, data)
            return None
        return original(path, data)
    monkeypatch.setattr(native.signing._creator, "_fresh_create", uncertain)
    with pytest.raises(GuardUnavailable):
        native.service().execute(native.context, raw)
    assert native.signer.calls == []
    if persisted:
        with pytest.raises(GuardUnavailable):
            native.service().execute(native.context, raw)
        assert native.signer.calls == []
    else:
        assert native.service().execute(native.context, raw)["status"] == "CANDIDATE_CONFIRMED"
        assert len(native.signer.calls) == 1  # First unknown never reached signing.


def test_narrow_typed_reader_never_widens_generic_namespaces_or_writes(native):
    c = native.case
    def inspect(tx):
        ordinary = _PairingTransaction(tx, c.coordinator, "confirm_candidate")
        trusted = _PasswordReadTransaction(tx, c.coordinator, native.context)
        for namespace, key in (("pairing_account_bindings", str(native.context.account_binding_id)),
                               ("pairing_account_tombstones", str(native.context.account_binding_id)),
                               ("pairing_web_sessions", c.session)):
            with pytest.raises(GuardDenied):
                ordinary.get(namespace, key)
            with pytest.raises(GuardDenied):
                trusted.get(namespace, str(uuid4()))
            with pytest.raises(GuardDenied):
                trusted.put(namespace, key, {})
        with pytest.raises(GuardDenied):
            trusted.append(str(uuid4()), "PASSWORD_ACCOUNT_BOUND", {})
    c.registry.run(inspect)


def test_closed_fence_after_sign_withholds_consumption_and_never_reissues(native):
    c = native.case
    request, _, raw = candidate(native)
    native.signer.hook = lambda: setattr(c.fence, "closed", True)
    with pytest.raises(GuardUnavailable):
        native.service().execute(native.context, raw)
    assert len(native.signer.calls) == 1
    c.fence.closed = False
    native.signer.hook = None
    with pytest.raises(GuardUnavailable):
        native.service().execute(native.context, raw)
    assert len(native.signer.calls) == 1 and c.registry.read("pairing_confirmations", request["pairing_id"]) is None


def test_default_signing_store_is_unavailable_without_signing_or_claim(native):
    from app.domains.recovery.gcp_password_attempts import UnavailablePasswordSigning
    native.case.coordinator.password_signing = UnavailablePasswordSigning()
    _, _, raw = candidate(native)
    with pytest.raises(GuardUnavailable):
        native.service().execute(native.context, raw)
    assert native.signer.calls == []


def test_typed_read_is_connected_to_actual_native_core_and_no_password(native):
    _, _, raw = candidate(native)
    proof = CandidateChallengeProof(operation="confirm_pairing", challenge_id=UUID(raw["challenge_id"]), nonce=raw["nonce"])
    identity, payload, issued = native.boundary.read(native.context, proof)
    assert identity["subject_uuid"] == native.case.subject and issued == native.case.now
    assert payload["challenge_id"] == raw["challenge_id"]
    assert PASSWORD not in repr(proof) and raw["nonce"] not in repr(proof)


def test_concurrent_coordinators_one_protected_signing_winner(native):
    _, _, raw = candidate(native)
    entered, release = Event(), Event()
    def pause_signer():
        entered.set()
        assert release.wait(timeout=5.0)
    native.signer.hook = pause_signer
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(native.service().execute, native.context, raw)
        assert entered.wait(timeout=5.0)
        try:
            with pytest.raises(GuardUnavailable):
                restarted_service(native).execute(native.context, raw)
            assert len(native.signer.calls) == 1
        finally:
            release.set()
        assert first.result(timeout=5.0)["status"] == "CANDIDATE_CONFIRMED"


@pytest.mark.parametrize("fault", ["bad_name", "bad_bucket", "bad_size", "bad_generation", "altered_media", "response_loss"])
def test_actual_storage_http_corrupt_or_unknown_ack_never_signs_or_adopts(native, monkeypatch, fault):
    import requests
    _, _, raw = candidate(native)
    c, request = native.case, native.case.journal.bucket.client._http.request
    original = request.side_effect
    touched = []
    def corrupt(method, url, **kwargs):
        reply = original(method, url, **kwargs)
        signing_request = "authority-password-signing" in url or (method == "POST" and b"authority-password-signing" in kwargs["data"])
        if signing_request and not touched:
            if method == "POST" and fault != "altered_media":
                touched.append(method)
                if fault == "response_loss":
                    raise requests.Timeout("Synthetic response loss")
                body = reply.json()
                key, bad = {"bad_name": ("name", "different"), "bad_bucket": ("bucket", "different"),
                    "bad_size": ("size", "0"), "bad_generation": ("generation", "01")}[fault]
                body[key] = bad
                reply._content = json.dumps(body).encode()
            elif fault == "altered_media" and "alt=media" in url:
                touched.append(method)
                reply._content = b"x" * len(reply.content)
        return reply
    monkeypatch.setattr(request, "side_effect", corrupt)
    with pytest.raises(GuardUnavailable):
        native.service().execute(native.context, raw)
    assert touched and native.signer.calls == []
    monkeypatch.setattr(request, "side_effect", original)
    with pytest.raises(GuardUnavailable):
        restarted_service(native).execute(native.context, raw)
    assert native.signer.calls == []
    # POST has no hidden retry; restart's separate fresh attempt sees 412, and
    # never performs GET adoption after that unknown/existing POST response.
    assert len([call for call in c.http.calls if call[0] == "POST"]) == 8  # Three lifecycle intent/markers + two signing creates.


def test_old_v1_journal_canonical_shape_preserved_without_password_port(native):
    from app.domains.recovery.contracts import fingerprint
    from app.domains.recovery.gcp_pairing_contracts import PairingJournalIntent
    c = native.case
    plan = c.coordinator.allocate("prepare_request", {"key": jwk(c.device_key), "extension_id": EXTENSION, "revision": RELEASE})
    old = plan.intent.model_dump(mode="json")
    assert "password_signing" not in old and plan.intent.digest == fingerprint(old)
    assert PairingJournalIntent.model_validate(old).model_dump(mode="json") == old


@pytest.fixture
def connected_kms(native, monkeypatch):
    # Dependency ensures the authorized emulator channel is built BEFORE the
    # reviewed KMS fixture forbids all further insecure/cloud channel creation.
    from test_pairing_kms import native as kms_fixture
    yield from kms_fixture.__wrapped__(monkeypatch)


def test_native_password_admission_actual_kms_sdk_core_and_claim_are_connected(native, connected_kms):
    from app.domains.recovery.pairing_auth import PinnedAssertions, verify_signature
    c, kms = native.case, connected_kms
    kms.now = c.now
    coordinator = GcpPairingCoordinator(c.registry, c.journal, c.pin, epoch_generation=1,
        fence=c.fence, attempts=c.attempts, password_signing=native.signing,
        assertions=PinnedAssertions(issuer="fixture_auth", public_key=jwk(kms.private)), claims=kms.claims,
        now_ms=lambda: c.now)
    c.coordinator = coordinator
    request, _, raw = candidate(native)
    service = GcpPasswordCandidateBoundary(coordinator).service(credentials=native.credentials,
        signer=kms.assertions, issuer="fixture_auth")
    assert service.execute(native.context, raw)["status"] == "CANDIDATE_CONFIRMED"
    assert [name for name, _, _ in kms.calls] == ["public", "sign"]
    challenge, _ = execute(c, "device_challenge", pairing_id=request["pairing_id"])
    completed, _ = execute(c, "complete_device", challenge_id=challenge["payload"]["challenge_id"], nonce=challenge["nonce"],
        signature=sign(c.device_key, "device-challenge", challenge["payload"]))
    assert [name for name, _, _ in kms.calls] == ["public", "sign", "public", "sign"]
    verify_signature(jwk(kms.private), "device-claim", completed["device_claim"]["payload"], completed["device_claim"]["signature"])


def test_corrupt_kms_reply_after_native_admission_never_consumes_or_signs_again(native, connected_kms):
    from app.domains.recovery.pairing_auth import PinnedAssertions
    c, kms = native.case, connected_kms
    kms.now = c.now
    c.coordinator.core.assertions = PinnedAssertions(issuer="fixture_auth", public_key=jwk(kms.private))
    request, _, raw = candidate(native)
    service = native.boundary.service(credentials=native.credentials, signer=kms.assertions, issuer="fixture_auth")
    kms.sign_hook = lambda reply: setattr(reply, "verified_digest_crc32c", False)
    with pytest.raises(GuardUnavailable):
        service.execute(native.context, raw)
    assert [name for name, _, _ in kms.calls] == ["public", "sign"]
    kms.sign_hook = None
    with pytest.raises(GuardUnavailable):
        native.boundary.service(credentials=native.credentials, signer=kms.assertions, issuer="fixture_auth").execute(native.context, raw)
    assert [name for name, _, _ in kms.calls] == ["public", "sign"]
    assert c.registry.read("pairing_confirmations", request["pairing_id"]) is None


def test_account_tombstone_between_sign_and_dispatch_is_not_sql_recreated(native):
    c = native.case
    request, _, raw = candidate(native)
    def tombstone():
        c.registry.run(lambda tx: tx.put("pairing_account_tombstones", str(native.context.account_binding_id),
            {"account_binding_id": str(native.context.account_binding_id)}, immutable=True))
    native.signer.hook = tombstone
    with pytest.raises(GuardDenied):
        native.service().execute(native.context, raw)
    assert len(native.signer.calls) == 1
    assert c.registry.read("pairing_confirmations", request["pairing_id"]) is None


def test_changed_actual_device_owner_on_consuming_retry_is_checked_by_real_core(native, monkeypatch):
    c = native.case
    request, _, raw = candidate(native)
    native.service().execute(native.context, raw)
    challenge, _ = execute(c, "device_challenge", pairing_id=request["pairing_id"])
    execute(c, "complete_device", challenge_id=challenge["payload"]["challenge_id"], nonce=challenge["nonce"],
        signature=sign(c.device_key, "device-challenge", challenge["payload"]))
    revoke, _ = execute(c, "revocation_challenge", device_id=request["device_id"], subject_id=c.subject, session_id=c.session)
    original, fired = c.rpc.commit, []
    def change_owner_on_abort(transaction, writes):
        if "pairing_device_tombstones" in namespaces(writes) and not fired:
            fired.append(True)
            c.rpc.rollback(transaction)
            device = c.registry.read("devices", request["device_id"])
            c.operator_replace("devices", request["device_id"], {**device, "subject_uuid": str(uuid4())})
            raise Aborted("Synthetic definite abort with changed device owner")
        return original(transaction, writes)
    monkeypatch.setattr(c.rpc, "commit", change_owner_on_abort)
    with pytest.raises(GuardDenied):
        native.service().execute(native.context, {**raw, "operation": "revoke_device",
            "challenge_id": revoke["payload"]["challenge_id"], "nonce": revoke["nonce"]})
    assert fired == [True] and len(native.signer.calls) == 2
    assert c.registry.read("pairing_device_tombstones", request["device_id"]) is None


@pytest.mark.parametrize("mode", ["invalid_signature", "signing_exception"])
def test_failed_signer_never_admits_another_signature_after_restart(native, mode):
    c = native.case
    request, _, raw = candidate(native)
    class FailedSigner:
        calls = 0
        def sign(self, payload):
            self.calls += 1
            if mode == "signing_exception":
                raise RuntimeError("Synthetic withheld signature")
            return {"payload": payload, "signature": "invalid"}
    signer = FailedSigner()
    service = native.boundary.service(credentials=native.credentials, signer=signer, issuer="fixture_auth")
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.execute(native.context, raw)
    assert signer.calls == 1
    with pytest.raises(GuardUnavailable):
        restarted_service(native).execute(native.context, raw)
    assert native.signer.calls == [] and c.registry.read("pairing_confirmations", request["pairing_id"]) is None
