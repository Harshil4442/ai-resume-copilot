"""Admission wiring uses real checkout, settlement and credit state machines."""
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.billing.razorpay import RazorpayAdapter, RazorpayProviderError
from backend.app.domains.credits import admission, finance, service
from backend.app.domains.credits.models import (
    CreditExecutionHold,
    CreditGrant,
    CreditPurchaseQuote,
    CreditReservation,
    CreditServiceQuote,
)
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.models import EntitlementLedger, ModelCostLiability, PaymentOrder, PaymentRefund
from backend.app.routers import credits
from test_razorpay_billing import _capture_payload, _post_event, _refund_payload
from test_unified_credit_payments import (
    client as client,
)
from test_unified_credit_payments import (
    enabled_checkout as enabled_checkout,
)
from test_unified_credit_payments import (
    session_factory as session_factory,
)
from test_unified_credit_payments import (
    start,
)
from test_unified_credit_payments import (
    unified as unified,
)
from test_unified_credits import NOW, grant
from test_unified_execution import admitted
from test_unified_execution import factory as factory


def _deny(*args, **kwargs):
    raise finance.FundingUnavailable("Synthetic unresolved payment expense")


def test_catalog_and_new_quote_close_on_unfunded_expense(unified, monkeypatch, session_factory):
    client, calls = unified
    observed = []

    def deny(db, *, policy, now):
        assert isinstance(policy, finance.FinancialPolicy)
        assert isinstance(now, datetime) and now.tzinfo is not None
        observed.append(db)
        _deny()

    monkeypatch.setattr(credits, "assert_new_exposure_funded", deny, raising=False)
    catalog = client.get("/api/v1/credits/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["checkout_enabled"] is False
    quoted = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "unfunded-purchase-quote"})
    assert quoted.status_code == 503 and observed and calls == []
    with session_factory() as db:
        assert db.query(CreditPurchaseQuote).count() == 0


def test_paused_purchase_quote_denies_before_offers_or_funding_work(unified, monkeypatch, session_factory):
    client, calls = unified
    monkeypatch.setenv("UNIFIED_CREDITS_ENABLED", "false")

    def forbidden(*args, **kwargs):
        raise AssertionError("An early purchase kill switch must not query offers or payment funding")

    monkeypatch.setattr(credits, "_service_offers", forbidden)
    monkeypatch.setattr(credits, "assert_new_exposure_funded", forbidden)
    response = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "paused-before-audience-work"})
    assert response.status_code == 503 and calls == []
    with session_factory() as db:
        assert db.query(CreditPurchaseQuote).count() == 0


def test_new_order_pauses_before_quote_acceptance_or_provider_io(unified, monkeypatch, session_factory):
    client, calls = unified
    quoted = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "quote-before-expense-change"})
    assert quoted.status_code == 200, quoted.text
    monkeypatch.setattr(credits, "assert_new_exposure_funded", _deny, raising=False)
    result = client.post("/api/v1/credits/orders", json={
        "quote_id": quoted.json()["quote_id"], "billing_country": "IN",
        "idempotency_key": "quote-before-expense-change"})
    assert result.status_code == 503 and calls == []
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 0
        assert db.query(CreditPurchaseQuote).one().accepted_order_id is None


def test_exact_original_order_recovery_does_not_create_new_exposure(unified, monkeypatch):
    client, calls = unified
    _, payload, created = start(client)
    assert created.status_code == 200, created.text
    original = created.json()
    monkeypatch.setattr(credits, "assert_new_exposure_funded", _deny, raising=False)
    recovered = client.post("/api/v1/credits/orders", json=payload)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["order_id"] == original["order_id"]
    assert len(calls) == 1
    status = client.get(f"/api/v1/credits/orders/{original['order_id']}")
    assert status.status_code == 200
    assert {key: value for key, value in status.json()["accepted_quote"].items() if key != "server_time"} == {
        key: value for key, value in original["accepted_quote"].items() if key != "server_time"}
    assert status.json()["checkout_available"] is True
    drifted = client.post("/api/v1/credits/orders", json=payload | {"quote_id": "different-original-quote"})
    assert drifted.status_code == 409 and len(calls) == 1


