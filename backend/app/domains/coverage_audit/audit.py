"""Deterministic employer-cluster estimators, never a coverage certification.

No posting-level independence is assumed. The conservative Hoeffding union bound
requires SRS without replacement inside each complete employer-frame stratum and
an independently justified upper bound on cluster totals. See the protocol note.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

from .contracts import AuditInput, Scope, draw_sample, eligible


@dataclass(frozen=True)
class ClusterCell:
    population: int
    bound: int | None
    successes: tuple[int, ...]
    totals: tuple[int, ...]


def lower_bound(cells: list[ClusterCell], alpha: float = 0.05) -> dict[str, Any]:
    """At-least-(1-alpha) one-sided lower ratio bound conditional on the design.

    Bound recovered totals from below and reference totals from above, allocating
    alpha/(2H) to each of H strata and each tail. A census has no sampling error.
    """
    if not cells or not math.isfinite(alpha) or not 0 < alpha < 1:
        raise ValueError("Nonempty cells and a finite tail probability in (0,1) are required")
    issues: list[str] = []
    recovered = denominator = lower = upper = 0.0
    for cell in cells:
        n = len(cell.totals)
        if (type(cell.population) is not int or cell.population <= 0
                or len(cell.successes) != n or n > cell.population
                or (cell.bound is not None and (type(cell.bound) is not int or cell.bound <= 0))):
            raise ValueError("Invalid cluster design/counts")
        for hit, total in zip(cell.successes, cell.totals, strict=True):
            if (type(hit) is not int or type(total) is not int or not 0 <= hit <= total
                    or (cell.bound is not None and total > cell.bound)):
                raise ValueError("Cluster successes/totals exceed their exact declared range")
        if n == 0:
            issues.append("unsampled_stratum")
            continue
        weight = cell.population / n
        recovered += weight * sum(cell.successes)
        denominator += weight * sum(cell.totals)
        if n == cell.population:
            lower += sum(cell.successes)
            upper += sum(cell.totals)
        elif cell.bound is None:
            issues.append("missing_independent_cluster_bound")
        else:
            margin = cell.bound * math.sqrt(math.log(2 * len(cells) / alpha) / (2 * n))
            lower += cell.population * max(0.0, sum(cell.successes) / n - margin)
            upper += cell.population * min(cell.bound, sum(cell.totals) / n + margin)
    if denominator == 0:
        issues.append("empty_denominator")
    return {
        "estimate": recovered / denominator if denominator else None,
        "one_sided_lower_bound": lower / upper if not issues and upper else None,
        "confidence_at_least": 1 - alpha,
        "weighted_recovered_total": recovered,
        "weighted_reference_total": denominator,
        "issues": sorted(set(issues)),
    }


def _provenance_issues(data: AuditInput, query: bool = False) -> list[str]:
    p = data.provenance
    checks = {
        "synthetic_evidence_only": not p.synthetic,
        "nonprobability_or_unknown_draw": p.sampling_method == "stratified_srs_without_replacement",
        "independent_frame_not_declared": p.frame_independent_of_registry and bool(p.frame_evidence_ref),
        "preregistered_draw_not_declared": p.draw_frozen_before_collection and bool(p.draw_evidence_ref),
        "reference_not_frozen_before_results": p.reference_frozen_before_results and bool(p.reference_evidence_ref),
        "independent_human_reference_not_declared": p.independent_human_review_declared and bool(p.human_review_evidence_ref),
        "whole_employer_holdout_not_declared": p.whole_employer_holdout_declared and bool(p.holdout_evidence_ref),
    }
    if query:
        checks["independent_queries_not_declared"] = (
            p.independent_queries_declared and bool(p.query_collection_evidence_ref)
        )
    return [name for name, passed in checks.items() if not passed]


def audit(data: AuditInput) -> dict[str, Any]:
    selected = draw_sample(data.employers, data.strata, data.seed)
    frames = {row.id: row for row in data.employers}
    strata = {row.id: row for row in data.strata}
    references = {row.employer_id: row for row in data.reference}
    captures = {row.reference_posting_id: row for row in data.captured_postings}
    employer_captures = {row.employer_id: row for row in data.captured_employers}
    # All scope and stratum cells and all three recall metrics share a 5% error budget.
    cell_count = sum(1 + len(scope.stratum_ids) for scope in data.scopes)
    alpha = 0.05 / (cell_count * 3)

    def report_cell(scope: Scope, cell_ids: list[str]) -> dict[str, Any]:
        identities = [identity for cell_id in sorted(cell_ids) for identity in selected[cell_id]]
        queries = [row for row in data.queries if row.scope_id == scope.id]
        postings = {posting.id: (identity, posting) for identity in identities
                    if identity in references for posting in references[identity].postings
                    if eligible(posting, scope, data.window_start, data.window_end)}
        hit_ids = set()
        for identity, (_, posting) in postings.items():
            captured = captures.get(identity)
            if (captured is not None and captured.observed_at == data.observation_at
                    and captured.destination_verified and posting.destination_verified
                    and captured.canonical_key == posting.canonical_key
                    and captured.canonical_destination == posting.canonical_destination):
                hit_ids.add(identity)
        common = _provenance_issues(data)
        reference_issues = []
        for identity in identities:
            row = references.get(identity)
            if row is None:
                reference_issues.append("missing_sampled_employer_enumeration")
            elif (not row.enumeration_complete or not row.human_reviewed
                  or not row.authoritative_snapshot_ref or not row.independent_review_ref):
                reference_issues.append("incomplete_or_unreviewed_enumeration")
            if row is not None and row.observed_at != data.observation_at:
                reference_issues.append("reference_observation_mismatch")
        uncertain = [identity for identity, (_, posting) in postings.items()
                     if posting.original_date_status != "known" or posting.status == "uncertain"]
        if uncertain:
            reference_issues.append("unresolved_original_date_or_active_status")
        if any(not posting.destination_verified for _, posting in postings.values()):
            reference_issues.append("unverified_reference_employer_destination")

        metrics: dict[str, Any] = {}
        for metric in ("employer_discovery", "posting_recall", "query_retrieval_recall"):
            design = []
            for cell_id in sorted(cell_ids):
                members = selected[cell_id]
                successes, totals = [], []
                for identity in members:
                    member_posts = {key for key, (owner, _) in postings.items() if owner == identity}
                    if metric == "employer_discovery":
                        captured_owner = employer_captures.get(identity)
                        successes.append(int(captured_owner is not None and captured_owner.origin_verified
                                             and captured_owner.observed_at == data.observation_at))
                        totals.append(1)
                    elif metric == "posting_recall":
                        successes.append(len(member_posts & hit_ids))
                        totals.append(len(member_posts))
                    else:
                        successes.append(sum(len(member_posts & hit_ids & set(query.relevant_posting_ids)
                                                 & set(query.retrieved_ids)) for query in queries))
                        totals.append(sum(len(member_posts & set(query.relevant_posting_ids))
                                          for query in queries))
                bound = strata[cell_id].max_reference_postings
                if metric == "employer_discovery":
                    bound = 1
                elif metric == "query_retrieval_recall" and bound is not None:
                    bound *= len(queries)
                    if bound == 0:
                        bound = None
                design.append(ClusterCell(sum(row.stratum_id == cell_id for row in data.employers),
                                          bound, tuple(successes), tuple(totals)))
            result = lower_bound(design, alpha)
            issues = common + result["issues"]
            if metric != "employer_discovery":
                issues += reference_issues
            if metric == "query_retrieval_recall":
                issues += _provenance_issues(data, query=True)
                if not queries:
                    issues.append("no_independent_queries")
                if any(not query.relevant_reference_complete or not query.independent_judgment_ref
                       for query in queries):
                    issues.append("incomplete_query_relevance_reference")
                if any(query.observed_at != data.observation_at for query in queries):
                    issues.append("query_observation_mismatch")
            result["diagnostic_estimate_under_declared_reference"] = result["estimate"]
            result["diagnostic_lower_bound_under_declared_design"] = result["one_sided_lower_bound"]
            if issues:
                result["estimate"] = None
                result["one_sided_lower_bound"] = None
            result["issues"] = sorted(set(issues))
            result["status"] = ("insufficient_evidence" if issues else
                                "statistical_threshold_passed_pending_external_review"
                                if result["one_sided_lower_bound"] > 0.95 else "target_not_demonstrated")
            metrics[metric] = result

        sample_weights = [{"stratum_id": identity,
                           "frame_employers": sum(row.stratum_id == identity for row in data.employers),
                           "sampled_employers": len(selected[identity]),
                           "weight": sum(row.stratum_id == identity for row in data.employers)
                           / len(selected[identity]) if selected[identity] else None,
                           "dimensions": {key: getattr(strata[identity], key) for key in
                                          ("country", "sector", "size_band", "ats_family")}}
                          for identity in sorted(cell_ids)]
        return {
            "weights": sample_weights,
            "sampled_employers": len(identities),
            "unsupported_sampled_employers": sum(frames[key].connector_status == "unsupported"
                                                for key in identities),
            "reference_candidate_postings": len(postings),
            "captured_verified_reference_postings": len(hit_ids),
            "missed_posting_ids": sorted(set(postings) - hit_ids),
            "original_date_or_status_uncertain_ids": sorted(uncertain),
            "captured_original_date_unknown": sum(key in captures and captures[key].original_published_at is None
                                                  for key in postings),
            "captured_original_date_mismatch": sum(key in captures and posting.original_published_at is not None
                                                   and captures[key].original_published_at != posting.original_published_at
                                                   for key, (_, posting) in postings.items()),
            "capture_observation_mismatches": sum(key in captures and captures[key].observed_at != data.observation_at
                                                 for key in postings),
            "withdrawn_reference_postings": sum(posting.status == "withdrawn" for identity in identities
                                                if identity in references for posting in references[identity].postings),
            "metrics": metrics,
        }

    scopes = []
    for scope in sorted(data.scopes, key=lambda row: row.id):
        overall = report_cell(scope, scope.stratum_ids)
        cell_reports = {identity: report_cell(scope, [identity]) for identity in sorted(scope.stratum_ids)}
        critical_failed = [identity for identity in scope.critical_stratum_ids
                           if cell_reports[identity]["metrics"]["posting_recall"]["status"]
                           != "statistical_threshold_passed_pending_external_review"]
        critical_insufficient = any(cell_reports[identity]["metrics"]["posting_recall"]["issues"]
                                    for identity in scope.critical_stratum_ids)
        usefulness = []
        for query in sorted((row for row in data.queries if row.scope_id == scope.id), key=lambda row: row.id):
            relevant = set(query.relevant_posting_ids)
            unknown = set(query.ranked_ids) - relevant - set(query.judged_irrelevant_ids)
            hits = len(set(query.ranked_ids) & relevant)
            usefulness.append({"query_id": query.id, "top_k": query.top_k,
                               "returned_count": len(query.ranked_ids), "judged_relevant_top_k": hits,
                               "unjudged_top_k_ids": sorted(unknown),
                               "precision_at_k_lower": hits / query.top_k,
                               "precision_at_k_upper": (hits + len(unknown)) / query.top_k,
                               "inference": "descriptive_for_fixed_queries_only"})
        scopes.append({"scope_id": scope.id, "definition": scope.model_dump(),
                       "overall": overall, "strata": cell_reports,
                       "failed_critical_posting_strata": sorted(critical_failed),
                       "posting_gate": "insufficient_evidence" if critical_insufficient else
                       "target_not_demonstrated" if critical_failed else
                       overall["metrics"]["posting_recall"]["status"],
                       "top_k_usefulness": usefulness})
    encoded = json.dumps(data.model_dump(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return {"report_version": 1, "audit_id": data.audit_id,
            "input_sha256": hashlib.sha256(encoded).hexdigest(),
            "observation_at": data.observation_at,
            "method": "stratified_employer_cluster_hoeffding_ratio_union_bound",
            "family_confidence_at_least": 0.95,
            "interval_error_allocation": alpha,
            "selected_employer_ids": selected,
            "independent_human_reference_established": False,
            "evidence_verification": "input_attestations_unverified_by_this_offline_tool",
            "public_coverage_claim_authorized": False,
            "automatic_submission_coverage_measured": False,
            "scopes": scopes}
