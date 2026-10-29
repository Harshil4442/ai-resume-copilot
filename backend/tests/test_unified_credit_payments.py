"""Synthetic provider transport; real HTTP, quote, capture and refund state machines."""
from datetime import UTC, datetime, timedelta

import pytest
from backend.app.billing.razorpay import RazorpayAdapter, RazorpayProviderError
from backend.app.billing.reconciliation import OriginalOrder
from backend.app.domains.credits import release, service
from backend.app.domains.credits.finance import FinancialPolicy, FundingUnavailable
from backend.app.domains.credits.models import CreditCampaign, CreditGrant, CreditPurchaseQuote
from backend.app.domains.credits.pricing import CATALOG_VERSION
from backend.app.domains.credits.runtime import RuntimeConfig
from backend.app.models import EntitlementLedger, PaymentOrder, User
from backend.app.routers import credits
from backend.app.services.delivery_contracts import current_output_contract
from test_razorpay_billing import (  # Reuse verified-signature test transport only.
    _capture_payload,
    _post_event,
    _refund_payload,
)
from test_razorpay_billing import client as client
from test_razorpay_billing import enabled_checkout as enabled_checkout
from test_razorpay_billing import session_factory as session_factory
from test_unified_execution import budgets, route


def review_payload():
    now = datetime.now(UTC)
    return release.ReleaseReview(
        catalog_version=CATALOG_VERSION,
        policy=FinancialPolicy(version="test-policy", valid_from=now-timedelta(hours=1),
                               expires_at=now+timedelta(days=1), provenance="synthetic_test", evidence_sha256="a"*64),
        reviewed_by="synthetic-test", evidence={key: "a"*64 for key in
            ("quality", "legacy_rights", "commercial_hosting", "invoices", "security", "shadow_accounting", "delivery_routes")},
        shadow_started_at=now-timedelta(days=8), shadow_ended_at=now-timedelta(days=1),
        shadow_successful_operations=200, monthly_shared_minor=3_500_000,
        monthly_additional_business_minor=0, planning_paid_packs=2000, funded_runway_minor=3_500_000,
    )


@pytest.fixture
def unified(client, enabled_checkout, monkeypatch, session_factory):
    client.app.include_router(credits.router, prefix="/api")
    client.app.include_router(credits.public_router, prefix="/api")
    monkeypatch.setenv("UNIFIED_CREDITS_ENABLED", "true")
    review = review_payload()
    monkeypatch.setenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON", review.model_dump_json())
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "canary")
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "1")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", RuntimeConfig(
        policy=review.policy, budgets=budgets(), routes=(route(attempts=1),),
    ).model_dump_json())
    with session_factory() as db:
        now = datetime.now(UTC)
        db.add(CreditCampaign(id="launch", version="launch-v1", catalog_version=CATALOG_VERSION,
                             enabled=True, starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=29),
                             timezone="Asia/Kolkata", reference_price_verified=False))
        db.commit()
    calls = []

    def create(self, *, product, local_order_id, receipt):
        calls.append((local_order_id, product.amount_minor))
        return {"provider_order_id": f"order_unified{len(calls)}"}

    monkeypatch.setattr(RazorpayAdapter, "create_order", create)
    return client, calls


def start(client, *, key="first-purchase"):
    quote = client.post("/api/v1/credits/purchase-quotes", json={"sku": "starter_pass", "idempotency_key": key})
    assert quote.status_code == 200, quote.text
    payload = {"quote_id": quote.json()["quote_id"], "billing_country": "IN", "idempotency_key": key}
    result = client.post("/api/v1/credits/orders", json=payload)
    return quote.json(), payload, result


