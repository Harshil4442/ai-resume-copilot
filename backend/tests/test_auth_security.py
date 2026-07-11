from fastapi import FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.database import Base, get_db
from backend.app.models import User
from backend.app.rate_limiter import limiter
from backend.app.routers import auth


def _client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(auth.router, prefix="/api")

    def override_db():
        db = factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_db
    return engine, factory, TestClient(app)


def test_password_registration_grants_free_analysis_units():
    limiter._storage.reset()
    engine, factory, client = _client()
    try:
        response = client.post(
            "/api/auth/register",
            json={
                "email": "new.user@example.com",
                "password": "strong-password-123",
                "accepted_terms": True,
                "confirmed_age_18": True,
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["ai_credits"] == 50
        with factory() as db:
            user = db.query(User).one()
            assert user.ai_credits == 50
    finally:
        client.close()
        engine.dispose()


def test_google_login_derives_identity_from_verified_token(monkeypatch):
    limiter._storage.reset()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-web-client.apps.exampleusercontent.com")
    monkeypatch.setattr(
        auth.google_id_token,
        "verify_oauth2_token",
        lambda token, request, audience: {
            "iss": "https://accounts.google.com",
            "sub": "google-subject-123",
            "email": "Verified.User@Example.com",
            "email_verified": True,
            "name": "Verified User",
            "aud": audience,
        },
    )
    engine, factory, client = _client()
    try:
        response = client.post(
            "/api/auth/google-login",
            json={
                "id_token": "x" * 200,
                "registration_consent": True,
                "policy_version": "2026-07-11",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["access_token"]
        with factory() as db:
            user = db.query(User).one()
            assert user.email == "verified.user@example.com"
            assert user.password_hash == ""
            assert user.ai_credits == 50
            assert user.terms_version == "2026-07-11"
            assert user.terms_accepted_at is not None
    finally:
        client.close()
        engine.dispose()


def test_google_login_rejects_unverified_or_caller_supplied_identity(monkeypatch):
    limiter._storage.reset()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-web-client.apps.exampleusercontent.com")

    def reject_token(token, request, audience):
        raise ValueError("bad signature")

    monkeypatch.setattr(auth.google_id_token, "verify_oauth2_token", reject_token)
    engine, factory, client = _client()
    try:
        invalid = client.post(
            "/api/auth/google-login",
            json={"id_token": "x" * 200},
        )
        assert invalid.status_code == 401

        caller_identity = client.post(
            "/api/auth/google-login",
            json={
                "id_token": "x" * 200,
                "email": "victim@example.com",
                "name": "Victim",
            },
        )
        assert caller_identity.status_code == 422
        with factory() as db:
            assert db.query(User).count() == 0
    finally:
        client.close()
        engine.dispose()


def test_google_registration_requires_current_policy_consent(monkeypatch):
    limiter._storage.reset()
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "google-web-client.apps.exampleusercontent.com")
    monkeypatch.setattr(
        auth.google_id_token,
        "verify_oauth2_token",
        lambda token, request, audience: {
            "iss": "accounts.google.com",
            "sub": "google-subject-no-consent",
            "email": "new.user@example.com",
            "email_verified": True,
            "aud": audience,
        },
    )
    engine, factory, client = _client()
    try:
        response = client.post(
            "/api/auth/google-login",
            json={"id_token": "x" * 200},
        )
        assert response.status_code == 403
        with factory() as db:
            assert db.query(User).count() == 0
    finally:
        client.close()
        engine.dispose()
