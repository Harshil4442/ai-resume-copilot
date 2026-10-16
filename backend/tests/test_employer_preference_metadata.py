"""Reviewed public fixture fields, optional-malformed data and honest unknowns."""
from types import SimpleNamespace

import pytest
from backend.app.domains.employer import connectors
from backend.app.domains.employer.preference_metadata import normalize_metadata
from backend.app.domains.employer.search_preferences import PostingPreferenceMetadata


def metadata(provider, row, **kwargs):
    return PostingPreferenceMetadata.model_validate(normalize_metadata(SimpleNamespace(platform=provider), row, **kwargs))


def test_lever_country_commitment_salary_and_exact_public_provenance():
    result = metadata("lever", {"country": "IN", "categories": {"commitment": "Full-time"},
        "salaryRange": {"min": 1200000, "max": 1800000, "currency": "INR", "interval": "year"}})
    assert result.country_codes.value == ["IN"]
    assert result.employment_types.value == ["full_time"]
    assert result.salary.value.currency == "INR" and str(result.salary.value.minimum) == "1200000"
    assert result.salary.evidence[0].field == "salaryRange"
    assert result.country_codes.evidence[0].document == "https://github.com/lever/postings-api"
    assert len(result.country_codes.evidence[0].raw_sha256) == 64


def test_ashby_multiple_countries_and_single_supplied_salary_component():
    row = {"address": {"postalAddress": {"addressCountry": "USA"}}, "secondaryLocations": [
        {"address": {"addressCountry": "CAN"}}], "employmentType": "Contract", "compensation": {
        "summaryComponents": [{"compensationType": "Equity", "minValue": 1, "maxValue": 2},
            {"compensationType": "Salary", "minValue": 100, "maxValue": 200, "currencyCode": "USD", "interval": "1 YEAR"}]}}
    result = metadata("ashby", row)
    assert result.country_codes.value == ["US", "CA"]
    assert result.salary.value.period == "year" and result.employment_types.value == ["contract"]
    row["compensation"]["summaryComponents"].append(row["compensation"]["summaryComponents"][1])
    assert metadata("ashby", row).salary.state == "unknown"  # No ambiguous tier flattening.
    row["secondaryLocations"].append({"address": {"addressCountry": "unreviewed"}})
    assert metadata("ashby", row).country_codes.state == "unknown"  # Incomplete location set.


def test_smartrecruiters_personio_and_posting_locale_are_distinct_facts():
    result = metadata("smartrecruiters", {"location": {"country": "in"}, "typeOfEmployment": {"label": "Permanent"}, "language": "en"})
    assert result.country_codes.value == ["IN"] and result.employment_types.value == ["permanent"]
    assert result.posting_languages.state == "unknown"  # Default language isn't a requirement.
    result = metadata("personio", {"employmentType": "permanent", "schedule": "part-time"}, feed_language="de")
    assert result.employment_types.value == ["permanent", "part_time"]
    assert result.posting_languages.value == ["de"] and result.country_codes.state == "unknown"
    assert metadata("pinpoint", {}, feed_language="pt-BR").posting_languages.value == ["pt-BR"]


@pytest.mark.parametrize("provider", ["greenhouse", "lever", "ashby", "smartrecruiters", "workable", "personio", "pinpoint"])
def test_missing_metadata_and_job_prose_never_invent_salary_or_eligibility(provider):
    result = metadata(provider, {"description": "Remote worldwide, INR 5000000. No sponsorship, must be a citizen.", "language": "en"})
    assert all(getattr(result, key).state == "unknown" for key in ("country_codes", "posting_languages", "employment_types", "salary", "sponsorship_available", "work_authorization_required", "relocation_supported"))


@pytest.mark.parametrize("provider,row", [
    ("lever", {"categories": [], "salaryRange": "invalid"}),
    ("ashby", {"address": [], "secondaryLocations": [False], "compensation": 7}),
    ("smartrecruiters", {"location": [], "typeOfEmployment": "Permanent"}),
    ("personio", {"employmentType": ["permanent"], "schedule": False}),
    ("lever", {"salaryRange": {"min": True, "max": 10, "currency": "USD", "interval": "year"}}),
    ("lever", {"salaryRange": {"min": 20, "max": 10, "currency": "USD", "interval": "year"}}),
    ("lever", {"salaryRange": {"min": "NaN", "max": 10, "currency": "USD", "interval": "year"}}),
])
def test_malformed_optional_shapes_stay_unknown_without_discarding_posting(provider, row):
    result = metadata(provider, row)
    assert result.salary.state == "unknown"


def test_personio_real_xml_parser_preserves_schedule_and_feed_locale():
    source = SimpleNamespace(platform="personio", careers_url="https://fixture.jobs.personio.de/?language=de", board_token="fixture", allowed_hosts=["fixture.jobs.personio.de"])
    rows = connectors._personio_rows(source, b'<workzag-jobs><position><id>123</id><name>Engineer</name><office>Berlin</office><employmentType>permanent</employmentType><schedule>full-time</schedule><jobDescriptions><jobDescription><name>Role</name><value>Build services.</value></jobDescription></jobDescriptions></position></workzag-jobs>')
    assert rows[0]["schedule"] == "full-time" and rows[0]["employmentType"] == "permanent"
    assert metadata("personio", rows[0], feed_language=rows[0]["language"]).employment_types.value == ["permanent", "full_time"]


def test_actual_normalizer_binds_metadata_into_content_hash():
    source = SimpleNamespace(platform="lever", board_token="fixture", employer="Fixture", region="global", allowed_hosts=["jobs.lever.co"])
    row = {"id": "123", "text": "Engineer", "descriptionPlain": "Build services", "country": "IN", "categories": {"location": "Bengaluru", "commitment": "Full-time"}, "hostedUrl": "https://jobs.lever.co/fixture/123"}
    first = connectors._normalize(source, row)
    row["country"] = "US"
    second = connectors._normalize(source, row)
    assert first["preference_metadata"]["country_codes"]["value"] == ["IN"]
    assert first["content_sha256"] != second["content_sha256"]
