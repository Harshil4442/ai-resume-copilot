"""Private indexed-history integration against synthetic actual SQL rows."""

import json
from copy import deepcopy

import pytest
from backend.app import models as core
from backend.app.domains.employer import admissions, delivery_history, models
from sqlalchemy import event
from test_employer_search_new_results import indexed_search as _indexed_search
from test_employer_search_new_results import search

indexed_search = _indexed_search


@pytest.fixture
def indexed(indexed_search, monkeypatch):
    monkeypatch.setenv("EMPLOYER_PAID_HISTORY_MODE", "indexed")
    return indexed_search


def historical(
    db,
    *,
    identity="old_search",
    posting="job_1",
    description="Python Engineer",
    duplicate=None,
    opening=True,
):
    source = db.get(models.EmployerSource, "source_index")
    job = db.get(models.EmployerPosting, posting)
    facts = {
        "posting": {
            "id": job.id,
            "source_id": job.source_id,
            "external_id": job.external_id,
            "platform": source.platform,
            "description": description,
        },
        "fit": {"score": 7.0},
        "charged_credits": 2,
    }
    if opening:
        facts["posting"]["opening_key"] = admissions.opening_identity(job, source)
    items = [facts]
    if duplicate == "concordant":
        items.append(
            {key: deepcopy(value) for key, value in reversed(list(facts.items()))}
        )
    if duplicate == "conflicting":
        other = deepcopy(facts)
        other["posting"]["description"] += " conflict"
        items.append(other)
    row = models.EmployerSearch(
        id=identity,
        user_id=1,
        resume_id=10,
        idempotency_key=identity,
        input_fingerprint="a" * 64,
        query={"role": "Python Engineer"},
        desired_count=1,
        delivered_count=1,
        reserved_credits=2,
        charged_credits=2,
        refunded_credits=0,
        scope={},
        items=items,
    )
    db.add(row)
    db.flush([row])
    delivery = models.EmployerJobDelivery(
        id="delivery_" + identity,
        user_id=1,
        posting_id=posting,
        search_id=identity,
        opening_key=None,
        charged_credits=2,
    )
    db.add(delivery)
    db.commit()
    return row, delivery


def complete_history(db, **budgets):
    receipts = []
    for _ in range(10_000):
        receipt = delivery_history.advance_owner(db, 1, **budgets)
        db.commit()
        receipts.append(receipt)
        assert receipt["bytes"] <= budgets.get(
            "byte_budget", delivery_history.MAX_SOURCE_BYTES_PER_CHUNK
        )
        assert receipt["work"] <= budgets.get(
            "work_budget", delivery_history.MAX_WORK_PER_CHUNK
        )
        if receipt["state"] in {"ready", "unresolved"}:
            return receipts
    pytest.fail("Finite history failed to resume to a terminal readiness state")


def test_indexed_new_paid_dual_writes_exact_quote_and_free_replay(indexed, monkeypatch):
    db = indexed
    first = search(db, "stage-c-first")
    assert (
        first["charged_credits"] == 2 and db.get(core.User, 1).job_service_credits == 48
    )
    proof = db.query(models.EmployerPaidDeliveryHistory).one()
    assert (
        proof.state == "proven"
        and proof.original_charged_credits == proof.snapshot_charged_credits == 2
    )
    assert proof.identity_proof["tenant"] == "synthetic"
    assert (
        db.query(models.EmployerPaidOpeningRegistry).one().canonical_delivery_id
        == proof.delivery_id
    )
    original = deepcopy(first)
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "9")
    assert search(db, "stage-c-first") == original
    second = search(db, "stage-c-second", count=2)
    assert [item["charged_credits"] for item in second["items"]] == [9, 0]
    assert second["scope"]["price_quote"]["unit_price"] == 9
    assert db.query(models.EmployerPaidDeliveryHistory).count() == 2


@pytest.mark.parametrize("duplicate", [None, "concordant", "conflicting"])
def test_resumable_large_snapshot_and_semantic_duplicates(indexed, duplicate):
    db = indexed
    historical(db, description="Long résumé 🌍 " * 3000, duplicate=duplicate)
    receipts = complete_history(db, byte_budget=512, work_budget=1024)
    assert len(receipts) > 30 and receipts[0]["state"] == "backfilling"
    assert receipts[-1]["state"] == (
        "unresolved" if duplicate == "conflicting" else "ready"
    )
    seen = db.query(models.EmployerSearchHistorySeen).one()
    assert seen.occurrence_count == (1 if duplicate is None else 2)
    assert "description" not in seen.facts["posting"]
    assert seen.conflicting == (duplicate == "conflicting")
    progress = db.query(models.EmployerSearchHistoryProgress).one()
    assert progress.byte_offset == progress.source_size_bytes
    assert "Long résumé" not in json.dumps(
        progress.parser_checkpoint, ensure_ascii=False
    )
    proof = db.query(models.EmployerPaidDeliveryHistory).one()
    assert proof.original_charged_credits == 2
    assert proof.state == ("unresolved" if duplicate == "conflicting" else "proven")


