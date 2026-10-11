"""Search admission, immutable pricing and unused-credit release regressions."""

from datetime import timedelta

import pytest
from backend.app import models as core
from backend.app.domains.common import utcnow
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import catalogue_search as catalogue
from backend.app.domains.employer import config, credits, models, privacy, schemas, service
from backend.tests.test_employer_catalogue_search import (
    TOKEN,
    _complete,
    _content,
    _plan,
    _posting,
    _select,
)
from backend.tests.test_employer_catalogue_search import (
    catalogue_db as catalogue_db,
)
from backend.tests.test_employer_search_new_results import indexed_search as indexed_search
from pydantic import ValidationError
from sqlalchemy import event


def _reservation(db, identity):
    return db.query(models.ServiceCreditReservation).filter_by(
        source_id=identity, operation="job_search").one()


def _payload(key, count=3):
    return schemas.SearchCreate(resume_id=10, role="Software engineer",
        desired_count=count, idempotency_key=key)


def test_requested_credit_hold_and_queue_commit_atomically_after_bounded_normalization(catalogue_db, monkeypatch):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    claims = []
    lock = catalogue.lock_owner_nowait
    variants = catalogue.role_aliases.variants

    def claimed(session, owner):
        claims.append(owner)
        return lock(session, owner)

    def before_hold(role):
        assert not claims
        assert not db.query(models.ServiceCreditReservation).filter_by(operation="job_search").first()
        assert db.get(core.User, 1).job_service_credits == balance
        return variants(role)

    monkeypatch.setattr(catalogue, "lock_owner_nowait", claimed)
    monkeypatch.setattr(catalogue.role_aliases, "variants", before_hold)
    plan = service.create_search(db, 1, _payload("catalogue-upfront-replay"))
    reservation = _reservation(db, plan.id)
    assert claims == [1]
    assert plan.state == "pending" and plan.query["role"] == "Software engineer"
    assert plan.worker_query["role_variants"] == variants("Software engineer")
    assert (reservation.requested_count, reservation.reserved_amount, reservation.state) == (3, 6, "reserved")
    queued = db.query(DispatchOutbox).filter_by(aggregate_id=plan.id).one()
    assert (queued.topic, queued.status) == ("employer.search", "pending")
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=plan.id, event_type="reserve").one().amount == -6
    monkeypatch.setattr(catalogue.role_aliases, "variants", variants)
    monkeypatch.setattr(config, "prices", lambda: pytest.fail("An accepted replay cannot be repriced"))
    assert service.create_search(db, 1, _payload("catalogue-upfront-replay")).id == plan.id
    assert db.get(core.User, 1).job_service_credits == balance - 6
    assert db.query(models.ServiceCreditReservation).filter_by(source_id=plan.id).count() == 1
    assert db.query(DispatchOutbox).filter_by(aggregate_id=plan.id).count() == 1