def test_review_cannot_enable_production_with_estimates_or_synthetic_evidence(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON", review_payload().model_dump_json())
    with pytest.raises(FundingUnavailable):
        release.current_review()
    for field, value in [("shadow_successful_operations", 199), ("evidence", {}),
                         ("funded_runway_minor", 1), ("planning_paid_packs", 1000),
                         ("planning_paid_packs", 2500)]:
        payload = review_payload().model_dump() | {field: value}
        with pytest.raises(ValueError):
            release.ReleaseReview.model_validate(payload)


def test_release_requires_approved_adverse_case_even_with_low_measured_overhead():
    payload = review_payload().model_dump() | {"monthly_shared_minor": 1_800_000,
        "monthly_additional_business_minor": 1_000_000}
    with pytest.raises(ValueError, match="minimum margin"):
        release.ReleaseReview.model_validate(payload)


def test_public_catalog_and_quote_gate_are_not_old_billing_flags(unified, monkeypatch):
    client, calls = unified
    response = client.get("/api/public/credits/catalog")
    assert response.status_code == 200
    assert response.json()["products"][0]["amount_minor"] == 5900
    assert "no-store" in response.headers["cache-control"]
    assert not client.get("/api/public/billing/catalog").json()["checkout_enabled"]
    monkeypatch.delenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON")
    assert not client.get("/api/public/credits/catalog").json()["checkout_enabled"]
    assert client.post("/api/v1/credits/purchase-quotes", json={"sku": "starter_pass", "idempotency_key": "missing-review"}).status_code == 503
    assert calls == []


def test_only_eligible_accounts_with_matching_funding_can_buy(unified, monkeypatch):
    client, calls = unified
    assert not client.get("/api/public/credits/catalog").json()["checkout_enabled"]
    assert client.get("/api/v1/credits/catalog").json()["checkout_enabled"]
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "2")
    assert not client.get("/api/v1/credits/catalog").json()["checkout_enabled"]
    assert client.post("/api/v1/credits/purchase-quotes", json={"sku": "starter_pass", "idempotency_key": "not-in-canary"}).status_code == 503
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "1")
    review = review_payload()
    review = review.model_copy(update={"policy": review.policy.model_copy(update={"version": "changed-financial-policy"})})
    monkeypatch.setenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON", review.model_dump_json())
    assert not client.get("/api/v1/credits/catalog").json()["checkout_enabled"]
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_AUDIENCE", "reviewed_public")
    assert not client.get("/api/public/credits/catalog").json()["checkout_enabled"]
    assert calls == []


@pytest.mark.parametrize("flag", ["FEATURE_ASYNC_ANALYSIS_ENABLED", "FEATURE_EVIDENCE_TAILORING_ENABLED",
                                  "OPTIONAL_AI_GENERATION_ENABLED"])
def test_disabled_delivery_services_cannot_be_advertised_or_purchased(unified, monkeypatch, flag):
    client, calls = unified
    assert client.get("/api/v1/credits/catalog").json()["eligible_services"]
    monkeypatch.setenv(flag, "false")
    catalog = client.get("/api/v1/credits/catalog").json()
    assert catalog["eligible_services"] == [] and not catalog["checkout_enabled"]
    assert client.get("/api/v1/credits/availability").json()["supported_operations"] == []
    response = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "disabled-delivery"})
    assert response.status_code == 503 and calls == []


def test_discovery_kill_switch_also_closes_its_purchase_offer(unified, monkeypatch):
    client, calls = unified
    config = RuntimeConfig(policy=review_payload().policy, budgets=budgets(), routes=(route(native=True),))
    # Match the exact review; independently rebuilt timestamps are not equivalent.
    config = config.model_copy(update={"policy": release.current_review().policy})
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "false")
    monkeypatch.setenv("EMPLOYER_PAID_HISTORY_MODE", "indexed")
    assert not client.get("/api/v1/credits/catalog").json()["checkout_enabled"]
    assert client.get("/api/v1/credits/availability").json()["supported_operations"] == []
    assert calls == []


