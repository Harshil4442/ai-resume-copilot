import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from backend.app.billing.catalog import CATALOG_VERSION
from backend.app.billing.razorpay import RazorpayAdapter
from backend.app.database import Base, get_db
from backend.app.models import (
    EntitlementLedger,
    PaymentEvent,
    PaymentOrder,
    PaymentRefund,
    PaymentTransaction,
    User,
    UserProfile,
)
from backend.app.rate_limiter import limiter
from backend.app.routers import billing
from backend.app.routers.auth import delete_account
from backend.app.security import get_current_user
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TEST_KEY_ID = "rzp_test_hirewiz123456"
TEST_KEY_SECRET = "test_key_secret_for_checkout_hmac"
TEST_WEBHOOK_SECRET = "test_webhook_secret_is_long_enough"


@pytest.fixture()
def session_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    with factory() as db:
        user = User(email="buyer@example.com", password_hash="x", ai_credits=20)
        db.add(user)
        db.commit()
        db.refresh(user)
        db.add(UserProfile(user_id=user.id))
        db.commit()
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def client(session_factory):
    try:
        limiter._storage.reset()
    except Exception:
        pass

    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.include_router(billing.router, prefix="/api")
    app.include_router(billing.public_router, prefix="/api")

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    def override_user(db=Depends(get_db)):
        return db.query(User).filter(User.email == "buyer@example.com").first()

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = override_user
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def enabled_checkout(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("RAZORPAY_CHECKOUT_ENABLED", "true")
    monkeypatch.setenv("RAZORPAY_ACCOUNT_APPROVED", "true")
    monkeypatch.setenv("PAYMENTS_GO_LIVE_REVIEW_COMPLETE", "true")
    monkeypatch.setenv("RAZORPAY_MODE", "test")
    monkeypatch.setenv("RAZORPAY_KEY_ID", TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", TEST_KEY_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)

    calls = []

    def fake_create(self, *, product, local_order_id, receipt):
        assert product.amount_minor == {"starter_bundle": 64_900, "growth_bundle": 109_900, "scale_bundle": 219_900}[product.sku]
        assert product.currency == "INR"
        assert len(receipt) <= 40
        calls.append(local_order_id)
        return {
            "provider_order_id": f"order_fake_{len(calls)}",
            "notes": {},
        }

    monkeypatch.setattr(RazorpayAdapter, "create_order", fake_create)
    return calls


def _create_order(client):
    response = client.post("/api/billing/orders", json={"sku": "starter_bundle", "billing_country": "IN"})
    assert response.status_code == 200, response.text
    return response.json()


def _accepted_legacy_order(client, sku="premium_30d"):
    """An immutable accepted order from before the finite catalog cutover.

    This intentionally does not call the current checkout route: old products
    cannot be sold now, while actual webhook/refund contracts must still work.
    """
    import uuid
    from backend.app.billing.catalog import PRODUCTS
    product = PRODUCTS[sku]
    session = client.app.dependency_overrides[get_db]()
    db = next(session)
    try:
        user = db.query(User).one()
        order = PaymentOrder(public_id="ord_"+uuid.uuid4().hex, user_id=user.id,
            provider="razorpay",provider_mode="test",provider_key_id=TEST_KEY_ID,
            provider_order_id="order_"+uuid.uuid4().hex, sku=sku,
            catalog_version="inr-2026-10-08-v2", billing_type="one_time",
            entitlement_kind=product.entitlement_kind, entitlement_quantity=product.entitlement_quantity,
            billing_country="IN",billing_country_confirmed_at=datetime.now(timezone.utc),
            gross_amount_minor=product.amount_minor,refunded_amount_minor=0,currency="INR",status="created")
        db.add(order);db.commit();db.refresh(order)
        return {"order_id":order.public_id,"provider_order_id":order.provider_order_id,"amount_minor":order.gross_amount_minor}
    finally:
        session.close()


def _service_order(client):
    return _accepted_legacy_order(client,"job_service_500")


def test_paid_job_credits_can_be_purchased_by_premium_and_granted_only_once(client, enabled_checkout, session_factory):
    from backend.app.domains.employer.models import ServiceCreditEvent
    with session_factory() as db:
        user = db.query(User).one()
        user.tier = "premium"
        db.commit()
    created = _service_order(client)
    with session_factory() as db:
        assert db.query(User).one().job_service_credits == 0
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        capture = _capture_payload(order)
        second = _capture_payload(order, event="order.paid")
    assert _post_event(client, "evt_service_paid", capture).status_code == 200
    assert _post_event(client, "evt_service_paid_duplicate", second).status_code == 200
    with session_factory() as db:
        user = db.query(User).one()
        assert user.job_service_credits == 500
        assert user.ai_credits == 20
        assert db.query(ServiceCreditEvent).filter_by(event_type="grant").count() == 1


def test_service_partial_refund_revokes_proportionally_and_spent_refund_is_debt(client, enabled_checkout, session_factory):
    from backend.app.domains.employer.models import ServiceCreditEvent
    created = _service_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        capture = _capture_payload(order)
    assert _post_event(client, "evt_service_capture", capture).status_code == 200
    with session_factory() as db:
        user = db.query(User).one()
        user.job_service_credits = 100  # 400 credits already consumed by services.
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        partial = _refund_payload(order, "rfnd_service_half", 24_950)
        remainder = _refund_payload(order, "rfnd_service_rest", 24_950)
        db.commit()
    assert _post_event(client, "evt_service_half_refund", partial).status_code == 200
    assert _post_event(client, "evt_service_half_refund", partial).status_code == 200
    with session_factory() as db:
        assert db.query(User).one().job_service_credits == -150
    assert _post_event(client, "evt_service_full_refund", remainder).status_code == 200
    with session_factory() as db:
        assert db.query(User).one().job_service_credits == -400
        assert sum(event.amount for event in db.query(ServiceCreditEvent).filter_by(event_type="refund")) == -500


def test_service_refund_before_capture_does_not_regrant_refunded_units(client, enabled_checkout, session_factory):
    created = _service_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        partial = _refund_payload(order, "rfnd_service_early", 24_950)
        capture = _capture_payload(order)
    assert _post_event(client, "evt_service_early_refund", partial).status_code == 200
    assert _post_event(client, "evt_service_late_capture", capture).status_code == 200
    with session_factory() as db:
        assert db.query(User).one().job_service_credits == 250
        assert db.query(EntitlementLedger).count() == 1


def _signature(raw: bytes) -> str:
    return hmac.new(TEST_WEBHOOK_SECRET.encode(), raw, hashlib.sha256).hexdigest()


def _post_event(client, event_id: str, payload: dict):
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return client.post(
        "/api/billing/webhooks/razorpay",
        content=raw,
        headers={
            "content-type": "application/json",
            "x-razorpay-event-id": event_id,
            "x-razorpay-signature": _signature(raw),
        },
    )


def _notes(order: PaymentOrder) -> dict:
    return {
        "hirewiz_order_id": order.public_id,
        "sku": order.sku,
        "billing_country": "IN",
    }


def _payment_entity(order: PaymentOrder, *, status="captured", amount=None) -> dict:
    return {
        "id": "pay_hirewiz_1",
        "entity": "payment",
        "amount": order.gross_amount_minor if amount is None else amount,
        "currency": order.currency,
        "status": status,
        "captured": status != "failed",
        "order_id": order.provider_order_id,
        "method": "upi",
        "international": False,
        "fee": 2_500 if status != "failed" else None,
        "tax": 381 if status != "failed" else None,
        "notes": _notes(order),
    }


def _capture_payload(order: PaymentOrder, *, event="payment.captured", amount=None) -> dict:
    payload = {
        "event": event,
        "payload": {"payment": {"entity": _payment_entity(order, amount=amount)}},
    }
    if event == "order.paid":
        payload["payload"]["order"] = {
            "entity": {
                "id": order.provider_order_id,
                "entity": "order",
                "amount": order.gross_amount_minor,
                "amount_paid": order.gross_amount_minor,
                "amount_due": 0,
                "currency": order.currency,
                "receipt": f"hw_{order.public_id}",
                "status": "paid",
                "partial_payment": False,
                "notes": _notes(order),
            }
        }
    return payload


def _refund_payload(order: PaymentOrder, refund_id: str, amount: int) -> dict:
    payment = _payment_entity(order)
    payment["status"] = "refunded"
    return {
        "event": "refund.processed",
        "payload": {
            "payment": {"entity": payment},
            "refund": {
                "entity": {
                    "id": refund_id,
                    "entity": "refund",
                    "payment_id": payment["id"],
                    "amount": amount,
                    "currency": order.currency,
                    "status": "processed",
                }
            },
        },
    }


def test_catalog_and_checkout_fail_closed(client, monkeypatch, session_factory):
    for key in (
        "RAZORPAY_CHECKOUT_ENABLED",
        "RAZORPAY_ACCOUNT_APPROVED",
        "PAYMENTS_GO_LIVE_REVIEW_COMPLETE",
        "RAZORPAY_KEY_ID",
        "RAZORPAY_KEY_SECRET",
        "RAZORPAY_WEBHOOK_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    catalog_response = client.get("/api/public/billing/catalog")
    assert catalog_response.status_code == 200
    catalog = catalog_response.json()
    assert catalog["checkout_enabled"] is False
    assert catalog["provider"] is None
    products = {product["sku"]: product for product in catalog["products"]}
    assert set(products) == {"starter_bundle", "growth_bundle", "scale_bundle"}
    assert all(not product["enabled_for_purchase"] for product in products.values())
    assert products["starter_bundle"]["amount_minor"] == 64_900
    assert products["growth_bundle"]["amount_minor"] == 109_900
    assert products["growth_bundle"]["entitlement_quantity"] == 300
    assert products["growth_bundle"]["entitlement_kind"] == "credit_bundle"

    config_response = client.get("/api/billing/config")
    assert config_response.headers["cache-control"] == "no-store, private"
    create_response = client.post(
        "/api/billing/orders",
        json={"sku": "starter_bundle", "billing_country": "IN"},
    )
    assert create_response.status_code == 503
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 0


def test_recent_order_returns_null_when_checkout_has_not_started(client):
    response = client.get("/api/billing/recent-order")

    assert response.status_code == 200
    assert response.json() is None
    assert response.headers["cache-control"] == "no-store, private"


def test_production_never_enables_test_mode(client, monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RAZORPAY_CHECKOUT_ENABLED", "true")
    monkeypatch.setenv("RAZORPAY_ACCOUNT_APPROVED", "true")
    monkeypatch.setenv("PAYMENTS_GO_LIVE_REVIEW_COMPLETE", "true")
    monkeypatch.setenv("RAZORPAY_MODE", "test")
    monkeypatch.setenv("RAZORPAY_KEY_ID", TEST_KEY_ID)
    monkeypatch.setenv("RAZORPAY_KEY_SECRET", TEST_KEY_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", TEST_WEBHOOK_SECRET)
    assert client.get("/api/public/billing/catalog").json()["checkout_enabled"] is False


def test_order_contract_rejects_non_india_country(client, enabled_checkout, session_factory):
    response = client.post(
        "/api/billing/orders",
        json={"sku": "starter_bundle", "billing_country": "US"},
    )
    assert response.status_code == 422
    assert enabled_checkout == []
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 0


def test_fresh_initializing_attempt_blocks_a_second_provider_order(
    client, enabled_checkout, session_factory
):
    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        db.add(
            PaymentOrder(
                public_id="ord_initializing_collision",
                user_id=user.id,
                provider="razorpay",
                provider_mode="test",
                provider_key_id=TEST_KEY_ID,
                sku="starter_bundle",
                catalog_version=CATALOG_VERSION,
                billing_type="one_time",
                entitlement_kind="credit_bundle",
                entitlement_quantity=100,
                billing_country="IN",
                billing_country_confirmed_at=datetime.now(timezone.utc),
                gross_amount_minor=64_900,
                refunded_amount_minor=0,
                currency="INR",
                status="initializing",
                active_attempt_key=f"{user.id}:starter_bundle:test",
            )
        )
        db.commit()

    response = client.post(
        "/api/billing/orders",
        json={"sku": "starter_bundle", "billing_country": "IN"},
    )
    assert response.status_code == 409
    assert enabled_checkout == []
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 1
        assert db.query(PaymentOrder).one().status == "initializing"


def test_server_owned_order_reuse_and_mode_isolation(
    client, enabled_checkout, monkeypatch, session_factory
):
    manipulated = client.post(
        "/api/billing/orders",
        json={"sku": "starter_bundle", "billing_country": "IN", "amount_minor": 1},
    )
    assert manipulated.status_code == 422

    first = _create_order(client)
    assert first["amount_minor"] == 64_900
    assert first["currency"] == "INR"
    assert "notes" not in first
    assert first["provider_order_id"] == "order_fake_1"
    assert client.post(
        "/api/billing/orders",
        json={"sku": "starter_bundle", "billing_country": "IN"},
    ).json()[
        "order_id"
    ] == first["order_id"]
    assert len(enabled_checkout) == 1

    with session_factory() as db:
        old = db.query(PaymentOrder).filter(PaymentOrder.public_id == first["order_id"]).one()
        old.created_at = datetime.now(timezone.utc) - timedelta(minutes=31)
        db.commit()

    aged = _create_order(client)
    assert aged["order_id"] == first["order_id"]
    assert len(enabled_checkout) == 1
    with session_factory() as db:
        old = db.query(PaymentOrder).filter(PaymentOrder.public_id == first["order_id"]).one()
        assert old.status == "created"

    # Same-mode key rotation cannot pair an old provider order with a new key,
    # and cannot expose a second payable order automatically.
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_test_rotatedhirewiz123")
    rotated = client.post(
        "/api/billing/orders",
        json={"sku": "starter_bundle", "billing_country": "IN"},
    )
    assert rotated.status_code == 409
    assert len(enabled_checkout) == 1

    # A test-mode order is never returned alongside a live key.
    from backend.tests.expense_policy_fixtures import estimated_expense_policy
    approved = estimated_expense_policy() | {"review_status":"approved","reviewed_by":"synthetic-operator"}
    monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(approved))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("RAZORPAY_MODE", "live")
    monkeypatch.setenv("RAZORPAY_KEY_ID", "rzp_live_hirewiz123456")
    live = _create_order(client)
    assert live["order_id"] != first["order_id"]
    assert live["provider_order_id"] == "order_fake_2"


def test_checkout_signature_records_evidence_but_never_provisions(
    client, enabled_checkout, session_factory
):
    created = _create_order(client)
    payment_id = "pay_checkout_result"
    message = f"{created['provider_order_id']}|{payment_id}".encode()
    signature = hmac.new(TEST_KEY_SECRET.encode(), message, hashlib.sha256).hexdigest()
    response = client.post(
        f"/api/billing/orders/{created['order_id']}/checkout-result",
        json={
            "razorpay_payment_id": payment_id,
            "razorpay_order_id": created["provider_order_id"],
            "razorpay_signature": signature,
        },
    )
    assert response.status_code == 200
    assert response.json() == {"accepted": True, "status": "pending", "fulfilled": False}
    assert response.headers["cache-control"] == "no-store, private"
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        assert order.status == "client_confirmed"
        assert user.tier == "free"
        assert db.query(EntitlementLedger).count() == 0


def test_capture_is_webhook_only_atomic_and_idempotent(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        first_payload = _capture_payload(order)

    first = _post_event(client, "evt_capture_1", first_payload)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "processed"
    duplicate = _post_event(client, "evt_capture_1", first_payload)
    assert duplicate.status_code == 200
    assert duplicate.json()["status"] == "duplicate"

    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        original_expiry = db.query(User).filter(User.email == "buyer@example.com").one().premium_until
        second_payload = _capture_payload(order, event="order.paid")
    assert _post_event(client, "evt_order_paid_2", second_payload).status_code == 200

    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        tx = db.query(PaymentTransaction).one()
        assert user.tier == "premium"
        assert user.premium_until == original_expiry
        assert db.query(EntitlementLedger).count() == 1
        assert db.query(PaymentEvent).count() == 2
        assert tx.status == "captured"
        assert tx.payment_method == "upi"
        assert tx.gross_amount_minor == 99_900

    status = client.get(f"/api/billing/orders/{created['order_id']}")
    assert status.json()["status"] == "paid"
    assert status.json()["fulfilled"] is True


def test_previous_webhook_secret_is_accepted_during_rotation(
    client, enabled_checkout, session_factory, monkeypatch
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        payload = _capture_payload(order)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET_PREVIOUS", TEST_WEBHOOK_SECRET)
    monkeypatch.setenv("RAZORPAY_WEBHOOK_SECRET", "new_webhook_secret_with_32_plus_chars")
    response = _post_event(client, "evt_old_rotation_secret", payload)
    assert response.status_code == 200
    with session_factory() as db:
        assert db.query(EntitlementLedger).count() == 1


def test_amount_mismatch_is_rejected_and_replay_cannot_provision(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        payload = _capture_payload(order, amount=1)
    rejected = _post_event(client, "evt_bad_amount", payload)
    assert rejected.status_code == 400
    replay = _post_event(client, "evt_bad_amount", payload)
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"
    with session_factory() as db:
        assert db.query(EntitlementLedger).count() == 0
        event = db.query(PaymentEvent).one()
        assert event.processing_status == "rejected"
        assert event.error_code == "amount_mismatch"
        assert len(event.payload_sha256) == 64


def test_failed_payment_never_provisions(client, enabled_checkout, session_factory):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        payload = {
            "event": "payment.failed",
            "payload": {"payment": {"entity": _payment_entity(order, status="failed")}},
        }
    assert _post_event(client, "evt_failed", payload).status_code == 200
    status = client.get(f"/api/billing/orders/{created['order_id']}").json()
    assert status["status"] == "failed"
    assert status["fulfilled"] is False
    with session_factory() as db:
        assert db.query(EntitlementLedger).count() == 0
        assert db.query(PaymentTransaction).one().status == "failed"
    # Old failed orders remain readable; they are never re-offered as new sales.
    assert client.get(f"/api/billing/orders/{created['order_id']}").status_code == 200
    assert client.post("/api/billing/orders", json={"sku":"premium_30d","billing_country":"IN"}).status_code == 503
    assert enabled_checkout == []


def test_partial_and_full_refund_revoke_only_on_full_refund(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        capture = _capture_payload(order)
    assert _post_event(client, "evt_capture_refund", capture).status_code == 200

    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        partial = _refund_payload(order, "rfnd_partial_1", 50_000)
    assert _post_event(client, "evt_refund_partial", partial).status_code == 200
    partial_status = client.get(f"/api/billing/orders/{created['order_id']}").json()
    assert partial_status["status"] == "paid"
    assert partial_status["fulfilled"] is True
    assert partial_status["refunded_amount_minor"] == 50_000

    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        remainder = _refund_payload(order, "rfnd_remainder_2", 49_900)
    assert _post_event(client, "evt_refund_full", remainder).status_code == 200
    final_status = client.get(f"/api/billing/orders/{created['order_id']}").json()
    assert final_status["status"] == "refunded"
    assert final_status["fulfilled"] is False
    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        assert user.tier == "free"
        assert user.premium_until is None
        assert db.query(PaymentRefund).count() == 2
        assert db.query(EntitlementLedger).one().status == "refunded"


def test_refund_before_capture_is_terminal_and_late_capture_does_not_grant(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        refund = _refund_payload(order, "rfnd_early_full", 99_900)
        capture = _capture_payload(order)
    assert _post_event(client, "evt_early_refund", refund).status_code == 200
    assert _post_event(client, "evt_late_capture", capture).status_code == 200
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        transaction = db.query(PaymentTransaction).one()
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        assert order.status == "refunded"
        assert transaction.status == "refunded"
        assert transaction.refunded_amount_minor == 99_900
        assert user.tier == "free"
        assert db.query(EntitlementLedger).count() == 0


def test_partial_refund_before_capture_still_fulfils_once(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        partial = _refund_payload(order, "rfnd_early_partial", 10_000)
        capture = _capture_payload(order)
    assert _post_event(client, "evt_early_partial", partial).status_code == 200
    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        first_expiry = user.premium_until
        assert user.is_premium_active()
        assert db.query(EntitlementLedger).count() == 1
    assert _post_event(client, "evt_capture_after_partial", capture).status_code == 200
    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        assert order.status == "partially_refunded"
        assert user.premium_until == first_expiry
        assert db.query(EntitlementLedger).count() == 1


def test_delayed_webhook_uses_order_entitlement_snapshot(
    client, enabled_checkout, session_factory, monkeypatch
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        payload = _capture_payload(order)
        assert order.entitlement_kind == "premium_access"
        assert order.entitlement_quantity == 30

    from backend.app.billing import catalog

    monkeypatch.delitem(catalog.PRODUCTS, "premium_30d")
    assert _post_event(client, "evt_after_catalog_removal", payload).status_code == 200
    with session_factory() as db:
        entitlement = db.query(EntitlementLedger).one()
        assert entitlement.quantity == 30


def test_unknown_order_event_is_retryable_and_reconciles_same_event_id(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        payload = _capture_payload(order)
    payment = payload["payload"]["payment"]["entity"]
    payment["order_id"] = "order_arrived_before_local_commit"

    first = _post_event(client, "evt_unknown_then_known", payload)
    assert first.status_code == 503
    with session_factory() as db:
        event = db.query(PaymentEvent).one()
        assert event.processing_status == "retryable_unknown_order"
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        order.provider_order_id = "order_arrived_before_local_commit"
        db.commit()

    retry = _post_event(client, "evt_unknown_then_known", payload)
    assert retry.status_code == 200
    assert retry.json()["status"] == "processed"
    with session_factory() as db:
        assert db.query(PaymentEvent).count() == 1
        assert db.query(PaymentEvent).one().processing_status == "processed"
        assert db.query(EntitlementLedger).count() == 1


def test_same_event_id_with_different_payload_and_second_capture_are_rejected(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        first_payload = _capture_payload(order)
    assert _post_event(client, "evt_stable_payload", first_payload).status_code == 200

    changed = json.loads(json.dumps(first_payload))
    changed["payload"]["payment"]["entity"]["amount"] = 1
    assert _post_event(client, "evt_stable_payload", changed).status_code == 400

    second_payment = json.loads(json.dumps(first_payload))
    second_payment["payload"]["payment"]["entity"]["id"] = "pay_hirewiz_second"
    assert _post_event(client, "evt_second_capture", second_payment).status_code == 400
    with session_factory() as db:
        assert db.query(PaymentTransaction).count() == 1
        assert db.query(EntitlementLedger).count() == 1


def test_account_deletion_unlinks_but_retains_payment_audit(
    client, enabled_checkout, session_factory
):
    created = _accepted_legacy_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        capture = _capture_payload(order)
    assert _post_event(client, "evt_before_delete", capture).status_code == 200

    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        # This historical financial owner has no native password enrollment.
        request = Request({"type": "http", "method": "POST", "scheme": "https", "path": "/api/auth/delete-account", "raw_path": b"/api/auth/delete-account", "query_string": b"", "headers": [], "server": ("owned-test.invalid", 443), "client": ("127.0.0.1", 12345)})
        assert delete_account(request=request, db=db, current_user=user) == {"status": "deleted"}

    with session_factory() as db:
        assert db.query(User).count() == 0
        assert db.query(UserProfile).count() == 0
        order = db.query(PaymentOrder).one()
        entitlement = db.query(EntitlementLedger).one()
        assert order.user_id is None
        assert order.customer_deleted_at is not None
        assert entitlement.user_id is None
        assert entitlement.status == "ended_account_deleted"
        assert db.query(PaymentTransaction).count() == 1
        assert db.query(PaymentEvent).count() == 1


def test_new_bundle_delivers_both_balances_once_from_frozen_terms(client, enabled_checkout, session_factory, monkeypatch):
    from backend.app.models import UsageEvent
    from backend.app.domains.employer.models import ServiceCreditEvent
    created = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        assert order.cost_policy_snapshot["entitlements"] == {"job_service_credits":100,"analysis_units":2}
        capture = _capture_payload(order)
    # Current cost policy/catalog removal cannot rewrite an accepted order.
    monkeypatch.delenv("HIREWIZ_EXPENSE_POLICY_JSON")
    assert _post_event(client,"evt_bundle",capture).status_code == 200
    assert _post_event(client,"evt_bundle_replay",capture).status_code == 200
    with session_factory() as db:
        user = db.query(User).one()
        assert (user.ai_credits,user.job_service_credits,user.tier) == (22,100,"free")
        assert db.query(UsageEvent).filter_by(event_type="grant").count() == 1
        assert db.query(ServiceCreditEvent).filter_by(event_type="grant").count() == 1
        assert db.query(EntitlementLedger).count() == 1


def test_bundle_refund_revokes_both_proportionally_and_keeps_spent_debt(client, enabled_checkout, session_factory):
    created = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        capture = _capture_payload(order)
    assert _post_event(client,"evt_bundle_capture",capture).status_code == 200
    with session_factory() as db:
        user=db.query(User).one();user.job_service_credits=0;user.ai_credits=0
        order=db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        partial=_refund_payload(order,"rfnd_bundle_half",32450)
        rest=_refund_payload(order,"rfnd_bundle_rest",32450)
        db.commit()
    assert _post_event(client,"evt_bundle_half",partial).status_code == 200
    assert _post_event(client,"evt_bundle_half",partial).status_code == 200
    with session_factory() as db:
        user=db.query(User).one();assert (user.job_service_credits,user.ai_credits)==(-50,-1)
    assert _post_event(client,"evt_bundle_rest",rest).status_code == 200
    with session_factory() as db:
        user=db.query(User).one();assert (user.job_service_credits,user.ai_credits)==(-100,-2)


@pytest.mark.parametrize("sku",["premium_30d","job_service_500"])
def test_retired_unlimited_and_old_pack_not_offered_for_new_purchase(client,enabled_checkout,session_factory,sku):
    assert client.post("/api/billing/orders",json={"sku":sku,"billing_country":"IN"}).status_code==503
    assert enabled_checkout==[]
    assert len(client.get("/api/public/billing/catalog").json()["products"])==3


def test_missing_expense_policy_blocks_checkout_before_provider_or_order(client,enabled_checkout,session_factory,monkeypatch):
    monkeypatch.delenv("HIREWIZ_EXPENSE_POLICY_JSON")
    assert client.post("/api/billing/orders",json={"sku":"starter_bundle","billing_country":"IN"}).status_code==503
    assert enabled_checkout==[]
    with session_factory() as db: assert db.query(PaymentOrder).count()==0


@pytest.mark.parametrize("first_event", ["payment.captured", "order.paid"])
@pytest.mark.parametrize("omission", ["absent", "null"])
def test_capture_expense_missing_signed_event_retains_known_overrun(
    client, enabled_checkout, session_factory, first_event, omission
):
    from backend.app.billing.cost_policy import (
        CostPolicyUnavailable,
        ExpensePolicy,
        assert_actual_variance,
    )
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    created = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=created["order_id"]).one()
        first = _capture_payload(order, event=first_event)
        later = _capture_payload(
            order, event="order.paid" if first_event == "payment.captured" else "payment.captured"
        )
    first["payload"]["payment"]["entity"].update(fee=20_000, tax=3_000)
    for field in ("fee", "tax"):
        if omission == "absent":
            later["payload"]["payment"]["entity"].pop(field)
        else:
            later["payload"]["payment"]["entity"][field] = None
    assert _post_event(client, "evt_known_expense", first).status_code == 200
    with session_factory() as db:
        with pytest.raises(CostPolicyUnavailable, match="actual payment"):
            assert_actual_variance(db, ExpensePolicy.model_validate(estimated_expense_policy()))
    assert _post_event(client, "evt_distinct_missing_expense", later).status_code == 200
    replay = _post_event(client, "evt_distinct_missing_expense", later)
    assert replay.status_code == 200 and replay.json()["status"] == "duplicate"
    with session_factory() as db:
        with pytest.raises(CostPolicyUnavailable, match="actual payment"):
            assert_actual_variance(db, ExpensePolicy.model_validate(estimated_expense_policy()))
        order, tx = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one()
        assert order.provider_fee_amount_minor == tx.provider_fee_amount_minor == 20_000
        assert order.provider_fee_tax_minor == tx.provider_fee_tax_minor == 3_000
        assert order.estimated_net_amount_minor == tx.estimated_net_amount_minor == 44_900
        assert order.status == "paid" and tx.status == "captured"
        assert db.query(EntitlementLedger).count() == 1
        user = db.query(User).one()
        assert user.job_service_credits == 100 and user.ai_credits == 22
        events = db.query(PaymentEvent).order_by(PaymentEvent.id).all()
        assert len(events) == 2 and all(e.processing_status == "processed" for e in events)
        assert events[0].payload_sha256 != events[1].payload_sha256


@pytest.mark.parametrize("known_source", ["order_only", "transaction_only"])
@pytest.mark.parametrize("later_fee,later_tax", [(None, None), (2_000, 300), (25_000, 2_000)])
def test_capture_expense_reconciles_order_and_transaction_high_water_facts(
    client, enabled_checkout, session_factory, known_source, later_fee, later_tax
):
    from backend.app.billing.cost_policy import (
        CostPolicyUnavailable,
        ExpensePolicy,
        assert_actual_variance,
    )
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    created = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        first = _capture_payload(order)
    first["payload"]["payment"]["entity"].update(fee=20_000, tax=3_000)
    assert _post_event(client, "evt_known_high_water", first).status_code == 200
    with session_factory() as db:
        order, tx = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one()
        missing = tx if known_source == "order_only" else order
        missing.provider_fee_amount_minor = None
        missing.provider_fee_tax_minor = None
        missing.estimated_net_amount_minor = None
        later = _capture_payload(order, event="order.paid")
        later["payload"]["payment"]["entity"].update(fee=later_fee, tax=later_tax)
        db.commit()
    assert _post_event(client, "evt_reconcile_high_water", later).status_code == 200
    with session_factory() as db:
        with pytest.raises(CostPolicyUnavailable, match="actual payment"):
            assert_actual_variance(db, ExpensePolicy.model_validate(estimated_expense_policy()))
        order, tx = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one()
        expected_fee = max(20_000, later_fee or 0)
        assert order.provider_fee_amount_minor == tx.provider_fee_amount_minor == expected_fee
        assert order.provider_fee_tax_minor == tx.provider_fee_tax_minor == 3_000
        assert order.estimated_net_amount_minor == tx.estimated_net_amount_minor == 64_900 - expected_fee
        marker = db.query(PaymentEvent).filter_by(provider_event_id="evt_reconcile_high_water").one()
        assert marker.processing_status == "processed"
        assert marker.error_code == ("provider_expense_conflict" if later_fee is not None else None)
        assert db.query(EntitlementLedger).count() == 1
        user = db.query(User).one()
        assert user.job_service_credits == 100 and user.ai_credits == 22
    response = client.get(f"/api/billing/orders/{created['order_id']}")
    assert response.status_code == 200 and response.json()["fulfilled"] is True


def test_capture_expense_known_overrun_keeps_callbacks_and_refunds_available(
    client, enabled_checkout, session_factory
):
    from backend.app.billing.cost_policy import (
        CostPolicyUnavailable,
        ExpensePolicy,
        assert_actual_variance,
    )
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    created = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        capture = _capture_payload(order)
    capture["payload"]["payment"]["entity"].update(fee=20_000, tax=3_000)
    assert _post_event(client, "evt_expensive_capture", capture).status_code == 200
    payment_id = capture["payload"]["payment"]["entity"]["id"]
    callback_signature = hmac.new(
        TEST_KEY_SECRET.encode(),
        f"{created['provider_order_id']}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    callback = client.post(
        f"/api/billing/orders/{created['order_id']}/checkout-result",
        json={"razorpay_payment_id": payment_id,
              "razorpay_order_id": created["provider_order_id"],
              "razorpay_signature": callback_signature},
    )
    assert callback.status_code == 200
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        refund = _refund_payload(order, "rfnd_overrun_partial", 32_450)
        late = _capture_payload(order, event="order.paid")
        late["payload"]["payment"]["entity"].pop("fee")
        late["payload"]["payment"]["entity"].pop("tax")
    assert _post_event(client, "evt_expensive_partial_refund", refund).status_code == 200
    assert _post_event(client, "evt_expensive_partial_refund", refund).json()["status"] == "duplicate"
    assert _post_event(client, "evt_late_capture_after_refund", late).status_code == 200
    with session_factory() as db:
        with pytest.raises(CostPolicyUnavailable, match="actual payment"):
            assert_actual_variance(db, ExpensePolicy.model_validate(estimated_expense_policy()))
        order, tx = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one()
        assert order.status == tx.status == "partially_refunded"
        assert order.provider_fee_amount_minor == tx.provider_fee_amount_minor == 20_000
        assert order.provider_fee_tax_minor == tx.provider_fee_tax_minor == 3_000
        assert db.query(PaymentRefund).count() == 1
        assert db.query(EntitlementLedger).count() == 1
        user = db.query(User).one()
        assert user.job_service_credits == 50 and user.ai_credits == 21


@pytest.mark.parametrize("later_expenses", ["missing", "same_known"])
def test_older_distinct_capture_cannot_hide_observed_adverse_fee_from_variance_guard(client, enabled_checkout, session_factory, later_expenses):
    from datetime import datetime, timedelta, timezone

    from backend.app.billing.cost_policy import CostPolicyUnavailable, ExpensePolicy, assert_actual_variance
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    _create_order(client)
    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    with session_factory() as db:
        first = _capture_payload(db.query(PaymentOrder).one())
    first['created_at'] = int(datetime.now(timezone.utc).timestamp())
    first['payload']['payment']['entity'].update(fee=20000, tax=3000)
    assert _post_event(client, 'evt_independent_observed_overrun', first).status_code == 200
    with session_factory() as db:
        with pytest.raises(CostPolicyUnavailable, match='actual payment'):
            assert_actual_variance(db, policy)
        older = _capture_payload(db.query(PaymentOrder).one(), event='order.paid')
    older['created_at'] = int((policy.reconciled_through - timedelta(hours=1)).timestamp())
    older['payload']['payment']['entity'].update(
        fee=None if later_expenses == 'missing' else 20000,
        tax=None if later_expenses == 'missing' else 3000,
    )
    assert _post_event(client, 'evt_independent_older_capture', older).status_code == 200
    with session_factory() as db:
        order, transaction = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one()
        assert order.provider_fee_amount_minor == transaction.provider_fee_amount_minor == 20000
        assert order.provider_fee_tax_minor == transaction.provider_fee_tax_minor == 3000
        with pytest.raises(CostPolicyUnavailable, match='actual payment'):
            assert_actual_variance(db, policy)


@pytest.mark.parametrize('previous_report', ['none', 'failed_projection'])
def test_partial_refund_before_capture_retains_known_expenses_and_pauses_new_work(client, enabled_checkout, session_factory, previous_report, record_property):
    from backend.app.billing.cost_policy import CostPolicyUnavailable, ExpensePolicy, assert_actual_variance
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        refund = _refund_payload(order, 'rfnd_independent_first', 100)
        failure = _capture_payload(order)
    if previous_report == 'failed_projection':
        failure['event'] = 'payment.failed'
        failure['payload']['payment']['entity'].update(status='failed', captured=False, fee=20000, tax=3000)
        assert _post_event(client, 'evt_independent_failed_expense', failure).status_code == 200
        refund['payload']['payment']['entity'].update(fee=None, tax=None)
    else:
        refund['payload']['payment']['entity'].update(fee=20000, tax=3000)
    assert _post_event(client, 'evt_independent_refund_first', refund).status_code == 200
    with session_factory() as db:
        order, transaction, user = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one(), db.query(User).one()
        assert order.status == transaction.status == 'partially_refunded'
        assert order.refunded_amount_minor == 100
        assert db.query(EntitlementLedger).count() == 1
        assert user.job_service_credits == 99 and user.ai_credits == 21
        record_property('expense_projection_observation', json.dumps({
            'order_fee': order.provider_fee_amount_minor,
            'transaction_fee': transaction.provider_fee_amount_minor,
            'order_tax': order.provider_fee_tax_minor,
            'transaction_tax': transaction.provider_fee_tax_minor,
        }))
        with pytest.raises(CostPolicyUnavailable, match='actual payment'):
            assert_actual_variance(db, ExpensePolicy.model_validate(estimated_expense_policy()))
        assert order.provider_fee_amount_minor == transaction.provider_fee_amount_minor == 20000
        assert order.provider_fee_tax_minor == transaction.provider_fee_tax_minor == 3000


def test_newly_processed_old_dated_refund_consumes_current_variance_reserve(client, enabled_checkout, session_factory):
    from datetime import datetime, timedelta, timezone

    from backend.app.billing.cost_policy import CostPolicyUnavailable, ExpensePolicy, assert_actual_variance
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    _create_order(client)
    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    with session_factory() as db:
        capture = _capture_payload(db.query(PaymentOrder).one())
    capture['created_at'] = int(datetime.now(timezone.utc).timestamp())
    capture['payload']['payment']['entity'].update(fee=1500, tax=229)
    assert _post_event(client, 'evt_independent_refund_budget_capture', capture).status_code == 200
    with session_factory() as db:
        assert_actual_variance(db, policy)
        refund = _refund_payload(db.query(PaymentOrder).one(), 'rfnd_independent_old_dated', 10000)
    refund['created_at'] = int((policy.reconciled_through - timedelta(days=1)).timestamp())
    refund['payload']['payment']['entity'].update(fee=1500, tax=229)
    assert _post_event(client, 'evt_independent_old_dated_refund', refund).status_code == 200
    with session_factory() as db:
        assert db.query(PaymentOrder).one().refunded_amount_minor == 10000
        with pytest.raises(CostPolicyUnavailable, match='refund expenses'):
            assert_actual_variance(db, policy)



@pytest.mark.parametrize("fee,tax", [(True, 0), (65_000, 0), (1_000, 1_001)])
def test_invalid_refund_expenses_cannot_mutate_balances(
    client, enabled_checkout, session_factory, fee, tax
):
    _create_order(client)
    with session_factory() as db:
        refund = _refund_payload(db.query(PaymentOrder).one(), "rfnd_invalid_expense", 100)
    refund["payload"]["payment"]["entity"].update(fee=fee, tax=tax)
    assert _post_event(client, "evt_invalid_refund_expense", refund).status_code == 400
    with session_factory() as db:
        assert db.query(PaymentRefund).count() == 0
        assert db.query(PaymentTransaction).count() == 0
        assert db.query(EntitlementLedger).count() == 0
        assert db.query(PaymentEvent).one().processing_status == "rejected"
        user = db.query(User).one()
        assert user.job_service_credits == 0 and user.ai_credits == 20


def test_full_refund_before_capture_keeps_expenses_with_unknown_paid_date(
    client, enabled_checkout, session_factory
):
    from backend.app.billing.cost_policy import (
        CostPolicyUnavailable, ExpensePolicy, assert_actual_variance,
    )
    from backend.tests.expense_policy_fixtures import estimated_expense_policy

    _create_order(client)
    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    with session_factory() as db:
        refund = _refund_payload(db.query(PaymentOrder).one(), "rfnd_full_expense_first", 64_900)
    refund["created_at"] = int((policy.reconciled_through - timedelta(days=1)).timestamp())
    refund["payload"]["payment"]["entity"].update(fee=20_000, tax=3_000)
    assert _post_event(client, "evt_full_expense_first", refund).status_code == 200
    with session_factory() as db:
        order, transaction = db.query(PaymentOrder).one(), db.query(PaymentTransaction).one()
        assert order.status == transaction.status == "refunded"
        assert order.paid_at is None
        assert order.provider_fee_amount_minor == transaction.provider_fee_amount_minor == 20_000
        assert order.provider_fee_tax_minor == transaction.provider_fee_tax_minor == 3_000
        assert order.refunded_amount_minor == 64_900
        assert db.query(EntitlementLedger).count() == 0
        user = db.query(User).one()
        assert user.job_service_credits == 0 and user.ai_credits == 20
        with pytest.raises(CostPolicyUnavailable, match="actual payment"):
            assert_actual_variance(db, policy)
