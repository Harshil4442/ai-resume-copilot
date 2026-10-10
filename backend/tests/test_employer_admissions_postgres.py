"""Opt-in evidence against a disposable PostgreSQL database, never production.

HIREWIZ_TEST_POSTGRES_URL must point to the task's local admission test database.
Each test gets its own schema; no other database/schema is dropped or modified.
"""
from __future__ import annotations

import copy
import hashlib
import os
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path
from threading import Barrier, Event, get_ident
from uuid import uuid4

import pytest
from alembic.config import Config
from backend.app import models as core
from backend.app.database import get_db
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import (
    batches,
    connectors,
    credits,
    models,
    schemas,
    service,
    tasks,
)
from backend.app.domains.entitlements import lock_entitlement_owner
from backend.app.domains.usage import service as usage_service
from backend.app.routers.v1 import admin as admin_routes
from backend.app.routers.v1.employer import router
from backend.app.security import get_current_user
from backend.app.services.guardrails import billable_operation
from backend.tests import test_employer_admissions as checks
from backend.tests import test_employer_services as original
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, create_engine, event, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command


@pytest.fixture
def pg_engine():
    configured = os.getenv("HIREWIZ_TEST_POSTGRES_URL")
    if not configured:
        pytest.skip("Set HIREWIZ_TEST_POSTGRES_URL for disposable PostgreSQL admission evidence")
    url = make_url(configured)
    if (url.host != "127.0.0.1" or url.port != 55433 or url.database != "hirewiz_admission_test"):
        pytest.fail("PostgreSQL admission tests accept only the dedicated local disposable database")
    schema = "admission_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_engine(url, connect_args={
        "options": f"-csearch_path={schema} -clock_timeout=4000 -cstatement_timeout=10000",
    })
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def pg_context(pg_engine, monkeypatch):
    # Reuse the existing synthetic employer/form fixtures, not real credentials.
    source_factory, _, content = original.context.__wrapped__(monkeypatch)
    _migrate(pg_engine, "head")
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    with source_factory() as source, factory() as target:
        for model in (core.User, core.Resume, models.EmployerSource, models.EmployerPosting):
            for row in source.query(model).all():
                target.add(model(**{column.name: copy.deepcopy(getattr(row, column.name))
                                   for column in model.__table__.columns}))
            target.commit()
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def database():
        with factory() as db:
            yield db

    def owner(db=Depends(get_db)):
        # Production auth and the mutation share this identity map. Detached
        # fixture users cannot reproduce a stale authenticated balance.
        return db.get(core.User, 1)

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    with TestClient(app) as client:
        yield factory, client, content


def test_concurrent_daily_admissions_accept_exactly_one_slot(pg_context, monkeypatch):
    monkeypatch.setenv("EMPLOYER_CANDIDATE_DAILY_LIMIT", "1")
    factory, _, _ = pg_context
    applications = [original._prepare(pg_context), original._prepare(pg_context, job="job_2")]
    start = Barrier(2)

    def queue(app):
        with factory() as db:
            start.wait(timeout=5)
            try:
                return service.request_execution(db, 1, app["id"], schemas.ExecuteCreate(
                    package_digest=app["package_digest"])).status
            except HTTPException as exc:
                db.rollback()
                return exc.detail["code"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(queue, applications))
    assert sorted(outcomes) == ["candidate_daily_limit", "queued"]
    with factory() as db:
        assert db.query(models.EmployerAdmission).count() == 1
        assert db.query(models.ServiceCreditReservation).count() == 1
        assert db.query(DispatchOutbox).filter_by(topic="employer.apply").count() == 1
        assert db.get(core.User, 1).job_service_credits == 45


def test_concurrent_replay_reserves_one_admission_and_money_event(pg_context):
    factory, _, _ = pg_context
    app = original._prepare(pg_context)
    start = Barrier(2)

    def queue(_):
        with factory() as db:
            start.wait(timeout=5)
            return service.request_execution(db, 1, app["id"], schemas.ExecuteCreate(
                package_digest=app["package_digest"])).status

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(queue, range(2))) == ["queued", "queued"]
    with factory() as db:
        assert db.query(models.EmployerAdmission).count() == 1
        assert db.query(models.ServiceCreditReservation).count() == 1
        assert db.query(models.ServiceCreditEvent).filter_by(event_type="reserve").count() == 1


