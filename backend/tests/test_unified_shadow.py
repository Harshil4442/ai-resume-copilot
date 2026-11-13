"""Shadow observation cannot debit a wallet or certify incomplete evidence."""
import json
import sys
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from backend.app.domains.credits import finance, runtime, service, shadow
from backend.app.domains.employer.models import ServiceCreditReservation
from backend.app.models import AnalysisRun, ModelCostLiability
from backend.tests.test_unified_credits import NOW, factory, grant
from backend.tests.test_unified_execution import admitted, budgets, financial_policy, route
from pydantic import ValidationError

from scripts import credit_shadow_report

assert factory


def config():
    return runtime.RuntimeConfig(policy=financial_policy(), budgets=budgets(), routes=(route(),))


def reporting_review(now=NOW):
    dates = {"valid_from": now - timedelta(days=1), "expires_at": now + timedelta(days=1)}
    return shadow.ReportingReview(
        schema_version="credit-shadow-reporting-review-v1",
        policy=financial_policy().model_copy(update=dates | {"provenance": "estimated"}),
        costs=shadow.ReportingCosts.model_validate(
            budgets().model_dump(include=set(shadow.ReportingCosts.model_fields)) | dates
        ),
    )


def test_shadow_aggregates_without_private_content_charge_or_false_qualification(factory):
    with factory() as db:
        grant(db)
        admitted(db)
        for identity, mode in (("shadow-basic", "basic"), ("shadow-enhanced", "enhanced")):
            db.add(AnalysisRun(id=identity, user_id=1, operation="job_match", status="succeeded",
                idempotency_key=identity, input_fingerprint="a" * 64,
                input_payload={"mode": mode, "resume": "private-resume@example.test"},
                result_payload={"personal_answer": "private-output-must-not-appear"}, created_at=NOW))
        db.commit()
        before = service.wallet(db, user_id=1, now=NOW).available_credits
        result = shadow.report(db, config=config(), start=NOW-timedelta(hours=1),
            end=NOW+timedelta(minutes=30), now=NOW+timedelta(hours=1))
        assert result["deterministic_or_curated_runs"] == 1
        assert result["services"] == [{"operation": "enhanced_job_match",
            "recorded_status_counts": {"succeeded": 1}, "hypothetical_success_credits": 3,
            "quality_verified_successes": None}]
        assert result["release_qualified"] is False
        assert result["accepted_reserved_customer_credits"] == 8
        assert result["protected_execution_exposure_inr_minor"]["month"] > 0
        assert "private-" not in json.dumps(result)
        assert service.wallet(db, user_id=1, now=NOW).available_credits == before
        assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize("start,end", [(NOW, NOW),
    (NOW-timedelta(days=32), NOW), (NOW, NOW+timedelta(days=1)),
    (NOW.replace(tzinfo=None)-timedelta(hours=1), NOW)])
def test_shadow_rejects_unbounded_future_or_ambiguous_windows(factory, start, end):
    with factory() as db, pytest.raises(ValueError):
        shadow.report(db, config=config(), start=start, end=end, now=NOW)


