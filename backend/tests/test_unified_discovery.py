"""Actual local SQL discovery delivery and credit settlement, no model/provider IO."""

from datetime import timedelta

import pytest
from backend.app import models as core
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.credits import discovery, runtime, service
from backend.app.domains.credits.discovery_models import (
    CreditDiscoveryBatch,
    CreditDiscoveryDelivery,
)
from backend.app.domains.credits.discovery_schemas import DiscoveryCreate, DiscoveryRequest
from backend.app.domains.credits.models import CreditExecutionHold, CreditReservation
from backend.app.domains.dispatch.models import DispatchOutbox
from backend.app.domains.employer import delivery_history, models, schemas
from backend.app.domains.employer import service as employer
from backend.tests.test_employer_search_new_results import indexed_search
from backend.tests.test_unified_credits import grant
from backend.tests.test_unified_execution import budgets, financial_policy, route
from fastapi import HTTPException
from pydantic import ValidationError

assert indexed_search


def configure(monkeypatch):
    selected = route(native=True).model_copy(
        update={
            "supported_limits": {
                "context_bytes": 32768,
                "candidate_limit": 1000,
                "candidate_content_bytes": 2_000_000,
                "maximum_results": 5,
            }
        }
    )
    configuration = runtime.RuntimeConfig(
        policy=financial_policy(), budgets=budgets(), routes=(selected,)
    )
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "true")
    monkeypatch.setenv("UNIFIED_CREDIT_CANARY_OWNER_IDS", "1")
    monkeypatch.setenv("UNIFIED_CREDIT_RUNTIME_REVIEW_JSON", configuration.model_dump_json())
    monkeypatch.setenv("EMPLOYER_PAID_HISTORY_MODE", "indexed")
    return configuration


@pytest.fixture
def discovery_db(indexed_search, monkeypatch):
    configure(monkeypatch)
    grant(indexed_search)
    indexed_search.commit()
    return indexed_search


def quoted(db, key="batch-one", **changes):
    payload = DiscoveryRequest.model_validate(
        {"resume_id": 10, "role": "Python Engineer", **changes}
    )
    q = discovery.quote(db, user_id=1, payload=payload, idempotency_key="quote-" + key)
    db.commit()
    return DiscoveryCreate(
        **payload.model_dump(), accepted_service_quote_id=q.id, idempotency_key=key
    )


def completed(db, key="batch-one", **changes):
    payload = quoted(db, key, **changes)
    result = discovery.create(db, user_id=1, payload=payload)
    assert result.status == "preparing" and result.reserved_credits == 1
    return payload, discovery.execute(db, user_id=1, search_id=result.id)


def test_two_result_partial_batch_costs_one_credit_and_has_no_legacy_per_job_proof(discovery_db):
    db = discovery_db
    _, result = completed(db)
    assert result.status == "completed", result.scope
    assert (
        result.delivered_count,
        result.charged_credits,
        result.refunded_credits,
        result.partial_batch,
    ) == (2, 1, 0, True)
    assert all(item["charged_credits"] == 0 and item["included_in_batch"] for item in result.items)
    assert service.wallet(db, user_id=1).available_credits == 39
    assert db.get(core.User, 1).job_service_credits == 50
    assert (
        db.query(models.ServiceCreditReservation).count()
        == db.query(models.EmployerJobDelivery).count()
        == 0
    )
    assert db.query(CreditDiscoveryDelivery).count() == 2
    hold = db.query(CreditExecutionHold).one()
    assert hold.delivery_state == "delivered" and hold.native_settled_minor is None
    assert hold.state == "outcome_unknown" and hold.reserved_minor == 13
    assert db.query(core.ModelCostLiability).count() == 0
    assert db.query(DispatchOutbox).filter_by(topic="employer.search").count() == 1


def test_zero_results_release_credit_but_not_unknown_native_cost(discovery_db):
    db = discovery_db
    db.query(models.EmployerPosting).update({"is_open": False})
    db.commit()
    _, result = completed(db)
    assert (
        result.status,
        result.delivered_count,
        result.charged_credits,
        result.refunded_credits,
    ) == ("completed", 0, 0, 1)
    assert service.wallet(db, user_id=1).available_credits == 40
    hold = db.query(CreditExecutionHold).one()
    assert (
        hold.delivery_state == "failed"
        and hold.reserved_minor == 13
        and hold.native_settled_minor is None
    )


