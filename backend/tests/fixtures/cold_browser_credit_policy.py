"""Synthetic test-only funding, never production release or invoice evidence."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta


def environment() -> dict[str, str]:
    now = datetime.now(UTC)
    dates = {"valid_from": (now-timedelta(hours=1)).isoformat(), "expires_at": (now+timedelta(days=1)).isoformat()}
    financial = {"version": "cold-synthetic-finance", "provenance": "synthetic_test", "evidence_sha256": "a"*64, **dates}
    operations = {name: {"route_id": f"cold.free.{name}", "reserved_minor": 20, "max_input_bytes": 8*1024*1024,
        "max_source_records": 1000, "max_attempts": 1, "per_account_30_days": limit}
        for name, limit in (("resume_parse", 1), ("basic_match", 10), ("curated_interview", 5), ("skill_roi", 5))}
    budgets = {"version": "cold-synthetic-budget", "evidence_sha256": "b"*64, **dates,
        "daily_global_minor": 100_000, "monthly_global_minor": 100_000, "monthly_account_minor": 50_000,
        "monthly_incident_minor": 50_000, "monthly_route_minor": {"cold.native.discovery": 50_000,
            **{row["route_id"]: 50_000 for row in operations.values()}},
        "legacy_search_cost_minor": 10, "legacy_application_cost_minor": 30,
        "legacy_preparation_minor": 10, "legacy_provider_attempt_overhead_minor": 2}
    route = {"id": "cold.native.discovery", "version": "synthetic-v1", "operation": "job_discovery_batch",
        "tariff_version": "credit-services-2026-10-27-v1", "credits": 1, "mode": "native",
        "deliverable": "Up to five new verified employer jobs; a completed nonempty batch costs one credit.",
        "supported_limits": {"context_bytes": 32768, "candidate_limit": 1000, "candidate_content_bytes": 2_000_000, "maximum_results": 5},
        "supported_source_formats": ["docx"], "qualification_id": "synthetic-cold-browser-only",
        "qualification_evidence_sha256": "c"*64, "qualified_from": dates["valid_from"], "qualified_until": dates["expires_at"],
        "native_cost_evidence_sha256": "d"*64, "native_processing_minor": 5, "worker_processing_minor": 8, "model_quotes": []}
    review = {"catalog_version": "inr-unified-2026-10-27-v1", "policy": financial, "reviewed_by": "synthetic-local-browser-only",
        "evidence": {name: "e"*64 for name in ("quality", "legacy_rights", "commercial_hosting", "invoices", "security", "shadow_accounting", "delivery_routes")},
        "shadow_started_at": (now-timedelta(days=8)).isoformat(), "shadow_ended_at": (now-timedelta(days=1)).isoformat(),
        "shadow_successful_operations": 200, "monthly_shared_minor": 3_500_000, "monthly_additional_business_minor": 0,
        "planning_paid_packs": 2000, "funded_runway_minor": 3_500_000}
    free = {"version": "cold-synthetic-free", "provenance": "synthetic_test", "funding_period_id": "cold-synthetic-period",
        "evidence_sha256": "f"*64, **dates, "shared_period_funded_minor": 10_000, "free_allocation_minor": 10_000,
        "global_30_day_operations": 1000, "financial_policy": financial, "execution_budgets": budgets, "operations": operations}
    return {"UNIFIED_CREDITS_ENABLED": "true", "UNIFIED_CREDIT_EXECUTION_ENABLED": "true",
        "UNIFIED_CREDIT_EXECUTION_AUDIENCE": "canary", "UNIFIED_CREDIT_CANARY_OWNER_IDS": "2",
        "UNIFIED_CREDIT_RELEASE_REVIEW_JSON": json.dumps(review),
        "UNIFIED_CREDIT_RUNTIME_REVIEW_JSON": json.dumps({"policy": financial, "budgets": budgets, "routes": [route]}),
        "FREE_CORE_ENABLED": "true", "FREE_CORE_POLICY_JSON": json.dumps(free), "EMPLOYER_PAID_HISTORY_MODE": "indexed"}
