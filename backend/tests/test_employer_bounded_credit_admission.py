"""Funded bounded search, crash recovery and paid-result visibility regressions."""
from datetime import timedelta

import pytest
from backend.app import models as core
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import config, credits, models, schemas, search_pages, service
from backend.tests.test_employer_catalogue_search import catalogue_db as catalogue_db
from backend.tests.test_employer_search_new_results import indexed_search as indexed_search
from fastapi import HTTPException
from sqlalchemy import event


@pytest.fixture
def bounded_db(catalogue_db, monkeypatch):
    monkeypatch.setenv("EMPLOYER_FULL_CATALOGUE_SEARCH_ENABLED", "false")
    monkeypatch.setenv("EMPLOYER_PAID_HISTORY_MODE", "off")
    db = catalogue_db
    db.add(models.EmployerPosting(id="bounded-job", source_id="source_index", external_id="bounded-job",
        title="Python Software Engineer", employer="Synthetic Employer", location="Bengaluru, India",
        description="Python software engineer PostgreSQL", canonical_url="https://employer.example/careers/bounded-job",
        apply_url="https://employer.example/careers/bounded-job", last_checked_at=utcnow(),
        content_sha256=payload_fingerprint({"job": "bounded-job"})))
    db.commit()
    return db


def _payload(key):
    return schemas.SearchCreate(resume_id=10, role="Software engineer", desired_count=3, idempotency_key=key)


def _hold(db, identity):
    return db.query(models.ServiceCreditReservation).filter_by(source_id=identity, operation="job_search").one()


def test_insufficient_balance_refuses_before_matching_and_outbox(bounded_db, monkeypatch):
    db = bounded_db
    db.get(core.User, 1).job_service_credits = 5
    db.commit()
    monkeypatch.setattr(service, "_prepare_bounded_search", lambda *args: pytest.fail("Unfunded matching is forbidden"))
    with pytest.raises(HTTPException) as exc:
        service.create_search(db, 1, _payload("bounded-insufficient"))
    assert exc.value.status_code == 402
    db.rollback()
    assert db.get(core.User, 1).job_service_credits == 5
    assert not db.query(models.ServiceCreditReservation).first()
    assert not db.query(models.EmployerSearch).first()
    assert not db.query(DispatchOutbox).filter_by(topic="employer.search").first()


def test_funding_and_recovery_intent_are_durable_before_matching(bounded_db, monkeypatch):
    db = bounded_db
    balance = db.get(core.User, 1).job_service_credits
    matching = service.basic_match
    calls = []

    def funded_matching(*args):
        held = db.query(models.ServiceCreditReservation).filter_by(operation="job_search").one()
        assert (held.requested_count, held.reserved_amount) == (3, 6)
        assert db.get(core.User, 1).job_service_credits == balance - 6
        assert db.query(DispatchOutbox).filter_by(aggregate_id=held.source_id, status="pending").one()
        calls.append(True)
        return matching(*args)

    monkeypatch.setattr(service, "basic_match", funded_matching)
    result = service.create_search(db, 1, _payload("bounded-funded-matching"))
    assert calls and result.status == "completed"
    assert (result.reserved_credits, result.charged_credits, result.refunded_credits) == (6, 2, 4)
    assert db.get(core.User, 1).job_service_credits == balance - 2
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=result.id, event_type="reserve").count() == 1


