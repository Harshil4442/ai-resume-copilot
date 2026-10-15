"""Real local crypto proofs; no identity provider, browser, network or production auth."""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from copy import deepcopy

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from app.domains.recovery.pairing_auth import (
    PinnedAssertions,
    UnavailableAssertions,
    UnavailableClaims,
    key_fingerprint,
    production_assertions,
    production_claims,
    public_jwk,
    verify_signature,
)
from app.domains.recovery.pairing_contracts import canonical_bytes, digest
from app.domains.recovery.store import GuardDenied, GuardUnavailable

NOW = 1_700_000_000_000
ORDER = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
VECTOR = {"nested": {"text": "A\n😀éé", "zero": 0},
          "list": [None, True, False, -7, 9_007_199_254_740_991], "unicode": "Ω"}


def b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def jwk(key: ec.EllipticCurvePrivateKey) -> dict:
    point = key.public_key().public_numbers()
    return {"kty": "EC", "crv": "P-256", "x": b64(point.x.to_bytes(32, "big")),
            "y": b64(point.y.to_bytes(32, "big"))}


def sign(key: ec.EllipticCurvePrivateKey, kind: str, payload: dict) -> str:
    der = key.sign(canonical_bytes(kind, payload), ec.ECDSA(hashes.SHA256()))
    r, s = decode_dss_signature(der)
    return b64(r.to_bytes(32, "big") + s.to_bytes(32, "big"))


def noncanonical(value: str) -> str:
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    return value[:-1] + alphabet[alphabet.index(value[-1]) + 1]


@pytest.fixture
def key():
    # Public deterministic test key, never a production signing credential.
    return ec.derive_private_key(1, ec.SECP256R1())


def assertion(**changes) -> dict:
    return {"protocol_version": 2, "assertion_id": "10000000-0000-4000-8000-000000000001",
            "issuer": "synthetic-auth", "audience": "hirewiz:pairing-only",
            "operation": "confirm_pairing", "subject_uuid": "20000000-0000-4000-8000-000000000001",
            "auth_generation": 3, "principal_sha256": "a" * 64,
            "session_id": "30000000-0000-4000-8000-000000000001",
            "method": "password_reauth", "authenticated_at_ms": NOW - 1000,
            "issued_at_ms": NOW, "expires_at_ms": NOW + 60_000,
            "binding_sha256": "b" * 64, "confirmed": True, **changes}


def envelope(key, payload=None) -> dict:
    payload = assertion() if payload is None else payload
    return {"payload": payload, "signature": sign(key, "candidate-assertion", payload)}


def check(verifier, value, **changes):
    return verifier.verify(value, **{"binding_sha256": "b" * 64,
                                   "operation": "confirm_pairing", "now_ms": NOW, **changes})


def test_public_key_shape_point_and_domain_fingerprint(key):
    value = jwk(key)
    assert public_jwk(value) == value
    assert public_jwk(value) is not value
    assert key_fingerprint(value) == digest("public-key", value)
    assert key_fingerprint(value) != digest("device-proof", value)


@pytest.mark.parametrize("changes", [
    {"d": "A" * 43}, {"alg": "ES256"}, {"kid": "untrusted"}, {"key_ops": "verify"},
    {"kty": "RSA"}, {"crv": "P-384"}, {"x": 1}, {"x": "A" * 42},
    {"x": "/" * 43}, {"x": "A" * 43 + "="}, {"x": " A" + "A" * 41},
    {"x": b64(bytes(32)), "y": b64(bytes(32))},
])
def test_public_keys_reject_private_extra_wrong_curve_encoding_and_invalid_points(key, changes):
    with pytest.raises(GuardDenied):
        public_jwk({**jwk(key), **changes})


def test_public_keys_reject_missing_fields_and_nonzero_unused_bits(key):
    value = jwk(key)
    missing = {name: item for name, item in value.items() if name != "y"}
    for malformed in [missing, [], None, {**value, "x": noncanonical(value["x"])}]:
        with pytest.raises(GuardDenied):
            public_jwk(malformed)


def test_real_detached_raw_es256_rejects_tampering_domain_and_wrong_key(key):
    signature = sign(key, "device-proof", VECTOR)
    verify_signature(jwk(key), "device-proof", VECTOR, signature)
    wrong_key = ec.generate_private_key(ec.SECP256R1())
    for public, kind, payload in [(jwk(key), "device-proof", {**VECTOR, "extra": True}),
                                 (jwk(key), "candidate-assertion", VECTOR),
                                 (jwk(wrong_key), "device-proof", VECTOR)]:
        with pytest.raises(GuardDenied):
            verify_signature(public, kind, payload, signature)


