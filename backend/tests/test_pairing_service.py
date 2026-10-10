"""Synthetic identity-only lifecycle, no production authentication or network."""
from __future__ import annotations

import json
import sqlite3
from functools import partial
from uuid import UUID, uuid4

import pytest
from backend.tests.fixtures.pairing_authority import (
    EXTENSION,
    RELEASE,
    Scenario,
    jwk,
    sign,
)
from cryptography.hazmat.primitives.asymmetric import ec
from pydantic import ValidationError

from app.domains.recovery.contracts import AuthenticatedActor, Revocation
from app.domains.recovery.pairing_auth import (
    UnavailableAssertions,
    UnavailableClaims,
    verify_signature,
)
from app.domains.recovery.pairing_contracts import CLAIM_MS, DeviceClaim, PairingRequest
from app.domains.recovery.pairing_service import PairingService
from app.domains.recovery.sequence_service import SequenceGuard
from app.domains.recovery.service import RecoveryGuard
from app.domains.recovery.store import GuardDenied, GuardUnavailable


@pytest.fixture
def case(tmp_path):
    return Scenario(tmp_path)


def read(case, namespace, key):
    return case.store.transact(lambda tx: tx.get(namespace, key))


def revoke(case, device_id):
    challenge = case.service.revocation_challenge(device_id, case.subject, case.session)
    return case.service.revoke_device(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))


def test_success_consumes_candidate_then_distinct_device_challenge_and_only_identifies(case):
    request = case.requested()
    candidate = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    envelope = case.assertion(candidate)
    case.service.confirm_candidate(candidate["payload"]["challenge_id"], candidate["nonce"], envelope)
    confirmation = read(case, "pairing_confirmations", request["pairing_id"])
    row = read(case, "pairing_requests", request["pairing_id"])
    assert read(case, "pairing_challenges", candidate["payload"]["challenge_id"])["consumed_by"] == row["confirmation_sha256"]
    assert confirmation["assertion"]["assertion_id"] == envelope["payload"]["assertion_id"]
    proof = case.proof(request)
    result = case.service.complete_device(**proof)
    claim = DeviceClaim.model_validate(result["device_claim"]["payload"])
    verify_signature(jwk(case.claim_key), "device-claim", result["device_claim"]["payload"], result["device_claim"]["signature"])
    assert str(claim.subject_uuid) == case.subject and claim.operation == "device_identity"
    assert claim.expires_at_ms - claim.issued_at_ms == CLAIM_MS
    assert read(case, "pairing_challenges", proof["challenge_id"])["consumed_by"] == request["device_id"]
    assert read(case, "pairing_key_owners", request["key_sha256"]) == {
        "subject_uuid": case.subject, "device_id": request["device_id"],
    }
    assert set(case.service.status(request["pairing_id"])) == {"pairing_id", "device_id", "status"}
    # No actor, employer grant, approval, artifact binding or financial authority was created.
    with sqlite3.connect(case.store.path) as db:
        namespaces = {row[0] for row in db.execute("SELECT DISTINCT namespace FROM records")}
    assert namespaces.isdisjoint({"actors", "grants", "approvals", "sequence_approvals", "artifact_bindings", "claims", "permits", "sequence_attempts"})
    assert all(word not in json.dumps(result) for word in ("may_act", "callback", "application_id", "artifact", "answers", "credit"))
    actor = AuthenticatedActor(actor_id=request["device_id"], role="device", subject_uuid=case.subject,
        device_id=request["device_id"], key_sha256=request["key_sha256"], executor_revision=RELEASE)
    with pytest.raises(GuardDenied):
        case.store.transact(lambda tx: RecoveryGuard(case.store)._actor(tx, actor, {"device"}))
    # A successful pair supplies no sequence actor or sealed attempt to approve/begin/read.
    sequence = SequenceGuard(case.store, lambda: case.now)
    with pytest.raises(GuardDenied):
        sequence.status(actor, uuid4())
    with pytest.raises(GuardDenied):
        sequence.approve(actor, uuid4(), str(uuid4()), "0" * 64)
    with pytest.raises(GuardDenied):
        sequence.prepare(actor, uuid4(), 0, None)
    with pytest.raises(GuardDenied):
        sequence.begin(actor, str(uuid4()), "0" * 64, None)
    with pytest.raises(GuardDenied):
        case.store.transact(lambda tx: sequence.guard._actor(tx, actor, {"device"}))
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert "device_claim" not in case.service.status(request["pairing_id"])


