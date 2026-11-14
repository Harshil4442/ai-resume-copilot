"""Synthetic route tests using the real signed-bearer and owner query paths."""
from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta
from os import getenv
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app import models as core
from backend.app.database import Base, get_db
from backend.app.domains.candidate_accounts import service as candidate_accounts
from backend.app.domains.employer import models, service
from backend.app.domains.recovery.native_metadata_admission import MetadataCapacityUnavailable
from backend.app.domains.recovery.store import GuardDenied
from backend.app.routers.v1 import employer
from backend.app.security import create_access_token
from backend.tests.browser_submission_unified_fixture import approve, configure, review
from backend.tests.browser_submit_owned import OwnedSubmit
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateSchema, DropSchema

BOOTSTRAP = "/api/v1/employer-jobs/bootstrap"


@pytest.fixture
def jobs_engine(request):
    schema = None
    if getattr(request, "param", "sqlite") == "postgresql":
        url = getenv("HIREWIZ_TEST_POSTGRES_URL")
        if not url:
            pytest.skip("Disposable PostgreSQL URL is not configured")
        schema = "jobs_bootstrap_" + uuid4().hex
        engine = create_engine(url, execution_options={"schema_translate_map": {None: schema}})
        with engine.begin() as connection:
            connection.execute(CreateSchema(schema))
    else:
        engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    try:
        yield engine
    finally:
        if schema is not None:
            with engine.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
        engine.dispose()


@pytest.fixture
def owned_jobs(jobs_engine, monkeypatch):
    def no_socket(*args, **kwargs):
        pytest.fail("The owned Jobs projection must not call any provider")

    monkeypatch.setattr(socket, "getaddrinfo", no_socket)
    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setenv("EMPLOYER_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("EMPLOYER_AUTO_SUBMIT_ENABLED", "false")
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "2")
    monkeypatch.setenv("EMPLOYER_APPLY_CREDITS_PER_JOB", "7")
    monkeypatch.setenv("EMPLOYER_MAX_SEARCH_JOBS", "12")
    engine = jobs_engine
    Base.metadata.create_all(engine)
    calls, statements = [], []
    native_errors = []
    after_validate = []
    context = {"candidate_id": 1, "account_binding_id": str(uuid4()), "session_id": str(uuid4())}
    now = datetime.now(UTC)
    with Session(engine) as db:
        db.add_all([core.User(id=1, email="owner@example.invalid", job_service_credits=17),
                    core.User(id=2, email="other@example.invalid", job_service_credits=89, credential_epoch=3)])
        db.flush()
        db.add(core.CandidatePasswordAccount(user_id=1, subject_uuid=str(uuid4()),
            account_binding_id=context["account_binding_id"], principal_sha256="a" * 64, credential_sha256="b" * 64,
            auth_generation=1, state="ACTIVE"))
        for owner in (1, 2):
            for index in range(1, 23 if owner == 1 else 2):
                db.add(core.Resume(id=owner * 100 + index, user_id=owner, original_filename=f"owner-{owner}-{index}.pdf",
                    source_format="pdf", source_document=b"private resume bytes", raw_text="private extracted source",
                    created_at=now - timedelta(minutes=index)))
        db.add(models.EmployerSource(id="source", employer="Synthetic Employer", platform="greenhouse",
            board_token="synthetic", enabled=True, careers_url="https://employer.example/careers",
            verification_url="https://employer.example/careers", verification_note="Synthetic fixture"))
        db.flush()
        db.add(models.EmployerPosting(id="posting", source_id="source", external_id="synthetic",
            title="Engineer", employer="Synthetic Employer", description="Synthetic role",
            canonical_url="https://employer.example/careers/synthetic", apply_url="https://employer.example/apply/synthetic",
            content_sha256="b" * 64))
        db.flush()
        for owner in (1, 2):
            db.add(models.SealedApplicationArtifact(id=f"artifact-{owner}", user_id=owner, resume_id=owner * 100 + 1,
                sha256="c" * 64, filename=f"owner-{owner}.pdf", media_type="application/pdf", size_bytes=32,
                content=b"private application bytes"))
            db.flush()
            for index in range(31 if owner == 1 else 1):
                db.add(models.EmployerApplication(id=f"application-{owner}-{index}", user_id=owner, posting_id="posting",
                    resume_id=owner * 100 + 1, resume_choice="original", artifact_id=f"artifact-{owner}",
                    idempotency_key=f"application-key-{index}", input_fingerprint="d" * 64, form={"fields": [], "consents": []},
                    package_digest="e" * 64, job_content_sha256="b" * 64, credit_cost=0,
                    created_at=now - timedelta(minutes=index)))
            db.flush()
            db.add(models.EmployerAdmission(id=f"admission-{owner}", user_id=owner, application_id=f"application-{owner}-0",
                employer_key="synthetic-employer", opening_key=f"opening-{owner}", credit_cost=0, policy_snapshot={},
                state="released", release_reason="cancelled", settled_at=now))
            for index in range(12 if owner == 1 else 1):
                db.add(models.EmployerSearch(id=f"search-{owner}-{index}", user_id=owner, resume_id=owner * 100 + 1,
                    idempotency_key=f"search-key-{index}", input_fingerprint="f" * 64, desired_count=2,
                    _legacy_query={"role": "Engineer"}, _legacy_items=[{"private": "do not project result bodies"}],
                    scope={}, created_at=now - timedelta(minutes=index)))
        db.commit()

    def validate(value, *, auth_generation):
        calls.append((value.model_dump(mode="json"), auth_generation))
        if native_errors:
            raise native_errors[0]
        for callback in after_validate:
            callback()

    monkeypatch.setattr(candidate_accounts, "production_candidate_accounts", lambda: SimpleNamespace(validate=validate))

    def session():
        with Session(engine, autoflush=False) as db:
            yield db

    app = FastAPI()
    app.include_router(employer.router, prefix="/api/v1")
    app.dependency_overrides[get_db] = session

    def record(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    token = create_access_token(subject="1", candidate_lifetime={"version": 1, "context": context, "auth_generation": 1})
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, headers={"Authorization": "Bearer " + token}, calls=calls,
            statements=statements, native_errors=native_errors, context=context, engine=engine,
            after_validate=after_validate)


