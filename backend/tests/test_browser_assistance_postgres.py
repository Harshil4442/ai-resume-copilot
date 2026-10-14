"""Real row-lock/replay/cascade evidence in the dedicated local PostgreSQL DB.

The shipping browser definitions and synthetic owned package are loaded by the
isolated flow helper. These tests establish PostgreSQL transaction behavior, not
production authentication, live employer disclosure or migration admission.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from backend.scripts.monetary_cutover_preflight import unresolved_application_sql
from backend.tests import test_browser_assistance_isolated as flow
from backend.tests import test_employer_admissions_postgres as admission
from fastapi import HTTPException
from sqlalchemy import event, text
from sqlalchemy.orm import sessionmaker


@pytest.fixture
def browser_pg_engine():
    yield from admission.pg_engine.__wrapped__()


@pytest.fixture
def browser_pg(browser_pg_engine):
    pg_engine = browser_pg_engine
    owned = flow.BrowserAssistanceFlow()
    owned.setUp()
    sqlite_db, sqlite_engine = owned.db, owned.engine
    flow.Base.metadata.create_all(pg_engine)
    factory = sessionmaker(bind=pg_engine, autoflush=False, expire_on_commit=False)
    with sqlite_engine.connect() as source, pg_engine.begin() as target:
        for table in flow.Base.metadata.sorted_tables:
            rows = [dict(row) for row in source.execute(table.select()).mappings()]
            if rows:
                target.execute(table.insert(), rows)
    sqlite_db.close()
    sqlite_engine.dispose()
    owned.db, owned.engine = factory(), pg_engine
    owned.app = owned.db.get(flow.employer.EmployerApplication, "apply_test")
    try:
        yield owned, factory
    finally:
        owned.tearDown()


@pytest.mark.parametrize("same_request", [False, True])
def test_competing_begins_authorize_one_field_once(browser_pg, same_request):
    owned, factory = browser_pg
    review = owned.approved()
    first = owned.request("begin", owned.begin_body(review))
    requests = [first, first if same_request else owned.request("begin", owned.begin_body(review))]
    start = Barrier(2)

    def begin(request):
        with factory() as db:
            start.wait(timeout=5)
            try:
                return flow.service.device(db, owned.origin, request)
            except HTTPException as exc:
                db.rollback()
                return {"state": "refused", "status_code": exc.status_code}

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(begin, requests))
    expected = ["begun", "unknown"] if same_request else ["begun", "refused"]
    assert sorted(result["state"] for result in results) == expected
    assert sum("lease" in result for result in results) == 1
    with factory() as db:
        assert db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="field").count() == 1
        assert db.get(flow.models.BrowserAssistanceReview, review["id"]).next_index == 0
        assert db.query(flow.employer.ServiceCreditReservation).count() == 0
        assert db.get(flow.employer.EmployerApplication, "apply_test").status == "ready"


def test_begin_waits_for_committed_owner_revocation_then_refuses(browser_pg):
    owned, factory = browser_pg
    review = owned.approved()
    request = owned.request("begin", owned.begin_body(review))
    attempted_owner_lock = Event()

    def observe(connection, cursor, statement, parameters, context, executemany):
        if "users" in statement and "FOR UPDATE" in statement:
            attempted_owner_lock.set()

    with factory() as revoker:
        revoker.query(flow.User.id).filter_by(id=1).with_for_update().one()
        event.listen(owned.engine, "before_cursor_execute", observe)

        def begin():
            with factory() as db:
                try:
                    flow.service.device(db, owned.origin, request)
                    return "authorized"
                except HTTPException as exc:
                    db.rollback()
                    assert exc.status_code == 403
                    return "revoked"

        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(begin)
                assert attempted_owner_lock.wait(timeout=5)
                flow.service.candidate(revoker, 1, "revoke", flow.schemas.CandidateRequest(
                    identity=owned.device_id, confirmed=True))
                assert pending.result(timeout=5) == "revoked"
        finally:
            event.remove(owned.engine, "before_cursor_execute", observe)
    with factory() as db:
        assert db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="field").count() == 0
        assert db.get(flow.models.BrowserAssistanceReview, review["id"]).payload == {}


def test_database_device_deletion_cascades_answer_bearing_review_and_receipts(browser_pg):
    owned, factory = browser_pg
    review = owned.approved()
    owned.call("begin", owned.begin_body(review))
    with factory() as db:
        assert db.query(flow.models.BrowserAssistanceReview).count() == 1
        assert db.query(flow.models.BrowserAssistanceRequest).count() > 0
        db.query(flow.models.BrowserAssistanceDevice).filter_by(id=owned.device_id).delete(synchronize_session=False)
        db.commit()
        assert db.query(flow.models.BrowserAssistanceReview).count() == 0
        assert db.query(flow.models.BrowserAssistanceRequest).count() == 0
        assert db.query(flow.employer.EmployerApplication).count() == 1


def candidate_report(owned):
    application = owned.app
    application.status, application.cancel_requested = "user_reported", True
    application.approved_digest, application.allowed_actions = None, []
    application.receipt = {"source": "candidate_report", "reported_at": flow.service.now().isoformat(),
                           "actor_user_id": 1, "saved_package_digest": application.package_digest,
                           "employer_confirmed": False, "used_file_verified": False}
    owned.db.commit()


def unresolved(owned, version="20261014_0017"):
    namespace = owned.db.execute(text("SELECT current_schema()")).scalar_one()
    return owned.db.execute(text(unresolved_application_sql(namespace, version))).scalar_one()


def test_financial_reader_keeps_manual_report_separate_from_paid_confirmation(browser_pg):
    owned, _ = browser_pg
    candidate_report(owned)
    assert unresolved(owned) == 0
    # Before introduction of this feature the same row remains unresolved.
    assert unresolved(owned, "20261011_0016") == 1
    assert owned.app.charged_credits == 0


@pytest.mark.parametrize("field,value", [
    ("credit_cost", 20), ("charged_credits", 1), ("application_mode", "api"),
    ("cancel_requested", False), ("approved_digest", "a" * 64), ("allowed_actions", ["fill"]),
    ("receipt", None), ("receipt", []),
    ("source", "provider"), ("actor_user_id", 2), ("actor_user_id", "1"),
    ("saved_package_digest", "f" * 64), ("employer_confirmed", True),
    ("employer_confirmed", "false"), ("used_file_verified", True), ("reported_at", ""),
])
def test_financial_reader_refuses_charges_and_malformed_candidate_report(browser_pg, field, value):
    owned, _ = browser_pg
    candidate_report(owned)
    if field in {"credit_cost", "charged_credits", "application_mode", "cancel_requested", "approved_digest", "allowed_actions", "receipt"}:
        setattr(owned.app, field, value)
    else:
        owned.app.receipt = {**owned.app.receipt, field: value}
    owned.db.commit()
    assert unresolved(owned) == 1


@pytest.mark.parametrize("history", ["attempt", "hold"])
def test_candidate_report_cannot_hide_a_prior_execution_attempt_or_credit_hold(browser_pg, history):
    owned, _ = browser_pg
    candidate_report(owned)
    if history == "attempt":
        owned.db.add(flow.employer.EmployerApplicationAttempt(id="synthetic_attempt", application_id=owned.app.id,
            launch_token="synthetic_token", package_digest=owned.app.package_digest, state="failed"))
    else:
        owned.db.add(flow.employer.ServiceCreditReservation(id="synthetic_hold", user_id=1,
            operation="job_application", source_id=owned.app.id, unit_price=20, requested_count=1,
            reserved_amount=20, pricing_version="synthetic", state="settled", released_amount=20))
    owned.db.commit()
    assert unresolved(owned) == 1
