"""Real local SQL/native SDK/HTTP-adapter path, with no live provider traffic.

Native operator baselines and employer catalogue records are synthetic. Native
enrollment, consent, BEGINs and outcomes run through the configured production
code; PostgreSQL owns the actual detached mail ledger. HTTP is intercepted.
"""
from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from test_candidate_production_factory import NOW, PASSWORD, register
from test_candidate_production_factory import configured as configured
from test_mail_budget_postgres import owned_pg_url as owned_pg_url

from app import database
from app.database import Base
from app.domains.candidate_accounts.account_erasure import erase_account_sql
from app.domains.career.evidence_fence_definition import install as install_evidence_fence
from app.domains.employer import models as employer
from app.domains.employer.admissions import opening_identity
from app.domains.notification_consent import service
from app.domains.notification_consent.models import NotificationPreference
from app.domains.notifications import digest_delivery as delivery
from app.domains.notifications import mail_budget, provider
from app.domains.recovery.gcp_scoped_census import validate_scoped_rows
from app.models import AdminAuditEvent, NotificationOutbox, Opportunity, User
from app.routers import notification_preferences
from app.security import create_access_token

STAMP = datetime.fromtimestamp(NOW / 1000, UTC)


class Bytes(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        yield self.body


@pytest.fixture
def pipeline(owned_pg_url, configured, monkeypatch):
    c = configured
    schema = "digest_pipeline_" + uuid4().hex
    admin = create_engine(owned_pg_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    old_source = c.source
    c.source = create_engine(owned_pg_url, connect_args={"options":
        f"-csearch_path={schema} -clock_timeout=5000 -cstatement_timeout=10000 -ctimezone=UTC"})
    old_source.dispose()
    Base.metadata.create_all(c.source)
    with c.source.begin() as connection:
        install_evidence_fence(connection, schema)
    factory = sessionmaker(c.source, expire_on_commit=False, autoflush=False)
    monkeypatch.setattr(database, "engine", c.source)
    monkeypatch.setattr(database, "SessionLocal", factory)
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "true")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    monkeypatch.setenv("RESEND_API_KEY", "synthetic-resend-key")
    monkeypatch.setenv("EMAIL_FROM", "HireWiz <account@example.invalid>")
    for module in (service, delivery, mail_budget):
        monkeypatch.setattr(module, "utcnow", lambda: STAMP)
    uid = register(c)
    login = c.accounts.login(email="synthetic@example.test", password=PASSWORD)
    c.uid, c.context, c.factory = uid, login.context, factory
    c.provider_calls, c.statuses = [], [200]

    def http(request):
        assert not c.sql_active and not c.native.views
        assert str(request.url) == provider.ENDPOINT and request.method == "POST"
        c.provider_calls.append(request)
        status = c.statuses.pop(0) if len(c.statuses) > 1 else c.statuses[0]
        headers = {"Content-Type": "application/json", "Retry-After": "0"}
        body = b'{"id":"synthetic-mail-123"}' if status == 200 else b'{"error":"synthetic"}'
        return httpx.Response(status, headers=headers, stream=Bytes(body), request=request)

    monkeypatch.setattr(provider.httpx, "AsyncHTTPTransport", lambda **kwargs: httpx.MockTransport(http))
    app = FastAPI()
    app.include_router(notification_preferences.router, prefix="/api")
    app.include_router(notification_preferences.public_router, prefix="/api")
    jwt = create_access_token(subject=str(uid), candidate_lifetime={"version": 1,
        "context": login.context.model_dump(mode="json"), "auth_generation": login.auth_generation})
    c.client = TestClient(app, headers={"Authorization": "Bearer " + jwt},
                          client=("owned-fixture-" + uuid4().hex, 50000))
    try:
        yield c
    finally:
        c.client.close()
        c.source.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def opt_in(c):
    response = c.client.put("/api/v1/notifications/preferences", json={"expected_generation": 0,
        "weekly_digest": True, "explicit_confirmation": True})
    assert response.status_code == 200, response.text
    assert response.json()["weekly_digest"] is True and response.json()["generation"] == 1
    assert set(response.json()) == {"weekly_digest", "generation", "policy_version", "next_digest_at", "projection_current"}
    with c.factory() as db:
        row = db.get(NotificationPreference, c.uid)
        row.next_due_at = STAMP - timedelta(seconds=1)
        db.commit()


def saved_job(c):
    with c.factory() as db:
        source = employer.EmployerSource(id="owned-source", employer="Acme", employer_key="acme",
            platform="greenhouse", board_token="acme", region="global", careers_url="https://acme.example/careers",
            allowed_hosts=["acme.example"], verification_url="https://acme.example/careers",
            verification_note="Synthetic operator-reviewed original employer careers page.",
            verified_at=STAMP - timedelta(days=30), enabled=True, status="healthy",
            last_success_at=STAMP - timedelta(hours=1))
        posting = employer.EmployerPosting(id="owned-role", source_id=source.id, external_id="owned-role",
            requisition_id="owned-role", title="Backend Engineer", employer="Acme", location="Bengaluru",
            description="PRIVATE_JOB_BODY", canonical_url="https://job-boards.greenhouse.io/acme/jobs/123",
            apply_url="https://job-boards.greenhouse.io/acme/jobs/123", content_sha256="b" * 64,
            first_seen_at=STAMP - timedelta(days=2), last_checked_at=STAMP - timedelta(hours=1), is_open=True)
        posting.opening_key = opening_identity(posting, source)
        db.add(source)
        db.flush()  # No ORM relationship may stand in for the real source FK.
        db.add(posting)
        db.add(Opportunity(id="saved-role", user_id=c.uid, title="Saved Backend role", source_url=posting.canonical_url,
            stage="saved", created_at=(STAMP - timedelta(days=1)).replace(tzinfo=None)))
        db.commit()


def queued(c):
    with c.factory() as db:
        assert delivery._queue_owner(db, c.uid, now=STAMP)
        row = db.query(NotificationOutbox).filter_by(notification_type=delivery.TYPE).one()
        return row.id, copy.deepcopy(row.payload)


def test_api_consent_to_real_native_and_postgres_budget_to_intercepted_http_once(pipeline):
    c = pipeline
    first = c.client.get("/api/v1/notifications/preferences")
    assert first.status_code == 200 and first.json()["weekly_digest"] is False
    opt_in(c)
    saved_job(c)
    with c.factory() as db:
        result = delivery.deliver_pending_digests(db)
        assert result.queued == result.claimed == result.sent == 1
        row = db.query(NotificationOutbox).filter_by(notification_type=delivery.TYPE).one()
        assert row.status == delivery.SENT and row.payload == {}
        funded = db.query(AdminAuditEvent).filter_by(action=mail_budget.RESERVATION).all()
        assert len(funded) == 1 and funded[0].after_state["units"] == 3
        assert delivery.deliver_pending_digests(db).claimed == 0
    assert len(c.provider_calls) == 1
    sent = json.loads(c.provider_calls[0].content)
    assert "Backend Engineer" in sent["text"] and "PRIVATE_JOB_BODY" not in sent["text"]
    assert "#token=" in sent["text"] and sent["to"] == ["synthetic@example.test"]
    validate_scoped_rows(c.native.values())


def test_known429_retry_reuses_exact_message_and_one_funding_envelope(pipeline):
    c = pipeline
    opt_in(c)
    saved_job(c)
    c.statuses = [429, 200]
    with c.factory() as db:
        assert delivery.deliver_pending_digests(db).sent == 1
        assert db.query(AdminAuditEvent).filter_by(action=mail_budget.RESERVATION).count() == 1
    assert len(c.provider_calls) == 2
    assert c.provider_calls[0].content == c.provider_calls[1].content
    assert c.provider_calls[0].headers["Idempotency-Key"] == c.provider_calls[1].headers["Idempotency-Key"]


def test_unknown_provider_and_restored_sql_pending_row_cannot_replay(pipeline):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, original = queued(c)
    c.statuses = [500]
    with c.factory() as db:
        assert delivery._deliver(db, identity) == delivery.UNKNOWN
        row = db.get(NotificationOutbox, identity)
        row.status, row.payload = delivery.PENDING, original  # Simulated SQL-only restoration.
        db.commit()
        assert delivery._deliver(db, identity) == delivery.UNKNOWN
    assert len(c.provider_calls) == 1


def test_login_free_unsubscribe_still_works_while_sending_paused(pipeline, monkeypatch):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, _ = queued(c)
    with c.factory() as db:
        row = db.get(NotificationOutbox, identity)
        _, _, text_body = delivery._open(row, now=STAMP)
        proof = text_body.split("#token=", 1)[1]
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "false")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "false")
    response = c.client.post("/api/public/notifications/unsubscribe", headers={"Authorization": ""}, json={"token": proof})
    assert response.status_code == 200, response.text
    assert response.json()["weekly_digest"] is False and response.json()["generation"] == 2
    assert "no-store" in response.headers["Cache-Control"]
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "true")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "true")
    with c.factory() as db:
        assert delivery._deliver(db, identity) == delivery.CANCELLED
    assert c.provider_calls == []


