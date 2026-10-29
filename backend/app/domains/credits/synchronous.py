"""Owned synchronous quote/execution boundary, with durable exact-result replay.

Provider and native expense survive failed delivery. An interrupted inline run
is never re-dispatched: its pending outcome must first be reconciled.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from ... import models, schemas
from ...services import synchronous_contracts as contracts
from ...services.result_commit import ResultOwnerGone, begin_result_commit
from ..analysis.models import AnalysisRequestKey
from ..common import payload_fingerprint, public_id, utcnow
from . import admission, execution, finance, pricing, runtime, service
from .models import CreditServiceQuote

Operation = Literal["resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"]
LEGACY_OPERATIONS = {"resume_enrichment": "resume_enrichment", "bullet_rewrite": "rewrite_bullets", "enhanced_qa": "match_question", "learning_strategy": "learning_strategy"}


class SynchronousServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Operation
    input: dict = Field(default_factory=dict)


@dataclass
class PreparedSynchronous:
    operation: str
    input: dict
    context: dict
    messages: list[dict]
    fingerprint: str


def _reject(message: str, status: int = 409):
    return HTTPException(status, {"code": "synchronous_service_rejected", "run_created": False, "message": message})


def request(operation: Operation, payload: BaseModel) -> SynchronousServiceRequest:
    return SynchronousServiceRequest(operation=operation, input=payload.model_dump(exclude={"accepted_service_quote_id", "idempotency_key"}))


def _snapshot(value, fields):
    return {name: getattr(value, name) for name in fields}


def normalized_request(payload: SynchronousServiceRequest) -> dict:
    types = {"resume_enrichment": schemas.ResumeEnrichmentRequest, "bullet_rewrite": schemas.RewriteBulletsRequest,
             "enhanced_qa": schemas.RagAskRequest, "learning_strategy": schemas.LearningStrategyRequest}
    if "accepted_service_quote_id" in payload.input or "idempotency_key" in payload.input:
        raise _reject("Quote inputs cannot contain an acceptance or request identity", 422)
    try:
        parsed = types[payload.operation].model_validate(payload.input)
    except ValidationError:
        raise _reject("Review the complete supported request inputs", 422) from None
    if set(payload.input) - set(types[payload.operation].model_fields):
        raise _reject("This service does not support the supplied input fields", 422)
    normalized = parsed.model_dump(exclude={"accepted_service_quote_id", "idempotency_key"})
    return normalized


def owned_context(db: Session, *, user_id: int, payload: SynchronousServiceRequest) -> PreparedSynchronous:
    from ...services import llm_client

    normalized = normalized_request(payload)
    if payload.operation in {"learning_strategy", "enhanced_qa"} and normalized["mode"] != "enhanced":
        raise _reject("Direct and curated guidance do not require a paid quote")
    match = None
    if payload.operation in {"learning_strategy", "enhanced_qa"}:
        match_id = normalized.get("match_id", normalized.get("job_match_id"))
        match = db.query(models.JobMatch).filter_by(id=match_id, user_id=user_id).populate_existing().one_or_none()
        if match is None:
            raise HTTPException(404, "Job match not found for this user")
        selected = normalized.get("resume_id")
        if selected is not None and selected != match.resume_id:
            raise _reject("The selected resume does not belong to this match", 400)
        resume_id = match.resume_id
    else:
        resume_id = normalized["resume_id"]
    resume = db.query(models.Resume).filter_by(id=resume_id, user_id=user_id).populate_existing().one_or_none()
    if resume is None:
        raise HTTPException(404, "Resume not found for this user")
    facts = db.query(models.EvidenceItem).filter_by(user_id=user_id, resume_id=resume.id, approval_state="approved").order_by(models.EvidenceItem.id).populate_existing().all()
    context = {
        "request": normalized,
        "resume": _snapshot(resume, ("id", "raw_text", "skills", "experience_years", "sections", "source_format")),
        "approved_evidence": [{"id": item.id, "title": item.title, "text": item.evidence_text,
            "skills": item.skills or [], "metrics": item.metrics or {}, "source_ref": item.source_ref,
            "category": item.category, "confidence": item.confidence,
            "provenance": item.provenance, "updated_at": item.updated_at.isoformat() if item.updated_at else None} for item in facts],
        "versions": dict(contracts.VERSIONS),
        "generation_configuration": {"model": llm_client.LLM_MODEL, "api_base": llm_client.LLM_API_BASE,
                                     "fallback_models": list(llm_client.GEMINI_FALLBACK_MODELS)},
    }
    from ...services.delivery_contracts import current_output_contract

    context["output_contract_sha256"] = current_output_contract(payload.operation)
    if payload.operation == "bullet_rewrite" and not facts:
        raise _reject("Approve the supporting resume evidence before requesting changed personal claims")
    if match is not None:
        context["match"] = _snapshot(match, ("id", "resume_id", "job_title", "company", "job_description", "required_skills", "full_matches", "partial_matches", "true_gaps", "match_score", "fit_summary", "dimension_scores", "skill_verification_rate", "improvement_tips"))
    if payload.operation == "learning_strategy":
        assert match is not None
        selected_skills = (match.true_gaps or []) + (match.required_skills or [])
        selected_skills += [item.get("skill", "") for item in match.partial_matches or [] if isinstance(item, dict)]
        context["reviewed_resources"] = contracts.reviewed_learning_resources(selected_skills, allow_expired=True)
    # Enrichment updates this derived field itself. The immutable complete source
    # governs replay; its newly enriched skill list must not cause another charge.
    semantic = context
    if payload.operation == "resume_enrichment":
        semantic = {**context, "resume": {key: value for key, value in context["resume"].items() if key != "skills"}}
    try:
        messages = contracts.messages(payload.operation, context)
    except contracts.SynchronousOutputUnavailable as exc:
        raise _reject(str(exc), 422) from None
    return PreparedSynchronous(payload.operation, normalized, context, messages,
                               payload_fingerprint({"operation": payload.operation, "context": semantic}))


def prepare_quote_context(db: Session, *, user_id: int, payload: SynchronousServiceRequest):
    prepared = owned_context(db, user_id=user_id, payload=payload)
    config = runtime.current_runtime(user_id)
    if payload.operation == "learning_strategy":
        skills = list(prepared.context["reviewed_resources"])
        current = contracts.reviewed_learning_resources(skills)
        if not any(current.values()):
            raise _reject("Learning resources need a current review before a personalized plan can be purchased", 503)
    route = config.resolve_route(payload.operation, prepared.input, observed_limits={
        "context_bytes": len(json.dumps(prepared.context, ensure_ascii=False, default=str).encode()),
        "evidence_items": len(prepared.context["approved_evidence"]),
    })
    from ...services.model_cost_policy import input_token_estimate

    if not route.model_quotes or input_token_estimate(prepared.messages) > route.model_quotes[0].max_input_tokens:
        raise _reject("The complete prompt does not fit this qualified provider envelope. No content was shortened; review a supported scope.", 422)
    return prepared, prepared.fingerprint, config, route


def _replay(run, fingerprint):
    if run.input_fingerprint != fingerprint or run.input_purged_at or run.result_purged_at:
        raise _reject("The request identity belongs to different or expired inputs")
    if run.status == "succeeded" and run.result_payload is not None:
        return _delivery_view(run, reused=True)
    raise HTTPException(409, {"code": "synchronous_service_in_use", "run_id": run.id,
        "status": run.status, "run_created": True,
        "message": "This request already started. Check its result; it will not be executed twice."})


def _delivery_view(run, *, reused: bool) -> dict:
    result = dict(run.result_payload)
    if reused and run.operation == "sync_learning_strategy":
        skills = [item["skill"] for item in result.get("learning_priorities", [])]
        if not any(contracts.reviewed_learning_resources(skills).values()):
            result["warnings"] = [*result.get("warnings", []), "This saved plan's resource freshness review has expired. Recheck the resources before using them."]
    return {**result, "credit_execution": {"run_id": run.id,
        "billing_currency": "credits", "committed_credits": run.committed_units,
        "charged_this_request": 0 if reused else run.committed_units, "reused": reused}}


def _run_for_key(db, user_id, key):
    run = db.query(models.AnalysisRun).filter_by(user_id=user_id, idempotency_key=key).one_or_none()
    if run is not None:
        return run
    alias = db.query(AnalysisRequestKey).filter_by(user_id=user_id, idempotency_key=key).one_or_none()
    return db.get(models.AnalysisRun, alias.analysis_run_id) if alias is not None else None


def _check_accepted_request(run, payload):
    same = False
    if run.input_purged_at is None and run.result_purged_at is None:
        try:
            accepted = SynchronousServiceRequest.model_validate(run.input_payload)
            same = accepted.operation == payload.operation and normalized_request(accepted) == normalized_request(payload)
        except (ValidationError, HTTPException):
            pass
    if not same:
        raise HTTPException(409, {"code": "synchronous_service_in_use", "run_id": run.id,
            "status": run.status, "run_created": True,
            "message": "This key already belongs to an accepted request with different or expired inputs. Inspect that run before starting new work."})


def lookup_result(db: Session, *, user_id: int, payload: SynchronousServiceRequest,
                  idempotency_key: str | None = None) -> dict:
    """Read-only owned recovery/reuse, independent of current funding or flags."""
    from ..analysis.schemas import AnalysisRunResponse

    run = None
    if idempotency_key:
        if not 8 <= len(idempotency_key) <= 160:
            raise _reject("Provide an idempotency key between 8 and 160 characters", 422)
        run = _run_for_key(db, user_id, "sync:" + hashlib.sha256(idempotency_key.encode()).hexdigest())
        if run is not None:
            _check_accepted_request(run, payload)
    if run is None:
        prepared = owned_context(db, user_id=user_id, payload=payload)
        if not idempotency_key:
            run = db.query(models.AnalysisRun).filter_by(user_id=user_id, operation="sync_" + payload.operation,
                input_fingerprint=prepared.fingerprint, status="succeeded", cancel_requested=False,
                input_purged_at=None, result_purged_at=None).order_by(models.AnalysisRun.created_at.desc()).first()
    if run is None:
        return {"reused_run": None, "result": None}
    return {"reused_run": AnalysisRunResponse.model_validate(run).model_dump(mode="json"),
            "result": _delivery_view(run, reused=True) if run.status == "succeeded" else None}


def execute(db: Session, *, user_id: int, payload: SynchronousServiceRequest,
            accepted_service_quote_id: str | None, idempotency_key: str | None) -> dict:
    if not idempotency_key or not 8 <= len(idempotency_key) <= 160:
        raise _reject("Provide an idempotency key between 8 and 160 characters", 422)
    key = "sync:" + hashlib.sha256(idempotency_key.encode()).hexdigest()
    from ..employer.admissions import lock_application_set

    lock_application_set(db, user_id)
    service._lock_owner(db, user_id)
    existing = _run_for_key(db, user_id, key)
    if existing is not None:
        _check_accepted_request(existing, payload)
        return _replay(existing, existing.input_fingerprint)
    prepared = owned_context(db, user_id=user_id, payload=payload)
    # Existing complete results are free even when the current quote expired.
    saved = db.query(models.AnalysisRun).filter_by(user_id=user_id, operation="sync_" + payload.operation,
        input_fingerprint=prepared.fingerprint, status="succeeded", input_purged_at=None,
        result_purged_at=None, cancel_requested=False).order_by(models.AnalysisRun.created_at.desc()).first()
    if saved is not None and saved.result_payload is not None:
        response = _delivery_view(saved, reused=True)
        db.add(AnalysisRequestKey(user_id=user_id, idempotency_key=key,
            analysis_run_id=saved.id, input_fingerprint=prepared.fingerprint))
        db.commit()
        return response
    if not runtime.enabled_for(user_id):
        raise _reject("Unified paid execution is not enabled for this account")
    if not accepted_service_quote_id:
        raise _reject("Review a current service quote before starting this work")
    try:
        prepared, fingerprint, config, route = prepare_quote_context(db, user_id=user_id, payload=payload)
        quote = db.query(CreditServiceQuote).filter_by(id=accepted_service_quote_id, user_id=user_id).one_or_none()
        if quote is None or quote.operation != route.operation or quote.input_fingerprint != fingerprint:
            raise pricing.CreditContractError("The service quote does not match these complete inputs")
        if quote.accepted_reservation_id:
            raise pricing.CreditContractError("This service quote already belongs to another request")
        from ...services.generation_gate import check_generation_admission

        check_generation_admission(LEGACY_OPERATIONS[payload.operation], {"mode": "enhanced"})
        stamp = utcnow()
        run = models.AnalysisRun(id=public_id("run"), user_id=user_id,
            operation="sync_" + payload.operation, status="running", idempotency_key=key,
            input_fingerprint=fingerprint, input_payload=payload.model_dump(),
            estimated_units=route.credits, committed_units=0, usage_state="reserved",
            attempt_count=1, generation_attempt_limit=len(route.model_quotes),
            created_at=stamp, updated_at=stamp, started_at=stamp)
        db.add(run)
        db.flush([run])
        hold = admission.admit(db, user_id=user_id, quote_id=quote.id, input_fingerprint=fingerprint,
            idempotency_key=key, source_id=run.id, route=route, policy=config.policy,
            budgets=config.budgets, inline=True)
        hold.state = "running"
        run.model_cost_group_id = hold.financial_group_id
        run.model_cost_quote = {"billing_currency": "credits", "unified_hold_id": hold.id,
            "tariff_version": route.tariff_version, "credits": route.credits}
        identity, hold_id = run.id, hold.id
        db.commit()
    except (finance.FundingUnavailable, pricing.CreditContractError) as exc:
        db.rollback()
        raise _reject(str(exc)) from None
    try:
        from ...services.llm_client import chat_json
        from .analysis import settle_analysis

        with execution.persistent_execution_budget(sessionmaker(bind=db.get_bind()), user_id=user_id,
                hold_id=hold_id, route=route, policy=config.policy, analysis_run_id=identity):
            raw = chat_json(prepared.messages)
            result = contracts.validate(payload.operation, raw, prepared.context)
        begin_result_commit(db, user_id, identity)
        fresh = owned_context(db, user_id=user_id, payload=payload)
        if fresh.fingerprint != prepared.fingerprint:
            raise contracts.SynchronousOutputUnavailable("The selected inputs changed during generation. Review a new quote; this attempt is not charged.")
        if payload.operation == "learning_strategy" and not any(contracts.reviewed_learning_resources(list(fresh.context["reviewed_resources"])).values()):
            raise contracts.SynchronousOutputUnavailable("The resource freshness review expired during generation; this attempt is not charged.")
        current = db.get(models.AnalysisRun, identity, populate_existing=True)
        if payload.operation == "resume_enrichment":
            resume = db.query(models.Resume).filter_by(id=result["resume_id"], user_id=user_id).with_for_update().one()
            resume.skills = result["skills"]
        current.result_payload = result
        settle_analysis(db, current, delivered=True)
        current.status, current.usage_state, current.committed_units = "succeeded", "committed", route.credits
        current.completed_at = current.updated_at = utcnow()
        response = _delivery_view(current, reused=False)
        db.commit()
        return response
    except Exception as exc:
        db.rollback()
        try:
            begin_result_commit(db, user_id, identity, allow_cancelled=True)
            current = db.get(models.AnalysisRun, identity, populate_existing=True)
            if current is not None:
                settle_analysis(db, current, delivered=False)
                current.status, current.usage_state, current.committed_units = "failed", "released", 0
                current.error_code = type(exc).__name__
                current.error_message = "The synchronous service did not produce a validated deliverable."
                current.completed_at = current.updated_at = utcnow()
                db.commit()
        except ResultOwnerGone:
            db.rollback()
        if isinstance(exc, contracts.SynchronousOutputUnavailable):
            raise HTTPException(422, {"code": "synchronous_delivery_unavailable", "run_id": identity,
                "run_created": True, "credits_charged": 0, "message": str(exc)}) from None
        from ...services.llm_client import LLMProviderError

        if isinstance(exc, finance.FundingUnavailable) or (
            isinstance(exc, LLMProviderError) and exc.budget_exhausted
        ):
            raise HTTPException(503, {
                "code": "model_preflight_unavailable", "run_id": identity,
                "run_created": True, "credits_charged": 0,
                "message": "The model request could not pass its cost and input checks. No credits were charged.",
            }) from None
        raise
