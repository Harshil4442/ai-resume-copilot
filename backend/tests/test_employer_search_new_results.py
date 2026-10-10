"""Real local SQL search settlement; no employer, model or provider transport."""
from __future__ import annotations

from collections.abc import Iterator
from copy import deepcopy

import pytest
from backend.app import models as core
from backend.app.database import Base
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.employer import admissions, models, schemas, service
from sqlalchemy import create_engine
from sqlalchemy.orm import Session


@pytest.fixture
def indexed_search(monkeypatch) -> Iterator[Session]:
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "false")
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "2")
    monkeypatch.setenv("EMPLOYER_MAX_SEARCH_JOBS", "100")
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, autoflush=False) as db:
            db.add_all([
                core.User(id=1, email="first@example.invalid", job_service_credits=50),
                core.User(id=2, email="second@example.invalid", job_service_credits=50),
                core.Resume(id=10, user_id=1, original_filename="synthetic.pdf", skills=["Python", "PostgreSQL"]),
                core.Resume(id=20, user_id=2, original_filename="synthetic.pdf", skills=["Python", "PostgreSQL"]),
                models.EmployerSource(id="source_index", employer="Synthetic Employer", platform="greenhouse",
                    board_token="synthetic", enabled=True, region="global",
                    careers_url="https://employer.example/careers", verification_url="https://employer.example/careers",
                    verification_note="Synthetic reviewed-origin fixture", status="healthy"),
            ])
            db.commit()
            for number, description in [(1, "Python PostgreSQL Engineer"), (2, "Python Engineer")]:
                db.add(models.EmployerPosting(id=f"job_{number}", source_id="source_index", external_id=str(number),
                    requisition_id=f"req-{number}", title="Python Engineer", employer="Synthetic Employer",
                    location="Bengaluru", description=description, canonical_url=f"https://job-boards.greenhouse.io/synthetic/jobs/{number}",
                    apply_url=f"https://job-boards.greenhouse.io/synthetic/jobs/{number}",
                    content_sha256=payload_fingerprint({"job": number}), last_checked_at=utcnow()))
            db.commit()
            yield db
    finally:
        engine.dispose()


def search(db, key, *, owner=1, count=1):
    return service.search_response(service.create_search(db, owner, schemas.SearchCreate(
        resume_id=10 if owner == 1 else 20, role="Python Engineer",
        desired_count=count, idempotency_key=key,
    )))


@pytest.mark.parametrize("legacy", [False, True])
def test_next_search_delivers_unseen_qualifying_opening_before_saved_top_match(indexed_search, legacy):
    db = indexed_search
    first = search(db, "new-results-first")
    assert first["items"][0]["posting"]["id"] == "job_1"
    assert first["charged_credits"] == 2
    if legacy:
        db.query(models.EmployerJobDelivery).filter_by(user_id=1).one().opening_key = None
        db.commit()
    second = search(db, "new-results-second")
    assert second["items"][0]["posting"]["id"] == "job_2"
    assert (second["desired_count"], second["delivered_count"], second["reserved_credits"], second["charged_credits"]) == (1, 1, 2, 2)
    assert second["items"][0]["preference_evaluation"]["eligible"] is True
    assert db.get(core.User, 1).job_service_credits == 46
    assert db.query(models.EmployerJobDelivery).filter_by(user_id=1).count() == 2
    assert second["scope"]["worldwide_recall_verified"] is False
    assert second["scope"]["candidate_limit"] == 1000
    assert second["scope"]["price_quote"]["maximum_credits"] == 2
    before = deepcopy(second)
    assert search(db, "new-results-second") == before
    assert db.get(core.User, 1).job_service_credits == 46


