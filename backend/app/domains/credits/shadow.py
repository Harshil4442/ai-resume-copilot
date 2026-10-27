"""Read-only tariff observation. A report is evidence, never release permission.

Only aggregate amounts and operation labels leave this module. Source text,
answers, owner identities, keys and provider response bodies are never exported.
Recorded success is not proof of deliverable quality or invoice reconciliation.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func

from ...models import AnalysisRun, ModelCostLiability
from ..employer.models import EmployerSearch
from . import admission, pricing
from .discovery_models import CreditDiscoveryBatch
from .models import CreditExecutionHold, CreditGrant, CreditReservation
from .runtime import RuntimeConfig


def _service(operation: str, mode: str | None, scope: str | None) -> str | None:
    if operation.startswith("sync_"):
        name = operation.removeprefix("sync_")
        return name if name in pricing.SERVICE_CREDITS else None
    if operation == "job_match":
        return "enhanced_job_match" if mode == "enhanced" else None
    if operation == "interview_questions":
        return operation if mode == "enhanced" else None
    if operation == "resume_tailor":
        return "resume_tailor_extended" if scope == "extended" else "resume_tailor"
    if operation in {"match_question", "learning_strategy"} and mode != "enhanced":
        return None
    return {"rewrite_bullets": "bullet_rewrite", "match_question": "enhanced_qa",
        "resume_enrichment": "resume_enrichment", "learning_strategy": "learning_strategy",
        "market_analysis": "market_report"}.get(operation)


def report(db, *, config: RuntimeConfig, start: datetime, end: datetime,
           now: datetime | None = None) -> dict:
    stamp = now or datetime.now(UTC)
    if any(value.tzinfo is None for value in (start, end, stamp)):
        raise ValueError("Observation times must include their timezone")
    if not start < end <= stamp or end - start > timedelta(days=31):
        raise ValueError("Choose a completed observation window no longer than 31 days")
    # SQL projects mode/scope only, never the complete private input payload.
    mode = AnalysisRun.input_payload["mode"].as_string()
    scope = AnalysisRun.input_payload["tailoring_scope"].as_string()
    counts = db.query(AnalysisRun.operation, mode, scope, AnalysisRun.status,
        func.count(AnalysisRun.id)).filter(AnalysisRun.created_at >= start,
        AnalysisRun.created_at < end).group_by(AnalysisRun.operation, mode, scope, AnalysisRun.status).all()
    services: dict[str, dict] = {}
    unclassified = deterministic = 0
    for operation, input_mode, input_scope, status, count in counts:
        target = _service(operation, input_mode, input_scope)
        if target is None:
            if operation in {"job_match", "interview_questions", "skill_roi", "match_question", "learning_strategy"}:
                deterministic += count
            else:
                unclassified += count
            continue
        row = services.setdefault(target, {"operation": target, "recorded_status_counts": {},
            "hypothetical_success_credits": 0, "quality_verified_successes": None})
        row["recorded_status_counts"][status] = row["recorded_status_counts"].get(status, 0) + count
        if status == "succeeded":
            row["hypothetical_success_credits"] += count * pricing.SERVICE_CREDITS[target]
    discovery = [{"status": state, "batches": count, "delivered_jobs": jobs,
        "recorded_charged_credits": charged, "recorded_refunded_credits": refunded}
        for state, count, jobs, charged, refunded in db.query(EmployerSearch.status,
            func.count(CreditDiscoveryBatch.search_id), func.sum(EmployerSearch.delivered_count),
            func.sum(EmployerSearch.charged_credits), func.sum(EmployerSearch.refunded_credits)).join(
                CreditDiscoveryBatch, (CreditDiscoveryBatch.search_id == EmployerSearch.id)
                & (CreditDiscoveryBatch.user_id == EmployerSearch.user_id)).filter(
                    CreditDiscoveryBatch.created_at >= start, CreditDiscoveryBatch.created_at < end).group_by(
                        EmployerSearch.status).all()]
    # Detached liabilities include erased accounts and unknown work from earlier
    # periods. Settled and protected amounts stay distinct and cannot be netted.
    provider_rows = db.query(ModelCostLiability.cost_state,
        func.count(ModelCostLiability.id),
        func.coalesce(func.sum(ModelCostLiability.reserved_cost_micros), 0),
        func.coalesce(func.sum(ModelCostLiability.settled_cost_micros), 0)).group_by(ModelCostLiability.cost_state).all()
    provider = [{"state": state, "attempts": count, "reserved_usd_micros": held,
        "recorded_settled_usd_micros": settled} for state, count, held, settled in provider_rows]
    holds = db.query(CreditExecutionHold.delivery_state, CreditExecutionHold.state,
        func.count(CreditExecutionHold.id)).group_by(CreditExecutionHold.delivery_state, CreditExecutionHold.state).all()
    live_credits = db.query(func.coalesce(func.sum(CreditGrant.remaining_credits), 0)).filter(
        (CreditGrant.expires_at.is_(None)) | (CreditGrant.expires_at > stamp)).scalar()
    reserved_credits = db.query(func.coalesce(func.sum(CreditReservation.reserved_credits), 0)).filter_by(state="reserved").scalar()
    day = stamp.replace(hour=0, minute=0, second=0, microsecond=0)
    exposure = {}
    for label, since in (("day", day), ("month", day.replace(day=1))):
        exposure[label] = admission._hold_usage(db, start=since) + admission._legacy_usage(
            db, start=since, policy=config.policy, budgets=config.budgets)
    return {"schema_version": "credit-shadow-observation-v1", "observed_at": stamp.isoformat(),
        "window": {"start": start.isoformat(), "end": end.isoformat()},
        "tariff_version": pricing.TARIFF_VERSION, "financial_policy_version": config.policy.version,
        "funding_policy_version": config.budgets.version,
        "services": [services[key] for key in sorted(services)],
        "unified_discovery_batches": discovery,
        "deterministic_or_curated_runs": deterministic, "unclassified_runs": unclassified,
        "all_retained_provider_liabilities": provider,
        "all_retained_delivery_states": [{"delivery": delivery, "cost": cost, "count": count} for delivery, cost, count in holds],
        "protected_execution_exposure_inr_minor": exposure,
        "unexpired_unspent_customer_credits": int(live_credits or 0),
        "accepted_reserved_customer_credits": int(reserved_credits or 0),
        "release_qualified": False,
        "limitations": ["Hypothetical tariffs never change customer charges.",
            "Recorded success does not certify quality or human review.",
            "Provider token accounting is not supplier invoice reconciliation.",
            "Unknown costs remain protected across cancellation, deletion and period boundaries.",
            "Discovery uses explicit batch totals; counts alone do not certify source freshness or result relevance.",
            "Final application submission is not enabled or inferred from preparation activity.",
            "A historical date range alone does not establish seven days of representative shadow observation."]}
