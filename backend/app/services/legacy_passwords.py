"""Bounded verification of existing bcrypt formats using maintained PyCA bcrypt.

No hashing, rewriting or generation adoption occurs here. Passlib1.7.4's backend
probe is incompatible with bcrypt5's >72-byte rejection, even for short secrets.
Raw legacy bcrypt retains UTF-8/first-72-byte semantics. Existing bcrypt-sha256
formats use their documented version-specific prehash.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

import bcrypt

MAX_LEGACY_PASSWORD_BYTES = 4_096
MAX_LEGACY_HASH_BYTES = 1_024
MAX_LEGACY_BCRYPT_ROUNDS = 16
_RAW = re.compile(r"\$(2[aby])\$(\d{2})\$([./A-Za-z0-9]{22})([./A-Za-z0-9]{31})\Z")
_V1 = re.compile(r"\$bcrypt-sha256\$(2[ab]),([1-9]\d?)\$([./A-Za-z0-9]{22})\$([./A-Za-z0-9]{31})\Z")
_V2 = re.compile(r"\$bcrypt-sha256\$v=2,t=(2b),r=([1-9]\d?)\$([./A-Za-z0-9]{22})\$([./A-Za-z0-9]{31})\Z")


def bounded_password_bytes(password: object, encoded: object) -> bytes | None:
    if type(password) is not str or type(encoded) is not str:
        return None
    try:
        secret, stored = password.encode("utf-8"), encoded.encode("ascii")
    except UnicodeError:
        return None
    if not 0 < len(secret) <= MAX_LEGACY_PASSWORD_BYTES or not 0 < len(stored) <= MAX_LEGACY_HASH_BYTES:
        return None
    return secret


def verify_legacy_bcrypt(secret: bytes, encoded: str) -> bool:
    match = _RAW.fullmatch(encoded)
    if match is not None:
        if b"\0" in secret:
            return False  # Passlib's raw bcrypt rejects NULL-containing passwords.
        key = secret[:72]  # Compatibility only; new passwords use PBKDF2 in full.
    else:
        match = _V2.fullmatch(encoded) or _V1.fullmatch(encoded)
        if match is None:
            return False
        salt = match[3]
        if salt[-1] not in ".Oeu":
            return False
        digest = (hmac.digest(salt.encode("ascii"), secret, "sha256")
                  if encoded.startswith("$bcrypt-sha256$v=2,") else hashlib.sha256(secret).digest())
        key = base64.b64encode(digest)
    ident, rounds, salt, checksum = match.groups()
    cost = int(rounds)
    if not 4 <= cost <= MAX_LEGACY_BCRYPT_ROUNDS:
        return False
    stored = f"${ident}${cost:02}${salt}{checksum}".encode("ascii")
    try:
        return bcrypt.checkpw(key, stored)
    except (ValueError, TypeError):
        return False