@pytest.mark.parametrize("field,value", [("recipient", "other@example.invalid"),
    ("idempotency_key", "different-key"), ("ciphertext", "invalid")])
def test_changed_capsule_destination_or_key_cannot_reach_provider(pipeline, field, value):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, original = queued(c)
    with c.factory() as db:
        row = db.get(NotificationOutbox, identity)
        if field == "ciphertext":
            row.payload = original | {field: value}
        else:
            setattr(row, field, value)
        db.commit()
        assert delivery._deliver(db, identity) == delivery.CANCELLED
    assert c.provider_calls == []


def test_zero_items_uses_no_additional_native_or_mail_budget_calls(pipeline):
    c = pipeline
    opt_in(c)
    calls = len(c.http_calls)
    with c.factory() as db:
        result = delivery.deliver_pending_digests(db)
        assert result.queued == result.claimed == 0
        assert db.query(AdminAuditEvent).filter_by(action=mail_budget.RESERVATION).count() == 0
    assert len(c.http_calls) == calls and not c.provider_calls


def test_paused_retention_clears_personal_payload_without_destroying_unknown_metadata(pipeline, monkeypatch):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, _ = queued(c)
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "false")
    with c.factory() as db:
        row = db.get(NotificationOutbox, identity)
        row.status, row.created_at = delivery.UNKNOWN, STAMP - timedelta(days=2)
        db.commit()
        previous = row.updated_at  # Capture the fixture mutation's own onupdate.
        assert delivery.clear_expired_digest_payloads(db, now=STAMP) == 1
        db.commit()
        db.expire_all()
        row = db.get(NotificationOutbox, identity)
        assert row.status == delivery.UNKNOWN and row.payload == {} and delivery._utc(row.updated_at) == delivery._utc(previous)
    assert not c.provider_calls


