"""Actual consumer builders/callbacks with a fake persistence port, no DB/model."""
import asyncio
import contextvars
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from io import BytesIO
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from backend.app import models, schemas
from backend.app.domains.analysis import operations
from backend.app.domains.analysis import service as analysis
from backend.app.domains.analysis import supported_evaluation as evaluation
from backend.app.domains.career import transcription_review as review
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.credits import synchronous
from backend.app.routers import jobs, llm, rag, recommendations, resume
from backend.app.services import (
    generation_budget,
    matching_contract,
    provider_input_guard,
    source_transcription,
    tailoring_contract,
)
from backend.app.services.provider_input_guard import guard_provider_inputs
from backend.tests.fixtures.transcription_source import source_for_text
from backend.tests.test_source_transcription_review import (
    CORRECTED,
    ORIGINAL,
    PersistencePort,
    Query,
    payload,
)
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from starlette.requests import Request


class ConsumerQuery(Query):
    def filter_by(self, **values):
        return self.filter(*(getattr(self.model, name) == value for name, value in values.items()))

    def one_or_none(self):
        rows = self.all()
        assert len(rows) <= 1
        return next(iter(rows), None)

    def order_by(self, *args):
        return self


class ConsumerPort(PersistencePort):
    def __init__(self):
        super().__init__()
        self.resume.skills, self.resume.experience_years = ["Python"], 2
        self.resume.contact_info = {}
        self.match = models.JobMatch(id=7, user_id=1, resume_id=3, job_title="Engineer", company="Synthetic",
            job_description="Build Python APIs and documented reliable systems.", required_skills=["Python"],
            true_gaps=["Docker"], partial_matches=[], full_matches=["Python"], match_score=70,
            dimension_scores=[], improvement_tips=[], skill_verification_rate=80, fit_summary="Recorded match")
        self.rows.append(self.match)

    def query(self, model):
        return ConsumerQuery(self, model)


def request(operation):
    inputs = {"resume_enrichment": {"resume_id": 3},
        "bullet_rewrite": {"resume_id": 3, "job_description": "Build reliable Python APIs", "tone": "concise"},
        "enhanced_qa": {"job_match_id": 7, "resume_id": 3, "question": "Discuss my reviewed records", "mode": "enhanced"},
        "learning_strategy": {"match_id": 7, "mode": "enhanced"}}
    return synchronous.SynchronousServiceRequest(operation=operation, input=inputs[operation])


def approved(db):
    review.record_review(db, 1, db.item.id, payload(db))


def fake_factory(monkeypatch, db, module=synchronous):
    @contextmanager
    def session():
        yield db
    monkeypatch.setattr(module, "sessionmaker", lambda **kwargs: session)


def budget(*, before=None, preflight=None):
    return generation_budget.GenerationBudget(run_id="run-source", limit=3,
        before=before or (lambda *a, **k: {"attempt_number": 1}),
        preflight=preflight or (lambda *a, **k: 10000), after=lambda *a, **k: None)


@pytest.mark.parametrize("operation", ["resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"])
def test_unresolved_refuses_before_quote_runtime_or_supplier_admission(operation, monkeypatch):
    db = ConsumerPort()
    monkeypatch.setattr(synchronous.runtime, "current_runtime", lambda *a, **k: pytest.fail("Unreviewed source reached quote funding"))
    with pytest.raises(HTTPException) as error:
        synchronous.prepare_quote_context(db, user_id=1, payload=request(operation))
    assert error.value.detail["code"] == "transcription_review_required" and db.commits == 0