def test_defaults_have_no_store_auth_or_claim_fallback(case, monkeypatch):
    for name in ("DATABASE_URL", "RECOVERY_STORE_PATH", "PAIRING_AUTH_SECRET"):
        monkeypatch.setenv(name, str(case.store.path))
    with pytest.raises(GuardUnavailable):
        PairingService().prepare_request(jwk(case.device_key), EXTENSION, RELEASE)
    request = case.requested()
    challenge = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    absent_auth = PairingService(case.store, now_ms=lambda: case.now)
    assert isinstance(absent_auth.assertions, UnavailableAssertions)
    assert isinstance(absent_auth.claims, UnavailableClaims)
    with pytest.raises(GuardUnavailable):
        absent_auth.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))
    assert case.service.status(request["pairing_id"])["status"] == "REQUESTED"


def test_candidate_only_and_device_only_cannot_complete(case):
    request = case.requested()
    with pytest.raises(GuardDenied):
        case.service.device_challenge(request["pairing_id"])
    challenge = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    forged = case.assertion(challenge)
    forged["signature"] = sign(case.device_key, "candidate-assertion", forged["payload"])
    with pytest.raises(GuardDenied):
        case.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], forged)
    case.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))
    proof = case.proof(request)
    device_challenge = read(case, "pairing_challenges", proof["challenge_id"])
    proof["signature"] = sign(case.auth_key, "device-challenge", device_challenge["payload"])
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert case.service.status(request["pairing_id"])["status"] == "CANDIDATE_CONFIRMED"


@pytest.mark.parametrize("field,value", [
    ("subject_uuid", str(uuid4())), ("session_id", str(uuid4())), ("principal_sha256", "1" * 64),
    ("auth_generation", 2), ("binding_sha256", "2" * 64), ("audience", "another-audience"),
    ("operation", "revoke_device"), ("confirmed", False),
])
def test_signed_wrong_context_is_denied_before_consumption(case, field, value):
    request = case.requested()
    challenge = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    envelope = case.assertion(challenge, **{field: value})
    with pytest.raises(GuardDenied):
        case.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], envelope)
    assert read(case, "pairing_challenges", challenge["payload"]["challenge_id"])["consumed_by"] is None


@pytest.mark.parametrize("mutation", ["auth_generation", "principal", "session", "deleted", "epoch", "extension"])
def test_current_state_changes_after_confirm_deny_completion(case, mutation):
    request = case.confirmed()
    proof = case.proof(request)
    subject = read(case, "subjects", case.subject)
    if mutation == "auth_generation":
        case.store.seed([("subjects", case.subject, {**subject, "auth_generation": 2})])
    elif mutation == "principal":
        case.store.seed([("subjects", case.subject, {**subject, "principal_sha256": "3" * 64})])
    elif mutation == "session":
        session = read(case, "pairing_sessions", case.session)
        case.store.seed([("pairing_sessions", case.session, {**session, "active": False})])
    elif mutation == "deleted":
        scope = Revocation(subject_uuid=UUID(case.subject), kind="subject", target=case.subject, revision=0)
        case.store.seed([("revocations", scope.key, {"scope": scope.model_dump(mode="json")})])
    elif mutation == "epoch":
        control = read(case, "control", "current")
        case.store.seed([("control", "current", {**control, "epoch_id": str(uuid4()), "generation": 2})])
    else:
        case.store.seed([("pairing_extensions", f"{EXTENSION}:{RELEASE}", {"active": False})])
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert read(case, "devices", request["device_id"]) is None


