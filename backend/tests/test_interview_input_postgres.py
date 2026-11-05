"""Owned PG17 final-snapshot races through real worker and unified settlement.

Synthetic eight-question transport only; no native provider, model call, actual
account, or independent semantic quality qualification is supplied by this test.
"""
from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import uuid4

import pytest
from backend.app import models
from backend.app.database import Base
from backend.app.domains.analysis import operations, schemas, tasks
from backend.app.domains.analysis import service as analysis_service
from backend.app.domains.career import schemas as career_schemas
from backend.app.domains.career import service as career
from backend.app.domains.career.evidence_write_fence import flush_evidence_writes
from backend.app.domains.credits import analysis, runtime, service, service_quotes
from backend.app.domains.credits.models import CreditExecutionAttempt, CreditReservation
from backend.app.services import llm_client
from backend.app.services.delivery_contracts import current_output_contract
from backend.tests.fixtures.evidence_fence import install_owned_fence
from backend.tests.fixtures.reviewed_runtime import reviewed_runtime
from backend.tests.test_interview_input_guards import synthetic_response, synthetic_transport
from backend.tests.test_llm_client import _question_set
from backend.tests.test_unified_credits import grant
from backend.tests.test_unified_execution import budgets, financial_policy, route
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from test_mail_budget_postgres import owned_pg_url as owned_pg_url  # noqa: F401


@pytest.fixture
def pipeline(owned_pg_url, monkeypatch):
    namespace = "interview_guard_" + uuid4().hex
    admin = create_engine(owned_pg_url)
    with admin.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{namespace}"'))
    engine = create_engine(owned_pg_url, hide_parameters=True, connect_args={"options":
        f"-csearch_path={namespace} -clock_timeout=5000 -cstatement_timeout=15000 -ctimezone=UTC"})
    try:
        Base.metadata.create_all(engine)
        install_owned_fence(engine)
        factory = sessionmaker(engine, autoflush=False)
        selected = route(attempts=1).model_copy(update={"operation": "interview_questions", "credits": 5,
            "output_contract_sha256": current_output_contract("interview_questions"),
            "supported_limits": {"context_bytes": 200_000, "source_bytes": 100_000, "evidence_items": 100},
            "model_quotes": tuple(q.model_copy(update={"model": "gemini-3.6-flash", "max_input_tokens": 20_000})
                                  for q in route(attempts=1).model_quotes)})
        configured = runtime.RuntimeConfig(policy=financial_policy(), budgets=budgets(), routes=(selected,))
        reviewed_runtime(monkeypatch, configured)
        monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
        monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", configured.model_dump_json())
        monkeypatch.setenv("OPTIONAL_AI_GENERATION_ENABLED", "true")
        monkeypatch.setenv("APP_ENV", "test")
        monkeypatch.setenv("LLM_API_KEY", "synthetic-no-provider-key")
        monkeypatch.setattr(llm_client, "LLM_MODEL", "gemini-3.6-flash")
        monkeypatch.setattr(tasks, "SessionLocal", factory)
        with factory() as db:
            db.add(models.User(id=1, email="interview-owner@example.invalid", ai_credits=17))
            db.flush()
            grant(db)
            db.add(models.Resume(id=1, user_id=1, original_filename="synthetic.pdf", source_document=b"owned source",
                source_format="pdf", raw_text="Python services", skills=["Python"], sections={}))
            db.flush()
            db.add(models.Opportunity(id="opp_test", user_id=1, resume_id=1, title="Software Engineer",
                company="Synthetic", job_description="Build reliable Python services and test production behavior.", job_snapshot={}))
            db.add(models.UserProfile(user_id=1, target_role="Software Engineer"))
            fact = models.EvidenceItem(id="fact-1", user_id=1, resume_id=1, category="experience", title="Approved source",
                evidence_text="Built Python services.", approval_state="approved", provenance="user")
            db.add(fact)
            flush_evidence_writes(db, [fact])
            db.commit()
        yield factory, engine
    finally:
        engine.dispose()
        with admin.begin() as db:
            db.execute(text(f'DROP SCHEMA "{namespace}" CASCADE'))
        admin.dispose()


