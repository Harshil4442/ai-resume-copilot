"""Own PG17 checkout callback versus signed webhook; no provider I/O."""

import hashlib
import hmac
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from backend.app.models import EntitlementLedger, PaymentOrder, PaymentTransaction, User
from backend.tests.test_payment_funding_postgres import owned_pg_url, pg_factory
from test_razorpay_billing import (
    TEST_KEY_SECRET,
    _capture_payload,
    _post_event,
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


def test_checkout_callback_does_not_downgrade_capture_committed_after_original_read(
    client, enabled_checkout, session_factory, monkeypatch,
):
    from backend.app.billing.razorpay import RazorpayAdapter

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
