"""Existing numeric/email auth cannot bypass new pending or mapped lifetimes."""
from __future__ import annotations

from uuid import uuid4

import pytest
from backend.app.models import CandidatePasswordAccount, User
from backend.app.security import (
    JWT_ALGORITHM,
    JWT_SECRET,
    create_access_token,
    hash_password,
)
from jose import jwt
from test_auth_security import _client


def mapped(factory, *, state="PENDING"):
    with factory() as db:
        user = User(email="candidate@example.com", password_hash=hash_password("synthetic-password-123"))
        db.add(user)
        db.flush()
        db.add(CandidatePasswordAccount(user_id=user.id, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
            principal_sha256="a" * 64, credential_sha256="b" * 64, auth_generation=1, state=state))
        db.commit()
        return user.id


@pytest.mark.parametrize("state", ["PENDING", "DELETE_DENIED"])
def test_pending_or_deleted_candidate_registration_cannot_use_legacy_password_login(state):
    engine, factory, client = _client()
    try:
        mapped(factory, state=state)
        result = client.post("/api/auth/login", json={"email": "candidate@example.com", "password": "synthetic-password-123"})
        assert result.status_code == 401
        assert "access_token" not in result.json()
    finally:
        client.close()
        engine.dispose()


@pytest.mark.parametrize("state", ["PENDING", "ACTIVE", "DELETE_DENIED"])
def test_numeric_bearer_cannot_authorize_mapped_candidate_account(state):
    engine, factory, client = _client()
    try:
        uid = mapped(factory, state=state)
        result = client.get("/api/auth/me", headers={"Authorization": "Bearer " + create_access_token(subject=str(uid))})
        assert result.status_code == 401
    finally:
        client.close()
        engine.dispose()


def test_google_email_lookup_cannot_link_mapped_password_lifetime(monkeypatch):
    from backend.app.routers import auth
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "synthetic-google-client")
    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", lambda *args: {
        "iss": "https://accounts.google.com", "sub": "synthetic-distinct-provider-principal",
        "email": "candidate@example.com", "email_verified": True})
    engine, factory, client = _client()
    try:
        mapped(factory, state="ACTIVE")
        result = client.post("/api/auth/google-login", json={"id_token": "x" * 200})
        assert result.status_code == 403 and "access_token" not in result.json()
    finally:
        client.close()
        engine.dispose()


def test_versioned_signed_claim_is_not_rederived_from_numeric_identity():
    from backend.app.domains.candidate_accounts.access import context_from_claims
    from backend.app.domains.recovery.store import GuardDenied
    context = {"candidate_id": 1, "account_binding_id": str(uuid4()), "session_id": str(uuid4())}
    token = create_access_token(subject="1", candidate_lifetime={"version": 1, "context": context, "auth_generation": 2})
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    checked, generation = context_from_claims(payload, candidate_id=1)
    assert checked.model_dump(mode="json") == context and generation == 2
    with pytest.raises(GuardDenied):
        context_from_claims(payload, candidate_id=2)
