"""Synthetic operator declarations; never actual bank or invoice verification."""

from datetime import timedelta

import pytest
from backend.app.domains.credits import (
    finance,
)
from backend.app.domains.credits import (
    payment_expenses as expenses,
)
from backend.app.domains.credits import (
    payment_funding as funding,
)
from backend.app.models import AdminAuditEvent, PaymentOrder
from backend.tests.test_payment_expenses import NOW, factory, paid, policy

assert factory


def declaration(*, reference="d" * 64, amount=1000, now=NOW, **changes):
    return funding.FundingDeclaration.model_validate(
        dict(
            external_reference_sha256=reference,
            amount_minor=amount,
            funded_at=NOW - timedelta(hours=1),
            availability_basis="available_after_existing_obligations",
            evidence_sha256="e" * 64,
            reviewer_reference_sha256="f" * 64,
            reviewed_at=now,
            review_valid_until=now + timedelta(hours=1),
        )
        | changes
    )


def request(component, *, amount=None, reference="d" * 64, now=NOW, **changes):
    return funding.AllocationRequest.model_validate(
        dict(
            component_key=component.key,
            expected_facts_sha256=component.facts_sha256,
            funding_reference_sha256=reference,
            amount_minor=component.required_minor if amount is None else amount,
            expense_evidence_sha256="e" * 64,
            reviewer_reference_sha256="f" * 64,
            reviewed_at=now,
            review_valid_until=now + timedelta(hours=1),
            basis="reviewed_conservative_envelope"
            if component.basis == "conservative"
            else "reviewed_invoice_declaration",
        )
        | changes
    )


def cover(db, *, name="one", amount=1000):
    order, _ = paid(db, name=name)
    component = expenses.collect_expenses(db, policy=policy()).components[0]
    original = funding.register_declaration(db, declaration(amount=amount), now=NOW)
    allocation = request(component)
    result = funding.allocate_loss(db, allocation, policy=policy(), now=NOW)
    db.commit()
    return order, component, allocation, result, original


def test_empty_initial_cohort_needs_no_invented_funding(factory):
    with factory() as db:
        expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)
        assert db.query(AdminAuditEvent).count() == 0


def test_current_funded_exact_facts_unblocks_without_rewriting_payment(factory):
    with factory() as db:
        order, component, allocation, result, original = cover(db)
        assert component.required_minor == 264
        assert result["covered_minor"] == 264
        expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)
        rows = funding.audit_rows(db, tuple([*original.values(), result["allocation_id"]]))
        assert len(rows) == 3 and all(row["actor_user_id"] is None for row in rows.values())
        assert order.user_id is None and db.query(PaymentOrder).count() == 1
        assert allocation.basis == "reviewed_invoice_declaration"


def test_source_reference_cannot_be_redeclared_to_reset_or_increase_capacity(factory):
    with factory() as db:
        cover(db, amount=300)
        renewed = declaration(now=NOW + timedelta(minutes=10))
        with pytest.raises(finance.FundingUnavailable, match="original_audit_changed"):
            funding.register_declaration(db, renewed, now=NOW + timedelta(minutes=10))
        db.rollback()
        funding.register_declaration(
            db,
            declaration(amount=300, now=NOW + timedelta(minutes=10)),
            now=NOW + timedelta(minutes=10),
        )
        paid(db, name="two")
        # Select the uncovered component independently of key ordering.
        covered = {
            row.after_state["request"]["component_key"]
            for row in db.query(AdminAuditEvent).filter_by(action=funding.ALLOC_ACTION)
        }
        second = next(
            row
            for row in expenses.collect_expenses(db, policy=policy()).components
            if row.key not in covered
        )
        with pytest.raises(finance.FundingUnavailable, match="available_funding_exhausted"):
            funding.allocate_loss(
                db,
                request(second, now=NOW + timedelta(minutes=10)),
                policy=policy(),
                now=NOW + timedelta(minutes=10),
            )


def test_exact_allocation_replay_after_expiry_never_spends_twice(factory):
    with factory() as db:
        _, _, allocation, result, _ = cover(db)
        count = db.query(AdminAuditEvent).count()
        assert (
            funding.allocate_loss(db, allocation, policy=policy(), now=NOW + timedelta(hours=2))
            == result
        )
        assert db.query(AdminAuditEvent).count() == count
        expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW + timedelta(hours=2))


