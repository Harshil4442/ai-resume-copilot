"""Real database state transitions for unified credits; no providers or real payments."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.app.database import Base
from backend.app.domains.credits import pricing, service
from backend.app.domains.credits.models import (
    CreditAccount,
    CreditAllocation,
    CreditCampaign,
    CreditEvent,
    CreditGrant,
    CreditPurchaseQuote,
    CreditReservation,
)
from backend.app.models import User
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 10, 27, 12, tzinfo=UTC)


@pytest.fixture
def factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    maker = sessionmaker(engine, autoflush=False)
    with maker() as db:
        db.add_all(
            [
                User(
                    id=i,
                    email=f"credit-test-{i}@example.com",
                    ai_credits=17,
                    job_service_credits=13,
                )
                for i in (1, 2)
            ]
        )
        db.commit()
    yield maker
    engine.dispose()


def campaign(*, start=NOW - timedelta(days=1), verified=False):
    return CreditCampaign(
        id="launch",
        version="launch-v1",
        catalog_version=pricing.CATALOG_VERSION,
        enabled=True,
        starts_at=start,
        ends_at=start + timedelta(days=30),
        timezone="Asia/Kolkata",
        reference_price_verified=verified,
        reference_evidence_id="reviewed-price-history" if verified else None,
        reference_approved_at=NOW - timedelta(days=2) if verified else None,
    )


def grant(
    db, *, owner=1, quantity=40, source="order-one", expiry=NOW + timedelta(days=30), now=NOW
):
    return service.grant_credits(
        db,
        user_id=owner,
        quantity=quantity,
        source_type="payment_order",
        source_id=source,
        funding_kind="paid",
        terms_snapshot={"accepted": source},
        expires_at=expiry,
        now=now,
    )


def reserve(db, *, owner=1, credits=8, source="run-one", key="request-one", now=NOW):
    return service.reserve_credits(
        db,
        user_id=owner,
        operation="resume_tailor",
        source_id=source,
        idempotency_key=key,
        credits=credits,
        input_fingerprint="a" * 64,
        quote_snapshot={"credits": credits, "version": pricing.TARIFF_VERSION},
        execution_allowance_minor=credits * 45,
        now=now,
    )


def test_catalog_has_no_invented_active_offer():
    absent = pricing.public_catalog(campaign=None, now=NOW)
    assert not absent.checkout_enabled
    assert [p.amount_minor for p in absent.products] == [7900, 22900, 59900]
    assert all(p.campaign is None and not p.purchase_enabled for p in absent.products)
    live = pricing.public_catalog(campaign=campaign(), now=NOW, checkout_enabled=True)
    assert [p.amount_minor for p in live.products] == [5900, 17900, 44900]
    assert [p.credits for p in live.products] == [40, 130, 360]
    assert all(
        p.campaign.status == "active" and not p.campaign.reference_price_verified
        for p in live.products
    )
    assert all(p.duration_days == 30 and not p.auto_renews for p in live.products)


def test_campaign_boundaries_and_evidence():
    row = campaign(start=NOW, verified=True)
    before = pricing.public_catalog(campaign=row, now=NOW - timedelta(seconds=1))
    start = pricing.public_catalog(campaign=row, now=NOW)
    end = pricing.public_catalog(campaign=row, now=NOW + timedelta(days=30))
    assert before.products[0].campaign.status == "scheduled"
    assert start.products[0].amount_minor == 5900
    assert start.products[0].campaign.reference_price_verified
    assert end.products[0].amount_minor == 7900
    assert end.products[0].campaign.status == "ended"
    assert not before.products[0].campaign.reference_price_verified
    assert not end.products[0].campaign.reference_price_verified
    row.reference_evidence_id = None
    with pytest.raises(pricing.CreditContractError, match="reference evidence"):
        pricing.public_catalog(campaign=row, now=NOW)


@pytest.mark.parametrize(
    "change", [{"ends_at": NOW}, {"timezone": "Not/AZone"}, {"catalog_version": "old"}]
)
def test_invalid_campaign_cannot_authorize_checkout(change):
    row = campaign()
    for key, value in change.items():
        setattr(row, key, value)
    with pytest.raises(pricing.CreditContractError):
        pricing.public_catalog(campaign=row, now=NOW, checkout_enabled=True)


def test_quote_crosses_campaign_end_but_never_resets_its_lifetime(factory):
    with factory() as db:
        row = campaign(start=NOW - timedelta(days=30) + timedelta(minutes=1))
        db.add(row)
        db.commit()
        quote = service.issue_purchase_quote(
            db,
            user_id=1,
            sku="starter_pass",
            idempotency_key="purchase-one",
            checkout_enabled=True,
            now=NOW,
        )
        assert quote.snapshot["amount_minor"] == 5900
        original = dict(quote.snapshot)
        db.commit()
        later = NOW + timedelta(minutes=2)
        assert pricing.public_catalog(campaign=row, now=later).products[0].amount_minor == 7900
        accepted = service.accept_purchase_quote(
            db, user_id=1, quote_id=quote.id, order_id="order-verified", now=later
        )
        assert accepted.snapshot == original
        assert accepted.state == "accepted"
        repeated = service.issue_purchase_quote(
            db,
            user_id=1,
            sku="starter_pass",
            idempotency_key="purchase-one",
            checkout_enabled=False,
            now=NOW + timedelta(hours=1),
        )
        assert repeated.id == quote.id and repeated.snapshot == original
        response = service.quote_response(repeated, now=later)
        assert response.server_time == later and response.campaign.status == "active"
        assert response.expires_at == NOW + timedelta(minutes=15)
        with pytest.raises(pricing.CreditContractError, match="another checkout"):
            service.accept_purchase_quote(
                db, user_id=1, quote_id=quote.id, order_id="different-order", now=later
            )
        db.rollback()


def test_expired_and_other_owner_quotes_are_not_accepted(factory):
    with factory() as db:
        quote = service.issue_purchase_quote(
            db,
            user_id=1,
            sku="growth_pass",
            idempotency_key="purchase-one",
            checkout_enabled=True,
            now=NOW,
        )
        db.commit()
        with pytest.raises(pricing.CreditContractError, match="expired"):
            service.accept_purchase_quote(
                db,
                user_id=1,
                quote_id=quote.id,
                order_id="order-one",
                now=NOW + timedelta(minutes=15),
            )
        with pytest.raises(pricing.CreditContractError, match="not found"):
            service.accept_purchase_quote(
                db, user_id=2, quote_id=quote.id, order_id="order-one", now=NOW
            )
        assert db.query(CreditGrant).count() == 0
        assert db.query(CreditPurchaseQuote).one().state == "quoted"


def test_fulfillment_is_idempotent_and_late_webhook_keeps_30_usable_days(factory):
    with factory() as db:
        quote = service.issue_purchase_quote(
            db,
            user_id=1,
            sku="starter_pass",
            idempotency_key="purchase-one",
            checkout_enabled=True,
            now=NOW,
        )
        service.accept_purchase_quote(
            db, user_id=1, quote_id=quote.id, order_id="order-one", now=NOW
        )
        first = service.fulfill_purchase_quote(
            db,
            user_id=1,
            quote_id=quote.id,
            order_id="order-one",
            captured_at=NOW,
            now=NOW + timedelta(days=31),
        )
        db.commit()
        assert pricing.aware(first.expires_at) == NOW + timedelta(days=61)
        again = service.fulfill_purchase_quote(
            db,
            user_id=1,
            quote_id=quote.id,
            order_id="order-one",
            captured_at=NOW,
            now=NOW + timedelta(days=32),
        )
        assert again.id == first.id
        assert pricing.aware(again.expires_at) == NOW + timedelta(days=61)
        assert db.query(CreditGrant).count() == 1 and db.query(CreditEvent).count() == 1
        assert db.get(User, 1).ai_credits == 17 and db.get(User, 1).job_service_credits == 13


def test_earliest_expiry_then_permanent_legacy_and_atomic_rollback(factory):
    with factory() as db:
        permanent = grant(db, source="approved-legacy", quantity=10, expiry=None)
        later = grant(db, source="later", quantity=10, expiry=NOW + timedelta(days=20))
        sooner = grant(db, source="sooner", quantity=5, expiry=NOW + timedelta(days=2))
        db.commit()
        row = reserve(db, credits=8)
        allocations = (
            db.query(CreditAllocation)
            .filter_by(reservation_id=row.id)
            .order_by(CreditAllocation.ordinal)
            .all()
        )
        assert [(a.grant_id, a.quantity) for a in allocations] == [(sooner.id, 5), (later.id, 3)]
        assert permanent.remaining_credits == 10
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 17
        db.rollback()
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 25
        assert db.query(CreditReservation).count() == db.query(CreditAllocation).count() == 0


def test_reservation_replay_does_not_spend_twice_and_input_change_is_refused(factory):
    with factory() as db:
        grant(db)
        row = reserve(db)
        db.commit()
        repeated = reserve(db, key="second-key-same-operation")
        assert repeated.id == row.id
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 32
        with pytest.raises(pricing.CreditContractError, match="different accepted terms"):
            reserve(db, credits=9)
        assert db.query(CreditReservation).count() == 1
        assert db.query(CreditEvent).filter_by(event_type="reserve").count() == 1


def test_expiry_does_not_invalidate_reserved_delivery_and_failure_restores_seven_days(factory):
    with factory() as db:
        original = grant(db, expiry=NOW + timedelta(hours=1))
        row = reserve(db, credits=8)
        db.commit()
        later = NOW + timedelta(days=2)
        service.expire_credits(db, user_id=1, now=later)
        assert original.remaining_credits == 0 and original.expired_credits == 32
        assert service.wallet(db, user_id=1, now=later).reserved_credits == 8
        service.settle_reservation(
            db, user_id=1, reservation_id=row.id, committed_credits=3, now=later
        )
        db.commit()
        restoration = db.query(CreditGrant).filter_by(parent_grant_id=original.id).one()
        assert restoration.quantity == restoration.remaining_credits == 5
        assert pricing.aware(restoration.expires_at) == later + timedelta(days=7)
        assert restoration.origin_id == original.origin_id
        assert service.wallet(db, user_id=1, now=later).available_credits == 5
        assert service.wallet(db, user_id=1, now=later).reserved_credits == 0
        service.settle_reservation(
            db, user_id=1, reservation_id=row.id, committed_credits=3, now=later
        )
        assert db.query(CreditGrant).count() == 2
        with pytest.raises(pricing.CreditContractError, match="different final outcome"):
            service.settle_reservation(
                db, user_id=1, reservation_id=row.id, committed_credits=0, now=later
            )


def test_restore_does_not_shorten_later_or_permanent_original_expiry(factory):
    with factory() as db:
        original = grant(db, quantity=5, expiry=None)
        row = reserve(db, credits=5)
        service.settle_reservation(
            db, user_id=1, reservation_id=row.id, committed_credits=0, now=NOW
        )
        restored = db.query(CreditGrant).filter_by(parent_grant_id=original.id).one()
        assert restored.expires_at is None
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 5


def test_cash_refund_during_reserved_work_cannot_mint_free_failure_credits(factory):
    with factory() as db:
        original = grant(db)
        row = reserve(db, credits=8)
        service.revoke_origin(
            db,
            user_id=1,
            origin_type="payment_order",
            origin_id="order-one",
            target_credits=40,
            now=NOW,
        )
        db.commit()
        assert original.remaining_credits == 0
        assert db.get(CreditAccount, 1).debt_credits == 8
        service.settle_reservation(
            db, user_id=1, reservation_id=row.id, committed_credits=3, now=NOW
        )
        db.commit()
        assert db.get(CreditAccount, 1).debt_credits == 3
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 0
        restored = db.query(CreditGrant).filter_by(parent_grant_id=original.id).one()
        assert restored.quantity == 5 and restored.remaining_credits == 0
        assert (
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=40,
                now=NOW,
            )
            == 0
        )
        grant(db, quantity=10, source="fresh-purchase")
        assert db.get(CreditAccount, 1).debt_credits == 0
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 7


def test_cash_refund_tracks_restorations_already_reused_and_expired_unused_credit(factory):
    with factory() as db:
        grant(db, expiry=NOW + timedelta(hours=1))
        row = reserve(db, credits=10)
        later = NOW + timedelta(days=2)
        service.settle_reservation(
            db, user_id=1, reservation_id=row.id, committed_credits=0, now=later
        )
        second = reserve(db, credits=6, source="run-two", key="request-two", now=later)
        service.settle_reservation(
            db, user_id=1, reservation_id=second.id, committed_credits=6, now=later
        )
        service.revoke_origin(
            db,
            user_id=1,
            origin_type="payment_order",
            origin_id="order-one",
            target_credits=40,
            now=later,
        )
        db.commit()
        assert service.wallet(db, user_id=1, now=later).available_credits == 0
        assert db.get(CreditAccount, 1).debt_credits == 6
        event = db.query(CreditEvent).filter_by(event_type="revoke").one()
        assert event.details == {"target_credits": 40, "expired_offset": 30, "debt_added": 6}


def test_other_owner_and_expired_credits_cannot_fund_work(factory):
    with factory() as db:
        grant(db, expiry=NOW + timedelta(seconds=1))
        with pytest.raises(service.InsufficientCredits):
            reserve(db, owner=2)
        with pytest.raises(service.InsufficientCredits):
            reserve(db, now=NOW + timedelta(seconds=1))
        db.rollback()
        assert db.query(CreditReservation).count() == 0


def test_cash_refund_is_cumulative_and_does_not_revoke_twice(factory):
    with factory() as db:
        grant(db)
        assert (
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=10,
                now=NOW,
            )
            == 10
        )
        assert (
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=10,
                now=NOW,
            )
            == 0
        )
        assert (
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=20,
                now=NOW,
            )
            == 10
        )
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 20
        with pytest.raises(pricing.CreditContractError, match="backwards"):
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=10,
                now=NOW,
            )


def test_expired_unused_cash_refund_preserves_reconstructible_ledger(factory):
    with factory() as db:
        original = grant(db, expiry=NOW + timedelta(hours=1))
        later = NOW + timedelta(days=2)
        assert (
            service.revoke_origin(
                db,
                user_id=1,
                origin_type="payment_order",
                origin_id="order-one",
                target_credits=40,
                now=later,
            )
            == 40
        )
        assert service.wallet(db, user_id=1, now=later).available_credits == 0
        assert db.get(CreditAccount, 1).debt_credits == 0
        assert sum(event.amount for event in db.query(CreditEvent).all()) == 0
        assert db.query(CreditEvent).filter_by(event_type="revoke").one().amount == 0
        repeated = grant(db, expiry=NOW + timedelta(hours=1), now=later)
        assert repeated.id == original.id and db.query(CreditGrant).count() == 1


@pytest.mark.parametrize("quantity", [True, -1, 0, 10**10])
def test_invalid_credit_quantities_cannot_create_grants(factory, quantity):
    with factory() as db, pytest.raises(pricing.CreditContractError):
        grant(db, quantity=quantity)


def test_allowance_above_45_paise_per_credit_is_rejected(factory):
    with factory() as db:
        grant(db)
        with pytest.raises(pricing.CreditContractError, match="execution allowance"):
            service.reserve_credits(
                db,
                user_id=1,
                operation="resume_tailor",
                source_id="run-one",
                idempotency_key="request-one",
                credits=8,
                input_fingerprint="a" * 64,
                quote_snapshot={"credits": 8},
                execution_allowance_minor=361,
                now=NOW,
            )
        assert db.query(CreditReservation).count() == 0