def test_changed_service_list_requires_new_purchase_quote_without_provider_call(unified, monkeypatch, session_factory):
    client, calls = unified
    first = route(attempts=1)
    second = first.model_copy(update={"id": "bullet-route", "operation": "bullet_rewrite", "credits": 2,
                                      "output_contract_sha256": current_output_contract("bullet_rewrite")})
    limits = budgets()
    limits = limits.model_copy(update={"monthly_route_minor": limits.monthly_route_minor | {second.id: 10_000}})
    configuration = RuntimeConfig(policy=release.current_review().policy, budgets=limits, routes=(first, second))
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", configuration.model_dump_json())
    quoted = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "two-reviewed-services"})
    assert quoted.status_code == 200, quoted.text
    assert len(quoted.json()["eligible_services"]) == 2
    monkeypatch.setenv("FEATURE_EVIDENCE_TAILORING_ENABLED", "false")
    assert client.get("/api/v1/credits/catalog").json()["checkout_enabled"]
    attempted = client.post("/api/v1/credits/orders", json={"quote_id": quoted.json()["quote_id"],
        "billing_country": "IN", "idempotency_key": "two-reviewed-services"})
    assert attempted.status_code == 409, attempted.text
    assert attempted.json()["detail"]["order_created"] is False and calls == []
    with session_factory() as db:
        assert db.query(PaymentOrder).count() == 0
        assert db.query(CreditPurchaseQuote).one().accepted_order_id is None


def test_route_with_another_services_output_contract_cannot_create_a_purchase(unified, monkeypatch, session_factory):
    client, calls = unified
    original_quote = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "before-contract-drift"})
    assert original_quote.status_code == 200, original_quote.text
    first = route(attempts=1)
    # A changed service needs its own reviewed output contract. Relabeling a
    # qualified tailoring route must never make bullet delivery purchasable.
    wrong = first.model_copy(update={"id": "wrong-bullet-route", "operation": "bullet_rewrite", "credits": 2})
    assert wrong.output_contract_sha256 != current_output_contract("bullet_rewrite")
    limits = budgets()
    limits = limits.model_copy(update={"monthly_route_minor": limits.monthly_route_minor | {wrong.id: 10_000}})
    config = RuntimeConfig(policy=release.current_review().policy, budgets=limits, routes=(first, wrong))
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
    catalog = client.get("/api/v1/credits/catalog")
    assert catalog.status_code == 200
    assert catalog.json()["eligible_services"] == [] and not catalog.json()["checkout_enabled"]
    quote = client.post("/api/v1/credits/purchase-quotes", json={
        "sku": "starter_pass", "idempotency_key": "wrong-service-contract"})
    assert quote.status_code == 503 and calls == []
    order = client.post("/api/v1/credits/orders", json={
        "quote_id": original_quote.json()["quote_id"], "billing_country": "IN",
        "idempotency_key": "before-contract-drift"})
    assert order.status_code == 503 and calls == []
    with session_factory() as db:
        assert db.query(CreditPurchaseQuote).count() == 1
        assert db.query(CreditPurchaseQuote).one().accepted_order_id is None
        assert db.query(PaymentOrder).count() == 0


def test_payment_recovery_remains_readable_but_not_payable_while_service_paused(unified, monkeypatch):
    client, calls = unified
    _, payload, result = start(client)
    assert result.status_code == 200, result.text
    monkeypatch.setenv("FEATURE_ASYNC_ANALYSIS_ENABLED", "false")
    observed = client.get(f"/api/v1/credits/orders/{result.json()['order_id']}")
    assert observed.status_code == 200 and not observed.json()["checkout_available"]
    assert observed.json()["accepted_quote"]["eligible_services"]
    assert client.post("/api/v1/credits/orders", json=payload).status_code == 503
    assert len(calls) == 1


