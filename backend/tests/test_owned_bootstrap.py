"""Actual bearer/native composition with intercepted SDKs, never provider qualification."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, event, update
from sqlalchemy.orm import Session

from app import security
from app.database import get_db
from app.domains.candidate_accounts import bootstrap
from app.domains.candidate_accounts import service as account_service
from app.domains.credits import availability
from app.domains.credits import service as credit_service
from app.domains.credits.models import CreditAccount, CreditGrant, CreditReservation
from app.domains.recovery.store import GuardDenied
from app.feature_flags import decisions_for_user
from app.models import CandidatePasswordAccount, User, UserProfile
from app.routers import auth
from tests.test_candidate_production_factory import PASSWORD, configured  # noqa: F401

PATH = "/api/auth/bootstrap"
EMAIL = "bootstrap-owner@example.com"


@pytest.fixture
def api(configured, monkeypatch):  # noqa: F811
    c = configured
    for name in ("UNIFIED_CREDITS_ENABLED", "UNIFIED_CREDIT_EXECUTION_ENABLED", "FREE_CORE_ENABLED"):
        monkeypatch.setenv(name, "false")
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", raising=False)
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "false")
    monkeypatch.setenv("CANDIDATE_METADATA_ADMISSION_ENABLED", "false")
    owner = c.accounts.register(email=EMAIL, password=PASSWORD, policy_version="2026-10-08")
    session = c.accounts.login(email=EMAIL, password=PASSWORD)
    token = security.create_access_token(subject=str(owner), candidate_lifetime={
        "version": 1, "context": session.context.model_dump(mode="json"), "auth_generation": 1})
    validation = Mock(wraps=c.accounts.validate)
    monkeypatch.setattr(c.accounts, "validate", validation)
    monkeypatch.setattr(account_service, "production_candidate_accounts", lambda: c.accounts)
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    def database():
        with c.accounts.sessions() as db:
            yield db
    app.dependency_overrides[get_db] = database
    with TestClient(app, raise_server_exceptions=False) as client:
        yield SimpleNamespace(c=c, client=client, owner=owner, token=token,
            validation=validation, headers={"Authorization": f"Bearer {token}"})


def _reads_only(engine):
    calls = SimpleNamespace(statements=[], commits=0, flushes=0)
    def sql(_connection, _cursor, statement, _parameters, _context, _many):
        calls.statements.append(statement)
    def commit(_connection):
        calls.commits += 1
    def flush(_session, _context, _instances):
        calls.flushes += 1
    event.listen(engine, "before_cursor_execute", sql)
    event.listen(engine, "commit", commit)
    event.listen(Session, "before_flush", flush)
    def remove():
        event.remove(engine, "before_cursor_execute", sql)
        event.remove(engine, "commit", commit)
        event.remove(Session, "before_flush", flush)
    return calls, remove


def _request(api):
    return api.client.get(PATH, headers=api.headers)


def test_one_real_native_authorization_per_request_and_second_request_is_fresh(api):
    c = api.c
    baseline = c.native.sdk.admin.transport.get_database.call_count
    native_writes = list(c.native.calls)
    for attempt in (1, 2):
        response = _request(api)
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["schema_version"] == "owned-bootstrap-v1" and body["unavailable"] == []
        assert body["profile"]["email"] == EMAIL
        assert body["features"] == decisions_for_user(user_id=api.owner, email=EMAIL)
        assert body["credit_wallet"]["available_credits"] == 0
        assert api.validation.call_count == attempt
        assert c.native.sdk.admin.transport.get_database.call_count - baseline == 4 * attempt
        assert c.native.calls == native_writes
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["pragma"] == "no-cache"
        assert not {"id", "password_hash", "candidate_lifetime", "account_binding_id"} & body.keys()


def test_no_profile_wallet_or_financial_writes_on_default_projection(api):
    c = api.c
    with c.accounts.sessions() as db:
        db.execute(delete(UserProfile).where(UserProfile.user_id == api.owner))
        db.commit()
    observed, remove = _reads_only(c.source)
    try:
        response = _request(api)
    finally:
        remove()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"]["profile_completeness"] == 0
    assert body["profile"]["years_experience"] == 0 and body["profile"]["skills"] == []
    assert body["profile"]["full_name"] is None and body["credit_wallet"]["available_credits"] == 0
    assert body["credit_availability"] == {
        "catalog_enabled": False, "execution_enabled": False, "supported_operations": [],
        "free_core_operations": [], "free_core_limits": {},
    }
    assert observed.commits == observed.flushes == 0
    assert observed.statements and all(sql.lstrip().upper().startswith("SELECT") for sql in observed.statements)
    with c.accounts.sessions() as db:
        assert db.query(UserProfile).filter_by(user_id=api.owner).count() == 0
        assert db.query(CreditAccount).count() == db.query(CreditGrant).count() == db.query(CreditReservation).count() == 0


def test_existing_saved_profile_legacy_balances_and_wallet_survive_paused_catalog(api):
    c = api.c
    with c.accounts.sessions() as db:
        user = db.get(User, api.owner)
        user.ai_credits, user.job_service_credits = 47, 23
        db.query(UserProfile).filter_by(user_id=api.owner).one().full_name = "Saved Candidate"
        # Synthetic old paid-lot state only; no purchase, invoice or qualification is claimed.
        credit_service.grant_credits(db, user_id=api.owner, quantity=17,
            source_type="payment_order", source_id="synthetic-existing-order", funding_kind="paid",
            terms_snapshot={"synthetic": True}, expires_at=datetime.now(UTC) + timedelta(days=10))
        db.commit()
    observed, remove = _reads_only(c.source)
    try:
        response = _request(api)
    finally:
        remove()
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"]["full_name"] == "Saved Candidate"
    assert body["profile"]["ai_credits"] == 47
    assert body["credit_availability"]["catalog_enabled"] is False
    assert body["credit_wallet"]["available_credits"] == 17
    assert body["credit_wallet"]["lots"][0]["remaining_credits"] == 17
    assert observed.commits == observed.flushes == 0
    with c.accounts.sessions() as db:
        assert db.get(User, api.owner).job_service_credits == 23
        assert db.query(CreditGrant).filter_by(user_id=api.owner).one().remaining_credits == 17


def test_two_genuinely_enrolled_owners_receive_only_their_own_profile_and_wallet(api):
    c = api.c
    second = c.accounts.register(email="bootstrap-other@example.com", password=PASSWORD,
        policy_version="2026-10-08")
    session = c.accounts.login(email="bootstrap-other@example.com", password=PASSWORD)
    second_token = security.create_access_token(subject=str(second), candidate_lifetime={
        "version": 1, "context": session.context.model_dump(mode="json"), "auth_generation": 1})
    with c.accounts.sessions() as db:
        for identity, name, amount in ((api.owner, "First private profile", 5), (second, "Second private profile", 31)):
            db.query(UserProfile).filter_by(user_id=identity).one().full_name = name
            credit_service.grant_credits(db, user_id=identity, quantity=amount,
                source_type="payment_order", source_id=f"synthetic-prior-{identity}", funding_kind="paid",
                terms_snapshot={"synthetic": True}, expires_at=datetime.now(UTC) + timedelta(days=10))
        db.commit()
    first = _request(api)
    second_response = api.client.get(PATH, headers={"Authorization": f"Bearer {second_token}"})
    assert first.status_code == second_response.status_code == 200
    assert first.json()["profile"]["full_name"] == "First private profile"
    assert first.json()["credit_wallet"]["available_credits"] == 5
    assert "Second private profile" not in first.text
    assert second_response.json()["profile"]["full_name"] == "Second private profile"
    assert second_response.json()["credit_wallet"]["available_credits"] == 31
    assert "First private profile" not in second_response.text


def test_complete_wallet_beyond_supported_lot_bound_is_unavailable_without_truncating(api):
    instant = datetime.now(UTC)
    with api.c.accounts.sessions() as db:
        db.add_all([CreditGrant(id=f"synthetic-lot-{i}", user_id=api.owner,
            source_type="synthetic_test", source_id=str(i), origin_type="synthetic_test", origin_id=str(i),
            funding_kind="paid", quantity=1, remaining_credits=1, expired_credits=0,
            issued_at=instant, expires_at=instant + timedelta(days=10), terms_snapshot={"synthetic": True})
            for i in range(5001)])
        db.commit()
    response = _request(api)
    assert response.status_code == 200, response.text
    assert response.json()["credit_wallet"] is None
    assert response.json()["unavailable"] == ["credit_wallet"]
    assert response.json()["profile"]["email"] == EMAIL
    with api.c.accounts.sessions() as db:
        assert db.query(CreditGrant).filter_by(user_id=api.owner).count() == 5001


def test_changed_native_incarnation_denies_before_any_component(api, monkeypatch):
    c = api.c
    spies = []
    for name in ("profile_view", "decisions_for_user", "availability_view", "wallet"):
        spy = Mock(side_effect=AssertionError("A denied native request reached a component"))
        spies.append(spy)
        monkeypatch.setattr(bootstrap, name, spy)
    c.native.identities[c.execution.database] = str(uuid4())
    response = _request(api)
    assert response.status_code == 503
    assert api.validation.call_count == 1
    assert all(spy.call_count == 0 for spy in spies)
    assert "profile" not in response.json() and EMAIL not in response.text


def test_unavailable_wallet_is_null_not_a_successful_zero_and_keeps_other_components(api, monkeypatch):
    monkeypatch.setattr(bootstrap, "wallet", Mock(side_effect=RuntimeError("synthetic-private-driver-detail")))
    response = _request(api)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["credit_wallet"] is None and body["unavailable"] == ["credit_wallet"]
    assert body["profile"]["email"] == EMAIL and body["features"] is not None
    assert body["credit_availability"]["execution_enabled"] is False
    assert "synthetic-private" not in response.text


@pytest.mark.parametrize("name,attribute,value", [
    ("features", "decisions_for_user", {"career_workspace": True}),
    ("credit_wallet", "wallet", {"available_credits": "bad"}),
    ("profile", "profile_view", {"email": "invalid"}),
    ("credit_availability", "availability_view", {"catalog_enabled": "bad"}),
])
def test_malformed_component_isolated_instead_of_corrupting_successful_owned_reads(api, monkeypatch, name, attribute, value):
    monkeypatch.setattr(bootstrap, attribute, Mock(return_value=value))
    response = _request(api)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[name] is None and body["unavailable"] == [name]
    if name != "profile":
        assert body["profile"]["email"] == EMAIL


@pytest.mark.parametrize("name", bootstrap.COMPONENTS)
def test_each_complete_component_has_a_finite_serialized_bound(api, monkeypatch, name):
    # Lower just the source's existing bound, leaving the actual reader/model in place.
    monkeypatch.setitem(bootstrap.MAX_COMPONENT_BYTES, name, 1)
    response = _request(api)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body[name] is None and body["unavailable"] == [name]
    if name != "profile":
        assert body["profile"]["email"] == EMAIL


def test_real_oversized_saved_profile_is_not_truncated_or_replaced_with_zero(api):
    with api.c.accounts.sessions() as db:
        db.query(UserProfile).filter_by(user_id=api.owner).one().bio = "x" * 65_536
        db.commit()
    response = _request(api)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["profile"] is None and body["unavailable"] == ["profile"]
    assert body["credit_wallet"]["available_credits"] == 0
    with api.c.accounts.sessions() as db:
        assert len(db.query(UserProfile).filter_by(user_id=api.owner).one().bio) == 65_536


@pytest.mark.parametrize("change", ["erase", "credential_epoch", "auth_generation", "account_state", "account_binding"])
def test_mid_read_owner_or_credential_generation_change_refuses_the_whole_response(api, monkeypatch, change):
    original = bootstrap.decisions_for_user
    def changed(**kwargs):
        value = original(**kwargs)
        with api.c.accounts.sessions() as other:
            if change == "erase":
                other.execute(delete(User).where(User.id == api.owner))
            elif change == "credential_epoch":
                other.execute(update(User).where(User.id == api.owner).values(credential_epoch=1))
            else:
                field, value_to_set = ({"auth_generation": ("auth_generation", 2),
                    "account_state": ("state", "DELETE_DENIED"),
                    "account_binding": ("account_binding_id", str(uuid4()))}[change])
                other.execute(update(CandidatePasswordAccount).where(CandidatePasswordAccount.user_id == api.owner)
                    .values(**{field: value_to_set}))
            other.commit()
        return value
    monkeypatch.setattr(bootstrap, "decisions_for_user", changed)
    response = _request(api)
    assert response.status_code == 401, response.text
    assert response.json() == {"detail": "Not authenticated"}
    assert EMAIL not in response.text


def test_subject_erased_after_native_validation_is_not_exported(api, monkeypatch):
    original = api.c.accounts.validate.__wrapped__ if hasattr(api.c.accounts.validate, "__wrapped__") else api.validation._mock_wraps
    def erase_after_validation(*args, **kwargs):
        original(*args, **kwargs)
        with api.c.accounts.sessions() as other:
            other.execute(delete(User).where(User.id == api.owner))
            other.commit()
    api.validation.side_effect = erase_after_validation
    response = _request(api)
    assert response.status_code == 401, response.text
    assert EMAIL not in response.text


def test_transient_and_other_session_owner_cannot_enter_domain_projection(api):
    with api.c.accounts.sessions() as db, api.c.accounts.sessions() as other:
        foreign_session_owner = other.get(User, api.owner)
        for invalid in (User(id=api.owner, email="fabricated@example.com"), foreign_session_owner):
            with pytest.raises(GuardDenied):
                bootstrap.bootstrap_view(db, invalid)


@pytest.mark.parametrize("suffix,headers", [
    ("?owner_id=2", {}), ("?component=profile", {}), ("", {"Content-Encoding": "gzip"}),
    ("", {"Content-Length": "1"}),
])
def test_new_route_refuses_unbounded_selectors_or_nonempty_get(api, suffix, headers):
    response = api.client.get(PATH + suffix, headers=api.headers | headers)
    assert response.status_code == 422
    assert response.json() == {"detail": "Owned bootstrap accepts only an empty GET"}


def test_new_route_refuses_nonempty_chunked_get_without_a_length(api):
    request = api.client.build_request("GET", PATH, headers=api.headers, content=b"x")
    del request.headers["Content-Length"]
    request.headers["Transfer-Encoding"] = "chunked"
    response = api.client.send(request)
    assert response.status_code == 422
    assert response.json() == {"detail": "Owned bootstrap accepts only an empty GET"}


def test_absent_bearer_never_enters_native_or_component_reads(api, monkeypatch):
    projection = Mock(side_effect=AssertionError("Anonymous request reached owned data"))
    monkeypatch.setattr(auth, "bootstrap_view", projection)
    response = api.client.get(PATH)
    assert response.status_code == 401
    assert api.validation.call_count == projection.call_count == 0


def test_real_availability_retains_the_existing_unqualified_funding_gate(api, monkeypatch):
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", "{}")
    response = _request(api)
    assert response.status_code == 200, response.text
    with api.c.accounts.sessions() as db:
        current = availability.availability_view(db, db.get(User, api.owner))
    assert response.json()["credit_availability"] == current
    assert current["execution_enabled"] is False and current["supported_operations"] == []
