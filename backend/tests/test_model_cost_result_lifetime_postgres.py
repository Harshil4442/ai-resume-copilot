"""Actual post-network tailoring/enrichment writes serialize with erasure on PG."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from io import BytesIO
from threading import Event, local
from types import SimpleNamespace

import pytest
from backend.app import models
from backend.app.domains.analysis import operations, schemas, service, tasks
from backend.app.routers import resume as resume_routes
from backend.app.services import guardrails, llm_client, result_commit, resume_layout
from backend.app.services.generation_budget import current_budget
from backend.tests.test_analysis_runs import (
    SOURCE_EXPERIENCE,
    _native_source,
    _source_edit_response,
)
from backend.tests.test_model_cost_budgets import admit, usage
from backend.tests.test_model_cost_liabilities import erase
from backend.tests.test_model_cost_liabilities import no_external_calls as no_external_calls
from backend.tests.test_model_cost_liabilities_postgres import factory as factory
from backend.tests.test_model_cost_liabilities_postgres import pg_engine as pg_engine
from fastapi import HTTPException, UploadFile
from sqlalchemy import event, text
from starlette.datastructures import Headers
from starlette.requests import Request


def _gate(monkeypatch, *, order):
    roles = local()
    ready, release = Event(), Event()
    original = result_commit.begin_result_commit
    def seam(*args, **kwargs):
        if getattr(roles, "worker", False) and not getattr(roles, "used", False):
            roles.used = True
            if order == "write_first":
                original(*args, **kwargs)
                roles.guarded = True
                ready.set()
                assert release.wait(8)
                return
            ready.set()
            assert release.wait(8)
        original(*args, **kwargs)
        roles.guarded = True
    for module in (result_commit, operations, tasks, guardrails):
        monkeypatch.setattr(module, "begin_result_commit", seam)
    return roles, ready, release


def _race(factory, worker, *, order, ready, release):
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = pool.submit(worker)
        assert ready.wait(8), "worker did not reach the post-network seam"
        deleted = pool.submit(erase, factory)
        if order == "write_first":
            with pytest.raises(TimeoutError):
                deleted.result(timeout=0.1)
            release.set()
            outcome = result.result(timeout=12)
            deleted.result(timeout=12)
        else:
            deleted.result(timeout=8)
            release.set()
            outcome = result.result(timeout=12)
    return outcome


def _retained_only(factory):
    with factory() as db:
        assert db.query(models.User).count() == db.query(models.AnalysisRun).count() == 0
        assert db.query(models.Resume).count() == db.query(models.ResumeVersion).count() == 0
        assert db.query(models.ModelCallEvent).count() == db.query(models.UsageEvent).count() == 0
        liability = db.query(models.ModelCostLiability).one()
        assert liability.reserved_cost_micros == 1267 and liability.settled_cost_micros == 17


@pytest.mark.parametrize("order", ["write_first", "delete_first"])
def test_actual_tailoring_worker_result_and_unit_commit_serialize_after_render(factory, monkeypatch, order):
    with factory() as db:
        resume = db.get(models.Resume, 1)
        resume.source_document, resume.source_format = _native_source(), "docx"
        db.add(models.EvidenceItem(
            id="evd_approved", user_id=1, resume_id=1, category="experience",
            title="Synthetic platform", evidence_text=SOURCE_EXPERIENCE, skills=["python"],
            approval_state="approved", metrics={},
        ))
        db.commit()
        run, _ = service.create_run(db, user_id=1, payload=schemas.AnalysisRunCreate(
            operation="resume_tailor", opportunity_id="opp_test", input={},
        ), header_idempotency_key="synthetic-real-tailor")
        run_id = run.id
    roles, ready, release = _gate(monkeypatch, order=order)
    calls, rendered, version_writes, unit_commits = [], [], [], []
    def generation(**kwargs):
        assert not getattr(roles, "guarded", False)
        calls.append("one synthetic SDK response")
        budget = current_budget()
        record = admit(budget)
        budget.finish(record, latency_ms=1, response=usage())
        return _source_edit_response(kwargs["source_units"])
    monkeypatch.setattr(llm_client, "tailor_resume_from_evidence", generation)
    native_render = resume_layout.apply_source_edits
    def render(*args, **kwargs):
        assert not getattr(roles, "guarded", False), "render must precede result guard"
        rendered.append(True)
        return native_render(*args, **kwargs)
    monkeypatch.setattr(resume_layout, "apply_source_edits", render)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    def observe(connection, cursor, statement, parameters, context, executemany):
        if getattr(roles, "worker", False) and statement.startswith("INSERT INTO resume_versions"):
            assert roles.guarded
            version_writes.append(True)
        if getattr(roles, "worker", False) and statement.startswith("INSERT INTO usage_events") and parameters.get("event_type") == "commit":
            assert roles.guarded
            unit_commits.append(True)
    event.listen(factory.kw["bind"], "before_cursor_execute", observe)
    def worker():
        roles.worker = True
        return tasks.process_analysis_run(run_id)
    try:
        outcome = _race(factory, worker, order=order, ready=ready, release=release)
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", observe)
    assert calls == ["one synthetic SDK response"] and rendered
    if order == "write_first":
        assert len(version_writes) == len(unit_commits) == 1
        assert outcome in {"succeeded", "missing"}
    else:
        assert not version_writes and not unit_commits and outcome == "missing"
    _retained_only(factory)


@pytest.mark.parametrize("order", ["write_first", "delete_first"])
def test_actual_upload_enrichment_route_acquires_guard_before_resume_flush(factory, monkeypatch, order):
    # The fixture assigned Resume1 manually; advance only its owned local
    # schema's sequence so the actual upload handler can allocate its new row.
    with factory() as db:
        db.execute(text("SELECT setval(pg_get_serial_sequence('resumes','id'), (SELECT MAX(id) FROM resumes), true)"))
        db.commit()
    monkeypatch.setattr(resume_routes, "parse_resume_file", lambda *a, **k: (
        "Synthetic Python and SQL", {"experience": "Synthetic Python and SQL"}, ["Python"], 1.0, {},
    ))
    roles, ready, release = _gate(monkeypatch, order=order)
    calls, writes = [], []
    def enrich(raw, skills):
        assert not getattr(roles, "guarded", False)
        calls.append(True)
        budget = current_budget()
        record = admit(budget)
        budget.finish(record, latency_ms=1, response=usage())
        return ["Python", "SQL"]
    monkeypatch.setattr(resume_routes, "enrich_resume_skills", enrich)
    def observe(connection, cursor, statement, parameters, context, executemany):
        if getattr(roles, "worker", False) and statement.startswith("UPDATE resumes"):
            assert roles.guarded
            writes.append(True)
    event.listen(factory.kw["bind"], "before_cursor_execute", observe)
    def worker():
        roles.worker = True
        with factory() as db:
            upload = UploadFile(file=BytesIO(_native_source()), filename="synthetic.docx",
                                headers=Headers({"content-type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}))
            request = Request({"type": "http", "method": "POST", "path": "/resume/parse", "headers": [],
                               "client": ("127.0.0.1", 1), "app": SimpleNamespace(state=SimpleNamespace())})
            try:
                response = asyncio.run(resume_routes.parse_resume.__wrapped__(
                    request=request, file=upload, enrich_skills=True, db=db, current_user=db.get(models.User, 1),
                ))
                return response.enrichment_state
            except HTTPException as exc:
                return exc.status_code
    try:
        outcome = _race(factory, worker, order=order, ready=ready, release=release)
    finally:
        event.remove(factory.kw["bind"], "before_cursor_execute", observe)
    assert calls == [True]
    assert (outcome, len(writes)) == (("completed", 1) if order == "write_first" else (410, 0))
    _retained_only(factory)


def test_result_guard_no_autoflush_rejects_staged_customer_write_after_erasure(factory):
    # A caller may have staged memory changes. Acquiring/rechecking the seam
    # must not flush those changes before discovering the account is gone.
    with factory() as db:
        db.autoflush = True
        resume = db.get(models.Resume, 1)
        resume.skills = ["Synthetic staged change"]
        erase(factory)
        with pytest.raises(result_commit.ResultOwnerGone):
            result_commit.begin_result_commit(db, 1, "private_run_1")
        db.rollback()
    with factory() as db:
        assert db.query(models.Resume).count() == db.query(models.User).count() == 0


def test_result_guard_rechecks_cancelled_sql_state_instead_of_cached_run(factory):
    with factory() as stale, factory() as change:
        run = stale.get(models.AnalysisRun, "private_run_1")
        assert run.cancel_requested is False
        change.get(models.AnalysisRun, run.id).cancel_requested = True
        change.commit()
        with pytest.raises(HTTPException) as exc:
            result_commit.begin_result_commit(stale, 1, run.id)
        assert exc.value.status_code == 409
        stale.rollback()
    with factory() as db:
        assert db.query(models.ResumeVersion).count() == 0
