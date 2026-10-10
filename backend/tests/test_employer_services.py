from __future__ import annotations

import hashlib
from io import BytesIO

import httpx
import pytest
from backend.app import models as core
from backend.app.database import Base, get_db
from backend.app.domains.common import payload_fingerprint, utcnow
from backend.app.domains.employer import connectors, models, schemas, service, tasks
from backend.app.routers.v1.employer import router
from backend.app.security import get_current_user
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from reportlab.pdfgen.canvas import Canvas
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

FORM = {"version": "a" * 64, "supported": True, "consents": [], "fields": [
    {"id": "first_name", "label": "First name", "type": "text", "required": True, "options": []},
    {"id": "last_name", "label": "Last name", "type": "text", "required": True, "options": []},
    {"id": "email", "label": "Email", "type": "email", "required": True, "options": []},
    {"id": "resume", "label": "Resume", "type": "file", "required": True, "options": []},
]}
ANSWERS = {"first_name": "Ada", "last_name": "Lovelace", "email": "ada@example.com"}


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "1")
    monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "5")
    monkeypatch.setenv("EMPLOYER_CREDENTIAL_TEST", "fake-test-key")
    monkeypatch.delenv("EMPLOYER_ARTIFACT_BUCKET", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(tasks, "SessionLocal", factory)
    monkeypatch.setattr(connectors, "load_form", lambda *_args, **_kwargs: dict(FORM))
    output = BytesIO()
    pdf = Canvas(output)
    pdf.drawString(40, 700, "Ada Lovelace — Python Engineer")
    pdf.save()
    original = output.getvalue()
    with factory() as db:
        db.add_all([core.User(id=1, email="ada@example.com", ai_credits=500, job_service_credits=50, tier="premium"),
                    core.User(id=2, email="other@example.com", job_service_credits=50)])
        db.commit()
        db.add(core.Resume(id=10, user_id=1, original_filename="Ada.pdf", source_format="pdf",
                           source_document=original, skills=["Python", "PostgreSQL"], raw_text="Python Engineer"))
        db.add(models.EmployerSource(id="source_test", employer="Verified Employer", platform="greenhouse",
            board_token="verified", region="global", careers_url="https://employer.example/careers",
            allowed_hosts=["employer.example"], verification_url="https://employer.example/careers",
            verification_note="Operator verified the company career portal links to this exact tenant.",
            submission_enabled=True, submission_grant="Employer-approved test integration",
            credential_env="EMPLOYER_CREDENTIAL_TEST", form_parity_verified=True,
            receipt_contract={"receipt_id_field": "application_id", "completion_field": "completed",
                              "completion_value": True, "verification_note": "Contracted complete application receipt"},
            status="healthy", last_success_at=utcnow()))
        db.commit()
        for index in (1, 2):
            db.add(models.EmployerPosting(id=f"job_{index}", source_id="source_test", external_id=str(index),
                title="Python Software Engineer", employer="Verified Employer", location="Bengaluru, India",
                description="Python PostgreSQL software engineer", canonical_url=f"https://job-boards.greenhouse.io/verified/jobs/{index}",
                apply_url=f"https://job-boards.greenhouse.io/verified/jobs/{index}", content_sha256=payload_fingerprint({"job": index}),
                last_checked_at=utcnow(), skills=["Python", "PostgreSQL"]))
        db.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    def db_override():
        with factory() as db:
            yield db

    def user_override():
        with factory() as db:
            return db.get(core.User, 1)

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[get_current_user] = user_override
    return factory, TestClient(app), original


def _prepare(context, *, job="job_1"):
    _, client, _ = context
    result = client.post("/api/v1/employer-jobs/applications", json={
        "posting_id": job, "resume_id": 10, "resume_choice": "original", "idempotency_key": "application-key-" + job,
    })
    assert result.status_code == 201, result.text
    application = result.json()
    result = client.put(f"/api/v1/employer-jobs/applications/{application['id']}/package", json={
        "resume_id": 10, "resume_choice": "original", "answers": ANSWERS, "consents": {},
    })
    assert result.status_code == 200, result.text
    application = result.json()
    result = client.post(f"/api/v1/employer-jobs/applications/{application['id']}/approve", json={
        "package_digest": application["package_digest"], "allowed_actions": ["upload", "submit"],
    })
    assert result.status_code == 200, result.text
    return result.json()


def _execute(client, application):
    result = client.post(f"/api/v1/employer-jobs/applications/{application['id']}/execute", json={"package_digest": application["package_digest"]})
    assert result.status_code == 200, result.text
    return result.json()


def test_paid_search_partial_delivery_idempotency_and_no_premium_waiver(context, monkeypatch):
    factory, client, _ = context
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "2")
    payload = {"resume_id": 10, "role": "Python Engineer", "desired_count": 3, "idempotency_key": "search-key-1"}
    result = client.post("/api/v1/employer-jobs/searches", json=payload)
    assert result.status_code == 201, result.text
    result = result.json()
    assert (result["delivered_count"], result["reserved_credits"], result["charged_credits"], result["refunded_credits"]) == (2, 6, 4, 2)
    repeat = client.post("/api/v1/employer-jobs/searches", json=payload)
    assert repeat.json()["id"] == result["id"]
    payload["idempotency_key"] = "search-key-2"
    assert client.post("/api/v1/employer-jobs/searches", json=payload).json()["charged_credits"] == 0
    payload["role"] = "Java Engineer"
    assert client.post("/api/v1/employer-jobs/searches", json=payload).status_code == 409
    with factory() as db:
        user = db.get(core.User, 1)
        assert user.job_service_credits == 46
        assert user.ai_credits == 500  # Premium and promotional AI units are unrelated.
        assert db.query(models.EmployerJobDelivery).count() == 2