def test_uncertain_original_intent_is_recovered_without_resending_or_new_funding_check(
    unified, monkeypatch,
):
    client, _ = unified
    attempts = []

    def unknown(*args, **kwargs):
        attempts.append(1)
        raise RazorpayProviderError("Synthetic unknown original response")

    monkeypatch.setattr(RazorpayAdapter, "create_order", unknown)
    _, payload, first = start(client)
    assert first.status_code == 409 and attempts == [1]
    monkeypatch.setattr(credits, "assert_new_exposure_funded", _deny, raising=False)
    repeated = client.post("/api/v1/credits/orders", json=payload)
    assert repeated.status_code == 409 and attempts == [1]


def test_readiness_finishes_qualification_before_funding_lock(unified, monkeypatch):
    client, calls = unified
    entered = False
    original_offers = credits._service_offers

    def offers(*args, **kwargs):
        assert not entered, "Audience/route qualification ran after the money guard"
        return original_offers(*args, **kwargs)

    def funded(*args, **kwargs):
        nonlocal entered
        entered = True

    monkeypatch.setattr(credits, "_service_offers", offers)
    monkeypatch.setattr(credits, "assert_new_exposure_funded", funded, raising=False)
    assert client.get("/api/v1/credits/catalog").json()["checkout_enabled"] is True
    entered = False
    quote = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "qualification-before-money-lock"})
    assert quote.status_code == 200, quote.text
    assert entered and calls == []


def test_capture_refund_and_duplicates_settle_while_new_admission_paused(
    unified, monkeypatch, session_factory,
):
    client, calls = unified
    _, _, created = start(client)
    assert created.status_code == 200
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        capture = _capture_payload(order)
        refund = _refund_payload(order, "rfnd_admission_pause", order.gross_amount_minor)
    monkeypatch.setattr(credits, "assert_new_exposure_funded", _deny, raising=False)
    monkeypatch.setattr(admission, "assert_new_exposure_funded", _deny, raising=False)
    assert _post_event(client, "evt_admission_paused_capture", capture).status_code == 200
    assert _post_event(client, "evt_admission_paused_capture", capture).status_code == 200
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 40
    assert _post_event(client, "evt_admission_paused_refund", refund).status_code == 200
    assert _post_event(client, "evt_admission_paused_refund", refund).status_code == 200
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 0
    with session_factory() as db:
        assert db.query(CreditGrant).count() == 1
        assert db.query(PaymentRefund).count() == 1
        assert db.query(EntitlementLedger).one().status == "refunded"
    assert len(calls) == 1


def test_new_paid_hold_refuses_before_credit_or_dispatch_reservation(factory, monkeypatch):
    with factory() as db:
        grant(db)
        db.commit()
        monkeypatch.setattr(admission, "assert_new_exposure_funded", _deny, raising=False)
        with pytest.raises(finance.FundingUnavailable, match="unresolved payment expense"):
            admitted(db)
        assert db.query(CreditExecutionHold).count() == 0
        assert db.query(CreditReservation).count() == 0
        assert db.query(ModelCostLiability).count() == 0
        assert db.query(DispatchOutbox).count() == 0
        assert db.query(CreditServiceQuote).one().accepted_reservation_id is None
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 40


def test_exact_accepted_hold_replay_survives_new_admission_pause(factory, monkeypatch):
    with factory() as db:
        grant(db)
        monkeypatch.setattr(admission, "assert_new_exposure_funded", lambda *args, **kwargs: None, raising=False)
        hold = admitted(db)
        db.commit()
        monkeypatch.setattr(admission, "assert_new_exposure_funded", _deny, raising=False)
        assert admitted(db).id == hold.id
        with pytest.raises(finance.FundingUnavailable, match="unresolved payment expense"):
            admitted(db, source="next-operation")
        assert db.query(CreditExecutionHold).count() == 1
        assert db.query(CreditReservation).count() == 1
        assert db.query(DispatchOutbox).count() == 1


