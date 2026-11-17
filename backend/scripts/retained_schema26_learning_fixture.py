"""Refresh one synthetic review in the immutable schema26 compatibility suite."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

_CASE = "test_unsupported_personal_claim_refunds_once_with_supplier_cost_retained_on_postgres"
_APPLIED = pytest.StashKey[bool]()


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
