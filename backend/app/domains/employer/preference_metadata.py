"""Reviewed structural fields only. Never parse job prose for pay/eligibility."""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from ..common import payload_fingerprint
from .search_preferences import Fact, FieldEvidence, PostingPreferenceMetadata, SalaryRange

DOCS = {
    "lever": "https://github.com/lever/postings-api",
    "ashby": "https://developers.ashbyhq.com/docs/public-job-posting-api",
    "smartrecruiters": "https://developers.smartrecruiters.com/docs/endpoints",
    "personio": "https://developer.personio.de/docs/integration-of-open-positions",
    "pinpoint": "https://developers.pinpointhq.com/docs/jobs-json-endpoint",
}
EMPLOYMENT = {
    "fulltime": "full_time",
    "full-time": "full_time",
    "full time": "full_time",
    "parttime": "part_time",
    "part-time": "part_time",
    "part time": "part_time",
    "contract": "contract",
    "intern": "internship",
    "internship": "internship",
    "temporary": "temporary",
    "permanent": "permanent",
    "freelance": "freelance",
    "trainee": "traineeship",
}
PERIODS = {
    "year": "year",
    "yearly": "year",
    "annual": "year",
    "1 year": "year",
    "month": "month",
    "monthly": "month",
    "1 month": "month",
    "week": "week",
    "weekly": "week",
    "1 week": "week",
    "day": "day",
    "daily": "day",
    "1 day": "day",
    "hour": "hour",
    "hourly": "hour",
    "1 hour": "hour",
}
# Small explicit normalization policy for provider alpha-3 values. Unknown codes
# are retained as unknown, never guessed from locality or remote geography.
ALPHA3 = dict(
    zip(
        ["USA", "IND", "GBR", "CAN", "AUS", "DEU", "FRA", "SGP", "JPN", "ARE", "NLD", "IRL", "NZL"],
        ["US", "IN", "GB", "CA", "AU", "DE", "FR", "SG", "JP", "AE", "NL", "IE", "NZ"],
        strict=True,
    )
)


def _mapping(value):
    return value if type(value) is dict else {}


def _country(value):
    if type(value) is not str:
        return None
    value = value.upper().strip()
    return value if re.fullmatch(r"[A-Z]{2}", value) else ALPHA3.get(value)


def _fact(provider, value, fields):
    return Fact(
        state="known",
        value=value,
        reason="Documented structural provider field",
        evidence=[
            FieldEvidence(field=name, document=DOCS[provider], raw_sha256=payload_fingerprint(raw))
            for name, raw in fields
        ],
    )


def _lever_country_fields(row: dict) -> list[tuple[str, Any]] | None:
    """Do not extend Lever's primary country to unresolved extra locations."""
    categories = row.get("categories")
    if categories is not None and type(categories) is not dict:
        return None
    categories = _mapping(categories)
    fields = [("country", row.get("country"))]
    if "allLocations" not in categories:
        # Compatibility with legacy single-location payloads carrying country.
        # A missing optional location set is not a city-to-country inference.
        return fields
    locations, primary = categories["allLocations"], categories.get("location")
    if (
        type(locations) is not list
        or len(locations) != 1
        or type(primary) is not str
        or not primary.strip()
        or type(locations[0]) is not str
        or locations[0].strip() != primary.strip()
    ):
        return None
    fields.extend([
        ("categories.location", primary),
        ("categories.allLocations", locations),
    ])
    return fields