def test_raw_format_only_and_valid_high_s_is_not_a_replay_identity(key):
    signature = sign(key, "device-proof", VECTOR)
    raw = base64.urlsafe_b64decode(signature + "==")
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    for scalar in [s, ORDER - s]:
        verify_signature(jwk(key), "device-proof", VECTOR,
                         b64(r.to_bytes(32, "big") + scalar.to_bytes(32, "big")))
    der = key.sign(canonical_bytes("device-proof", VECTOR), ec.ECDSA(hashes.SHA256()))
    for malformed in [b64(der), b64(raw[:-1]), b64(raw + b"\0"), signature + "=",
                      noncanonical(signature), b64(bytes(64)),
                      b64(ORDER.to_bytes(32, "big") + raw[32:]),
                      b64(raw[:32] + ORDER.to_bytes(32, "big")), True, None]:
        with pytest.raises(GuardDenied):
            verify_signature(jwk(key), "device-proof", VECTOR, malformed)


@pytest.mark.parametrize("payload", [
    {"bad": 1.0}, {"bad": 2**53}, {"bad": -(2**53)}, {"bad": "\ud800"},
    {"é": "non-ASCII key"}, {"bad": (1, 2)}, ["not an envelope"],
])
def test_canonical_contract_rejects_ambiguous_types(payload):
    with pytest.raises((ValueError, UnicodeError)):
        canonical_bytes("device-proof", payload)


def test_canonical_domain_and_unicode_vector_is_frozen():
    expected = ('hirewiz.pairing.device-proof.v2\n'
                '{"list":[null,true,false,-7,9007199254740991],'
                '"nested":{"text":"A\\n😀éé","zero":0},"unicode":"Ω"}').encode()
    assert canonical_bytes("device-proof", VECTOR) == expected
    assert digest("device-proof", VECTOR) == "a340bd57d58a51deb17320c9bb5191d58c0c90ccb06f273aabc31a1f5ba37849"
    assert digest("device-proof", {"text": "é"}) != digest("device-proof", {"text": "é"})
    for kind in ["", "Device", "../device", "a" * 65, True]:
        with pytest.raises(ValueError):
            canonical_bytes(kind, VECTOR)


def test_pinned_trust_accepts_both_recent_auth_methods_and_freezes_key(key):
    public = jwk(key)
    verifier = PinnedAssertions(issuer="synthetic-auth", public_key=public)
    public.update(jwk(ec.generate_private_key(ec.SECP256R1())))
    for method in ["password_reauth", "provider_reauth"]:
        value = check(verifier, envelope(key, assertion(method=method)))
        assert str(value.subject_uuid) == assertion()["subject_uuid"]
        assert value.auth_generation == 3
        assert value.method == method


@pytest.mark.parametrize("changes", [
    {"issuer": "untrusted-auth"}, {"audience": "hirewiz:fill"},
    {"operation": "revoke_device"}, {"binding_sha256": "c" * 64},
    {"confirmed": False}, {"confirmed": 1}, {"auth_generation": True},
    {"auth_generation": "3"}, {"issued_at_ms": str(NOW)}, {"issued_at_ms": True},
    {"method": "bearer"}, {"session_id": "not-a-session"}, {"protocol_version": "2"},
    {"role": "candidate"}, {"issuer_key": "caller-supplied"},
])
def test_signed_assertions_reject_wrong_scope_extra_fields_and_coerced_identity(key, changes):
    verifier = PinnedAssertions(issuer="synthetic-auth", public_key=jwk(key))
    with pytest.raises(GuardDenied):
        check(verifier, envelope(key, assertion(**changes)))


def test_assertions_reject_unsigned_session_edits_missing_fields_and_envelope_keys(key):
    verifier = PinnedAssertions(issuer="synthetic-auth", public_key=jwk(key))
    tampered = envelope(key)
    tampered["payload"]["session_id"] = "40000000-0000-4000-8000-000000000001"
    missing = assertion()
    del missing["protocol_version"]
    for value in [tampered, envelope(key, missing), {**envelope(key), "jwk": jwk(key)},
                  {**envelope(key), "alg": "ES256"}, "generic-bearer", {},
                  {"payload": assertion(), "signature": "bad"}]:
        with pytest.raises(GuardDenied):
            check(verifier, value)


@pytest.mark.parametrize("changes", [
    {"authenticated_at_ms": NOW - 60_001}, {"issued_at_ms": NOW + 1},
    {"authenticated_at_ms": NOW + 1, "issued_at_ms": NOW + 1},
    {"expires_at_ms": NOW}, {"expires_at_ms": NOW + 60_001},
    {"authenticated_at_ms": NOW + 1}, {"authenticated_at_ms": 0},
])
def test_assertion_time_boundaries_future_and_recent_auth_fail_closed(key, changes):
    verifier = PinnedAssertions(issuer="synthetic-auth", public_key=jwk(key))
    with pytest.raises(GuardDenied):
        check(verifier, envelope(key, assertion(**changes)))