@pytest.mark.parametrize("operation", ["resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"])
def test_each_complete_prompt_uses_exact_approved_effective_text_and_binds_source_and_evidence(operation):
    db = ConsumerPort()
    approved(db)
    prepared = synchronous.owned_context(db, user_id=1, payload=request(operation))
    assert CORRECTED in prepared.context["resume"]["raw_text"]
    assert ORIGINAL not in prepared.context["resume"]["raw_text"]
    assert prepared.context["resume"]["sections"]["experience"] == CORRECTED
    assert prepared.context["transcription"]["context"]
    assert prepared.context["approved_evidence"][0]["snapshot_fingerprint"]
    assert ORIGINAL in db.resume.raw_text and db.resume.sections["experience"] == ORIGINAL
    db.item.title += " changed"
    newer = synchronous.owned_context(db, user_id=1, payload=request(operation))
    assert newer.fingerprint != prepared.fingerprint


def test_unchanged_plain_text_with_different_original_binary_invalidates_cache():
    db = ConsumerPort()
    db.resume.raw_text = CORRECTED
    db.resume.sections = {"experience": CORRECTED}
    one = synchronous.owned_context(db, user_id=1, payload=request("resume_enrichment"))
    db.resume.source_document += b"changed source bytes"
    two = synchronous.owned_context(db, user_id=1, payload=request("resume_enrichment"))
    assert one.context["resume"] == two.context["resume"] and one.fingerprint != two.fingerprint


def test_missing_original_is_an_explicit_precondition_not_empty_context():
    db = ConsumerPort()
    db.resume.source_document = None
    with pytest.raises(HTTPException) as error:
        synchronous.owned_context(db, user_id=1, payload=request("resume_enrichment"))
    assert error.value.detail["code"] == "transcription_source_required"


@pytest.mark.parametrize("stage", ["preflight", "before"])
def test_change_during_existing_native_callback_prevents_prompt_dispatch_and_keeps_existing_claim(stage, monkeypatch):
    db = ConsumerPort()
    approved(db)
    selected = request("bullet_rewrite")
    prepared = synchronous.owned_context(db, user_id=1, payload=selected)
    fake_factory(monkeypatch, db)
    claims, supplier = [], []
    def native(*args, **kwargs):
        if stage == "before":
            claims.append({"attempt_number": 1, "state": "started"})
        db.item.approval_state = "pending"
        return claims[-1] if claims else 10000
    active = budget(**{stage: native})
    originals = active.before, active.preflight, active.after
    with generation_budget.generation_budget(active), pytest.raises(HTTPException):
        with synchronous.guard_prepared_inputs(db, user_id=1, payload=selected, prepared=prepared, run_id=active.run_id):
            if stage == "preflight":
                active.preflight("google", "synthetic", prepared.messages, "synthetic")
            else:
                active.admit("google", "synthetic", prepared.messages, api_base="synthetic")
            supplier.append("must not dispatch")
    assert not supplier
    assert claims == ([{"attempt_number": 1, "state": "started"}] if stage == "before" else [])
    assert (active.before, active.preflight, active.after) == originals


def test_review_revoked_after_count_and_between_fallbacks_blocks_next_attempt(monkeypatch):
    db = ConsumerPort()
    approved(db)
    selected = request("bullet_rewrite")
    prepared = synchronous.owned_context(db, user_id=1, payload=selected)
    fake_factory(monkeypatch, db)
    active = budget()
    with generation_budget.generation_budget(active):
        with synchronous.guard_prepared_inputs(db, user_id=1, payload=selected, prepared=prepared, run_id=active.run_id):
            assert active.preflight("google", "first", prepared.messages, "synthetic") == 10000
            active.admit("google", "first", prepared.messages, api_base="synthetic")
            db.item.approval_state = "pending"
            with pytest.raises(HTTPException):
                active.preflight("google", "fallback", prepared.messages, "synthetic")
            with pytest.raises(HTTPException):
                active.admit("google", "second", prepared.messages, api_base="synthetic")
    assert len(active.attempts) == 1


def test_final_result_input_check_refuses_changed_review_before_delivery_settlement():
    db = ConsumerPort()
    approved(db)
    selected = request("bullet_rewrite")
    prepared = synchronous.owned_context(db, user_id=1, payload=selected)
    db.item.updated_at = db.item.updated_at.replace(microsecond=(db.item.updated_at.microsecond + 1) % 1000000)
    with pytest.raises(HTTPException):
        synchronous.require_current_inputs(db, user_id=1, payload=selected, prepared=prepared)


