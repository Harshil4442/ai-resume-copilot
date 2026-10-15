"""Actual PostgreSQL one-shot binary claims and unlocked-read revocation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from backend.tests import test_browser_assistance_isolated as flow
from backend.tests import test_browser_attachment_isolated as checks
from backend.tests import test_employer_admissions_postgres as admission
from backend.tests.test_browser_assistance_postgres import browser_pg as browser_pg
from backend.tests.test_browser_assistance_postgres import browser_pg_engine as browser_pg_engine
from backend.tests.test_browser_typed_migration_postgres import migrated_browser as migrated_browser
from fastapi import HTTPException


@pytest.mark.parametrize("same_request", [False, True])
def test_competing_binary_deliveries_claim_and_return_bytes_only_once(browser_pg, monkeypatch, same_request):
    owned, factory = browser_pg
    monkeypatch.setenv("BROWSER_ASSISTANCE_ATTACHMENT_ENABLED", "true")
    review, lease = checks.approved_action(owned)
    request = owned.request("artifact", checks.delivery_body(review, lease))
    requests = [request, request if same_request else owned.request("artifact", checks.delivery_body(review, lease))]
    start = Barrier(2)

    def deliver(request):
        with factory() as db:
            start.wait(timeout=5)
            try:
                return flow.service.device(db, owned.origin, request)
            except HTTPException as failure:
                db.rollback()
                return failure.status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(deliver, requests))
    assert sum(isinstance(result, flow.attachments.BinaryDelivery) for result in results) == 1
    assert results.count(409) == 1
    with factory() as db:
        assert db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 1
        action = db.get(flow.models.BrowserAssistanceRequest, lease["payload"]["action_id"])
        assert action.response == {"delivery_claimed": True}
        assert db.query(flow.employer.ServiceCreditReservation).count() == 0


def test_revocation_can_commit_during_storage_read_and_stops_binary_claim(browser_pg, monkeypatch):
    owned, factory = browser_pg
    monkeypatch.setenv("BROWSER_ASSISTANCE_ATTACHMENT_ENABLED", "true")
    review, lease = checks.approved_action(owned)
    request = owned.request("artifact", checks.delivery_body(review, lease))
    reading, release = Event(), Event()
    read = flow.artifact_reader.read

    def paused_read(artifact, **kwargs):
        content = read(artifact, **kwargs)
        reading.set()
        assert release.wait(timeout=5)
        return content

    monkeypatch.setattr(flow.artifact_reader, "read", paused_read)

    def deliver():
        with factory() as db:
            try:
                return flow.service.device(db, owned.origin, request)
            except HTTPException as failure:
                db.rollback()
                return failure.status_code

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(deliver)
            assert reading.wait(timeout=5)
            with factory() as revoker:
                flow.service.candidate(revoker, 1, "revoke", flow.schemas.CandidateRequest(identity=owned.device_id, confirmed=True))
            release.set()
            assert pending.result(timeout=5) == 403
    finally:
        release.set()
    with factory() as db:
        assert db.get(flow.models.BrowserAssistanceRequest, lease["payload"]["action_id"]).response == {}
        assert db.query(flow.models.BrowserAssistanceRequest).filter_by(operation="artifact").count() == 0


def test_actual_migrated_schema19_retains_attachment_selection_kind_after_payload_erasure(migrated_browser, monkeypatch):
    owned = migrated_browser
    monkeypatch.setenv("BROWSER_ASSISTANCE_ATTACHMENT_ENABLED", "true")
    admission._migrate(owned.engine, "20261015_0019")
    review, lease = checks.approved_action(owned)
    delivered = checks.delivery(owned, review, lease)
    assert isinstance(delivered, flow.attachments.BinaryDelivery)
    assert flow.hashlib.sha256(delivered.content).hexdigest() == review["payload"]["artifact"]["sha256"]
    selected = owned.call("outcome", checks.outcome_body(review, lease))
    assert selected["state"] == "filled" and selected["attachment_status"] == "selected_upload_unconfirmed"
    row = owned.db.get(flow.models.BrowserAssistanceReview, review["id"])
    row.expires_at = flow.service.now() - flow.service.timedelta(seconds=1)
    owned.db.commit()
    flow.service.expire_reviews(owned.db)
    owned.db.refresh(row)
    assert row.payload == {} and row.review_protocol_version == 3 and row.next_index == 1
    owned.db.rollback()
    with pytest.raises(RuntimeError, match="Typed-control review history must be retained"):
        admission._migrate(owned.engine, "20261015_0018", direction="downgrade")
    checks.assert_no_settlement(owned)