def test_outbox_failure_rolls_back_funds_and_unfinished_search(bounded_db):
    db = bounded_db
    balance = db.get(core.User, 1).job_service_credits

    def reject(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO dispatch_outbox"):
            raise RuntimeError("Synthetic bounded outbox refusal")

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", reject)
    try:
        with pytest.raises(RuntimeError, match="Synthetic bounded outbox refusal"):
            service._admit_bounded_search(db, 1, _payload("bounded-atomic-outbox"))
    finally:
        event.remove(engine, "before_cursor_execute", reject)
        db.rollback()
    assert db.get(core.User, 1).job_service_credits == balance
    assert not db.query(models.ServiceCreditReservation).first()
    assert not db.query(models.EmployerSearch).first()
    assert not db.query(DispatchOutbox).filter_by(topic="employer.search").first()


def test_crash_recovery_reuses_original_hold_and_quote(bounded_db, monkeypatch):
    db = bounded_db
    balance = db.get(core.User, 1).job_service_credits
    identity = service._admit_bounded_search(db, 1, _payload("bounded-durable-recovery")).id
    db.close()
    monkeypatch.setattr(credits, "reserve", lambda *args, **kwargs: pytest.fail("Recovery cannot reserve twice"))
    monkeypatch.setattr(config, "prices", lambda: pytest.fail("Recovery cannot reprice an accepted hold"))
    result = service.resume_bounded_search(db, 1, identity)
    assert result.status == "completed"
    assert (result.charged_credits, result.refunded_credits) == (2, 4)
    assert db.get(core.User, 1).job_service_credits == balance - 2
    assert (_hold(db, identity).committed_amount, _hold(db, identity).released_amount) == (2, 4)


def test_completed_replay_and_seen_only_search_do_not_charge_again(bounded_db, monkeypatch):
    db = bounded_db
    balance = db.get(core.User, 1).job_service_credits
    first = service.create_search(db, 1, _payload("bounded-first-paid"))
    with monkeypatch.context() as replay:
        replay.setattr(credits, "reserve", lambda *args, **kwargs: pytest.fail("Completed replay must not debit"))
        replay.setattr(service, "_prepare_bounded_search", lambda *args: pytest.fail("Completed replay must not rematch"))
        assert service.create_search(db, 1, _payload("bounded-first-paid")).id == first.id
    second = service.create_search(db, 1, _payload("bounded-seen-only"))
    assert (second.reserved_credits, second.charged_credits, second.refunded_credits) == (6, 0, 6)
    assert db.get(core.User, 1).job_service_credits == balance - 2
    assert db.query(models.EmployerJobDelivery).filter_by(user_id=1).count() == 1


def test_unfinished_staged_results_are_hidden_from_both_readers(bounded_db):
    db = bounded_db
    row = service._admit_bounded_search(db, 1, _payload("bounded-hidden-staging"))
    row, token = service._claim_bounded_search(db, 1, row.id)
    prepared = service._prepare_bounded_search(db, row, _payload("bounded-hidden-staging"))
    service._stage_bounded_search(db, 1, row.id, token, prepared)
    staged = db.get(models.EmployerSearch, row.id)
    assert staged.items
    assert service.search_response(staged)["items"] == []
    with pytest.raises(HTTPException) as exc:
        search_pages.saved_results(db, 1, row.id, limit=10, cursor=None)
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "search_not_completed"
    assert _hold(db, row.id).state == "reserved"


def test_flag_off_cancel_is_idempotent_and_worker_cannot_deliver(bounded_db, monkeypatch):
    db = bounded_db
    balance = db.get(core.User, 1).job_service_credits
    identity = service._admit_bounded_search(db, 1, _payload("bounded-cancelled-hold")).id
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "false")
    ended = service.cancel_search_preparation(db, 1, identity)
    events = db.query(models.ServiceCreditEvent).filter_by(source_id=identity).count()
    assert ended["status"] == "cancelled"
    assert (ended["reserved_credits"], ended["refunded_credits"]) == (0, 6)
    assert service.cancel_search_preparation(db, 1, identity)["status"] == "cancelled"
    assert service.resume_bounded_search(db, 1, identity).status == "cancelled"
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=identity).count() == events
    assert db.get(core.User, 1).job_service_credits == balance
    assert not db.query(models.EmployerJobDelivery).filter_by(search_id=identity).first()


def test_failed_preparation_and_abandoned_admission_release_credits(bounded_db, monkeypatch):
    db = bounded_db
    balance = db.get(core.User, 1).job_service_credits
    identity = service._admit_bounded_search(db, 1, _payload("bounded-failed-preparation")).id
    with monkeypatch.context() as failure:
        failure.setattr(service, "basic_match", lambda *args: (_ for _ in ()).throw(RuntimeError("Synthetic matching failure")))
        with pytest.raises(RuntimeError, match="Synthetic matching failure"):
            service.resume_bounded_search(db, 1, identity)
    assert _hold(db, identity).released_amount == 6
    abandoned = service._admit_bounded_search(db, 1, _payload("bounded-abandoned-admission"))
    abandoned.created_at = utcnow() - timedelta(minutes=31)
    db.commit()
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "false")
    service.bounded_search_maintenance(db)
    assert _hold(db, abandoned.id).released_amount == 6
    assert db.get(core.User, 1).job_service_credits == balance


def test_another_owner_cannot_read_or_cancel_search_hold(bounded_db):
    db = bounded_db
    identity = service._admit_bounded_search(db, 1, _payload("bounded-owner-fence")).id
    for callback in (service.search_preparation_status, service.cancel_search_preparation):
        with pytest.raises(HTTPException) as exc:
            callback(db, 999, identity)
        assert exc.value.status_code == 404
    assert (_hold(db, identity).state, _hold(db, identity).released_amount) == ("reserved", 0)
