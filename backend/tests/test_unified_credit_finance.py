from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from backend.app.domains.credits.finance import (
    ExecutionEnvelope,
    FinancialPolicy,
    FundingUnavailable,
    ProviderAttemptEnvelope,
    execution_cost_minor,
    forecast,
    require_current_policy,
    require_execution_allowance,
)
from pydantic import ValidationError

NOW = datetime(2026, 10, 27, tzinfo=UTC)


def policy(**changes):
    values = dict(
        version="test-finance-v1", valid_from=NOW, expires_at=NOW + timedelta(days=7),
        provenance="synthetic_test", evidence_sha256="a" * 64,
    )
    return FinancialPolicy(**(values | changes))


@pytest.mark.parametrize("buyers,expected", [(2000, 2436968), (2250, 3054089), (2500, 3671210)])
def test_requirements_full_redemption_scenarios(buyers, expected):
    report = forecast(policy(), gross_minor=buyers * 5900, consumed_credits=buyers * 40,
                      shared_minor=2_500_000)
    assert report["surplus_minor"] == expected
    assert report["minimum_margin_met"] and report["target_margin_met"]
    assert report["gross_minor"] - report["sales_tax_minor"] == report["revenue_minor"]


def test_adverse_case_meets_minimum_but_not_normal_target():
    report = forecast(policy(), gross_minor=11_800_000, consumed_credits=80_000,
                      shared_minor=3_500_000)
    assert report["surplus_minor"] == 1_436_968
    assert report["surplus_minor"] - report["minimum_surplus_minor"] == 14_140
    assert report["minimum_margin_met"] is True
    assert report["target_margin_met"] is False
    with_extra = forecast(policy(), gross_minor=11_800_000, consumed_credits=80_000,
                          shared_minor=3_500_000, additional_business_minor=1_000_000)
    assert with_extra["surplus_minor"] == 436_968
    assert with_extra["minimum_margin_met"] is False


def test_low_sales_and_no_sales_do_not_claim_profitability():
    report = forecast(policy(), gross_minor=5_900_000, consumed_credits=40_000,
                      shared_minor=3_500_000)
    assert report["surplus_minor"] == -1_031_516
    assert report["minimum_margin_met"] is False
    empty = forecast(policy(), gross_minor=0, consumed_credits=0, shared_minor=0)
    assert empty["minimum_margin_met"] is False


def test_money_uses_exact_fraction_and_rejects_bool_or_float():
    report = forecast(policy(), gross_minor=17900, consumed_credits=130, shared_minor=0)
    assert report["surplus_minor"] == Fraction(104727159, 14750)
    for invalid in [True, -1, 1.5]:
        with pytest.raises(ValueError):
            forecast(policy(), gross_minor=invalid, consumed_credits=0, shared_minor=0)


def test_expired_or_estimated_policy_cannot_enable_commercial_work():
    with pytest.raises(FundingUnavailable):
        require_current_policy(policy(), now=NOW + timedelta(days=7))
    for provenance in ["synthetic_test", "estimated"]:
        with pytest.raises(FundingUnavailable):
            require_current_policy(policy(provenance=provenance), now=NOW, commercial=True)
    require_current_policy(policy(provenance="invoice_reconciled"), now=NOW, commercial=True)
    with pytest.raises(ValidationError):
        policy(expires_at=NOW + timedelta(days=32))
    with pytest.raises(ValidationError):
        policy(valid_from=NOW.replace(tzinfo=None))


def attempt(**changes):
    return ProviderAttemptEnvelope(**(dict(
        provider="synthetic", model="reference", max_input_tokens=10_000,
        max_output_tokens=2_000, input_usd_micros_per_million=750_000,
        output_usd_micros_per_million=3_750_000, rate_evidence_sha256="b" * 64,
    ) | changes))


def test_envelope_funds_all_attempts_reasoning_taxes_and_native_work():
    one = ExecutionEnvelope(attempts=(attempt(),), native_processing_minor=10,
                            worker_processing_minor=5, qualification_id="reviewed-route")
    assert execution_cost_minor(policy(), one) == 192
    assert require_execution_allowance(policy(), one, charge_credits=5, now=NOW) == 192
    two = one.model_copy(update={"attempts": (attempt(), attempt())})
    assert execution_cost_minor(policy(), two) == 369
    with pytest.raises(FundingUnavailable):
        require_execution_allowance(policy(), two, charge_credits=8, now=NOW)
    assert require_execution_allowance(policy(), two, charge_credits=15, now=NOW) == 369


def test_rounding_is_conservative_per_attempt_and_no_zero_credit_paid_work():
    tiny = attempt(max_input_tokens=1, max_output_tokens=1)
    envelope = ExecutionEnvelope(attempts=(tiny, tiny), native_processing_minor=0,
                                 worker_processing_minor=0, qualification_id="test-route")
    assert execution_cost_minor(policy(), envelope) == 2
    with pytest.raises(FundingUnavailable):
        require_execution_allowance(policy(), envelope, charge_credits=0, now=NOW)
