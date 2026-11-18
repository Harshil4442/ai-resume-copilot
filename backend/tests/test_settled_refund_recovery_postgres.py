"""An owned payment recovers missed refund webhooks using bounded provider GETs."""

from contextlib import contextmanager

import httpx
import pytest
from backend.app.billing import reconciliation
from backend.app.domains.credits.models import CreditGrant
from backend.app.models import (
    EntitlementLedger,
    PaymentEvent,
    PaymentOrder,
    PaymentRefund,
    User,
    UserProfile,
)
from backend.tests.test_employer_admissions_postgres import _migrate, pg_engine
from sqlalchemy.orm import sessionmaker
from test_razorpay_billing import (
    TEST_KEY_ID,
    TEST_KEY_SECRET,
    _capture_payload,
    _post_event,
    _refund_payload,
    client,
    enabled_checkout,
)
from test_unified_credit_payments import start, unified

assert client and enabled_checkout and pg_engine and unified

PAYMENT_ID = "pay_ownedrefund1"


@pytest.fixture
def session_factory(pg_engine):
    _migrate(pg_engine, "head")
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    with factory() as db:
        buyer = User(email="buyer@example.com", password_hash="synthetic", ai_credits=20, job_service_credits=13)
        db.add(buyer)
        db.flush()
        db.add(UserProfile(user_id=buyer.id))
        db.commit()
    return factory


def _captured_order(client, factory):
    _, _, created = start(client)
    assert created.status_code == 200, created.text
    local_id = created.json()["order_id"]
    with factory() as db:
        order = db.query(PaymentOrder).filter_by(public_id=local_id).one()
        capture = _capture_payload(order, event="order.paid")
        capture["payload"]["payment"]["entity"]["id"] = PAYMENT_ID
        half, remainder = (_refund_payload(order, refund_id, 2950) for refund_id in ("rfnd_first", "rfnd_rest"))
        for webhook in (half, remainder):
            webhook["payload"]["payment"]["entity"]["id"] = PAYMENT_ID
            webhook["payload"]["refund"]["entity"]["payment_id"] = PAYMENT_ID
    assert _post_event(client, "evt_owned_capture", capture).status_code == 200
    return local_id, capture["payload"], half, remainder


def _provider(monkeypatch, capture):
    calls = []
    payment = capture["payment"]["entity"] | {"amount_refunded": 0}
    remote = capture["order"]["entity"] | {"attempts": 1}
    refunds = []

    def collection(*items):
        return {"entity": "collection", "count": len(items), "items": list(items)}

    @contextmanager
    def stream(method, url, *, auth, params, timeout):
        assert method == "GET" and auth == (TEST_KEY_ID, TEST_KEY_SECRET) and 0 < timeout <= 5
        prefix = "https://api.razorpay.com/v1/"
        assert url.startswith(prefix)
        path = url.removeprefix(prefix)
        calls.append(path)
        if path == "orders":
            assert params == {"receipt": remote["receipt"], "count": 100, "skip": 0}
            payload = collection(remote)
        elif path == f"orders/{remote['id']}":
            assert not params
            payload = remote
        elif path == f"orders/{remote['id']}/payments":
            assert not params
            payload = collection(payment)
        elif path == f"payments/{PAYMENT_ID}":
            assert not params
            payload = payment
        elif path == f"payments/{PAYMENT_ID}/refunds":
            assert params == {"count": 100, "skip": 0}
            payload = collection(*refunds)
        else:
            pytest.fail(f"Unexpected provider read: {path}")
        yield httpx.Response(200, json=payload, request=httpx.Request(method, url))

    monkeypatch.setattr(reconciliation.httpx, "stream", stream)
    return calls, payment, refunds


def _reconcile(client, local_id, *, resolution="settled"):
    reply = client.post(f"/api/billing/orders/{local_id}/reconcile")
    assert reply.status_code == 200, reply.text
    assert reply.json()["resolution"] == resolution
    return reply.json()["order"]


def _financial_snapshot(factory):
    with factory() as db:
        order = db.query(PaymentOrder).one()
        grant = db.query(CreditGrant).one()
        user = db.get(User, order.user_id)
        return {
            "order": (order.status, order.refunded_amount_minor, order.paid_at, order.updated_at),
            "grant": (grant.id, grant.quantity, grant.remaining_credits, grant.expires_at),
            "legacy": (user.ai_credits, user.job_service_credits),
            "entitlements": db.query(EntitlementLedger).count(),
            "refunds": [(row.provider_refund_id, row.amount_minor) for row in db.query(PaymentRefund).order_by(PaymentRefund.id)],
            "api_events": db.query(PaymentEvent).filter_by(provider="razorpay_api").count(),
        }


