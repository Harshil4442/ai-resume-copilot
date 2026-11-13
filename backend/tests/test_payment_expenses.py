"""Owned retained money facts; no live payment, invoice or bank authority."""

from datetime import UTC, datetime, timedelta

import pytest
from backend.app.billing.cost_policy import ExpensePolicy
from backend.app.billing.cost_policy import snapshot as accepted_policy_snapshot
from backend.app.database import Base
from backend.app.domains.credits import finance
from backend.app.domains.credits import payment_expenses as expenses
from backend.app.domains.credits.models import (
    CreditAllocation,
    CreditExecutionHold,
    CreditGrant,
    CreditReservation,
)
from backend.app.domains.credits.schemas import PurchaseQuoteView
from backend.app.models import (
    ModelCostLiability,
    PaymentEvent,
    PaymentOrder,
    PaymentRefund,
    PaymentTransaction,
)
from backend.tests.expense_policy_fixtures import synthetic_expense_policy
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

NOW = datetime(2026, 11, 5, 12, tzinfo=UTC)


def policy(**changes):
    return finance.FinancialPolicy.model_validate(
        dict(
            version="owned-loss-test",
            valid_from=NOW - timedelta(days=1),
            expires_at=NOW + timedelta(days=1),
            provenance="synthetic_test",
            evidence_sha256="a" * 64,
        )
        | changes
    )


@pytest.fixture
def factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(engine, autoflush=False, expire_on_commit=False)
    engine.dispose()


def paid(db, *, name="one", refund=0, fee=500, tax=0, mode="live", legacy=False):
    quote = PurchaseQuoteView(
        quote_id="owned-" + name,
        sku="starter_pass",
        name="Owned",
        catalog_version="owned-v1",
        terms_version="owned-v1",
        amount_minor=10000,
        credits=10,
        created_at=NOW - timedelta(hours=1),
        expires_at=NOW + timedelta(hours=1),
        pass_expires_at=NOW + timedelta(days=30),
        server_time=NOW,
    )
    snapshot = {
        "unified_purchase_quote": quote.model_dump(mode="json"),
        "release_review": {"policy": policy().model_dump(mode="json")},
    }
    if legacy:
        snapshot = accepted_policy_snapshot(
            ExpensePolicy.model_validate(synthetic_expense_policy())
        )
    order = PaymentOrder(
        public_id="owned-" + name,
        user_id=None,
        provider="razorpay",
        provider_mode=mode,
        sku=quote.sku,
        catalog_version=quote.catalog_version,
        cost_policy_snapshot=snapshot,
        entitlement_kind="service_credits" if legacy else "credit_pass",
        entitlement_quantity=10,
        billing_country_confirmed_at=NOW,
        gross_amount_minor=10000,
        currency="INR",
        paid_at=NOW,
        status="partially_refunded" if refund else "paid",
        refunded_amount_minor=refund,
        provider_fee_amount_minor=fee,
        customer_tax_amount_minor=tax,
    )
    db.add(order)
    db.flush()
    tx = PaymentTransaction(
        order_id=order.id,
        provider="razorpay",
        provider_payment_id="pay-" + name,
        status="captured",
        gross_amount_minor=10000,
        currency="INR",
        provider_fee_amount_minor=fee,
        customer_tax_amount_minor=tax,
    )
    db.add(tx)
    db.flush()
    if refund:
        db.add(
            PaymentRefund(
                order_id=order.id,
                transaction_id=tx.id,
                provider="razorpay",
                provider_refund_id="refund-" + name,
                amount_minor=refund,
                currency="INR",
                status="processed",
            )
        )
    db.commit()
    return order, tx