def test_service_credits_required_even_with_promotional_credits_and_premium(context):
    factory, client, _ = context
    with factory() as db:
        db.get(core.User, 1).job_service_credits = 0
        db.commit()
    result = client.post("/api/v1/employer-jobs/searches", json={"resume_id": 10, "role": "Python", "desired_count": 2, "idempotency_key": "unfunded-search"})
    assert result.status_code == 402
    assert result.json()["detail"]["code"] == "insufficient_service_credits"
    with factory() as db:
        assert db.query(models.ServiceCreditEvent).count() == 0


def test_exact_reviewed_bytes_and_verified_receipt_charge_once(context, monkeypatch):
    factory, client, original = context
    application = _prepare(context)
    preview = client.get(application["artifact"]["preview_url"])
    assert preview.content == original
    assert preview.headers["x-artifact-sha256"] == hashlib.sha256(original).hexdigest()
    assert _execute(client, application)["status"] == "queued"
    sent = []

    def send(*args, **kwargs):
        sent.append(kwargs)
        assert kwargs["content"] == preview.content
        assert kwargs["answers"] == ANSWERS
        return 201, {"provider": "greenhouse", "application_id": "receipt-1", "completion_verified": True}

    monkeypatch.setattr(connectors, "submit_greenhouse", send)
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert len(sent) == 1
    detail = client.get(f"/api/v1/employer-jobs/applications/{application['id']}").json()
    assert detail["charged_credits"] == 5
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 45
        assert db.query(models.EmployerApplicationAttempt).count() == 1
        assert db.query(models.EmployerApplicationApproval).count() == 1


def test_unknown_submission_is_held_and_never_blindly_retried(context, monkeypatch):
    factory, client, _ = context
    application = _prepare(context)
    _execute(client, application)
    calls = []
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *args, **kwargs: calls.append(1) or (200, None))
    assert tasks.execute_application(application["id"]) == "unknown"
    assert tasks.execute_application(application["id"]) == "unknown"
    assert _execute(client, application)["status"] == "unknown"
    assert calls == [1]
    credit = client.get("/api/v1/employer-jobs/credits").json()
    assert credit["reserved_credits"] == 5
    with factory() as db:
        reservation = db.query(models.ServiceCreditReservation).one()
        assert (reservation.state, reservation.committed_amount) == ("reserved", 0)


def test_cancel_before_launch_refunds_and_changed_form_prevents_send(context, monkeypatch):
    factory, client, _ = context
    first = _prepare(context)
    _execute(client, first)
    assert client.post(f"/api/v1/employer-jobs/applications/{first['id']}/cancel").json()["status"] == "cancelled"
    assert tasks.execute_application(first["id"]) == "cancelled"
    second = _prepare(context, job="job_2")
    _execute(client, second)
    monkeypatch.setattr(connectors, "load_form", lambda *args, **kwargs: FORM | {"version": "b" * 64})
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *args, **kwargs: pytest.fail("Changed form must never be sent"))
    assert tasks.execute_application(second["id"]) == "failed"
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 50