def test_a_missed_refund_webhook_adjusts_the_existing_lot_once(unified, session_factory, monkeypatch):
    client, purchases = unified
    local_id, capture, half, _ = _captured_order(client, session_factory)
    calls, payment, refunds = _provider(monkeypatch, capture)
    before = _financial_snapshot(session_factory)
    assert before["order"][:2] == ("paid", 0)
    assert before["grant"][1:3] == (40, 40)
    assert before["legacy"] == (20, 13)

    refund = half["payload"]["refund"]["entity"]
    payment["amount_refunded"] = 2950
    refunds.append(refund | {"status": "pending"})
    assert _reconcile(client, local_id, resolution="unresolved")["status"] == "paid"
    assert calls[-1] == f"payments/{PAYMENT_ID}/refunds"
    assert _financial_snapshot(session_factory) == before

    refunds[:] = [refund]
    assert _reconcile(client, local_id)["refunded_amount_minor"] == 2950
    recovered = _financial_snapshot(session_factory)
    assert recovered["order"][:3] == ("partially_refunded", 2950, before["order"][2])
    assert recovered["grant"] == (before["grant"][0], 40, 20, before["grant"][3])
    assert recovered["refunds"] == [("rfnd_first", 2950)]
    assert (recovered["api_events"], recovered["entitlements"], recovered["legacy"]) == (1, 1, (20, 13))
    assert len(purchases) == 1
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 20

    assert _reconcile(client, local_id)["refunded_amount_minor"] == 2950
    assert _financial_snapshot(session_factory) == recovered
    assert _post_event(client, "evt_late_refund", half).status_code == 200
    assert _post_event(client, "evt_late_refund", half).json()["status"] == "duplicate"
    assert _financial_snapshot(session_factory) == recovered


def test_remainder_requires_proof_of_recorded_refund_and_never_reopens_full_refund(unified, session_factory, monkeypatch):
    client, _ = unified
    local_id, capture, half, remainder = _captured_order(client, session_factory)
    assert _post_event(client, "evt_first_refund", half).status_code == 200
    calls, payment, refunds = _provider(monkeypatch, capture)
    payment.update(status="refunded", amount_refunded=5900)
    first = half["payload"]["refund"]["entity"]
    last = remainder["payload"]["refund"]["entity"]
    refunds[:] = [first | {"id": "rfnd_conflicting"}, last]
    before = _financial_snapshot(session_factory)
    assert _reconcile(client, local_id, resolution="unresolved")["refunded_amount_minor"] == 2950
    assert _financial_snapshot(session_factory) == before

    refunds[:] = [first, last]
    assert _reconcile(client, local_id)["status"] == "refunded"
    recovered = _financial_snapshot(session_factory)
    assert recovered["order"][:2] == ("refunded", 5900)
    assert recovered["grant"] == (before["grant"][0], 40, 0, before["grant"][3])
    assert recovered["refunds"] == [("rfnd_first", 2950), ("rfnd_rest", 2950)]
    assert recovered["api_events"] == 1
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 0
    before_repeat = list(calls)
    assert _reconcile(client, local_id)["status"] == "refunded"
    assert calls == before_repeat
    assert _financial_snapshot(session_factory) == recovered


def test_transport_failure_does_not_confirm_an_unchanged_refund(unified, session_factory, monkeypatch):
    client, purchases = unified
    local_id, capture, _, _ = _captured_order(client, session_factory)
    calls, _, _ = _provider(monkeypatch, capture)
    before = _financial_snapshot(session_factory)

    def unavailable(method, url, **kwargs):
        assert method == "GET"
        raise httpx.ReadTimeout("synthetic provider timeout", request=httpx.Request(method, url))

    with monkeypatch.context() as outage:
        outage.setattr(reconciliation.httpx, "stream", unavailable)
        reply = _reconcile(client, local_id, resolution="unresolved")
        assert reply["status"] == "paid" and reply["fulfilled"]
        assert reply["refunded_amount_minor"] == 0
    assert _financial_snapshot(session_factory) == before

    assert _reconcile(client, local_id)["refunded_amount_minor"] == 0
    assert calls[-1] == f"payments/{PAYMENT_ID}"
    assert _financial_snapshot(session_factory) == before
    assert len(purchases) == 1
