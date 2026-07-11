import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.app.billing.razorpay import RazorpayAdapter
from backend.app.billing.catalog import CATALOG_VERSION
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
        assert product.amount_minor == 99_900
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
    response = client.post(
        "/api/billing/orders",
        json={"sku": "premium_30d", "billing_country": "IN"},
    )
    assert response.status_code == 200, response.text
    return response.json()


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
    assert catalog["products"] == [
        {
            "sku": "premium_30d",
            "name": "HireWiz Premium — 30 days",
            "description": "One-time purchase of 30 days of HireWiz Premium access.",
            "amount_minor": 99_900,
            "amount_display": "₹999",
            "currency": "INR",
            "billing_type": "one_time",
            "duration_days": 30,
            "auto_renews": False,
            "catalog_visible": True,
            "enabled_for_purchase": False,
        }
    ]

    config_response = client.get("/api/billing/config")
    assert config_response.headers["cache-control"] == "no-store, private"
    create_response = client.post(
        "/api/billing/orders",
        json={"sku": "premium_30d", "billing_country": "IN"},
    )
    assert create_response.status_code == 503
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 0


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
        json={"sku": "premium_30d", "billing_country": "US"},
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
                sku="premium_30d",
                catalog_version=CATALOG_VERSION,
                billing_type="one_time",
                entitlement_kind="premium_access",
                entitlement_quantity=30,
                billing_country="IN",
                billing_country_confirmed_at=datetime.now(timezone.utc),
                gross_amount_minor=99_900,
                refunded_amount_minor=0,
                currency="INR",
                status="initializing",
                active_attempt_key=f"{user.id}:premium_30d:test",
            )
        )
        db.commit()

    response = client.post(
        "/api/billing/orders",
        json={"sku": "premium_30d", "billing_country": "IN"},
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
        json={"sku": "premium_30d", "billing_country": "IN", "amount_minor": 1},
    )
    assert manipulated.status_code == 422

    first = _create_order(client)
    assert first["amount_minor"] == 99_900
    assert first["currency"] == "INR"
    assert "notes" not in first
    assert first["provider_order_id"] == "order_fake_1"
    assert client.post(
        "/api/billing/orders",
        json={"sku": "premium_30d", "billing_country": "IN"},
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
        json={"sku": "premium_30d", "billing_country": "IN"},
    )
    assert rotated.status_code == 409
    assert len(enabled_checkout) == 1

    # A test-mode order is never returned alongside a live key.
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    retry_order = _create_order(client)
    assert retry_order["order_id"] == created["order_id"]
    assert len(enabled_checkout) == 1


def test_partial_and_full_refund_revoke_only_on_full_refund(
    client, enabled_checkout, session_factory
):
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
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
    created = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).filter(PaymentOrder.public_id == created["order_id"]).one()
        capture = _capture_payload(order)
    assert _post_event(client, "evt_before_delete", capture).status_code == 200

    with session_factory() as db:
        user = db.query(User).filter(User.email == "buyer@example.com").one()
        assert delete_account(db=db, current_user=user) == {"status": "deleted"}

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