@pytest.mark.parametrize("jobs_engine", ["sqlite", "postgresql"], indirect=True)
def test_one_fresh_owner_validation_matches_existing_initial_projections(owned_jobs):
    f = owned_jobs
    response = f.client.get(BOOTSTRAP + "?current_resume_id=122", headers=f.headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert f.calls == [(f.context, 1)]
    assert response.headers["cache-control"] == "private, no-store"
    assert payload["schema_version"] == "owned-jobs-bootstrap-v1"
    assert payload["catalog"]["balance"] == 17
    assert payload["catalog"]["search_credits_per_job"] == 2 and payload["catalog"]["max_search_jobs"] == 12
    assert payload["catalog"]["enabled"] is True and payload["catalog"]["auto_submit_enabled"] is False
    assert len(payload["resumes"]["resumes"]) == 20 and payload["resumes"]["selected_resume"]["id"] == 122
    assert payload["resumes"]["next_cursor"]
    assert [row["id"] for row in payload["saved_searches"]["items"]] == [f"search-1-{i}" for i in range(10)]
    assert payload["saved_searches"]["next_cursor"]
    assert [row["id"] for row in payload["applications"]] == [f"application-1-{i}" for i in range(30)]
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in f.statements)
    assert len(f.statements) == 7
    assert payload["applications"][0]["artifact"]["filename"] == "owner-1.pdf"
    assert payload["applications"][0]["admission"]["id"] == "admission-1"
    assert payload["applications"][0]["admission"]["state"] == "released"
    assert "private resume bytes" not in response.text and "do not project result bodies" not in response.text

    prefix = "/api/v1/employer-jobs"
    assert f.client.get(prefix + "/catalog", headers=f.headers).json() == payload["catalog"]
    assert f.client.get(prefix + "/search-summaries?limit=10", headers=f.headers).json() == payload["saved_searches"]
    assert f.client.get(prefix + "/applications", headers=f.headers).json()["items"] == payload["applications"]
    next_page = f.client.get(prefix + "/search-summaries", params={"cursor": payload["saved_searches"]["next_cursor"]}, headers=f.headers)
    assert [row["id"] for row in next_page.json()["items"]] == ["search-1-10", "search-1-11"]