def test_matching_uses_corrected_context_while_tailoring_keeps_original_physical_text():
    db = ConsumerPort()
    approved(db)
    data = {"resume_id": 3, "mode": "enhanced", "job_title": "Engineer", "job_description": "Build reliable documented Python APIs."}
    context = operations.complete_matching_context(db, user_id=1, resume=db.resume, payload=data)
    assert CORRECTED in context["resume"]["raw_text"] and context["transcription"]["context"]
    matching = analysis._dependency_snapshot(db, 1, "job_match", None, data, paid_context=True)
    native = analysis._dependency_snapshot(db, 1, "resume_tailor", None, {"resume_id": 3}, paid_context=True)
    assert CORRECTED in matching["resume"]["text"] and native["resume"]["text"] == db.resume.raw_text
    assert native["transcription"] == matching["transcription"]


def test_callback_ownership_nesting_copy_and_tamper_fail_closed_and_restore_exact_callbacks():
    active = budget()
    originals = active.before, active.preflight, active.after
    with generation_budget.generation_budget(active):
        with guard_provider_inputs(lambda: None, run_id=active.run_id):
            copied = contextvars.copy_context()
            def overlap():
                with pytest.raises(generation_budget.GenerationBudgetExhausted):
                    with guard_provider_inputs(lambda: None, run_id=active.run_id):
                        pytest.fail("Copied context acquired a second mutable budget owner")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(copied.run, overlap).result(timeout=5)
        with pytest.raises(generation_budget.GenerationBudgetExhausted):
            with guard_provider_inputs(lambda: None, run_id="different-run"):
                pytest.fail("Cross-operation guard entered")
        with pytest.raises(generation_budget.GenerationBudgetExhausted):
            with guard_provider_inputs(lambda: None, run_id=active.run_id):
                active.before = lambda *args: {}
    assert (active.before, active.preflight, active.after) == originals


def test_no_budget_or_callback_guard_cannot_issue_provider_authority():
    with pytest.raises(generation_budget.GenerationBudgetExhausted):
        with guard_provider_inputs(lambda: None, run_id="missing"):
            pytest.fail("Missing native budget accepted")


@pytest.mark.parametrize("operation", ["bullets", "qa", "learning", "matching", "tailoring"])
def test_actual_legacy_routes_refuse_unresolved_source_before_billable_admission(operation, monkeypatch):
    db = ConsumerPort()
    db.match.resume = db.resume
    user = SimpleNamespace(id=1)
    monkeypatch.setattr(synchronous.runtime, "requires_unified_billing", lambda *a, **k: False)
    for module in (jobs, llm, rag, recommendations):
        monkeypatch.setattr(module, "billable_operation", lambda *a, **k: pytest.fail("Unresolved transcription entered a paid operation"))
    monkeypatch.setattr(rag, "ask_match_ai", lambda **kwargs: SimpleNamespace(mode="basic"))
    with pytest.raises(HTTPException) as error:
        if operation == "bullets":
            asyncio.run(llm.rewrite_bullets_endpoint(schemas.RewriteBulletsRequest(**request("bullet_rewrite").input), db, user))
        elif operation == "qa":
            rag.ask_ai_about_match(schemas.RagAskRequest(**request("enhanced_qa").input), db, user)
        elif operation == "learning":
            asyncio.run(recommendations.match_learning_strategy(schemas.LearningStrategyRequest(**request("learning_strategy").input), db, user))
        elif operation == "matching":
            jobs.match_job(schemas.JobMatchRequest(resume_id=3, job_title="Engineer", mode="enhanced",
                job_description="Build reliable Python APIs and documented systems."), db, user)
        else:
            jobs.tailor_resume(7, schemas.ResumeTailorRequest(), db, user)
    assert error.value.status_code == 422 and error.value.detail["code"] == "transcription_review_required"
    assert db.commits == 0


