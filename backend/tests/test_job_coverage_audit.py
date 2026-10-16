"""Offline synthetic design/denominator tests; no market reference is established."""
from __future__ import annotations

import itertools
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from app.domains.coverage_audit.audit import ClusterCell, audit, lower_bound
from app.domains.coverage_audit.contracts import AuditInput, Employer, Stratum, draw_sample

NOW = "2026-10-09T12:00:00Z"
EARLIER = "2026-10-08T12:00:00Z"


def sample_input() -> dict[str, Any]:
    strata = [{"id": "IN-small-custom", "country": "IN", "sector": "software",
               "size_band": "small", "ats_family": "custom", "sample_size": 2,
               "max_reference_postings": 2, "bound_evidence_ref": "synthetic-bound"}]
    employers = [{"id": f"employer-{i}", "stratum_id": "IN-small-custom",
                  "connector_status": "unsupported"} for i in range(4)]
    seed = "0" * 64
    chosen = draw_sample([Employer.model_validate(row) for row in employers],
                         [Stratum.model_validate(row) for row in strata], seed)["IN-small-custom"]
    reference: list[dict[str, Any]] = []
    for employer in chosen:
        reference.append({"employer_id": employer, "observed_at": NOW, "enumeration_complete": True,
                          "human_reviewed": True, "authoritative_snapshot_ref": "synthetic-snapshot",
                          "independent_review_ref": "synthetic-review", "postings": [{
                              "id": employer + "-job", "canonical_key": employer + ":opening",
                              "canonical_destination": "https://example.invalid/" + employer,
                              "destination_verified": True, "country": "IN", "language": "en",
                              "role_family": "software", "status": "active", "original_date_status": "known",
                              "original_published_at": EARLIER,
                          }]})
    posting = reference[0]["postings"][0]
    return {"schema_version": 1, "audit_id": "synthetic-coverage-example", "seed": seed,
            "frozen_at": "2026-10-01T12:00:00Z", "observation_at": NOW,
            "window_start": "2026-10-02T00:00:00Z", "window_end": NOW,
            "system_snapshot_ref": "synthetic-index-snapshot",
            "provenance": {"synthetic": True, "sampling_method": "stratified_srs_without_replacement",
                           "frame_independent_of_registry": False, "frame_evidence_ref": None,
                           "draw_frozen_before_collection": False, "draw_evidence_ref": None,
                           "reference_frozen_before_results": False, "reference_evidence_ref": None,
                           "independent_human_review_declared": False, "human_review_evidence_ref": None,
                           "whole_employer_holdout_declared": False, "holdout_evidence_ref": None,
                           "independent_queries_declared": False, "query_collection_evidence_ref": None},
            "strata": strata, "employers": employers, "development_employer_ids": [],
            "scopes": [{"id": "india-software", "description": "Synthetic bounded employer population only",
                        "stratum_ids": ["IN-small-custom"], "critical_stratum_ids": ["IN-small-custom"],
                        "posting_countries": ["IN"], "languages": ["en"], "role_families": ["software"],
                        "eligibility_policy_ref": "synthetic-policy", "candidate_query_criteria_ref": "synthetic-queries",
                        "excluded_private_or_inaccessible_classes": ["private-vacancies"]}],
            "reference": reference,
            "captured_employers": [{"employer_id": chosen[0], "origin_verified": True, "observed_at": NOW}],
            "captured_postings": [{"reference_posting_id": posting["id"], "canonical_key": posting["canonical_key"],
                                   "canonical_destination": posting["canonical_destination"], "destination_verified": True,
                                   "observed_at": NOW, "original_published_at": EARLIER, "first_seen_at": EARLIER,
                                   "last_origin_check_at": NOW}],
            "queries": [{"id": "query-1", "scope_id": "india-software", "criteria_ref": "synthetic-query",
                         "observed_at": NOW, "independent_judgment_ref": "synthetic-judgment",
                         "relevant_reference_complete": True,
                         "relevant_posting_ids": [row["postings"][0]["id"] for row in reference],
                         "retrieved_ids": [posting["id"], "outside-unjudged"], "top_k": 2,
                         "ranked_ids": [posting["id"], "outside-unjudged"], "judged_irrelevant_ids": []}]}


def declare_evidence(value: dict) -> None:
    value["provenance"]["synthetic"] = False
    for key in value["provenance"]:
        if key.endswith("_ref"):
            value["provenance"][key] = "declared-but-not-verified"
        elif key not in {"synthetic", "sampling_method"}:
            value["provenance"][key] = True


def test_unsupported_employers_and_misses_remain_in_denominator_and_no_claim_is_minted():
    data = AuditInput.model_validate(sample_input())
    result = audit(data)
    overall = result["scopes"][0]["overall"]
    assert overall["unsupported_sampled_employers"] == 2
    assert overall["reference_candidate_postings"] == 2
    assert overall["captured_verified_reference_postings"] == 1
    metric = overall["metrics"]["posting_recall"]
    assert metric["diagnostic_estimate_under_declared_reference"] == 0.5
    assert metric["estimate"] is None and metric["one_sided_lower_bound"] is None
    assert metric["status"] == "insufficient_evidence"
    assert result["independent_human_reference_established"] is False
    assert result["public_coverage_claim_authorized"] is False
    assert result["automatic_submission_coverage_measured"] is False
    assert audit(data) == result