def test_canary_qualification_finishes_before_payment_money_guard(factory, monkeypatch):
    from backend.app.domains.credits import canary
    from test_canary_admission import baseline, consent, prepare, source

    with factory() as db:
        grant(db)
        config, review, digest = prepare(db, monkeypatch)
        consent(db, config, review, digest, expected_sequence=0)
        baseline(db)
        source(db, identity="run-one", fingerprint="a" * 64)
        db.commit()
        observed = []
        original_accept = canary.accept

        def accepted(*args, **kwargs):
            assert observed == []
            result = original_accept(*args, **kwargs)
            observed.append("qualified")
            return result

        def funded(*args, **kwargs):
            assert observed == ["qualified"]
            observed.append("funding")

        monkeypatch.setattr(canary, "accept", accepted)
        monkeypatch.setattr(admission, "assert_new_exposure_funded", funded, raising=False)
        assert admitted(db).snapshot["canary"]["source_id"] == "run-one"
        assert observed == ["qualified", "funding"]


def test_real_receipt_funded_loss_reopens_new_checkout_after_capture_and_refund(
    unified, session_factory,
):
    """All evidence/funding declarations here belong only to this synthetic DB."""
    from backend.app.domains.credits import payment_expenses, payment_funding, release

    client, calls = unified
    _, _, created = start(client)
    assert created.status_code == 200
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        capture = _capture_payload(order)
        refund = _refund_payload(order, "rfnd_real_loss_protocol", order.gross_amount_minor)
    assert _post_event(client, "evt_real_loss_capture", capture).status_code == 200
    assert _post_event(client, "evt_real_loss_refund", refund).status_code == 200
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 0
    with session_factory() as db:
        # HTTP transport above stays in Razorpay test mode. This owned retained
        # fixture selects the live-cash accounting branch; it is not a live
        # payment receipt, provider account, funding claim or deployment proof.
        db.query(PaymentOrder).one().provider_mode = "live"
        db.commit()
    denied = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "unfunded-real-refund"})
    assert denied.status_code == 503 and len(calls) == 1
    now = datetime.now(UTC)
    policy = release.current_review().policy
    with session_factory() as db:
        snapshot = payment_expenses.collect_expenses(db, policy=policy)
        assert not snapshot.unresolved
        required = sum(row.required_minor for row in snapshot.components)
        assert required > 0
        declaration = payment_funding.FundingDeclaration(
            external_reference_sha256="1" * 64, amount_minor=required,
            funded_at=now - timedelta(minutes=1),
            availability_basis="available_after_existing_obligations",
            evidence_sha256="2" * 64, reviewer_reference_sha256="3" * 64,
            reviewed_at=now, review_valid_until=now + timedelta(hours=1),
        )
        payment_funding.register_declaration(db, declaration, now=now)
        for row in snapshot.components:
            if not row.required_minor and not row.review_required:
                continue
            request = payment_funding.AllocationRequest(
                component_key=row.key, expected_facts_sha256=row.facts_sha256,
                funding_reference_sha256=declaration.external_reference_sha256 if row.required_minor else None,
                amount_minor=row.required_minor, expense_evidence_sha256="4" * 64,
                reviewer_reference_sha256="3" * 64, reviewed_at=now,
                review_valid_until=now + timedelta(hours=1),
                basis="reviewed_conservative_envelope" if row.basis == "conservative" else "reviewed_invoice_declaration",
            )
            payment_funding.allocate_loss(db, request, policy=policy, now=now)
        payment_expenses.assert_new_exposure_funded(db, policy=policy, now=now)
        db.commit()
    quote = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "receipt-funded-next-purchase"})
    assert quote.status_code == 200, quote.text
    result = client.post("/api/v1/credits/orders", json={
        "quote_id": quote.json()["quote_id"], "billing_country": "IN",
        "idempotency_key": "receipt-funded-next-purchase"})
    assert result.status_code == 200, result.text
    assert result.json()["order_id"] != created.json()["order_id"] and len(calls) == 2
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 2
        assert db.query(CreditGrant).count() == 1
        assert db.query(PaymentRefund).count() == 1
