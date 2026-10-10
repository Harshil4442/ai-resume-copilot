"""Durable SQLite catalogue regressions using real domain/credit fixtures.

Ready projections and resume facts isolate ranking and settlement. The clock
is controlled only in the deadline test; no provider transport is invoked.
"""
from types import SimpleNamespace

import pytest
from backend.app import models as core
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.employer import catalogue_search as catalogue
from backend.app.domains.employer import credits, models, schemas, service
from backend.tests.test_employer_search_new_results import (
    indexed_search as indexed_search,
)
from sqlalchemy import event

TOKEN = "catalogue-ci-worker"


@pytest.fixture
def catalogue_db(indexed_search, monkeypatch):
    db = indexed_search  # Preserve the ordinary Session(autoflush=False) fixture.
    monkeypatch.setenv("EMPLOYER_PAID_HISTORY_MODE", "indexed")
    monkeypatch.setenv("EMPLOYER_FULL_CATALOGUE_SEARCH_ENABLED", "true")
    db.query(models.EmployerPosting).delete(synchronize_session=False)
    source = db.get(models.EmployerSource, "source_index")
    source.employer_key = "synthetic-employer"
    db.get(core.Resume, 10).skills = ["Python"]
    db.add_all([
        models.EmployerCatalogueClock(id=1, watermark=0, inventory_state="ready"),
        models.EmployerCatalogueResumeBinding(resume_id=10, revision=1),
        models.EmployerPaidHistoryProgress(user_id=1, state="ready", generation=1),
        models.EmployerCatalogueSourceRevision(source_id=source.id, valid_from=0, enabled=True,
            platform=source.platform, region=source.region, board_token=source.board_token,
            employer_key=source.employer_key, employer=source.employer),
    ])
    db.commit()
    return db


def _content(db, identity, required):
    db.add(models.EmployerCatalogueContent(id=identity,
        description="Software engineer Python. Immutable synthetic wording.", preference_metadata=None,
        public_fields={"canonical_url": "https://employer.example/jobs/synthetic",
            "apply_url": "https://employer.example/jobs/synthetic", "language": "en", "country": None}))
    db.flush()
    db.add(models.EmployerCatalogueProjection(content_id=identity, projection_version=catalogue.PROJECTION_VERSION,
        projection_state="ready", required_skills=required, scoring_warnings=[],
        preference_valid=False, preference_facts={}))
    db.flush()


def _posting(db, identity, content, *, requisition=None, title="Software engineer"):
    now = utcnow()
    values = dict(source_id="source_index", external_id=identity, requisition_id=requisition or identity,
        title=title, employer="Synthetic Employer", location="Bengaluru", remote=True, is_open=True,
        publication_at=now, first_seen_at=now, last_checked_at=now, content_sha256=payload_fingerprint(identity))
    db.add(models.EmployerPosting(id=identity, description="Software engineer Python",
        canonical_url="https://employer.example/jobs/" + identity,
        apply_url="https://employer.example/jobs/" + identity, **values))
    db.add(models.EmployerCataloguePostingRevision(posting_id=identity, valid_from=0, content_id=content, **values))


def _plan(db, key, count=1):
    plan = service.create_search(db, 1, schemas.SearchCreate(resume_id=10, role="Software engineer",
        desired_count=count, idempotency_key=key))
    plan.resume_ready, plan.resume_skills, plan.lease_token = True, ["python"], TOKEN
    db.commit()
    return plan.id


def _select(db, identity):
    for _ in range(32):
        plan = db.get(models.EmployerSearchPreparation, identity)
        if plan.state not in ("pending", "scanning"):
            break
        assert catalogue.scan_step(db, identity, TOKEN)
    assert plan.state == "classifying"
    for _ in range(32):
        if db.get(models.EmployerSearchPreparation, identity).state == "selecting":
            break
        assert catalogue.classify_step(db, identity, TOKEN) == "ready"
    plan = db.get(models.EmployerSearchPreparation, identity)
    assert plan.state == "selecting"
    return catalogue._selected(db, plan)


def _complete(db, identity):
    for _ in range(8):
        if catalogue.finish(db, identity, TOKEN) == "completed":
            return db.get(models.EmployerSearch, identity)
    pytest.fail("Catalogue snapshot/settlement did not complete")


def test_global_alias_winner_beyond_first_thousand_and_all_winners_classified(catalogue_db):
    db = catalogue_db
    _content(db, "low", ["Python", "SQL", "Docker"])
    _content(db, "middle", ["Python", "SQL"])
    _content(db, "high", ["Python"])
    for number in range(1001):
        shared = number in (0, 1, 1000)
        content = "high" if number == 1000 else "middle" if number == 1 else "low"
        _posting(db, f"job_{number:04d}", content, requisition="same-opening" if shared else None)
    db.commit()
    first = _plan(db, "catalogue-global-winner")
    assert [winner.posting_id for winner in _select(db, first)] == ["job_1000"]
    plan = db.get(models.EmployerSearchPreparation, first)
    winners = db.query(models.EmployerCatalogueWinner).filter_by(preparation_id=first).all()
    assert plan.candidates_before_preferences == 1001 and len(winners) == 999
    assert all(winner.classification_generation == plan.history_generation for winner in winners)
    assert not {"job_0000", "job_0001"} & {winner.posting_id for winner in winners}
    assert _complete(db, first).charged_credits == 2

    second = _plan(db, "catalogue-unseen-winner")
    assert [winner.posting_id for winner in _select(db, second)] == ["job_0002"]
    plan = db.get(models.EmployerSearchPreparation, second)
    winners = db.query(models.EmployerCatalogueWinner).filter_by(preparation_id=second).all()
    assert len(winners) == 999
    assert all(winner.classification_generation == plan.history_generation for winner in winners)
    paid = next(winner for winner in winners if winner.posting_id == "job_1000")
    assert paid.seen is True and paid.score_tenths == 1000


