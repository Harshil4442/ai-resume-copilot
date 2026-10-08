# Independent coverage audit harness — 9 October 2026

This additive offline tool implements input validation and conservative statistical
reporting for CV01–05, G02C and G06A–B. It does **not** establish an independent human
reference, verify evidence files, enlarge the live catalog, measure automatic submission,
authorize an employer, or demonstrate worldwide or greater-than-95% recall. The existing
[remaining requirements](REMAINING_REQUIREMENTS.md) remain open. The checked-in example
and focused tests contain synthetic public-shape metadata only.

## Reproduce locally

From `backend`, use the existing Python 3.12 environment and lockfile:

```sh
python scripts/audit_job_coverage.py --schema > /tmp/coverage-input-schema.json
python scripts/audit_job_coverage.py tests/fixtures/coverage_audit_example.json --draw-only
python scripts/audit_job_coverage.py tests/fixtures/coverage_audit_example.json --output /tmp/coverage-report.json
pytest -q tests/test_job_coverage_audit.py
ruff check app/domains/coverage_audit scripts/audit_job_coverage.py tests/test_job_coverage_audit.py
mypy app/domains/coverage_audit scripts/audit_job_coverage.py tests/test_job_coverage_audit.py
```

The script reads only its local input and writes JSON locally or to stdout. It imports
neither application authentication/database configuration nor any provider client. No
network, credentials, AI calls, enrollment or candidate resumes/preferences are needed.
No dependency, shipping configuration, route, migration or existing requirement file
changes are included. Invalid inputs return exit code 2; valid evidence reports return 0
even when their result is `insufficient_evidence`. Repeated identical inputs give identical
JSON and a canonical input SHA-256. Duplicate JSON keys and nonfinite numbers are rejected.

## Freeze the reference before inspecting HireWiz output

1. Approve a specific eligible employer population, countries, posting locations,
   languages, role families, public-vacancy policy, original-date window, observation
   instant and independently collected query criteria. Document private/inaccessible
   classes separately. Do not claim a population broader than this frame. Employer-frame
   geography and posting location are distinct fields. Eligibility policy must precede
   connector support decisions; public eligible custom/unsupported portals and outages
   stay in the frame and denominator.
2. Construct the full eligible employer frame independently of the source registry,
   with one canonical employer ID and one mutually exclusive country/sector/size/ATS
   stratum per employer. Identify ownership groups/tenants that should be one cluster
   before drawing; treating related subsidiaries as independent needs review. Store
   evidence for frame provenance/completeness, including unsupported and unknown ATSs.
3. Freeze each stratum's sample size and any justified upper bound on **all reference
   postings per employer**, before enumerating vacancies or seeing results. A bound is
   not a collection cap. An employer exceeding it invalidates the input; include every
   posting and review the design instead of truncating. An absent bound is valid input
   but blocks noncensus posting/query inference. A bound justified only by the observed
   sample maximum is insufficient. Very loose bounds give very conservative intervals.
4. Record an independently generated uniform seed and an independently reviewed SRS
   without-replacement design. The helper hash-ranks employer IDs within each stratum,
   making the draw reproducible; this pseudorandom procedure and a public seed are **not
   proof** of a probability sample or preregistration. Retain the actual draw and review
   its selection law/seed provenance externally. Never search seeds, substitute a missed
   employer, choose the draw from indexed tenants, or change strata after viewing misses.
   Unsupported designs, convenience samples and unverifiable provenance stay descriptive.
5. Keep every selected employer completely outside tuning/development until evaluation
   is sealed. List development employer IDs explicitly; any intersection with the draw
   is rejected. Split whole employers, never postings within one employer. Known/unknown
   connector status does not affect the draw. Reviewing/retraining on a held-out miss
   requires a fresh untouched employer evaluation; do not recycle the old holdout.
6. Independently enumerate each drawn employer's public vacancies at the observation
   instant through authoritative feed/portal evidence **and separate human review**.
   Record a row even for an independently confirmed empty enumeration. Missing rows,
   partial scans, unresolved pagination, access failures or unreviewed rows cannot be
   interpreted as zero vacancies. Preserve unique canonical openings/destinations,
   active/withdrawn/uncertain status and original-date evidence. Do not use HireWiz's
   captured list to construct the reference.
7. Keep original publication, republication/unknown date, first seen and last origin
   check separate. Unknown original dates and uncertain activity remain candidate
   denominator entries and block eligible-date inference until adjudicated; they are
   never silently excluded or assigned today's date. Withdrawn postings are counted
   separately; known original dates outside the frozen window are excluded from the
   eligible posting denominator. Time-mismatched reference snapshots
   block inference; time-mismatched captures count as misses at the declared instant.
8. Freeze independently collected queries and relevant-job judgments for each scope.
   Judge against the full eligible reference, including unindexed jobs, before examining
   result rankings. Preserve pre-ranking/filter retrieval IDs separately from top-K.
   Independently judge every top-K item, including out-of-reference/irrelevant items;
   retain unjudged items as uncertainty. Preserve a view-all route separately; this
   harness does not add or test that production UI route.

The independent review/provenance fields are input **declarations**, not an attestation
issued by the program. Evidence references are opaque IDs; the tool never opens them.
Human sampling/reference audit, population-frame completeness and independent review
remain external acceptance work. Every report hard-codes
`independent_human_reference_established=false` and `public_coverage_claim_authorized=false`.

## Input schema and denominator contracts