def test_per_job_manual_handoff_ownership_and_approval_required(context, monkeypatch):
    factory, client, _ = context
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "false")
    application = _prepare(context)
    assert application["application_mode"] == "manual"
    assert _execute(client, application)["status"] == "manual_handoff"
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 50
        with pytest.raises(HTTPException) as error:
            service._owned(db, models.EmployerApplication, application["id"], 2)
        assert error.value.status_code == 404
    second = client.post("/api/v1/employer-jobs/applications", json={"posting_id": "job_2", "resume_id": 10, "idempotency_key": "not-approved-app"}).json()
    assert client.post(f"/api/v1/employer-jobs/applications/{second['id']}/execute", json={"package_digest": second["package_digest"]}).status_code == 409


def test_required_questions_choices_and_approval_change(context):
    _, client, _ = context
    application = _prepare(context)
    changed = client.put(f"/api/v1/employer-jobs/applications/{application['id']}/package", json={
        "resume_id": 10, "resume_choice": "original", "answers": ANSWERS | {"email": "other@example.com"}, "consents": {},
    }).json()
    assert changed["status"] == "ready"
    assert changed["package_digest"] != application["package_digest"]
    assert client.post(f"/api/v1/employer-jobs/applications/{changed['id']}/execute", json={"package_digest": changed["package_digest"]}).status_code == 409
    with pytest.raises(HTTPException):
        service._validate_answers({"fields": [{"id": "sponsorship", "label": "Sponsorship", "type": "single_select", "options": [{"value": "yes", "label": "Yes"}]}]}, {"sponsorship": "invented"}, {})


def test_failed_source_scan_does_not_close_jobs(context, monkeypatch):
    factory, _, _ = context
    monkeypatch.setattr(connectors, "fetch_postings", lambda *args: (_ for _ in ()).throw(connectors.ConnectorError("source_http_429")))
    with pytest.raises(connectors.ConnectorError):
        tasks.refresh_source("source_test")
    with factory() as db:
        assert db.query(models.EmployerPosting).filter_by(is_open=True).count() == 2
        assert db.get(models.EmployerSource, "source_test").status == "unavailable"


def test_public_connector_origin_scan_integrity_and_unlisted_filter():
    source = connectors.SourceContract(id="s", employer="Acme", platform="ashby", board_token="acme", region="global", allowed_hosts=("acme.example",), careers_url="https://acme.example/careers")
    rows = [
        {"id": "listed", "isListed": True, "title": "Engineer", "descriptionPlain": "Python", "location": "India", "jobUrl": "https://jobs.ashbyhq.com/acme/listed", "applyUrl": "https://jobs.ashbyhq.com/acme/listed/application"},
        {"id": "secret", "isListed": False},
    ]
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"jobs": rows}))) as client:
        result = connectors.fetch_postings(source, client)
    assert [row["external_id"] for row in result] == ["listed"]
    assert result[0]["publication_at"] is None
    with pytest.raises(connectors.ConnectorError):
        connectors.safe_origin_url("https://job-board.example/apply", source)
    with pytest.raises(ValueError):
        schemas.SourceCreate(employer="Acme", platform="greenhouse", board_token="acme", careers_url="https://acme.example/careers", allowed_hosts=["acme.example"], verification_url="https://acme.example/careers", verification_note="Verified employer origin link by operator.", submission_enabled=True)


def test_account_deletion_revokes_work_and_anonymizes_money(context, monkeypatch):
    factory, client, _ = context
    application = _prepare(context)
    _execute(client, application)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *args, **kwargs: pytest.fail("Deleted account must never submit"))
    with factory() as db:
        service.delete_account_data(db, 1)
        db.commit()
        assert db.query(models.EmployerApplication).count() == 0
        assert db.query(models.SealedApplicationArtifact).count() == 0
        assert all(row.user_id is None for row in db.query(models.ServiceCreditEvent).all())
    assert tasks.execute_application(application["id"]) == "missing"