def create_paid_interview(factory):
    payload = schemas.AnalysisRunCreate(operation="interview_questions", opportunity_id="opp_test",
        input={"mode": "enhanced", "num_questions": 8}, idempotency_key="owned-pg-interview-001")
    with factory() as db:
        _, fingerprint, config, selected = analysis.prepare_quote_context(db, user_id=1, payload=payload)
        quote = service_quotes.issue_service_quote(db, user_id=1, input_fingerprint=fingerprint,
            idempotency_key="owned-pg-interview-quote", route=selected, policy=config.policy)
        db.commit()
        run, created = analysis_service.create_run(db, user_id=1,
            payload=payload.model_copy(update={"accepted_service_quote_id": quote.id}), header_idempotency_key=None)
        assert created
        return run.id


@pytest.mark.parametrize("mutation", ["revoke", "new_approved_fact", "approve_pending", "job", "resume", "preferences"])
def test_final_interview_snapshot_serializes_real_mutations_through_credit_commit(pipeline, monkeypatch, mutation):
    factory, engine = pipeline
    if mutation == "approve_pending":
        with factory() as db:
            fact = models.EvidenceItem(id="pending-fact", user_id=1, resume_id=1, category="experience",
                title="Pending source", evidence_text="Tested Python services.", approval_state="pending", provenance="user")
            db.add(fact)
            flush_evidence_writes(db, [fact])
            db.commit()
    run_id = create_paid_interview(factory)
    counts, generations = [], []
    # Preserve the real parser/schema and provide a citation to the exact fact.
    def transport(messages, active):
        active.preflight("google", "gemini-3.6-flash", messages, "https://generativelanguage.googleapis.com")
        counts.append("owned-count")
        event = active.admit("google", "gemini-3.6-flash", messages,
            api_base="https://generativelanguage.googleapis.com")
        generations.append("owned-generation")
        active.finish(event, latency_ms=1, response=synthetic_response())
        questions = _question_set()
        questions[0]["evidence_ids"] = ["fact-1"]
        return json.dumps(questions)
    monkeypatch.setattr(llm_client, "_chat_with_budget", transport)
    checked, release, mutating, completed = Event(), Event(), Event(), Event()
    original_record = operations._record_model_call

    def pause_after_final_check(*args, **kwargs):
        if kwargs.get("status") == "succeeded":
            checked.set()
            assert release.wait(timeout=10), "Owned release barrier expired"
        return original_record(*args, **kwargs)

    monkeypatch.setattr(operations, "_record_model_call", pause_after_final_check)
    mutation_pid = []

    def change():
        with factory() as db:
            mutation_pid.append(db.scalar(text("SELECT pg_backend_pid()")))
            mutating.set()
            if mutation in {"revoke", "approve_pending"}:
                identifier = "fact-1" if mutation == "revoke" else "pending-fact"
                item = db.get(models.EvidenceItem, identifier)
                career.update_evidence(db, 1, identifier, career_schemas.EvidenceUpdate(
                    expected_fingerprint=career_schemas.evidence_snapshot_fingerprint(item),
                    approval_state="rejected" if mutation == "revoke" else "approved"))
            elif mutation == "new_approved_fact":
                career.create_evidence(db, 1, career_schemas.EvidenceCreate(resume_id=1, category="experience",
                    title="Later fact", evidence_text="Maintained Python services.", approval_state="approved"))
            elif mutation == "job":
                career.update_opportunity(db, 1, "opp_test", career_schemas.OpportunityUpdate(
                    job_description="New role requirements added after the completed interview."))
            elif mutation == "resume":
                db.get(models.Resume, 1).source_document = b"replaced original source"
                db.commit()
            else:
                db.query(models.UserProfile).filter_by(user_id=1).one().preferred_location = "Bengaluru"
                db.commit()
            completed.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker = pool.submit(tasks.process_analysis_run, run_id)
        other = None
        try:
            assert checked.wait(timeout=10), "Worker did not reach the final locked snapshot"
            other = pool.submit(change)
            assert mutating.wait(timeout=5)
            deadline = time.monotonic() + 3
            waiting = False
            while time.monotonic() < deadline:
                with engine.connect() as observer:
                    waiting = observer.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),
                                              {"pid": mutation_pid[0]}) is True
                if waiting:
                    break
                time.sleep(.02)
            assert waiting and not completed.is_set(), "Actual concurrent mutation escaped the final snapshot locks"
            with factory() as observer:
                pending = observer.get(models.AnalysisRun, run_id)
                assert pending.status == "running" and pending.result_payload is None
        finally:
            release.set()
        assert worker.result(timeout=10) == "succeeded"
        assert other is not None and other.result(timeout=10) is None
    assert completed.is_set() and counts == ["owned-count"] and generations == ["owned-generation"]
    with factory() as db:
        result = db.get(models.AnalysisRun, run_id)
        assert len(result.result_payload["questions"]) == 8 and result.result_payload["questions"][0]["evidence_ids"] == ["fact-1"]
        assert result.usage_state == "committed" and result.committed_units == 5
        assert service.wallet(db, user_id=1).available_credits == 35
        assert db.query(CreditReservation).count() == 1 and db.get(models.User, 1).ai_credits == 17


