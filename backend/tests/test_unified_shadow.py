"""Shadow observation cannot debit a wallet or certify incomplete evidence."""
import json
from datetime import timedelta

import pytest
from backend.app.domains.credits import runtime, service, shadow
from backend.app.models import AnalysisRun
from backend.tests.test_unified_credits import NOW, factory, grant
from backend.tests.test_unified_execution import admitted, budgets, financial_policy, route

assert factory


def config():
    return runtime.RuntimeConfig(policy=financial_policy(), budgets=budgets(), routes=(route(),))


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