def test_unknown_reconciliation_requires_admin_independent_matching_receipt(context, monkeypatch):
    factory, client, _ = context
    application = _prepare(context)
    _execute(client, application)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *args, **kwargs: (200, None))
    assert tasks.execute_application(application["id"]) == "unknown"
    path = f"/api/v1/employer-jobs/applications/{application['id']}/reconcile"
    proof = {"outcome": "confirmed", "proof_kind": "provider_lookup", "proof_reference": "Provider support ticket receipt-123",
             "reason": "Support independently verified the complete application for this exact employer posting.",
             "employer_posting_id": "1", "provider_receipt": {"application_id": "receipt-123", "completed": True}}
    assert client.post(path, json=proof).status_code == 404
    monkeypatch.setenv("ADMIN_EMAILS", "ada@example.com")
    assert client.post(path, json=proof | {"employer_posting_id": "wrong-job"}).status_code == 422
    assert client.post(path, json=proof | {"provider_receipt": {"application_id": "receipt-123", "completed": 1}}).status_code == 422
    result = client.post(path, json=proof)
    assert result.status_code == 200, result.text
    assert (result.json()["status"], result.json()["charged_credits"]) == ("confirmed", 5)
    assert client.post(path, json=proof).status_code == 409
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 45
        assert db.query(core.AdminAuditEvent).filter_by(action="reconcile_employer_application").count() == 1


def test_proven_no_send_reconciliation_refunds_unknown_hold(context, monkeypatch):
    factory, client, _ = context
    monkeypatch.setenv("ADMIN_EMAILS", "ada@example.com")
    application = _prepare(context)
    _execute(client, application)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *args, **kwargs: (503, None))
    tasks.execute_application(application["id"])
    result = client.post(f"/api/v1/employer-jobs/applications/{application['id']}/reconcile", json={
        "outcome": "not_submitted", "proof_kind": "provider_support", "proof_reference": "Provider case proof-no-send-1",
        "reason": "Employer support verified no application was created by the timed-out request.", "employer_posting_id": "1",
    })
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "failed"
    with factory() as db:
        assert db.get(core.User, 1).job_service_credits == 50
        assert db.query(models.ServiceCreditReservation).one().released_amount == 5


def test_worker_redelivery_during_send_has_one_launch_and_late_receipt_resolves(context, monkeypatch):
    factory, client, _ = context
    application = _prepare(context)
    _execute(client, application)
    calls = []

    def send(*args, **kwargs):
        calls.append(1)
        # The first worker committed its launch; a second delivery cannot send.
        assert tasks.execute_application(application["id"]) == "unknown"
        return 201, {"application_id": "verified-late-receipt", "completion_verified": True}

    monkeypatch.setattr(connectors, "submit_greenhouse", send)
    assert tasks.execute_application(application["id"]) == "confirmed"
    assert calls == [1]
    with factory() as db:
        assert db.query(models.EmployerApplicationAttempt).count() == 1
        assert db.get(core.User, 1).job_service_credits == 45


def test_private_artifact_cleanup_is_durable_and_generation_fenced(context, monkeypatch):
    factory, client, _ = context
    application = _prepare(context)
    with factory() as db:
        intent = db.get(models.EmployerApplication, application["id"])
        artifact = db.get(models.SealedApplicationArtifact, intent.artifact_id)
        artifact.gcs_object, artifact.gcs_generation = "gs://private-test/applications/1/sealed-hash", "42"
        db.commit()
        service.delete_account_data(db, 1)
        db.commit()
        deletion = db.query(models.EmployerArtifactDeletion).one()
        deletion_id = deletion.id
        assert deletion.completed_at is None
        assert db.query(models.SealedApplicationArtifact).count() == 0
    from google.cloud import storage
    deleted = []

    class Blob:
        def delete(self, **kwargs):
            deleted.append(kwargs)

    class Bucket:
        def blob(self, path, **kwargs):
            assert path == "applications/1/sealed-hash"
            assert kwargs == {"generation": 42}
            return Blob()

    class Storage:
        def bucket(self, name):
            assert name == "private-test"
            return Bucket()

    monkeypatch.setattr(storage, "Client", Storage)
    assert tasks.delete_artifact(deletion_id) == "completed"
    assert tasks.delete_artifact(deletion_id) == "completed"
    assert len(deleted) == 1 and deleted[0]["if_generation_match"] == 42
    assert deleted[0]["timeout"] == 30 and deleted[0]["retry"].deadline == 60
    with factory() as db:
        assert db.get(models.EmployerArtifactDeletion, deletion_id).gcs_object == "deleted"