def test_changed_preimage_needs_explicit_review_then_only_incremental_capacity(factory):
    with factory() as db:
        order, component, allocation, _, _ = cover(db)
        order.provider_fee_amount_minor = 600
        db.commit()
        changed = expenses.collect_expenses(db, policy=policy()).components[0]
        assert changed.key == component.key and changed.facts_sha256 != component.facts_sha256
        with pytest.raises(finance.FundingUnavailable, match="expense_funding_review_required"):
            expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)
        with pytest.raises(finance.FundingUnavailable, match="expense_facts_changed"):
            funding.allocate_loss(
                db,
                allocation.model_copy(update={"expense_evidence_sha256": "b" * 64}),
                policy=policy(),
                now=NOW,
            )
        funding.allocate_loss(db, request(changed, amount=100), policy=policy(), now=NOW)
        expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)
        assert (
            sum(
                row.after_state["request"]["amount_minor"]
                for row in db.query(AdminAuditEvent).filter_by(action=funding.ALLOC_ACTION)
            )
            == 364
        )


def test_same_funded_amount_drift_requires_zero_review_not_new_money(factory):
    with factory() as db:
        order, _, _, _, _ = cover(db)
        order.provider_fee_tax_minor = 50
        db.commit()
        changed = expenses.collect_expenses(db, policy=policy()).components[0]
        with pytest.raises(finance.FundingUnavailable):
            expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)
        funding.allocate_loss(
            db, request(changed, amount=0, reference=None), policy=policy(), now=NOW
        )
        expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)


def test_no_new_allocation_from_expired_funding_review_but_renewal_preserves_consumption(factory):
    with factory() as db:
        cover(db)
        paid(db, name="two")
        allocated = {
            row.after_state["request"]["component_key"]
            for row in db.query(AdminAuditEvent).filter_by(action=funding.ALLOC_ACTION)
        }
        second = next(
            row
            for row in expenses.collect_expenses(db, policy=policy()).components
            if row.key not in allocated
        )
        later = NOW + timedelta(hours=2)
        with pytest.raises(finance.FundingUnavailable, match="funding_review_expired"):
            funding.allocate_loss(db, request(second, now=later), policy=policy(), now=later)
        funding.register_declaration(db, declaration(now=later), now=later)
        funding.allocate_loss(db, request(second, now=later), policy=policy(), now=later)
        expenses.assert_new_exposure_funded(db, policy=policy(), now=later)


@pytest.mark.parametrize("change", ["body", "actor", "missingfund", "missingreview", "timestamp"])
def test_tampered_or_missing_detached_original_facts_fail_closed(factory, change):
    with factory() as db:
        _, _, _, result, original = cover(db)
        if change == "body":
            db.get(AdminAuditEvent, result["allocation_id"]).after_state = {"missing": "request"}
        if change == "actor":
            db.get(
                AdminAuditEvent, result["allocation_id"]
            ).actor_email = "candidate@example.invalid"
        if change == "missingfund":
            db.delete(db.get(AdminAuditEvent, original["funding_id"]))
        if change == "missingreview":
            db.delete(db.get(AdminAuditEvent, original["review_id"]))
        if change == "timestamp":
            db.get(AdminAuditEvent, result["allocation_id"]).created_at = NOW - timedelta(days=1)
        db.commit()
        with pytest.raises(finance.FundingUnavailable):
            expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)


def test_unresolved_original_policy_cannot_be_funded_as_zero(factory):
    with factory() as db:
        order, _ = paid(db)
        order.cost_policy_snapshot = {}
        db.commit()
        funding.register_declaration(db, declaration(), now=NOW)
        with pytest.raises(finance.FundingUnavailable, match="original_expense_facts_unresolved"):
            expenses.assert_new_exposure_funded(db, policy=policy(), now=NOW)


@pytest.mark.parametrize(
    "changes",
    [
        {"amount_minor": True},
        {"availability_basis": "bank_verified"},
        {"external_reference_sha256": "not-a-digest"},
        {"review_valid_until": NOW + timedelta(days=32)},
        {"funded_at": NOW + timedelta(seconds=1)},
        {"funded_at": NOW.replace(tzinfo=None)},
    ],
)
def test_declaration_is_strict_bounded_operator_assertion(changes):
    with pytest.raises(ValueError):
        declaration(**changes)
