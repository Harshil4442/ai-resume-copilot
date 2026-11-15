"""Owned PostgreSQL races through checkout and signed settlement HTTP."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from backend.app.domains.candidate_accounts.account_erasure import erase_account_sql
from backend.app.domains.credits import service
from backend.app.domains.credits.models import CreditGrant, CreditPurchaseQuote
from backend.app.domains.employer.admissions import lock_application_set
from backend.app.models import EntitlementLedger, PaymentEvent, PaymentOrder, User
from sqlalchemy import event
from sqlalchemy.exc import OperationalError
from test_razorpay_billing import _capture_payload, _post_event, _refund_payload
from test_settled_refund_recovery_postgres import client as client
from test_settled_refund_recovery_postgres import enabled_checkout as enabled_checkout
from test_settled_refund_recovery_postgres import pg_engine as pg_engine
from test_settled_refund_recovery_postgres import session_factory as session_factory
from test_settled_refund_recovery_postgres import unified as unified
from test_unified_credit_payments import start


def _contend_for_owner(factory, request, work):
    """Wait for the HTTP contender's actual advisory SQL, not a sleep."""
    entered = Event()
    engine = factory.kw["bind"]
    with factory() as db:
        lock_application_set(db, 1)
        holder = db.connection()

        def observe(conn, _cursor, statement, *_args):
            if conn is not holder and "pg_advisory_xact_lock" in statement:
                entered.set()

        event.listen(engine, "before_cursor_execute", observe)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                response = pool.submit(request)
                failure = None
                try:
                    assert entered.wait(5), "HTTP request did not reach the shared owner gate"
                    work(db)
                    db.commit()
                except OperationalError as exc:
                    failure = exc.orig.sqlstate
                finally:
                    db.rollback()
                return response.result(timeout=10), failure
        finally:
            event.remove(engine, "before_cursor_execute", observe)


def test_unified_checkout_and_quote_share_owner_first_order(unified, session_factory):
    client, calls = unified
    quote = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "checkout-lock-quote",
    })
    assert quote.status_code == 200
    payload = {"quote_id": quote.json()["quote_id"], "billing_country": "IN",
               "idempotency_key": "checkout-lock-quote"}
    response, failure = _contend_for_owner(session_factory,
        lambda: client.post("/api/v1/credits/orders", json=payload),
        lambda db: service.issue_purchase_quote(db, user_id=1, sku="starter_pass",
            idempotency_key="competing-lock-quote", checkout_enabled=True))
    assert (response.status_code, failure) == (200, None), response.text
    assert len(calls) == 1
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 1
        assert db.query(CreditPurchaseQuote).count() == 2


@pytest.mark.parametrize("settlement", ["capture", "refund"])
def test_signed_settlement_waits_for_erasure_without_restoring_owner(unified, session_factory, settlement):
    client, calls = unified
    _, _, checkout = start(client)
    assert checkout.status_code == 200
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        payload = capture = _capture_payload(order, event="order.paid")
        if settlement == "refund":
            payload = _refund_payload(order, "rfnd_order_lock_erasure", order.gross_amount_minor)
            # Exercise the provider's refund-only event and transaction hint.
            del payload["payload"]["payment"]
    if settlement == "refund":
        assert _post_event(client, "evt_before_owner_erasure", capture).json()["status"] == "processed"
    response, failure = _contend_for_owner(session_factory,
        lambda: _post_event(client, "evt_owner_lock_erasure", payload), lambda db: erase_account_sql(db, 1))
    assert (response.status_code, failure) == (200, None), response.text
    assert response.json()["status"] == "processed"
    assert _post_event(client, "evt_owner_lock_erasure", payload).json()["status"] == "duplicate"
    assert len(calls) == 1
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        assert db.get(User, 1) is None and order.user_id is None
        if settlement == "refund":
            assert order.status == "refunded"
            assert db.query(CreditGrant).one().user_id is None
            grant = db.query(EntitlementLedger).one()
            assert (grant.user_id, grant.status) == (None, "refunded")
            assert db.query(PaymentEvent).count() == 2
        else:
            assert order.status == "paid_unfulfilled"
            assert db.query(CreditGrant).count() == db.query(EntitlementLedger).count() == 0
            assert db.query(PaymentEvent).count() == 1