def test_actual_optional_parse_retains_source_and_reports_requires_review_without_enrichment(monkeypatch):
    from backend.app.domains.free_core import policy as free_policy
    from backend.app.services import generation_gate, guardrails

    db = ConsumerPort()
    db.rows = []
    def add(row):
        row.id, row.source_available = 3, True
        db.resume = row
        db.rows.append(row)
    db.add = add
    db.refresh = lambda row: None
    monkeypatch.setattr(free_policy, "enabled", lambda: False)
    monkeypatch.setattr(synchronous.runtime, "requires_unified_billing", lambda *a: False)
    monkeypatch.setattr(generation_gate, "generation_enabled", lambda: True)
    monkeypatch.setattr(resume, "inspect_resume_document", lambda *a, **k: (ORIGINAL, {"experience": ORIGINAL}, [], 0, {}))
    monkeypatch.setattr(guardrails, "billable_operation", lambda *a, **k: pytest.fail("Ambiguous parse entered enrichment reservation"))
    monkeypatch.setattr(resume, "enrich_resume_skills", lambda *a, **k: pytest.fail("Ambiguous parse dispatched enrichment"))
    source = source_for_text(ORIGINAL)
    upload = UploadFile(filename="synthetic.pdf", file=BytesIO(source), headers=Headers({"content-type": "application/pdf"}))
    http_request = Request({"type": "http", "method": "POST", "path": "/resume/parse", "headers": [], "client": ("127.0.0.1", 12345)})
    result = asyncio.run(resume.parse_resume.__wrapped__(request=http_request, file=upload, enrich_skills=True,
        db=db, current_user=SimpleNamespace(id=1)))
    assert result.enrichment_state == "requires_review" and result.extraction_mode == "deterministic"
    assert result.enrichment_units == 0 and result.sections["experience"] == ORIGINAL
    assert db.resume.source_document == source and db.commits == 1  # Source save only.


def test_interview_fact_fingerprint_path_does_not_require_or_claim_transcription_review(monkeypatch):
    db = ConsumerPort()
    db.rows.append(models.Opportunity(id="opp", user_id=1, resume_id=3, title="Engineer",
        job_description="Build reliable Python APIs.", company="Synthetic", job_snapshot={}))
    monkeypatch.setattr(review, "require_effective_transcription", lambda *a, **k: pytest.fail("Interview changed its approved-facts-only scope"))
    dependencies = analysis._dependency_snapshot(db, 1, "interview_questions", "opp", {"mode": "enhanced"})
    assert "transcription" not in dependencies
    assert all("snapshot_fingerprint" not in row for row in dependencies["approved_evidence"])


@pytest.mark.parametrize("operation", ["job_match", "resume_tailor"])
def test_actual_operation_dispatcher_rechecks_source_before_returning_result_for_commit(operation, monkeypatch):
    db = ConsumerPort()
    approved(db)
    data = {"resume_id": 3, "mode": "enhanced", "job_title": "Engineer", "job_description": "Build reliable Python APIs."}
    dependencies = analysis._dependency_snapshot(db, 1, operation, None, data, paid_context=True)
    run = models.AnalysisRun(id="run-source", user_id=1, operation=operation, input_payload=data,
        input_fingerprint=payload_fingerprint({"operation": operation, "opportunity_id": None,
            "input": data, "dependencies": dependencies}), model_cost_quote={"billing_currency": "credits"})
    fake_factory(monkeypatch, db, module=analysis)
    def generated(*args, **kwargs):
        db.item.approval_state = "pending"
        return {"synthetic": "result awaiting commit"}
    monkeypatch.setattr(operations, "execute_job_match" if operation == "job_match" else "execute_resume_tailor", generated)
    with generation_budget.generation_budget(budget()), pytest.raises(HTTPException):
        operations.execute_operation(db, run)
    assert db.commits == 1  # Earlier explicit review only, no result delivery.