def test_cached_authenticated_owners_cannot_reserve_the_same_last_credits_twice(pg_context):
    factory, _, _ = pg_context
    with factory() as db:
        db.get(core.User, 1).job_service_credits = 5
        db.commit()
    start = Barrier(2)

    def reserve_last(index):
        with factory() as db:
            authenticated = db.get(core.User, 1)  # Keep the request's identity alive.
            assert authenticated.job_service_credits == 5
            start.wait(timeout=5)
            try:
                credits.reserve(db, user_id=authenticated.id, operation="job_application",
                                source_id=f"synthetic-last-{index}", unit_price=5, count=1)
                db.commit()
                return "reserved"
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 402 and exc.detail["balance"] == 0
                return "insufficient_service_credits"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve_last, range(2))) == ["insufficient_service_credits", "reserved"]
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 0
        assert db.query(models.ServiceCreditReservation).count() == 1
        events = db.query(models.ServiceCreditEvent).all()
        assert len(events) == 1 and events[0].amount == -5 and events[0].balance_after == 0


@pytest.mark.parametrize("first_action", ["reserve", "release"])
def test_cached_authenticated_money_owner_preserves_competing_reserve_and_release(pg_context, first_action):
    factory, _, _ = pg_context
    with factory() as db:
        db.get(core.User, 1).job_service_credits = 10
        reservation = credits.reserve(db, user_id=1, operation="job_application",
                                      source_id="synthetic-refund", unit_price=5, count=1)
        reservation_id = reservation.id
        db.commit()
    start, first_done = Barrier(2), Event()

    def mutate(action):
        with factory() as db:
            authenticated = db.get(core.User, 1)
            assert authenticated.job_service_credits == 5
            start.wait(timeout=5)
            if action != first_action:
                assert first_done.wait(timeout=5)
            if action == "reserve":
                credits.reserve(db, user_id=authenticated.id, operation="job_application",
                                source_id="synthetic-new", unit_price=5, count=1)
            else:
                held = db.query(models.ServiceCreditReservation).filter_by(id=reservation_id).with_for_update().one()
                credits.settle(db, held, completed_count=0, reason="Synthetic pre-send cancellation")
            db.commit()
            if action == first_action:
                first_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mutate, action) for action in ("reserve", "release")]
        for future in futures:
            future.result(timeout=10)
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 5
        released = db.get(models.ServiceCreditReservation, reservation_id)
        assert released.state == "settled" and released.released_amount == 5
        last = db.query(models.ServiceCreditEvent).order_by(models.ServiceCreditEvent.created_at.desc()).first()
        assert last.balance_after == 5
        assert sum(row.amount for row in db.query(models.ServiceCreditEvent)) == -5


def test_competing_cached_authenticated_refunds_preserve_both_releases(pg_context):
    factory, _, _ = pg_context
    with factory() as db:
        db.get(core.User, 1).job_service_credits = 10
        ids = [credits.reserve(db, user_id=1, operation="job_application",
                               source_id=f"synthetic-refund-{index}", unit_price=5, count=1).id
               for index in range(2)]
        db.commit()
    start = Barrier(2)

    def release(identity):
        with factory() as db:
            authenticated = db.get(core.User, 1)
            assert authenticated.job_service_credits == 0
            held = db.query(models.ServiceCreditReservation).filter_by(id=identity).with_for_update().one()
            start.wait(timeout=5)
            credits.settle(db, held, completed_count=0, reason="Synthetic pre-send cancellation")
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(release, ids))
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 10
        releases = db.query(models.ServiceCreditEvent).filter_by(event_type="release").all()
        assert len(releases) == 2 and {row.balance_after for row in releases} == {5, 10}
        assert sum(row.amount for row in db.query(models.ServiceCreditEvent)) == 0


@pytest.mark.parametrize("balance, expected_status", [(5, 402), (10, 200)])
def test_batch_pending_debits_preserve_balance_and_insufficient_batch_rolls_back(pg_context, balance, expected_status):
    factory, client, _ = pg_context
    apps = [original._prepare(pg_context), original._prepare(pg_context, job="job_2")]
    batch = checks._batch(client, apps).json()
    assert checks._batch_action(client, batch, "approve").status_code == 200
    with factory() as db:
        db.get(core.User, 1).job_service_credits = balance
        db.commit()
    response = checks._batch_action(client, batch, "execute")
    assert response.status_code == expected_status, response.text
    accepted = expected_status == 200
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == (0 if accepted else balance)
        assert db.query(models.EmployerAdmission).count() == (2 if accepted else 0)
        assert db.query(models.ServiceCreditReservation).count() == (2 if accepted else 0)
        assert db.query(models.ServiceCreditEvent).count() == (2 if accepted else 0)
        assert db.query(DispatchOutbox).filter_by(topic="employer.apply").count() == (2 if accepted else 0)
        assert {row.status for row in db.query(models.EmployerApplication)} == ({"queued"} if accepted else {"approved"})
        assert db.get(models.EmployerApplicationBatch, batch["id"]).status == ("queued" if accepted else "approved")


