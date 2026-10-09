"""Real credential SQL + real protected native grants; final activation is synthetic.

These service-ordering tests do not establish actual V3 normal availability,
production IAM, bootstrap, restore closure or browser transport activation.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime

from app.database import Base
from app.domains.candidate_accounts.contracts import CandidateCredentialRevision
from app.domains.candidate_accounts.retention import NativeCandidateLifetimeRetention
from app.domains.candidate_accounts.service import CandidateAccountService, credential_digest
from app.domains.recovery.gcp_password_lifetime_contracts import (
    CreateVerifiedPasswordWebSession,
    EnrollPasswordAccount,
)
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import CandidatePasswordAccount, User
from app.security import hash_password

PASSWORD = "synthetic-password-123"
NEW_PASSWORD = "synthetic-replacement-456"  # gitleaks:allow -- synthetic test credential


class SyntheticFinalActivation:
    """Explicit retained-state simulation, never the production V3 adapter."""
    def __init__(self):
        self.revisions, self.sessions, self.deleted = {}, {}, set()
        self.allow_activation, self.allow_denial, self.unknown_denial = True, True, False
        self.calls = []

    def activate(self, ack, issuer):
        evidence = issuer.consume_owned_ack(ack)
        command = evidence.intent.command
        self.calls.append(("activation", evidence.intent.operation_id))
        if not self.allow_activation:
            return False
        if isinstance(command, EnrollPasswordAccount):
            self.revisions[command.account_binding_id] = CandidateCredentialRevision(**command.model_dump(exclude={"kind", "subject_registration_event_id"}), auth_generation=1)
        elif isinstance(command, CreateVerifiedPasswordWebSession):
            revision = self.revisions.get(command.account_binding_id)
            if revision is None or (revision.auth_generation, revision.credential_sha256) != (command.expected_auth_generation, command.credential_sha256):
                return False
            self.sessions[command.session_id] = command.account_binding_id
        else:
            raise AssertionError("Only real owned native enrollment/session ACKs are accepted")
        return True

    def check_account(self, revision):
        return (revision.account_binding_id not in self.deleted
                and self.revisions.get(revision.account_binding_id) == revision)

    def check_session(self, context, revision):
        return self.check_account(revision) and self.sessions.get(context.session_id) == context.account_binding_id

    def revoke_session(self, context, revision):
        self.calls.append(("revoke", context.session_id))
        if not self.allow_denial:
            return False
        self.sessions.pop(context.session_id, None)
        return not self.unknown_denial

    def advance_credentials(self, context, old, new):
        self.calls.append(("advance", old.auth_generation, new.auth_generation))
        if not self.allow_denial:
            return False
        self.revisions[old.account_binding_id] = new
        return not self.unknown_denial

    def delete_account(self, context, revision):
        self.calls.append(("delete", context.candidate_id))
        if not self.allow_denial:
            return False
        self.deleted.add(revision.account_binding_id)
        return not self.unknown_denial


@pytest.fixture
def lifecycle(lifetime, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'only-synthetic-candidate.sqlite'}", hide_parameters=True)
    Base.metadata.create_all(engine)
    protected = SyntheticFinalActivation()
    retention = NativeCandidateLifetimeRetention(lifetime.lifetime, protected)
    service = CandidateAccountService(engine, retention, now_ms=lambda: lifetime.now)
    try:
        yield service, protected, lifetime
    finally:
        engine.dispose()


def register(service, email="candidate@example.com"):
    return service.register(email=email, password=PASSWORD, policy_version="2026-10-08")


def test_real_registration_and_password_login_issue_native_session_without_identity_seed(lifecycle):
    service, protected, native = lifecycle
    uid = register(service)
    result = service.login(email="candidate@example.com", password=PASSWORD)
    with service.sessions() as db:
        user, row = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
        assert user.password_hash != PASSWORD and row.state == "ACTIVE"
        assert row.credential_sha256 == credential_digest(user.password_hash)
        assert native.registry.read("subjects", row.subject_uuid)["active"] is True
        assert native.registry.read("pairing_account_bindings", row.account_binding_id)["candidate_id"] == uid
    service.validate(result.context, auth_generation=1)
    assert len(protected.calls) == 2


@pytest.mark.parametrize("failure", ["native_unknown", "activation_unknown"])
def test_pending_registration_cannot_login_or_reenroll_after_unknown(lifecycle, monkeypatch, failure):
    service, protected, native = lifecycle
    if failure == "activation_unknown":
        protected.allow_activation = False
    else:
        from app.domains.recovery.gcp_contracts import AmbiguousCommit
        original = native.rpc.commit
        def unknown(transaction, writes):
            original(transaction, writes)  # Actual native persistence followed by lost acknowledgement.
            raise AmbiguousCommit("Synthetic native acknowledgement was lost")
        monkeypatch.setattr(native.rpc, "commit", unknown)
    with pytest.raises(GuardUnavailable):
        register(service)
    with service.sessions() as db:
        assert db.query(CandidatePasswordAccount).one().state == "PENDING"
    with pytest.raises(GuardDenied):
        service.login(email="candidate@example.com", password=PASSWORD)
    before = len(native.commits)
    with pytest.raises(GuardDenied):
        register(service)
    assert len(native.commits) == before


@pytest.mark.parametrize("mutation", ["generation", "digest", "password_hash", "subject", "binding"])
def test_sql_restore_or_mutation_cannot_create_new_retained_session(lifecycle, mutation):
    service, protected, native = lifecycle
    uid = register(service)
    with service.sessions() as db:
        user, row = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
        if mutation == "generation":
            row.auth_generation = 2
        if mutation == "digest":
            row.credential_sha256 = "0" * 64
        if mutation == "password_hash":
            user.password_hash = hash_password(PASSWORD)
        if mutation == "subject":
            row.subject_uuid = str(uuid4())
        if mutation == "binding":
            row.account_binding_id = str(uuid4())
        db.commit()
    before = len(native.commits)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        service.login(email="candidate@example.com", password=PASSWORD)
    assert len(native.commits) == before


def test_session_final_activation_unknown_withholds_context(lifecycle):
    service, protected, _ = lifecycle
    register(service)
    protected.allow_activation = False
    with pytest.raises(GuardUnavailable):
        service.login(email="candidate@example.com", password=PASSWORD)


@pytest.mark.parametrize("operation", ["logout", "reset", "delete"])
def test_unknown_retained_denial_happens_before_sql_ack_and_rejects_old_session(lifecycle, operation):
    service, protected, _ = lifecycle
    uid = register(service)
    result = service.login(email="candidate@example.com", password=PASSWORD)
    with service.sessions() as db:
        original_hash = db.get(User, uid).password_hash
    protected.unknown_denial = True
    with pytest.raises(GuardUnavailable):
        if operation == "logout":
            service.logout(result.context)
        if operation == "reset":
            service.change_password(result.context, current_password=PASSWORD, new_password=NEW_PASSWORD)
        if operation == "delete":
            service.prepare_delete(result.context)
    with service.sessions() as db:
        assert db.get(User, uid).password_hash == original_hash
        assert db.get(CandidatePasswordAccount, uid).auth_generation == 1
    with pytest.raises(GuardDenied):
        service.validate(result.context, auth_generation=1)


def test_legacy_password_row_never_becomes_candidate_identity_at_login(lifecycle):
    service, _, native = lifecycle
    with service.sessions() as db:
        db.add(User(email="legacy@example.com", password_hash=hash_password(PASSWORD)))
        db.commit()
    before = len(native.commits)
    with pytest.raises(GuardDenied):
        service.login(email="legacy@example.com", password=PASSWORD)
    assert len(native.commits) == before


def test_http_registration_login_bearer_and_logout_use_genuine_credential_native_issuer(lifecycle, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.database import get_db
    from app.domains.candidate_accounts import service as service_module
    from app.rate_limiter import limiter
    from app.routers import auth, candidate_accounts
    from app.domains.candidate_ingress import replay
    from backend.tests.fixtures.candidate_ingress import create, signed_headers
    import json
    service, _, _ = lifecycle
    monkeypatch.setattr(service_module, "production_candidate_accounts", lambda: service)
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED", "true")
    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", lambda: service)
    limiter._storage.reset()
    app = FastAPI()
    app.state.limiter = limiter
    app.include_router(auth.router, prefix="/api")
    app.include_router(candidate_accounts.router, prefix="/api")
    def database():
        with service.sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    ingress = create()
    monkeypatch.setattr(replay, "production_candidate_ingress", lambda: ingress.ingress)
    with TestClient(app, base_url="https://testserver") as client:
        def post(path, value=None, authorization=""):
            raw = json.dumps(value).encode() if value is not None else b""
            return client.post(path, content=raw, headers=signed_headers(ingress, path, raw, authorization=authorization))
        registered = post("/api/auth/register", {"email": "candidate@example.com",
            "password": PASSWORD, "accepted_terms": True, "confirmed_age_18": True})
        assert registered.status_code == 200 and registered.json()["ai_credits"] == 50
        authenticated = post("/api/auth/login", {"email": "candidate@example.com", "password": PASSWORD})
        assert authenticated.status_code == 200
        body = authenticated.json()
        assert body["browser_pairing_session"]["candidate_id"] == registered.json()["id"]
        bearer = {"Authorization": "Bearer " + body["access_token"]}
        assert client.get("/api/auth/me", headers=bearer).status_code == 200
        assert post("/api/auth/candidate/v1/logout", authorization=bearer["Authorization"]).status_code == 200
        assert client.get("/api/auth/me", headers=bearer).status_code == 401
    ingress.channel.close()


def test_genuine_bcrypt_credential_native_enrollment_login_keeps_exact_commitment(lifecycle, monkeypatch):
    import bcrypt

    from app.domains.candidate_accounts import service as service_module
    service, _, native = lifecycle
    encoded = bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()
    assert bcrypt.checkpw(PASSWORD.encode(), encoded.encode())
    monkeypatch.setattr(service_module, "hash_password", lambda value: encoded)
    uid = register(service)
    result = service.login(email="candidate@example.com", password=PASSWORD)
    with service.sessions() as db:
        user, row = db.get(User, uid), db.get(CandidatePasswordAccount, uid)
        assert user.password_hash == encoded
        assert row.credential_sha256 == credential_digest(encoded)
        assert native.registry.read("password_credential_revisions", row.subject_uuid)["credential_sha256"] == credential_digest(encoded)
    service.validate(result.context, auth_generation=1)
    with pytest.raises(GuardDenied):
        service.login(email="candidate@example.com", password="wrong-password")
