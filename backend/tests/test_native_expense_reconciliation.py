"""Synthetic receipts prove accounting, never real invoice qualification."""
from datetime import timedelta

import pytest
from backend.app.domains.credits import admission, execution, finance, reconciliation, service
from backend.app.domains.credits.models import CreditExecutionHold
from backend.app.domains.credits.privacy import erase_owner_credit_links
from backend.app.domains.employer.testing_promotion import Operator
from backend.app.domains.free_core.models import FreeCoreRequest, FreeCoreReservation
from backend.app.models import AdminAuditEvent, ModelCostLiability, User
from backend.tests.test_unified_credits import NOW, factory, grant
from backend.tests.test_unified_execution import admitted, claim, route

assert factory
STAMP = NOW + timedelta(minutes=3)
ACTOR = Operator("operator@example.invalid", "f" * 64)


def receipt(identity, *, kind="paid_native", amount=9, work_state="completed", **changes):
    return reconciliation.NativeExpenseReceipt.model_validate({
        "kind": kind, "target_id": identity, "provenance": "measured_usage",
        "actual_minor": amount, "source_sha256": "a" * 64, "completion_sha256": "b" * 64,
        "allocation": "direct_operation_metering", "work_state": work_state,
        "window_start": NOW - timedelta(minutes=1), "window_end": NOW + timedelta(minutes=1),
        "terminal_at": NOW + timedelta(seconds=30), "reviewed_at": NOW + timedelta(minutes=2),
    } | changes)


def paid(db, *, native=True):
    grant(db)
    hold = admitted(db, selected=route(native=native))
    binding = None
    if not native:
        binding, _ = claim(db, hold)
        execution.finish_attempt(db, binding=binding, error=TimeoutError(), now=NOW)
    execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=native, now=NOW)
    db.commit()
    return hold, binding


def free(db):
    reservation = FreeCoreReservation(id="fcr_synthetic", user_id=1,
        operation="resume_parse", route_id="synthetic-free", policy_version="synthetic-v1",
        funding_period_id="synthetic-month", reserved_minor=10, state="reserved", created_at=NOW)
    db.add(reservation)
    db.flush()
    db.add(FreeCoreRequest(id="fcq_synthetic", user_id=1, source_type="resume_upload",
        source_id="synthetic-upload", idempotency_key="synthetic-free-request",
        input_fingerprint="d" * 64, reservation_id=reservation.id,
        result_id="synthetic-result", state="completed"))
    db.commit()
    return reservation


def terminal(db, native_receipt, **changes):
    row = db.get(CreditExecutionHold if native_receipt.kind == "paid_native" else FreeCoreReservation,
                 native_receipt.target_id)
    return reconciliation.TerminalWorkEvidence.model_validate({
        "provenance": "operator_reviewed_native_execution", "kind": native_receipt.kind,
        "target_id": native_receipt.target_id, "financial_group_id": (
            row.financial_group_id if native_receipt.kind == "paid_native" else None),
        "reservation_id": row.reservation_id if native_receipt.kind == "paid_native" else row.id,
        "execution_readback_sha256": ["d" * 64], "work_state": native_receipt.work_state,
        "all_native_attempts_terminal": True, "no_future_native_work": True,
        "terminal_at": native_receipt.terminal_at, "observed_at": native_receipt.reviewed_at,
    } | changes)


def plan(db, native_receipt, *, nonce="c" * 32):
    return reconciliation.capture(db, receipt=native_receipt, terminal=terminal(db, native_receipt),
                                  actor=ACTOR, nonce=nonce, now=STAMP)


def test_capture_and_dry_run_are_read_only_and_exact_repeat_is_idempotent(factory):
    with factory() as db:
        hold, _ = paid(db)
        reviewed = plan(db, receipt(hold.id))
        before = service.wallet(db, user_id=1, now=NOW).available_credits
        assert reconciliation.dry_run(db, plan=reviewed, now=STAMP)["status"] == "ready_for_operator_review"
        assert not db.new and not db.dirty and not db.deleted
        assert hold.native_settled_minor is None
        assert reconciliation.apply(db, plan=reviewed, now=STAMP)["status"] == "native_expense_reconciled"
        db.commit()
        assert reconciliation.apply(db, plan=reviewed, now=STAMP)["status"] == "already_reconciled"
        db.commit()
        assert db.query(AdminAuditEvent).count() == 1
        assert hold.state == "settled" and hold.native_settled_minor == hold.settled_minor == 9
        assert service.wallet(db, user_id=1, now=NOW).available_credits == before
        assert db.query(AdminAuditEvent).one().after_state["invoice_qualification_granted"] is False
        assert reconciliation.dry_run(db, plan=reviewed, now=STAMP + timedelta(days=2))["status"] == "already_reconciled"


def test_detached_native_expense_can_settle_but_unknown_provider_cost_remains_held(factory):
    with factory() as db:
        hold, binding = paid(db, native=False)
        reviewed = plan(db, receipt(hold.id, work_state="failed"))
        erase_owner_credit_links(db, user_id=1)
        db.delete(db.get(User, 1))
        db.commit()
        assert hold.user_id is None
        reconciliation.apply(db, plan=reviewed, now=STAMP)
        db.commit()
        assert hold.native_settled_minor == 9 and hold.state == "outcome_unknown"
        assert db.get(ModelCostLiability, binding.liability_id).cost_state == "outcome_unknown"
        assert admission._hold_usage(db, start=NOW + timedelta(days=40)) == hold.reserved_minor


