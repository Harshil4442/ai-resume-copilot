"""Commercial policy floors, not invoice/profit or live-release qualification."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.domains.credits import finance, pricing, release, runtime

NOW = datetime.now(UTC)


def financial(**changes):
    return finance.FinancialPolicy(**(dict(version="owned-margin-review", valid_from=NOW-timedelta(hours=1),
        expires_at=NOW+timedelta(days=1), provenance="invoice_reconciled", evidence_sha256="a"*64) | changes))


def payload(*, minimum=1500, target=2000, extra=0):
    return dict(catalog_version=pricing.CATALOG_VERSION,
        policy=financial(minimum_net_margin_bps=minimum, target_net_margin_bps=target).model_dump(mode="json"),
        reviewed_by="owned-test-not-real-expense-review", evidence={name: "b"*64 for name in
            ("quality", "legacy_rights", "commercial_hosting", "invoices", "security", "shadow_accounting", "delivery_routes")},
        shadow_started_at=NOW-timedelta(days=9), shadow_ended_at=NOW-timedelta(days=1),
        shadow_successful_operations=200, monthly_shared_minor=3_500_000,
        monthly_additional_business_minor=extra, planning_paid_packs=2000, funded_runway_minor=10_000_000)


@pytest.mark.parametrize("minimum,target,extra", [(0, 0, 1_000_000), (1499, 2000, 0), (1500, 1999, 0)])
def test_launch_review_cannot_relax_either_approved_margin_floor(minimum, target, extra):
    with pytest.raises(ValueError, match="margin"):
        release.ReleaseReview.model_validate(payload(minimum=minimum, target=target, extra=extra))


@pytest.mark.parametrize("minimum,target", [(1500, 2000), (1501, 2001)])
def test_current_valid_launch_review_accepts_exact_or_stricter_floor(minimum, target, monkeypatch):
    candidate = release.ReleaseReview.model_validate(payload(minimum=minimum, target=target))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON", candidate.model_dump_json())
    assert release.current_review(now=NOW) == candidate


@pytest.mark.parametrize("extra,accepted", [(14_140, True), (14_141, False)])
def test_one_paise_adverse_overhead_boundary_is_still_enforced(extra, accepted):
    if accepted:
        release.ReleaseReview.model_validate(payload(extra=extra))
    else:
        with pytest.raises(ValueError, match="minimum margin"):
            release.ReleaseReview.model_validate(payload(extra=extra))


def test_historical_and_noncommercial_what_if_policy_parsing_is_preserved():
    candidate = financial(minimum_net_margin_bps=0, target_net_margin_bps=0, provenance="estimated")
    historical = finance.FinancialPolicy.model_validate_json(candidate.model_dump_json())
    assert finance.require_current_policy(historical, now=NOW, commercial=False) is historical
    result = finance.forecast(historical, gross_minor=11_800_000, consumed_credits=80_000,
        shared_minor=3_500_000, additional_business_minor=1_000_000)
    assert result["surplus_minor"] == 436_968 and result["minimum_margin_met"] is True


@pytest.mark.parametrize("minimum,target", [(0, 0), (1499, 2000), (1500, 1999)])
def test_commercial_policy_guard_also_denies_direct_execution_without_a_release_review(minimum, target):
    candidate = financial(minimum_net_margin_bps=minimum, target_net_margin_bps=target)
    with pytest.raises(finance.FundingUnavailable, match="margin"):
        finance.require_current_policy(candidate, now=NOW, commercial=True)


@pytest.mark.parametrize("minimum,target", [(0, 0), (1499, 2000), (1500, 1999)])
def test_paid_checkout_stays_closed_for_low_margin_review(minimum, target, monkeypatch):
    import json

    candidate = payload(minimum=minimum, target=target)
    # Use raw configuration rather than constructing a now-invalid review.
    candidate["shadow_started_at"] = candidate["shadow_started_at"].isoformat()
    candidate["shadow_ended_at"] = candidate["shadow_ended_at"].isoformat()
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UNIFIED_CREDITS_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RELEASE_REVIEW_JSON", json.dumps(candidate))
    monkeypatch.setattr(release.RazorpaySettings, "from_env", lambda: SimpleNamespace(checkout_enabled=True))
    assert release.checkout_ready() is False


def execution_config(candidate):
    return runtime.RuntimeConfig(policy=candidate,
        budgets=dict(version="owned-budgets", evidence_sha256="c"*64,
            valid_from=NOW-timedelta(hours=1), expires_at=NOW+timedelta(days=1),
            daily_global_minor=100, monthly_global_minor=100, monthly_account_minor=100,
            monthly_incident_minor=100, monthly_route_minor={"owned-native-route": 100},
            legacy_search_cost_minor=1, legacy_application_cost_minor=1,
            legacy_preparation_minor=1, legacy_provider_attempt_overhead_minor=0),
        routes=(dict(id="owned-native-route", version="owned-v1", operation="market_report",
            tariff_version=pricing.TARIFF_VERSION, credits=pricing.SERVICE_CREDITS["market_report"],
            mode="native", deliverable="Owned synthetic contract only", supported_limits={},
            qualification_id="owned-not-native-qualified", qualification_evidence_sha256="d"*64,
            qualified_from=NOW-timedelta(hours=1), qualified_until=NOW+timedelta(days=1),
            native_cost_evidence_sha256="e"*64, native_processing_minor=1,
            worker_processing_minor=0, model_quotes=()),))


@pytest.mark.parametrize("minimum,target", [(0, 0), (1499, 2000), (1500, 1999)])
def test_production_runtime_rejects_floor_before_canary_audience_or_database(minimum, target, monkeypatch):
    configured = execution_config(financial(minimum_net_margin_bps=minimum, target_net_margin_bps=target))
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", configured.model_dump_json())
    monkeypatch.setattr(runtime, "_audience_enabled", lambda *_args, **_kwargs: pytest.fail("Margin failure reached audience/native/SQL setup"))
    with pytest.raises(finance.FundingUnavailable):
        runtime.current_runtime(1, now=NOW)


def test_valid_commercial_runtime_reaches_separate_audience_guard(monkeypatch):
    configured = execution_config(financial())
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", configured.model_dump_json())
    seen = []
    monkeypatch.setattr(runtime, "_audience_enabled", lambda *_args, **_kwargs: seen.append("separate-audience-review") or True)
    assert runtime.current_runtime(1, now=NOW) == configured
    assert seen == ["separate-audience-review"]