def _analysis_run(db, identity, *, flush=True):
    run = core.AnalysisRun(id=identity, user_id=1, operation="job_match",
                           idempotency_key=identity, input_fingerprint="a" * 64,
                           input_payload={}, estimated_units=1, usage_state="pending")
    db.add(run)
    if flush:
        db.flush([run])
    return run


def test_cached_authenticated_owners_cannot_reserve_the_same_last_analysis_unit_twice(pg_context):
    factory, _, _ = pg_context
    with factory() as db:
        owner = db.get(core.User, 1)
        owner.tier, owner.ai_credits = "free", 1
        db.commit()
    start = Barrier(2)

    def reserve_last(index):
        with factory() as db:
            authenticated = db.get(core.User, 1)
            assert authenticated.ai_credits == 1
            run = _analysis_run(db, f"synthetic-last-analysis-{index}", flush=False)
            start.wait(timeout=5)
            try:
                usage_service.reserve_run_usage(db, user_id=authenticated.id, run=run, units=1)
                # The real create_run path also inserts only after its owner
                # lock; child FK inserts must not precede that locking boundary.
                db.flush([run])
                db.commit()
                return "reserved"
            except usage_service.InsufficientUnitsError as exc:
                db.rollback()
                assert exc.balance == 0 and exc.required == 1
                return "insufficient"

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve_last, range(2))) == ["insufficient", "reserved"]
    with factory() as db:
        assert db.get(core.User, 1).ai_credits == 0
        assert db.query(core.AnalysisRun).count() == 1
        event = db.query(core.UsageEvent).one()
        assert event.event_type == "reserve" and event.amount == -1 and event.balance_after == 0


@pytest.mark.parametrize("first_action", ["reserve", "release"])
def test_cached_analysis_owner_preserves_competing_reserve_and_release(pg_context, first_action):
    factory, _, _ = pg_context
    with factory() as db:
        owner = db.get(core.User, 1)
        owner.tier, owner.ai_credits = "free", 2
        old = _analysis_run(db, "synthetic-refunded-analysis")
        usage_service.reserve_run_usage(db, user_id=1, run=old, units=1)
        db.commit()
    start, first_done = Barrier(2), Event()

    def mutate(action):
        with factory() as db:
            authenticated = db.get(core.User, 1)
            assert authenticated.ai_credits == 1
            start.wait(timeout=5)
            if action != first_action:
                assert first_done.wait(timeout=5)
            if action == "reserve":
                run = _analysis_run(db, "synthetic-new-analysis")
                usage_service.reserve_run_usage(db, user_id=authenticated.id, run=run, units=1)
            else:
                run = db.query(core.AnalysisRun).filter_by(id="synthetic-refunded-analysis").with_for_update().one()
                usage_service.release_run_usage(db, run, reason="Synthetic pre-send cancellation")
            db.commit()
            if action == first_action:
                first_done.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(mutate, action) for action in ("reserve", "release")]
        for future in futures:
            future.result(timeout=10)
    with factory() as db:
        assert db.get(core.User, 1).ai_credits == 1
        assert db.get(core.AnalysisRun, "synthetic-refunded-analysis").usage_state == "released"
        events = db.query(core.UsageEvent).order_by(core.UsageEvent.created_at).all()
        assert len(events) == 3 and sum(row.amount for row in events) == -1
        assert events[-1].balance_after == 1


def test_competing_cached_analysis_refunds_preserve_both_releases(pg_context):
    factory, _, _ = pg_context
    with factory() as db:
        owner = db.get(core.User, 1)
        owner.tier, owner.ai_credits = "free", 2
        ids = [f"synthetic-analysis-refund-{index}" for index in range(2)]
        for identity in ids:
            usage_service.reserve_run_usage(db, user_id=1, run=_analysis_run(db, identity), units=1)
        db.commit()
    start = Barrier(2)

    def release(identity):
        with factory() as db:
            authenticated = db.get(core.User, 1)
            assert authenticated.ai_credits == 0
            run = db.query(core.AnalysisRun).filter_by(id=identity).with_for_update().one()
            start.wait(timeout=5)
            usage_service.release_run_usage(db, run, reason="Synthetic cancellation")
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(release, ids))
    with factory() as db:
        assert db.get(core.User, 1).ai_credits == 2
        refunds = db.query(core.UsageEvent).filter_by(event_type="release").all()
        assert len(refunds) == 2 and {row.balance_after for row in refunds} == {1, 2}
        assert sum(row.amount for row in db.query(core.UsageEvent)) == 0


