import json
from datetime import UTC, datetime, timedelta
from fractions import Fraction

import pytest
from backend.app.billing.cost_policy import (
    CostPolicyUnavailable,
    ExpensePolicy,
    audit,
    net_receipts,
    service_prices,
)
from backend.tests.expense_policy_fixtures import estimated_expense_policy


def test_full_mixed_workload_has_cost_and_margin_cover():
    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    result = audit(policy)
    assert result["search_floor"] == 1 and result["apply_floor"] == 14
    assert result["analysis_cost_minor_per_unit"] == 2650
    assert [(r["net_receipts_minor"], r["required_net_minor"]) for r in result["packs"]] == [
        (49457, 45600),
        (83749, 76800),
        (167575, 144500),
    ]
    for pack, row in zip(policy.packs, result["packs"], strict=True):
        for applications in range(pack.service_credits // 20 + 1):
            searches = pack.service_credits - applications * 20
            raw = 15000 + pack.analysis_units * 2650 + searches * 25 + applications * 500
            assert row["net_receipts_minor"] >= 2 * raw


@pytest.mark.parametrize(
    "change",
    [
        {"adverse_fx_minor_per_usd": 30000},
        {"monthly_platform_operations_minor": 5000000},
        {"minimum_paid_packs_per_month": 10},
        {"application_cost_minor": 5000},
        {"gateway_fee_bps": 5000},
    ],
)
def test_adverse_expenses_refuse_unsafe_pack(change):
    with pytest.raises(CostPolicyUnavailable):
        audit(ExpensePolicy.model_validate(estimated_expense_policy() | change))


@pytest.mark.parametrize(
    "field,quantity",
    [
        ("max_discount_minor", 40000),
        ("max_bonus_service_credits", 2000),
        ("max_bonus_analysis_units", 20),
    ],
)
def test_promotion_variants_are_in_the_same_floor(field, quantity):
    value = estimated_expense_policy()
    value["packs"][2][field] = quantity
    with pytest.raises(CostPolicyUnavailable):
        audit(ExpensePolicy.model_validate(value))


def test_expenses_round_up_receipts_down():
    policy = ExpensePolicy.model_validate(estimated_expense_policy())
    gross = 64901
    exact = (
        Fraction(gross)
        - Fraction(gross * 1800, 11800)
        - Fraction(gross * 354, 10000)
        - Fraction(gross * 500, 10000)
    )
    assert net_receipts(policy, gross) <= exact


@pytest.mark.parametrize("invalid", ["missing", "expired", "overdue", "invalid", "catalog"])
def test_stale_or_unreviewed_policy_unavailable(monkeypatch, invalid):
    value = estimated_expense_policy()
    now = datetime.now(UTC)
    if invalid == "missing":
        monkeypatch.delenv("HIREWIZ_EXPENSE_POLICY_JSON")
    elif invalid == "invalid":
        monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", '{"secret":"must-not-escape"}')
    else:
        if invalid == "expired":
            value.update(
                valid_from=(now - timedelta(days=2)).isoformat(),
                expires_at=(now - timedelta(days=1)).isoformat(),
                reconciled_through=(now - timedelta(days=3)).isoformat(),
            )
        elif invalid == "overdue":
            value["reconciled_through"] = (now - timedelta(days=40)).isoformat()
        else:
            value["catalog_version"] = "wrong"
        monkeypatch.setenv("HIREWIZ_EXPENSE_POLICY_JSON", json.dumps(value))
    with pytest.raises(CostPolicyUnavailable) as error:
        service_prices()
    assert "secret" not in str(error.value)
