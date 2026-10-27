"""Old entitlements cannot evade the shared paid/free monetary ceiling."""
import json
from datetime import UTC, datetime

import pytest
from backend.app.billing import cost_policy
from backend.app.domains.credits import admission, legacy_funding, runtime
from backend.app.domains.credits.finance import FundingUnavailable
from backend.app.domains.free_core import service as free_service
from backend.tests.test_unified_credits import factory, grant
from test_free_core import synthetic_free_policy
from test_unified_execution import admitted, budgets, financial_policy, route

assert factory


def test_new_paid_hold_blocks_legacy_model_and_service_expense(factory, monkeypatch):
    with factory() as db:
        grant(db)
        admitted(db)
        db.commit()
        held = admission._hold_usage(db, start=datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0))
        assert held > 0
        config = runtime.RuntimeConfig(policy=financial_policy(),
            budgets=budgets(daily_global_minor=held), routes=(route(),))
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", config.model_dump_json())
        with pytest.raises(cost_policy.CostPolicyUnavailable, match="common execution budget"):
            cost_policy.assert_model_funding(db, operation="job_match", operation_ceiling=1,
                reservation=1, funding="legacy")
        with pytest.raises(cost_policy.CostPolicyUnavailable, match="common execution budget"):
            cost_policy.service_expense_holds(db, cost_policy.current_policy(), operation="job_search",
                unit_price=1, count=1, user_id=1)


def test_funded_free_work_blocks_legacy_spend_even_without_paid_rollout(factory, monkeypatch):
    values = synthetic_free_policy()
    values["execution_budgets"]["daily_global_minor"] = 20
    monkeypatch.setenv("FREE_CORE_ENABLED", "true")
    monkeypatch.setenv("FREE_CORE_POLICY_JSON", json.dumps(values))
    with factory() as db:
        free_service.admit(db, user_id=1, operation="basic_match", source_type="synthetic",
            source_id="free-before-legacy", key="free-before-legacy", fingerprint="a" * 64,
            input_bytes=100, attempts=3)
        db.commit()
        with pytest.raises(FundingUnavailable, match="common execution budget"):
            legacy_funding.assert_legacy_headroom(db, native_minor=1)
        with pytest.raises(cost_policy.CostPolicyUnavailable, match="common execution budget"):
            cost_policy.assert_model_funding(db, operation="job_match", operation_ceiling=1,
                reservation=1, funding="legacy")


def test_legacy_guard_does_not_change_old_behavior_while_rollout_off(factory, monkeypatch):
    monkeypatch.delenv("FREE_CORE_ENABLED", raising=False)
    monkeypatch.delenv("UNIFIED_CREDIT_EXECUTION_ENABLED", raising=False)
    with factory() as db:
        legacy_funding.assert_legacy_headroom(db, native_minor=10**9)
