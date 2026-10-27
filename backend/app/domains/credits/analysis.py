"""Explicit canary adapter from existing analysis work to unified paid accounting."""

from __future__ import annotations

import contextlib
import json

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ... import models
from ...services.generation_budget import persistent_run_budget
from ...services.model_cost_policy import ModelCostUnavailable
from ..common import payload_fingerprint, public_id, utcnow
from . import admission, execution, finance, pricing, runtime, service
from .models import (
    CreditExecutionAttempt,
    CreditExecutionHold,
    CreditReservation,
    CreditServiceQuote,
)


class UnifiedExecutionUnavailable(ModelCostUnavailable):
    """A declined worker path must not trigger an unfunded whole-worker retry."""


def is_unified(run: models.AnalysisRun) -> bool:
    return (run.model_cost_quote or {}).get("billing_currency") == "credits"


def paid_operation(operation: str, payload: dict) -> bool:
    return operation in {"resume_tailor", "market_analysis"} or (
        operation in {"job_match", "interview_questions"} and payload.get("mode") == "enhanced"
    )


def prepare_quote_context(db: Session, *, user_id: int, payload):
    """Same normalized semantic identity used by execution; bounds are server facts."""
    from ..analysis.service import _dependency_snapshot, validate_analysis_input

    normalized = dict(payload.input)
    validate_analysis_input(
        db,
        user_id=user_id,
        operation=payload.operation,
        opportunity_id=payload.opportunity_id,
        payload=normalized,
    )
    dependencies = _dependency_snapshot(
        db, user_id, payload.operation, payload.opportunity_id, normalized
    )
    semantic = {
        "operation": payload.operation,
        "opportunity_id": payload.opportunity_id,
        "input": normalized,
        "dependencies": dependencies,
    }
    fingerprint = payload_fingerprint(semantic)
    source_bytes = 0
    if normalized.get("resume_id"):
        source_bytes = int(
            db.query(func.length(models.Resume.source_document.expression))
            .filter_by(id=normalized["resume_id"], user_id=user_id)
            .scalar()
            or 0
        )
    observed = {
        "context_bytes": len(json.dumps(semantic, ensure_ascii=False, default=str).encode()),
        "source_bytes": source_bytes,
        "evidence_items": len(dependencies.get("approved_evidence", [])),
    }
    config = runtime.current_runtime(user_id)
    route = config.resolve_route(payload.operation, normalized, observed_limits=observed)
    if (
        payload.operation == "resume_tailor"
        and dependencies.get("resume", {}).get("source_format")
        not in route.supported_source_formats
    ):
        raise finance.FundingUnavailable(
            "The selected route is not qualified for this source format"
        )
    return normalized, fingerprint, config, route


def _client_intent_fingerprint(payload) -> str:
    return payload_fingerprint(
        {
            "operation": payload.operation,
            "opportunity_id": payload.opportunity_id,
            "input": dict(payload.input),
            "accepted_service_quote_id": getattr(payload, "accepted_service_quote_id", None),
        }
    )


