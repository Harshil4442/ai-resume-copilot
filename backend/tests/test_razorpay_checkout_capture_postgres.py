"""Own PG17 checkout callback versus signed webhook; no provider I/O."""

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from backend.app.billing.catalog import CATALOG_VERSION, PRODUCTS
from backend.app.billing.purchase_quote import product_quote
from backend.app.billing.razorpay import RazorpayAdapter, RazorpayProviderError
from backend.app.models import EntitlementLedger, PaymentOrder, PaymentTransaction, User
from backend.app.routers import billing
from backend.tests.test_payment_funding_postgres import owned_pg_url, pg_factory
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from test_razorpay_billing import (
    TEST_KEY_SECRET,
    _capture_payload,
    _create_order,
    _post_event,
    _purchase_payload,
    _service_order,
)
from test_razorpay_billing import client as client
from test_razorpay_billing import enabled_checkout as enabled_checkout

assert owned_pg_url and pg_factory


@pytest.fixture
def session_factory(pg_factory):
    with pg_factory() as db:
        db.add(User(email="buyer@example.com", password_hash="synthetic", ai_credits=20))
        db.commit()
    return pg_factory


def test_legacy_cleanup_keeps_owner_lock_until_replacement_is_durable(
    client, enabled_checkout, session_factory, monkeypatch,
):
    original = _create_order(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        order.provider_order_id = None
        db.commit()

    admission_entered, resume = Event(), Event()
    original_variance = billing.assert_actual_variance

    def pause_admission(db, policy):
        admission_entered.set()
        assert resume.wait(5), "The ownership check must finish before checkout resumes"
        return original_variance(db, policy)

    monkeypatch.setattr(billing, "assert_actual_variance", pause_admission)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/api/billing/orders", json=_purchase_payload())
        try:
            assert admission_entered.wait(5), "Replacement did not reach the funding check"
            with session_factory() as db:
                # FOR UPDATE NOWAIT on the actual owner must fail while the
                # real HTTP request is still reviewing the replacement.
                with pytest.raises(OperationalError) as locked:
                    db.execute(text("SELECT id FROM users FOR UPDATE NOWAIT"))
                assert locked.value.orig.sqlstate == "55P03"
        finally:
            resume.set()
        response = pending.result(timeout=5)
    assert response.status_code == 200

    growth = _purchase_payload() | {"sku": "growth_bundle", "accepted_quote":
        product_quote(PRODUCTS["growth_bundle"], CATALOG_VERSION).model_dump()}
    blocked = client.post("/api/billing/orders", json=growth)
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["order_id"] == response.json()["order_id"]
    assert enabled_checkout == [original["order_id"], response.json()["order_id"]]


def test_checkout_callback_does_not_downgrade_capture_committed_after_original_read(
    client, enabled_checkout, session_factory, monkeypatch,
):
    order_id = _service_order(client)["order_id"]
    with session_factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=order_id).one()
        capture = _capture_payload(order)
        provider_id = order.provider_order_id
    payment_id = capture["payload"]["payment"]["entity"]["id"]
    signature = hmac.new(TEST_KEY_SECRET.encode(), f"{provider_id}|{payment_id}".encode(), hashlib.sha256).hexdigest()
    callback = {"razorpay_order_id": provider_id, "razorpay_payment_id": payment_id, "razorpay_signature": signature}
    verified, captured = Event(), Event()
    actual_verify = RazorpayAdapter.verify_checkout_signature

    def wait_after_verified(self, **terms):
        result = actual_verify(self, **terms)
        if not verified.is_set():
            verified.set()
            assert captured.wait(4), "Capture must commit before the original callback resumes"
        return result

    monkeypatch.setattr(RazorpayAdapter, "verify_checkout_signature", wait_after_verified)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(client.post, f"/api/billing/orders/{order_id}/checkout-result", json=callback)
        try:
            assert verified.wait(4), "Callback did not read the original order"
            response = _post_event(client, "evt_checkout_capture_race", capture)
            assert response.status_code == 200 and response.json()["status"] == "processed"
        finally:
            captured.set()
        assert future.result(timeout=5).json() == {"accepted": True, "status": "pending", "fulfilled": False}

    assert client.post(f"/api/billing/orders/{order_id}/checkout-result", json=callback).status_code == 200
    with session_factory() as db:
        assert db.query(PaymentOrder).one().status == "paid"
        assert db.query(PaymentTransaction).one().status == "captured"
        assert db.query(EntitlementLedger).count() == 1
        assert db.query(User).one().job_service_credits == 500


@pytest.mark.parametrize("response_lost", [False, True])
def test_legacy_creation_reply_keeps_capture_settled(
    client, enabled_checkout, session_factory, monkeypatch, response_lost,
):
    provider_id = "order_legacycreationrace"
    provider_calls = []

    def finish_create(self, *, product, local_order_id, receipt):
        provider_calls.append(local_order_id)
        with session_factory() as db:
            order = db.query(PaymentOrder).filter_by(public_id=local_order_id).one()
            assert order.status == "initializing"
            # The reconciliation route can bind the provider order before
            # the original create call finishes. Then the signed event settles it.
            order.provider_order_id = provider_id
            db.commit()
            capture = _capture_payload(order)
        event = _post_event(client, "evt_legacy_creation_race", capture)
        assert event.status_code == 200 and event.json()["status"] == "processed"
        if response_lost:
            raise RazorpayProviderError("synthetic response loss after settlement")
        return {"provider_order_id": provider_id}

    monkeypatch.setattr(RazorpayAdapter, "create_order", finish_create)
    purchase = _purchase_payload()
    result = client.post("/api/billing/orders", json=purchase)
    assert result.status_code == 409
    detail = result.json()["detail"]
    assert detail["code"] == "billing_order_requires_status"
    order_id = detail["order_id"]
    assert client.post("/api/billing/orders", json=purchase).status_code == 409
    readback = client.get(f"/api/billing/orders/{order_id}").json()
    assert readback["status"] == "paid" and readback["fulfilled"]
    assert not readback["checkout_available"]
    assert provider_calls == [order_id]
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        assert order.provider_order_id == provider_id
        assert order.active_attempt_key is None
        assert db.query(PaymentTransaction).one().status == "captured"
        assert db.query(EntitlementLedger).count() == 1
