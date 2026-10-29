"""Actual complete synchronous services under the separate funded v2 authority.

No retail quote, entitlement, canary population, resource verification or model
receipt is fabricated. Basic matching is the production deterministic operation;
its result is retained and rechecked as context, never labelled enhanced output.
"""
from __future__ import annotations

import hashlib
import os

from sqlalchemy.orm import Session

from ... import models
from ...services import basic_matching, generation_budget, synchronous_contracts
from ..common import payload_fingerprint
from ..credits import synchronous

OPERATIONS = frozenset({"resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"})
VERSION = "supported-synchronous-inputs-v1"
QA_QUESTION = "How does my approved experience fit this role, and which evidence gaps should I address?"
_KEY = "synchronous_input"


def supports(operation: str) -> bool:
    return operation in OPERATIONS


def prompt_version(operation: str) -> str:
    if not supports(operation):
        raise ValueError("Unsupported synchronous evaluation operation")
    return synchronous_contracts.VERSIONS["prompt"]


def contract_sources() -> tuple:
    from . import operations

    return (synchronous, synchronous_contracts, basic_matching, operations)


def _sources(db: Session, run):
    if not supports(run.operation):
        raise ValueError("Unsupported synchronous evaluation operation")
    payload = run.input_payload or {}
    resume = db.query(models.Resume).filter_by(id=payload.get("resume_id"), user_id=run.user_id).populate_existing().one_or_none()
    opportunity = db.query(models.Opportunity).filter_by(id=run.opportunity_id, user_id=run.user_id).populate_existing().one_or_none()
    if (resume is None or opportunity is None or opportunity.resume_id != resume.id
            or payload.get("opportunity_id") != opportunity.id):
        raise ValueError("The complete owned synchronous sources changed")
    return resume, opportunity


def _request(run, resume, opportunity, match_id):
    if run.operation == "resume_enrichment":
        value = {"resume_id": resume.id}
    elif run.operation == "bullet_rewrite":
        value = {"resume_id": resume.id, "job_description": opportunity.job_description, "tone": "concise"}
    else:
        if type(match_id) is not int or match_id <= 0:
            raise ValueError("A genuine basic matching dependency is required")
        if run.operation == "enhanced_qa":
            value = {"job_match_id": match_id, "resume_id": resume.id, "question": QA_QUESTION,
                     "recent_messages": [], "mode": "enhanced"}
        else:
            value = {"match_id": match_id, "mode": "enhanced"}
    return synchronous.SynchronousServiceRequest(operation=run.operation, input=value)


def _resource_review(operation):
    if operation != "learning_strategy":
        return None
    # Bind the operator's actual retained review, not model-provided URLs or a
    # newly synthesized 'reviewed' label. Freshness is also checked separately.
    return hashlib.sha256(os.getenv("LEARNING_RESOURCE_REVIEW_JSON", "").encode()).hexdigest()


def _require_basic_match(db, run, resume, opportunity, identity):
    match = db.query(models.JobMatch).filter_by(id=identity, user_id=run.user_id, resume_id=resume.id).populate_existing().one_or_none()
    if match is None:
        raise ValueError("The genuine basic matching dependency is unavailable")
    basic = basic_matching.basic_match(resume.skills or [], opportunity.job_description, opportunity.title)
    expected = {
        "job_title": str(opportunity.title or "Target role").strip(),
        "company": str(opportunity.company or "").strip(), "job_description": opportunity.job_description,
        "match_score": basic["score"], "required_skills": basic["required_skills"],
        "full_matches": basic["matched_skills"], "partial_matches": [], "true_gaps": basic["missing_evidence"],
        "fit_summary": " ".join(basic["reasons"] + basic["uncertainties"][:1]),
        "dimension_scores": [], "skill_verification_rate": 0.0,
        "improvement_tips": ["Review missing evidence; add only skills and experience you can substantiate."],
    }
    if any(getattr(match, key) != value for key, value in expected.items()):
        raise ValueError("The production basic matching result changed")