def test_outbox_insert_failure_returns_all_credits_and_publishes_no_plan(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    counts = {cls: db.query(cls).count() for cls in (
        models.ServiceCreditReservation, models.ServiceCreditEvent, models.EmployerSearchPreparation, DispatchOutbox)}

    def reject(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO dispatch_outbox"):
            raise RuntimeError("Synthetic outbox refusal")

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", reject)
    try:
        with pytest.raises(RuntimeError, match="Synthetic outbox refusal"):
            service.create_search(db, 1, _payload("catalogue-outbox-rollback"))
    finally:
        event.remove(engine, "before_cursor_execute", reject)
        db.rollback()
    assert db.get(core.User, 1).job_service_credits == balance
    assert all(db.query(cls).count() == count for cls, count in counts.items())


def test_interrupted_response_retains_funded_queued_work_for_recovery(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits

    def interrupt_response(_session):
        raise RuntimeError("Synthetic response interruption")

    event.listen(db, "after_commit", interrupt_response)
    try:
        with pytest.raises(RuntimeError, match="Synthetic response interruption"):
            service.create_search(db, 1, _payload("catalogue-response-interruption"))
    finally:
        event.remove(db, "after_commit", interrupt_response)
        db.close()
    plan = db.query(models.EmployerSearchPreparation).filter_by(idempotency_key="catalogue-response-interruption").one()
    assert plan.state == "pending"
    assert db.query(DispatchOutbox).filter_by(aggregate_id=plan.id, status="pending").count() == 1
    assert db.get(core.User, 1).job_service_credits == balance - 6
    assert service.create_search(db, 1, _payload("catalogue-response-interruption")).id == plan.id
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=plan.id, event_type="reserve").count() == 1


def test_oversized_utf8_request_refuses_before_financial_owner_lock(catalogue_db, monkeypatch):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    payload = schemas.SearchCreate(resume_id=10, role="Software engineer", desired_count=3,
        idempotency_key="catalogue-oversized-query", excluded_employers=[str(i) + "界" * 197 for i in range(100)])
    monkeypatch.setattr(catalogue, "lock_owner_nowait", lambda *args: pytest.fail("Oversized request must not claim financial locks"))
    with pytest.raises(catalogue.HTTPException) as exc:
        service.create_search(db, 1, payload)
    assert exc.value.status_code == 422
    assert db.get(core.User, 1).job_service_credits == balance
    assert not db.query(models.ServiceCreditReservation).filter_by(operation="job_search").first()


def test_new_excluded_employer_name_contract_has_a_finite_bound():
    with pytest.raises(ValidationError):
        schemas.SearchCreate(resume_id=10, role="Software engineer", desired_count=3,
            idempotency_key="catalogue-long-employer", excluded_employers=["x" * 201])


def test_legacy_unpublished_accepting_hold_is_refunded_without_manual_replay(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    plan = service.create_search(db, 1, _payload("catalogue-legacy-unpublished"))
    identity = plan.id
    plan.state, plan.query, plan.worker_query = "accepting", {}, {}
    db.query(DispatchOutbox).filter_by(aggregate_id=identity).delete()
    db.commit()
    catalogue.maintenance_step(db)
    assert db.get(models.EmployerSearchPreparation, identity).state == "failed"
    assert db.get(core.User, 1).job_service_credits == balance
    assert _reservation(db, identity).released_amount == 6
    assert not db.query(DispatchOutbox).filter_by(aggregate_id=identity).first()


def test_insufficient_balance_refuses_before_preparation_and_dispatch(catalogue_db):
    db = catalogue_db
    db.get(core.User, 1).job_service_credits = 5
    db.commit()
    counts = {cls: db.query(cls).count() for cls in (
        models.ServiceCreditReservation, models.ServiceCreditEvent, models.EmployerSearchPreparation)}
    with pytest.raises(catalogue.HTTPException) as exc:
        service.create_search(db, 1, _payload("catalogue-insufficient-upfront"))
    assert exc.value.status_code == 402
    db.rollback()
    assert db.get(core.User, 1).job_service_credits == 5
    assert all(db.query(cls).count() == count for cls, count in counts.items())


def test_accepting_plan_failure_rolls_back_customer_and_expense_hold(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    counts = {cls: db.query(cls).count() for cls in (
        models.ServiceCreditReservation, models.ServiceCreditEvent, models.EmployerSearchPreparation)}

    def reject_header(_connection, _cursor, statement, _parameters, _context, _executemany):
        if statement.startswith("INSERT INTO employer_search_preparations"):
            raise RuntimeError("Synthetic accepting header refusal")

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", reject_header)
    try:
        with pytest.raises(RuntimeError, match="Synthetic accepting header refusal"):
            service.create_search(db, 1, _payload("catalogue-atomic-admission"))
    finally:
        event.remove(engine, "before_cursor_execute", reject_header)
        db.rollback()
    assert db.get(core.User, 1).job_service_credits == balance
    assert all(db.query(cls).count() == count for cls, count in counts.items())


def test_partial_delivery_settles_original_hold_without_another_reserve(catalogue_db, monkeypatch):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    _content(db, "partial-credit-content", ["Python"])
    _posting(db, "partial-credit-job", "partial-credit-content")
    db.commit()
    identity = _plan(db, "catalogue-partial-refund", count=3)
    authority = _reservation(db, identity).cost_policy_snapshot
    _select(db, identity)
    monkeypatch.setattr(credits, "reserve", lambda *args, **kwargs: pytest.fail("Search must not debit again at completion"))
    monkeypatch.setattr(config, "prices", lambda: pytest.fail("Search must preserve the accepted quote"))
    search = _complete(db, identity)
    reservation = _reservation(db, identity)
    assert (search.reserved_credits, search.charged_credits, search.refunded_credits) == (6, 2, 4)
    assert (reservation.reserved_amount, reservation.committed_amount, reservation.released_amount, reservation.state) == (6, 2, 4, "settled")
    assert reservation.cost_policy_snapshot["version"] == authority["version"]
    assert reservation.cost_policy_snapshot["legacy_service_hold_minor"] == authority["legacy_service_hold_minor"]
    assert db.get(core.User, 1).job_service_credits == balance - 2
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=identity, event_type="reserve").count() == 1


def test_empty_search_returns_entire_requested_hold(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    identity = _plan(db, "catalogue-empty-refund", count=3)
    _select(db, identity)
    search = _complete(db, identity)
    assert (search.reserved_credits, search.charged_credits, search.refunded_credits) == (6, 0, 6)
    assert db.get(core.User, 1).job_service_credits == balance
    assert not db.query(models.EmployerJobDelivery).filter_by(search_id=identity).first()


def test_seen_only_new_search_returns_hold_and_preserves_paid_delivery(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    _content(db, "seen-credit-content", ["Python"])
    _posting(db, "seen-credit-job", "seen-credit-content")
    db.commit()
    first = _plan(db, "catalogue-first-paid-result")
    _select(db, first)
    _complete(db, first)
    second = _plan(db, "catalogue-seen-result-refund", count=3)
    _select(db, second)
    search = _complete(db, second)
    assert (search.reserved_credits, search.charged_credits, search.refunded_credits) == (6, 0, 6)
    assert db.get(core.User, 1).job_service_credits == balance - 2
    assert db.query(models.EmployerJobDelivery).filter_by(user_id=1).count() == 1


@pytest.mark.parametrize("reason", ["cancelled_by_owner", "dispatch_attempts_exhausted", "projection_version_unavailable"])
def test_safe_terminal_search_release_is_idempotent(catalogue_db, reason):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    identity = _plan(db, "catalogue-terminal-" + reason, count=3)
    catalogue.cancel(db, 1, identity, reason=reason)
    events = db.query(models.ServiceCreditEvent).filter_by(source_id=identity).count()
    catalogue.cancel(db, 1, identity, reason=reason)
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=identity).count() == events
    reservation = _reservation(db, identity)
    assert (reservation.committed_amount, reservation.released_amount, reservation.state) == (0, 6, "settled")
    assert db.get(core.User, 1).job_service_credits == balance
    assert catalogue.execute_plan(db, identity) in {"failed", "cancelled"}


@pytest.mark.parametrize("failure", ["resume_changed", "resume_skills_require_review"])
def test_resume_failure_releases_customer_hold(catalogue_db, failure):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    identity = _plan(db, "catalogue-resume-failure-" + failure, count=3)
    if failure == "resume_changed":
        db.get(models.EmployerCatalogueResumeBinding, 10).revision += 1
    else:
        db.get(core.Resume, 10).skills = 42
    db.commit()
    assert catalogue.resume_step(db, identity, TOKEN) is False
    assert db.get(models.EmployerSearchPreparation, identity).failure_reason == failure
    assert _reservation(db, identity).released_amount == 6
    assert db.get(core.User, 1).job_service_credits == balance


def test_cancelled_worker_cannot_finish_or_duplicate_a_release(catalogue_db, monkeypatch):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    _content(db, "cancelled-credit-content", ["Python"])
    _posting(db, "cancelled-credit-job", "cancelled-credit-content")
    db.commit()
    identity = _plan(db, "catalogue-cancel-wins-settlement", count=3)
    _select(db, identity)
    catalogue.cancel(db, 1, identity)
    monkeypatch.setattr(catalogue, "inventory_step", lambda *args: pytest.fail("A cancelled worker cannot do paid work"))
    assert catalogue.execute_plan(db, identity) == "cancelled"
    assert db.get(core.User, 1).job_service_credits == balance
    assert not db.query(models.EmployerSearch).filter_by(id=identity).first()
    assert not db.query(models.EmployerJobDelivery).filter_by(search_id=identity).first()


def test_completed_delivery_survives_late_cancel(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    _content(db, "late-cancel-content", ["Python"])
    _posting(db, "late-cancel-job", "late-cancel-content")
    db.commit()
    identity = _plan(db, "catalogue-completion-wins-cancel", count=3)
    _select(db, identity)
    _complete(db, identity)
    catalogue.cancel(db, 1, identity)
    assert db.get(core.User, 1).job_service_credits == balance - 2
    assert (_reservation(db, identity).committed_amount, _reservation(db, identity).released_amount) == (2, 4)
    assert db.query(models.EmployerJobDelivery).filter_by(search_id=identity).count() == 1


def test_account_erasure_releases_only_undelivered_search_holds(catalogue_db):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    identity = _plan(db, "catalogue-account-erase-hold", count=3)
    reservation = _reservation(db, identity)
    cash = reservation.cost_policy_snapshot["legacy_service_hold_minor"]
    bounded = credits.reserve(db, user_id=1, operation="job_search", source_id="synthetic-planless-bounded",
        unit_price=2, count=2, pricing_version=reservation.pricing_version)
    db.add(models.EmployerSearch(id=bounded.source_id, user_id=1, resume_id=10,
        idempotency_key="synthetic-bounded-preparing", input_fingerprint="a" * 64, query={}, items=[],
        desired_count=2, delivered_count=0, status="preparing", reserved_credits=4, charged_credits=0, refunded_credits=0, scope={}))
    application = credits.reserve(db, user_id=1, operation="job_application", source_id="synthetic-unknown-application",
        unit_price=20, count=1, pricing_version=reservation.pricing_version)
    db.commit()
    privacy.delete_account_data(db, 1)
    db.commit()
    for row in (db.get(models.ServiceCreditReservation, reservation.id), db.get(models.ServiceCreditReservation, bounded.id)):
        assert (row.user_id, row.state, row.committed_amount, row.released_amount) == (None, "settled", 0, row.reserved_amount)
    assert db.get(models.ServiceCreditReservation, reservation.id).cost_policy_snapshot["legacy_service_hold_minor"] == cash
    held = db.get(models.ServiceCreditReservation, application.id)
    assert (held.user_id, held.operation, held.state, held.reserved_amount, held.released_amount) == (None, "job_application", "reserved", 20, 0)
    assert db.get(core.User, 1).job_service_credits == balance - 20
    assert catalogue.execute_plan(db, identity) == "missing"


def test_abandoned_search_refunds_hold_when_feature_flag_is_off(catalogue_db, monkeypatch):
    db = catalogue_db
    balance = db.get(core.User, 1).job_service_credits
    identity = _plan(db, "catalogue-abandoned-funded-hold", count=3)
    plan = db.get(models.EmployerSearchPreparation, identity)
    plan.updated_at = utcnow() - timedelta(minutes=31)
    plan.lease_token = plan.lease_until = None
    db.commit()
    monkeypatch.setenv("EMPLOYER_FULL_CATALOGUE_SEARCH_ENABLED", "false")
    catalogue.maintenance_step(db)
    assert db.get(models.EmployerSearchPreparation, identity).failure_reason == "abandoned_without_progress"
    assert (_reservation(db, identity).state, _reservation(db, identity).released_amount) == ("settled", 6)
    assert db.get(core.User, 1).job_service_credits == balance


def test_status_shows_current_hold_and_refund_separately(catalogue_db):
    db = catalogue_db
    identity = _plan(db, "catalogue-visible-current-hold", count=3)
    pending = catalogue.status(db, 1, identity)
    assert (pending["reserved_credits"], pending["refunded_credits"], pending["charged_credits"]) == (6, 0, 0)
    catalogue.cancel(db, 1, identity)
    ended = catalogue.status(db, 1, identity)
    assert (ended["reserved_credits"], ended["refunded_credits"], ended["charged_credits"]) == (0, 6, 0)
