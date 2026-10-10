"""Real search/credit transactions with explicit employer facts and old clients."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from backend.app import models as core
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.employer import connectors, models, schemas, service, tasks
from backend.app.domains.employer.preference_metadata import normalize_metadata
from backend.app.domains.employer.role_aliases import variants
from backend.app.domains.employer.search_preferences import SearchPreferencesV1, evaluate
from backend.tests.test_employer_services import context as context
from fastapi import HTTPException
from pydantic import ValidationError


def facts(country="IN", commitment="Full-time", minimum=100, maximum=200, currency="INR", interval="year"):
    return normalize_metadata(SimpleNamespace(platform="lever"), {"country": country, "categories": {"commitment": commitment},
        "salaryRange": {"min": minimum, "max": maximum, "currency": currency, "interval": interval}})


def test_contract_has_unanswered_defaults_and_strict_version():
    result = SearchPreferencesV1()
    assert result.country_codes is None and result.sponsorship_required is None and result.authorized_country_codes is None
    assert result.unknown_metadata == "include"
    for raw in ({"version": True}, {"version": 2}, {"sponsorship_required": "false"}, {"unknown_metadata": "exclude"}, {"country_codes": ["in"]}, {"country_codes": []}, {"country_codes": ["IN", "IN"]}, {"citizenship": "IN"}):
        with pytest.raises(ValidationError):
            SearchPreferencesV1.model_validate(raw)


@pytest.mark.parametrize("salary", [
    {"currency": "INR", "period": "year"}, {"minimum": 200, "maximum": 100, "currency": "INR", "period": "year"},
    {"minimum": -1, "currency": "INR", "period": "year"}, {"minimum": "NaN", "currency": "INR", "period": "year"},
    {"minimum": "10.001", "currency": "INR", "period": "year"}, {"minimum": 100, "currency": "inr", "period": "year"},
])
def test_salary_contract_rejects_ambiguous_or_invalid_bounds(salary):
    with pytest.raises(ValidationError):
        SearchPreferencesV1(salary=salary)


def test_deterministic_preference_conflicts_and_unknown_axes():
    requested = SearchPreferencesV1(country_codes=["IN"], employment_types=["full_time"], salary={"minimum": "150", "currency": "INR", "period": "year"}, sponsorship_required=True, willing_to_relocate=False)
    result = evaluate(requested, facts())
    assert result["eligible"] and result["states"]["salary"] == "match" and result["states"]["sponsorship"] == "unknown"
    assert not evaluate(requested, facts(country="US"))["eligible"]
    assert not evaluate(requested, facts(maximum=120))["eligible"]
    assert evaluate(requested, facts(currency="USD"))["states"]["salary"] == "unknown"
    assert evaluate(requested, facts(interval="month"))["states"]["salary"] == "unknown"
    assert evaluate(requested, facts(commitment="Permanent"))["states"]["employment_types"] == "unknown"
    assert not evaluate(requested, facts(commitment="Part-time"))["eligible"]
    assert evaluate(requested, None)["eligible"] and evaluate(requested, {"version": 999})["eligible"]
    assert result["eligibility_verified"] is False


def test_partial_country_and_salary_search_persists_exact_quote_billing_and_unknown(context, monkeypatch):
    factory, client, _ = context
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "lever"
        db.commit()
    monkeypatch.setenv("EMPLOYER_SEARCH_CREDITS_PER_JOB", "2")
    with factory() as db:
        db.get(models.EmployerPosting, "job_1").preference_metadata = facts(country="US")
        db.commit()
    payload = {"resume_id": 10, "role": "Python Engineer", "desired_count": 3, "idempotency_key": "preference-search-1",
        "preferences": {"version": 1, "country_codes": ["IN"], "salary": {"minimum": "150", "currency": "INR", "period": "year"}, "sponsorship_required": True}}
    response = client.post("/api/v1/employer-jobs/searches", json=payload)
    assert response.status_code == 201, response.text
    result = response.json()
    assert (result["delivered_count"], result["charged_credits"], result["refunded_credits"]) == (1, 2, 4)
    assert result["items"][0]["posting"]["id"] == "job_2"
    assert result["items"][0]["preference_evaluation"]["states"]["salary"] == "unknown"
    assert result["scope"]["candidates_before_preferences"] == 2 and result["scope"]["known_preference_conflicts"] == 1
    assert result["scope"]["price_quote"]["search_input_fingerprint"] == payload_fingerprint(result["query"])
    assert result["scope"]["price_quote"]["maximum_credits"] == 6
    assert result["query"]["preferences"]["salary"]["minimum"] == "150"
    assert client.post("/api/v1/employer-jobs/searches", json=payload).json()["id"] == result["id"]
    changed = deepcopy(payload)
    changed["preferences"]["country_codes"] = ["US"]
    assert client.post("/api/v1/employer-jobs/searches", json=changed).status_code == 409
    changed["idempotency_key"] = "preference-search-2"
    second = client.post("/api/v1/employer-jobs/searches", json=changed).json()
    assert second["delivered_count"] == 2 and second["charged_credits"] == 2  # Unknown opening was already delivered.
    with factory() as db:
        user = db.get(core.User, 1)
        assert user.job_service_credits == 46 and user.ai_credits == 500 and user.tier == "premium"
        assert db.query(models.EmployerJobDelivery).count() == 2
        with pytest.raises(HTTPException) as denied:
            service.get_search(db, 2, result["id"])
        assert denied.value.status_code == 404
        assert service.list_searches(db, 2) == []


def test_legacy_fingerprint_retry_and_saved_snapshot_are_preserved(context):
    factory, client, _ = context
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "lever"
        db.commit()
    payload = {"resume_id": 10, "role": "Python Engineer", "idempotency_key": "legacy-pref-key"}
    original_query = schemas.SearchCreate(**payload).model_dump(exclude={"idempotency_key", "preferences"})
    result = client.post("/api/v1/employer-jobs/searches", json=payload).json()
    assert result["query"] == original_query and "preferences" not in result["query"]
    with factory() as db:
        saved = db.get(models.EmployerSearch, result["id"])
        assert saved.input_fingerprint == payload_fingerprint(original_query)
        db.get(models.EmployerPosting, "job_1").preference_metadata = facts(country="US")
        db.commit()
    assert client.post("/api/v1/employer-jobs/searches", json=payload).json() == result
    assert client.get(f'/api/v1/employer-jobs/searches/{result["id"]}').json()["items"] == result["items"]


def test_qualifiers_survive_aliases_and_known_employment_changes_results(context):
    factory, client, _ = context
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "lever"
        db.commit()
    with factory() as db:
        first, second = db.get(models.EmployerPosting, "job_1"), db.get(models.EmployerPosting, "job_2")
        first.title, first.description = "Senior Python Software Developer", "Senior Python software developer"
        first.preference_metadata = facts(commitment="Full-time")
        second.title, second.description = "Junior Python Software Engineer", "Junior Python software engineer"
        db.commit()
    payload = {"resume_id": 10, "role": "Senior Python SDE", "preferences": {"employment_types": ["full_time"]}, "idempotency_key": "alias-pref-key"}
    result = client.post("/api/v1/employer-jobs/searches", json=payload).json()
    assert result["delivered_count"] == 1 and result["items"][0]["posting"]["id"] == "job_1"
    assert all("senior python" in role for role in result["scope"]["role_variants"])
    assert variants("Data Scientist") == ["data scientist"]
    payload["preferences"]["employment_types"] = ["part_time"]
    payload["idempotency_key"] = "alias-pref-key2"
    result = client.post("/api/v1/employer-jobs/searches", json=payload).json()
    assert result["delivered_count"] == 0 and result["charged_credits"] == 0


def test_worker_upserts_optional_metadata_and_unknown_does_not_resurrect_old_fact(context, monkeypatch):
    factory, _, _ = context
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "lever"
        db.commit()
    row = {"external_id": "1", "requisition_id": "1", "title": "Python Engineer", "employer": "Verified Employer", "location": "Bengaluru", "description": "Python services", "remote": False, "canonical_url": "https://job-boards.greenhouse.io/verified/jobs/1", "apply_url": "https://job-boards.greenhouse.io/verified/jobs/1", "language": "en", "publication_at": None, "source_updated_at": None, "content_sha256": "b" * 64, "preference_metadata": facts()}
    monkeypatch.setattr(connectors, "fetch_postings", lambda *_: [deepcopy(row)])
    assert tasks.refresh_source("source_test") == "completed"
    with factory() as db:
        assert db.get(models.EmployerPosting, "job_1").preference_metadata["country_codes"]["value"] == ["IN"]
    row["preference_metadata"] = normalize_metadata(SimpleNamespace(platform="lever"), {})
    assert tasks.refresh_source("source_test") == "completed"
    with factory() as db:
        assert db.get(models.EmployerPosting, "job_1").preference_metadata["country_codes"]["state"] == "unknown"


def test_actual_salary_filter_excludes_only_comparable_known_conflict(context):
    factory, client, _ = context
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "lever"
        db.get(models.EmployerPosting, "job_1").preference_metadata = facts(maximum=120)
        db.get(models.EmployerPosting, "job_2").preference_metadata = facts(currency="USD")
        db.commit()
    response = client.post("/api/v1/employer-jobs/searches", json={"resume_id": 10, "role": "Python Engineer", "desired_count": 2, "idempotency_key": "salary-real-filter", "preferences": {"salary": {"minimum": "150", "currency": "INR", "period": "year"}}})
    assert response.status_code == 201
    result = response.json()
    assert result["delivered_count"] == 1 and result["items"][0]["posting"]["id"] == "job_2"
    assert result["items"][0]["preference_evaluation"]["states"]["salary"] == "unknown"
    assert (result["charged_credits"], result["refunded_credits"]) == (1, 1)


def test_actual_posting_locale_changes_results_without_certifying_working_language(context):
    factory, client, _ = context
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "personio"
        for identity, locale in (("job_1", "de"), ("job_2", "en")):
            db.get(models.EmployerPosting, identity).preference_metadata = normalize_metadata(SimpleNamespace(platform="personio"), {}, feed_language=locale)
        db.commit()
    response = client.post("/api/v1/employer-jobs/searches", json={"resume_id": 10, "role": "Python Engineer", "desired_count": 2, "idempotency_key": "locale-real-filter", "preferences": {"posting_languages": ["de"]}})
    assert response.status_code == 201
    result = response.json()
    assert result["delivered_count"] == 1 and result["items"][0]["posting"]["id"] == "job_1"
    assert result["items"][0]["preference_evaluation"]["states"]["posting_languages"] == "match"
    assert result["items"][0]["preference_evaluation"]["eligibility_verified"] is False
