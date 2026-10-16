"""Fresh signed Google proof + real SQL and intercepted native/protected grants."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from google.auth import _helpers as google_helpers
from jose import jwt
from test_candidate_production_factory import NOW, PASSWORD
from test_candidate_production_factory import configured as configured

from app.domains.candidate_accounts.google import GoogleBearerProof, verify_google_identity
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import (
    CandidateGoogleEnrollmentChallenge,
    CandidateGoogleIdentity,
    CandidateIdentityHistory,
    CandidatePasswordAccount,
    User,
    UserProfile,
)
from app.security import hash_password

CLIENT = "synthetic-google-client.apps.googleusercontent.com"
EMAIL = "google-owner@example.test"


@pytest.fixture
def signed_google(monkeypatch):
    from app.domains.candidate_accounts import google as google_identity

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    public = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("GOOGLE_CLIENT_ID", CLIENT)
    monkeypatch.setattr(
        google_identity.google_id_token, "_fetch_certs", lambda *a, **kw: {"synthetic-key": public}
    )
    monkeypatch.setattr(
        google_helpers,
        "utcnow",
        lambda: datetime.fromtimestamp(NOW / 1000, UTC).replace(tzinfo=None),
    )

    def sign(**overrides):
        claims = {
            "iss": "https://accounts.google.com",
            "sub": "synthetic-stable-subject",
            "email": EMAIL,
            "email_verified": True,
            "aud": CLIENT,
            "iat": NOW // 1000,
            "exp": NOW // 1000 + 300,
            "nonce": "n" * 43,
            "jti": str(uuid4()),
        }
        claims.update(overrides)
        return jwt.encode(claims, private, algorithm="RS256", headers={"kid": "synthetic-key"})

    return sign


def identity(sign, **values):
    return verify_google_identity(sign(**values), now_ms=NOW)


def legacy(c, *, password=""):
    with c.accounts.sessions() as db:
        user = User(
            email=EMAIL, password_hash=hash_password(password) if password else "", ai_credits=137
        )
        db.add(user)
        db.flush()
        db.add(UserProfile(user_id=user.id, full_name="Synthetic original profile"))
        db.commit()
        return user.id


def challenge(c, sign, uid):
    owner = identity(sign)
    proof = GoogleBearerProof.model_validate(owner.bearer_claim())
    return proof, c.accounts.google_challenge(uid, proof)


def complete(c, sign, uid, proof, review, **extra):
    fresh = verify_google_identity(sign(nonce=review["nonce"]), nonce=review["nonce"], now_ms=NOW)
    return c.accounts.enroll_google(
        uid,
        proof,
        challenge_id=review["challenge_id"],
        nonce=review["nonce"],
        identity=fresh,
        password=PASSWORD,
        current_password=None,
        **extra,
    )


@pytest.mark.parametrize(
    "change",
    [
        {"aud": "different-client"},
        {"iss": "https://untrusted.example"},
        {"email_verified": False},
        {"exp": NOW // 1000 - 1},
        {"iat": NOW // 1000 - 121},
        {"nonce": "different"},
        {"sub": ""},
    ],
)
def test_actual_google_rsa_verifier_rejects_wrong_or_stale_claims(signed_google, change):
    with pytest.raises(GuardDenied):
        verify_google_identity(signed_google(**change), nonce="n" * 43, now_ms=NOW)


def test_actual_rsa_verification_binds_stable_subject_not_email(signed_google):
    first = identity(signed_google)
    renamed = identity(signed_google, email="renamed@example.test")
    different = identity(signed_google, sub="another-subject")
    assert first.principal_sha256 == renamed.principal_sha256 != different.principal_sha256
    raw = signed_google()
    broken = (
        raw.rsplit(".", 1)[0]
        + "."
        + ("A" if raw.rsplit(".", 1)[1][0] != "A" else "B")
        + raw.rsplit(".", 1)[1][1:]
    )
    with pytest.raises(GuardDenied):
        verify_google_identity(broken, now_ms=NOW)


def test_google_upgrade_preserves_user_profile_credits_and_ordinary_oauth_signin(
    configured, signed_google
):
    c = configured
    uid = legacy(c)
    proof, review = challenge(c, signed_google, uid)
    assert complete(c, signed_google, uid, proof, review) == uid
    with c.accounts.sessions() as db:
        assert db.query(User).count() == 1 and db.get(User, uid).ai_credits == 137
        assert db.query(UserProfile).one().full_name == "Synthetic original profile"
        assert db.get(CandidatePasswordAccount, uid).state == "ACTIVE"
        assert db.get(CandidateGoogleIdentity, uid).state == "ACTIVE"
        assert db.get(CandidateGoogleEnrollmentChallenge, review["challenge_id"]).used is True
        assert db.query(CandidateIdentityHistory).count() == 1
    result = c.accounts.login_google(identity(signed_google))
    assert result.context.candidate_id == uid and result.context.authentication_method == "google"
    assert result.context.model_dump(mode="json")["authentication_method"] == "google"
    c.accounts.validate(result.context, auth_generation=1)
    password = c.accounts.login(email=EMAIL, password=PASSWORD)
    assert (
        password.context.authentication_method == "password"
        and "authentication_method" not in password.context.model_dump(mode="json")
    )
    with pytest.raises(GuardDenied):
        complete(c, signed_google, uid, proof, review)


@pytest.mark.parametrize(
    "mutation", ["owner", "nonce", "principal", "email", "credential", "expired", "superseded"]
)
def test_changed_enrollment_review_stops_before_any_native_claim(
    configured, signed_google, mutation
):
    c = configured
    uid = legacy(c)
    proof, review = challenge(c, signed_google, uid)
    values = {}
    if mutation == "owner":
        uid += 1
    if mutation == "nonce":
        review["nonce"] = "x" * 43
    if mutation == "principal":
        values["sub"] = "different-owner"
    if mutation in {"email", "credential", "expired"}:
        with c.accounts.sessions() as db:
            if mutation == "email":
                db.get(User, uid).email = "changed@example.test"
            if mutation == "credential":
                db.get(User, uid).password_hash = hash_password(PASSWORD)
            if mutation == "expired":
                db.get(
                    CandidateGoogleEnrollmentChallenge, review["challenge_id"]
                ).expires_at_ms = NOW
            db.commit()
    if mutation == "superseded":
        c.accounts.google_challenge(uid, proof)
    before = dict(c.native.values())
    fresh = verify_google_identity(
        signed_google(nonce=review["nonce"], **values), nonce=review["nonce"], now_ms=NOW
    )
    with pytest.raises(GuardDenied):
        c.accounts.enroll_google(
            uid,
            proof,
            challenge_id=review["challenge_id"],
            nonce=review["nonce"],
            identity=fresh,
            password=PASSWORD,
            current_password=None,
        )
    assert c.native.values() == before


def test_password_owner_must_reauthenticate_before_google_link(configured, signed_google):
    c = configured
    uid = legacy(c, password=PASSWORD)
    proof, review = challenge(c, signed_google, uid)
    with pytest.raises(GuardDenied):
        complete(c, signed_google, uid, proof, review)
    fresh = verify_google_identity(
        signed_google(nonce=review["nonce"]), nonce=review["nonce"], now_ms=NOW
    )
    c.accounts.enroll_google(
        uid,
        proof,
        challenge_id=review["challenge_id"],
        nonce=review["nonce"],
        identity=fresh,
        password=PASSWORD,
        current_password=PASSWORD,
    )
    assert c.accounts.login_google(identity(signed_google)).context.candidate_id == uid


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_original_enrollment_never_retries_or_adopts_google_subject(
    configured, signed_google, monkeypatch, persisted
):
    c = configured
    uid = legacy(c)
    proof, review = challenge(c, signed_google, uid)
    c.native.fault = lambda names: (
        ("persist_loss" if persisted else "no_persist_loss")
        if "password_credential_revisions" in names
        else None
    )
    with pytest.raises(GuardUnavailable):
        complete(c, signed_google, uid, proof, review)
    c.native.fault = None
    before = dict(c.native.values())
    assert (
        c.accounts.registration_status(email=EMAIL, password=PASSWORD) == "PENDING_REVIEW_REQUIRED"
    )
    with pytest.raises(GuardDenied):
        complete(c, signed_google, uid, proof, review)
    with pytest.raises(GuardDenied):
        c.accounts.login_google(identity(signed_google))
    assert c.native.values() == before


def test_existing_password_account_explicitly_enrolls_without_replacing_credential_or_data(
    configured,
):
    c = configured
    uid = legacy(c, password=PASSWORD)
    with c.accounts.sessions() as db:
        old = db.get(User, uid).password_hash
    with pytest.raises(GuardDenied):
        c.accounts.enroll_password(uid, "wrong-synthetic-password")
    assert c.accounts.enroll_password(uid, PASSWORD) == uid
    with c.accounts.sessions() as db:
        assert db.get(User, uid).password_hash == old and db.get(User, uid).ai_credits == 137
        assert db.get(CandidateGoogleIdentity, uid) is None
    assert c.accounts.login(email=EMAIL, password=PASSWORD).context.candidate_id == uid


def test_changed_pending_generation_cannot_activate_after_native_io(
    configured, signed_google, monkeypatch
):
    c = configured
    uid = legacy(c)
    proof, review = challenge(c, signed_google, uid)
    retention = c.accounts.retention
    enroll = retention.enroll

    def changed(revision):
        accepted = enroll(revision)
        with c.accounts.sessions() as db:
            db.get(CandidatePasswordAccount, uid).auth_generation = 2
            db.commit()
        return accepted

    monkeypatch.setattr(retention, "enroll", changed)
    with pytest.raises(GuardUnavailable):
        complete(c, signed_google, uid, proof, review)
    with c.accounts.sessions() as db:
        assert db.get(CandidatePasswordAccount, uid).state == "PENDING"
