"""Refresh exact synthetic input dates in the immutable schema26 test suite."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_CASE = "test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres"
_APPLIED = pytest.StashKey[bool]()
_FINANCIAL_ANCHOR = datetime.now(UTC)
_PHASE_ENV = "HIREWIZ_RETAINED_SCHEMA26_FIXTURE_PHASE"
_FINANCIAL_CASES = {
    "test_unified_synchronous_postgres.py": {
        "test_concurrent_identical_inline_requests_cannot_generate_or_charge_twice",
        "test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres",
        "test_unknown_supplier_hold_survives_personal_claim_route_rollback_on_postgres",
        "test_changed_source_during_native_dispatch_admission_on_postgres",
    },
    "test_unified_browser_submission_postgres.py": {
        "test_parallel_acceptance_reserves_three_once_without_legacy_debit",
        "test_parallel_begin_exports_one_exact_five_lease_and_one_native_claim",
        "test_cancellation_during_native_claim_prevents_export_but_keeps_three_held",
        "test_parallel_preclaim_cancel_releases_three_once_and_never_releases_native_exposure",
    },
    "test_submission_canary_postgres.py": {
        "test_concurrent_exact_application_acceptance_counts_canary_and_reserves_three_once",
    },
}
_PROTOCOL_FINANCIAL_CASES = {
    "test_unified_browser_submission.py": {
        "test_actual_quote_validator_drift_changes_contract_and_refuses_before_quote_or_spend",
        "test_three_credit_quote_is_inert_and_acceptance_never_debits_legacy_wallet",
        "test_no_fallback_to_large_legacy_balance_when_unified_balance_is_insufficient",
        "test_approval_revalidates_quote_owner_expiry_current_inputs_and_terms",
        "test_replaced_draft_keeps_old_quote_but_old_cancel_cannot_touch_new_hold",
        "test_expired_unaccepted_display_never_requotes_until_new_explicit_inspection",
        "test_expired_accepted_display_refunds_once_and_requires_new_application_identity",
        "test_preclaim_cancel_refunds_once_retains_cash_and_same_source_cannot_reprice",
        "test_unknown_native_ack_never_refunds_requotes_or_repeats_send",
        "test_lease_binds_exact_quote_and_wallet_hold_without_worker_dispatch",
        "test_unified_activation_refuses_new_legacy_review_but_accepted_twenty_stays_recoverable",
        "test_accepted_legacy_twenty_settlement_and_refund_do_not_convert_after_pause",
        "test_accepted_unified_history_blocks_legacy_fallback_after_pause",
        "test_new_legacy_approval_for_unified_owner_is_refused_without_hold",
        "test_over_budget_or_disabled_route_creates_no_quote_hold_or_native_call",
        "test_owner_erasure_releases_unstarted_but_retains_unknown_money_detached",
        "test_unknown_owner_erasure_never_turns_possible_send_into_a_refund",
        "test_generic_cancel_projects_refund_and_accepted_package_cannot_be_edited",
        "test_unaccepted_legacy_draft_requires_fresh_five_quote_and_no_twenty_hold",
        "test_synthetic_receipt_validation_charges_three_once_only_for_exact_accepted_exchange",
        "test_public_billing_is_closed_and_does_not_expose_internal_funding_or_credentials",
        "test_expiry_during_real_hold_admission_rolls_back_without_accepting_or_repricing",
        "test_availability_is_only_advisory_and_every_product_gate_remains_required",
    },
    "test_submission_canary.py": {
        "test_actual_quote_approval_binds_canary_to_current_quote_not_application_creation",
        "test_drafts_requotes_and_low_real_traffic_never_open_canary_slot",
        "test_application_canary_refuses_inexact_quote_review_and_owner_binding",
        "test_same_opening_repeated_applications_do_not_inflate_business_denominator",
        "test_genuine_distinct_legacy_acceptances_count_but_manual_and_drafts_do_not",
        "test_accepted_submission_hold_rejects_cross_kind_quote_and_fingerprint_proofs",
    },
}


@pytest.fixture(autouse=True)
def retained_synthetic_financial_inputs(request, monkeypatch):
    """Fresh test data before dependent fixtures; no production clock override."""
    item = request.node
    name = item.originalname or item.name.split("[", 1)[0]
    phase = os.getenv(_PHASE_ENV, "compatibility")
    if phase not in {"compatibility", "protocol"}:
        pytest.fail("Retained synthetic fixture phase changed")
    cases = _FINANCIAL_CASES if phase == "compatibility" else _PROTOCOL_FINANCIAL_CASES
    if name not in cases.get(item.path.name, set()):
        return
    owned_tests = Path(__file__).resolve().parent
    if item.path.resolve() != owned_tests / item.path.name:
        pytest.fail("Retained synthetic financial fixture location changed")
    from backend.tests import test_unified_credits as credits
    from backend.tests import test_unified_execution as inputs

    frozen = datetime(2026, 10, 27, 12, tzinfo=UTC)
    if (Path(inputs.__file__).resolve() != owned_tests / "test_unified_execution.py"
            or Path(credits.__file__).resolve() != owned_tests / "test_unified_credits.py"
            or inputs.NOW != frozen or credits.NOW != frozen):
        pytest.fail("Retained synthetic financial input scope changed")
    # These owned helpers read NOW only to construct synthetic financial,
    # qualified-route/model and budget evidence. Live expiry validators remain stock.
    monkeypatch.setattr(inputs, "NOW", _FINANCIAL_ANCHOR)
    if item.path.name == "test_unified_synchronous_postgres.py":
        defaults = credits.grant.__kwdefaults__
        if (Path(credits.grant.__code__.co_filename).resolve() != owned_tests / "test_unified_credits.py"
                or defaults.get("now") != frozen or defaults.get("expiry") != frozen + timedelta(days=30)):
            pytest.fail("Retained synthetic credit lot defaults changed")
        # Preserve every imported alias of this exact function and all funding facts.
        monkeypatch.setattr(credits.grant, "__kwdefaults__", defaults | {
            "now": _FINANCIAL_ANCHOR, "expiry": _FINANCIAL_ANCHOR + timedelta(days=30)})


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_call(item):
    owned_tests = Path(__file__).resolve().parent
    if item.name != _CASE + "[learning_strategy]" or item.path.resolve() != owned_tests / "test_unified_synchronous_postgres.py":
        return
    fixture = item.module.synchronous_checks
    if (Path(fixture.__file__).resolve() != owned_tests / "test_unified_synchronous.py"
            or fixture.NOW != datetime(2026, 10, 27, 12, tzinfo=UTC)
            or item.config.stash.get(_APPLIED, False)):
        pytest.fail("Retained synthetic learning review fixture scope changed")
    # The frozen fixture uses this alias only for its synthetic reviewed-resource
    # timestamps. Ledger clocks and the actual expiry validator remain live.
    item.funcargs["monkeypatch"].setattr(fixture, "NOW", datetime.now(UTC))
    item.config.stash[_APPLIED] = True


def pytest_sessionfinish(session, exitstatus):
    if (exitstatus == pytest.ExitCode.OK and os.getenv(_PHASE_ENV, "compatibility") == "compatibility"
            and not session.config.stash.get(_APPLIED, False)):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
