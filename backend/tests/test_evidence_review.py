"""Exact reviewed snapshots; SQLite controls do not claim native row-lock proof."""
from datetime import datetime, timedelta

import pytest
from backend.app import models
from backend.app.database import Base, get_db
from backend.app.domains.career import schemas, service
from backend.app.routers.v1.career import router
from backend.app.security import get_current_user
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


def seed(factory):
    with factory() as db:
        db.add_all([models.User(id=1, email="review-owner@example.invalid"),
                    models.User(id=2, email="review-other@example.invalid")])
        db.commit()
        item = service.create_evidence(db, 1, schemas.EvidenceCreate(
            category="experience", title="Reviewed fact", evidence_text="Built reliable systems.",
            metrics={"count": 2}, skills=["Python"], source_ref="synthetic:source"))
        return item.id, schemas.evidence_snapshot_fingerprint(item)


@pytest.fixture
def context():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    identifier, fingerprint = seed(factory)
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    def database():
        with factory() as db:
            yield db
    def owner():
        with factory() as db:
            return db.get(models.User, 1)
    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_current_user] = owner
    with TestClient(app) as client:
        yield factory, client, identifier, fingerprint
    engine.dispose()


def test_api_stale_approve_cannot_approve_unseen_edit_and_fresh_review_succeeds(context):
    factory, client, identifier, shown = context
    listed = client.get("/api/v1/evidence-items").json()[0]
    assert listed["snapshot_fingerprint"] == shown
    changed = client.patch(f"/api/v1/evidence-items/{identifier}", json={
        "expected_fingerprint": shown, "evidence_text": "Built changed systems."})
    assert changed.status_code == 200
    newer = changed.json()["snapshot_fingerprint"]
    assert newer != shown and changed.json()["approval_state"] == "pending"
    stale = client.patch(f"/api/v1/evidence-items/{identifier}", json={
        "expected_fingerprint": shown, "approval_state": "approved"})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "evidence_refresh_required"
    assert "snapshot_fingerprint" not in stale.text
    with factory() as db:
        assert db.get(models.EvidenceItem, identifier).approval_state == "pending"
    approved = client.patch(f"/api/v1/evidence-items/{identifier}", json={
        "expected_fingerprint": newer, "approval_state": "approved"})
    assert approved.status_code == 200 and approved.json()["approval_state"] == "approved"
    # Replaying even a successful approval is a stale request, not a new review.
    assert client.patch(f"/api/v1/evidence-items/{identifier}", json={
        "expected_fingerprint": newer, "approval_state": "rejected"}).status_code == 409


@pytest.mark.parametrize("changes", [{"approval_state": "approved"}, {"evidence_text": "Unversioned edit"}])
def test_missing_version_from_older_client_fails_closed(context, changes):
    factory, client, identifier, shown = context
    response = client.patch(f"/api/v1/evidence-items/{identifier}", json=changes)
    assert response.status_code == 409
    with factory() as db:
        assert schemas.evidence_snapshot_fingerprint(db.get(models.EvidenceItem, identifier)) == shown


def test_stale_editor_cannot_overwrite_a_new_review_state(context):
    factory, client, identifier, opened = context
    approved = client.patch(f"/api/v1/evidence-items/{identifier}", json={
        "expected_fingerprint": opened, "approval_state": "approved"})
    assert approved.status_code == 200
    refused = client.patch(f"/api/v1/evidence-items/{identifier}", json={
        "expected_fingerprint": opened, "evidence_text": "Stale editor text."})
    assert refused.status_code == 409
    with factory() as db:
        row = db.get(models.EvidenceItem, identifier)
        assert row.evidence_text == "Built reliable systems." and row.approval_state == "approved"
        assert schemas.evidence_snapshot_fingerprint(row) == approved.json()["snapshot_fingerprint"]


def test_foreign_evidence_is_404_before_version_comparison(context):
    factory, client, _, _ = context
    with factory() as db:
        other = service.create_evidence(db, 2, schemas.EvidenceCreate(
            category="experience", title="Private fact", evidence_text="Other owner's fact."))
        identifier = other.id
    for token in (None, "0" * 64):
        response = client.patch(f"/api/v1/evidence-items/{identifier}", json={
            "expected_fingerprint": token, "approval_state": "approved"})
        assert response.status_code == 404 and response.json()["detail"] == "Evidence item not found"


def test_stale_sqlalchemy_identity_is_refreshed_before_version_check(context):
    factory, _, identifier, shown = context
    with factory() as stale, factory() as writer:
        cached = stale.get(models.EvidenceItem, identifier)
        service.update_evidence(writer, 1, identifier, schemas.EvidenceUpdate(
            expected_fingerprint=shown, evidence_text="A newly saved fact."))
        assert cached.evidence_text == "Built reliable systems."
        with pytest.raises(HTTPException) as error:
            service.update_evidence(stale, 1, identifier, schemas.EvidenceUpdate(
                expected_fingerprint=shown, approval_state="approved"))
        assert error.value.status_code == 409
    with factory() as db:
        assert db.get(models.EvidenceItem, identifier).approval_state == "pending"


@pytest.mark.parametrize("field,value", [
    ("id", "different-id"), ("resume_id", 42), ("category", "projects"), ("title", "Changed title"),
    ("evidence_text", "Built reliable systems. "), ("metrics", {"count": 3}),
    ("skills", ["Python", "SQL"]), ("source_ref", "synthetic:changed"),
    ("provenance", "resume_extraction"), ("approval_state", "approved"), ("confidence", "extracted"),
    ("created_at", datetime(2026, 1, 1)), ("updated_at", datetime(2026, 1, 2)),
])
def test_snapshot_binds_every_response_field_without_normalizing_text(context, field, value):
    factory, _, identifier, shown = context
    with factory() as db:
        response = schemas.EvidenceResponse.model_validate(db.get(models.EvidenceItem, identifier))
    changed = response.model_copy(update={field: value})
    assert schemas.evidence_snapshot_fingerprint(changed) != shown
    assert changed.snapshot_fingerprint == schemas.evidence_snapshot_fingerprint(changed)


def test_metrics_key_order_is_canonical_but_skill_order_and_timestamp_are_exact(context):
    factory, _, identifier, _ = context
    with factory() as db:
        response = schemas.EvidenceResponse.model_validate(db.get(models.EvidenceItem, identifier))
    first = response.model_copy(update={"metrics": {"a": 1, "b": 2}, "skills": ["A", "B"]})
    same = first.model_copy(update={"metrics": {"b": 2, "a": 1}})
    assert first.snapshot_fingerprint == same.snapshot_fingerprint
    assert first.snapshot_fingerprint != first.model_copy(update={"skills": ["B", "A"]}).snapshot_fingerprint
    assert first.snapshot_fingerprint != first.model_copy(update={
        "updated_at": first.updated_at + timedelta(microseconds=1)}).snapshot_fingerprint
