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


def test_disabled_workspace_never_queries_restricted_previews(api, monkeypatch):
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ENABLED", "false")
    def forbidden(*_):
        pytest.fail("disabled workspace preview was queried")
    monkeypatch.setattr(dashboard, "_opportunities", forbidden)
    monkeypatch.setattr(dashboard, "_reminders", forbidden)
    result = request(api)
    assert result.status_code == 200
    body = result.json()
    assert body["bootstrap"]["features"]["career_workspace"]["enabled"] is False
    assert body["opportunities"] is None and body["reminders"] is None
    assert body["unavailable"] == ["opportunities", "reminders"]
    assert body["summary"] is not None


def test_workspace_kill_during_reader_withholds_whole_snapshot(api, monkeypatch):
    original = dashboard._summary
    def kill(db, user_id):
        value = original(db, user_id)
        monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ENABLED", "false")
        return value
    monkeypatch.setattr(dashboard, "_summary", kill)
    monkeypatch.setattr(dashboard, "_opportunities", lambda *_: pytest.fail("disabled data queried"))
    assert request(api).status_code == 401


def test_nullable_scores_count_as_zero_and_empty_average_remains_zero(api):
    with api.c.accounts.sessions() as db:
        db.add(Resume(id=55, user_id=api.owner, original_filename="average.pdf"))
        db.add_all([JobMatch(id=55, user_id=api.owner, resume_id=55, job_title="Scored", match_score=75),
                    JobMatch(id=56, user_id=api.owner, resume_id=55, job_title="Unscored")])
        db.commit()
        db.execute(update(JobMatch).where(JobMatch.id == 56).values(match_score=None))
        db.commit()
    assert request(api).json()["summary"]["average_match_score"] == 37.5
    with api.c.accounts.sessions() as db:
        db.query(JobMatch).delete()
        db.commit()
    assert request(api).json()["summary"]["average_match_score"] == 0


def test_latest_resume_null_dates_are_last_and_timestamp_ties_have_stable_id_order(api):
    with api.c.accounts.sessions() as db:
        for identifier, stamp, skills, sections in [
            (60, datetime(2026, 1, 1), ["Python"], {"experience": "Python"}),
            (61, datetime(2026, 1, 1), ["SQL"], {}),
            (62, datetime(2026, 1, 2), ["Java"], {"experience": "Java"}),
            (63, datetime(2026, 1, 3), ["Other"], {}),
        ]:
            db.add(Resume(id=identifier, user_id=api.owner, original_filename="dated.pdf",
                          created_at=stamp, skills=skills, sections=sections))
        db.commit()
        db.execute(update(Resume).where(Resume.id == 63).values(created_at=None))
        db.commit()
    observed, remove = _reads_only(api.c.source)
    try:
        assert request(api).json()["summary"]["resume_quality"]["verification_rate"] == 100
    finally:
        remove()
    # The actual executed projection explicitly declares PostgreSQL-compatible
    # ordering; a SQLite success alone could hide a missing NULLS LAST clause.
    assert any("resumes.created_at DESC NULLS LAST, resumes.id ASC" in sql for sql in observed.statements)
    with api.c.accounts.sessions() as db:
        db.query(Resume).filter(Resume.id == 62).delete()
        db.commit()
    assert request(api).json()["summary"]["resume_quality"]["verification_rate"] == 100