@pytest.mark.parametrize("cached_tier,current_tier,state", [("premium", "free", "reserved"), ("free", "premium", "waived")])
def test_cached_auth_premium_decision_uses_current_locked_entitlement(pg_context, cached_tier, current_tier, state):
    factory, _, _ = pg_context
    with factory() as db:
        owner = db.get(core.User, 1)
        owner.tier, owner.ai_credits = cached_tier, 1
        db.commit()
    with factory() as request:
        authenticated = request.get(core.User, 1)
        assert authenticated.tier == cached_tier
        with factory() as grant:
            owner = grant.query(core.User).filter_by(id=1).with_for_update().one()
            owner.tier = current_tier
            grant.commit()
        run = _analysis_run(request, "synthetic-entitlement-change")
        usage_service.reserve_run_usage(request, user_id=authenticated.id, run=run, units=1)
        assert run.usage_state == state
        request.commit()
    with factory() as db:
        assert db.get(core.User, 1).ai_credits == (0 if state == "reserved" else 1)
        assert db.query(core.UsageEvent).one().event_type == ("reserve" if state == "reserved" else "waive")


def test_entitlement_lock_never_autoflushes_unrelated_pending_runs(pg_context):
    factory, _, _ = pg_context
    with factory(autoflush=True) as db:
        authenticated = db.get(core.User, 1)
        run = _analysis_run(db, "synthetic-no-early-fk", flush=False)
        assert lock_entitlement_owner(db, authenticated.id) is authenticated
        assert run in db.new
        with db.no_autoflush:
            assert db.query(core.AnalysisRun).count() == 0
        db.rollback()


def test_cached_self_admin_adjustment_uses_post_reservation_balance_and_is_audited(pg_context, monkeypatch):
    factory, _, _ = pg_context
    monkeypatch.setenv("ADMIN_EMAILS", "ada@example.com")
    with factory() as db:
        owner = db.get(core.User, 1)
        owner.tier, owner.ai_credits = "free", 1
        db.commit()
    with factory() as request:
        authenticated = request.get(core.User, 1)
        assert authenticated.ai_credits == 1
        administrator = admin_routes.require_admin(authenticated)
        with factory() as competing:
            run = _analysis_run(competing, "synthetic-before-self-adjustment")
            usage_service.reserve_run_usage(competing, user_id=1, run=run, units=1)
            competing.commit()
        payload = admin_routes.UsageAdjustment(user_id=1, amount=1, reason="Synthetic bounded support adjustment")
        result = admin_routes.adjust_usage(payload, Request({"type": "http", "headers": []}),
                                          idempotency_key="synthetic-self-adjustment", db=request, admin=administrator)
        assert result["balance"] == 1
        replay = admin_routes.adjust_usage(payload, Request({"type": "http", "headers": []}),
                                          idempotency_key="synthetic-self-adjustment", db=request, admin=administrator)
        assert replay["balance"] == 1 and replay["idempotent_replay"] is True
    with factory() as db:
        assert db.get(core.User, 1).ai_credits == 1
        ledger = db.query(core.UsageEvent).order_by(core.UsageEvent.created_at).all()
        assert [row.event_type for row in ledger] == ["reserve", "adjust"]
        assert [row.balance_after for row in ledger] == [0, 1]
        assert sum(row.amount for row in ledger) == 0
        audit = db.query(core.AdminAuditEvent).one()
        assert audit.before_state == {"analysis_units": 0}
        assert audit.after_state["analysis_units"] == 1


def test_concurrent_cached_auth_legacy_operations_lock_owner_before_fk_and_only_yield_once(pg_context, pg_engine):
    factory, _, _ = pg_context
    with factory() as db:
        owner = db.get(core.User, 1)
        owner.tier, owner.ai_credits = "free", 1
        db.commit()
    start, statements, provider_yields = Barrier(2), {}, []

    def observe(_connection, _cursor, statement, _parameters, _context, _many):
        if "FROM users" in statement and "FOR UPDATE" in statement:
            statements.setdefault(get_ident(), []).append("owner_lock")
        elif statement.startswith("INSERT INTO analysis_runs"):
            prior = statements.setdefault(get_ident(), [])
            assert "owner_lock" in prior, "Legacy child FK must be inserted after the owner lock"
            prior.append("child_insert")

    def run_operation(index):
        with factory() as db:
            authenticated = db.get(core.User, 1)
            assert authenticated.ai_credits == 1
            start.wait(timeout=5)
            try:
                with billable_operation(user_id=authenticated.id, db=db,
                                        operation="synthetic_legacy", amount=1):
                    provider_yields.append(index)  # No provider/network request.
                return "performed"
            except HTTPException as exc:
                assert exc.status_code == 402
                return "insufficient"

    event.listen(pg_engine, "after_cursor_execute", observe)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            assert sorted(pool.map(run_operation, range(2))) == ["insufficient", "performed"]
        assert len(provider_yields) == 1
        assert all(events.index("owner_lock") < events.index("child_insert") for events in statements.values())
        with factory() as db:
            assert db.get(core.User, 1).ai_credits == 0
            saved = db.query(core.AnalysisRun).one()
            assert saved.status == "succeeded" and saved.usage_state == "committed"
            ledger = db.query(core.UsageEvent).order_by(core.UsageEvent.created_at).all()
            assert [row.event_type for row in ledger] == ["reserve", "commit"]
            assert sum(row.amount for row in ledger) == -1
    finally:
        event.remove(pg_engine, "after_cursor_execute", observe)


