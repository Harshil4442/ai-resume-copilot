"""Explicit synthetic fixtures; estimated candidate is never auto-activated."""

from datetime import UTC, datetime, timedelta


def synthetic_expense_policy():
    now = datetime.now(UTC)
    from backend.tests.model_cost_fixtures import synthetic_policy

    return {
        "version": "synthetic-expense-v1",
        "catalog_version": "inr-finite-2026-10-09-v3",
        "provenance": "synthetic_test",
        "reviewed_by": "automated-test-fixture",
        "review_status": "approved",
        "evidence_references": ["synthetic-only-never-deploy"],
        "valid_from": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "reconciled_through": (now - timedelta(hours=1)).isoformat(),
        "actual_variance_reviewed": True,
        "currency": "INR",
        "margin_bps": 4000,
        "contingency_bps": 2000,
        "sale_tax_reserve_bps": 1800,
        "gateway_fee_bps": 300,
        "gateway_fee_tax_bps": 1800,
        "gateway_fixed_minor": 0,
        "settlement_fx_minor": 0,
        "refund_chargeback_reserve_bps": 500,
        "all_enabled_payment_methods_covered": True,
        "adverse_fx_minor_per_usd": 1,
        "monthly_platform_operations_minor": 100,
        "monthly_marketing_operations_minor": 10000,
        "minimum_paid_packs_per_month": 100,
        "search_cost_minor": 1,
        "service_preparation_cost_minor": 1,
        "application_cost_minor": 5,
        "analysis_overhead_minor_per_unit": 0,
        "provider_attempt_overhead_minor": 0,
        "analysis_unit_model_ceiling_micros": 10_000_000,
        "operation_model_ceilings_micros": synthetic_policy()["operation_limits_micros"],
        "daily_model_budget_micros": 1_000_000_000,
        "monthly_model_budget_micros": 1_000_000_000,
        "monthly_promotional_model_budget_micros": 500_000_000,
        "monthly_legacy_premium_model_budget_micros": 500_000_000,
        "monthly_legacy_service_budget_minor": 5000,
        "service_search_credits": 1,
        "service_apply_credits": 5,
        "packs": [
            {"sku": sku, "gross_minor": price, "service_credits": credits, "analysis_units": units}
            for sku, price, credits, units in [
                ("starter_bundle", 64900, 100, 2),
                ("growth_bundle", 109900, 300, 6),
                ("scale_bundle", 219900, 700, 15),
            ]
        ],
    }


def estimated_expense_policy():
    value = synthetic_expense_policy()
    value.update(
        version="estimated-india-2026-10-09-v1",
        provenance="estimated",
        reviewed_by="operator-review-required",
        review_status="candidate",
        evidence_references=["cost-pricing-policy-2026-10-09", "operator-estimates-not-invoices"],
        adverse_fx_minor_per_usd=10000,
        monthly_platform_operations_minor=1300000,
        monthly_marketing_operations_minor=200000,
        search_cost_minor=25,
        service_preparation_cost_minor=50,
        application_cost_minor=500,
        analysis_overhead_minor_per_unit=150,
        provider_attempt_overhead_minor=50,
        analysis_unit_model_ceiling_micros=250000,
        daily_model_budget_micros=5_000_000,
        monthly_model_budget_micros=70_000_000,
        monthly_promotional_model_budget_micros=10_000_000,
        monthly_legacy_premium_model_budget_micros=5_000_000,
        monthly_legacy_service_budget_minor=50000,
        service_apply_credits=20,
    )
    value["operation_model_ceilings_micros"] = {
        k: 500000 if "tailor" in k else 250000 for k in value["operation_model_ceilings_micros"]
    }
    return value