def test_more_postings_in_one_employer_do_not_create_more_independent_samples():
    single = lower_bound([ClusterCell(100, 1, (1,), (1,))])
    large = lower_bound([ClusterCell(100, 1000, (1000,), (1000,))])
    assert single["one_sided_lower_bound"] == large["one_sided_lower_bound"] == 0
    assert large["estimate"] == 1


def test_design_weights_are_population_over_sample_and_ratio_is_not_mean_employer_recall():
    result = lower_bound([ClusterCell(2, 10, (1, 0), (1, 10)),
                          ClusterCell(100, 10, (10, 10), (10, 10))])
    assert result["weighted_recovered_total"] == 1001
    assert result["weighted_reference_total"] == 1011
    assert result["estimate"] == pytest.approx(1001 / 1011)


def test_census_is_exact_but_zero_denominator_missing_strata_and_unknown_bounds_are_not():
    assert lower_bound([ClusterCell(2, None, (2, 1), (2, 3))])["one_sided_lower_bound"] == 0.6
    for cell, issue in [(ClusterCell(4, 2, (), ()), "unsampled_stratum"),
                        (ClusterCell(4, None, (1,), (1,)), "missing_independent_cluster_bound"),
                        (ClusterCell(1, 1, (0,), (0,)), "empty_denominator")]:
        result = lower_bound([cell])
        assert result["one_sided_lower_bound"] is None and issue in result["issues"]


def test_stricter_confidence_allocation_cannot_improve_the_bound():
    cells = [ClusterCell(1000, 1, (1,) * 100, (1,) * 100)]
    assert lower_bound(cells, 0.01)["one_sided_lower_bound"] < lower_bound(cells, 0.05)["one_sided_lower_bound"]
    assert lower_bound([ClusterCell(1, 100, (95,), (100,))])["one_sided_lower_bound"] == 0.95


def test_finite_population_enumeration_checks_conservative_one_sided_coverage():
    totals = (1,) * 20
    successes = (1,) * 18 + (0, 0)
    truth = sum(successes) / sum(totals)
    # Enumerate every possible SRS draw; postings within an employer stay together.
    for n in (18, 19, 20):
        draws = list(itertools.combinations(range(len(totals)), n))
        bounds = [lower_bound([ClusterCell(20, 1, tuple(successes[i] for i in draw),
                                          tuple(totals[i] for i in draw))])["one_sided_lower_bound"]
                  for draw in draws]
        assert min(bounds) > 0  # Exercise useful, nonzero bounds rather than a degenerate zero.
        failures = sum(bound > truth for bound in bounds)
        assert failures / len(draws) <= 0.05


@pytest.mark.parametrize("cell", [ClusterCell(0, 1, (), ()), ClusterCell(2, True, (1,), (1,)),
                                   ClusterCell(2, 1, (2,), (1,)), ClusterCell(2, 1, (1,), (2,)),
                                   ClusterCell(2, 1, (True,), (1,)), ClusterCell(1, 1, (1, 1), (1, 1))])
def test_statistical_design_rejects_impossible_or_coerced_denominators(cell):
    with pytest.raises(ValueError):
        lower_bound([cell])


def test_query_retrieval_is_distinct_from_posting_capture_and_top_k_usefulness():
    value = sample_input()
    value["queries"][0]["retrieved_ids"] = ["outside-unjudged"]
    value["queries"][0]["ranked_ids"] = ["outside-unjudged"]
    result = audit(AuditInput.model_validate(value))["scopes"][0]
    assert result["overall"]["metrics"]["posting_recall"]["diagnostic_estimate_under_declared_reference"] == 0.5
    assert result["overall"]["metrics"]["query_retrieval_recall"]["diagnostic_estimate_under_declared_reference"] == 0
    assert result["top_k_usefulness"][0]["precision_at_k_lower"] == 0
    assert result["top_k_usefulness"][0]["precision_at_k_upper"] == 0.5


@pytest.mark.parametrize("case", ["date", "status", "incomplete", "missing", "reference-time", "query-time"])
def test_unresolved_reference_and_observation_issues_block_inference(case):
    value = sample_input()
    declare_evidence(value)
    if case == "date":
        value["reference"][0]["postings"][0].update(original_date_status="unknown", original_published_at=None)
    elif case == "status":
        value["reference"][0]["postings"][0]["status"] = "uncertain"
    elif case == "incomplete":
        value["reference"][0]["enumeration_complete"] = False
    elif case == "missing":
        value["reference"].pop()
        value["queries"][0]["relevant_posting_ids"].pop()
    elif case == "reference-time":
        value["reference"][0]["observed_at"] = EARLIER
    else:
        value["queries"][0]["observed_at"] = EARLIER
    report = audit(AuditInput.model_validate(value))["scopes"][0]["overall"]
    metric = "query_retrieval_recall" if case == "query-time" else "posting_recall"
    assert report["metrics"][metric]["status"] == "insufficient_evidence"
    assert report["metrics"][metric]["one_sided_lower_bound"] is None