@pytest.mark.parametrize("phase", ["create", "candidate", "device", "refresh", "request"])
def test_exact_expiry_is_closed_and_plus_one_never_revives(case, phase):
    if phase == "create":
        initial = case.service.prepare_request(jwk(case.device_key), EXTENSION, RELEASE)
        request = initial["request"]
        attempt = partial(case.service.create_request, request["pairing_id"], initial["nonce"], sign(case.device_key, "request", request))
        deadline = case.now + 10_000
    elif phase == "candidate":
        request = case.requested()
        challenge = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
        envelope = case.assertion(challenge)
        attempt = partial(case.service.confirm_candidate, challenge["payload"]["challenge_id"], challenge["nonce"], envelope)
        deadline = challenge["payload"]["expires_at_ms"]
    elif phase == "refresh":
        request, _ = case.complete()
        challenge = case.service.refresh_challenge(request["device_id"])
        signature = sign(case.device_key, "device-challenge", challenge["payload"])
        attempt = partial(case.service.refresh_claim, challenge["payload"]["challenge_id"], challenge["nonce"], signature)
        deadline = challenge["payload"]["expires_at_ms"]
    else:
        request = case.confirmed()
        if phase == "request":
            case.now = request["expires_at_ms"] - 1
        proof = case.proof(request)
        attempt = partial(case.service.complete_device, **proof)
        deadline = request["expires_at_ms"] if phase == "request" else case.now + 10_000
    case.now = deadline
    with pytest.raises(GuardDenied):
        attempt()
    case.now = deadline + 1
    with pytest.raises(GuardDenied):
        attempt()


def test_creation_challenge_and_request_digest_cannot_cross_pairings(case):
    first = case.service.prepare_request(jwk(case.device_key), EXTENSION, RELEASE)
    second = case.service.prepare_request(jwk(case.device_key), EXTENSION, RELEASE)
    with pytest.raises(GuardDenied):
        case.service.create_request(second["request"]["pairing_id"], first["nonce"], sign(case.device_key, "request", first["request"]))
    with pytest.raises(GuardDenied):
        case.service.prepare_request(jwk(case.device_key), EXTENSION, "unapproved_release")
    altered = {**first["request"], "audience": "other"}
    with pytest.raises(GuardDenied):
        case.service.create_request(first["request"]["pairing_id"], first["nonce"], sign(case.device_key, "request", altered))


def test_consumed_candidate_challenge_is_bound_to_exact_confirmation(case):
    request = case.confirmed()
    confirmation = read(case, "pairing_confirmations", request["pairing_id"])
    challenge = read(case, "pairing_challenges", confirmation["challenge_id"])
    case.store.seed([("pairing_challenges", confirmation["challenge_id"], {**challenge, "consumed_by": "4" * 64})])
    with pytest.raises(GuardDenied, match="exact confirmation"):
        case.service.device_challenge(request["pairing_id"])


def test_duplicate_confirmation_and_assertion_replay_cannot_change_owner(case):
    request = case.requested()
    first = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    envelope = case.assertion(first)
    case.service.confirm_candidate(first["payload"]["challenge_id"], first["nonce"], envelope)
    with pytest.raises(GuardDenied):
        case.service.confirm_candidate(first["payload"]["challenge_id"], first["nonce"], envelope)
    assert read(case, "pairing_confirmations", request["pairing_id"])["context"]["subject_uuid"] == case.subject


def test_completion_reply_loss_requires_distinct_refresh_and_rechecks_revoke(case):
    request = case.confirmed()
    proof = case.proof(request)
    class LostClaims:
        def sign(self, payload):
            raise GuardUnavailable("Synthetic signer/reply outage")
    case.service.claims = LostClaims()
    with pytest.raises(GuardUnavailable):
        case.service.complete_device(**proof)
    assert case.service.status(request["pairing_id"])["status"] == "COMPLETED"
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert "device_claim" not in case.service.status(request["pairing_id"])
    from backend.tests.fixtures.pairing_authority import FixtureClaims
    case.service.claims = FixtureClaims(case.claim_key)
    challenge = case.service.refresh_challenge(request["device_id"])
    signature = sign(case.device_key, "device-challenge", challenge["payload"])
    result = case.service.refresh_claim(challenge["payload"]["challenge_id"], challenge["nonce"], signature)
    assert result["status"] == "IDENTIFIED"
    with pytest.raises(GuardDenied):
        case.service.refresh_claim(challenge["payload"]["challenge_id"], challenge["nonce"], signature)
    pending = case.service.refresh_challenge(request["device_id"])
    revoke(case, request["device_id"])
    with pytest.raises(GuardDenied):
        case.service.refresh_claim(pending["payload"]["challenge_id"], pending["nonce"], sign(case.device_key, "device-challenge", pending["payload"]))
    assert case.service.status(request["pairing_id"])["status"] == "REVOKED"