def test_ready_paid_admission_reads_no_legacy_items(indexed):
    db = indexed
    historical(db, description="large " * 10_000)
    complete_history(db)
    statements = []

    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    try:
        result = search(db, "bounded-normal-next")
    finally:
        event.remove(db.bind, "before_cursor_execute", capture)
    assert (
        result["items"][0]["posting"]["id"] == "job_2"
        and result["charged_credits"] == 2
    )
    reads = [s for s in statements if "FROM employer_searches" in s]
    assert not any("substring(" in s.lower() or "substr(" in s.lower() for s in reads)
    # Only finite idempotency/current result ORM reads remain, never an owner
    # historical search join or old saved-items scan for paid membership.
    assert not any(
        "employer_job_deliveries" in s and "employer_searches.items" in s
        for s in statements
    )


@pytest.mark.parametrize("empty", [False, True])
def test_pending_or_unresolved_history_keeps_paid_physical_and_empty_free(
    indexed, empty
):
    db = indexed
    historical(db, description="large " * 100_000, opening=False)
    db.get(models.EmployerPosting, "job_2").is_open = False
    if empty:
        db.get(models.EmployerPosting, "job_1").is_open = False
    db.get(core.User, 1).job_service_credits = 0
    db.commit()
    result = search(db, "pending-free", count=1)
    assert result["charged_credits"] == result["reserved_credits"] == 0
    assert len(result["items"]) == (0 if empty else 1)
    assert db.query(models.EmployerPaidHistoryProgress).one().state == "backfilling"
    assert db.query(models.EmployerPaidOpeningRegistry).count() == 0


def test_zero_preparation_resumes_same_quote_after_history_wait(indexed, monkeypatch):
    from fastapi import HTTPException

    db = indexed
    historical(db, description="large " * 10000)
    with pytest.raises(HTTPException) as waiting:
        search(db, "history-resumable")
    assert waiting.value.detail["code"] == "paid_delivery_history_preparing"
    db.rollback()
    prep = db.query(models.ServiceCreditReservation).filter_by(state="reserved").one()
    assert prep.reserved_amount == 0 and prep.unit_price == 2
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "9")
    complete_history(db)
    result = search(db, "history-resumable")
    assert (
        result["charged_credits"] == 2
        and result["scope"]["price_quote"]["unit_price"] == 2
    )
    assert db.get(core.User, 1).job_service_credits == 48


def test_dual_write_failure_rolls_back_money_and_result(indexed, monkeypatch):
    db = indexed

    def broken(*args):
        raise RuntimeError("synthetic failure before history registry")

    monkeypatch.setattr(delivery_history, "_registry", broken)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        search(db, "atomic-history-failure")
    db.rollback()
    assert db.get(core.User, 1).job_service_credits == 50
    assert (
        db.query(models.EmployerSearch).count()
        == db.query(models.EmployerJobDelivery).count()
        == 0
    )
    assert (
        db.query(models.EmployerPaidDeliveryHistory).count()
        == db.query(models.EmployerPaidOpeningRegistry).count()
        == 0
    )
    assert db.query(models.ServiceCreditReservation).one().reserved_amount == 0


def test_missing_key_is_explicit_review_not_empty_ready(indexed):
    from fastapi import HTTPException

    db = indexed
    historical(db, opening=False)
    receipts = complete_history(db)
    assert receipts[-1]["state"] == "unresolved"
    assert (
        db.query(models.EmployerPaidDeliveryHistory).one().static_reason
        == "historical_identity_archive_missing"
    )
    with pytest.raises(HTTPException) as refused:
        search(db, "cannot-invent-key")
    assert refused.value.detail["code"] == "legacy_delivery_identity_unavailable"
    db.rollback()
    assert db.get(core.User, 1).job_service_credits == 50


def test_aggregate_budget_spans_two_searches(indexed):
    db = indexed
    historical(db, identity="old_a", posting="job_1", description="x" * 500)
    historical(db, identity="old_b", posting="job_2", description="y" * 500)
    receipts = complete_history(
        db, delivery_budget=8, byte_budget=1024, work_budget=4096
    )
    assert receipts[-1]["state"] == "ready" and len(receipts) > 1
    assert db.query(models.EmployerPaidDeliveryHistory).count() == 2


def test_full_valid_unicode_identity_fields_fit_new_proof(indexed):
    db = indexed
    job = db.get(models.EmployerPosting, "job_1")
    job.external_id = "🌍" * 160
    job.requisition_id = "🌍" * 160
    db.commit()
    result = search(db, "max-valid-identity")
    assert result["charged_credits"] == 2
    proof = db.query(models.EmployerPaidDeliveryHistory).one()
    assert proof.identity_proof["external_id"] == "🌍" * 160
    assert proof.identity_proof["requisition_id"] == "🌍" * 160
    assert proof.original_charged_credits == 2


def test_full_valid_unicode_external_preserved_in_legacy_facts(indexed):
    db = indexed
    db.get(models.EmployerPosting, "job_1").external_id = "🌍" * 160
    db.commit()
    historical(db)
    receipts = complete_history(db, byte_budget=512, work_budget=1024)
    assert receipts[-1]["state"] == "ready"
    seen = db.query(models.EmployerSearchHistorySeen).one()
    assert seen.facts["posting"]["external_id"] == "🌍" * 160