def test_documented_greenhouse_schema_enforces_required_groups_and_public_scan():
    source = connectors.SourceContract(id="s", employer="Acme", platform="greenhouse", board_token="acme", region="global", allowed_hosts=("acme.example",), careers_url="https://acme.example/careers")
    job = {"id": 123, "internal_job_id": 5, "title": "Engineer", "location": {"name": "India"}, "content": "Python",
           "absolute_url": "https://job-boards.greenhouse.io/acme/jobs/123", "questions": [
               {"label": "Resume", "required": True, "fields": [{"name": "resume", "type": "input_file"}, {"name": "resume_text", "type": "textarea"}]},
               {"label": "Authorization", "required": True, "fields": [{"name": "question_5", "type": "multi_value_single_select", "values": [{"value": 0, "label": "No"}, {"value": 1, "label": "Yes"}]}]},
           ]}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=job))) as client:
        form = connectors.load_form(source, "123", client)
    assert form["supported"] is True
    assert form["required_groups"] == [["resume", "resume_text"], ["question_5"]]
    assert next(field for field in form["fields"] if field["id"] == "first_name")["required"] is True
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"jobs": [job], "meta": {"total": 2}}))) as client:
        with pytest.raises(connectors.ConnectorError, match="incomplete_scan"):
            connectors.fetch_postings(source, client)
    with pytest.raises(connectors.ConnectorError, match="tenant_mismatch"):
        connectors.safe_origin_url("https://job-boards.greenhouse.io/another-employer/jobs/123", source)


def test_provider_2xx_or_candidate_id_does_not_prove_complete_application(monkeypatch):
    source = connectors.SourceContract(id="s", employer="Acme", platform="greenhouse", board_token="acme", region="global", allowed_hosts=("acme.example",), careers_url="https://acme.example/careers",
        submission_enabled=True, submission_grant="Contracted tenant write permission", form_parity_verified=True,
        receipt_contract={"receipt_id_field": "application_id", "completion_field": "completed", "completion_value": True})
    for body in ({"id": "candidate-id"}, {"application_id": "app", "completed": 1}, {"application_id": "app", "completed": False}):
        client = httpx.Client(transport=httpx.MockTransport(lambda request, body=body: httpx.Response(200, json=body)))
        monkeypatch.setattr(connectors, "_client", lambda client=client: client)
        status, receipt = connectors.submit_greenhouse(source, "123", credential="test", answers=ANSWERS,
            content=b"source bytes", filename="resume.pdf", media_type="application/pdf")
        assert status == 200 and receipt is None
    client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(201, json={"application_id": "verified", "completed": True})))
    monkeypatch.setattr(connectors, "_client", lambda: client)
    _, receipt = connectors.submit_greenhouse(source, "123", credential="test", answers=ANSWERS,
        content=b"source bytes", filename="resume.pdf", media_type="application/pdf")
    assert receipt["completion_verified"] is True


def test_llm_unavailable_does_not_block_discovery_or_supported_original_submission(context, monkeypatch):
    from backend.app.services import llm_client
    monkeypatch.setattr(llm_client, "_chat", lambda *args, **kwargs: pytest.fail("Employer workflow cannot require AI"))
    monkeypatch.setattr(llm_client, "_chat_with_budget", lambda *args, **kwargs: pytest.fail("Employer workflow cannot require AI"))
    _, client, _ = context
    result = client.post("/api/v1/employer-jobs/searches", json={"resume_id": 10, "role": "Python", "desired_count": 1, "idempotency_key": "cold-no-ai-search"})
    assert result.status_code == 201, result.text
    application = _prepare(context)
    _execute(client, application)
    monkeypatch.setattr(connectors, "submit_greenhouse", lambda *args, **kwargs: (201, {"application_id": "no-ai-receipt", "completion_verified": True}))
    assert tasks.execute_application(application["id"]) == "confirmed"