@pytest.mark.parametrize("stale", [False, True])
def test_paused_cleanup_never_keeps_expired_processing_body_or_reopens_its_claim(pipeline, monkeypatch, stale):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, _ = queued(c)
    monkeypatch.setenv("NOTIFICATION_DIGESTS_ENABLED", "false")
    monkeypatch.setenv("LIFECYCLE_EMAILS_ENABLED", "false")
    with c.factory() as db:
        row = db.get(NotificationOutbox, identity)
        row.status, row.created_at = delivery.PROCESSING, STAMP - timedelta(days=2)
        row.claimed_at = STAMP - timedelta(minutes=30) if stale else STAMP
        db.commit()
        funded = db.query(AdminAuditEvent).filter_by(action=mail_budget.RESERVATION).count()
        assert delivery.clear_expired_digest_payloads(db, now=STAMP) == 1
        db.commit()
        db.expire_all()
        row = db.get(NotificationOutbox, identity)
        assert row.payload == {} and row.status == (delivery.UNKNOWN if stale else delivery.PROCESSING)
        assert db.query(AdminAuditEvent).filter_by(action=mail_budget.RESERVATION).count() == funded == 1
    assert not c.provider_calls


def test_account_sql_erasure_cannot_leave_a_queued_message_sendable(pipeline):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, _ = queued(c)
    with c.factory() as db:
        erase_account_sql(db, c.uid)  # Local SQL privacy helper; no native authorization claim.
        db.commit()
        assert db.get(NotificationPreference, c.uid) is None
        assert delivery._deliver(db, identity) == delivery.CANCELLED
    assert not c.provider_calls


