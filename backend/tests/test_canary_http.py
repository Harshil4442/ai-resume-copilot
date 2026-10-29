"""Authenticated explicit consent transport, with synthetic accounts and no model calls."""
from uuid import uuid4

import pytest
from backend.app.database import get_db
from backend.app.domains.credits import canary, service
from backend.app.domains.credits.models import CreditReservation, CreditServiceQuote
from backend.app.models import AdminAuditEvent, ModelCallEvent, User
from backend.app.routers import credits
from backend.app.security import get_current_user
from backend.tests import test_unified_synchronous as synchronous_checks
from backend.tests.test_canary_admission import prepare
from backend.tests.test_unified_credit_http import factory
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

assert factory


@pytest.fixture
def api(factory, monkeypatch):
    synchronous_checks.setup.__wrapped__(factory, monkeypatch)
    config = synchronous_checks.configure(monkeypatch, "enhanced_qa")
    with factory() as db:
        _, review, digest = prepare(db, monkeypatch, config=config)
        db.commit()
    app = FastAPI()
    app.include_router(credits.router, prefix="/api")
    def database():
        with factory() as db:
            yield db
    def owner(db=Depends(get_db), fixture_owner: int = Header(default=1)):
        return db.get(User, fixture_owner)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    with TestClient(app, client=("canary-" + uuid4().hex, 50000)) as client:
        yield client, factory, config, review, digest


def choice(review, digest, *, action="opt_in", key="explicit-fixture-choice"):
    return {"review_id": review.id, "review_sha256": digest, "notice_sha256": review.notice_sha256,
        "choice": action, "confirmed_notice": True, "idempotency_key": key}


def test_owner_must_actually_opt_in_to_exact_notice_and_cannot_choose_for_another_account(api, monkeypatch):
    client, factory, _, review, digest = api
    observed = client.get("/api/v1/credits/canary-enrollment")
    assert observed.status_code == 200 and "no-store" in observed.headers["cache-control"]
    assert observed.json()["status"] == "NOT_OPTED_IN"
    assert "selected_owner_ids" not in observed.json() and "members" not in observed.json()
    payload = choice(review, digest)
    assert client.post("/api/v1/credits/canary-enrollment", json=payload | {"confirmed_notice": False}).status_code == 422
    assert client.post("/api/v1/credits/canary-enrollment", json=payload | {"user_id": 2}).status_code == 422
    assert client.post("/api/v1/credits/canary-enrollment", json=payload,
        headers={"fixture-owner": "40"}).status_code == 409
    assert client.get("/api/v1/credits/canary-enrollment", headers={"fixture-owner": "40"}).json() == {"status": "NOT_AVAILABLE"}
    first = client.post("/api/v1/credits/canary-enrollment", json=payload)
    assert first.status_code == 200 and first.json()["sequence"] == 1
    assert client.post("/api/v1/credits/canary-enrollment", json=payload).json() == first.json()
    assert client.get("/api/v1/credits/canary-enrollment").json()["status"] == "OPTED_IN"
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    monkeypatch.delenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON")
    paused = client.get("/api/v1/credits/canary-enrollment").json()
    assert paused["status"] == "OPTED_IN" and paused["can_opt_in"] is False
    assert paused["execution_flag_enabled"] is False
    revoked = client.post("/api/v1/credits/canary-enrollment", json=choice(review, digest, action="revoke", key="explicit-fixture-revoke"))
    assert revoked.status_code == 200 and revoked.json()["sequence"] == 2
    with factory() as db:
        assert db.query(AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).count() == 2
        assert db.query(CreditReservation).count() == db.query(ModelCallEvent).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40


def test_quote_and_accept_recheck_live_consent_and_census_without_charging_on_denial(api):
    client, factory, _, review, digest = api
    payload = synchronous_checks.payload("enhanced_qa")
    body = payload.model_dump()
    headers = {"Idempotency-Key": "fixture-canary-quote"}
    assert client.post("/api/v1/credits/synchronous-service-quotes", json=body, headers=headers).status_code == 503
    assert client.post("/api/v1/credits/canary-enrollment", json=choice(review, digest)).status_code == 200
    quoted = client.post("/api/v1/credits/synchronous-service-quotes", json=body, headers=headers)
    assert quoted.status_code == 200, quoted.text
    with factory() as db:
        # An eligibility identity changed after quoting. No stale authority may
        # reserve money even though the accepted quote itself is still retained.
        db.get(User, 40).privacy_version = "changed-notice"
        db.commit()
        with pytest.raises(HTTPException) as caught:
            synchronous_checks.invoke(db, payload, quoted.json()["quote_id"])
        assert caught.value.status_code == 409
        assert caught.value.detail["run_created"] is False
        assert db.query(CreditServiceQuote).count() == 1
        assert db.query(CreditReservation).count() == db.query(ModelCallEvent).count() == 0
        assert service.wallet(db, user_id=1).available_credits == 40
    assert client.post("/api/v1/credits/synchronous-service-quotes", json=body,
        headers={"Idempotency-Key": "fixture-canary-quote-after-change"}).status_code == 503


@pytest.mark.parametrize("pointer", ["cleared", "rotated"])
def test_old_review_remains_owned_readable_revocable_after_pointer_change(api, monkeypatch, pointer):
    client, factory, _, review, digest = api
    original = choice(review, digest)
    assert client.post("/api/v1/credits/canary-enrollment", json=original).status_code == 200
    if pointer == "cleared":
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_ID")
        monkeypatch.delenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256")
    else:
        monkeypatch.setenv("UNIFIED_CREDIT_CANARY_REVIEW_ID", "another-current-review")
        monkeypatch.setenv("UNIFIED_CREDIT_CANARY_REVIEW_SHA256", "f" * 64)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    selectors = {"review_id": review.id, "review_sha256": digest}
    for params in ({}, selectors):
        retained = client.get("/api/v1/credits/canary-enrollment", params=params).json()
        assert retained["status"] == "OPTED_IN" and retained["can_opt_in"] is False
        assert retained["review_sha256"] == digest
    assert client.get("/api/v1/credits/canary-enrollment", params=selectors,
        headers={"fixture-owner": "40"}).json() == {"status": "NOT_AVAILABLE"}
    revoked = client.post("/api/v1/credits/canary-enrollment", json=choice(
        review, digest, action="revoke", key="after-current-pointer-rotation"))
    assert revoked.status_code == 200 and revoked.json()["sequence"] == 2
    assert client.post("/api/v1/credits/canary-enrollment", json=original).status_code == 200  # receipt, not reenrollment
    assert client.get("/api/v1/credits/canary-enrollment", params=selectors).json()["status"] == "NOT_OPTED_IN"
    fresh = choice(review, digest, key="new-opt-in-to-old-review")
    assert client.post("/api/v1/credits/canary-enrollment", json=fresh).status_code == 409
    assert client.post("/api/v1/credits/canary-enrollment", json=choice(
        review, digest, action="revoke", key="other-owner-cannot-revoke"), headers={"fixture-owner": "40"}).status_code == 409
    with factory() as db:
        assert db.query(AdminAuditEvent).filter_by(action=canary.CONSENT_ACTION).count() == 2
        assert db.query(CreditReservation).count() == 0