def work(db, order, *, name="one", group="one", settled=200, native=0, state="settled"):
    grant = CreditGrant(
        id="grant-" + name,
        user_id=None,
        source_type="payment_order",
        source_id=order.public_id,
        origin_type="payment_order",
        origin_id=order.public_id,
        funding_kind="paid",
        quantity=10,
        remaining_credits=0,
        expired_credits=0,
        issued_at=NOW,
        terms_snapshot={},
    )
    reservation = CreditReservation(
        id="reservation-" + name,
        user_id=None,
        idempotency_key=name,
        operation="resume_tailor",
        source_id=name,
        input_fingerprint="b" * 64,
        quote_snapshot={},
        execution_allowance_minor=400,
        reserved_credits=8,
        state="settled",
        created_at=NOW,
    )
    db.add_all([grant, reservation])
    db.flush()
    db.add(
        CreditAllocation(
            id="allocation-" + name,
            grant_id=grant.id,
            reservation_id=reservation.id,
            ordinal=1,
            quantity=8,
        )
    )
    hold = CreditExecutionHold(
        id="hold-" + name,
        user_id=None,
        reservation_id=reservation.id,
        financial_group_id=group,
        route_id="owned",
        funding_kind="prepaid",
        reserved_minor=400,
        native_reserved_minor=20,
        settled_minor=settled,
        native_settled_minor=native,
        state=state,
        delivery_state="delivered",
        snapshot={"policy": policy().model_dump(mode="json")},
        created_at=NOW,
    )
    db.add(hold)
    db.commit()
    return hold


def test_single_statement_snapshot_max_fee_and_tax_breakdown_not_extra(factory):
    with factory() as db:
        order, tx = paid(db)
        order.provider_fee_amount_minor = 400
        order.provider_fee_tax_minor = 80
        tx.provider_fee_tax_minor = 90
        db.commit()
        statements = []

        def observe(*args):
            statements.append(args[2])

        event.listen(db.get_bind(), "before_cursor_execute", observe)
        try:
            snapshot = expenses.collect_expenses(db, policy=policy())
        finally:
            event.remove(db.get_bind(), "before_cursor_execute", observe)
        assert len(statements) == 1 and "UNION ALL" in statements[0]
        assert not snapshot.unresolved
        assert [(row.kind, row.required_minor) for row in snapshot.components] == [
            ("payment_fee", 264)
        ]
        assert expenses.collect_expenses(db, policy=policy(gateway_gross_bps=9999)) == snapshot


def test_partial_refund_whole_fee_tax_work_are_once_conservative_not_principal(factory):
    with factory() as db:
        order, _ = paid(db, refund=1)
        work(db, order)
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert not snapshot.unresolved
        assert {row.kind: row.required_minor for row in snapshot.components} == {
            "payment_fee": 500,
            "customer_tax": 1526,
            "refunded_work": 200,
        }
        assert all(row.basis == "conservative" for row in snapshot.components)
        assert sum(row.required_minor for row in snapshot.components) == 2226


@pytest.mark.parametrize(
    "state,settled,native,expected",
    [("reserved", 10, None, 400), ("settled", 10, None, 400), ("settled", 10, 0, 10)],
)
def test_unknown_native_work_never_becomes_zero(factory, state, settled, native, expected):
    with factory() as db:
        order, _ = paid(db, refund=1)
        work(db, order, state=state, settled=settled, native=native)
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert not snapshot.unresolved
        assert (
            next(row.required_minor for row in snapshot.components if row.kind == "refunded_work")
            == expected
        )


@pytest.mark.parametrize("mutation", ["policy", "terms", "refund", "provider", "mode", "inclusive"])
def test_unresolved_original_facts_fail_closed_without_user_join(factory, mutation):
    with factory() as db:
        order, tx = paid(db, refund=1)
        if mutation == "policy":
            order.cost_policy_snapshot = {}
        if mutation == "terms":
            order.entitlement_quantity = 999
        if mutation == "refund":
            order.refunded_amount_minor = 2
        if mutation == "provider":
            tx.provider = "unknown"
        if mutation == "mode":
            order.provider_mode = "unknown"
        if mutation == "inclusive":
            tx.provider_fee_tax_minor = 501
        db.commit()
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert len(snapshot.unresolved) == 1


def test_test_mode_empty_cohort_and_complete_scope_refusal(factory, monkeypatch):
    with factory() as db:
        paid(db, mode="test", refund=10000)
        assert not expenses.collect_expenses(db, policy=policy()).components
        paid(db, name="live")
        monkeypatch.setattr(expenses, "MAX_FACTS", 1)
        with pytest.raises(finance.FundingUnavailable, match="complete review scope"):
            expenses.collect_expenses(db, policy=policy())