def test_reporting_review_preserves_output_and_all_retained_liabilities(factory):
    with factory() as db:
        grant(db)
        hold = admitted(db)
        for identity, state, created, reserved, settled in (
            ("prior-unknown", "outcome_unknown", NOW - timedelta(days=40), 100_000, None),
            ("prior-settled", "settled", NOW - timedelta(days=40), 20_000, 10_000),
            ("current-settled", "settled", NOW, 80_000, 30_000),
        ):
            db.add(ModelCostLiability(
                id=identity, financial_group_id=identity, attempt_number=1,
                provider="synthetic", model="synthetic", endpoint_key="a" * 64,
                pricing_quote={}, input_token_estimate=100, reserved_cost_micros=reserved,
                settled_cost_micros=settled, cost_state=state, created_at=created,
            ))
        db.add(ServiceCreditReservation(
            id="old-search", user_id=None, operation="job_search", source_id="old-search",
            requested_count=3, unit_price=1, reserved_amount=3, pricing_version="legacy-test",
            state="reserved", created_at=NOW - timedelta(days=40),
        ))
        db.commit()
        window = {"start": NOW - timedelta(hours=1), "end": NOW + timedelta(minutes=30),
                  "now": NOW + timedelta(hours=1)}
        observed = shadow.report(db, config=reporting_review(), **window)
        assert observed == shadow.report(db, config=config(), **window)
        states = {row["state"]: row for row in observed["all_retained_provider_liabilities"]}
        assert states["outcome_unknown"] == {
            "state": "outcome_unknown", "attempts": 1, "reserved_usd_micros": 100_000,
            "recorded_settled_usd_micros": 0,
        }
        assert states["settled"] == {
            "state": "settled", "attempts": 2, "reserved_usd_micros": 100_000,
            "recorded_settled_usd_micros": 40_000,
        }
        # Day/month exposure includes the old unknown and new settled amounts,
        # both attempts' overhead, and the old detached reserved search. The
        # still-held unified liability is counted once through its whole hold.
        expected = hold.reserved_minor + 1_534 + 4 + 30
        assert observed["protected_execution_exposure_inr_minor"] == {"day": expected, "month": expected}
        assert observed["accepted_reserved_customer_credits"] == 8
        assert observed["release_qualified"] is False
        assert not db.new and not db.dirty and not db.deleted


def test_reporting_review_is_closed_and_cannot_supply_runtime_authority(monkeypatch):
    review = reporting_review()
    raw = review.model_dump(mode="json")
    with pytest.raises(ValidationError):
        shadow.ReportingReview.model_validate(raw | {"routes": []})
    with pytest.raises(ValidationError):
        shadow.ReportingReview.model_validate(raw | {"costs": raw["costs"] | {"legacy_search_cost_minor": True}})
    with pytest.raises(ValidationError):
        runtime.RuntimeConfig.model_validate(raw)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", review.model_dump_json())
    with pytest.raises(finance.FundingUnavailable):
        runtime.current_runtime(1, now=NOW)


def test_shadow_serializes_postgres_numeric_liability_sums_as_exact_integers(factory, monkeypatch):
    amount = 9_007_199_254_740_993
    with factory() as db:
        query = db.query

        def postgres_sums(*columns):
            if columns[0] is ModelCostLiability.cost_state:
                return SimpleNamespace(group_by=lambda *_: SimpleNamespace(
                    all=lambda: [("settled", 2, Decimal(amount), Decimal(15))]
                ))
            return query(*columns)

        monkeypatch.setattr(db, "query", postgres_sums)
        result = shadow.report(db, config=reporting_review(), start=NOW - timedelta(hours=1),
                               end=NOW, now=NOW)
        liability, = json.loads(json.dumps(result))["all_retained_provider_liabilities"]
        assert liability == {"state": "settled", "attempts": 2, "reserved_usd_micros": amount,
                             "recorded_settled_usd_micros": 15}


@pytest.mark.parametrize("component", ["policy", "costs"])
def test_reporting_review_requires_current_assumptions_before_querying(component):
    review = reporting_review()
    expired = getattr(review, component).model_copy(update={"expires_at": NOW})
    with pytest.raises(ValueError, match="current review"):
        shadow.report(None, config=review.model_copy(update={component: expired}),
                      start=NOW - timedelta(hours=1), end=NOW, now=NOW)


def test_cli_accepts_reporting_review_without_routes(factory, tmp_path, monkeypatch, capsys):
    now = datetime.now(UTC)
    path = tmp_path / "reporting-review.json"
    path.write_text(reporting_review(now).model_dump_json())
    monkeypatch.setattr(credit_shadow_report, "SessionLocal", factory)
    monkeypatch.setattr(sys, "argv", ["credit_shadow_report.py", "--reporting-review", str(path),
        "--start", (now - timedelta(days=1)).isoformat(), "--end", now.isoformat()])
    assert credit_shadow_report.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["schema_version"] == "credit-shadow-observation-v1"
    assert result["funding_policy_version"] == reporting_review(now).costs.version
    assert result["release_qualified"] is False