def test_fresh_result_then_free_saved_fill_preserve_requested_bound_and_quote(indexed_search):
    db = indexed_search
    search(db, "mixed-results-first")
    second = search(db, "mixed-results-second", count=2)
    assert [item["posting"]["id"] for item in second["items"]] == ["job_2", "job_1"]
    assert [item["charged_credits"] for item in second["items"]] == [2, 0]
    assert (second["reserved_credits"], second["charged_credits"], second["refunded_credits"]) == (4, 2, 2)
    assert second["scope"]["price_quote"]["requested_count"] == 2
    assert second["scope"]["price_quote"]["maximum_credits"] == 4
    exhausted = search(db, "mixed-results-exhausted")
    assert exhausted["items"][0]["posting"]["id"] == "job_1"
    assert exhausted["charged_credits"] == 0 and exhausted["reserved_credits"] == 0
    assert db.get(core.User, 1).job_service_credits == 46


def test_paid_delivery_is_owner_specific(indexed_search):
    db = indexed_search
    search(db, "owner-first-results")
    other = search(db, "owner-second-results", owner=2)
    assert other["items"][0]["posting"]["id"] == "job_1"
    assert other["charged_credits"] == 2
    assert db.get(core.User, 1).job_service_credits == 48
    assert db.get(core.User, 2).job_service_credits == 48


def test_canonical_duplicate_of_saved_opening_does_not_displace_unseen_job(indexed_search):
    db = indexed_search
    first = search(db, "duplicate-results-first")
    db.add(models.EmployerPosting(id="job_0_alias", source_id="source_index", external_id="alias-1",
        requisition_id="req-1", title="Python Engineer", employer="Synthetic Employer",
        location="Bengaluru", description="Python PostgreSQL Engineer",
        canonical_url="https://job-boards.greenhouse.io/synthetic/jobs/alias-1",
        apply_url="https://job-boards.greenhouse.io/synthetic/jobs/alias-1",
        content_sha256="a" * 64, last_checked_at=utcnow()))
    db.commit()
    assert admissions.opening_identity(db.get(models.EmployerPosting, "job_0_alias"), db.get(models.EmployerSource, "source_index")) == first["items"][0]["posting"]["opening_key"]
    second = search(db, "duplicate-results-second")
    assert second["items"][0]["posting"]["id"] == "job_2"
    assert second["charged_credits"] == 2
    assert db.query(models.EmployerJobDelivery).filter_by(user_id=1).count() == 2


def test_two_requested_results_progress_measured_over_same_index(indexed_search):
    import json

    db = indexed_search
    first = search(db, "measured-results-first")
    second = search(db, "measured-results-second")
    identities = {item["posting"]["opening_key"] for result in [first, second] for item in result["items"]}
    measured = {
        "synthetic_qualifying_indexed_jobs": 2,
        "requests": 2,
        "requested_count_each": 1,
        "distinct_delivered_openings": len(identities),
        "new_charged_results_each": [sum(item["charged_credits"] > 0 for item in result["items"]) for result in [first, second]],
        "total_charged_credits": first["charged_credits"] + second["charged_credits"],
        "unit_price": 2,
        "worldwide_recall_verified": False,
    }
    print(json.dumps(measured, sort_keys=True))
    assert measured["distinct_delivered_openings"] == 2
    assert measured["new_charged_results_each"] == [1, 1]
    assert measured["total_charged_credits"] == 4


@pytest.mark.parametrize("count", [1, 3])
def test_empty_index_preserves_requested_count_and_zero_charge(indexed_search, count):
    db = indexed_search
    db.query(models.EmployerPosting).update({models.EmployerPosting.is_open: False})
    db.commit()
    result = search(db, "empty-results", count=count)
    assert result["desired_count"] == count and result["items"] == []
    assert (result["delivered_count"], result["reserved_credits"], result["charged_credits"], result["refunded_credits"]) == (0, 0, 0, 0)
    assert result["scope"]["price_quote"]["maximum_credits"] == count * 2
    assert db.get(core.User, 1).job_service_credits == 50
    assert db.query(models.EmployerJobDelivery).count() == 0


