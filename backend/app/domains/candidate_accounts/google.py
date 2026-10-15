"""Verified Google identity, never an email-only enrollment or password proof."""
from __future__ import annotations

import hashlib
import json
import os
import time
from functools import partial

from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from pydantic import Field

from ..recovery.contracts import Contract, Digest, Timestamp
from ..recovery.store import GuardDenied, GuardUnavailable


class GoogleIdentity(Contract):
    principal_sha256: Digest
    proof_sha256: Digest
    email: str = Field(min_length=3, max_length=320)
    authenticated_at_ms: Timestamp
    issued_at_ms: int = 0
    name: str = ""

    def bearer_claim(self) -> dict:
        return self.model_dump(exclude={"email", "issued_at_ms", "name"}, mode="json")


class GoogleBearerProof(Contract):
    principal_sha256: Digest
    proof_sha256: Digest
    authenticated_at_ms: Timestamp


def verify_google_identity(token: str, *, nonce: str | None = None,
                           now_ms: int | None = None) -> GoogleIdentity:
    """Actual Google verifier checks RSA signature, audience and token expiry.

    Enrollment additionally demands a recent, nonce-bound ID token. Its exact
    signed bytes are hashed and immediately discarded, never stored or logged.
    """
    client_id = (os.getenv("GOOGLE_CLIENT_ID") or "").strip()
    if not client_id:
        raise GuardUnavailable("Google sign-in is not configured")
    if type(token) is not str or not 100 <= len(token.encode()) <= 4096:
        raise GuardDenied("Invalid Google identity token")
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    try:
        claims = google_id_token.verify_oauth2_token(token, partial(google_requests.Request(), timeout=3.0), client_id)
        subject, email = claims.get("sub"), claims.get("email")
        if (claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}
                or claims.get("email_verified") is not True
                or type(subject) is not str or not 1 <= len(subject) <= 255
                or type(email) is not str or not 3 <= len(email) <= 320):
            raise ValueError
        if nonce is not None:
            issued = claims.get("iat")
            if (type(nonce) is not str or not 32 <= len(nonce) <= 128
                    or claims.get("nonce") != nonce or type(issued) is not int
                    or not now - 120_000 <= issued * 1000 <= now + 5_000):
                raise ValueError
        principal = json.dumps({"issuer": "https://accounts.google.com", "subject": subject,
                                "audience": client_id}, sort_keys=True, separators=(",", ":"))
        return GoogleIdentity(principal_sha256=hashlib.sha256(principal.encode()).hexdigest(),
            proof_sha256=hashlib.sha256(token.encode()).hexdigest(), email=email.strip().lower(),
            authenticated_at_ms=now, issued_at_ms=claims["iat"] * 1000 if type(claims.get("iat")) is int else 0,
            name=claims.get("name") if type(claims.get("name")) is str else "")
    except google_exceptions.TransportError:
        raise GuardUnavailable("Google identity verification is unavailable") from None
    except (ValueError, TypeError, google_exceptions.GoogleAuthError):
        raise GuardDenied("Invalid Google identity token") from None