def test_concurrent_copied_callbacks_cannot_both_enter_the_original_native_admission():
    entered, release = Event(), Event()
    records = []
    def original(*args, **kwargs):
        records.append({"attempt_number": 1})
        entered.set()
        assert release.wait(timeout=5)
        return records[-1]
    active = budget(before=original)
    with generation_budget.generation_budget(active), guard_provider_inputs(lambda: None, run_id=active.run_id):
        first, second = contextvars.copy_context(), contextvars.copy_context()
        with ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(first.run, active.admit, "google", "synthetic", [], api_base="synthetic")
            assert entered.wait(timeout=5)
            try:
                with pytest.raises(generation_budget.GenerationBudgetExhausted):
                    pool.submit(second.run, active.admit, "google", "synthetic", [], api_base="synthetic").result(timeout=5)
            finally:
                release.set()
            pending.result(timeout=5)
    assert records == [{"attempt_number": 1}] and len(active.attempts) == 1


def test_openai_style_budget_without_count_callback_retains_its_original_contract():
    active = budget()
    active.preflight = None
    original = active.before, active.preflight, active.after
    with generation_budget.generation_budget(active), guard_provider_inputs(lambda: None, run_id=active.run_id):
        assert active.preflight is None
        active.admit("openai", "synthetic", [], api_base="synthetic")
    assert (active.before, active.preflight, active.after) == original


def test_source_refusal_with_retry_looking_id_cannot_requeue_a_whole_model_worker():
    from backend.app.domains.analysis.tasks import _retryable

    db = ConsumerPort()
    db.resume.id = db.item.resume_id = 503
    selected = request("resume_enrichment")
    selected.input["resume_id"] = 503
    with pytest.raises(review.TranscriptionUnavailable) as error:
        synchronous.owned_context(db, user_id=1, payload=selected)
    assert "503" in str(error.value) and not _retryable(error.value)


@pytest.mark.parametrize("operation", ["job_match", "resume_tailor", "resume_enrichment", "bullet_rewrite", "enhanced_qa", "learning_strategy"])
@pytest.mark.parametrize("helper", [source_transcription, review, provider_input_guard])
def test_each_actual_helper_drift_invalidates_affected_frozen_operation_contract(operation, helper, monkeypatch):
    """Patch only a source-hash read port; no source/native authority is changed."""
    original = evaluation.packet.file_digest
    baseline = evaluation.contract_digest(operation)
    target, observed = Path(helper.__file__).resolve(), []
    def changed(path):
        path = Path(path).resolve()
        observed.append(path)
        return "0" * 64 if path == target else original(path)
    monkeypatch.setattr(evaluation.packet, "file_digest", changed)
    assert evaluation.contract_digest(operation) != baseline
    assert target in observed
    version = evaluation._sync().prompt_version(operation) if evaluation._sync().supports(operation) else (
        matching_contract.PROMPT_VERSION if operation == "job_match" else tailoring_contract.COMPLETE_PROMPT_VERSION)
    # Minimal frozen metadata for this pure refusal test is not an authorization
    # or a funding/quality receipt. _current must reject before further work.
    old_contract = SimpleNamespace(operation=operation,
        routes={"candidate": SimpleNamespace(prompt_version=version, contract_sha256=baseline)})
    with pytest.raises(ValueError, match="frozen operation"):
        evaluation._current(old_contract)


@pytest.mark.parametrize("helper", [source_transcription, review, provider_input_guard])
def test_helper_only_drift_does_not_expand_interview_existing_source_scope(helper, monkeypatch):
    baseline = evaluation.contract_digest("interview_questions")
    original = evaluation.packet.file_digest
    target, observed = Path(helper.__file__).resolve(), []
    def changed(path):
        path = Path(path).resolve()
        observed.append(path)
        return "0" * 64 if path == target else original(path)
    monkeypatch.setattr(evaluation.packet, "file_digest", changed)
    assert evaluation.contract_digest("interview_questions") == baseline
    assert target not in observed