def test_committed_batch_cancel_cannot_be_revived_by_an_earlier_cached_approve(pg_context, monkeypatch):
    factory, client, _ = pg_context
    apps = [original._prepare(pg_context), original._prepare(pg_context, job="job_2")]
    batch = checks._batch(client, apps).json()
    cached, cancelled = Event(), Event()
    approval_thread = {}
    original_applications = batches._applications

    def pause_after_batch_read(db, user_id, items):
        if get_ident() == approval_thread.get("identity"):
            cached.set()
            assert cancelled.wait(timeout=5)
        return original_applications(db, user_id, items)

    def approve():
        approval_thread["identity"] = get_ident()
        with factory() as db:
            try:
                batches.approve(db, 1, batch["id"], schemas.ExecuteCreate(package_digest=batch["package_digest"]))
                return "approved"
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 409
                return exc.detail["code"]

    monkeypatch.setattr(batches, "_applications", pause_after_batch_read)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(approve)
        assert cached.wait(timeout=5)
        try:
            with factory() as db:
                batches.cancel(db, 1, batch["id"])
        finally:
            cancelled.set()
        assert future.result(timeout=10) == "batch_expired_or_cancelled"
    with factory() as db:
        saved = db.get(models.EmployerApplicationBatch, batch["id"])
        assert saved.status == "cancelled" and saved.cancelled_at is not None
        assert all(app.batch_id is None for app in db.query(models.EmployerApplication))
        assert db.query(models.EmployerApplicationApproval).count() == 2
        assert db.query(models.ServiceCreditReservation).count() == 0
        assert db.query(models.EmployerAdmission).count() == 0
        assert db.query(DispatchOutbox).count() == 0


def test_external_form_and_post_hold_no_application_or_candidate_lock_and_late_cancel_settles(pg_context, monkeypatch):
    factory, client, _ = pg_context
    app = original._prepare(pg_context)
    assert checks._queue(client, app).status_code == 200
    phases = []

    def form(*_args, **_kwargs):
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            locked = service._owned(db, models.EmployerApplication, app["id"], 1, lock=True)
            db.query(core.User).filter_by(id=1).with_for_update().one()
            assert locked.status == "queued"
            phases.append("form")
        return dict(original.FORM)

    def send(*_args, **_kwargs):
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            cancelled = service.cancel_application(db, 1, app["id"])
            assert cancelled.status == "submitting" and cancelled.cancel_requested
            assert db.query(models.EmployerAdmission).one().state == "unknown"
            phases.append("post")
        return 201, {"application_id": "verified-late-receipt", "completion_verified": True}

    monkeypatch.setattr(connectors, "load_form", form)
    monkeypatch.setattr(connectors, "submit_greenhouse", send)
    assert tasks.execute_application(app["id"]) == "confirmed"
    assert phases == ["form", "post"]
    with factory() as db:
        assert db.query(models.EmployerAdmission).one().state == "consumed"
        assert db.query(models.EmployerAdmission).one().active_key is not None
        assert db.get(core.User, 1).job_service_credits == 45
        assert db.query(models.ServiceCreditReservation).one().committed_amount == 5


def test_batch_execute_racing_account_deletion_does_not_deadlock_or_leave_launches(pg_context):
    factory, client, _ = pg_context
    applications = [original._prepare(pg_context), original._prepare(pg_context, job="job_2")]
    batch = checks._batch(client, applications).json()
    assert checks._batch_action(client, batch, "approve").status_code == 200
    start = Barrier(2)

    def queue():
        with factory() as db:
            start.wait(timeout=5)
            try:
                return batches.execute(db, 1, batch["id"], schemas.ExecuteCreate(
                    package_digest=batch["package_digest"])).status
            except HTTPException as exc:
                db.rollback()
                assert exc.status_code == 404
                return "deleted_before_acceptance"

    def delete():
        with factory() as db:
            start.wait(timeout=5)
            service.delete_account_data(db, 1)
            db.commit()
            return "deleted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(queue), pool.submit(delete)]
        assert futures[1].result(timeout=15) == "deleted"
        assert futures[0].result(timeout=15) in {"queued", "deleted_before_acceptance"}
    with factory() as db:
        assert db.query(models.EmployerApplication).count() == 0
        assert db.query(models.EmployerApplicationBatch).count() == 0
        assert db.get(core.User, 1).job_service_credits == 50
        assert all(row.state == "released" and row.user_id is None for row in db.query(models.EmployerAdmission))
    for app in applications:
        assert tasks.execute_application(app["id"]) == "missing"