def test_following_batch_excludes_both_physical_and_canonical_deliveries(discovery_db):
    db = discovery_db
    completed(db, "first-discovery", desired_count=1)
    db.add(
        models.EmployerPosting(
            id="alias-one",
            source_id="source_index",
            external_id="alias-one",
            requisition_id="req-1",
            title="Python Engineer",
            employer="Synthetic Employer",
            location="Bengaluru",
            description="Python PostgreSQL Engineer",
            canonical_url="https://example.com/alias",
            apply_url="https://example.com/alias",
            content_sha256="a" * 64,
            last_checked_at=utcnow(),
        )
    )
    db.commit()
    _, second = completed(db, "second-discovery", desired_count=5)
    assert second.delivered_count == 1 and second.items[0]["posting"]["id"] == "job_2"
    _, third = completed(db, "third-discovery")
    assert third.delivered_count == third.charged_credits == 0
    assert service.wallet(db, user_id=1).available_credits == 38


def test_maximum_five_and_full_batch_charge(discovery_db):
    db = discovery_db
    for number in range(3, 8):
        db.add(
            models.EmployerPosting(
                id=f"job_{number}",
                source_id="source_index",
                external_id=str(number),
                requisition_id=f"req-{number}",
                title="Python Engineer",
                employer="Synthetic Employer",
                location="Bengaluru",
                description="Python Engineer",
                canonical_url=f"https://example.com/{number}",
                apply_url=f"https://example.com/{number}",
                content_sha256=payload_fingerprint(number),
                last_checked_at=utcnow(),
            )
        )
    db.commit()
    _, result = completed(db)
    assert result.delivered_count == 5 and result.charged_credits == 1 and not result.partial_batch
    with pytest.raises(ValidationError):
        DiscoveryRequest(resume_id=10, role="Python Engineer", desired_count=6)


def test_accepted_replay_survives_pause_and_deleted_source(discovery_db, monkeypatch):
    db = discovery_db
    payload, first = completed(db)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    db.query(core.Resume).filter_by(id=10).delete()
    db.commit()
    assert discovery.create(db, user_id=1, payload=payload) == first
    assert db.query(CreditReservation).count() == 1
    with pytest.raises(HTTPException) as error:
        discovery.status(db, user_id=2, search_id=first.id)
    assert error.value.status_code == 404


def test_source_change_before_delivery_refunds_without_native_retry(discovery_db, monkeypatch):
    db = discovery_db
    payload = quoted(db)
    accepted = discovery.create(db, user_id=1, payload=payload)
    original = employer._stage_bounded_search

    def stage(*args):
        result = original(*args)
        db.get(models.EmployerPosting, "job_1").is_open = False
        db.commit()
        return result

    monkeypatch.setattr(employer, "_stage_bounded_search", stage)
    result = discovery.execute(db, user_id=1, search_id=accepted.id)
    assert result.status == "failed" and result.refunded_credits == 1
    assert db.query(CreditDiscoveryDelivery).count() == 0
    assert discovery.execute(db, user_id=1, search_id=accepted.id).status == "failed"
    assert service.wallet(db, user_id=1).available_credits == 40


def test_old_accepted_history_is_excluded_without_rewriting_its_proofs(indexed_search, monkeypatch):
    db = indexed_search
    from backend.tests.test_employer_search_new_results import search

    old = search(db, "legacy-discovery-first", count=1)
    old_amount = db.query(models.EmployerJobDelivery).one().charged_credits
    configure(monkeypatch)
    grant(db)
    db.commit()
    # Review/finish the existing bounded history projection, preserving proof.
    for _ in range(20):
        progress = delivery_history.advance_owner(db, 1)
        db.commit()
        if progress["state"] == "ready":
            break
    assert progress["state"] == "ready"
    _, new = completed(db)
    assert (
        new.delivered_count == 1
        and new.items[0]["posting"]["id"] != old["items"][0]["posting"]["id"]
    )
    assert db.query(models.EmployerJobDelivery).one().charged_credits == old_amount == 2
    assert db.get(core.User, 1).job_service_credits == 48


def test_cancel_and_stale_worker_keep_financial_liability(discovery_db):
    db = discovery_db
    accepted = discovery.create(db, user_id=1, payload=quoted(db))
    row, token, hold = discovery._claim(db, 1, accepted.id)
    assert token
    row.created_at = utcnow() - timedelta(minutes=6)
    db.commit()
    result = discovery.execute(db, user_id=1, search_id=accepted.id)
    assert result.status == "failed" and result.refunded_credits == 1
    assert hold.native_settled_minor is None
    assert db.query(CreditDiscoveryDelivery).count() == 0


