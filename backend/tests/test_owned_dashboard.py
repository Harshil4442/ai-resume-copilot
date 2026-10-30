"""Actual auth/factory with intercepted SDK and scratch SQLite, no native capacity claim."""
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse
from sqlalchemy import update
from sqlalchemy.exc import OperationalError

from app.domains.candidate_accounts import dashboard
from app.domains.recovery.store import GuardDenied
from app.models import JobMatch, Opportunity, Reminder, Resume, User
from tests.test_candidate_production_factory import configured as configured
from tests.test_native_metadata_admission import inert_shared_redis
from tests.test_owned_bootstrap import EMAIL, _reads_only
from tests.test_owned_bootstrap import api as api

PATH = "/api/auth/dashboard"


def request(api):
    return api.client.get(PATH, headers=api.headers)


def test_dashboard_one_native_validation_and_owned_bounded_projection(api):
    with api.c.accounts.sessions() as db:
        db.add(User(id=100, email="foreign-dashboard@example.invalid"))
        db.add_all([Resume(id=100, user_id=100, original_filename="private.pdf", raw_text="PRIVATE SOURCE"),
            Resume(id=101, user_id=api.owner, original_filename="owned.pdf", raw_text="OWNED SOURCE",
                   skills=["Python", "PostgreSQL"], sections={"experience": "Built Python services."}),
            JobMatch(user_id=100, resume_id=100, job_title="PRIVATE ROLE", match_score=99),
            JobMatch(user_id=api.owner, resume_id=101, job_title="Owned match", match_score=75)])
        for owner in (api.owner, 100):
            for index in range(12):
                db.add(Opportunity(id=f"op-{owner}-{index}", user_id=owner, title=f"Role {owner} {index}",
                    company="Company", job_description="PRIVATE JOB CONTENT", job_snapshot={}, stage="saved"))
                db.add(Reminder(id=f"rem-{owner}-{index}", user_id=owner, message=f"Follow up {owner} {index}",
                    due_at=datetime(2026, 10, 30) + timedelta(hours=index), status="scheduled"))
        db.commit()
    native_before = api.c.native.sdk.admin.transport.get_database.call_count
    writes_before = list(api.c.native.calls)
    observed, remove = _reads_only(api.c.source)
    try:
        result = request(api)
    finally:
        remove()
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["bootstrap"]["profile"]["email"] == EMAIL
    assert body["summary"] == {"resume_count": 1, "applications_count": 1, "average_match_score": 75,
                               "resume_quality": {"evidenced_skills": 1, "verification_rate": 50}}
    assert body["opportunities"]["total"] == 12 and len(body["opportunities"]["items"]) == 8
    assert len(body["reminders"]) == 5
    assert all(row["id"].startswith(f"op-{api.owner}-") for row in body["opportunities"]["items"])
    assert all(row["id"].startswith(f"rem-{api.owner}-") for row in body["reminders"])
    assert not any(value in result.text for value in ("PRIVATE", "OWNED SOURCE", "password_hash", "candidate_lifetime"))
    assert api.validation.call_count == 1 and api.c.native.sdk.admin.transport.get_database.call_count - native_before == 4
    assert api.c.native.calls == writes_before and observed.commits == observed.flushes == 0
    assert all(sql.lstrip().upper().startswith("SELECT") for sql in observed.statements)
    assert result.headers["cache-control"] == "private, no-store"


def test_dashboard_known_connection_failure_is_explicit_partial_not_zero(api, monkeypatch):
    def unavailable(*_):
        raise OperationalError("synthetic select", {}, Exception("synthetic dependency unavailable"))
    monkeypatch.setattr(dashboard, "_summary", unavailable)
    result = request(api)
    assert result.status_code == 200
    body = result.json()
    assert body["summary"] is None and body["unavailable"] == ["summary"]
    assert body["bootstrap"]["profile"]["email"] == EMAIL and body["opportunities"] is not None


@pytest.mark.parametrize("error", [GuardDenied("synthetic denied"), ValueError("synthetic malformed"), RuntimeError("synthetic unknown")])
def test_dashboard_never_downgrades_authority_malformed_or_unknown_errors(api, monkeypatch, error):
    def fail(*_):
        raise error
    monkeypatch.setattr(dashboard, "_summary", fail)
    result = request(api)
    assert result.status_code == (401 if isinstance(error, GuardDenied) else 500)
    assert "bootstrap" not in result.text and "synthetic" not in result.text


def test_dashboard_current_subject_change_in_last_reader_refuses_entire_snapshot(api, monkeypatch):
    original = dashboard._reminders
    def drift(db, user_id):
        result = original(db, user_id)
        db.execute(update(User).where(User.id == user_id).values(credential_epoch=100))
        return result
    monkeypatch.setattr(dashboard, "_reminders", drift)
    result = request(api)
    assert result.status_code == 401 and "bootstrap" not in result.text


def test_dashboard_rejects_dynamic_scope_and_old_bootstrap_remains_compatible(api):
    assert api.client.get(PATH + "?user_id=100", headers=api.headers).status_code == 422
    assert api.client.get(PATH).status_code == 401
    old = api.client.get("/api/auth/bootstrap", headers=api.headers)
    assert old.status_code == 200 and old.json()["schema_version"] == "owned-bootstrap-v1"


def test_dashboard_malformed_credit_state_fails_whole_snapshot(api, monkeypatch):
    monkeypatch.setattr(dashboard, "availability_view", lambda *_: {"catalog_enabled": "wrong"})
    result = request(api)
    assert result.status_code == 500 and "bootstrap" not in result.text


def test_owned_reader404_cannot_trigger_old_server_compatibility(api, monkeypatch):
    def missing(*_):
        raise HTTPException(404, "Not Found")
    monkeypatch.setattr(dashboard, "_summary", missing)
    assert request(api).status_code == 503


def test_same_http_snapshot_keeps_two_fresh_lists_with_actual_intercepted_factory(api, monkeypatch):
    calls = []
    def listed(_request, **_kwargs):
        calls.append(1)
        return ListDatabasesResponse(databases=[Database(name=name, uid=uid, type_=1)
            for name, uid in api.c.native.identities.items()])
    api.c.native.sdk.admin.transport.list_databases.side_effect = listed
    admission = inert_shared_redis(monkeypatch, project="hirewiz-local-authority")
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true")
    before = api.c.native.sdk.admin.transport.get_database.call_count
    assert request(api).status_code == 200
    assert calls == [1, 1] and admission.grants == 2
    assert api.c.native.sdk.admin.transport.get_database.call_count == before
    assert api.validation.call_count == 1


def test_current_native_account_denial_prevents_dashboard_projection(api, monkeypatch):
    api.validation.side_effect = GuardDenied("synthetic closed account")
    monkeypatch.setattr(dashboard, "dashboard_view", lambda *_: pytest.fail("denied account reached projection"))
    assert request(api).status_code == 401
