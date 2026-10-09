"""Actual native/protected denial recovery from committed owned credentials."""
from __future__ import annotations

import json
from uuid import uuid4

import pytest
from test_candidate_scoped_lifecycle_emulator import service as service
from test_gcp_pairing_emulator import case as case
from test_gcp_password_lifetime_emulator import lifetime as lifetime
from test_gcp_scoped_authority_emulator import scoped as scoped

from app.domains.candidate_accounts.service import revision_from_row
from app.domains.recovery.gcp_candidate_logout import GcpCandidateCookieLogout
from app.domains.recovery.gcp_contracts import AmbiguousCommit
from app.domains.recovery.store import GuardDenied, GuardUnavailable
from app.models import CandidatePasswordAccount, User

EMAIL = "web-candidate@example.invalid"
PASSWORD = "synthetic-web-password-only-20261009"  # gitleaks:allow -- synthetic fixture credential


def fresh(c):
    c.account_service.cookie_logout = GcpCandidateCookieLogout(c.scoped)
    c.account_service.register(email=EMAIL, password=PASSWORD, policy_version="synthetic")
    return c.account_service.login(email=EMAIL, password=PASSWORD)


def test_password_verified_status_is_read_only_and_never_issues_session(service):
    c = service
    login = fresh(c)
    count = len(c.http.objects)
    assert c.account_service.registration_status(email=EMAIL, password=PASSWORD) == "ENROLLED"
    assert len(c.http.objects) == count
    with pytest.raises(GuardDenied):
        c.account_service.registration_status(email=EMAIL, password="wrong-synthetic-password")
    c.account_service.validate(login.context, auth_generation=1)


def test_cookie_logout_retains_exact_request_denial_without_closing_other_sessions(service):
    c = service
    first = fresh(c)
    second = c.account_service.login(email=EMAIL, password=PASSWORD)
    request = uuid4()
    c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=request)
    with pytest.raises(GuardDenied):
        c.account_service.validate(first.context, auth_generation=1)
    c.account_service.validate(second.context, auth_generation=1)
    # Restarted adapter may verify only the exact original denial fact.
    c.account_service.cookie_logout = GcpCandidateCookieLogout(c.scoped)
    c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=request)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=uuid4())


@pytest.mark.parametrize("persisted", [False, True])
def test_unknown_logout_keeps_cookie_gate_closed_until_same_request_retained_proof(service, monkeypatch, persisted):
    c = service
    first = fresh(c)
    request = uuid4()
    commit = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if any(json.loads(write.raw)["namespace"] == "v3_pending_session_denials" for write in writes):
            if persisted:
                commit(tx, writes)
            else:
                c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Owned test logout acknowledgement loss")
        return commit(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable):
        c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=request)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", commit)
    if persisted:
        with pytest.raises(GuardDenied):
            c.account_service.validate(first.context, auth_generation=1)
    else:
        c.account_service.validate(first.context, auth_generation=1)
    c.account_service.cookie_logout = GcpCandidateCookieLogout(c.scoped)
    c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=request)
    with pytest.raises(GuardDenied):
        c.account_service.validate(first.context, auth_generation=1)


def test_unknown_registration_status_never_adopts_activation_or_allows_login(service, monkeypatch):
    c = service
    commit = c.v3.registry.rpc.commit
    def lose(tx, writes):
        if any(json.loads(write.raw)["namespace"] == "v3_activations" for write in writes):
            c.v3.registry.rpc.rollback(tx)
            raise AmbiguousCommit("Owned registration acknowledgement loss")
        return commit(tx, writes)
    monkeypatch.setattr(c.v3.registry.rpc, "commit", lose)
    with pytest.raises(GuardUnavailable):
        c.account_service.register(email=EMAIL, password=PASSWORD, policy_version="synthetic")
    monkeypatch.setattr(c.v3.registry.rpc, "commit", commit)
    before = len(c.commits), len(c.http.objects)
    assert c.account_service.registration_status(email=EMAIL, password=PASSWORD) == "PENDING_REVIEW_REQUIRED"
    assert (len(c.commits), len(c.http.objects)) == before
    with pytest.raises(GuardDenied):
        c.account_service.login(email=EMAIL, password=PASSWORD)
    with c.account_service.sessions() as db:
        assert db.query(CandidatePasswordAccount).one().state == "PENDING"