def test_retained_mismatched_resume_refuses_before_supplier_and_refunds(pipeline, monkeypatch):
    factory, _ = pipeline
    with factory() as db:
        db.add(models.Resume(id=2, user_id=1, raw_text="Second owned source", source_document=b"second", source_format="pdf"))
        db.commit()
    run_id = create_paid_interview(factory)
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        payload = dict(run.input_payload, resume_id=2)
        # Even an internally consistent retained fingerprint may not make the
        # generator's opportunity source differ from its fingerprint source.
        run.input_payload = payload
        run.input_fingerprint = analysis_service._current_input_fingerprint(db, run, payload)
        db.commit()
    monkeypatch.setattr(llm_client, "_chat_with_budget", lambda *_: pytest.fail("Mismatched sources cannot dispatch"))
    from backend.app.services.interview_contract import InterviewInputsChanged
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        with pytest.raises(InterviewInputsChanged, match="selection differs"):
            operations.execute_interview_questions(db, user_id=1, run=run, payload=dict(run.input_payload))
        db.rollback()
    assert tasks.process_analysis_run(run_id) == "failed"
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert run.result_payload is None and run.committed_units == 0 and run.usage_state == "released"
        assert service.wallet(db, user_id=1).available_credits == 40
        assert all(attempt.started_at is None for attempt in db.query(CreditExecutionAttempt).all())
        assert all(row.settled_cost_micros == 0 for row in db.query(models.ModelCostLiability).all())


@pytest.mark.parametrize("older_order", ["review_evidence_first", "import_resume_first"])
def test_preexisting_writer_order_declines_without_deadlock_provider_replay_or_charge(pipeline, monkeypatch, older_order):
    factory, _ = pipeline
    run_id = create_paid_interview(factory)
    counts, generations = [], []
    synthetic_transport(monkeypatch, counts=counts, generations=generations)
    held, finish = Event(), Event()

    def older_writer():
        with factory() as db:
            # Actual PG row locks in the unchanged review/import ordering. The
            # owned pause exposes the existing in-flight writer, not a new
            # global writer protocol or a fabricated transcription review ACK.
            if older_order == "review_evidence_first":
                db.query(models.EvidenceItem).filter_by(id="fact-1").with_for_update().one()
            else:
                db.query(models.Resume).filter_by(id=1).with_for_update().one()
            held.set()
            assert finish.wait(timeout=10)
            if older_order == "review_evidence_first":
                db.query(models.Resume).filter_by(id=1).with_for_update().one()
                db.rollback()
            else:
                career.create_evidence(db, 1, career_schemas.EvidenceCreate(resume_id=1, category="experience",
                    title="Imported after pause", evidence_text="Maintained Python services.", approval_state="pending"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        writer = pool.submit(older_writer)
        try:
            assert held.wait(timeout=5)
            worker = pool.submit(tasks.process_analysis_run, run_id)
            assert worker.result(timeout=5) == "failed"
            # Worker finishes while the old writer still owns its first row:
            # this is a NOWAIT refusal, not a deadlock timeout or broad retry.
            assert not finish.is_set()
        finally:
            finish.set()
        writer.result(timeout=5)
    assert counts == ["owned-count"] and generations == ["owned-generation"]
    assert tasks.process_analysis_run(run_id) == "failed"
    assert generations == ["owned-generation"]
    with factory() as db:
        run = db.get(models.AnalysisRun, run_id)
        assert run.error_code == "InterviewInputsChanged" and "being edited" in run.error_message
        assert run.result_payload is None and run.usage_state == "released" and run.committed_units == 0
        assert service.wallet(db, user_id=1).available_credits == 40
        assert db.query(models.ModelCostLiability).count() == 1