def test_cancellation_during_network_preflight_fences_post_and_returns_reservation(pg_context, monkeypatch):
    factory, client, _ = pg_context
    app = original._prepare(pg_context)
    checks._queue(client, app)
    checking, resume = Event(), Event()

    def form(*_args, **_kwargs):
        checking.set()
        assert resume.wait(timeout=5)
        return dict(original.FORM)

    monkeypatch.setattr(connectors, "load_form", form)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: pytest.fail("cancelled preflight must not POST"))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(tasks.execute_application, app["id"])
        assert checking.wait(timeout=5)
        try:
            assert client.post(f"/api/v1/employer-jobs/applications/{app['id']}/cancel").status_code == 200
        finally:
            resume.set()
        assert future.result(timeout=10) == "cancelled"
    with factory() as db:
        assert db.query(models.EmployerAdmission).one().state == "released"
        assert db.query(models.EmployerApplicationAttempt).count() == 0
        assert db.get(core.User, 1).job_service_credits == 50


def _erase_fixture_owner(db):
    service.delete_account_data(db, 1)
    db.query(core.Resume).filter_by(user_id=1).delete(synchronize_session=False)
    db.query(core.User).filter_by(id=1).delete(synchronize_session=False)
    db.commit()


def test_account_erasure_fences_creation_after_its_initial_application_scan(pg_context, pg_engine, monkeypatch):
    factory, _, _ = pg_context
    scanned, proceed, form_ready = Event(), Event(), Event()
    threads = {}

    def after_execute(_connection, _cursor, statement, _parameters, _context, _many):
        if (get_ident() == threads.get("erase") and statement.lstrip().startswith("SELECT")
                and "FROM employer_applications" in statement and "FOR UPDATE" in statement
                and not scanned.is_set()):
            scanned.set()
            assert proceed.wait(timeout=10)

    def form(*_args, **_kwargs):
        form_ready.set()
        return dict(original.FORM)

    def erase():
        threads["erase"] = get_ident()
        with factory() as db:
            _erase_fixture_owner(db)

    def create():
        with factory() as db:
            try:
                service.create_application(db, 1, schemas.ApplicationCreate(
                    posting_id="job_1", resume_id=10, idempotency_key="create-during-erasure"))
                return "created"
            except HTTPException as exc:
                db.rollback()
                return exc.status_code

    monkeypatch.setattr(connectors, "load_form", form)
    event.listen(pg_engine, "after_cursor_execute", after_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            deletion = pool.submit(erase)
            assert scanned.wait(timeout=5)
            creation = pool.submit(create)
            try:
                assert form_ready.wait(timeout=5)
                # Erasure has not locked the candidate yet. Creation must still
                # wait rather than insert a row missing from the frozen ID set.
                with pytest.raises(TimeoutError):
                    creation.result(timeout=0.25)
            finally:
                proceed.set()
            deletion.result(timeout=10)
            assert creation.result(timeout=10) == 409
        with factory() as db:
            assert db.get(core.User, 1) is None
            assert db.query(models.EmployerApplication).count() == 0
            assert db.query(models.SealedApplicationArtifact).count() == 0
    finally:
        proceed.set()
        event.remove(pg_engine, "after_cursor_execute", after_execute)


def test_account_erasure_waits_for_creation_before_freezing_application_ids(pg_context, pg_engine):
    factory, _, _ = pg_context
    owner_locked, proceed, deletion_started, deletion_scanned = Event(), Event(), Event(), Event()
    threads = {}

    def after_execute(_connection, _cursor, statement, _parameters, _context, _many):
        if not statement.lstrip().startswith("SELECT") or "FOR UPDATE" not in statement:
            return
        if (get_ident() == threads.get("create") and "FROM users" in statement
                and not owner_locked.is_set()):
            owner_locked.set()
            assert proceed.wait(timeout=10)
        if get_ident() == threads.get("erase") and "FROM employer_applications" in statement:
            deletion_scanned.set()

    def create():
        threads["create"] = get_ident()
        with factory() as db:
            service.create_application(db, 1, schemas.ApplicationCreate(
                posting_id="job_1", resume_id=10, idempotency_key="create-before-erasure"))
            return "created"

    def erase():
        threads["erase"] = get_ident()
        deletion_started.set()
        with factory() as db:
            _erase_fixture_owner(db)

    event.listen(pg_engine, "after_cursor_execute", after_execute)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            creation = pool.submit(create)
            assert owner_locked.wait(timeout=5)
            deletion = pool.submit(erase)
            try:
                assert deletion_started.wait(timeout=5)
                assert not deletion_scanned.wait(timeout=0.25)
            finally:
                proceed.set()
            assert creation.result(timeout=10) == "created"
            deletion.result(timeout=10)
        assert deletion_scanned.is_set()
        with factory() as db:
            assert db.get(core.User, 1) is None
            assert db.query(models.EmployerApplication).count() == 0
            assert db.query(models.SealedApplicationArtifact).count() == 0
    finally:
        proceed.set()
        event.remove(pg_engine, "after_cursor_execute", after_execute)


def test_account_deletion_after_possible_send_retains_anonymous_hold(pg_context, monkeypatch):
    factory, client, _ = pg_context
    app = original._prepare(pg_context)
    checks._queue(client, app)

    def send(*_args, **_kwargs):
        with factory() as db:
            db.execute(text("SET LOCAL lock_timeout = '500ms'"))
            service.delete_account_data(db, 1)
            db.commit()
        return 200, None

    monkeypatch.setattr(connectors, "submit_greenhouse", send)
    assert tasks.execute_application(app["id"]) == "missing"
    with factory() as db:
        admission = db.query(models.EmployerAdmission).one()
        assert admission.state == "submitting" and admission.possible_send_at is not None
        assert admission.user_id is admission.application_id is admission.active_key is None
        reservation = db.query(models.ServiceCreditReservation).one()
        assert reservation.user_id is None and reservation.state == "reserved"
        assert db.query(models.EmployerApplication).count() == 0
        assert db.query(models.EmployerApplicationAttempt).count() == 0


def _migrate(engine, revision, direction="upgrade"):
    root = Path(__file__).resolve().parents[1]
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "alembic"))
    with engine.begin() as connection:
        cfg.attributes["connection"] = connection
        getattr(command, direction)(cfg, revision)