def test_free_receipt_settles_once_and_survives_owner_erasure(factory):
    with factory() as db:
        row = free(db)
        reviewed = plan(db, receipt(row.id, kind="free_core", amount=8))
        db.delete(db.get(User, 1))
        # SQLite doesn't enforce FK erasure; model the same production unlink.
        db.query(FreeCoreRequest).delete()
        row.user_id = None
        db.commit()
        reconciliation.apply(db, plan=reviewed, now=STAMP)
        db.commit()
        assert row.state == "settled" and row.settled_minor == 8
        assert row.settlement_receipt_sha256 == "a" * 64
        assert reconciliation.apply(db, plan=reviewed, now=STAMP)["status"] == "already_reconciled"


@pytest.mark.parametrize("changes", [
    {"actual_minor": 0}, {"actual_minor": -1}, {"actual_minor": True},
    {"provenance": "estimated"}, {"work_state": "unknown"}, {"work_state": "running"},
    {"window_end": NOW - timedelta(days=1)}, {"reviewed_at": NOW.replace(tzinfo=None)},
    {"allocation": "reviewed_invoice_allocation"},
])
def test_unknown_estimated_zero_or_incomplete_receipt_is_refused(changes):
    with pytest.raises(ValueError):
        receipt("synthetic", **changes)


def test_changed_receipt_or_preimage_or_database_or_expired_plan_refuses(factory):
    with factory() as db:
        hold, _ = paid(db)
        reviewed = plan(db, receipt(hold.id))
        for changed in (
            reviewed.model_copy(update={"database_sha256": "e" * 64}),
            reviewed.model_copy(update={"target_sha256": "e" * 64}),
        ):
            with pytest.raises(reconciliation.ReconciliationDenied):
                reconciliation.apply(db, plan=changed, now=STAMP)
        with pytest.raises(reconciliation.ReconciliationDenied, match="expired"):
            reconciliation.apply(db, plan=reviewed, now=STAMP + timedelta(days=2))
        reconciliation.apply(db, plan=reviewed, now=STAMP)
        db.commit()
        for changes in ({"source_sha256": "e" * 64}, {"actual_minor": 10}):
            conflicting = reviewed.model_copy(update={"receipt": receipt(hold.id, **changes)})
            with pytest.raises(reconciliation.ReconciliationDenied, match="conflict"):
                reconciliation.apply(db, plan=conflicting, now=STAMP)
        assert hold.native_settled_minor == 9 and db.query(AdminAuditEvent).count() == 1


def test_unfinished_work_and_out_of_window_are_refused(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db, selected=route(native=True))
        with pytest.raises(reconciliation.ReconciliationDenied, match="not_terminal"):
            plan(db, receipt(hold.id))
        execution.settle_delivery(db, user_id=1, hold_id=hold.id, delivered=True, now=NOW)
        with pytest.raises(reconciliation.ReconciliationDenied, match="window"):
            plan(db, receipt(hold.id, window_start=NOW + timedelta(seconds=1)))
        assert hold.native_settled_minor is None and db.query(AdminAuditEvent).count() == 0


@pytest.mark.parametrize("changes", [
    {"all_native_attempts_terminal": False}, {"all_native_attempts_terminal": 1},
    {"no_future_native_work": False}, {"work_state": "running"},
])
def test_nonterminal_or_false_execution_claim_is_refused(factory, changes):
    with factory() as db:
        hold, _ = paid(db)
        with pytest.raises(ValueError):
            terminal(db, receipt(hold.id), **changes)
        assert hold.native_settled_minor is None


def test_wrong_native_execution_identity_is_not_a_terminal_proof(factory):
    with factory() as db:
        hold, _ = paid(db)
        bill = receipt(hold.id)
        other = terminal(db, bill, financial_group_id="another-operation")
        with pytest.raises(reconciliation.ReconciliationDenied, match="correlation"):
            reconciliation.capture(db, receipt=bill, terminal=other,
                                   actor=ACTOR, nonce="c" * 32, now=STAMP)
        assert hold.native_settled_minor is None


def test_free_request_still_admitted_cannot_be_settled_by_a_completion_claim(factory):
    with factory() as db:
        row = free(db)
        request = db.query(FreeCoreRequest).one()
        request.state, request.result_id = "admitted", None
        db.commit()
        with pytest.raises(reconciliation.ReconciliationDenied, match="not_terminal"):
            plan(db, receipt(row.id, kind="free_core"))
        assert row.settled_minor is None and db.query(AdminAuditEvent).count() == 0


def test_overrun_is_recorded_and_pauses_new_route_without_increasing_customer_charge(factory):
    with factory() as db:
        hold, _ = paid(db)
        reviewed = plan(db, receipt(hold.id, amount=30))
        reconciliation.apply(db, plan=reviewed, now=STAMP)
        db.commit()
        assert hold.state == "overrun" and hold.native_settled_minor == 30
        assert service.wallet(db, user_id=1, now=NOW).available_credits == 39
        with pytest.raises(finance.FundingUnavailable, match="overrun"):
            admitted(db, source="new-work", selected=route(native=True))
        db.rollback()
        assert db.query(CreditExecutionHold).count() == 1


def test_transaction_rollback_removes_both_settlement_and_audit(factory):
    with factory() as db:
        hold, _ = paid(db)
        reviewed = plan(db, receipt(hold.id))
        reconciliation.apply(db, plan=reviewed, now=STAMP)
        db.rollback()
        assert db.get(CreditExecutionHold, hold.id).native_settled_minor is None
        assert db.query(AdminAuditEvent).count() == 0
