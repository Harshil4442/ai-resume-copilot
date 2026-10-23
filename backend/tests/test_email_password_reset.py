"""Real credential/outbox transactions and existing native emulator ports only."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from test_candidate_production_factory import configured as configured
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_scoped_authority_emulator import scoped as scoped

from app.database import Base
from app.domains.candidate_accounts import email_reset as reset
from app.domains.candidate_accounts.retention import NativeCandidateLifetimeRetention
from app.domains.candidate_accounts.service import CandidateAccountService
from app.domains.notifications import password_reset as mail
from app.domains.notifications import service as notifications
from app.domains.recovery.gcp_candidate_lifetimes import GcpProtectedCandidateLifetimes
from app.domains.recovery.gcp_candidate_logout import GcpCandidateCookieLogout
from app.domains.recovery.gcp_password_reset import GcpPasswordCredentialProjection
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import CandidatePasswordAccount, NotificationOutbox, PasswordResetToken, User
from app.routers import password_reset as routes
from app.security import create_access_token, get_current_user, hash_password, verify_password

EMAIL = "reset-owner@example.com"
OLD = "synthetic-old-password-20261023"  # gitleaks:allow
NEW = "synthetic-new-password-20261023"  # gitleaks:allow


@pytest.fixture
def owned(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-no-provider-call")
    monkeypatch.setenv("EMAIL_FROM", "HireWiz <security@example.invalid>")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "false")
    engine = create_engine(f"sqlite:///{tmp_path / 'reset-owned.sqlite'}", hide_parameters=True)
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        db.add(User(id=1, email=EMAIL, password_hash=hash_password(OLD), ai_credits=7, job_service_credits=3))
        db.commit()
    yield engine, factory
    engine.dispose()


def issue(factory, *, now=None):
    with factory() as db:
        identity = reset.request_reset(db, EMAIL, now=now)
        item = db.get(NotificationOutbox, identity)
        text = mail.message(item)[1]
        return text.split("#token=", 1)[1].split("\n", 1)[0], identity


def test_request_persists_only_hash_and_authenticated_ciphertext_and_cooldown(owned):
    _, factory = owned
    token, identity = issue(factory)
    with factory() as db:
        row, item = db.query(PasswordResetToken).one(), db.get(NotificationOutbox, identity)
        assert row.token_sha256 == reset.digest(token) and token not in str(item.payload)
        assert set(item.payload) == {"reset_id", "expires_at_ms", "ciphertext"}
        assert reset.request_reset(db, EMAIL) is None and reset.request_reset(db, "unknown@example.com") is None
        assert db.query(PasswordResetToken).count() == db.query(NotificationOutbox).count() == 1
        item.payload = {**item.payload, "ciphertext": "AA=="}
        with pytest.raises(ValueError):
            mail.message(item)


def test_legacy_reset_is_atomic_single_use_revokes_old_bearers_and_does_not_enroll(owned):
    _, factory = owned
    old = create_access_token(subject="1", credential_epoch=0)
    token, _ = issue(factory)
    with factory() as db:
        reset.confirm_reset(db, token, NEW)
        owner = db.get(User, 1)
        assert verify_password(NEW, owner.password_hash) and not verify_password(OLD, owner.password_hash)
        assert owner.credential_epoch == 1 and owner.ai_credits == 7 and owner.job_service_credits == 3
        assert db.query(CandidatePasswordAccount).count() == 0
        assert db.query(PasswordResetToken).one().status == "COMPLETED"
        assert db.query(NotificationOutbox).filter_by(notification_type="password_changed").count() == 1
        with pytest.raises(HTTPException):
            get_current_user(token=old, db=db)
        new = create_access_token(subject="1", credential_epoch=1)
        assert get_current_user(token=new, db=db).id == 1
        with pytest.raises(reset.ResetLinkInvalid):
            reset.confirm_reset(db, token, "another-synthetic-password")


@pytest.mark.parametrize("change", ("expired", "email", "credential", "epoch"))
def test_changed_or_expired_proof_does_not_change_password(owned, change):
    _, factory = owned
    token, _ = issue(factory)
    with factory() as db:
        owner, proof = db.get(User, 1), db.query(PasswordResetToken).one()
        if change == "expired":
            proof.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        elif change == "email":
            owner.email = "changed@example.invalid"
        elif change == "credential":
            owner.password_hash = hash_password("already-changed-password")
        else:
            owner.credential_epoch = 2
        db.commit()
        before = owner.password_hash
        with pytest.raises(reset.ResetLinkInvalid):
            reset.confirm_reset(db, token, NEW)
        assert db.get(User, 1).password_hash == before


def test_superseding_request_invalidates_previous_link(owned):
    _, factory = owned
    token, _ = issue(factory)
    with factory() as db:
        reset.request_reset(db, EMAIL, now=datetime.now(UTC) + timedelta(seconds=61))
        assert db.query(PasswordResetToken).filter_by(status="PENDING").count() == 1
        with pytest.raises(reset.ResetLinkInvalid):
            reset.confirm_reset(db, token, NEW)


def test_targeted_send_is_independent_of_marketing_and_has_no_open_sql_transaction(owned, monkeypatch):
    _, factory = owned
    token, identity = issue(factory)
    with factory() as db:
        db.add(NotificationOutbox(id="unrelated", user_id=1, notification_type="welcome", channel="email", recipient=EMAIL,
            payload={}, idempotency_key="unrelated", status="pending", available_at=datetime.now(UTC)))
        db.commit()
        calls = []
        def send(item):
            assert not db.in_transaction()
            calls.append((item.idempotency_key, mail.message(item)[1]))
        monkeypatch.setattr(notifications, "_send", send)
        assert mail.deliver(db, identity) is True
        assert token in calls[0][1] and "https://www.hirewizhq.com/reset-password#token=" in calls[0][1]
        assert len(calls) == 1 and db.get(NotificationOutbox, "unrelated").status == "pending"
        assert db.get(NotificationOutbox, identity).payload == {}
        assert mail.deliver(db, identity) is False and len(calls) == 1


def test_request_http_only_schedules_generic_background_work(monkeypatch):
    from starlette.requests import Request
    monkeypatch.setattr(routes, "configured", lambda: True)
    monkeypatch.setattr(routes.limiter, "enabled", False)
    monkeypatch.setattr(reset, "request_reset", lambda *_a, **_k: pytest.fail("Lookup must run after response"))
    for email in (EMAIL, "unknown@example.com"):
        tasks = BackgroundTasks()
        request = Request({"type": "http", "method": "POST", "path": "/api/auth/password-reset/request", "headers": [], "client": ("127.0.0.1", 1)})
        response = routes.request_link(request, routes.ResetRequest(email=email), tasks)
        assert response.status_code == 202 and response.body == b'{"status":"RESET_LINK_REQUESTED"}'
        assert len(tasks.tasks) == 1 and tasks.tasks[0].func is routes._request_delivery


@pytest.fixture
def native(scoped, tmp_path, monkeypatch):
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-no-provider-call")
    monkeypatch.setenv("EMAIL_FROM", "security@example.invalid")
    c = scoped
    engine = create_engine(f"sqlite:///{tmp_path / 'native-reset-owned.sqlite'}", hide_parameters=True)
    Base.metadata.create_all(engine)
    projection = GcpPasswordCredentialProjection(c.lifetime, c.scoped)
    protected = GcpProtectedCandidateLifetimes(c.scoped, reset=projection)
    service = CandidateAccountService(engine, NativeCandidateLifetimeRetention(c.lifetime, protected), now_ms=lambda: c.now,
        cookie_logout=GcpCandidateCookieLogout(c.scoped))
    uid = service.register(email=EMAIL, password=OLD, policy_version="synthetic")
    old = service.login(email=EMAIL, password=OLD)
    yield service, c, uid, old
    engine.dispose()


def test_native_reset_uses_existing_generation_cut_and_stale_cookie_denial_only(native):
    service, c, uid, old = native
    token, _ = issue(service.sessions)
    with service.sessions() as db:
        reset.confirm_reset(db, token, NEW, accounts=service)
        row = db.get(CandidatePasswordAccount, uid)
        assert row.auth_generation == 2 and row.state == "ACTIVE"
        retained = c.v3.registry.read("v3_recovery_mailboxes", row.subject_uuid)
        assert retained["email_sha256"] == reset.digest(EMAIL) and retained["candidate_id"] == uid
        assert db.query(PasswordResetToken).one().status == "COMPLETED"
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.validate(old.context, auth_generation=1)
    newer = service.login(email=EMAIL, password=NEW)
    before = len(c.http.objects), len(c.commits)
    service.logout_cookie(old.context, auth_generation=1, operation_id=uuid4())
    assert (len(c.http.objects), len(c.commits)) == before  # Read-only denial proof, never session issuance.
    service.validate(newer.context, auth_generation=2)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.logout_cookie(old.context.model_copy(update={"account_binding_id": uuid4()}), auth_generation=1, operation_id=uuid4())


def test_native_unknown_outcome_remains_pending_and_consumed(native, monkeypatch):
    service, _, uid, _ = native
    token, _ = issue(service.sessions)
    def unknown(*_a):
        with service.sessions() as db:
            assert db.get(CandidatePasswordAccount, uid).state == "RESET_PENDING"
            assert db.query(PasswordResetToken).one().consumed_at is not None
        raise GuardUnavailable("synthetic_unknown")
    monkeypatch.setattr(service.retention, "advance_credentials", unknown)
    with service.sessions() as db:
        with pytest.raises(reset.ResetUnavailable):
            reset.confirm_reset(db, token, NEW, accounts=service)
        assert db.get(CandidatePasswordAccount, uid).state == "RESET_PENDING"
        assert db.query(PasswordResetToken).one().status == "UNAVAILABLE"
        with pytest.raises(reset.ResetLinkInvalid):
            reset.confirm_reset(db, token, NEW, accounts=service)


def test_legacy_google_signin_remains_usable_after_email_password_reset(owned, monkeypatch):
    from starlette.requests import Request

    from app.domains.candidate_accounts import google
    from app.models import CandidateGoogleIdentity
    from app.routers import auth
    _, factory = owned
    token, _ = issue(factory)
    identity = google.GoogleIdentity(principal_sha256="1" * 64, proof_sha256="2" * 64,
        email=EMAIL, authenticated_at_ms=1760000000000)
    monkeypatch.setattr(google, "verify_google_identity", lambda _: identity)
    monkeypatch.setattr(auth.limiter, "enabled", False)
    with factory() as db:
        reset.confirm_reset(db, token, NEW)
        request = Request({"type": "http", "method": "POST", "path": "/api/auth/google-login", "headers": [], "client": ("127.0.0.1", 1)})
        result = auth.google_login(request, auth.AuthGoogleLoginRequest(id_token="x" * 200), db)
        assert get_current_user(token=result.access_token, db=db).id == 1
        assert db.query(CandidatePasswordAccount).count() == db.query(CandidateGoogleIdentity).count() == 0
        assert verify_password(NEW, db.get(User, 1).password_hash)


def test_security_worker_recovers_stale_claim_without_enabling_marketing(owned, monkeypatch):
    _, factory = owned
    token, _ = issue(factory)
    with factory() as db:
        reset.confirm_reset(db, token, NEW)
        item = db.query(NotificationOutbox).filter_by(notification_type="password_changed").one()
        item.status, item.claimed_at = "processing", datetime.now(UTC) - timedelta(minutes=16)
        db.add(NotificationOutbox(id="unrelated", user_id=1, notification_type="welcome", channel="email", recipient=EMAIL,
            payload={}, idempotency_key="unrelated", status="pending", available_at=datetime.now(UTC)))
        db.commit()
        calls = []
        monkeypatch.setattr(notifications, "_send", lambda item: calls.append(item.notification_type))
        result = notifications.deliver_pending_notifications(db)
        assert calls == ["password_changed"] and result.sent == 1 and result.configured
        assert db.get(NotificationOutbox, "unrelated").status == "pending"


def test_native_rotated_cookie_refuses_current_account_unavailable(native, monkeypatch):
    service, _, _, old = native
    token, _ = issue(service.sessions)
    with service.sessions() as db:
        reset.confirm_reset(db, token, NEW, accounts=service)
    monkeypatch.setattr(service.retention, "check_account", lambda _: False)
    monkeypatch.setattr(service.cookie_logout, "confirm_rotated_cookie", lambda *_a, **_k: pytest.fail("No denial without current retained proof"))
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.logout_cookie(old.context, auth_generation=1, operation_id=uuid4())


@pytest.mark.parametrize("field", ("principal_sha256", "subject_uuid"))
def test_native_rotated_cookie_refuses_mismatched_current_subject(native, field):
    service, _, uid, old = native
    token, _ = issue(service.sessions)
    with service.sessions() as db:
        reset.confirm_reset(db, token, NEW, accounts=service)
        row, owner = db.get(CandidatePasswordAccount, uid), db.get(User, uid)
        from app.domains.candidate_accounts.service import revision_from_row
        revision = revision_from_row(row, owner.password_hash)
    value = "f" * 64 if field == "principal_sha256" else uuid4()
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.cookie_logout.confirm_rotated_cookie(old.context, revision.model_copy(update={field: value}), auth_generation=1)


@pytest.mark.parametrize("path,body", (("request", {"email": EMAIL}), ("confirm", {"token": "T" * 43, "new_password": NEW})))
def test_public_reset_reaches_real_private_ingress_guard_without_bearer(path, body, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app
    monkeypatch.setattr(routes, "_request_delivery", lambda *_a: pytest.fail("Unsigned caller must never queue mail"))
    with TestClient(app, base_url="https://testserver") as client:
        response = client.post("/api/auth/password-reset/" + path, json=body)
    assert response.status_code == 403
    assert response.json() == {"detail": "Private candidate transport authentication failed"}
    assert NEW not in response.text and "T" * 43 not in response.text
    assert {part.strip() for part in response.headers["cache-control"].split(",")} == {"private", "no-store"}


def test_native_sql_only_mailbox_change_cannot_authorize_reset(native, monkeypatch):
    service, c, uid, _ = native
    attacker = "changed-mailbox@example.com"
    with service.sessions() as db:
        owner, row = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
        original_hash, subject_uuid = owner.password_hash, row.subject_uuid
        owner.email = attacker
        db.commit()
        notification = reset.request_reset(db, attacker)
        token = mail.message(db.get(NotificationOutbox, notification))[1].split("#token=", 1)[1].split("\n", 1)[0]
    monkeypatch.setattr(service.retention, "issue", lambda *_a, **_k: pytest.fail("Changed SQL email must never issue native capability"))
    with service.sessions() as db:
        with pytest.raises(reset.ResetUnavailable):
            reset.confirm_reset(db, token, NEW, accounts=service)
        assert db.get(User, uid).password_hash == original_hash
        assert db.get(CandidatePasswordAccount, uid).state == "ACTIVE"
    assert c.v3.registry.read("v3_recovery_mailboxes", subject_uuid)["email_sha256"] == reset.digest(EMAIL)


@pytest.mark.parametrize("field,value", (("candidate_id", 999999), ("principal_sha256", "f" * 64), ("account_binding_id", uuid4())))
def test_native_mailbox_binding_refuses_other_owner_and_never_overwrites(native, field, value):
    service, c, uid, _ = native
    from app.domains.candidate_accounts.service import revision_from_row
    with service.sessions() as db:
        revision = revision_from_row(db.get(CandidatePasswordAccount, uid), db.get(User, uid).password_hash)
    original = c.v3.registry.read("v3_recovery_mailboxes", str(revision.subject_uuid))
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.retention.bind_recovery_mailbox(revision.model_copy(update={field: value}), reset.digest(EMAIL))
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.retention.bind_recovery_mailbox(revision, reset.digest("other@example.com"))
    assert c.v3.registry.read("v3_recovery_mailboxes", str(revision.subject_uuid)) == original


def test_native_unbound_existing_account_needs_verified_login_before_email_recovery(native, monkeypatch):
    service, c, _, _ = native
    email = "historical-native@example.com"
    original = service.retention.bind_recovery_mailbox
    # Build the genuine old-cohort enrollment with no new mailbox projection.
    with monkeypatch.context() as old_version:
        old_version.setattr(service.retention, "bind_recovery_mailbox", lambda *_a: True)
        uid = service.register(email=email, password=OLD, policy_version="synthetic")
    with service.sessions() as db:
        from app.domains.candidate_accounts.service import revision_from_row
        revision = revision_from_row(db.get(CandidatePasswordAccount, uid), db.get(User, uid).password_hash)
    assert service.retention.check_recovery_mailbox(revision, reset.digest(email)) is False
    with pytest.raises(GuardDenied):
        service.login(email=email, password=NEW)
    assert c.v3.registry.read("v3_recovery_mailboxes", str(revision.subject_uuid)) is None
    assert service.retention.bind_recovery_mailbox == original
    service.login(email=email, password=OLD)
    assert service.retention.check_recovery_mailbox(revision, reset.digest(email)) is True


@pytest.mark.parametrize("changed_sql_email", (False, True))
def test_intercepted_production_native_email_reset_requires_bound_mailbox(configured, monkeypatch, changed_sql_email):
    """Actual production composition/SDK envelopes; intercepted transports, not cloud durability."""
    c = configured
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-no-provider-call")
    monkeypatch.setenv("EMAIL_FROM", "security@example.com")
    uid = c.accounts.register(email=EMAIL, password=OLD, policy_version="synthetic")
    old = c.accounts.login(email=EMAIL, password=OLD)
    email = "sql-only-attacker@example.com" if changed_sql_email else EMAIL
    with c.accounts.sessions() as db:
        owner, row = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
        original_hash, subject = owner.password_hash, row.subject_uuid
        owner.email = email
        db.commit()
        identity = reset.request_reset(db, email)
        token = mail.message(db.get(NotificationOutbox, identity))[1].split("#token=", 1)[1].split("\n", 1)[0]
    if changed_sql_email:
        actual = c.accounts._composition().service
        monkeypatch.setattr(actual.retention, "issue", lambda *_a, **_k: pytest.fail("SQL email cannot authorize native session"))
    with c.accounts.sessions() as db:
        if changed_sql_email:
            with pytest.raises(reset.ResetUnavailable):
                reset.confirm_reset(db, token, NEW, accounts=c.accounts)
            assert db.get(User, uid).password_hash == original_hash
            assert db.get(CandidatePasswordAccount, uid).auth_generation == 1
        else:
            reset.confirm_reset(db, token, NEW, accounts=c.accounts)
            assert db.get(CandidatePasswordAccount, uid).auth_generation == 2
    assert c.native.values()["v3_recovery_mailboxes", subject]["email_sha256"] == reset.digest(EMAIL)
    if not changed_sql_email:
        with pytest.raises(GuardDenied):
            c.accounts.validate(old.context, auth_generation=1)
        c.accounts.logout_cookie(old.context, auth_generation=1, operation_id=uuid4())
        assert c.accounts.login(email=EMAIL, password=NEW).auth_generation == 2
    from app.domains.recovery.gcp_scoped_census import validate_scoped_rows
    validate_scoped_rows(c.native.values())
    assert not c.sql_active and not c.native.views


@pytest.mark.parametrize("attack", ("chosen_token", "other_owner_token", "expiry_extension"))
def test_sql_verifier_or_binding_replacement_cannot_authorize_reset(owned, attack):
    _, factory = owned
    token, _ = issue(factory)
    with factory() as db:
        victim = db.query(PasswordResetToken).one()
        original_hash = db.get(User, 1).password_hash
        if attack == "chosen_token":
            token = "A" * 43
            victim.token_sha256 = reset.digest(token)
        elif attack == "other_owner_token":
            email = "other-owner@example.com"
            db.add(User(id=2, email=email, password_hash=hash_password(OLD)))
            db.commit()
            item = reset.request_reset(db, email)
            token = mail.message(db.get(NotificationOutbox, item))[1].split("#token=", 1)[1].split("\n", 1)[0]
            attacker = db.query(PasswordResetToken).filter_by(user_id=2).one()
            attacker.token_sha256 = "0" * 64
            db.flush()
            victim.token_sha256 = reset.digest(token)
        else:
            victim.expires_at += timedelta(days=1)
        db.commit()
        with pytest.raises(reset.ResetLinkInvalid):
            reset.confirm_reset(db, token, NEW)
        assert db.get(User, 1).password_hash == original_hash
        assert db.get(PasswordResetToken, victim.id).status == "PENDING"


def test_intercepted_native_sql_verifier_replacement_never_issues_session(configured, monkeypatch):
    c = configured
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-no-provider-call")
    monkeypatch.setenv("EMAIL_FROM", "security@example.com")
    uid = c.accounts.register(email=EMAIL, password=OLD, policy_version="synthetic")
    issue(c.accounts.sessions)
    chosen = "A" * 43
    with c.accounts.sessions() as db:
        proof = db.query(PasswordResetToken).one()
        proof.token_sha256 = reset.digest(chosen)
        db.commit()
    actual = c.accounts._composition().service
    monkeypatch.setattr(actual.retention, "issue", lambda *_a, **_k: pytest.fail("SQL verifier cannot mint native authority"))
    with c.accounts.sessions() as db:
        with pytest.raises(reset.ResetLinkInvalid):
            reset.confirm_reset(db, chosen, NEW, accounts=c.accounts)
        assert db.get(CandidatePasswordAccount, uid).auth_generation == 1
        assert db.query(PasswordResetToken).one().status == "PENDING"


def test_intercepted_postclaim_retarget_cannot_issue_or_reset_other_native_owner(configured, monkeypatch):
    c = configured
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-no-provider-call")
    monkeypatch.setenv("EMAIL_FROM", "security@example.com")
    owner = c.accounts.register(email=EMAIL, password=OLD, policy_version="synthetic")
    victim = c.accounts.register(email="retarget-victim@example.com", password=OLD, policy_version="synthetic")
    token, _ = issue(c.accounts.sessions)
    actual = c.accounts._composition().service
    original = actual.reset_password_from_email
    native_before = c.native.values()
    def retarget(reset_id, encoded, original_token):
        with c.accounts.sessions() as db:
            proof = db.get(PasswordResetToken, reset_id)
            assert proof.status == "PROCESSING" and proof.user_id == owner
            user, row = db.get(User, victim), db.get(CandidatePasswordAccount, victim)
            from app.domains.candidate_accounts.service import revision_from_row
            proof.user_id, proof.email_sha256 = victim, reset.digest(user.email)
            proof.credential_sha256, proof.native_revision_sha256 = reset.digest(user.password_hash), reset.revision_digest(revision_from_row(row, user.password_hash))
            proof.legacy_epoch = user.credential_epoch
            db.commit()  # SQL-only race AFTER the route authenticated and claimed the original token.
        return original(reset_id, encoded, original_token)
    monkeypatch.setattr(actual, "reset_password_from_email", retarget)
    monkeypatch.setattr(actual.retention, "issue", lambda *_a, **_k: pytest.fail("Retargeted claim must not issue any native capability"))
    with c.accounts.sessions() as db:
        with pytest.raises(reset.ResetUnavailable):
            reset.confirm_reset(db, token, NEW, accounts=c.accounts)
        for uid in (owner, victim):
            assert verify_password(OLD, db.get(User, uid).password_hash)
            row = db.get(CandidatePasswordAccount, uid)
            assert row.auth_generation == 1 and row.state == "ACTIVE"
    assert c.native.values() == native_before
    assert not c.sql_active and not c.native.views
