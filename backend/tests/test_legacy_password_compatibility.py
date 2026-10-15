"""Actual PyCA bcrypt verification, no credential rewrite or production accounts."""
from __future__ import annotations

import base64
import hashlib

import bcrypt
import pytest

from app.security import hash_password, verify_password
from app.services import legacy_passwords


@pytest.mark.parametrize("password", ["short", "påssword-密码", "a" * 72, "a" * 71 + "é", "密码" * 30])
@pytest.mark.parametrize("ident", [b"2a", b"2b"])
def test_actual_raw_bcrypt_short_utf8_and_72_byte_legacy_semantics(password, ident):
    encoded = bcrypt.hashpw(password.encode()[:72], bcrypt.gensalt(rounds=4, prefix=ident)).decode()
    assert bcrypt.checkpw(password.encode()[:72], encoded.encode())
    assert verify_password(password, encoded)
    assert not verify_password("wrong-" + password, encoded)
    if len(password.encode()) >= 72:
        assert verify_password(password + "ignored-legacy-tail", encoded)
    assert encoded.startswith("$" + ident.decode() + "$")


def test_legacy_2y_prefix_is_verified_without_rewriting():
    encoded = bcrypt.hashpw(b"short", bcrypt.gensalt(rounds=4)).decode().replace("$2b$", "$2y$", 1)
    assert verify_password("short", encoded) and not verify_password("wrong", encoded)


def test_passlib_documented_v2_hash_vector_uses_salt_keyed_hmac():
    # Unmodified public vector from Passlib's bcrypt_sha256 documentation.
    encoded = "$bcrypt-sha256$v=2,t=2b,r=12$n79VH.0Q2TMWmt3Oqt9uku$Kq4Noyk3094Y2QlB8NdRT8SvGiI4ft2"
    assert verify_password("password", encoded)
    assert not verify_password("wrong", encoded)


def test_actual_old_passlib_v1_sha256_hash_preserves_full_long_password():
    password = "é" * 80
    raw = bcrypt.hashpw(base64.b64encode(hashlib.sha256(password.encode()).digest()), bcrypt.gensalt(rounds=4))
    ident, rounds, payload = raw.decode().split("$")[1:]
    encoded = f"$bcrypt-sha256${ident},{int(rounds)}${payload[:22]}${payload[22:]}"
    assert verify_password(password, encoded)
    assert not verify_password(password + "different-tail", encoded)


@pytest.mark.parametrize("password,encoded", [
    (None, "$2b$04$" + "a" * 53),
    (b"short", "$2b$04$" + "a" * 53),
    ("short", None),
    ("short", ""),
    ("short", "not-a-password-hash"),
    ("short", "$2x$04$" + "a" * 53),
    ("short", "$2$04$" + "a" * 53),
    ("short", "$2b$31$" + "a" * 53),
    ("short", "$2b$03$" + "a" * 53),
    ("short", "$2b$04$too-short"),
    ("short", "$2b$04$" + "é" * 53),
    ("short", "$bcrypt-sha256$v=3,t=2b,r=4$" + "a" * 22 + "$" + "a" * 31),
    ("short", "$bcrypt-sha256$v=2,t=2b,r=31$" + "a" * 21 + "." + "$" + "a" * 31),
    ("short", "$bcrypt-sha256$v=2,t=2b,r=4$" + "a" * 22 + "$" + "a" * 31),
    ("short", "x" * 1025),
    ("x" * 4097, "$2b$04$" + "a" * 53),
    ("\ud800", "$2b$04$" + "a" * 53),
    ("has\0null", "$2b$04$" + "a" * 53),
])
def test_malformed_unbounded_unsupported_or_null_input_fails_without_bcrypt_call(password, encoded, monkeypatch):
    calls = []
    def forbidden(*args):
        calls.append(True)
        raise AssertionError("Malformed input must not enter expensive bcrypt")
    monkeypatch.setattr(legacy_passwords.bcrypt, "checkpw", forbidden)
    assert verify_password(password, encoded) is False
    assert not calls


def test_new_pbkdf2_remains_default_and_uses_entire_password():
    password = "a" * 72 + "different-full-tail"
    encoded = hash_password(password)
    assert encoded.startswith("$pbkdf2-sha256$")
    assert verify_password(password, encoded)
    assert not verify_password("a" * 72 + "wrong-full-tail", encoded)


def test_existing_legacy_account_actual_http_bcrypt_login_does_not_rehash():
    from test_auth_security import _client

    from app.models import User
    from app.rate_limiter import limiter
    limiter._storage.reset()
    encoded = bcrypt.hashpw(b"legacy-password-123", bcrypt.gensalt(rounds=4)).decode()
    engine, factory, client = _client()
    try:
        with factory() as db:
            user = User(email="legacy-bcrypt@example.com", password_hash=encoded)
            db.add(user)
            db.commit()
            uid = user.id
        response = client.post("/api/auth/login", json={"email": "legacy-bcrypt@example.com", "password": "legacy-password-123"})
        assert response.status_code == 200 and response.json()["user_id"] == uid
        wrong = client.post("/api/auth/login", json={"email": "legacy-bcrypt@example.com", "password": "wrong-password"})
        assert wrong.status_code == 401
        with factory() as db:
            assert db.get(User, uid).password_hash == encoded
    finally:
        client.close()
        engine.dispose()