def test_equal_scores_keep_existing_id_tiebreak_within_new_and_saved_groups(indexed_search):
    db = indexed_search
    db.query(models.EmployerPosting).update({models.EmployerPosting.description: "Python Engineer"})
    db.commit()
    first = search(db, "equal-score-first")
    assert first["items"][0]["posting"]["id"] == "job_1"
    second = search(db, "equal-score-second", count=2)
    assert [item["posting"]["id"] for item in second["items"]] == ["job_2", "job_1"]
    assert second["items"][0]["fit"]["score"] == second["items"][1]["fit"]["score"]
    exhausted = search(db, "equal-score-exhausted", count=2)
    assert [item["posting"]["id"] for item in exhausted["items"]] == ["job_1", "job_2"]
    assert exhausted["charged_credits"] == 0


def test_search_price_one_and_saved_quote_remain_bound_when_configuration_changes(indexed_search, monkeypatch):
    # The principal repro uses the existing synthetic price-two fixture.
    # This control separately preserves the current one-credit search contract.
    db = indexed_search
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "1")
    first = search(db, "price-bound-first")
    original = deepcopy(first)
    assert first["charged_credits"] == 1
    assert first["scope"]["price_quote"]["unit_price"] == 1
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "3")
    assert search(db, "price-bound-first") == original
    second = search(db, "price-bound-second")
    assert second["items"][0]["posting"]["id"] == "job_2"
    assert second["charged_credits"] == 3
    assert second["scope"]["price_quote"]["unit_price"] == 3
    assert second["scope"]["price_quote"]["maximum_credits"] == 3
    assert db.get(core.User, 1).job_service_credits == 46


def _legacy_alias(db):
    first = search(db, "legacy-snapshot-first")
    delivery = db.query(models.EmployerJobDelivery).filter_by(user_id=1).one()
    delivery.opening_key = None
    db.add(models.EmployerPosting(id="job_0_alias", source_id="source_index", external_id="alias-1",
        requisition_id="req-1", title="Python Engineer", employer="Synthetic Employer",
        location="Bengaluru", description="Python PostgreSQL Engineer",
        canonical_url="https://job-boards.greenhouse.io/synthetic/jobs/alias-1",
        apply_url="https://job-boards.greenhouse.io/synthetic/jobs/alias-1",
        content_sha256="a" * 64, last_checked_at=utcnow()))
    db.commit()
    return db.get(models.EmployerSearch, first["id"]), delivery


def test_legacy_snapshot_keeps_paid_alias_free_after_original_metadata_changes(indexed_search):
    db = indexed_search
    original, delivery = _legacy_alias(db)
    historical_key = original.items[0]["posting"]["opening_key"]
    posting = db.get(models.EmployerPosting, delivery.posting_id)
    posting.requisition_id, posting.is_open = "different-current-requisition", False
    db.commit()
    assert admissions.opening_identity(posting, db.get(models.EmployerSource, "source_index")) != historical_key
    result = search(db, "legacy-changed-current", count=2)
    assert [item["posting"]["id"] for item in result["items"]] == ["job_2", "job_0_alias"]
    assert [item["charged_credits"] for item in result["items"]] == [2, 0]
    assert (result["reserved_credits"], result["charged_credits"], result["refunded_credits"]) == (4, 2, 2)
    assert db.get(core.User, 1).job_service_credits == 46
    assert db.query(models.EmployerJobDelivery).filter_by(user_id=1).count() == 2
    assert delivery.opening_key is None  # No historical ledger backfill.


@pytest.mark.parametrize("damage", ["missing", "wrong_posting", "wrong_owner", "wrong_search",
    "duplicate", "conflicting", "bad_key", "uppercase_key", "bool_charge", "wrong_charge"])