def test_exact_sixty_second_recency_and_one_millisecond_expiry_boundaries(key):
    verifier = PinnedAssertions(issuer="synthetic-auth", public_key=jwk(key))
    value = envelope(key, assertion(authenticated_at_ms=NOW - 60_000))
    assert check(verifier, value).authenticated_at_ms == NOW - 60_000
    value = envelope(key, assertion(authenticated_at_ms=NOW, expires_at_ms=NOW + 1))
    assert check(verifier, value).expires_at_ms == NOW + 1
    with pytest.raises(GuardDenied):
        check(verifier, value, now_ms=NOW + 1)
    for changes in [{"now_ms": True}, {"now_ms": float(NOW)}, {"now_ms": 2**53},
                    {"now_ms": 0}, {"operation": []}, {"binding_sha256": True}]:
        with pytest.raises(GuardDenied):
            check(verifier, envelope(key), **changes)


def test_unavailable_defaults_never_accept_a_signed_assertion_or_mint_claim(key):
    assert isinstance(production_assertions(), UnavailableAssertions)
    assert isinstance(production_claims(), UnavailableClaims)
    with pytest.raises(GuardUnavailable):
        check(production_assertions(), envelope(key))
    with pytest.raises(GuardUnavailable):
        production_claims().sign({"subject_uuid": assertion()["subject_uuid"]})


NODE_VECTOR = r"""
import { webcrypto } from 'node:crypto';
const chunks = []; for await (const chunk of process.stdin) chunks.push(chunk);
const input = JSON.parse(Buffer.concat(chunks).toString('utf8'));
const canonical = (value) => {
  if (value === null || typeof value === 'boolean') return JSON.stringify(value);
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value)) throw new Error('Unsafe integer');
    return JSON.stringify(value);
  }
  if (typeof value === 'string') {
    for (const point of value) if (point.length === 1 && /[\ud800-\udfff]/u.test(point))
      throw new Error('Lone surrogate');
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (typeof value !== 'object') throw new Error('Unsupported canonical data');
  const keys = Object.keys(value).sort();
  if (keys.some(key => !/^[\x00-\x7f]*$/.test(key))) throw new Error('Non-ASCII key');
  return '{' + keys.map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
};
const bytes = (kind, value) => Buffer.from('hirewiz.pairing.' + kind + '.v2\n' + canonical(value));
const hash = async data => Buffer.from(await webcrypto.subtle.digest('SHA-256', data)).toString('hex');
const publicKey = await webcrypto.subtle.importKey('jwk', input.jwk,
  {name: 'ECDSA', namedCurve: 'P-256'}, false, ['verify']);
const algorithm = {name: 'ECDSA', hash: 'SHA-256'};
const signature = Buffer.from(input.signature, 'base64url');
const data = bytes(input.kind, input.payload);
const node = await webcrypto.subtle.generateKey({name: 'ECDSA', namedCurve: 'P-256'}, false,
  ['sign', 'verify']);
const nodeKey = await webcrypto.subtle.exportKey('jwk', node.publicKey);
const nodeSignature = await webcrypto.subtle.sign(algorithm, node.privateKey, data);
process.stdout.write(JSON.stringify({canonical_hex: data.toString('hex'), digest: await hash(data),
  key_fingerprint: await hash(bytes('public-key', input.jwk)),
  python_verified: await webcrypto.subtle.verify(algorithm, publicKey, signature, data),
  wrong_domain_verified: await webcrypto.subtle.verify(algorithm, publicKey, signature,
    bytes('candidate-assertion', input.payload)),
  node_key: {kty: nodeKey.kty, crv: nodeKey.crv, x: nodeKey.x, y: nodeKey.y},
  node_signature: Buffer.from(nodeSignature).toString('base64url')}));
"""


def test_independent_node_webcrypto_and_python_cross_verify_canonical_hash_and_signatures(key):
    node = shutil.which("node")
    assert node is not None, "Node/WebCrypto is required for the scoped interoperability proof"
    value = {"kind": "device-proof", "payload": deepcopy(VECTOR), "jwk": jwk(key),
             "signature": sign(key, "device-proof", VECTOR)}
    result = subprocess.run([node, "--input-type=module", "-e", NODE_VECTOR],
                            input=json.dumps(value), text=True, capture_output=True,
                            check=True, timeout=15)
    verified = json.loads(result.stdout)
    assert verified["canonical_hex"] == canonical_bytes("device-proof", VECTOR).hex()
    assert verified["digest"] == "a340bd57d58a51deb17320c9bb5191d58c0c90ccb06f273aabc31a1f5ba37849"
    assert verified["key_fingerprint"] == key_fingerprint(value["jwk"])
    assert verified["python_verified"] is True
    assert verified["wrong_domain_verified"] is False
    assert verified["node_key"] != value["jwk"]
    verify_signature(verified["node_key"], "device-proof", VECTOR, verified["node_signature"])
    with pytest.raises(GuardDenied):
        verify_signature(verified["node_key"], "device-proof", {**VECTOR, "tampered": True},
                         verified["node_signature"])