@pytest.mark.parametrize("case", ["wrong-key", "wrong-destination", "unverified", "old-observation"])
def test_canonical_and_temporal_capture_failures_count_as_misses(case):
    value = sample_input()
    captured = value["captured_postings"][0]
    if case == "wrong-key":
        captured["canonical_key"] = "wrong"
    elif case == "wrong-destination":
        captured["canonical_destination"] = "https://example.invalid/wrong"
    elif case == "unverified":
        captured["destination_verified"] = False
    else:
        captured["observed_at"] = EARLIER
        captured["last_origin_check_at"] = EARLIER
    result = audit(AuditInput.model_validate(value))["scopes"][0]["overall"]
    assert result["captured_verified_reference_postings"] == 0
    assert len(result["missed_posting_ids"]) == 2


@pytest.mark.parametrize("case", ["duplicate-frame", "duplicate-posting", "private-date", "bound", "holdout",
                                   "bad-time", "extra", "coerced", "query-scope", "ranking", "judgment"])
def test_strict_schema_rejects_denominator_and_sampling_corruption(case):
    value = sample_input()
    if case == "duplicate-frame":
        value["employers"].append(deepcopy(value["employers"][0]))
    elif case == "duplicate-posting":
        value["reference"][1]["postings"][0]["id"] = value["reference"][0]["postings"][0]["id"]
    elif case == "private-date":
        value["reference"][0]["postings"][0]["original_date_status"] = "republication_only"
    elif case == "bound":
        value["strata"][0]["max_reference_postings"] = 1
        extra = deepcopy(value["reference"][0]["postings"][0])
        extra.update(id="extra", canonical_key="extra")
        value["reference"][0]["postings"].append(extra)
    elif case == "holdout":
        value["development_employer_ids"] = [value["reference"][0]["employer_id"]]
    elif case == "bad-time":
        value["observation_at"] = "2026-10-09"
    elif case == "extra":
        value["ignored_misses"] = True
    elif case == "coerced":
        value["schema_version"] = True
    elif case == "query-scope":
        value["queries"][0]["relevant_posting_ids"] = ["unknown-reference-job"]
    elif case == "ranking":
        value["queries"][0]["ranked_ids"] = ["not-retrieved"]
    else:
        value["queries"][0]["judged_irrelevant_ids"] = value["queries"][0]["relevant_posting_ids"]
    with pytest.raises(ValidationError):
        AuditInput.model_validate(value)


def test_cli_is_deterministic_offline_and_rejects_duplicate_json_keys(tmp_path):
    script = Path(__file__).resolve().parents[1] / "scripts/audit_job_coverage.py"
    source = tmp_path / "audit.json"
    source.write_text(json.dumps(sample_input()))
    command = [sys.executable, str(script), str(source)]
    first = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10)
    second = subprocess.run(command, capture_output=True, text=True, check=True, timeout=10)
    assert first.stdout == second.stdout
    assert json.loads(first.stdout)["public_coverage_claim_authorized"] is False
    source.write_text('{"schema_version":1,"schema_version":1}')
    rejected = subprocess.run(command, capture_output=True, text=True, timeout=10)
    assert rejected.returncode == 2 and "invalid_audit_input" in rejected.stderr


def test_aggregate_success_cannot_hide_a_failed_critical_stratum():
    value = sample_input()
    declare_evidence(value)
    chosen = [row["employer_id"] for row in value["reference"]]
    good, bad = "IN-small-custom", "IN-large-workday"
    value["employers"] = [{"id": chosen[0], "stratum_id": good, "connector_status": "supported"},
                          {"id": chosen[1], "stratum_id": bad, "connector_status": "unsupported"}]
    value["strata"][0].update(sample_size=1, max_reference_postings=100)
    second = deepcopy(value["strata"][0])
    second.update(id=bad, size_band="large", ats_family="workday")
    value["strata"].append(second)
    value["scopes"][0].update(stratum_ids=[good, bad], critical_stratum_ids=[good, bad])
    template = value["reference"][0]["postings"][0]
    value["reference"][0]["postings"] = [{**template, "id": f"job-{i}", "canonical_key": f"opening-{i}",
                                         "canonical_destination": f"https://example.invalid/job-{i}"}
                                        for i in range(99)]
    captured = value["captured_postings"][0]
    value["captured_postings"] = [{**captured, "reference_posting_id": posting["id"],
                                   "canonical_key": posting["canonical_key"],
                                   "canonical_destination": posting["canonical_destination"]}
                                  for posting in value["reference"][0]["postings"]]
    value["queries"] = []
    result = audit(AuditInput.model_validate(value))["scopes"][0]
    assert result["overall"]["metrics"]["posting_recall"]["one_sided_lower_bound"] == 0.99
    assert result["strata"][bad]["metrics"]["posting_recall"]["one_sided_lower_bound"] == 0
    assert result["failed_critical_posting_strata"] == [bad]
    assert result["posting_gate"] == "target_not_demonstrated"