def test_python_rounding_literal_title_and_binary_id_ties(catalogue_db):
    db = catalogue_db
    _content(db, "tie", ["Python", "Java", "C++", "C#", "SQL", "JavaScript", "TypeScript", "React",
        "Angular", "Vue", "Node.js", "Docker", "Kubernetes", "AWS", "Azure", "GCP"])
    for identity in ("A", "a", "ä"):
        _posting(db, identity, "tie")
    _posting(db, "0-alias", "tie", title="Backend developer")
    db.commit()
    identity = _plan(db, "catalogue-exact-order", count=4)
    selected = _select(db, identity)
    assert [winner.posting_id for winner in selected] == ["A", "a", "ä", "0-alias"]
    assert [winner.fit["score"] for winner in selected] == [6.2] * 4  # Python round(6.25, 1).
    assert [winner.score_tenths for winner in selected] == [62] * 4
    assert [winner.title_hit for winner in selected] == [True, True, True, False]


def test_saved_item_digest_binds_complete_body_and_native_paid_lineage(catalogue_db):
    db = catalogue_db
    _content(db, "body", ["Python"])
    for posting_id in ("proof-a", "proof-b"):
        _posting(db, posting_id, "body")
    db.commit()
    identity = _plan(db, "catalogue-native-proof", count=2)
    _select(db, identity)
    search = _complete(db, identity)
    assert (search.reserved_credits, search.charged_credits, search.refunded_credits) == (4, 4, 0)
    items = db.query(models.EmployerCatalogueSnapshotItem).filter_by(snapshot_id=search.native_snapshot_id).order_by(
        models.EmployerCatalogueSnapshotItem.ordinal).all()
    deliveries = db.query(models.EmployerJobDelivery).filter_by(search_id=identity).all()
    assert len(items) == len(deliveries) == 2
    for ordinal, item in enumerate(items):
        saved = search.items[ordinal]
        delivery = next(row for row in deliveries if row.posting_id == item.posting_id)
        proof = db.get(models.EmployerPaidDeliveryHistory, delivery.id)
        assert item.body == saved and item.item_digest == payload_fingerprint(saved)
        assert "Immutable synthetic wording" in saved["posting"]["description"]
        assert (proof.user_id, proof.search_id, proof.posting_id, proof.saved_ordinal) == (1, identity, item.posting_id, ordinal)
        assert (proof.state, proof.content_digest, proof.original_charged_credits) == ("proven", item.item_digest, 2)
        assert (item.posting_revision, item.source_revision, proof.opening_key) == (0, 0, delivery.opening_key)
        assert proof.identity_proof["native_snapshot_id"] == search.native_snapshot_id
        assert proof.identity_proof["native_item_digest"] == item.item_digest
        assert proof.identity_proof["native_digest_recipe"] == "sqlite-fixture-canonical-json-sha256-v1"


def test_final_settlement_dml_deadline_rolls_back_balance_ledger_search_and_delivery(catalogue_db, monkeypatch):
    db = catalogue_db
    _content(db, "deadline", ["Python"])
    _posting(db, "deadline-job", "deadline")
    db.commit()
    identity = _plan(db, "catalogue-deadline-rollback")
    _select(db, identity)
    for _ in range(8):
        if catalogue.snapshot_step(db, identity, TOKEN):
            break
    snapshot = db.query(models.EmployerCatalogueSnapshot).filter_by(preparation_id=identity).one()
    assert snapshot.state == "proven"
    plan = db.get(models.EmployerSearchPreparation, identity)
    plan.lease_token = None
    db.commit()
    balance = db.get(core.User, 1).job_service_credits
    events = db.query(models.ServiceCreditEvent).filter_by(user_id=1).count()
    state = {"now": 100.0, "settling": False}
    monkeypatch.setattr(catalogue, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    original_settle = credits.settle

    def settle(*args, **kwargs):
        state["settling"] = True
        return original_settle(*args, **kwargs)

    def expire_after_dml(_connection, _cursor, statement, _parameters, _context, _executemany):
        if state["settling"] and statement.startswith("UPDATE service_credit_reservations"):
            state["now"] = 109.0

    monkeypatch.setattr(credits, "settle", settle)
    engine = db.get_bind()
    event.listen(engine, "after_cursor_execute", expire_after_dml)
    try:
        assert catalogue.execute_plan(db, identity) == "pending"
    finally:
        event.remove(engine, "after_cursor_execute", expire_after_dml)
    assert state["now"] == 109.0
    assert db.get(core.User, 1).job_service_credits == balance
    assert db.query(models.ServiceCreditEvent).filter_by(user_id=1).count() == events
    assert not db.query(models.EmployerSearch).filter_by(id=identity).first()
    assert not db.query(models.EmployerJobDelivery).filter_by(search_id=identity).first()
    assert not db.query(models.EmployerPaidDeliveryHistory).filter_by(search_id=identity).first()
    reservation = db.get(models.ServiceCreditReservation, plan.reservation_id)
    assert (reservation.reserved_amount, reservation.committed_amount, reservation.released_amount, reservation.state) == (0, 0, 0, "reserved")
    plan = db.get(models.EmployerSearchPreparation, identity)
    assert (plan.state, plan.lease_token, plan.lease_until) == ("selecting", None, None)
    assert catalogue._TURN_DEADLINE_KEY not in db.info
