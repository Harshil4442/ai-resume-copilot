"""Current catalogue revisions fence paid delivery of a previously selected job."""
from copy import deepcopy

import pytest
from backend.app import models as core
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.employer import catalogue_search as catalogue
from backend.app.domains.employer import config, models, schemas, service
from backend.tests.test_employer_catalogue_search import (
    TOKEN,
    _complete,
    _content,
    _posting,
    _select,
)
from backend.tests.test_employer_catalogue_search import catalogue_db as catalogue_db
from backend.tests.test_employer_search_new_results import indexed_search as indexed_search
from sqlalchemy import event


def _remote_plan(db, key, count):
    prices = config.prices()
    plan = service.create_search(db, 1, schemas.SearchCreate(
        resume_id=10, role="Software engineer", desired_count=count, remote_only=True,
        idempotency_key=key, accepted_quote=schemas.SearchPriceAcceptance(
            pricing_version=prices["pricing_version"], unit_price=prices["search_credits_per_job"],
            maximum_credits=count * prices["search_credits_per_job"])))
    plan.resume_ready, plan.resume_skills, plan.lease_token = True, ["python"], TOKEN
    db.commit()
    return plan.id


def _publish_posting_revision(db, posting_id, *, onsite):
    """Mirror the existing native publisher's interval transition in SQLite."""
    posting = db.get(models.EmployerPosting, posting_id)
    old = db.get(models.EmployerCataloguePostingRevision, (posting_id, 0))
    values = {column.name: deepcopy(getattr(old, column.name)) for column in old.__table__.columns
              if column.name not in {"posting_id", "valid_from", "valid_to"}}
    old.valid_to = 1
    if onsite:
        posting.remote = values["remote"] = False
    else:
        posting.title = values["title"] = "Software engineer, backend"
    posting.content_sha256 = values["content_sha256"] = payload_fingerprint({"revision": 1, "onsite": onsite})
    db.add(models.EmployerCataloguePostingRevision(posting_id=posting_id, valid_from=1, **values))
    db.get(models.EmployerCatalogueClock, 1).watermark = 1
    db.commit()


@pytest.mark.parametrize("onsite, count", [
    pytest.param(True, 1, id="only-result-now-onsite"),
    pytest.param(True, 2, id="mixed-result-now-onsite"),
    pytest.param(False, 2, id="still-eligible-revision"),
])
def test_change_after_snapshot_proof_discards_old_revision_and_refunds_unused(
    catalogue_db, monkeypatch, onsite, count,
):
    db = catalogue_db
    _content(db, "qualification", ["Python"])
    for name in ("a-changed", "z-unchanged")[:count]:
        _posting(db, name, "qualification")
    db.commit()
    identity = _remote_plan(db, f"qualification-race-{onsite}-{count}", count)
    assert len(_select(db, identity)) == count
    snapshot_step = catalogue.snapshot_step
    changed = False

    def change_after_proof(*args):
        nonlocal changed
        snapshot_id = snapshot_step(*args)
        if snapshot_id and not changed:
            assert db.get(models.EmployerCatalogueSnapshot, snapshot_id).state == "proven"
            _publish_posting_revision(db, "a-changed", onsite=onsite)
            changed = True
        return snapshot_id

    monkeypatch.setattr(catalogue, "snapshot_step", change_after_proof)
    search = _complete(db, identity)
    assert changed
    remaining = count - 1
    assert [item["posting"]["id"] for item in search.items] == (["z-unchanged"] if remaining else [])
    assert (search.reserved_credits, search.charged_credits, search.refunded_credits) == (2 * count, 2 * remaining, 2)
    assert db.get(core.User, 1).job_service_credits == 50 - 2 * remaining
    reservation = db.query(models.ServiceCreditReservation).filter_by(source_id=identity).one()
    assert (reservation.state, reservation.committed_amount, reservation.released_amount) == ("settled", 2 * remaining, 2)
    assert db.query(models.EmployerJobDelivery).filter_by(search_id=identity).count() == remaining
    assert db.query(models.EmployerPaidDeliveryHistory).filter_by(search_id=identity).count() == remaining
    winner = db.query(models.EmployerCatalogueWinner).filter_by(preparation_id=identity, posting_id="a-changed").one()
    assert winner.stale is True
    assert db.get(models.EmployerCataloguePostingRevision, ("a-changed", 0)).valid_to == 1
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=identity, event_type="release").one().amount == 2
    # Reading/retrying the completed original request never settles again.
    assert catalogue.finish(db, identity, TOKEN) == "pending"
    assert db.get(core.User, 1).job_service_credits == 50 - 2 * remaining
    assert db.query(models.ServiceCreditEvent).filter_by(source_id=identity, event_type="commit").count() == 1


def test_current_revision_check_batches_one_hundred_selected_jobs(catalogue_db):
    db = catalogue_db
    _content(db, "batch", ["Python"])
    for number in range(100):
        _posting(db, f"batch-{number:03d}", "batch")
    db.commit()
    # Select without spending more than the fixture balance can hold.
    db.get(core.User, 1).job_service_credits = 250
    db.commit()
    identity = _remote_plan(db, "qualification-batch-100", 100)
    selected = _select(db, identity)
    plan = db.get(models.EmployerSearchPreparation, identity)
    statements = []

    def captured(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", captured)
    try:
        sources, postings, stale = catalogue._current_selected(db, plan, selected, fence=True)
    finally:
        event.remove(engine, "before_cursor_execute", captured)
    assert len(selected) == len(postings) == 100 and len(sources) == 1 and stale == []
    assert len(statements) == 2


def test_source_revision_change_cannot_publish_old_source_recipe(catalogue_db):
    db = catalogue_db
    _content(db, "source-change", ["Python"])
    _posting(db, "source-change-job", "source-change")
    db.commit()
    identity = _remote_plan(db, "qualification-source-change", 1)
    _select(db, identity)
    old = db.get(models.EmployerCatalogueSourceRevision, ("source_index", 0))
    values = {column.name: deepcopy(getattr(old, column.name)) for column in old.__table__.columns
              if column.name not in {"source_id", "valid_from", "valid_to"}}
    old.valid_to = 1
    # The explicit employer key leaves canonical identity unchanged.
    db.get(models.EmployerSource, "source_index").employer = values["employer"] = "Renamed synthetic employer"
    db.add(models.EmployerCatalogueSourceRevision(source_id="source_index", valid_from=1, **values))
    db.get(models.EmployerCatalogueClock, 1).watermark = 1
    db.commit()
    search = _complete(db, identity)
    assert search.items == [] and (search.charged_credits, search.refunded_credits) == (0, 2)
    assert db.get(core.User, 1).job_service_credits == 50