def test_postgres_clean_upgrade_and_populated_round_trip_preserve_unknown_holds(pg_engine, monkeypatch):
    # Seed the historical schema itself. Current service calls correctly write
    # expense snapshots whose 0013 downgrade guard must not be bypassed.
    _migrate(pg_engine, "20261008_0008")
    source_factory, source_client, content = original.context.__wrapped__(monkeypatch)
    metadata = MetaData()
    metadata.reflect(bind=pg_engine)
    assert "cost_policy_snapshot" not in metadata.tables["service_credit_reservations"].c
    assert "opening_key" not in metadata.tables["employer_applications"].c
    now = original.utcnow()
    app = {"id": "legacy_application"}
    digest = hashlib.sha256(content).hexdigest()
    try:
        with source_factory() as source, pg_engine.begin() as connection:
            for model in (core.User, core.Resume, models.EmployerSource, models.EmployerPosting):
                table = metadata.tables[model.__tablename__]
                for row in source.query(model).all():
                    values = {column.name: copy.deepcopy(getattr(row, column.name)) for column in table.c}
                    if model is core.User and row.id == 1:
                        values["job_service_credits"] = 43  # 50 - two deliveries - five held application credits.
                    if model is models.EmployerPosting:
                        values["requisition_id"] = "legacy-requisition-alias"
                    connection.execute(table.insert().values(**values))
            reservations = metadata.tables["service_credit_reservations"]
            for identifier, operation, source_id, price, count, state, committed in (
                ("legacy_search_reservation", "job_search", "legacy_search", 1, 2, "settled", 2),
                ("legacy_application_reservation", "job_application", app["id"], 5, 1, "reserved", 0),
            ):
                connection.execute(reservations.insert().values(
                    id=identifier, user_id=1, operation=operation, source_id=source_id,
                    unit_price=price, requested_count=count, reserved_amount=price * count,
                    committed_amount=committed, released_amount=0, state=state,
                    pricing_version="legacy-0008", created_at=now, settled_at=now if state == "settled" else None,
                ))
            connection.execute(metadata.tables["employer_searches"].insert().values(
                id="legacy_search", user_id=1, resume_id=10, idempotency_key="legacy-deliveries",
                input_fingerprint="a" * 64, query={"role": "Python"}, status="completed",
                desired_count=2, delivered_count=2, reserved_credits=2, charged_credits=2,
                refunded_credits=0, items=[{"id": "job_1"}, {"id": "job_2"}], scope={}, created_at=now,
            ))
            for index in (1, 2):
                connection.execute(metadata.tables["employer_job_deliveries"].insert().values(
                    id=f"legacy_delivery_{index}", user_id=1, posting_id=f"job_{index}",
                    search_id="legacy_search", charged_credits=1, created_at=now,
                ))
            connection.execute(metadata.tables["sealed_application_artifacts"].insert().values(
                id="legacy_artifact", user_id=1, resume_id=10, sha256=digest,
                filename="Ada.pdf", media_type="application/pdf", size_bytes=len(content), content=content, created_at=now,
            ))
            connection.execute(metadata.tables["employer_applications"].insert().values(
                id=app["id"], user_id=1, posting_id="job_1", idempotency_key="legacy-application",
                input_fingerprint="b" * 64, active_key="legacy-active", resume_id=10,
                resume_choice="original", artifact_id="legacy_artifact", form=original.FORM,
                answers=original.ANSWERS, consents={}, package_digest=digest,
                job_content_sha256=original.payload_fingerprint({"job": 1}), application_mode="api",
                status="unknown", allowed_actions=["upload", "submit"], approved_digest=digest,
                approved_at=now, cancel_requested=False, credit_cost=5, charged_credits=0,
                reservation_id="legacy_application_reservation", error_code="submission_unknown",
                created_at=now, updated_at=now,
            ))
            connection.execute(metadata.tables["employer_application_attempts"].insert().values(
                id="legacy_attempt", application_id=app["id"], launch_token="legacy_launch",
                package_digest=digest, state="unknown", response_status=200, started_at=now, completed_at=now,
            ))
            for identifier, kind, amount, balance, operation, source_id in (
                ("legacy_search_reserve", "reserve", -2, 48, "job_search", "legacy_search"),
                ("legacy_search_commit", "commit", 0, 48, "job_search", "legacy_search"),
                ("legacy_application_reserve", "reserve", -5, 43, "job_application", app["id"]),
            ):
                connection.execute(metadata.tables["service_credit_events"].insert().values(
                    id=identifier, user_id=1, event_type=kind, amount=amount, balance_after=balance,
                    idempotency_key=identifier, source_type=operation, source_id=source_id,
                    reason="Synthetic pre-cutover historical ledger", created_at=now,
                ))
    finally:
        source_client.close()
        source_factory.kw["bind"].dispose()
    _migrate(pg_engine, "head")
    factory = sessionmaker(bind=pg_engine, autoflush=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *_a, **_k: pytest.fail("Unknown historical send must not be retried"))
    with factory() as db:
        delivered = db.get(models.EmployerSearch, "legacy_search")
        assert delivered.status == "completed" and delivered.charged_credits == 2
        assert all(row.cost_policy_snapshot is None for row in db.query(models.ServiceCreditReservation))
    assert tasks.execute_application(app["id"]) == "unknown"
    # Verify the real migrated constraints, not a metadata-created schema.
    _migrate(pg_engine, "20261008_0008", direction="downgrade")
    assert "opening_key" not in {row["name"] for row in inspect(pg_engine).get_columns("employer_applications")}
    _migrate(pg_engine, "head")
    with factory() as db:
        saved = db.get(models.EmployerApplication, app["id"])
        admission = db.query(models.EmployerAdmission).one()
        assert saved.status == "unknown" and saved.admission_snapshot is None
        assert saved.pricing_snapshot is None and saved.opening_key == admission.opening_key
        assert admission.state == "unknown" and admission.active_key is not None
        assert admission.policy_snapshot == {"legacy": True, "requires_fresh_review": True}
        assert admission.possible_send_at == db.query(models.EmployerApplicationAttempt).one().started_at
        assert db.get(core.User, 1).job_service_credits == 43
        assert db.query(models.ServiceCreditReservation).filter_by(operation="job_application").one().state == "reserved"
        deliveries = db.query(models.EmployerJobDelivery).all()
        assert len(deliveries) == 2 and sum(row.charged_credits for row in deliveries) == 2
        assert sum(row.opening_key is not None for row in deliveries) == 1
    assert tasks.execute_application(app["id"]) == "unknown"


