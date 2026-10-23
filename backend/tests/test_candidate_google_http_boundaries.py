"""Public callers cannot enter new native enrollment before private admission."""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.routers import candidate_accounts


@pytest.mark.parametrize(
    "operation,body",
    [
        ("google-enrollment-challenge", {}),
        ("recovery-google-challenge", {}),
        (
            "recover-google",
            {
                "challenge_id": str(uuid4()),
                "nonce": "n" * 43,
                "id_token": "synthetic-private-token" * 10,
                "password": "synthetic-private-password",
                "accepted_enrollment": True,
                "accepted_recovery": True,
            },
        ),
        (
            "enroll-google",
            {
                "challenge_id": str(uuid4()),
                "nonce": "n" * 43,
                "id_token": "synthetic-private-token" * 10,
                "password": "synthetic-private-password",
                "accepted_enrollment": True,
            },
        ),
        (
            "enroll-password",
            {"password": "synthetic-private-password", "accepted_enrollment": True},
        ),
    ],
)
def test_public_enrollment_stops_before_owner_database_or_google_sdk(operation, body, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Unadmitted public caller reached identity/native/SQL")

    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", forbidden)
    monkeypatch.setattr(candidate_accounts, "verify_google_identity", forbidden)
    app = FastAPI()
    app.include_router(candidate_accounts.router, prefix="/api")
    app.dependency_overrides[get_db] = forbidden
    with TestClient(app, base_url="https://candidate.example.test") as client:
        result = client.post("/api/auth/candidate/v1/" + operation, json=body)
    assert result.status_code == 403
    assert result.json() == {"detail": "Private candidate transport authentication failed"}
    assert (
        "synthetic-private" not in result.text
        and result.headers["cache-control"] == "private, no-store"
    )


@pytest.mark.parametrize(
    "change",
    [
        {"accepted_enrollment": 1},
        {"accepted_enrollment": False},
        {"candidate_id": 92},
        {"extra": "synthetic-private"},
    ],
)
def test_explicit_enrollment_consent_and_exact_body_reject_before_ingress(change, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Invalid body reached native/SQL")

    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", forbidden)
    app = FastAPI()
    app.include_router(candidate_accounts.router, prefix="/api")
    app.dependency_overrides[get_db] = forbidden
    body = {"password": "synthetic-private-password", "accepted_enrollment": True, **change}
    with TestClient(app, base_url="https://candidate.example.test") as client:
        result = client.post(
            "/api/auth/candidate/v1/enroll-password",
            content=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
    assert result.status_code == 422 and result.json() == {
        "detail": "Invalid authentication request"
    }
    assert "synthetic-private" not in result.text


@pytest.mark.parametrize("change", [
    {"accepted_recovery": 1}, {"accepted_recovery": False}, {"accepted_enrollment": 1},
    {"current_password": "synthetic-private"}, {"authoritative_email": True},
    {"candidate_id": 92}, {"id_token": "synthetic-private" * 500},
])
def test_recovery_requires_exact_separate_consent_before_ingress(change, monkeypatch):
    def forbidden(*a, **kw):
        pytest.fail("Invalid recovery body reached identity/native/SQL")

    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", forbidden)
    monkeypatch.setattr(candidate_accounts, "verify_google_identity", forbidden)
    app = FastAPI()
    app.include_router(candidate_accounts.router, prefix="/api")
    app.dependency_overrides[get_db] = forbidden
    body = {"challenge_id": str(uuid4()), "nonce": "n" * 43,
        "id_token": "synthetic-private-token" * 10, "password": "synthetic-private-password",
        "accepted_enrollment": True, "accepted_recovery": True, **change}
    with TestClient(app, base_url="https://candidate.example.test") as client:
        result = client.post("/api/auth/candidate/v1/recover-google", json=body)
    assert result.status_code in {413, 422}
    assert result.json() == {"detail": "Invalid authentication request"}
    assert "synthetic-private" not in result.text
