"""Authoritative signed claims, real SQL and intercepted original native enrollment."""
from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from test_candidate_google_enrollment import EMAIL
from test_candidate_google_enrollment import signed_google as signed_google
from test_candidate_production_factory import NOW, PASSWORD, REPLACEMENT
from test_candidate_production_factory import configured as configured

from app.domains.candidate_accounts.google import (
    GoogleBearerProof,
    GoogleIdentity,
    verify_google_identity,
)
from app.domains.candidate_accounts.legacy_recovery import (
    RecoveryAuthenticationFailed,
    RecoveryEmailProofRequired,
    RecoveryUnavailable,
    recovery_digest,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import (
    CandidateGoogleEnrollmentChallenge,
    CandidateGoogleIdentity,
    CandidatePasswordAccount,
    NotificationOutbox,
    User,
    UserProfile,
)
from app.security import create_access_token, get_current_user, hash_password, verify_password


def owner(c, email=EMAIL):
    with c.accounts.sessions() as db:
        user = User(email=email, password_hash=hash_password(PASSWORD), ai_credits=137)
        db.add(user)
        db.flush()
        db.add(UserProfile(user_id=user.id, full_name="Synthetic retained profile"))
        db.commit()
        return user.id


def review(c, sign, uid, **claims):
    identity = verify_google_identity(sign(**claims), now_ms=NOW)
    proof = GoogleBearerProof.model_validate(identity.bearer_claim())
    challenge = c.accounts.google_recovery_challenge(uid, proof)
    return proof, challenge


def finish(c, sign, uid, proof, challenge, **claims):
    identity = verify_google_identity(sign(nonce=challenge["nonce"], **claims),
        nonce=challenge["nonce"], now_ms=NOW)
    return c.accounts.recover_google(uid, proof, challenge_id=challenge["challenge_id"],
        nonce=challenge["nonce"], identity=identity, password=REPLACEMENT)


@pytest.mark.parametrize("claims", [{"email": "synthetic-owner@gmail.com"}, {"hd": "example.test"}])
def test_authoritative_google_recovery_preserves_owner_and_revokes_old_legacy_bearer(
    configured, signed_google, monkeypatch, claims
):
    c = configured
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    uid = owner(c, claims.get("email", EMAIL))
    old_token = create_access_token(subject=str(uid))
    with c.accounts.sessions() as db:
        assert get_current_user(old_token, db).id == uid
    proof, challenge = review(c, signed_google, uid, **claims)
    assert finish(c, signed_google, uid, proof, challenge, **claims) == uid
    with c.accounts.sessions() as db:
        assert db.query(User).count() == 1
        user = db.get(User, uid)
        assert user.ai_credits == 137 and verify_password(REPLACEMENT, user.password_hash)
        assert not verify_password(PASSWORD, user.password_hash)
        assert db.query(UserProfile).one().full_name == "Synthetic retained profile"
        assert db.get(CandidatePasswordAccount, uid).state == "ACTIVE"
        assert db.get(CandidateGoogleIdentity, uid).state == "ACTIVE"
        assert db.get(CandidateGoogleEnrollmentChallenge, challenge["challenge_id"]).used is True
        notice = db.query(NotificationOutbox).one()
        assert notice.notification_type == "account_recovery_requested" and notice.payload == {}
        with pytest.raises(HTTPException) as denied:
            get_current_user(old_token, db)
        assert denied.value.status_code == 401
    assert c.accounts.login(email=claims.get("email", EMAIL), password=REPLACEMENT).context.candidate_id == uid
    with pytest.raises(RecoveryUnavailable):
        finish(c, signed_google, uid, proof, challenge, **claims)


@pytest.mark.parametrize("claims", [{}, {"hd": ""}, {"hd": " "}, {"hd": 7}])
def test_third_party_verified_email_alone_never_authorizes_recovery(configured, signed_google, claims):
    c = configured
    uid = owner(c)
    proof, challenge = review(c, signed_google, uid)
    before = dict(c.native.values())
    with pytest.raises(RecoveryEmailProofRequired):
        finish(c, signed_google, uid, proof, challenge, **claims)
    with c.accounts.sessions() as db:
        assert verify_password(PASSWORD, db.get(User, uid).password_hash)
        assert db.get(CandidatePasswordAccount, uid) is None
        assert db.get(CandidateGoogleEnrollmentChallenge, challenge["challenge_id"]).used is False
    assert c.native.values() == before


def test_authoritative_recovery_fact_cannot_be_serialized_or_supplied_by_a_caller(signed_google):
    nonce = "n" * 43
    verified = verify_google_identity(signed_google(hd="example.test"), nonce=nonce, now_ms=NOW)
    assert verified.verified_recovery_nonce(nonce)
    assert not verified.verified_recovery_nonce("different" * 5)
    assert not GoogleIdentity.model_validate(verified.model_dump()).verified_recovery_nonce(nonce)
    assert set(verified.bearer_claim()) == {"principal_sha256", "proof_sha256", "authenticated_at_ms"}
    ordinary = verify_google_identity(signed_google(hd="example.test"), now_ms=NOW)
    assert not ordinary.verified_recovery_nonce(nonce)
    with pytest.raises(ValidationError):
        GoogleIdentity.model_validate({**verified.model_dump(), "authoritative_email": True})


@pytest.mark.parametrize("mutation", ["owner", "nonce", "principal", "identity_email", "email", "credential", "expired", "superseded"])
def test_changed_preimage_stops_before_password_or_native_mutation(configured, signed_google, mutation):
    c = configured
    uid = owner(c)
    proof, challenge = review(c, signed_google, uid)
    values = {"hd": "example.test"}
    if mutation == "owner":
        uid += 1
    elif mutation == "nonce":
        challenge["nonce"] = "x" * 43
    elif mutation == "principal":
        values["sub"] = "another-google-subject"
    elif mutation == "identity_email":
        values["email"] = "another-mailbox@example.test"
    elif mutation == "superseded":
        c.accounts.google_recovery_challenge(uid, proof)
    else:
        with c.accounts.sessions() as db:
            if mutation == "email":
                db.get(User, uid).email = "changed@example.test"
            elif mutation == "credential":
                db.get(User, uid).password_hash = hash_password(REPLACEMENT)
            elif mutation == "expired":
                db.get(CandidateGoogleEnrollmentChallenge, challenge["challenge_id"]).expires_at_ms = NOW
            db.commit()
    before = dict(c.native.values())
    with pytest.raises((RecoveryAuthenticationFailed, RecoveryUnavailable)):
        finish(c, signed_google, uid, proof, challenge, **values)
    assert c.native.values() == before
    with c.accounts.sessions() as db:
        assert db.query(CandidatePasswordAccount).count() == db.query(CandidateGoogleIdentity).count() == 0


def test_normal_and_recovery_challenges_cannot_cross_purposes(configured, signed_google):
    c = configured
    uid = owner(c)
    identity = verify_google_identity(signed_google(), now_ms=NOW)
    proof = GoogleBearerProof.model_validate(identity.bearer_claim())
    normal = c.accounts.google_challenge(uid, proof)
    with pytest.raises(RecoveryAuthenticationFailed):
        finish(c, signed_google, uid, proof, normal, hd="example.test")
    _, recovery = review(c, signed_google, uid)
    fresh = verify_google_identity(signed_google(nonce=recovery["nonce"], hd="example.test"),
        nonce=recovery["nonce"], now_ms=NOW)
    before = dict(c.native.values())
    with pytest.raises(GuardDenied):
        c.accounts.enroll_google(uid, proof, challenge_id=recovery["challenge_id"], nonce=recovery["nonce"],
            identity=fresh, password=REPLACEMENT, current_password=PASSWORD)
    with c.accounts.sessions() as db:
        challenge = db.get(CandidateGoogleEnrollmentChallenge, recovery["challenge_id"])
        assert challenge.nonce_sha256 == recovery_digest(recovery["nonce"]) and not challenge.used
    assert c.native.values() == before


def test_a_caller_chosen_ordinary_nonce_cannot_alias_the_recovery_purpose(configured, signed_google):
    c = configured
    uid = owner(c)
    proof, challenge = review(c, signed_google, uid)
    # This aliased the previous string-prefixed construction even with a fresh
    # Google signature. Binary purpose framing is outside every UTF-8 nonce.
    alias = "hirewiz:legacy-google-recovery:v1\0" + challenge["nonce"]
    fresh = verify_google_identity(signed_google(nonce=alias, hd="example.test"), nonce=alias, now_ms=NOW)
    before = dict(c.native.values())
    with pytest.raises(GuardDenied):
        c.accounts.enroll_google(uid, proof, challenge_id=challenge["challenge_id"], nonce=alias,
            identity=fresh, password=REPLACEMENT, current_password=PASSWORD)
    with c.accounts.sessions() as db:
        assert verify_password(PASSWORD, db.get(User, uid).password_hash)
        assert db.get(CandidatePasswordAccount, uid) is None
        assert db.get(CandidateGoogleEnrollmentChallenge, challenge["challenge_id"]).used is False
    assert c.native.values() == before


@pytest.mark.parametrize("state", ["ACTIVE", "PENDING", "DELETE_DENIED", "OTHER"])
def test_every_existing_native_state_is_excluded(configured, signed_google, state):
    c = configured
    uid = owner(c)
    proof, challenge = review(c, signed_google, uid)
    with c.accounts.sessions() as db:
        db.add(CandidatePasswordAccount(user_id=uid, subject_uuid=str(uuid4()), account_binding_id=str(uuid4()),
            principal_sha256="a" * 64, credential_sha256="b" * 64, auth_generation=1, state=state))
        db.commit()
    before = dict(c.native.values())
    with pytest.raises(RecoveryUnavailable):
        c.accounts.google_recovery_challenge(uid, proof)
    with pytest.raises(RecoveryUnavailable):
        finish(c, signed_google, uid, proof, challenge, hd="example.test")
    assert c.native.values() == before


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_native_ack_keeps_pending_and_cannot_replay_or_adopt(configured, signed_google, persisted):
    c = configured
    uid = owner(c)
    old_token = create_access_token(subject=str(uid))
    proof, challenge = review(c, signed_google, uid)
    c.native.fault = lambda names: ("persist_loss" if persisted else "no_persist_loss") if "password_credential_revisions" in names else None
    with pytest.raises(GuardUnavailable):
        finish(c, signed_google, uid, proof, challenge, hd="example.test")
    c.native.fault = None
    before = dict(c.native.values())
    assert c.accounts.registration_status(email=EMAIL, password=REPLACEMENT) == "PENDING_REVIEW_REQUIRED"
    with c.accounts.sessions() as db:
        assert db.get(CandidatePasswordAccount, uid).state == "PENDING"
        with pytest.raises(HTTPException) as denied:
            get_current_user(old_token, db)
        assert denied.value.status_code == 401
    with pytest.raises(RecoveryUnavailable):
        finish(c, signed_google, uid, proof, challenge, hd="example.test")
    assert c.native.values() == before


def test_stale_ordinary_google_bearer_cannot_start_recovery(configured, signed_google):
    c = configured
    uid = owner(c)
    identity = verify_google_identity(signed_google(), now_ms=NOW)
    proof = GoogleBearerProof.model_validate(identity.bearer_claim()).model_copy(update={"authenticated_at_ms": NOW - 900_001})
    with pytest.raises(RecoveryAuthenticationFailed):
        c.accounts.google_recovery_challenge(uid, proof)
    with c.accounts.sessions() as db:
        assert db.query(CandidateGoogleEnrollmentChallenge).count() == 0


@pytest.mark.parametrize("state", ["ACTIVE", "PENDING", "OTHER"])
def test_existing_google_link_cannot_be_replaced(configured, signed_google, state):
    c = configured
    uid = owner(c)
    proof, challenge = review(c, signed_google, uid)
    with c.accounts.sessions() as db:
        db.add(CandidateGoogleIdentity(user_id=uid, principal_sha256="c" * 64,
            enrollment_proof_sha256="d" * 64, state=state))
        db.commit()
    before = dict(c.native.values())
    with pytest.raises(RecoveryUnavailable):
        finish(c, signed_google, uid, proof, challenge, hd="example.test")
    assert c.native.values() == before


def test_guard_denial_after_pending_commit_is_unavailable_not_definite_rejection(
    configured, signed_google, monkeypatch
):
    c = configured
    uid = owner(c)
    proof, challenge = review(c, signed_google, uid)

    def denied(_):
        raise GuardDenied("Synthetic protected denial")

    monkeypatch.setattr(c.accounts.retention, "enroll", denied)
    with pytest.raises(GuardUnavailable):
        finish(c, signed_google, uid, proof, challenge, hd="example.test")
    assert c.accounts.registration_status(email=EMAIL, password=REPLACEMENT) == "PENDING_REVIEW_REQUIRED"


def test_principal_collision_rolls_back_password_challenge_and_notification_atomically(
    configured, signed_google, monkeypatch
):
    c = configured
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    uid = owner(c)
    other = owner(c, "different-owner@example.test")
    proof, challenge = review(c, signed_google, uid)
    with c.accounts.sessions() as db:
        db.add(CandidateGoogleIdentity(user_id=other, principal_sha256=proof.principal_sha256,
            enrollment_proof_sha256="d" * 64, state="ACTIVE"))
        db.commit()
    before = dict(c.native.values())
    with pytest.raises(RecoveryUnavailable):
        finish(c, signed_google, uid, proof, challenge, hd="example.test")
    with c.accounts.sessions() as db:
        assert verify_password(PASSWORD, db.get(User, uid).password_hash)
        assert db.get(CandidatePasswordAccount, uid) is None
        assert db.get(CandidateGoogleEnrollmentChallenge, challenge["challenge_id"]).used is False
        assert db.query(NotificationOutbox).count() == 0
    assert c.native.values() == before