def test_signed_conflict_requires_zero_review_and_exact_fact_changes(factory):
    with factory() as db:
        order, _ = paid(db, fee=100)
        db.add(
            PaymentEvent(
                order_id=order.id,
                provider="razorpay",
                provider_event_id="owned-event",
                event_type="payment.captured",
                payload_sha256="a" * 64,
                error_code="provider_expense_conflict",
            )
        )
        db.commit()
        first = expenses.collect_expenses(db, policy=policy())
        assert len(first.components) == 1 and first.components[0].required_minor == 0
        assert first.components[0].review_required and first.components[0].basis == "conservative"
        order.provider_fee_amount_minor = 200
        db.commit()
        second = expenses.collect_expenses(db, policy=policy())
        assert second.components[0].key == first.components[0].key
        assert second.components[0].facts_sha256 != first.components[0].facts_sha256


def test_legacy_refund_ambiguous_global_model_is_bounded_once(factory):
    with factory() as db:
        paid(db, legacy=True, refund=1)
        paid(db, name="two", legacy=True, refund=1)
        db.add(
            ModelCostLiability(
                id="legacy-model",
                financial_group_id="legacy-group",
                attempt_number=1,
                provider="synthetic",
                model="synthetic",
                endpoint_key="c" * 64,
                currency="USD",
                pricing_quote={},
                input_token_estimate=1,
                reserved_cost_micros=1_000_000,
                settled_cost_micros=None,
                cost_state="unknown",
            )
        )
        db.commit()
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert not snapshot.unresolved
        work = [row for row in snapshot.components if row.kind == "refunded_work"]
        assert len(work) == 1 and work[0].required_minor == 2 and work[0].basis == "conservative"


@pytest.mark.parametrize(
    "case", ["only_failed", "multiple_capture", "unknown_state", "refund_failed_attempt"]
)
def test_captured_transaction_manifest_is_explicit_not_any_nonempty_history(factory, case):
    with factory() as db:
        order, tx = paid(db, refund=1)
        if case == "only_failed":
            tx.status = "failed"
        elif case == "unknown_state":
            tx.status = "authorized"
        else:
            extra = PaymentTransaction(
                order_id=order.id,
                provider="razorpay",
                provider_payment_id="second-owned",
                status="captured" if case == "multiple_capture" else "failed",
                gross_amount_minor=10000,
                currency="INR",
            )
            db.add(extra)
            db.flush()
            if case == "refund_failed_attempt":
                db.query(PaymentRefund).one().transaction_id = extra.id
        db.commit()
        assert expenses.collect_expenses(db, policy=policy()).unresolved


def test_failed_attempt_history_remains_allowed_beside_one_actual_capture(factory):
    with factory() as db:
        order, _ = paid(db)
        db.add(
            PaymentTransaction(
                order_id=order.id,
                provider="razorpay",
                provider_payment_id="failed-owned",
                status="failed",
                gross_amount_minor=10000,
                currency="INR",
            )
        )
        db.commit()
        assert not expenses.collect_expenses(db, policy=policy()).unresolved


def test_duplicate_unique_hold_group_and_missing_hold_refuse(factory, monkeypatch):
    from copy import copy

    with factory() as db:
        order, _ = paid(db, refund=1)
        work(db, order)
        rows = expenses._financial_rows(db)
        alternative = copy(rows["hold"][0])
        alternative.id = "owned-alternative"
        alternative.settled_minor = 300
        rows["hold"].append(alternative)
        monkeypatch.setattr(expenses, "_financial_rows", lambda _db: rows)
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert any(value.startswith("unresolved_refunded_work:") for value in snapshot.unresolved)
        rows["hold"] = []
        assert any(
            value.startswith("unresolved_missing_work_hold:")
            for value in expenses.collect_expenses(db, policy=policy()).unresolved
        )


def test_known_native_actual_above_unknown_aggregate_is_not_lost(factory):
    with factory() as db:
        order, _ = paid(db, refund=1)
        work(db, order, settled=None, native=900, state="outcome_unknown")
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert not snapshot.unresolved
        assert [
            (row.required_minor, row.basis)
            for row in snapshot.components
            if row.kind == "refunded_work"
        ] == [(900, "conservative")]