def normalize_metadata(source, row: dict, *, feed_language: str | None = None) -> dict:
    provider, facts = source.platform, {}
    if provider == "lever":
        country = _country(row.get("country"))
        country_fields = _lever_country_fields(row)
        if country and country_fields is not None:
            facts["country_codes"] = _fact(provider, [country], country_fields)
        commitment = _mapping(row.get("categories")).get("commitment")
        if type(commitment) is str and commitment.strip().lower() in EMPLOYMENT:
            facts["employment_types"] = _fact(
                provider,
                [EMPLOYMENT[commitment.strip().lower()]],
                [("categories.commitment", commitment)],
            )
        salary = row.get("salaryRange")
        if type(salary) is dict:
            _salary(facts, provider, salary, "salaryRange", "min", "max", "currency", "interval")
    elif provider == "ashby":
        countries: list[str] = []
        evidence = []
        primary = _mapping(_mapping(row.get("address")).get("postalAddress")).get("addressCountry")
        pairs = [("address.postalAddress.addressCountry", primary)]
        secondary = row.get("secondaryLocations")
        if secondary is None:
            secondary = []  # Optional absent/null differs from malformed falsy input.
        if type(secondary) is list and len(secondary) <= 19:
            for index, item in enumerate(secondary):
                raw = (
                    _mapping(item.get("address")).get("addressCountry")
                    if type(item) is dict
                    else None
                )
                pairs.append((f"secondaryLocations[{index}].address.addressCountry", raw))
            for path, raw in pairs:
                country = _country(raw)
                if not country:
                    countries = []
                    break
                countries.append(country)
                evidence.append((path, raw))
            if countries:
                facts["country_codes"] = _fact(provider, list(dict.fromkeys(countries)), evidence)
        employment = row.get("employmentType")
        if type(employment) is str and employment.lower() in EMPLOYMENT:
            facts["employment_types"] = _fact(
                provider, [EMPLOYMENT[employment.lower()]], [("employmentType", employment)]
            )
        components = _mapping(row.get("compensation")).get("summaryComponents") or []
        if type(components) is list and len(components) <= 20:
            salary = [
                (index, value)
                for index, value in enumerate(components)
                if type(value) is dict and value.get("compensationType") == "Salary"
            ]
            if len(salary) == 1:
                index, component = salary[0]
                _salary(
                    facts,
                    provider,
                    component,
                    f"compensation.summaryComponents[{index}]",
                    "minValue",
                    "maxValue",
                    "currencyCode",
                    "interval",
                )
    elif provider == "smartrecruiters":
        country = _country(_mapping(row.get("location")).get("country"))
        if country:
            facts["country_codes"] = _fact(
                provider, [country], [("location.country", row["location"]["country"])]
            )
        employment = _mapping(row.get("typeOfEmployment")).get("label")
        if type(employment) is str and employment.lower() in EMPLOYMENT:
            facts["employment_types"] = _fact(
                provider, [EMPLOYMENT[employment.lower()]], [("typeOfEmployment.label", employment)]
            )
    elif provider == "personio":
        employment, schedule = row.get("employmentType"), row.get("schedule")
        values, fields = [], []
        for path, raw in (("employmentType", employment), ("schedule", schedule)):
            if type(raw) is str and raw.lower() in EMPLOYMENT:
                values.append(EMPLOYMENT[raw.lower()])
                fields.append((path, raw))
        if values:
            facts["employment_types"] = _fact(provider, list(dict.fromkeys(values)), fields)
    # Feed locale is posting display language, never working-language eligibility.
    if (
        provider in {"personio", "pinpoint"}
        and type(feed_language) is str
        and re.fullmatch(r"[a-z]{2,3}(?:-[A-Z]{2})?", feed_language)
    ):
        facts["posting_languages"] = _fact(
            provider, [feed_language], [("configured_public_feed_locale", feed_language)]
        )
    # No currently reviewed public contract supplies sponsorship, work
    # authorization or relocation facts. Custom field labels/prose are unsafe.
    try:
        return PostingPreferenceMetadata(provider=provider, **facts).model_dump(mode="json")
    except ValidationError:
        return PostingPreferenceMetadata(provider=provider).model_dump(mode="json")


def _salary(
    facts: dict,
    provider: str,
    raw: dict[str, Any],
    path: str,
    minimum: str,
    maximum: str,
    currency: str,
    interval: str,
):
    period = PERIODS.get(str(raw.get(interval) or "").lower())
    try:
        # bool isn't a monetary quantity; unsupported/partial/invalid structures
        # stay unknown without dropping the otherwise valid employer posting.
        if type(raw.get(minimum)) not in {str, int, float} or type(raw.get(maximum)) not in {
            str,
            int,
            float,
        }:
            return
        value = SalaryRange(
            minimum=raw.get(minimum),
            maximum=raw.get(maximum),
            currency=raw.get(currency),
            period=period,
        )
        facts["salary"] = _fact(
            provider,
            value.model_dump(mode="json"),
            [(path, {key: raw.get(key) for key in (minimum, maximum, currency, interval)})],
        )
    except ValidationError:
        return