def test_accepted_quote_price_is_immutable_and_capture_only_grants_once(unified, session_factory):
    client, calls = unified
    quote, payload, result = start(client)
    assert result.status_code == 200, result.text
    created = result.json()
    assert created["amount_minor"] == 5900 and created["accepted_quote"]["credits"] == 40
    assert created["accepted_quote"]["eligible_services"][0]["operation"] == "resume_tailor"
    assert created["accepted_quote"]["eligible_services"][0]["credits"] == 8
    assert client.post("/api/v1/credits/orders", json=payload).json()["order_id"] == created["order_id"]
    assert len(calls) == 1
    assert client.get("/api/billing/recent-order").json() is None
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 0
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        capture = _capture_payload(order)
        original = OriginalOrder.from_order(order)
        assert original.quote["credits"] == 40 and original.quote["amount_minor"] == 5900
        assert "server_time" not in original.quote
        campaign = db.query(CreditCampaign).one()
        campaign.enabled = False
        db.commit()
    assert _post_event(client, "evt_unified_capture", capture).status_code == 200
    assert _post_event(client, "evt_unified_capture_again", capture).status_code == 200
    with session_factory() as db:
        assert db.query(CreditGrant).count() == 1
        assert db.query(EntitlementLedger).one().entitlement_kind == "credit_pass"
        assert db.query(User).one().ai_credits == 20
        assert db.query(User).one().job_service_credits == 0
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 40
    status = client.get(f"/api/v1/credits/orders/{created['order_id']}").json()
    assert status["fulfilled"] and status["status"] == "paid" and status["pass_expires_at"]


def test_partial_then_full_cash_refund_revokes_pass_and_preserves_legacy(unified, session_factory):
    client, _ = unified
    _, _, result = start(client)
    assert result.status_code == 200
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        capture = _capture_payload(order)
        half = _refund_payload(order, "rfnd_half", 2950)
        rest = _refund_payload(order, "rfnd_rest", 2950)
    assert _post_event(client, "evt_capture", capture).status_code == 200
    assert _post_event(client, "evt_half", half).status_code == 200
    assert _post_event(client, "evt_half", half).status_code == 200
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 20
    assert _post_event(client, "evt_rest", rest).status_code == 200
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 0
    with session_factory() as db:
        assert db.query(EntitlementLedger).one().status == "refunded"
        assert db.query(User).one().ai_credits == 20


def test_refund_before_capture_and_late_capture_do_not_regrant(unified, session_factory):
    client, _ = unified
    start(client)
    with session_factory() as db:
        order = db.query(PaymentOrder).one()
        half = _refund_payload(order, "rfnd_first", 2950)
        capture = _capture_payload(order)
    assert _post_event(client, "evt_refund_first", half).status_code == 200
    assert _post_event(client, "evt_capture_later", capture).status_code == 200
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 20


def test_unknown_provider_outcome_blocks_any_second_order(unified, monkeypatch):
    client, _ = unified
    attempts = []
    def unknown(*args, **kwargs):
        attempts.append(1)
        raise RazorpayProviderError("synthetic timeout")
    monkeypatch.setattr(RazorpayAdapter, "create_order", unknown)
    _, payload, first = start(client)
    assert first.status_code == 409
    assert client.post("/api/v1/credits/orders", json=payload).status_code == 409
    _, _, second = start(client, key="second-purchase")
    assert second.status_code == 409
    assert attempts == [1]
    assert client.get("/api/v1/credits/recent-order").json()["payment_status"] == "creation_unknown"