def test_postgres_clean_migrations_build_admission_constraints(pg_engine):
    _migrate(pg_engine, "head")
    inspector = inspect(pg_engine)
    assert {"employer_admissions", "employer_application_batches"} <= set(inspector.get_table_names())
    assert "uq_employer_delivery_user_opening" in {row["name"] for row in inspector.get_unique_constraints("employer_job_deliveries")}
    assert "fk_employer_application_batch" in {row["name"] for row in inspector.get_foreign_keys("employer_applications")}
    metadata = MetaData()
    version = Table("alembic_version", metadata, autoload_with=pg_engine)
    with pg_engine.connect() as connection:
        assert connection.execute(select(version.c.version_num)).scalar_one() == "20261010_0014"


def test_sqlite_admission_migration_can_upgrade_downgrade_and_upgrade(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'admission-roundtrip.sqlite'}")
    try:
        _migrate(engine, "head")
        _migrate(engine, "20261008_0008", direction="downgrade")
        assert "employer_admissions" not in inspect(engine).get_table_names()
        _migrate(engine, "head")
        assert "employer_admissions" in inspect(engine).get_table_names()
        assert "fk_employer_application_batch" in {row["name"] for row in inspect(engine).get_foreign_keys("employer_applications")}
    finally:
        engine.dispose()