def test_cancellation_before_complete_denies_proof_and_key_ownership_is_never_reassigned(case):
    request = case.confirmed()
    proof = case.proof(request)
    revoke(case, request["device_id"])
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert read(case, "devices", request["device_id"]) is None
    # An uncompleted cancelled key was never owned; a fresh request is permitted.
    next_request, _ = case.complete()
    original_owner = read(case, "pairing_key_owners", next_request["key_sha256"])
    revoke(case, next_request["device_id"])
    other_subject, other_session = case.store.subject()
    replacement = case.confirmed(subject=other_subject, session=other_session)
    with pytest.raises(GuardDenied, match="permanently claimed"):
        case.service.complete_device(**case.proof(replacement))
    assert read(case, "pairing_key_owners", next_request["key_sha256"]) == original_owner
    # New key + explicit current-owner confirmation can enroll; old deny is retained.
    case.device_key = ec.generate_private_key(ec.SECP256R1())
    fresh, result = case.complete()
    assert result["status"] == "COMPLETED" and fresh["key_sha256"] != next_request["key_sha256"]
    assert read(case, "pairing_device_tombstones", next_request["device_id"]) is not None


def test_cross_owner_cannot_revoke_active_or_pending_enrollment(case):
    request = case.confirmed()
    other_subject, other_session = case.store.subject()
    for completed in (False, True):
        if completed:
            case.service.complete_device(**case.proof(request))
        challenge = case.service.revocation_challenge(request["device_id"], other_subject, other_session)
        with pytest.raises(GuardDenied, match="another owner"):
            case.service.revoke_device(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))
    assert case.service.status(request["pairing_id"])["status"] == "COMPLETED"


@pytest.mark.parametrize("damage", ["missing-file", "wrong-authority", "missing-partition", "journal-only", "projection-only", "closed"])
def test_partial_restore_or_missing_authority_never_reconstructs_pairing(case, damage):
    request, _ = case.complete()
    if damage == "missing-file":
        case.store.path.rename(case.store.path.with_suffix(".retained"))
    elif damage == "wrong-authority":
        case.store.authority_id = str(uuid4())
    elif damage == "closed":
        control = read(case, "control", "current")
        case.store.seed([("control", "current", {**control, "status": "CLOSED"})])
    else:
        with sqlite3.connect(case.store.path) as db:
            if damage == "missing-partition":
                db.execute("DELETE FROM records WHERE namespace='subjects'")
            elif damage == "journal-only":
                db.execute("DELETE FROM records WHERE namespace='devices'")
            else:
                db.execute("UPDATE records SET payload=? WHERE namespace='devices'", (json.dumps({"active": True}),))
    with pytest.raises(GuardUnavailable):
        case.service.refresh_challenge(request["device_id"])


def test_request_claim_protocol_integer_uuid_and_bound_are_exact(case):
    request = case.requested()
    for invalid in (2.0, "2", True):
        with pytest.raises(ValidationError):
            PairingRequest.model_validate({**request, "protocol_version": invalid})
    with pytest.raises(ValidationError):
        PairingRequest.model_validate({**request, "pairing_id": request["pairing_id"].upper()})
    with pytest.raises(ValidationError):
        PairingRequest.model_validate({**request, "expires_at_ms": request["issued_at_ms"] + 120_001})
    _, result = case.complete()
    claim = result["device_claim"]["payload"]
    with pytest.raises(ValidationError):
        DeviceClaim.model_validate({**claim, "expires_at_ms": claim["issued_at_ms"] + CLAIM_MS + 1})
    with pytest.raises(ValidationError):
        DeviceClaim.model_validate({**claim, "may_act": True})


