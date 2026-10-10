"""Actual Cloud KMS SDK/protobuf boundary, intercepted gRPC and synthetic keys.

No real KMS, credentials, secure channel, emulator or shared database is used.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import traceback
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import google.auth
import google_crc32c
import grpc
import pytest
from backend.tests.fixtures.pairing_authority import Scenario, jwk
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
    encode_dss_signature,
)
from google.api_core.exceptions import DeadlineExceeded, ServiceUnavailable
from google.cloud import kms_v1

from app.domains.recovery.pairing_auth import (
    PinnedAssertions,
    UnavailableAssertions,
    UnavailableClaims,
    production_assertions,
    production_claims,
    verify_signature,
)
from app.domains.recovery.pairing_contracts import DeviceClaim, canonical_bytes
from app.domains.recovery.pairing_kms import (
    KmsCandidateAssertionIssuer,
    KmsEs256Boundary,
    KmsEs256Pin,
    KmsPairingClaimIssuer,
    _checked_crc,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable

NAME = "projects/hirewiz-local-authority/locations/us-central1/keyRings/pairing/cryptoKeys/identity/cryptoKeyVersions/7"
NOW = 1_800_000_000_000
ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def assertion(**changes):
    return {"protocol_version": 2, "assertion_id": str(uuid4()), "issuer": "fixture_auth",
        "audience": "hirewiz:pairing-only", "operation": "confirm_pairing", "subject_uuid": str(uuid4()),
        "auth_generation": 1, "principal_sha256": "a" * 64, "session_id": str(uuid4()),
        "method": "password_reauth", "authenticated_at_ms": NOW - 1000, "issued_at_ms": NOW,
        "expires_at_ms": NOW + 60_000, "binding_sha256": "b" * 64, "confirmed": True, **changes}


def claim(**changes):
    return DeviceClaim(claim_id=uuid4(), device_id=uuid4(), subject_uuid=uuid4(), key_sha256="c" * 64,
        key_generation=1, auth_generation=1, authority_id=uuid4(), authority_incarnation=1,
        epoch_id=uuid4(), epoch_generation=1, extension_id="fixture_extension", executor_revision="fixture_revision",
        event_sequence=2, event_sha256="d" * 64, issued_at_ms=NOW, expires_at_ms=NOW + 300_000,
    ).model_dump(mode="json") | changes


class InterceptedUnary(grpc.UnaryUnaryMultiCallable):
    def __init__(self, invoke):
        self.invoke = invoke

    def __call__(self, request, timeout=None, metadata=(), **kwargs):
        return self.invoke(request, timeout=timeout, metadata=metadata, **kwargs)

    def with_call(self, request, timeout=None, metadata=(), **kwargs):
        result = self(request, timeout=timeout, metadata=metadata, **kwargs)
        call = MagicMock(spec=grpc.Call)
        call.code.return_value = grpc.StatusCode.OK
        call.initial_metadata.return_value = ()
        call.trailing_metadata.return_value = ()
        return result, call

    def future(self, *args, **kwargs):
        raise AssertionError("No asynchronous KMS request is authorized")


class InterceptedKmsChannel(grpc.Channel):
    """The generated SDK serializes requests and deserializes replies here."""

    def __init__(self, fixture):
        self.fixture, self.closed = fixture, False

    def unary_unary(self, method, request_serializer, response_deserializer, *args, **kwargs):
        def rpc(request, *, timeout=None, metadata=(), **options):
            assert 0 < timeout <= 2.0
            raw = request_serializer(request)
            if method.endswith("/GetPublicKey"):
                request = kms_v1.GetPublicKeyRequest.deserialize(raw)
                assert request.name == NAME and request.public_key_format == kms_v1.PublicKey.PublicKeyFormat.PUBLIC_KEY_FORMAT_UNSPECIFIED
                self.fixture.calls.append(("public", request, timeout))
                reply = self.fixture.public()
                if self.fixture.public_hook:
                    self.fixture.public_hook(reply)
                encoded = kms_v1.PublicKey.serialize(reply)
            elif method.endswith("/AsymmetricSign"):
                request = kms_v1.AsymmetricSignRequest.deserialize(raw)
                self.fixture.calls.append(("sign", request, timeout))
                assert request.name == NAME and request.digest.sha256 and not request.data
                assert request.digest_crc32c == google_crc32c.value(request.digest.sha256)
                assert request.data_crc32c is None
                if self.fixture.failure:
                    raise self.fixture.failure
                hashed = request.digest.sha256
                if self.fixture.double_hash:
                    hashed = hashlib.sha256(hashed).digest()
                der = self.fixture.sign_key.sign(hashed, ec.ECDSA(Prehashed(hashes.SHA256())))
                reply = kms_v1.AsymmetricSignResponse(signature=der, signature_crc32c=google_crc32c.value(der),
                    verified_digest_crc32c=True, name=NAME, protection_level=kms_v1.ProtectionLevel.SOFTWARE)
                if self.fixture.sign_hook:
                    self.fixture.sign_hook(reply)
                encoded = kms_v1.AsymmetricSignResponse.serialize(reply)
            else:
                raise AssertionError("No other KMS RPC is authorized by this adapter")
            assert dict(metadata)["x-goog-request-params"].startswith("name=projects/")
            return response_deserializer(encoded)
        return InterceptedUnary(rpc)

    def close(self):
        self.closed = True

    def subscribe(self, callback, try_to_connect=False):
        raise AssertionError("No channel connection is authorized")

    def unsubscribe(self, callback):
        pass

    def unary_stream(self, *args, **kwargs):
        raise AssertionError("No streaming KMS call is authorized")

    def stream_unary(self, *args, **kwargs):
        raise AssertionError("No streaming KMS call is authorized")

    def stream_stream(self, *args, **kwargs):
        raise AssertionError("No streaming KMS call is authorized")


@pytest.fixture
def native(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("ADC or real cloud channel construction is forbidden")
    monkeypatch.setattr(google.auth, "default", forbidden)
    monkeypatch.setattr(grpc, "insecure_channel", forbidden)
    monkeypatch.setattr(grpc, "intercept_channel", forbidden)
    private = ec.generate_private_key(ec.SECP256R1())
    value = SimpleNamespace(private=private, sign_key=private, calls=[], public_hook=None,
                            sign_hook=None, failure=None, double_hash=False, now=NOW, monotonic=0.0)
    def public():
        pem = private.public_key().public_bytes(serialization.Encoding.PEM,
                                                serialization.PublicFormat.SubjectPublicKeyInfo).decode()
        return kms_v1.PublicKey(pem=pem, pem_crc32c=google_crc32c.value(pem.encode()), name=NAME,
            algorithm=kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P256_SHA256,
            protection_level=kms_v1.ProtectionLevel.SOFTWARE)
    value.public = public
    channel = InterceptedKmsChannel(value)
    value.channels = []
    def secure(target, credentials, *, options):
        value.channels.append((target, dict(options)))
        assert target in {"cloudkms.googleapis.com:443", "us-central1-cloudkms.googleapis.com:443"} and credentials is not None
        assert dict(options) == {"grpc.enable_retries": 0, "grpc.enable_http_proxy": 0,
                                "grpc.max_send_message_length": 16384, "grpc.max_receive_message_length": 16384}
        return channel
    monkeypatch.setattr(grpc, "secure_channel", secure)
    pin = KmsEs256Pin(NAME, "cloudkms.googleapis.com:443", "SOFTWARE", jwk(private))
    value.pin, value.channel = pin, channel
    value.boundary = KmsEs256Boundary(pin, access_token="fixture-token", monotonic=lambda: value.monotonic)
    value.assertions = KmsCandidateAssertionIssuer(value.boundary, issuer="fixture_auth", now_ms=lambda: value.now)
    value.claims = KmsPairingClaimIssuer(value.boundary, now_ms=lambda: value.now)
    try:
        yield value
    finally:
        value.boundary.close()
        assert channel.closed


def test_native_sdk_digest_crc_exact_version_raw64_and_existing_verifier(native):
    payload = assertion()
    envelope = native.assertions.sign(payload)
    raw = base64.urlsafe_b64decode(envelope["signature"] + "==")
    assert len(raw) == 64 and "=" not in envelope["signature"]
    assert envelope["payload"] == payload and envelope["payload"] is not payload
    assert [name for name, _, _ in native.calls] == ["public", "sign"]
    request = native.calls[1][1]
    assert request.digest.sha256 == hashlib.sha256(canonical_bytes("candidate-assertion", payload)).digest()
    verifier = PinnedAssertions(issuer="fixture_auth", public_key=jwk(native.private))
    assert verifier.verify(envelope, binding_sha256=payload["binding_sha256"], operation="confirm_pairing", now_ms=NOW)
    with pytest.raises(GuardDenied):
        verify_signature(jwk(native.private), "device-claim", payload, envelope["signature"])


def test_device_claim_seam_signs_only_actual_pairing_identity_schema(native):
    payload = claim()
    envelope = native.claims.sign(payload)
    verify_signature(jwk(native.private), "device-claim", payload, envelope["signature"])
    assert native.calls[-1][1].digest.sha256 == hashlib.sha256(canonical_bytes("device-claim", payload)).digest()
    with pytest.raises(GuardDenied):
        native.assertions.sign(payload)
    with pytest.raises(GuardDenied):
        native.claims.sign(assertion())


@pytest.mark.parametrize("changes", [
    {"issuer": "other_auth"}, {"audience": "hirewiz:fill"}, {"operation": "submit"},
    {"confirmed": 1}, {"auth_generation": True}, {"auth_generation": "1"},
    {"protocol_version": "2"}, {"issued_at_ms": float(NOW)}, {"issued_at_ms": NOW + 1},
    {"expires_at_ms": NOW}, {"expires_at_ms": NOW + 60_001}, {"authenticated_at_ms": NOW - 60_001},
    {"method": "bearer"}, {"role": "candidate"}, {"kms_key": NAME},
])
def test_candidate_scope_and_time_fail_before_any_rpc(native, changes):
    with pytest.raises(GuardDenied):
        native.assertions.sign(assertion(**changes))
    assert native.calls == []


@pytest.mark.parametrize("changes", [
    {"issuer": "other_pairing"}, {"audience": "hirewiz:fill"}, {"operation": "submit"},
    {"expires_at_ms": NOW + 300_001}, {"expires_at_ms": NOW}, {"issued_at_ms": NOW + 1},
    {"event_sequence": True}, {"permissions": ["fill"]},
])
def test_claim_scope_time_and_extra_permissions_fail_before_any_rpc(native, changes):
    with pytest.raises(GuardDenied):
        native.claims.sign(claim(**changes))
    assert native.calls == []


@pytest.mark.parametrize("fault", ["name", "algorithm", "protection", "missing_crc", "bad_crc", "empty", "wrong_key", "rsa", "p384"])
def test_public_key_integrity_or_identity_failure_never_calls_sign(native, fault):
    def corrupt(reply):
        if fault == "name":
            reply.name = NAME[:-1] + "8"
        elif fault == "algorithm":
            reply.algorithm = kms_v1.CryptoKeyVersion.CryptoKeyVersionAlgorithm.EC_SIGN_P384_SHA384
        elif fault == "protection":
            reply.protection_level = kms_v1.ProtectionLevel.HSM
        elif fault == "missing_crc":
            reply.pem_crc32c = None
        elif fault == "bad_crc":
            reply.pem_crc32c ^= 1
        elif fault == "empty":
            reply.pem = ""
        else:
            key = (rsa.generate_private_key(public_exponent=65537, key_size=2048) if fault == "rsa" else
                   ec.generate_private_key(ec.SECP384R1() if fault == "p384" else ec.SECP256R1()))
            reply.pem = key.public_key().public_bytes(serialization.Encoding.PEM,
                                                    serialization.PublicFormat.SubjectPublicKeyInfo).decode()
            reply.pem_crc32c = google_crc32c.value(reply.pem.encode())
    native.public_hook = corrupt
    with pytest.raises(GuardUnavailable):
        native.assertions.sign(assertion())
    assert [name for name, _, _ in native.calls] == ["public"]


@pytest.mark.parametrize("fault", ["name", "protection", "missing_crc", "bad_crc", "negative_crc", "overflow_crc", "false_flag", "bad_der", "zero", "order", "trailing"])
def test_checksum_valid_der_attacks_and_integrity_failures_never_emit_envelope(native, fault):
    def corrupt(reply):
        if fault == "name":
            reply.name = NAME[:-1] + "8"
        elif fault == "protection":
            reply.protection_level = kms_v1.ProtectionLevel.HSM
        elif fault == "missing_crc":
            reply.signature_crc32c = None
        elif fault == "bad_crc":
            reply.signature_crc32c ^= 1
        elif fault == "negative_crc":
            reply.signature_crc32c = -1
        elif fault == "overflow_crc":
            reply.signature_crc32c = 2**32
        elif fault == "false_flag":
            reply.verified_digest_crc32c = False
        else:
            reply.signature = {"bad_der": b"invalid-DER", "zero": encode_dss_signature(0, 1),
                               "order": encode_dss_signature(ORDER, 1), "trailing": reply.signature + b"\0"}[fault]
            reply.signature_crc32c = google_crc32c.value(reply.signature)
    native.sign_hook = corrupt
    with pytest.raises(GuardUnavailable):
        native.assertions.sign(assertion())
    assert [name for name, _, _ in native.calls] == ["public", "sign"]


@pytest.mark.parametrize("fault", ["wrong_signing_key", "double_hash"])
def test_locally_verified_signature_rejects_wrong_key_or_wrong_digest(native, fault):
    if fault == "wrong_signing_key":
        native.sign_key = ec.generate_private_key(ec.SECP256R1())
    else:
        native.double_hash = True
    with pytest.raises(GuardUnavailable):
        native.assertions.sign(assertion())
    assert len(native.calls) == 2


def test_valid_high_s_signature_remains_interoperable(native):
    def high_s(reply):
        r, s = decode_dss_signature(reply.signature)
        reply.signature = encode_dss_signature(r, max(s, ORDER - s))
        reply.signature_crc32c = google_crc32c.value(reply.signature)
    native.sign_hook = high_s
    payload = assertion(operation="revoke_device", method="provider_reauth")
    envelope = native.assertions.sign(payload)
    verify_signature(jwk(native.private), "candidate-assertion", payload, envelope["signature"])


@pytest.mark.parametrize("failure", [DeadlineExceeded("synthetic lost reply"), ServiceUnavailable("synthetic unavailable")])
def test_native_rpc_failures_are_single_attempt_and_return_no_signature(native, failure):
    native.failure = failure
    with pytest.raises(GuardUnavailable, match="no verified pairing signature"):
        native.assertions.sign(assertion())
    assert [name for name, _, _ in native.calls] == ["public", "sign"]


def test_public_key_rpc_unavailable_is_also_single_attempt(native):
    def fail(reply):
        raise ServiceUnavailable("Synthetic public key reply lost")
    native.public_hook = fail
    with pytest.raises(GuardUnavailable):
        native.assertions.sign(assertion())
    assert [name for name, _, _ in native.calls] == ["public"]


def test_remaining_operation_budget_caps_second_native_rpc_deadline(native):
    def spend(reply):
        native.monotonic = 4.75
    native.public_hook = spend
    assert native.assertions.sign(assertion())
    assert native.calls[0][2] <= 2.0 and 0 < native.calls[1][2] <= 0.25


@pytest.mark.parametrize("stage", ["public", "sign", "auth_expiry"])
def test_deadline_and_payload_expiry_close_after_rpc_without_output(native, stage):
    def close(reply):
        if stage == "auth_expiry":
            native.now += 60_000
        else:
            native.monotonic = 5.0
    if stage == "public":
        native.public_hook = close
    else:
        native.sign_hook = close
    with pytest.raises((GuardUnavailable, GuardDenied)):
        native.assertions.sign(assertion())
    assert len(native.calls) == (1 if stage == "public" else 2)


def test_caller_payload_and_key_mutation_cannot_change_signing_bytes(native):
    payload = assertion()
    original = payload.copy()
    def mutate(reply):
        payload["audience"] = "hirewiz:fill"
        native.pin.public_key.update(jwk(ec.generate_private_key(ec.SECP256R1())))
    native.public_hook = mutate
    envelope = native.assertions.sign(payload)
    assert envelope["payload"] == original and payload != original
    verify_signature(jwk(native.private), "candidate-assertion", original, envelope["signature"])


@pytest.mark.parametrize("changes", [
    {"version_name": NAME.rsplit("/", 2)[0]}, {"version_name": NAME + "?key=other"},
    {"version_name": NAME[:-1] + "07"}, {"endpoint": "attacker.example:443"},
    {"endpoint": "europe-west1-cloudkms.googleapis.com:443"}, {"endpoint": "http://cloudkms.googleapis.com"},
    {"protection_level": "EXTERNAL"}, {"public_key": {"kty": "RSA"}},
])
def test_pin_rejects_alias_user_url_wrong_region_or_non_p256_before_channel(native, changes):
    count = len(native.channels)
    with pytest.raises(GuardDenied):
        KmsEs256Boundary(replace(native.pin, **changes), access_token="fixture-token")
    assert len(native.channels) == count and native.calls == []


@pytest.mark.parametrize("options", [
    {"access_token": ""}, {"access_token": "fixture\nheader"}, {"rpc_timeout": True},
    {"rpc_timeout": float("inf")}, {"rpc_timeout": 0}, {"deadline_seconds": float("nan")},
    {"deadline_seconds": 11},
])
def test_credentials_and_finite_deadline_policy_never_fall_back_to_environment(native, options):
    count = len(native.channels)
    with pytest.raises(GuardDenied):
        KmsEs256Boundary(native.pin, **({"access_token": "fixture-token"} | options))
    assert len(native.channels) == count
    assert isinstance(production_assertions(), UnavailableAssertions)
    assert isinstance(production_claims(), UnavailableClaims)


def test_crc_wrapper_presence_zero_and_strict_uint32():
    _checked_crc(0, b"")
    for value in (None, True, False, -1, 2**32, "0", 0.0):
        with pytest.raises(GuardUnavailable):
            _checked_crc(value, b"")


def test_locational_endpoint_is_explicit_and_resource_location_bound(native):
    regional = KmsEs256Boundary(replace(native.pin, endpoint="us-central1-cloudkms.googleapis.com:443"),
                               access_token="fixture-token", monotonic=lambda: native.monotonic)
    try:
        envelope = KmsPairingClaimIssuer(regional, now_ms=lambda: native.now).sign(claim())
        verify_signature(jwk(native.private), "device-claim", envelope["payload"], envelope["signature"])
        assert native.channels[-1][0] == "us-central1-cloudkms.googleapis.com:443"
    finally:
        regional.close()


def test_environment_cannot_choose_endpoint_credentials_or_mtls_channel(native, monkeypatch):
    monkeypatch.setenv("GOOGLE_API_USE_MTLS_ENDPOINT", "always")
    monkeypatch.setenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "true")
    monkeypatch.setenv("HTTPS_PROXY", "https://attacker.invalid")
    explicit = KmsEs256Boundary(native.pin, access_token="fixture-token", monotonic=lambda: native.monotonic)
    try:
        assert native.channels[-1][0] == "cloudkms.googleapis.com:443"
        assert native.channels[-1][1]["grpc.enable_http_proxy"] == 0
        assert KmsCandidateAssertionIssuer(explicit, issuer="fixture_auth", now_ms=lambda: native.now).sign(assertion())
    finally:
        explicit.close()


def test_reusable_boundary_is_not_a_generic_digest_or_fill_signing_oracle(native):
    with pytest.raises(GuardDenied):
        native.boundary._sign("browser-approval", claim())
    with pytest.raises(GuardDenied):
        native.boundary._sign("device-claim", claim(audience="hirewiz:fill"))
    with pytest.raises(GuardDenied):
        native.boundary._sign("candidate-assertion", {"digest": "a" * 64})
    assert native.calls == []


def test_sdk_version_changes_fail_before_channel_or_network(native, monkeypatch):
    monkeypatch.setattr("app.domains.recovery.pairing_kms.importlib.metadata.version", lambda _: "unreviewed")
    before = len(native.channels)
    with pytest.raises(GuardUnavailable):
        KmsEs256Boundary(native.pin, access_token="fixture-token")
    assert len(native.channels) == before and native.calls == []


def test_actual_pairing_service_consumes_kms_assertion_and_returns_kms_claim(native, tmp_path):
    scenario = Scenario(tmp_path)
    scenario.service.assertions = PinnedAssertions(issuer="fixture_auth", public_key=jwk(native.private))
    scenario.service.claims = native.claims
    request = scenario.requested()
    challenge = scenario.service.candidate_challenge(request["pairing_id"], scenario.subject, scenario.session)
    payload = scenario.assertion(challenge)["payload"]
    envelope = native.assertions.sign(payload)
    scenario.service.confirm_candidate(challenge["payload"]["challenge_id"], challenge["nonce"], envelope)
    proof = scenario.proof(request)
    result = scenario.service.complete_device(**proof)
    verify_signature(jwk(native.private), "device-claim", result["device_claim"]["payload"], result["device_claim"]["signature"])
    assert result["device_claim"]["payload"]["subject_uuid"] == scenario.subject
    before = len(native.calls)
    with pytest.raises(GuardDenied):
        scenario.service.complete_device(**proof)
    assert len(native.calls) == before


@pytest.mark.parametrize("logger_mode", ["debug_before", "enable_during_rpc"])
@pytest.mark.parametrize("outcome", ["valid", "expired", "corrupt", "error"])
def test_debug_and_logging_config_races_never_log_signature_or_credential(native, caplog, logger_mode, outcome):
    logger_name = "google.cloud.kms_v1.services.key_management_service.transports.grpc"
    sdk_logger = logging.getLogger(logger_name)
    caplog.set_level(logging.DEBUG)
    caplog.set_level(logging.DEBUG if logger_mode == "debug_before" else logging.WARNING, logger=logger_name)
    payload = assertion()
    request_digest = hashlib.sha256(canonical_bytes("candidate-assertion", payload)).digest()
    synthetic_der = native.private.sign(request_digest, ec.ECDSA(Prehashed(hashes.SHA256())))
    secrets = ["fixture-token", base64.b64encode(synthetic_der).decode(), repr(synthetic_der),
               request_digest.hex(), base64.b64encode(request_digest).decode(), repr(request_digest),
               json.dumps(payload, sort_keys=True), payload["subject_uuid"], payload["session_id"]]
    captured_der = []
    def public_hook(reply):
        if logger_mode == "enable_during_rpc":
            sdk_logger.setLevel(logging.DEBUG)
    def sign_hook(reply):
        # This is the actual native response before adapter validation. Its raw
        # signature is valid even when integrity or current lifetime later fails.
        captured_der.append(reply.signature)
        r, s = decode_dss_signature(reply.signature)
        raw64 = base64.urlsafe_b64encode(r.to_bytes(32, "big") + s.to_bytes(32, "big")).rstrip(b"=").decode()
        verify_signature(jwk(native.private), "candidate-assertion", payload, raw64)
        secrets.extend([base64.b64encode(reply.signature).decode(), repr(reply.signature), raw64])
        if outcome == "expired":
            native.now += 60_000
        elif outcome == "corrupt":
            reply.signature_crc32c ^= 1
    native.public_hook, native.sign_hook = public_hook, sign_hook
    if outcome == "error":
        native.failure = ServiceUnavailable("synthetic remote response " + " ".join(secrets))
    caplog.clear()
    if outcome == "valid":
        assert native.assertions.sign(payload)
        exception_text = ""
    else:
        with pytest.raises((GuardDenied, GuardUnavailable)) as rejected:
            native.assertions.sign(payload)
        assert rejected.value.__cause__ is None and rejected.value.__context__ is None
        exception_text = "".join(traceback.format_exception(rejected.value)) + repr(rejected.value.args)
    assert len(native.calls) == 2
    assert bool(captured_der) == (outcome != "error")
    observed = caplog.text + exception_text
    assert not any(value in observed for value in secrets)