def test_capacity_and_native_denial_are_terminal_without_owner_projection(owned_jobs, monkeypatch):
    f = owned_jobs

    def no_projection(*args, **kwargs):
        pytest.fail("A refused request must not read or return Jobs data")

    monkeypatch.setattr(service, "catalog_for_verified_subject", no_projection)
    f.native_errors.append(MetadataCapacityUnavailable())
    result = f.client.get(BOOTSTRAP, headers=f.headers)
    assert result.status_code == 429
    assert result.json()["detail"]["code"] == "NATIVE_METADATA_CAPACITY"
    assert result.headers["retry-after"] == "60"
    assert result.headers["cache-control"] == "private, no-store"
    assert len(f.calls) == 1
    f.native_errors[0] = GuardDenied("synthetic refused validation")
    assert f.client.get(BOOTSTRAP, headers=f.headers).status_code == 401
    assert len(f.calls) == 2


def test_legacy_epoch_and_cross_owner_resume_stay_enforced(owned_jobs):
    f = owned_jobs
    assert f.client.get(BOOTSTRAP).status_code == 401
    numeric = {"Authorization": "Bearer " + create_access_token(subject="1")}
    assert f.client.get(BOOTSTRAP, headers=numeric).status_code == 401
    stale = {"Authorization": "Bearer " + create_access_token(subject="2", credential_epoch=2)}
    assert f.client.get(BOOTSTRAP, headers=stale).status_code == 401
    assert f.client.get(BOOTSTRAP + "?current_resume_id=201", headers=f.headers).status_code == 404
    legacy = {"Authorization": "Bearer " + create_access_token(subject="2", credential_epoch=3)}
    result = f.client.get(BOOTSTRAP, headers=legacy)
    assert result.status_code == 200, result.text
    assert result.json()["catalog"]["balance"] == 89
    assert result.json()["resumes"]["resumes"][0]["id"] == 201
    assert result.json()["saved_searches"]["items"][0]["id"] == "search-2-0"
    assert result.json()["applications"][0]["id"] == "application-2-0"
    assert len(f.calls) == 1


@pytest.mark.parametrize("transition", ["rotate", "remove", "deny"])
def test_changed_account_projection_cannot_use_prior_native_validation(owned_jobs, transition):
    f = owned_jobs

    def change_after_validation():
        with Session(f.engine) as db:
            account = db.get(core.CandidatePasswordAccount, 1)
            if transition == "rotate":
                account.auth_generation = 2
            elif transition == "remove":
                db.delete(account)
            else:
                account.state = "DELETE_DENIED"
            db.commit()

    f.after_validate.append(change_after_validation)
    result = f.client.get(BOOTSTRAP, headers=f.headers)
    assert result.status_code == 401
    assert result.headers["cache-control"] == "private, no-store"
    assert len(f.calls) == 1


@pytest.mark.parametrize(("query", "headers"), [
    ("?market=unknown", {}), ("?current_resume_id=1&current_resume_id=2", {}),
    ("?current_resume_id=0", {}), ("?current_resume_id=bad", {}),
    ("", {"content-length": "1"}), ("", {"content-encoding": "gzip"}), ("", {"transfer-encoding": "chunked"}),
])
def test_jobs_bootstrap_refuses_invalid_requests_before_owner_validation(owned_jobs, query, headers):
    f = owned_jobs
    result = f.client.get(BOOTSTRAP + query, headers=f.headers | headers)
    assert result.status_code == 422 and result.headers["cache-control"] == "private, no-store"
    assert not f.statements and not f.calls


def test_foreign_artifact_cannot_join_an_owned_application(owned_jobs):
    f = owned_jobs
    with Session(f.engine) as db:
        app = db.get(models.EmployerApplication, "application-1-0")
        app.artifact_id = "artifact-2"
        db.commit()
    result = f.client.get(BOOTSTRAP, headers=f.headers)
    assert result.status_code == 404 and result.json() == {"detail": "Not found"}
    assert "owner-2.pdf" not in result.text


def test_bootstrap_application_projection_keeps_existing_unified_hold_checks(monkeypatch):
    owned = OwnedSubmit(monkeypatch)
    monkeypatch.setenv("BROWSER_ASSISTANCE_NATIVE_UPLOAD_ENABLED", "true")
    try:
        configure(owned, monkeypatch)
        approve(owned, review(owned))
        expected = service.application_response(owned.db, owned.app)
        assert service.list_bootstrap_application_views(owned.db, 1) == [expected]
        assert expected["reserved_credits"] == 3 and expected["billing_currency"] == "credits"
        assert expected["artifact"]["filename"] == "Owned.pdf" and expected["admission"]["state"] == "reserved"
        assert owned.audit()["credits"] == 100 and owned.audit()["native_calls"] == 0
    finally:
        owned.close()