@pytest.mark.parametrize("persisted", [False, True])
def test_ambiguous_consent_never_creates_a_sql_schedule_from_a_read_head(pipeline, persisted):
    c = pipeline
    from app.domains.recovery.gcp_notification_digest import HISTORY
    c.native.fault = lambda namespaces: ("persist_loss" if persisted else "no_persist_loss") if HISTORY in namespaces else None
    response = c.client.put("/api/v1/notifications/preferences", json={"expected_generation": 0,
        "weekly_digest": True, "explicit_confirmation": True})
    assert response.status_code == 503
    c.native.fault = None
    with c.factory() as db:
        assert db.get(NotificationPreference, c.uid) is None
    current = c.client.get("/api/v1/notifications/preferences")
    assert current.status_code == 200
    assert current.json()["weekly_digest"] is persisted
    assert current.json()["projection_current"] is (not persisted)
    with c.factory() as db:
        assert db.get(NotificationPreference, c.uid) is None
    assert not c.provider_calls


def test_generic_lifecycle_sender_cannot_claim_even_a_mislabelled_weekly_row(pipeline):
    c = pipeline
    from app.domains.notifications import service as mail_service
    with c.factory() as db:
        db.add(NotificationOutbox(id="mislabelled", user_id=c.uid, notification_type=delivery.TYPE,
            recipient="synthetic@example.test", payload={}, idempotency_key="mislabelled-weekly",
            status="pending", attempt_count=0, available_at=STAMP, created_at=STAMP, updated_at=STAMP))
        db.commit()
        result = mail_service.deliver_pending_notifications(db)
        assert result.claimed == 0
        assert db.get(NotificationOutbox, "mislabelled").status == "pending"
    assert not c.provider_calls


def test_mailbox_change_after_native_admission_is_rechecked_before_physical_http(pipeline, monkeypatch):
    c = pipeline
    opt_in(c)
    saved_job(c)
    identity, _ = queued(c)
    original = delivery.ConfiguredDigestNative.execute_once

    def race(native, revision, **kwargs):
        callback = kwargs["callback"]
        def changed():
            with c.factory() as db:
                db.query(User).filter_by(id=c.uid).update({"email": "changed@example.invalid"})
                db.commit()
            return callback()
        return original(native, revision, **(kwargs | {"callback": changed}))

    monkeypatch.setattr(delivery.ConfiguredDigestNative, "execute_once", race)
    with c.factory() as db:
        assert delivery._deliver(db, identity) == delivery.UNKNOWN
    assert not c.provider_calls


def test_unfunded_optional_digest_creates_no_outbox_or_native_begin(pipeline):
    c = pipeline
    opt_in(c)
    saved_job(c)
    from app.domains.recovery.gcp_notification_digest import BEGINS
    with c.factory() as db:
        for index in range(20):
            mail_budget.reserve_messages(db, [f"other-funded-{index}"], category="lifecycle", now=STAMP)
        db.commit()
        result = delivery.deliver_pending_digests(db)
        assert result.queued == result.claimed == 0
        assert db.query(NotificationOutbox).filter_by(notification_type=delivery.TYPE).count() == 0
        assert delivery._utc(db.get(NotificationPreference, c.uid).next_due_at) > STAMP
    assert not any(namespace == BEGINS for namespace, _ in c.native.values())
    assert not c.provider_calls
