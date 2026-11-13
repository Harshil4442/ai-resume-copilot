"""Exact bearer/native scope with synthetic owner data and intercepted SDKs."""
from __future__ import annotations

import pytest
from google.cloud.firestore_admin_v1.types import Database, ListDatabasesResponse

from app.domains.career import service, workspace
from app.models import EvidenceItem, Opportunity, User
from tests.test_candidate_production_factory import configured as configured
from tests.test_native_metadata_admission import inert_shared_redis
from tests.test_owned_bootstrap import _reads_only
from tests.test_owned_bootstrap import api as api

PATH = "/api/v1/opportunities/owned-skill-roi/workspace"
QUERY = "?tab=learning&include_skill_roi=true"


@pytest.fixture
def learning(api, monkeypatch):
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ENABLED", "true")
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_PERCENT", "100")
    with api.c.accounts.sessions() as db:
        db.add(User(id=100, email="foreign-skill-roi@example.com"))
        db.add_all([
            Opportunity(id="owned-skill-roi", user_id=api.owner, title="Own role",
                job_description="PRIVATE BODY", job_snapshot={"required_skills": ["Python"]}),
            Opportunity(id="archived-skill-roi", user_id=api.owner, title="Archived role", stage="archived",
                job_snapshot={"required_skills": ["Archived skill"]}),
            Opportunity(id="foreign-skill-roi", user_id=100, title="Foreign role",
                job_snapshot={"required_skills": ["Foreign skill"]}),
            EvidenceItem(id="own-skill-roi-evidence", user_id=api.owner, title="Approved Python",
                category="experience", evidence_text="PRIVATE EVIDENCE", skills=["Python"], approval_state="approved"),
            EvidenceItem(id="foreign-skill-roi-evidence", user_id=100, title="Foreign evidence",
                category="experience", evidence_text="FOREIGN EVIDENCE", skills=["Python"], approval_state="approved"),
        ])
        db.commit()
    return api


def _list_transport(api, monkeypatch, *, budget=40):
    calls = []

    def listed(_request, **_kwargs):
        calls.append(1)
        return ListDatabasesResponse(databases=[
            Database(name=name, uid=uid, type_=1) for name, uid in api.c.native.identities.items()
        ])

    api.c.native.sdk.admin.transport.list_databases.side_effect = listed
    admission = inert_shared_redis(monkeypatch, project="hirewiz-local-authority", budget=budget)
    monkeypatch.setenv("CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED", "true")
    return calls, admission


def test_combined_read_has_two_fresh_lists_and_same_owned_skill_roi(learning, monkeypatch):
    calls, admission = _list_transport(learning, monkeypatch)
    observed, remove = _reads_only(learning.c.source)
    get_count = learning.c.native.sdk.admin.transport.get_database.call_count
    try:
        result = learning.client.get(PATH + QUERY, headers=learning.headers)
    finally:
        remove()
    assert result.status_code == 200, result.text
    assert result.headers["cache-control"] == "private, no-store"
    assert calls == [1, 1] and admission.grants == 2 and learning.validation.call_count == 1
    assert learning.c.native.sdk.admin.transport.get_database.call_count == get_count
    assert observed.commits == observed.flushes == 0
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in observed.statements)
    assert not any("opportunities.job_description" in statement or "evidence_items.evidence_text" in statement
                   for statement in observed.statements)
    summary = result.json()["skill_roi"]
    assert summary["opportunity_count"] == 1
    assert [item["skill"] for item in summary["items"]] == ["python"]
    assert summary["items"][0]["evidence_strength"] == 0.5
    assert learning.client.get("/api/v1/skill-roi", headers=learning.headers).json() == summary


@pytest.mark.parametrize(("path", "enabled", "authenticated", "status"), [
    (PATH + "?tab=learning", True, True, 200),
    (PATH + "?tab=overview&include_skill_roi=true", True, True, 200),
    (PATH.replace("owned-skill-roi", "foreign-skill-roi") + QUERY, True, True, 404),
    (PATH + QUERY, False, True, 404),
    (PATH + QUERY, True, False, 401),
])
def test_projection_requires_explicit_learning_opt_in_and_owned_access(
    learning, monkeypatch, path, enabled, authenticated, status,
):
    monkeypatch.setenv("FEATURE_CAREER_WORKSPACE_ENABLED", str(enabled).lower())
    monkeypatch.setattr(service, "calculate_skill_roi", lambda *_: pytest.fail("unrequested or denied ROI"))
    result = learning.client.get(path, headers=learning.headers if authenticated else {})
    assert result.status_code == status, result.text
    if status == 200:
        assert result.json()["skill_roi"] is None


def test_late_capacity_refusal_never_runs_combined_projection(learning, monkeypatch):
    calls, admission = _list_transport(learning, monkeypatch, budget=1)
    monkeypatch.setattr(service, "calculate_skill_roi", lambda *_: pytest.fail("capacity refusal exported ROI"))
    result = learning.client.get(PATH + QUERY, headers=learning.headers)
    assert result.status_code == 429, result.text
    assert result.headers["retry-after"] == "60"
    assert calls == [1] and admission.grants == 1


def test_late_dependency_failure_never_runs_combined_projection(learning, monkeypatch):
    calls, _ = _list_transport(learning, monkeypatch)
    listed = learning.c.native.sdk.admin.transport.list_databases.side_effect

    def missing(*args, **kwargs):
        if calls:
            raise RuntimeError("unavailable metadata")
        return listed(*args, **kwargs)

    learning.c.native.sdk.admin.transport.list_databases.side_effect = missing
    monkeypatch.setattr(service, "calculate_skill_roi", lambda *_: pytest.fail("dependency failure exported ROI"))
    result = learning.client.get(PATH + QUERY, headers=learning.headers)
    assert result.status_code == 503, result.text
    assert len(calls) == 1


def test_oversize_projection_is_explicitly_unavailable(learning, monkeypatch):
    monkeypatch.setattr(workspace, "MAX_SKILL_ROI_BYTES", 1)
    result = learning.client.get(PATH + QUERY, headers=learning.headers)
    assert result.status_code == 200 and result.json()["skill_roi"] is None
