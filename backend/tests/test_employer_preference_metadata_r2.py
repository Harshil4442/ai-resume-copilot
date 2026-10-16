"""Stored JSON trust, location completeness and refresh regressions for R2."""
from copy import deepcopy
from types import SimpleNamespace

import pytest
from backend.app.domains.common import payload_fingerprint
from backend.app.domains.employer import connectors, models, tasks
from backend.app.domains.employer.preference_metadata import normalize_metadata
from backend.app.domains.employer.search_preferences import (
    PostingPreferenceMetadata,
    SearchPreferencesV1,
    evaluate,
)
from backend.tests.test_employer_services import context as context
from pydantic import ValidationError


def normalized(provider, row):
    return normalize_metadata(SimpleNamespace(platform=provider), row)


def known(value):
    return {
        "state": "known", "value": value,
        "evidence": [{"field": "structural_fixture", "document": "https://example.test/contract", "raw_sha256": "a" * 64}],
    }


@pytest.mark.parametrize("version", [True, False, 1.0, "1", 0, 2, None])
def test_stored_version_rejects_coercion_before_country_can_exclude(version):
    metadata = normalized("lever", {"country": "US"})
    metadata["version"] = version
    with pytest.raises(ValidationError):
        PostingPreferenceMetadata.model_validate(metadata)
    result = evaluate(SearchPreferencesV1(country_codes=["IN"]), metadata, provider="lever")
    assert result["eligible"] and result["states"]["country_codes"] == "unknown"


def test_actual_integer_and_omitted_stored_version_preserve_existing_contract():
    metadata = normalized("lever", {"country": "US"})
    assert not evaluate(SearchPreferencesV1(country_codes=["IN"]), metadata)["eligible"]
    del metadata["version"]
    assert PostingPreferenceMetadata.model_validate(metadata).version == 1


@pytest.mark.parametrize("name", ["country_codes", "posting_languages", "employment_types", "work_authorization_required"])
def test_known_empty_employer_sets_cannot_claim_complete_requirements(name):
    metadata = {"provider": "lever", name: known([])}
    with pytest.raises(ValidationError):
        PostingPreferenceMetadata.model_validate(metadata)
    preferences = SearchPreferencesV1(country_codes=["IN"], posting_languages=["en"], employment_types=["full_time"], authorized_country_codes=[])
    result = evaluate(preferences, metadata)
    assert result["eligible"] and set(result["states"].values()) == {"unknown"}
    assert preferences.authorized_country_codes == []  # Explicit candidate answer, not null.


@pytest.mark.parametrize("secondary", [False, 0, "", {}, [False], [{}], [{"address": {"addressCountry": "US"}}] * 20])
def test_invalid_ashby_location_sets_leave_country_unknown_but_preserve_employment(secondary):
    metadata = normalized("ashby", {"address": {"postalAddress": {"addressCountry": "US"}}, "secondaryLocations": secondary, "employmentType": "FullTime"})
    assert metadata["country_codes"]["state"] == "unknown"
    assert metadata["employment_types"]["value"] == ["full_time"]
    assert evaluate(SearchPreferencesV1(country_codes=["IN"], employment_types=["full_time"]), metadata)["eligible"]


@pytest.mark.parametrize("optional", [{}, {"secondaryLocations": None}, {"secondaryLocations": []}])
def test_ashby_absent_null_or_complete_empty_secondary_set_preserves_primary(optional):
    metadata = normalized("ashby", {"address": {"postalAddress": {"addressCountry": "US"}}, **optional})
    assert metadata["country_codes"]["value"] == ["US"]


@pytest.mark.parametrize("locations", [None, [], False, 0, "", ["Austin", "Berlin"], ["Berlin"], [""], [None], ["Austin", "Austin"]])
def test_lever_incomplete_scope_does_not_extend_primary_country(locations):
    metadata = normalized("lever", {"country": "US", "categories": {"location": "Austin", "allLocations": locations, "commitment": "Full-time"}, "salaryRange": {"min": 100, "max": 200, "currency": "USD", "interval": "year"}})
    assert metadata["country_codes"]["state"] == "unknown"
    assert metadata["employment_types"]["value"] == ["full_time"]
    assert metadata["salary"]["value"]["currency"] == "USD"
    result = evaluate(SearchPreferencesV1(country_codes=["DE"], employment_types=["full_time"]), metadata)
    assert result["eligible"] and result["states"] == {"country_codes": "unknown", "employment_types": "match"}


@pytest.mark.parametrize("categories", [False, [], "", 0])
def test_lever_malformed_categories_cannot_certify_single_location(categories):
    assert normalized("lever", {"country": "US", "categories": categories})["country_codes"]["state"] == "unknown"


def test_lever_documented_single_location_preserves_scope_evidence():
    row = {"country": "US", "categories": {"location": "Austin", "allLocations": ["Austin"]}}
    fact = normalized("lever", row)["country_codes"]
    assert fact["value"] == ["US"]
    assert [(e["field"], e["raw_sha256"]) for e in fact["evidence"]] == [
        ("country", payload_fingerprint("US")),
        ("categories.location", payload_fingerprint("Austin")),
        ("categories.allLocations", payload_fingerprint(["Austin"])),
    ]
    assert not evaluate(SearchPreferencesV1(country_codes=["DE"]), normalized("lever", row))["eligible"]


def test_real_normalizer_and_worker_refresh_clear_previous_primary_country(context, monkeypatch):
    factory, _, _ = context
    source = SimpleNamespace(platform="lever", board_token="fixture", employer="Fixture", region="global", allowed_hosts=["jobs.lever.co"])
    row = {"id": "1", "text": "Python Engineer", "descriptionPlain": "Python services", "country": "US", "categories": {"location": "Austin", "allLocations": ["Austin"]}, "hostedUrl": "https://jobs.lever.co/fixture/1"}
    first = connectors._normalize(source, row)
    row["categories"]["allLocations"] = ["Austin", "Berlin"]
    second = connectors._normalize(source, row)
    assert first["content_sha256"] != second["content_sha256"]
    assert second["preference_metadata"]["country_codes"]["state"] == "unknown"
    with factory() as db:
        db.get(models.EmployerSource, "source_test").platform = "lever"
        db.get(models.EmployerPosting, "job_1").preference_metadata = first["preference_metadata"]
        db.commit()
    monkeypatch.setattr(connectors, "fetch_postings", lambda *_: [deepcopy(second)])
    assert tasks.refresh_source("source_test") == "completed"
    with factory() as db:
        refreshed = db.get(models.EmployerPosting, "job_1").preference_metadata
        assert refreshed["country_codes"]["state"] == "unknown"
        assert evaluate(SearchPreferencesV1(country_codes=["DE"]), refreshed)["eligible"]