@pytest.mark.parametrize("change", ["session", "expiry", "epoch", "generation", "principal"])
def test_fresh_owner_can_cancel_stopped_historical_confirmation(case, change):
    request = case.confirmed()
    proof = case.proof(request)
    subject = read(case, "subjects", case.subject)
    session = read(case, "pairing_sessions", case.session)
    new_session = str(uuid4())
    updates = [("pairing_sessions", case.session, {**session, "active": False})]
    if change == "expiry":
        case.now += 120_001
    elif change == "epoch":
        control = read(case, "control", "current")
        updates.append(("control", "current", {**control, "epoch_id": str(uuid4()), "generation": 2}))
    elif change == "generation":
        subject = {**subject, "auth_generation": 2}
        updates.append(("subjects", case.subject, subject))
    elif change == "principal":
        subject = {**subject, "principal_sha256": "5" * 64}
        updates.append(("subjects", case.subject, subject))
    updates.append(("pairing_sessions", new_session, {"active": True, "subject_uuid": case.subject,
                    "principal_sha256": subject["principal_sha256"], "auth_generation": subject["auth_generation"]}))
    case.store.seed(updates)
    challenge = case.service.revocation_challenge(request["device_id"], case.subject, new_session)
    assert case.service.revoke_device(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))["status"] == "REVOKED"
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
    assert read(case, "pairing_device_tombstones", request["device_id"]) is not None


@pytest.mark.parametrize("change", ["generation", "principal"])
def test_fresh_assertion_cannot_use_old_revocation_generation_context(case, change):
    request, _ = case.complete()
    challenge = case.service.revocation_challenge(request["device_id"], case.subject, case.session)
    subject = read(case, "subjects", case.subject)
    if change == "generation":
        subject = {**subject, "auth_generation": 2}
    else:
        subject = {**subject, "principal_sha256": "6" * 64}
    case.store.seed([("subjects", case.subject, subject), ("pairing_sessions", case.session,
        {"subject_uuid": case.subject, "principal_sha256": subject["principal_sha256"],
         "auth_generation": subject["auth_generation"], "active": True})])
    envelope = case.assertion(challenge, auth_generation=subject["auth_generation"], principal_sha256=subject["principal_sha256"])
    with pytest.raises(GuardDenied, match="challenge principal/session"):
        case.service.revoke_device(challenge["payload"]["challenge_id"], challenge["nonce"], envelope)
    assert read(case, "pairing_device_tombstones", request["device_id"]) is None


def test_candidate_challenge_is_not_reusable_for_another_request_or_nonce(case):
    first, second = case.requested(), case.requested()
    challenge = case.service.candidate_challenge(first["pairing_id"], case.subject, case.session)
    other = case.service.candidate_challenge(second["pairing_id"], case.subject, case.session)
    for wrong_id, nonce in ((other["payload"]["challenge_id"], challenge["nonce"]),
                            (challenge["payload"]["challenge_id"], str(uuid4()))):
        with pytest.raises(GuardDenied):
            case.service.confirm_candidate(wrong_id, nonce, case.assertion(challenge))
    case.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))
    assert case.service.status(second["pairing_id"])["status"] == "REQUESTED"


@pytest.mark.parametrize("namespace,field,value", [
    ("subjects", "auth_generation", True), ("subjects", "auth_generation", 1.0),
    ("subjects", "registration_event_id", str(uuid4())),
    ("pairing_sessions", "auth_generation", True), ("pairing_sessions", "auth_generation", 1.0),
    ("pairing_sessions", "active", 1),
    ("pairing_extensions", "protocol_version", 2.0),
])
def test_registered_metadata_requires_exact_types_and_subject_provenance(case, namespace, field, value):
    request = case.requested()
    challenge = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    key = {"subjects": case.subject, "pairing_sessions": case.session,
           "pairing_extensions": f"{EXTENSION}:{RELEASE}"}[namespace]
    record = read(case, namespace, key)
    case.store.seed([(namespace, key, {**record, field: value})])
    with pytest.raises(GuardDenied):
        case.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], case.assertion(challenge))
    assert read(case, "pairing_challenges", challenge["payload"]["challenge_id"])["consumed_by"] is None