def test_unified_buyer_cannot_fall_back_to_legacy_search_after_pause(discovery_db, monkeypatch):
    db = discovery_db
    completed(db)
    monkeypatch.setenv("UNIFIED_CREDIT_EXECUTION_ENABLED", "false")
    with pytest.raises(HTTPException) as error:
        employer.create_search(
            db,
            1,
            schemas.SearchCreate(
                resume_id=10, role="Python Engineer", idempotency_key="old-contract-new-action"
            ),
        )
    assert error.value.detail["code"] == "unified_discovery_review_required"
    assert db.get(core.User, 1).job_service_credits == 50
    assert db.query(CreditDiscoveryBatch).count() == 1


def test_abandoned_dispatch_sweep_refunds_once_without_rescanning(discovery_db, monkeypatch):
    db = discovery_db
    accepted = discovery.create(db, user_id=1, payload=quoted(db))
    row = db.get(models.EmployerSearch, accepted.id)
    row.created_at = utcnow() - timedelta(minutes=31)
    db.commit()
    monkeypatch.setattr(
        employer,
        "_prepare_bounded_search",
        lambda *args, **kwargs: pytest.fail("Abandoned work cannot rescan"),
    )
    employer.bounded_search_maintenance(db)
    result = discovery.status(db, user_id=1, search_id=accepted.id)
    assert result.status == "failed" and result.refunded_credits == 1
    employer.bounded_search_maintenance(db)
    assert service.wallet(db, user_id=1).available_credits == 40
    assert db.query(CreditExecutionHold).one().native_settled_minor is None


def test_expired_unaccepted_quote_has_definite_no_run_contract(discovery_db):
    db = discovery_db
    from backend.app.domains.credits.models import CreditServiceQuote

    payload = quoted(db)
    db.get(CreditServiceQuote, payload.accepted_service_quote_id).created_at = utcnow() - timedelta(
        minutes=16
    )
    db.get(CreditServiceQuote, payload.accepted_service_quote_id).expires_at = utcnow() - timedelta(
        seconds=1
    )
    db.commit()
    with pytest.raises(HTTPException) as caught:
        discovery.create(db, user_id=1, payload=payload)
    assert caught.value.detail["code"] == "credit_service_quote_rejected"
    assert caught.value.detail["run_created"] is False
    assert db.query(CreditReservation).count() == db.query(CreditDiscoveryBatch).count() == 0


def test_outbox_failure_rolls_back_entire_batch_and_credit_hold(discovery_db):
    from sqlalchemy import event

    db = discovery_db
    payload = quoted(db)

    def reject(_connection, _cursor, statement, *_args):
        if statement.startswith("INSERT INTO dispatch_outbox"):
            raise RuntimeError("Synthetic atomic dispatch failure")

    event.listen(db.get_bind(), "before_cursor_execute", reject)
    try:
        with pytest.raises(RuntimeError, match="Synthetic atomic"):
            discovery.create(db, user_id=1, payload=payload)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", reject)
        db.rollback()
    assert db.query(CreditReservation).count() == db.query(CreditDiscoveryBatch).count() == 0
    assert db.query(models.EmployerSearch).count() == 0
    assert service.wallet(db, user_id=1).available_credits == 40


def test_larger_changed_catalogue_is_refunded_before_loading_bodies(discovery_db, monkeypatch):
    db = discovery_db
    payload = quoted(db)
    accepted = discovery.create(db, user_id=1, payload=payload)
    db.get(models.EmployerPosting, "job_1").description = "Python Engineer " + "x" * 2_000_000
    db.commit()
    monkeypatch.setattr(
        employer, "basic_match", lambda *args: pytest.fail("Oversized bodies must not be ranked")
    )
    result = discovery.execute(db, user_id=1, search_id=accepted.id)
    assert result.status == "failed" and result.refunded_credits == 1
    assert db.query(CreditDiscoveryDelivery).count() == 0


def test_all_saved_search_transports_label_batch_credits_and_hide_worker_state(discovery_db):
    from backend.app.domains.employer import search_pages

    db = discovery_db
    payload, result = completed(db)
    direct = employer.get_search(db, 1, result.id)
    summary = search_pages.list_summaries(db, 1).items[0]
    for body in (direct, summary.model_dump(), result.model_dump()):
        assert body["billing_currency"] == "credits"
        assert body["scope"]["price_quote"]["pricing_unit"] == "completed_batch"
        assert body["scope"]["price_quote"]["maximum_credits"] == 1
        assert "lease_token" not in body["scope"]
        assert "staged_body_fingerprint" not in body["scope"]
        assert "unit_price" not in body["scope"]["price_quote"]
    assert discovery.create(db, user_id=1, payload=payload) == result