def recover_paid_run(db: Session, *, user_id: int, payload, idempotency_key: str):
    """Recover accepted exact intent before revalidating mutable or deleted sources."""
    accepted = getattr(payload, "accepted_service_quote_id", None)
    if not accepted:
        return None
    from ..analysis.models import AnalysisRequestKey

    alias = db.get(AnalysisRequestKey, (user_id, idempotency_key))
    existing = (
        db.query(models.AnalysisRun)
        .filter_by(user_id=user_id, id=alias.analysis_run_id)
        .one_or_none()
        if alias
        else db.query(models.AnalysisRun)
        .filter_by(user_id=user_id, idempotency_key=idempotency_key)
        .one_or_none()
    )
    if existing is None:
        quote = (
            db.query(CreditServiceQuote)
            .filter_by(id=accepted, user_id=user_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if quote is not None and quote.accepted_reservation_id:
            reservation = (
                db.query(CreditReservation)
                .filter_by(id=quote.accepted_reservation_id, user_id=user_id)
                .one_or_none()
            )
            existing = (
                db.query(models.AnalysisRun)
                .filter_by(id=reservation.source_id, user_id=user_id)
                .one_or_none()
                if reservation
                else None
            )
            if existing is None:
                raise HTTPException(
                    409,
                    {
                        "code": "credit_service_quote_in_use",
                        "run_created": True,
                        "message": "The accepted operation needs reconciliation before new work can start.",
                    },
                )
    if existing is None:
        return None
    if (existing.model_cost_quote or {}).get(
        "client_request_fingerprint"
    ) != _client_intent_fingerprint(payload):
        raise HTTPException(
            409,
            {
                "code": "credit_service_quote_in_use",
                "run_created": True,
                "run_id": existing.id,
                "message": "This request already has accepted work. Recover that result before changing the inputs.",
            },
        )
    if alias is None:
        db.add(
            AnalysisRequestKey(
                user_id=user_id,
                idempotency_key=idempotency_key,
                analysis_run_id=existing.id,
                input_fingerprint=existing.input_fingerprint,
            )
        )
        db.commit()
    return existing, False


def _quote_rejected(message: str) -> HTTPException:
    return HTTPException(
        409, {"code": "credit_service_quote_rejected", "run_created": False, "message": message}
    )


def maybe_create_paid_run(
    db: Session,
    *,
    user_id: int,
    payload,
    idempotency_key: str,
    input_payload: dict,
    fingerprint: str,
):
    """Called after existing owned reuse checks and before any legacy reservation."""
    accepted = getattr(payload, "accepted_service_quote_id", None)
    enabled = runtime.enabled_for(user_id)
    if accepted:
        prior = (
            db.query(CreditServiceQuote)
            .filter_by(id=accepted, user_id=user_id)
            .with_for_update()
            .populate_existing()
            .one_or_none()
        )
        if prior is not None and prior.accepted_reservation_id:
            reservation = db.get(CreditReservation, prior.accepted_reservation_id)
            # This response must not authorize a new attempt: the old quote
            # already produced durable intent, even if its response was lost.
            raise HTTPException(
                409,
                {
                    "code": "credit_service_quote_in_use",
                    "run_created": True,
                    "run_id": reservation.source_id,
                    "message": "This quote already belongs to an analysis. Check its status before starting new work.",
                },
            )
        if prior is None:
            raise _quote_rejected("The service quote is invalid or belongs to another account")
    else:
        prior = None
    if not enabled and (
        accepted
        or (
            paid_operation(payload.operation, input_payload)
            and runtime.has_unified_contract(db, user_id)
        )
    ):
        raise _quote_rejected(
            "Unified paid execution is paused; your existing credits remain unchanged"
        )
    if not enabled or not paid_operation(payload.operation, input_payload):
        if accepted:
            raise _quote_rejected("This basic or curated operation does not use paid credits")
        return None
    if not accepted:
        raise HTTPException(
            409,
            {
                "code": "service_quote_requires_review",
                "message": "Review a current service quote before starting this work.",
            },
        )
    try:
        normalized, checked, config, route = prepare_quote_context(
            db, user_id=user_id, payload=payload
        )
    except finance.FundingUnavailable as exc:
        raise _quote_rejected(str(exc)) from None
    stamp = service._stamp(None)
    if (
        checked != fingerprint
        or prior.input_fingerprint != fingerprint
        or prior.operation != route.operation
    ):
        raise _quote_rejected("The selected inputs changed; review a current service quote")
    if not pricing.aware(prior.created_at) <= stamp < pricing.aware(prior.expires_at):
        raise _quote_rejected("The service quote expired; review a new quote")
    if prior.snapshot["route"] != route.model_dump(mode="json") or prior.snapshot[
        "policy"
    ] != config.policy.model_dump(mode="json"):
        raise _quote_rejected("The qualified route or policy changed; review a new quote")
    # All definite quote rejections above precede AnalysisRun/reservation
    # creation. Later failures and uncertain commit outcomes keep recovery intent.
    from ...services.generation_gate import check_generation_admission
    from ..analysis.models import AnalysisRequestKey
    from ..career.service import get_opportunity

    check_generation_admission(payload.operation, normalized)
    now = utcnow()
    run = models.AnalysisRun(
        id=public_id("run"),
        user_id=user_id,
        opportunity_id=payload.opportunity_id,
        operation=payload.operation,
        status="queued",
        idempotency_key=idempotency_key,
        input_fingerprint=fingerprint,
        input_payload=normalized,
        estimated_units=route.credits,
        committed_units=0,
        usage_state="unified_reserved",
        generation_attempt_limit=len(route.model_quotes),
        generation_attempt_count=0,
        created_at=now,
        updated_at=now,
    )
    # Amount is denominated by explicit billing_currency. Existing AI-unit
    # fields are transport-compatible counters, never a silent legacy debit.
    db.add(run)
    try:
        db.flush([run])
        hold = admission.admit(
            db,
            user_id=user_id,
            quote_id=accepted,
            input_fingerprint=fingerprint,
            idempotency_key=idempotency_key,
            source_id=run.id,
            route=route,
            policy=config.policy,
            budgets=config.budgets,
            dispatch_topic="analysis.run",
            dispatch_payload={"run_id": run.id},
        )
        run.model_cost_quote = {
            "billing_currency": "credits",
            "unified_hold_id": hold.id,
            "tariff_version": route.tariff_version,
            "credits": route.credits,
            "client_request_fingerprint": _client_intent_fingerprint(payload),
        }
        run.model_cost_group_id, run.model_cost_state = hold.financial_group_id, "active"
        db.add(
            AnalysisRequestKey(
                user_id=user_id,
                idempotency_key=idempotency_key,
                analysis_run_id=run.id,
                input_fingerprint=fingerprint,
            )
        )
        if payload.opportunity_id:
            opportunity = get_opportunity(db, user_id, payload.opportunity_id)
            opportunity.latest_analysis_run_id, opportunity.updated_at = run.id, now
        db.commit()
    except (ValueError, finance.FundingUnavailable) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from None
    db.refresh(run)
    return run, True


def _run_hold(db: Session, run: models.AnalysisRun) -> CreditExecutionHold:
    hold = (
        db.query(CreditExecutionHold)
        .filter_by(
            id=(run.model_cost_quote or {}).get("unified_hold_id"),
            user_id=run.user_id,
            financial_group_id=run.model_cost_group_id,
        )
        .one_or_none()
    )
    if hold is None:
        raise UnifiedExecutionUnavailable("The analysis has no matching funded execution")
    return hold


@contextlib.contextmanager
def analysis_budget(factory, run_id: str):
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        if run is None:
            raise UnifiedExecutionUnavailable("The analysis is no longer available")
        unified = is_unified(run)
        if unified:
            owner = run.user_id
            try:
                from types import SimpleNamespace

                service._lock_owner(db, owner)
                _, fingerprint, config, route = prepare_quote_context(
                    db,
                    user_id=owner,
                    payload=SimpleNamespace(
                        operation=run.operation,
                        opportunity_id=run.opportunity_id,
                        input=run.input_payload,
                    ),
                )
                if fingerprint != run.input_fingerprint:
                    raise UnifiedExecutionUnavailable(
                        "The selected inputs changed after admission; review a new quote"
                    )
                hold = _run_hold(db, run)
                db.refresh(hold, with_for_update=True)
                if hold.state != "reserved" or hold.delivery_state != "pending":
                    raise UnifiedExecutionUnavailable(
                        "A prior execution needs reconciliation; do not repeat native work"
                    )
                if hold.snapshot["route"] != route.model_dump(mode="json") or hold.snapshot[
                    "policy"
                ] != config.policy.model_dump(mode="json"):
                    raise UnifiedExecutionUnavailable(
                        "The accepted route needs a current matching authorization"
                    )
                hold.state = "running"
                hold_id = hold.id
                db.commit()
            except finance.FundingUnavailable as exc:
                raise UnifiedExecutionUnavailable(str(exc)) from None
    if not unified:
        with persistent_run_budget(factory, run_id) as budget:
            yield budget
        return
    with execution.persistent_execution_budget(
        factory,
        user_id=owner,
        hold_id=hold_id,
        route=route,
        policy=config.policy,
        analysis_run_id=run_id,
    ) as budget:
        yield budget


def settle_analysis(db: Session, run: models.AnalysisRun, *, delivered: bool) -> bool:
    """Used by both result commit and cancellation; returns False for legacy work."""
    if not is_unified(run):
        return False
    hold = _run_hold(db, run)
    execution.settle_delivery(db, user_id=run.user_id, hold_id=hold.id, delivered=delivered)
    run.usage_state = "committed" if delivered else "released"
    run.committed_units = run.estimated_units if delivered else 0
    attempts = db.query(CreditExecutionAttempt).filter_by(hold_id=hold.id).all()
    run.generation_attempt_count = sum(attempt.started_at is not None for attempt in attempts)
    run.model_cost_state = hold.state
    # The native/worker estimate remains held until measured reconciliation.
    # Successful customer delivery does not manufacture a supplier invoice.
    return True