`--schema` emits the reproducible Pydantic JSON Schema. All fields are required unless
the schema explicitly supplies a default; types are strict and extra fields fail.
Timestamp fields use exact UTC seconds. IDs/evidence labels use bounded ASCII strings;
descriptive scope text allows Unicode scalar strings without normalization.

| Input | Meaning |
| --- | --- |
| `strata`, `employers` | Full frozen eligible frame; country/sector/size/ATS cells and drawn employer counts. Population counts and weights are derived, never supplied arbitrary weights. |
| `scopes` | Whole-stratum employer populations plus posting country/language/role/date criteria, policy/query references and explicitly declared critical launch strata. Every cell is reported, even if it was not named critical. |
| `provenance`, `seed`, `development_employer_ids` | Independent design/reference/query/holdout declarations and evidence IDs. Synthetic or missing declarations block inferential output. |
| `reference` | Independent employer enumeration, same observation time, completeness/human-review declarations and canonical postings, including unsupported and uncertain vacancies. |
| `captured_employers`, `captured_postings` | Same-time system matches. A posting hit requires both verified destinations and exact canonical key/destination agreement. Canonicalization failures/truncation/missing captures count as misses. |
| `queries` | Independent relevant-reference IDs, before-ranking retrieval, top-K ranked results and independent irrelevant judgments. Duplicate IDs, contradictory judgments and top-K outside retrieval are rejected. |

`employer_discovery` includes every drawn eligible employer, including confirmed zero-job
employers. Posting recall is a ratio of weighted recovered/reference **postings**, not the
mean of employer percentages. Query retrieval recall uses relevant query/posting pairs,
clustered by employer, conditional on the fixed independently collected query set.
Top-K usefulness is descriptive precision-at-K: known relevant/K to
(known relevant + unjudged)/K. Missing top-K slots do not earn relevance credit. This is
separate from both posting recall and query retrieval recall and does not establish
generalization to an independently sampled population of future candidate queries.
No preparation, submission-availability or confirmed-execution rate is inferred.

## One-sided confidence bound

The finite-population Hoeffding inequality applies to uniform draws without replacement;
this avoids assuming independent postings inside an employer. The current primary paper
states the bound in Proposition 1.2. The method here deliberately uses that conservative
range bound rather than estimating variance from a small zero-miss sample.
[Bardenet and Maillard, latest author version, Proposition 1.2](https://arxiv.org/html/1309.4029v2).

For each stratum h, let N_h be its full frame employer count, n_h its drawn employer
count, B_h a justified posting-count bound, Y_hi verified recovered postings and X_hi
eligible reference postings. The point estimator is
`sum_h (N_h/n_h)*sum_i Y_hi / sum_h (N_h/n_h)*sum_i X_hi`.
Each employer is one cluster; within-employer posting correlation is unrestricted.

For a metric covering H strata with tail budget a, use
`e_h = B_h * sqrt(log(2H/a)/(2n_h))`.
Set `L_Y = sum_h N_h*max(0, mean(Y_h)-e_h)` and
`U_X = sum_h N_h*min(B_h, mean(X_h)+e_h)`; the lower ratio bound is `L_Y/U_X`.
Apply the lower-tail inequality to Y and the upper-tail inequality to X, then a union
bound over 2H events. Census cells use their exact totals with no sampling margin.
Employer discovery has B_h=1; query/posting-pair totals have
B_h=`posting bound * number of fixed queries`. Positive population denominator,
complete enumeration and the declared sampling/range assumptions are required.

The implementation allocates 0.05 across all reported scope/stratum cells and all three
recall metrics, then across each cell's 2H tails. Thus the reported lower bounds have
**at least 95% simultaneous coverage conditional on valid design/reference inputs**;
individual `confidence_at_least` values are stronger than 95%. Overlapping scopes do
not require independence for this union bound. No finite-population improvement is
used except the census case. The interval may be wide or zero even at observed 100%.
Empty denominators, missing strata or absent noncensus bounds return no usable bound.

NCES requires analysis/uncertainty methods that account for the actual sample design,
including stratification and clustering; a posting-level binomial interval is not
a substitute. [Current NCES Statistical Standards, Chapter 5](https://nces.ed.gov/statprog/2012/pdf/Chapter5.pdf).
Primary sources were checked on 9 October 2026; no secondary statistical source is used.

## Interpreting outcomes and limits

- `insufficient_evidence`: missing design/reference/human/query declarations, incomplete
  enumeration, unresolved eligibility/date/activity, unsampled strata, missing bounds
  or an empty denominator. Inferential `estimate`/`one_sided_lower_bound` are null.
  Explicitly named diagnostic values describe only the supplied partial/declared data.
- `target_not_demonstrated`: eligible declared evidence exists but the lower bound is
  at or below 0.95. The threshold is strictly greater than 0.95, not greater-or-equal.
- `statistical_threshold_passed_pending_external_review`: the supplied data satisfy the
  statistical threshold under their declarations. This is not an independent-reference
  certificate or a public coverage claim. Failed critical posting strata block the
  scope's posting gate even when its aggregate bound passes.

Frozen single-time snapshots cannot prove six-hour freshness, outage reliability,
complete scan closure, worldwide frame completeness, future coverage or actual
submission success. Weighting cannot repair an omitted employer population, falsified
canonical destination, nonresponse or post-hoc sample selection. Keep evidence snapshots
immutable, rerun at declared times and seek independent review before any public claim.