def test_retained_denial_cannot_clear_cookie_for_other_context_revision_or_missing_provenance(service):
    c = service
    first = fresh(c)
    second = c.account_service.login(email=EMAIL, password=PASSWORD)
    request = uuid4()
    c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=request)
    with pytest.raises(GuardDenied):
        c.account_service.logout_cookie(second.context, auth_generation=1, operation_id=request)
    with c.account_service.sessions() as db:
        revision = revision_from_row(db.query(CandidatePasswordAccount).one(), db.query(User).one().password_hash)
    wrong = revision.model_copy(update={"credential_sha256": "a" * 64})
    with pytest.raises(GuardDenied):
        c.account_service.cookie_logout.deny_cookie_session(first.context, wrong, request)
    record = c.v3.registry.read("v3_records", str(request))
    altered = dict(record)
    altered["intent"] = {**record["intent"], "deadline_ms": record["intent"]["deadline_ms"] + 1}
    c.v3.registry.run(lambda tx: tx.put("v3_records", str(request), altered))
    with pytest.raises((GuardDenied, GuardUnavailable, ValueError)):
        c.account_service.logout_cookie(first.context, auth_generation=1, operation_id=request)


def test_expired_session_can_only_retain_denial_and_clear_its_cookie(service):
    c = service
    login = fresh(c)
    c.now = login.expires_at_ms + 1
    with pytest.raises(GuardDenied):
        c.account_service.validate(login.context, auth_generation=1)
    request = uuid4()
    c.account_service.logout_cookie(login.context, auth_generation=1, operation_id=request)
    c.account_service.logout_cookie(login.context, auth_generation=1, operation_id=request)
    with pytest.raises(GuardDenied):
        c.account_service.validate(login.context, auth_generation=1)


def test_definite_account_deletion_cookie_confirmation_never_validates_or_resurrects_account(service):
    c = service
    login = fresh(c)
    unrelated = c.account_service.login(email=EMAIL, password=PASSWORD)
    c.account_service.prepare_delete(login.context)
    c.account_service.logout_cookie(login.context, auth_generation=1, operation_id=uuid4())
    with pytest.raises(GuardDenied):
        c.account_service.validate(login.context, auth_generation=1)
    with pytest.raises((GuardDenied, GuardUnavailable)):
        c.account_service.logout_cookie(unrelated.context, auth_generation=1, operation_id=uuid4())
    with pytest.raises(GuardDenied):
        c.account_service.login(email=EMAIL, password=PASSWORD)


def test_actual_signed_cookie_denial_route_survives_sql_erasure_only_for_original_delete_owner(service, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import candidate_accounts
    from app.security import create_access_token
    c = service
    login = fresh(c)
    c.account_service.prepare_delete(login.context)
    with c.account_service.sessions() as db:
        row = db.query(CandidatePasswordAccount).one()
        db.delete(row)
        db.delete(db.query(User).one())
        db.commit()
    app = FastAPI()
    app.include_router(candidate_accounts.router, prefix="/api")
    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", lambda: c.account_service)
    claims = {"version": 1, "context": login.context.model_dump(mode="json"), "auth_generation": 1}
    token = create_access_token(subject=str(login.context.candidate_id), candidate_lifetime=claims)
    with TestClient(app) as client:
        result = client.post("/api/auth/candidate/v1/web-logout", json={"operation_id": str(uuid4())},
            headers={"Authorization": "Bearer " + token})
        assert result.status_code == 200 and result.json() == {"status": "COOKIE_DENIAL_RETAINED"}
        claims["auth_generation"] = 2
        wrong = create_access_token(subject=str(login.context.candidate_id), candidate_lifetime=claims)
        result = client.post("/api/auth/candidate/v1/web-logout", json={"operation_id": str(uuid4())},
            headers={"Authorization": "Bearer " + wrong})
        assert result.status_code == 401
    with c.account_service.sessions() as db:
        assert db.query(User).count() == 0 and db.query(CandidatePasswordAccount).count() == 0