def test_projection_boolean_equality_cannot_hide_tampering(case):
    request = case.confirmed()
    proof = case.proof(request)
    subject = read(case, "subjects", case.subject)
    # Python True == 1; canonical bytes must detect this non-journaled type replacement.
    with sqlite3.connect(case.store.path) as db:
        db.execute("UPDATE records SET payload=? WHERE namespace='subjects' AND key=?",
                   (json.dumps({**subject, "auth_generation": True}), case.subject))
    with pytest.raises(GuardUnavailable, match="partitions disagree"):
        case.service.complete_device(**proof)


@pytest.mark.parametrize("partition", ["pairing_key_owners", "pairing_device_tombstones", "revocations"])
def test_retained_revocation_journal_with_missing_partition_stays_closed(case, partition):
    request, _ = case.complete()
    revoke(case, request["device_id"])
    with sqlite3.connect(case.store.path) as db:
        db.execute("DELETE FROM records WHERE namespace=?", (partition,))
    with pytest.raises(GuardUnavailable):
        case.service.refresh_challenge(request["device_id"])
    with pytest.raises(GuardUnavailable):
        case.service.status(request["pairing_id"])


def test_nonce_values_are_returned_once_but_only_hashes_are_retained(case):
    initial = case.service.prepare_request(jwk(case.device_key), EXTENSION, RELEASE)
    request = initial["request"]
    case.service.create_request(request["pairing_id"], initial["nonce"], sign(case.device_key, "request", request))
    candidate = case.service.candidate_challenge(request["pairing_id"], case.subject, case.session)
    case.service.confirm_candidate(candidate["payload"]["challenge_id"], candidate["nonce"], case.assertion(candidate))
    device = case.service.device_challenge(request["pairing_id"])
    case.service.complete_device(device["payload"]["challenge_id"], device["nonce"], sign(case.device_key, "device-challenge", device["payload"]))
    with sqlite3.connect(case.store.path) as db:
        retained = json.dumps(list(db.execute("SELECT payload FROM records")) + list(db.execute("SELECT payload FROM events")))
    for challenge in (initial, candidate, device):
        assert challenge["nonce"] not in retained


def test_fresh_repeated_revocation_is_monotonic_and_never_recalls_identity_receipt(case):
    request, old_receipt = case.complete()
    first = revoke(case, request["device_id"])
    retained = read(case, "pairing_device_tombstones", request["device_id"])
    second = revoke(case, request["device_id"])
    assert second["event_sequence"] > first["event_sequence"]
    assert read(case, "pairing_device_tombstones", request["device_id"]) == retained
    assert read(case, "pairing_key_owners", request["key_sha256"]) == {
        "subject_uuid": case.subject, "device_id": request["device_id"],
    }
    verify_signature(jwk(case.claim_key), "device-claim", old_receipt["device_claim"]["payload"], old_receipt["device_claim"]["signature"])
    # Existing signed receipt remains bytes; only the current authority supplies denial.
    with pytest.raises(GuardDenied):
        case.service.refresh_challenge(request["device_id"])


def test_commit_ambiguity_does_not_return_a_signed_claim_without_confirmed_store_result(case):
    request = case.confirmed()
    proof = case.proof(request)
    class AmbiguousStore:
        def transact(self, operation):
            # Delegate a real committed transaction; emulate losing its verified result.
            result = case.store.transact(operation)
            if isinstance(result, dict) and result.get("operation") == "device_identity":
                raise GuardUnavailable("Synthetic ambiguous committed completion")
            return result
    class CountingClaims:
        def __init__(self):
            self.calls = 0
        def sign(self, payload):
            self.calls += 1
            raise AssertionError("No confirmed store result must reach issuer")
    signer = CountingClaims()
    isolated = PairingService(AmbiguousStore(), assertions=case.service.assertions, claims=signer,
                              now_ms=lambda: case.now)
    with pytest.raises(GuardUnavailable):
        isolated.complete_device(**proof)
    assert signer.calls == 0
    assert case.service.status(request["pairing_id"])["status"] == "COMPLETED"
    with pytest.raises(GuardDenied):
        case.service.complete_device(**proof)