def prepare_inputs(db: Session, run) -> None:
    """Once-only deterministic prerequisites before the frozen run fingerprint.

    The caller owns the evaluation prepare transaction. Resume enrichment does
    not persist derived skills here: each side must see the same native input.
    The actual validated enrichment deliverable is still generated in full.
    """
    if _KEY in (run.input_payload or {}):
        immutable_inputs_current(db, run)
        return
    resume, opportunity = _sources(db, run)
    match_id = None
    if run.operation in {"enhanced_qa", "learning_strategy"}:
        from .operations import execute_job_match

        result = execute_job_match(db, user_id=run.user_id, payload={
            "mode": "basic", "resume_id": resume.id, "job_title": opportunity.title,
            "company": opportunity.company, "job_description": opportunity.job_description,
        })
        if result.get("mode") != "basic" or result.get("provenance") != "local_rules":
            raise ValueError("The production basic matching contract changed")
        match_id = result["match_id"]
        _require_basic_match(db, run, resume, opportunity, match_id)
    payload = _request(run, resume, opportunity, match_id)
    prepared = synchronous.owned_context(db, user_id=run.user_id, payload=payload)
    synchronous.require_current_resources(prepared)
    run.input_payload = dict(run.input_payload) | {_KEY: {
        "version": VERSION, "request": payload.model_dump(), "context_sha256": prepared.fingerprint,
        "basic_match_id": match_id, "basic_scoring_version": basic_matching.SCORING_VERSION if match_id else None,
        "learning_resource_review_sha256": _resource_review(run.operation),
    }}


def _prepared(db, run):
    resume, opportunity = _sources(db, run)
    binding = (run.input_payload or {}).get(_KEY)
    if (not isinstance(binding, dict) or set(binding) != {
        "version", "request", "context_sha256", "basic_match_id", "basic_scoring_version", "learning_resource_review_sha256"
    } or binding["version"] != VERSION or binding["learning_resource_review_sha256"] != _resource_review(run.operation)):
        raise ValueError("The frozen complete synchronous input binding changed")
    match_id = binding["basic_match_id"]
    if run.operation in {"enhanced_qa", "learning_strategy"}:
        if type(match_id) is not int or binding["basic_scoring_version"] != basic_matching.SCORING_VERSION:
            raise ValueError("The genuine basic matching dependency changed")
        _require_basic_match(db, run, resume, opportunity, match_id)
    elif match_id is not None or binding["basic_scoring_version"] is not None:
        raise ValueError("This synchronous operation has no matching dependency")
    payload = _request(run, resume, opportunity, match_id)
    if binding["request"] != payload.model_dump():
        raise ValueError("The exact complete synchronous request changed")
    prepared = synchronous.owned_context(db, user_id=run.user_id, payload=payload)
    if binding["context_sha256"] != prepared.fingerprint:
        raise ValueError("The frozen complete synchronous context changed")
    synchronous.require_current_resources(prepared)
    return payload, prepared


def immutable_inputs_current(db: Session, run) -> None:
    _prepared(db, run)


def fingerprint(db: Session, run) -> str:
    _, prepared = _prepared(db, run)
    return payload_fingerprint({"operation": run.operation, "input": run.input_payload,
                                "complete_context_sha256": prepared.fingerprint})


def execute(db: Session, run) -> dict:
    from .supported_evaluation import require_provider_authority

    require_provider_authority(db, run)
    budget = generation_budget.current_budget()
    if budget is None or budget.run_id != run.id or budget.before is None or budget.after is None or budget.preflight is None:
        raise ValueError("Synchronous evaluation requires its exact persistent model budget")
    payload, prepared = _prepared(db, run)
    result = synchronous.generate_prepared(prepared)
    require_provider_authority(db, run)
    synchronous.require_current_inputs(db, user_id=run.user_id, payload=payload, prepared=prepared)
    _prepared(db, run)
    return result
