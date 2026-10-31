"""Actual v2 core/production operations on its own PG17, synthetic transports.

Native capture fixtures, resource reviews and model responses here are test data,
never provider/native/human/retail qualification. No external calls are made.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app import models
from app.domains.analysis import supported_evaluation as core
from app.domains.analysis import supported_evaluation_plan as inputs
from app.domains.analysis import supported_evaluation_synchronous as sync
from app.domains.career.evidence_write_fence import flush_evidence_writes
from app.domains.free_core.models import FreeCoreReservation
from app.services import (
    llm_client,
    matching_contract,
    model_cost_policy,
    prompt_privacy,
    recommender,
)
from tests.test_complete_matching import comparison_output
from tests.test_supported_evaluation import ROOT, actual_composite_capture, evaluation  # noqa: F401
from tests.test_supported_evaluation_postgres import owned_postgres  # noqa: F401
from tests.test_supported_evaluation_synchronous import output as sync_output


def authorization(values, owned, operation, *, different_models=False):
    data = values["auth"].model_dump(mode="json")
    data.update(operation=operation, database=owned["pin"])
    for side, route in data["routes"].items():
        route["prompt_version"] = sync.prompt_version(operation) if sync.supports(operation) else matching_contract.PROMPT_VERSION
        route["contract_sha256"] = core.contract_digest(operation)
        if different_models and side == "candidate":
            quote = next(q for q in model_cost_policy.current_policy().pricing_quotes if q.model == "gemini-3.5-flash-lite")
            route["price"] = quote.model_dump(mode="json")
    return values | {"auth": inputs.Authorization.model_validate(data)}


def select_pair(factory, auth, case_id):
    identities = {side: core._run_id(auth, side, case_id) for side in inputs.SIDES}
    with factory() as db:
        # Exclude unrelated test slots without dropping any from the 80-slot
        # prepared population/export. Production execute never modifies these.
        db.query(models.AnalysisRun).filter_by(status="queued").filter(models.AnalysisRun.id.not_in(identities.values())).update({"status": "running"})
        db.commit()
    return identities


def transport(monkeypatch, operation, *, invalid=False, unknown=False):
    calls = []
    def generate(messages, budget):
        context = json.loads(messages[-1]["content"].split("COMPLETE SOURCE DATA:\n", 1)[1])
        record = budget.admit("google", llm_client.LLM_MODEL, messages, api_base=llm_client.LLM_API_BASE)
        calls.append((llm_client.LLM_MODEL, copy.deepcopy(context)))
        budget.finish(record, latency_ms=1, response=None if unknown else SimpleNamespace(usage_metadata=SimpleNamespace(
            prompt_token_count=100, candidates_token_count=80, total_token_count=200, thoughts_token_count=20)))
        if invalid:
            return "{}"
        if operation == "job_match":
            return json.dumps(comparison_output(context))
        fact = context["approved_evidence"][0]
        if operation == "resume_enrichment":
            skills = {row["skill"] for row in matching_contract.extract_skill_mentions(context["resume"]["raw_text"])}
            result = {"skills": sorted(skills)}
        elif operation == "bullet_rewrite":
            original = fact["text"]
            # Pure fixture phrasing, same facts and metrics, distinct wording.
            replacement = original.replace("Built", "Developed", 1).replace("Implemented", "Developed", 1).replace("Python and PostgreSQL", "PostgreSQL and Python", 1)
            if replacement == original:
                replacement = " ".join(original.split()) + " "
            result = {"bullets": [{"original_text": original, "replacement_text": replacement, "evidence_ids": [fact["id"]]}], "summary": "Clearer action wording."}
        elif operation == "enhanced_qa":
            result = {"answer": "Review the cited approved record against this role, and explain the decisions you actually made.", "sources": [fact["id"]], "confidence": "medium", "suggested_followups": []}
        else:
            result = sync_output("learning_strategy")
            skill = next(skill for skill, rows in context["reviewed_resources"].items() if rows)
            result = json.loads(json.dumps(result).replace("Docker", skill).replace("docker", skill))
        return json.dumps(result)
    monkeypatch.setattr(llm_client, "_chat_with_budget", generate)
    return calls


def review_resources(monkeypatch):
    now = datetime.now(UTC)
    monkeypatch.setenv("LEARNING_RESOURCE_REVIEW_JSON", json.dumps({"checked_at": (now - timedelta(days=1)).isoformat(),
        "expires_at": (now + timedelta(days=1)).isoformat(),
        "urls": {row["url"]: "a" * 64 for row in recommender.COURSES}}))


def test_complete_match_actual_native_tex_production_persistence_and_two_model_sides(evaluation, owned_postgres, monkeypatch):  # noqa: F811
    case, _, source, _, parsed = actual_composite_capture(evaluation, monkeypatch, kind="tex", columns=1)
    values = authorization(evaluation, owned_postgres, "job_match", different_models=True)
    factory = core.local_factory(owned_postgres["url"], values["auth"])
    try:
        core.prepare(factory, **values)
        ids = select_pair(factory, values["auth"], case["case_id"])
        calls = transport(monkeypatch, "job_match")
        with pytest.raises(ValueError, match="model/endpoint"):
            core.execute(factory, **values, side="candidate")
        assert not calls
        assert core.execute(factory, **values, side="reference")["outcomes"] == {ids["reference"]: "succeeded"}
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.5-flash-lite")
        assert core.execute(factory, **values, side="candidate")["outcomes"] == {ids["candidate"]: "succeeded"}
        assert len(calls) == 2 and [x[0] for x in calls] == ["gemini-3.6-flash", "gemini-3.5-flash-lite"]
        assert calls[0][1] == calls[1][1]
        assert calls[0][1]["resume"]["raw_text"] == prompt_privacy.redact_prompt_text(parsed["raw_text"])
        with factory() as db:
            assert db.query(models.JobMatch).count() == 2
            for identity in ids.values():
                run = db.get(models.AnalysisRun, identity)
                result = run.result_payload
                assert result["mode"] == "enhanced" and result["provenance"] == "generated"
                assert result["score_basis"] == "documented_supported_signals_not_hiring_probability"
                assert result["gap_semantics"] == "missing_approved_evidence_not_missing_ability"
                assert len(result["dimensions"]) + len(result["unassessed_dimensions"]) == 8
                assert run.prompt_version == matching_contract.PROMPT_VERSION
                assert run.generation_attempt_count == 1 and run.committed_units == 0
                resume = db.get(models.Resume, run.input_payload["resume_id"])
                assert resume.source_document == source and resume.raw_text == parsed["raw_text"]
            assert {event.model for event in db.query(models.ModelCallEvent)} == {x[0] for x in calls}
            assert all(event.cost_state == "settled" for event in db.query(models.ModelCallEvent))
            assert all(row.settled_cost_micros == 200 for row in db.query(models.ModelCostLiability))
            assert all(row.settled_minor is None for row in db.query(FreeCoreReservation))
    finally:
        factory.kw["bind"].dispose()


@pytest.mark.parametrize("operation", sorted(sync.OPERATIONS))
def test_four_sync_operations_use_actual_core_authority_and_shared_executor(evaluation, owned_postgres, monkeypatch, operation):  # noqa: F811
    review_resources(monkeypatch)
    values = authorization(evaluation, owned_postgres, operation, different_models=True)
    factory = core.local_factory(owned_postgres["url"], values["auth"])
    try:
        core.prepare(factory, **values)
        ids = select_pair(factory, values["auth"], "synth-001")
        calls = transport(monkeypatch, operation)
        with factory() as db:
            original = copy.deepcopy(db.get(models.AnalysisRun, ids["candidate"]).input_payload)
        assert core.execute(factory, **values, side="reference")["outcomes"] == {ids["reference"]: "succeeded"}
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.5-flash-lite")
        assert core.execute(factory, **values, side="candidate")["outcomes"] == {ids["candidate"]: "succeeded"}
        assert len(calls) == 2
        with factory() as db:
            run = db.get(models.AnalysisRun, ids["candidate"])
            assert run.input_payload == original and run.prompt_version == "sync-evidence-v2"
            assert run.committed_units == 0 and run.generation_attempt_count == 1
            assert run.result_payload.get("credit_execution") is None
            assert all(x.cost_state == "settled" and x.settled_cost_micros == 200 for x in db.query(models.ModelCostLiability))
            assert all(x.settled_minor is None for x in db.query(FreeCoreReservation))
            if operation in {"enhanced_qa", "learning_strategy"}:
                assert db.query(models.JobMatch).count() == 80
                assert all(x.dimension_scores == [] for x in db.query(models.JobMatch))
                assert run.input_payload["synchronous_input"]["basic_scoring_version"] == "basic-catalog-overlap-v1"
            if operation == "learning_strategy":
                unsupported = db.query(models.AnalysisRun).filter_by(error_code="evaluation_unsupported_inputs").all()
                assert unsupported and all(row.status == "failed" and row.generation_attempt_count == 0 for row in unsupported)
                assert db.query(models.AnalysisRun).count() == 80
            if operation == "resume_enrichment":
                assert db.get(models.Resume, run.input_payload["resume_id"]).skills == []
                assert run.result_payload["skills"]
    finally:
        factory.kw["bind"].dispose()


@pytest.mark.parametrize("changed", ["evidence", "job", "request", "contract"])
def test_matching_tamper_refuses_even_with_recomputed_stored_fingerprint(evaluation, owned_postgres, monkeypatch, changed):  # noqa: F811
    values = authorization(evaluation, owned_postgres, "job_match")
    factory = core.local_factory(owned_postgres["url"], values["auth"])
    try:
        core.prepare(factory, **values)
        identity = select_pair(factory, values["auth"], "synth-001")["candidate"]
        with factory() as db:
            run = db.get(models.AnalysisRun, identity)
            if changed == "evidence":
                evidence = db.query(models.EvidenceItem).filter_by(resume_id=run.input_payload["resume_id"]).first()
                evidence.approval_state = "rejected"
                flush_evidence_writes(db, [evidence])
            elif changed == "job":
                db.get(models.Opportunity, run.opportunity_id).job_description += " Changed scope."
            elif changed == "request":
                run.input_payload = dict(run.input_payload) | {"job_description": "Changed complete job requiring Kubernetes."}
            else:
                metadata = copy.deepcopy(run.input_payload)
                metadata["_quality_evaluation"]["authorization"]["routes"]["candidate"]["contract_sha256"] = "f" * 64
                run.input_payload = metadata
            db.flush()
            run.input_fingerprint = core._fingerprint(db, run)
            db.commit()
        calls = transport(monkeypatch, "job_match")
        with pytest.raises(ValueError):
            core.execute(factory, **values, side="candidate")
        assert not calls
    finally:
        factory.kw["bind"].dispose()


@pytest.mark.parametrize("unknown", [False, True])
def test_invalid_or_unknown_comparison_retains_cost_and_never_retries(evaluation, owned_postgres, monkeypatch, unknown):  # noqa: F811
    values = authorization(evaluation, owned_postgres, "job_match")
    factory = core.local_factory(owned_postgres["url"], values["auth"])
    try:
        core.prepare(factory, **values)
        identity = select_pair(factory, values["auth"], "synth-001")["candidate"]
        calls = transport(monkeypatch, "job_match", invalid=True, unknown=unknown)
        assert core.execute(factory, **values, side="candidate")["outcomes"] == {identity: "failed"}
        assert core.execute(factory, **values, side="candidate")["outcomes"] == {} and len(calls) == 1
        with factory() as db:
            assert db.query(models.JobMatch).count() == 0
            assert db.query(models.ModelCostLiability).one().cost_state == ("usage_unavailable" if unknown else "settled")
            assert db.get(FreeCoreReservation, "qen_" + inputs.digest(identity)[:55]).settled_minor is None
    finally:
        factory.kw["bind"].dispose()
