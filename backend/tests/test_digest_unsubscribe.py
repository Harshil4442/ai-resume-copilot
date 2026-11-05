"""Purpose-bound crypto checks; one-use redemption remains the native CAS."""
import base64
import hashlib
import hmac
import json
import socket
from dataclasses import replace

import pytest

from app.domains.candidate_accounts.contracts import CandidateAccountIdentity
from app.domains.notification_consent.unsubscribe import (
    MAX_LIFETIME_MS,
    PURPOSE,
    UnsubscribeTokens,
    VerifiedUnsubscribeProof,
)
from app.domains.recovery.store import GuardDenied

SECRET = b"synthetic-unsubscribe-fixture-key-01"
NOW = 1793862000000
IDENTITY = CandidateAccountIdentity.model_validate_json(
    '{"candidate_id":11,"subject_uuid":"11111111-1111-4111-8111-111111111111",'
    '"account_binding_id":"22222222-2222-4222-8222-222222222222","principal_sha256":"' + "a" * 64 + '"}')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Unsubscribe codec cannot use a provider")
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)


def token(codec=None, **changes):
    codec = codec or UnsubscribeTokens(SECRET)
    values = dict(email_sha256="b" * 64, expected_generation=2, policy_version="digest-v1", now_ms=NOW)
    return codec.issue(IDENTITY, **(values | changes))


def encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def payload(value):
    segment = value.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def signed(raw, *, purpose=PURPOSE):
    body = encode(raw)
    key = hmac.new(SECRET, purpose, hashlib.sha256).digest()
    signature = hmac.new(key, b"hwdu1." + body.encode(), hashlib.sha256).digest()
    return "hwdu1." + body + "." + encode(signature)


def test_round_trip_binds_exact_generation_recipient_and_identity_without_login_authority():
    codec = UnsubscribeTokens(SECRET)
    value = token(codec)
    proof = codec.verify(value, now_ms=NOW)
    assert type(proof) is VerifiedUnsubscribeProof
    assert proof.identity == IDENTITY and proof.expected_generation == 2
    assert proof.email_sha256 == "b" * 64 and proof.policy_version == "digest-v1"
    assert proof.proof_sha256 == hashlib.sha256(value.encode()).hexdigest()
    assert not hasattr(proof, "context") and not hasattr(proof, "runtime_authority")
    assert "11111111" not in repr(proof) and value not in repr(proof)
    assert set(payload(value)) == {"version", "purpose", "audience", "identity", "email_sha256",
        "expected_generation", "policy_version", "issued_at_ms", "expires_at_ms", "proof_id"}
    assert not {"email", "password", "credential_sha256", "auth_generation", "enabled"} & set(payload(value))


def test_native_writer_must_recheck_expiry_after_verification():
    proof = UnsubscribeTokens(SECRET).verify(token(lifetime_ms=1000), now_ms=NOW)
    proof.require_current(NOW + 999)
    with pytest.raises(GuardDenied):
        proof.require_current(NOW + 1000)


def test_plain_input_cannot_construct_verified_proof():
    with pytest.raises(GuardDenied):
        VerifiedUnsubscribeProof(IDENTITY, "b" * 64, 2, "digest-v1", NOW, NOW + 1000, "c" * 64, object())


@pytest.mark.parametrize("changes", [{"expected_generation": 3}, {"email_sha256": "c" * 64},
    {"policy_version": "other-policy"}, {"expires_at_ms": NOW + 999999999},
    {"proof_sha256": "c" * 64}, {"identity": IDENTITY.model_copy(update={"candidate_id": 12})}])
def test_verified_python_port_cannot_clone_new_claims_while_reusing_original_seal(changes):
    proof = UnsubscribeTokens(SECRET).verify(token(), now_ms=NOW)
    with pytest.raises(GuardDenied):
        replace(proof, **changes)


def test_original_seal_cannot_be_retargeted_by_ordinary_attribute_assignment():
    proof = UnsubscribeTokens(SECRET).verify(token(), now_ms=NOW)
    with pytest.raises(AttributeError):
        proof._seal._proof_sha256 = "c" * 64
    proof.require_current(NOW)


@pytest.mark.parametrize("change", [
    {"purpose": "password_reset"}, {"audience": "hirewiz-login"}, {"version": 2},
    {"enabled": True}, {"expected_generation": True}, {"expires_at_ms": NOW},
    {"issued_at_ms": NOW + 1}, {"expires_at_ms": NOW + MAX_LIFETIME_MS + 1},
    {"email_sha256": "B" * 64}, {"proof_id": "11111111-1111-1111-8111-111111111111"},
])
def test_even_correct_mac_cannot_cross_purpose_schema_time_or_generation_boundaries(change):
    value = payload(token()) | change
    raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(GuardDenied):
        UnsubscribeTokens(SECRET).verify(signed(raw), now_ms=NOW)


def test_duplicate_fields_and_noncanonical_json_are_rejected_with_a_correct_mac():
    data = payload(token())
    duplicate = json.dumps(data, separators=(",", ":"))[:-1] + ',"expected_generation":2}'
    for raw in (duplicate.encode(), json.dumps(data, indent=2).encode()):
        with pytest.raises(GuardDenied):
            UnsubscribeTokens(SECRET).verify(signed(raw), now_ms=NOW)


def test_tampering_wrong_key_and_other_service_mac_never_validate():
    value = token()
    prefix, body, signature = value.split(".")
    altered = payload(value) | {"expected_generation": 3}
    changed_body = encode(json.dumps(altered, sort_keys=True, separators=(",", ":")).encode())
    for attack in (prefix + "." + changed_body + "." + signature,
                   prefix + "." + body + "." + signature[:-1] + ("A" if signature[-1] != "A" else "B"),
                   signed(json.dumps(payload(value), sort_keys=True, separators=(",", ":")).encode(), purpose=b"hirewiz:password-reset:v1")):
        with pytest.raises(GuardDenied) as error:
            UnsubscribeTokens(SECRET).verify(attack, now_ms=NOW)
        assert attack not in str(error.value)
    with pytest.raises(GuardDenied):
        UnsubscribeTokens(b"different-synthetic-test-key-32-bytes").verify(value, now_ms=NOW)


@pytest.mark.parametrize("value", ["", "hwdu1.x", "hwdu1.x.y.z", "hwdu1.===.===", "x" * 4097, None, True])
def test_malformed_or_oversized_tokens_do_not_reach_a_native_writer(value):
    with pytest.raises(GuardDenied):
        UnsubscribeTokens(SECRET).verify(value, now_ms=NOW)


@pytest.mark.parametrize("changes", [{"lifetime_ms": 0}, {"lifetime_ms": True},
    {"lifetime_ms": MAX_LIFETIME_MS + 1}, {"expected_generation": 0},
    {"expected_generation": True}, {"now_ms": True}, {"now_ms": 2**53 - 1}])
def test_issuer_refuses_invalid_bounds(changes):
    with pytest.raises(GuardDenied):
        token(**changes)


def test_verifier_refuses_expired_future_and_padded_wire_encodings():
    codec = UnsubscribeTokens(SECRET)
    value = token(lifetime_ms=1000)
    for now in (NOW - 1, NOW + 1000):
        with pytest.raises(GuardDenied):
            codec.verify(value, now_ms=now)
    with pytest.raises(GuardDenied):
        codec.verify(value + "=", now_ms=NOW)
