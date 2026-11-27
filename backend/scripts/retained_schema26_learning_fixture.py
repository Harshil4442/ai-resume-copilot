"""Refresh exact synthetic input dates in the immutable schema26 test suite."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_CASE = "test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres"
_APPLIED = pytest.StashKey[bool]()
_FINANCIAL_ANCHOR = datetime.now(UTC)
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


@pytest.fixture(autouse=True)
def retained_synthetic_financial_inputs(request, monkeypatch):
    """Fresh test data before dependent fixtures; no production clock override."""
    item = request.node
    name = item.originalname or item.name.split("[", 1)[0]
    if name not in _FINANCIAL_CASES.get(item.path.name, set()):
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
    if exitstatus == pytest.ExitCode.OK and not session.config.stash.get(_APPLIED, False):
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
