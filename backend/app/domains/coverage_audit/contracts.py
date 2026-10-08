"""Strict, public/synthetic audit inputs. Declarations are not verified evidence."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Label = Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9 _./:@-]{0,159}$")]
Count = Annotated[int, Field(ge=0, le=1_000_000)]
Bound = Annotated[int, Field(gt=0, le=1_000_000)]


def instant(value: str) -> datetime:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value) is None:
        raise ValueError("Use an exact UTC second timestamp, YYYY-MM-DDTHH:MM:SSZ")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def unique(values: list[str], name: str) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"Duplicate {name}")


class Contract(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Stratum(Contract):
    id: Label
    country: Label  # Employer-frame geography; posting locations are separate.
    sector: Label
    size_band: Label
    ats_family: Label
    sample_size: Count
    max_reference_postings: Bound | None
    bound_evidence_ref: Label | None

    @model_validator(mode="after")
    def bound_evidence(self) -> Stratum:
        if (self.max_reference_postings is None) != (self.bound_evidence_ref is None):
            raise ValueError("A posting bound and its independent justification travel together")
        return self


class Employer(Contract):
    id: Label
    stratum_id: Label
    connector_status: Literal["supported", "unsupported", "unknown"]


class Scope(Contract):
    id: Label
    description: Annotated[str, Field(min_length=1, max_length=2000)]
    stratum_ids: Annotated[list[Label], Field(min_length=1)]
    critical_stratum_ids: Annotated[list[Label], Field(min_length=1)]
    posting_countries: Annotated[list[Label], Field(min_length=1)]
    languages: Annotated[list[Label], Field(min_length=1)]
    role_families: Annotated[list[Label], Field(min_length=1)]
    eligibility_policy_ref: Label
    candidate_query_criteria_ref: Label
    excluded_private_or_inaccessible_classes: list[Label]

    @model_validator(mode="after")
    def lists(self) -> Scope:
        for name in ("stratum_ids", "critical_stratum_ids", "posting_countries", "languages",
                     "role_families", "excluded_private_or_inaccessible_classes"):
            unique(getattr(self, name), name)
        if not set(self.critical_stratum_ids) <= set(self.stratum_ids):
            raise ValueError("Critical strata must belong to the declared scope")
        return self


class Provenance(Contract):
    synthetic: bool
    sampling_method: Literal["stratified_srs_without_replacement", "convenience", "unknown"]
    frame_independent_of_registry: bool
    frame_evidence_ref: Label | None
    draw_frozen_before_collection: bool
    draw_evidence_ref: Label | None
    reference_frozen_before_results: bool
    reference_evidence_ref: Label | None
    independent_human_review_declared: bool
    human_review_evidence_ref: Label | None
    whole_employer_holdout_declared: bool
    holdout_evidence_ref: Label | None
    independent_queries_declared: bool
    query_collection_evidence_ref: Label | None


class Posting(Contract):
    id: Label
    canonical_key: Label
    canonical_destination: Annotated[str, Field(max_length=2000)]
    destination_verified: bool
    country: Label
    language: Label
    role_family: Label
    status: Literal["active", "withdrawn", "uncertain"]
    original_date_status: Literal["known", "unknown", "republication_only"]
    original_published_at: str | None

    @field_validator("canonical_destination")
    @classmethod
    def public_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or parsed.fragment or any(char.isspace() for char in value)):
            raise ValueError("An exact credential-free HTTPS canonical employer destination is required")
        return value

    @model_validator(mode="after")
    def original_date(self) -> Posting:
        if self.original_date_status == "known":
            if self.original_published_at is None:
                raise ValueError("A known original date needs its evidence timestamp")
            instant(self.original_published_at)
        elif self.original_published_at is not None:
            raise ValueError("Do not substitute first-seen or republication time for an unknown original date")
        return self


class ReferenceEmployer(Contract):
    employer_id: Label
    observed_at: str
    enumeration_complete: bool
    human_reviewed: bool
    authoritative_snapshot_ref: Label | None
    independent_review_ref: Label | None
    postings: list[Posting]

    @field_validator("observed_at")
    @classmethod
    def time(cls, value: str) -> str:
        instant(value)
        return value


class CapturedEmployer(Contract):
    employer_id: Label
    origin_verified: bool
    observed_at: str

    @field_validator("observed_at")
    @classmethod
    def time(cls, value: str) -> str:
        instant(value)
        return value


class CapturedPosting(Contract):
    reference_posting_id: Label
    canonical_key: Label
    canonical_destination: str
    destination_verified: bool
    observed_at: str
    original_published_at: str | None
    first_seen_at: str
    last_origin_check_at: str | None

    @field_validator("observed_at", "first_seen_at", "original_published_at", "last_origin_check_at")
    @classmethod
    def time(cls, value: str | None) -> str | None:
        if value is not None:
            instant(value)
        return value

    @model_validator(mode="after")
    def chronology(self) -> CapturedPosting:
        observed = instant(self.observed_at)
        if any(value is not None and instant(value) > observed for value in
               (self.original_published_at, self.first_seen_at, self.last_origin_check_at)):
            raise ValueError("Capture dates cannot follow its observation time")
        return self


class Query(Contract):
    id: Label
    scope_id: Label
    criteria_ref: Label
    observed_at: str
    independent_judgment_ref: Label | None
    relevant_reference_complete: bool
    relevant_posting_ids: list[Label]
    retrieved_ids: list[Label]  # Before ranking/truncation. May include unjudged external IDs.
    top_k: Annotated[int, Field(ge=1, le=100)]
    ranked_ids: list[Label]
    judged_irrelevant_ids: list[Label]

    @field_validator("observed_at")
    @classmethod
    def time(cls, value: str) -> str:
        instant(value)
        return value

    @model_validator(mode="after")
    def results(self) -> Query:
        for name in ("relevant_posting_ids", "retrieved_ids", "ranked_ids", "judged_irrelevant_ids"):
            unique(getattr(self, name), name)
        if len(self.ranked_ids) > self.top_k or not set(self.ranked_ids) <= set(self.retrieved_ids):
            raise ValueError("Top-K must be a bounded subset of pre-ranking retrieval")
        if set(self.relevant_posting_ids) & set(self.judged_irrelevant_ids):
            raise ValueError("Relevant and irrelevant judgments conflict")
        return self


def draw_sample(employers: list[Employer], strata: list[Stratum], seed: str) -> dict[str, list[str]]:
    """Deterministic hash-ranked draw. Seed/design independence is an external obligation."""
    result = {}
    for stratum in sorted(strata, key=lambda row: row.id):
        members = [row.id for row in employers if row.stratum_id == stratum.id]
        result[stratum.id] = sorted(members, key=lambda identity: (
            hashlib.sha256(f"{seed}\0{stratum.id}\0{identity}".encode()).hexdigest(), identity,
        ))[:stratum.sample_size]
    return result


class AuditInput(Contract):
    schema_version: Annotated[int, Field(ge=1, le=1)]
    audit_id: Label
    frozen_at: str
    observation_at: str
    window_start: str
    window_end: str
    system_snapshot_ref: Label
    seed: Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
    provenance: Provenance
    strata: Annotated[list[Stratum], Field(min_length=1)]
    employers: Annotated[list[Employer], Field(min_length=1)]  # Full eligible sampling frame.
    development_employer_ids: list[Label]
    scopes: Annotated[list[Scope], Field(min_length=1)]
    reference: list[ReferenceEmployer]
    captured_employers: list[CapturedEmployer]
    captured_postings: list[CapturedPosting]
    queries: list[Query]

    @model_validator(mode="after")
    def relationships(self) -> AuditInput:
        # Reject lone surrogates even in descriptive text, without silently normalizing it.
        import json

        json.dumps(self.model_dump(), ensure_ascii=False).encode("utf-8", errors="strict")
        if not (instant(self.frozen_at) < instant(self.observation_at)
                and instant(self.window_start) <= instant(self.window_end)
                <= instant(self.observation_at)):
            raise ValueError("Freeze before observation and use an ordered, nonfuture date window")
        for rows, name in ((self.strata, "strata"), (self.employers, "employers"),
                           (self.scopes, "scopes"), (self.queries, "queries")):
            unique([row.id for row in rows], name)
        unique(self.development_employer_ids, "development employers")
        cells = {row.id: row for row in self.strata}
        dimensions = [(row.country, row.sector, row.size_band, row.ats_family) for row in self.strata]
        if len(dimensions) != len(set(dimensions)):
            raise ValueError("Strata must be mutually exclusive dimension combinations")
        if any(row.stratum_id not in cells for row in self.employers):
            raise ValueError("Every frame employer needs a declared stratum")
        for cell in self.strata:
            population = sum(row.stratum_id == cell.id for row in self.employers)
            if population == 0 or cell.sample_size > population:
                raise ValueError("Every stratum needs a population and a draw no larger than it")
        selected = {identity for ids in draw_sample(self.employers, self.strata, self.seed).values()
                    for identity in ids}
        if selected & set(self.development_employer_ids):
            raise ValueError("Whole-employer holdout intersects development/tuning employers")
        if any(not set(scope.stratum_ids) <= cells.keys() for scope in self.scopes):
            raise ValueError("Scopes must use complete declared strata")
        unique([row.employer_id for row in self.reference], "reference employers")
        unique([row.employer_id for row in self.captured_employers], "captured employers")
        if any(row.employer_id not in selected for row in self.reference + self.captured_employers):
            raise ValueError("Reference and employer captures must belong to the frozen draw")
        posting_rows = [posting for row in self.reference for posting in row.postings]
        unique([row.id for row in posting_rows], "reference postings")
        owners = {row.id: row.stratum_id for row in self.employers}
        for row in self.reference:
            bound = cells[owners[row.employer_id]].max_reference_postings
            if bound is not None and len(row.postings) > bound:
                raise ValueError("Posting bound exceeded; never truncate the reference to fit it")
            unique([posting.canonical_key for posting in row.postings], "canonical employer openings")
            if any(posting.original_published_at is not None
                   and instant(posting.original_published_at) > instant(self.observation_at)
                   for posting in row.postings):
                raise ValueError("Reference original publication cannot be in the future")
        postings = {row.id: row for row in posting_rows}
        unique([row.reference_posting_id for row in self.captured_postings], "posting captures")
        if any(row.reference_posting_id not in postings for row in self.captured_postings):
            raise ValueError("Captured matches need a reviewed reference posting identity")
        scopes = {scope.id: scope for scope in self.scopes}
        posting_owner = {posting.id: row.employer_id for row in self.reference for posting in row.postings}
        for query in self.queries:
            if query.scope_id not in scopes:
                raise ValueError("Queries need a declared scope")
            scope = scopes[query.scope_id]
            for identity in query.relevant_posting_ids:
                if identity not in postings or owners[posting_owner[identity]] not in scope.stratum_ids:
                    raise ValueError("Relevant reference jobs must belong to the query scope")
                posting = postings[identity]
                if not eligible(posting, scope, self.window_start, self.window_end):
                    raise ValueError("Relevant jobs conflict with frozen scope/date eligibility")
        return self


def eligible(posting: Posting, scope: Scope, start: str, end: str) -> bool:
    return (posting.status != "withdrawn" and posting.country in scope.posting_countries
            and posting.language in scope.languages and posting.role_family in scope.role_families
            and (posting.original_published_at is None
                 or instant(start) <= instant(posting.original_published_at) <= instant(end)))