def test_actual_owner_erasure_detaches_but_cannot_remove_refund_cost_facts(factory):
    from backend.app.domains.credits.privacy import erase_owner_credit_links
    from backend.app.models import User

    with factory() as db:
        db.add(User(id=1, email="owned-erasure@example.invalid"))
        db.commit()
        order, _ = paid(db, refund=1)
        hold = work(db, order)
        order.user_id = 1
        hold.user_id = 1
        db.query(CreditGrant).update({CreditGrant.user_id: 1})
        db.query(CreditReservation).update({CreditReservation.user_id: 1})
        db.commit()
        before = expenses.collect_expenses(db, policy=policy())
        erase_owner_credit_links(db, user_id=1)
        order.user_id = None
        db.delete(db.get(User, 1))
        db.commit()
        assert expenses.collect_expenses(db, policy=policy()) == before


def test_two_refunded_origins_reaching_same_hold_do_not_double_group(factory):
    with factory() as db:
        first, _ = paid(db, name="first", refund=1)
        second, _ = paid(db, name="second", refund=1)
        hold = work(db, first)
        db.query(CreditAllocation).one().quantity = 4
        extra = CreditGrant(
            id="grant-second",
            user_id=None,
            source_type="payment_order",
            source_id=second.public_id,
            origin_type="payment_order",
            origin_id=second.public_id,
            funding_kind="paid",
            quantity=10,
            remaining_credits=0,
            expired_credits=0,
            issued_at=NOW,
            terms_snapshot={},
        )
        db.add(extra)
        db.flush()
        db.add(
            CreditAllocation(
                id="allocation-second",
                grant_id=extra.id,
                reservation_id=hold.reservation_id,
                ordinal=2,
                quantity=4,
            )
        )
        db.commit()
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert not snapshot.unresolved
        assert [
            row.required_minor for row in snapshot.components if row.kind == "refunded_work"
        ] == [200]


def test_legacy_attempt_and_whole_hold_shared_group_conservative_sum_once(factory):
    with factory() as db:
        paid(db, name="legacy", legacy=True, refund=1)
        order, _ = paid(db, name="unified", refund=1)
        work(db, order, group="shared-group")
        db.add(
            ModelCostLiability(
                id="unlinked-same-group",
                financial_group_id="shared-group",
                attempt_number=1,
                provider="synthetic",
                model="synthetic",
                endpoint_key="c" * 64,
                currency="USD",
                pricing_quote={},
                input_token_estimate=1,
                reserved_cost_micros=1_000_000,
                settled_cost_micros=None,
                cost_state="unknown",
            )
        )
        db.commit()
        snapshot = expenses.collect_expenses(db, policy=policy())
        assert not snapshot.unresolved
        assert [
            row.required_minor for row in snapshot.components if row.kind == "refunded_work"
        ] == [12000]


def test_unified_only_unlinked_call_uses_tied_original_conversion_and_linked_id_excluded(factory):
    from backend.app.domains.credits.models import CreditExecutionAttempt

    with factory() as db:
        order, _ = paid(db, refund=1)
        hold = work(db, order, group="original-policy-group", settled=100)
        original = policy(adverse_fx_minor_per_usd=10000, unrecoverable_provider_tax_bps=0)
        hold.snapshot = {"policy": original.model_dump(mode="json")}
        liability = ModelCostLiability(
            id="distinct-call",
            financial_group_id=hold.financial_group_id,
            attempt_number=1,
            provider="synthetic",
            model="synthetic",
            endpoint_key="c" * 64,
            currency="USD",
            pricing_quote={},
            input_token_estimate=1,
            reserved_cost_micros=10000,
            settled_cost_micros=10000,
            cost_state="settled",
        )
        db.add(liability)
        db.commit()
        snapshot = expenses.collect_expenses(db, policy=policy(adverse_fx_minor_per_usd=1))
        assert not snapshot.unresolved
        assert [
            (row.required_minor, row.basis)
            for row in snapshot.components
            if row.kind == "refunded_work"
        ] == [(200, "conservative")]
        db.add(
            CreditExecutionAttempt(
                id="owned-linked",
                hold_id=hold.id,
                liability_id=liability.id,
                ordinal=1,
                state="settled",
            )
        )
        db.commit()
        linked = expenses.collect_expenses(db, policy=policy())
        assert not linked.unresolved
        assert [row.required_minor for row in linked.components if row.kind == "refunded_work"] == [
            100
        ]