def test_uncertain_legacy_snapshot_refuses_new_charge_without_guessing_identity(indexed_search, damage):
    from fastapi import HTTPException

    db = indexed_search
    original, delivery = _legacy_alias(db)
    items = deepcopy(original.items)
    if damage == "missing":
        items = []
    elif damage == "wrong_posting":
        items[0]["posting"]["id"] = "other-posting"
    elif damage == "wrong_owner":
        original.user_id = 2
    elif damage == "wrong_search":
        other = search(db, "other-owner-history", owner=2)
        delivery.search_id = other["id"]
    elif damage == "duplicate":
        items.append(deepcopy(items[0]))
    elif damage == "conflicting":
        other_item = deepcopy(items[0])
        other_item["posting"]["opening_key"] = "b" * 64
        items.append(other_item)
    elif damage == "bad_key":
        items[0]["posting"]["opening_key"] = "not-a-canonical-key"
    elif damage == "uppercase_key":
        items[0]["posting"]["opening_key"] = items[0]["posting"]["opening_key"].upper()
    elif damage == "bool_charge":
        items[0]["charged_credits"] = True
    else:
        items[0]["charged_credits"] += 1
    original.items = items
    db.commit()
    before_events = db.query(models.ServiceCreditEvent).filter_by(user_id=1).count()
    with pytest.raises(HTTPException) as refused:
        search(db, "uncertain-legacy-progress", count=2)
    assert refused.value.status_code == 409
    assert refused.value.detail["code"] == "legacy_delivery_identity_unavailable"
    db.rollback()
    assert db.get(core.User, 1).job_service_credits == 48
    assert db.query(models.EmployerJobDelivery).filter_by(user_id=1).count() == 1
    assert db.query(models.ServiceCreditEvent).filter_by(user_id=1).count() == before_events
    preparation = db.query(models.ServiceCreditReservation).filter_by(user_id=1, state="reserved").one()
    assert preparation.reserved_amount == 0
    assert preparation.cost_policy_snapshot["search_preparation"] is True
    assert not db.query(models.EmployerSearch).filter_by(user_id=1, idempotency_key="uncertain-legacy-progress").first()


def test_uncertain_legacy_history_preserves_physical_saved_free_and_empty_searches(indexed_search):
    db = indexed_search
    original = search(db, "all-physical-first", count=2)
    row = db.get(models.EmployerSearch, original["id"])
    row.items = []
    db.query(models.EmployerJobDelivery).filter_by(user_id=1).update({models.EmployerJobDelivery.opening_key: None})
    db.get(core.User, 1).job_service_credits = 0
    db.commit()
    saved = search(db, "all-physical-saved", count=2)
    assert [item["posting"]["id"] for item in saved["items"]] == ["job_1", "job_2"]
    assert [item["charged_credits"] for item in saved["items"]] == [0, 0]
    assert (saved["reserved_credits"], saved["charged_credits"], saved["refunded_credits"]) == (0, 0, 0)
    db.query(models.EmployerPosting).update({models.EmployerPosting.is_open: False})
    db.commit()
    empty = search(db, "uncertain-empty")
    assert empty["items"] == [] and empty["charged_credits"] == 0
    assert db.get(core.User, 1).job_service_credits == 0


def test_legacy_alias_uses_finite_requested_funding_and_free_saved_result(indexed_search):
    from fastapi import HTTPException

    db = indexed_search
    _legacy_alias(db)
    db.get(core.User, 1).job_service_credits = 2
    db.commit()
    with pytest.raises(HTTPException) as insufficient:
        search(db, "finite-mixed", count=2)
    assert insufficient.value.status_code == 402
    assert insufficient.value.detail["required"] == 4
    db.rollback()
    assert db.get(core.User, 1).job_service_credits == 2
    result = search(db, "finite-single")
    assert result["items"][0]["posting"]["id"] == "job_2"
    assert result["charged_credits"] == 2 and db.get(core.User, 1).job_service_credits == 0
    saved = search(db, "finite-saved", count=2)
    assert [item["posting"]["id"] for item in saved["items"]] == ["job_0_alias", "job_2"]
    assert saved["charged_credits"] == 0 and saved["reserved_credits"] == 0
    assert search(db, "finite-single") == result