@pytest.mark.parametrize("transport_unknown", [False, True])
def test_reconciled_capture_wins_race_with_original_provider_response(unified, session_factory, monkeypatch, transport_unknown):
    client, _ = unified
    def concurrent_settlement(self, *, product, local_order_id, receipt):
        with session_factory() as db:
            order = db.query(PaymentOrder).filter_by(public_id=local_order_id).one()
            order.provider_order_id = "order_boundduringrequest"
            db.commit()
            payload = _capture_payload(order)
        assert _post_event(client, "evt_racing_capture", payload).status_code == 200
        if transport_unknown:
            raise RazorpayProviderError("response lost after capture")
        return {"provider_order_id": "order_boundduringrequest"}
    monkeypatch.setattr(RazorpayAdapter, "create_order", concurrent_settlement)
    _, _, result = start(client)
    assert result.status_code == 409
    status = client.get("/api/v1/credits/recent-order").json()
    assert status["payment_status"] == "paid" and status["fulfilled"]
    assert not status["checkout_available"]
    assert client.get("/api/v1/credits/wallet").json()["available_credits"] == 40


def test_quote_expiry_owner_and_client_amount_tampering_never_dispatch(unified, session_factory):
    client, calls = unified
    quote = client.post("/api/v1/credits/purchase-quotes", json={"sku": "starter_pass", "idempotency_key": "quote-only"}).json()
    payload = {"quote_id": quote["quote_id"], "billing_country": "IN", "idempotency_key": "only-order"}
    assert client.post("/api/v1/credits/orders", json=payload | {"amount_minor": 1}).status_code == 422
    with session_factory() as db:
        row = db.query(CreditPurchaseQuote).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        row.created_at = datetime.now(UTC) - timedelta(hours=1)
        db.commit()
    assert client.post("/api/v1/credits/orders", json=payload).status_code == 409
    assert client.get("/api/v1/credits/orders/ord_another_user").status_code == 404
    assert calls == []


def test_history_is_owned_and_keyset_paginated(unified, session_factory):
    client, _ = unified
    with session_factory() as db:
        user = db.query(User).one()
        other = User(email="other@example.test")
        db.add(other)
        db.flush()
        now = datetime.now(UTC)
        for owner in (user.id, other.id):
            for index in range(3):
                service.grant_credits(db, user_id=owner, quantity=1, source_type="test", source_id=f"grant-{index}",
                    funding_kind="promotion", terms_snapshot={}, expires_at=now+timedelta(days=30), now=now)
        db.commit()
    first = client.get("/api/v1/credits/history?limit=2").json()
    second = client.get("/api/v1/credits/history", params={"limit": 2, "cursor": first["next_cursor"]}).json()
    assert len(first["items"]) == 2 and len(second["items"]) == 1
    assert len({row["id"] for row in first["items"] + second["items"]}) == 3
    assert second["next_cursor"] is None
    assert client.get("/api/v1/credits/history?cursor=invalid").status_code == 400


def test_account_erasure_detaches_financial_history_without_deleting_it(unified, session_factory):
    from backend.app.domains.credits.models import CreditAccount, CreditEvent, CreditReservation
    from backend.app.domains.credits.privacy import erase_owner_credit_links, export_account_data

    client, _ = unified
    start(client)
    with session_factory() as db:
        capture = _capture_payload(db.query(PaymentOrder).one())
    assert _post_event(client, "evt_capture_delete", capture).status_code == 200
    with session_factory() as db:
        owner = db.query(User).one().id
        service.reserve_credits(db, user_id=owner, operation="resume_tailor", source_id="run-owned",
            idempotency_key="before-erasure", credits=8, input_fingerprint="a"*64,
            quote_snapshot={"credits": 8}, execution_allowance_minor=360)
        db.commit()
        export = export_account_data(db, owner)
        assert export["wallet"]["available_credits"] == 32
        assert export["grants"][0]["quantity"] == 40
        erase_owner_credit_links(db, user_id=owner)
        db.commit()
        assert db.query(CreditAccount).count() == db.query(CreditPurchaseQuote).count() == 0
        assert db.query(CreditGrant).one().user_id is None
        assert db.query(CreditGrant).one().remaining_credits == 32
        assert db.query(CreditReservation).one().execution_allowance_minor == 360
        assert db.query(CreditReservation).one().user_id is None
        assert db.query(CreditEvent).filter(CreditEvent.user_id.is_not(None)).count() == 0
