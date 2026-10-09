"""Versioned explicit candidate choices; missing employer data is never eligibility."""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

CountryCode = Annotated[str, Field(pattern=r"^[A-Z]{2}$")]
LanguageCode = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Z]{2})?$")]
EmploymentType = Literal[
    "full_time",
    "part_time",
    "contract",
    "internship",
    "temporary",
    "freelance",
    "permanent",
    "traineeship",
]
Period = Literal["year", "month", "week", "day", "hour"]
Amount = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2, allow_inf_nan=False)]
POLICY_VERSION: Literal["employer-preferences-v1"] = "employer-preferences-v1"


class PreferenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class SalaryPreference(PreferenceContract):
    minimum: Amount | None = None
    maximum: Amount | None = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    period: Period

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum is None and self.maximum is None:
            raise ValueError("Enter at least one salary bound")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("Salary minimum cannot exceed maximum")
        return self


class SalaryRange(PreferenceContract):
    minimum: Amount
    maximum: Amount
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    period: Period

    @model_validator(mode="after")
    def ordered(self):
        if self.minimum > self.maximum:
            raise ValueError("Invalid salary range")
        return self


class SearchPreferencesV1(PreferenceContract):
    version: Literal[1] = 1
    country_codes: list[CountryCode] | None = Field(default=None, min_length=1, max_length=20)
    posting_languages: list[LanguageCode] | None = Field(default=None, min_length=1, max_length=20)
    employment_types: list[EmploymentType] | None = Field(default=None, min_length=1, max_length=8)
    salary: SalaryPreference | None = None
    sponsorship_required: StrictBool | None = None
    authorized_country_codes: list[CountryCode] | None = Field(default=None, max_length=20)
    willing_to_relocate: StrictBool | None = None
    unknown_metadata: Literal["include"] = "include"

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported preference version")
        return value

    @field_validator(
        "country_codes", "posting_languages", "employment_types", "authorized_country_codes"
    )
    @classmethod
    def unique_values(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("Preference choices must be unique")
        return value


class FieldEvidence(PreferenceContract):
    field: str = Field(min_length=1, max_length=200)
    document: str = Field(pattern=r"^https://", max_length=1000)
    raw_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class Fact[T](PreferenceContract):
    state: Literal["known", "unknown"] = "unknown"
    value: T | None = None
    evidence: list[FieldEvidence] = Field(default_factory=list, max_length=20)
    reason: str = Field(
        default="Not supplied by a reviewed structural provider field", max_length=300
    )

    @model_validator(mode="after")
    def evidence_state(self):
        if self.state == "known" and (self.value is None or not self.evidence):
            raise ValueError("Known facts need a value and structural provenance")
        if self.state == "unknown" and (self.value is not None or self.evidence):
            raise ValueError("Unknown data cannot carry a claimed fact")
        return self


class PostingPreferenceMetadata(PreferenceContract):
    version: Literal[1] = 1
    normalization_version: Literal["employer-preferences-v1"] = POLICY_VERSION
    provider: Literal[
        "greenhouse", "lever", "ashby", "smartrecruiters", "workable", "personio", "pinpoint"
    ]
    country_codes: Fact[Annotated[list[CountryCode], Field(min_length=1, max_length=20)]] = Field(default_factory=Fact)
    posting_languages: Fact[Annotated[list[LanguageCode], Field(min_length=1, max_length=20)]] = Field(default_factory=Fact)
    employment_types: Fact[Annotated[list[EmploymentType], Field(min_length=1, max_length=8)]] = Field(default_factory=Fact)
    salary: Fact[SalaryRange] = Field(default_factory=Fact)
    sponsorship_available: Fact[StrictBool] = Field(default_factory=Fact)
    work_authorization_required: Fact[Annotated[list[CountryCode], Field(min_length=1, max_length=20)]] = Field(default_factory=Fact)
    relocation_supported: Fact[StrictBool] = Field(default_factory=Fact)

    @field_validator("version", mode="before")
    @classmethod
    def strict_version(cls, value):
        # Literal[1] otherwise accepts True and 1.0 from historic JSON rows.
        if type(value) is not int or value != 1:
            raise ValueError("Unsupported posting metadata version")
        return value


def evaluate(preferences: SearchPreferencesV1 | None, metadata: dict | None, *, provider: str | None = None) -> dict:
    """Confirmed incompatible preferences exclude; unknown stays visible by default."""
    from pydantic import ValidationError

    states, messages = {}, []
    if preferences is None:
        return {"version": POLICY_VERSION, "eligible": True, "states": {}, "messages": [], "eligibility_verified": False}
    try:
        facts = PostingPreferenceMetadata.model_validate(metadata) if metadata is not None else None
        if facts and provider is not None and facts.provider != provider:
            facts = None
    except (ValidationError, TypeError):
        facts = None  # Unsupported/malformed historic metadata cannot invent a conflict.

    def choice(name, selected):
        if not selected:
            return
        fact = getattr(facts, name) if facts else None
        state = (
            "unknown"
            if fact is None or fact.state != "known"
            else "match"
            if set(selected) & set(fact.value)
            else "conflict"
        )
        states[name] = state

    choice("country_codes", preferences.country_codes)
    choice("posting_languages", preferences.posting_languages)
    if preferences.employment_types:
        fact = facts.employment_types if facts else None
        offered = set(fact.value or []) if fact and fact.state == "known" else set()
        # Hours, contract tenure and programme type are different axes. A
        # permanent role is not proof of full-time hours, for example.
        axes = (
            {"full_time", "part_time"},
            {"permanent", "contract", "freelance", "temporary"},
            {"internship", "traineeship"},
        )
        if offered & set(preferences.employment_types):
            states["employment_types"] = "match"
        elif all(
            any(selected in axis and offered & axis for axis in axes)
            for selected in preferences.employment_types
        ):
            states["employment_types"] = "conflict"
        else:
            states["employment_types"] = "unknown"
    if preferences.salary:
        expected, fact = preferences.salary, facts.salary if facts else None
        if (
            fact is None
            or fact.state != "known"
            or fact.value.currency != expected.currency
            or fact.value.period != expected.period
        ):
            states["salary"] = "unknown"
        else:
            offered = fact.value
            states["salary"] = (
                "conflict"
                if (
                    (expected.minimum is not None and offered.maximum < expected.minimum)
                    or (expected.maximum is not None and offered.minimum > expected.maximum)
                )
                else "match"
            )
    if preferences.sponsorship_required is True:
        fact = facts.sponsorship_available if facts else None
        states["sponsorship"] = (
            "unknown"
            if fact is None or fact.state != "known"
            else "match"
            if fact.value
            else "conflict"
        )
    if preferences.authorized_country_codes is not None:
        fact = facts.work_authorization_required if facts else None
        if fact is None or fact.state != "known":
            states["work_authorization"] = "unknown"
        elif set(preferences.authorized_country_codes) & set(fact.value):
            states["work_authorization"] = "match"
        elif preferences.sponsorship_required is True:
            # A sponsorship route may exist. This never certifies eligibility.
            states["work_authorization"] = "unknown"
        else:
            states["work_authorization"] = "conflict"
    if preferences.willing_to_relocate is not None:
        # A location/remote label doesn't establish whether this candidate must
        # move. Keep the candidate answer without turning it into eligibility.
        states["relocation"] = "unknown"
    for name, state in states.items():
        if state == "unknown":
            messages.append(
                f"{name.replace('_', ' ')}: employer evidence unavailable or not comparable; review the posting"
            )
    return {
        "version": POLICY_VERSION,
        "eligible": "conflict" not in states.values(),
        "states": states,
        "messages": messages,
        "eligibility_verified": False,
    }
