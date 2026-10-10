# HireWiz admission and batch foundation — 8 October 2026

Status: **historical promoted admission foundation, verified within the scope below**.
Application release `7cc49c2c08a296b33cb30f96e383b959caf214c0` was promoted on GCP and
Vercel and is superseded by [release `ec52279`](PACING_AND_CATALOG_2026-10-08.md). This report
supersedes the current-release pointer in the [earlier pilot report](PRODUCTION_RELEASE_2026-10-08.md)
without replacing its incident history. The full [requirements register](REMAINING_REQUIREMENTS.md)
remains open. All recorded times are UTC.

## Immutable deployment identity

| Item | Recorded result |
| --- | --- |
| Exact-commit CI | [37801625745](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37801625745), all four jobs succeeded |
| Regional Cloud Build | `0655dc29-136f-4abd-9e18-a313327638d7`, SUCCESS at `2026-10-08T16:11:21.896813Z` |
| API and both worker image digest | `sha256:114be58f48e523cd22cae14e47a498556d1b4a76cdccd57ec4aa0220b5581afe` |
| Controlled migration | `hirewiz-schema-migration-f6645`, completed at `16:08:40.725016Z`; actual production head `20261008_0009` |
| API revision | `ai-resume-parser-00252-vor`, exact ready revision with 100% traffic |
| Analysis worker revision | `hirewiz-analysis-worker-00018-k66`, private; `analysis.run` scope |
| Employer worker revision | `hirewiz-employer-worker-00006-bj4`, private; employer search/refresh/apply/artifact-delete scope |
| Vercel deployment | `dpl_Ddh2JvT25wWFBFbQWiibfES9B37e`, READY, exact commit metadata and custom-domain alias verified |
| Public domain | `www.hirewizhq.com` resolves to that deployment; root-domain redirect remains configured |
| Automatic submission | Explicitly disabled on the API and both workers |

[Build metadata](evidence/2026-10-08-admission-cloudbuild.json),
[Vercel identity and alias metadata](evidence/2026-10-08-admission-vercel.json), and
[independent production checks](evidence/2026-10-08-admission-production-verification.json), and
[sanitized service/revision inventory](evidence/2026-10-08-admission-runtime-inventory.json)
retain the observed results. All three services use digest-pinned images, production
Cloud Tasks dispatch and explicit project/location settings. Anonymous worker requests
returned 403; authenticated health returned 200. The employer worker has no LLM,
payment or email credentials. No protection, DNS or Trusted Sources policy was broadened.
The alias endpoint proves the custom-domain mapping even though the deployment API's
alias array lists only its generic Vercel alias. The exact promotion timestamp was not recorded.

## Behavior and verification

The release adds fresh account-balance and Premium checks under the owner lock, durable
daily/rolling/pending admissions, canonical opening claims, immutable policy/price quotes,
atomic exact batches, and responsive review/status pages. Approval binds the file,
answers, consent, employer destination, actions and maximum credit budget. Queue acceptance
is distinct from an employer-confirmed application. Unknown sends retain their claim and
financial hold; cancellation never implies recall or permission to repeat a POST.

The default search price is **1 service credit per new qualifying delivered job** and
application price **5 service credits per verified complete automatic submission**.
Users choose 1–100 search results. The current paid pack is **₹499 for 500 service credits**;
Premium is **₹999 for 30 days**. Optional tailoring uses a separate analysis-unit quote.
Original/custom resume choices do not invoke tailoring.

Exact-commit CI passed **511 backend tests with no skips**, including **26 actual
PostgreSQL concurrency/migration cases**, **55 frontend unit tests**, **190 browser cases**,
**55 companion protocol tests** and **14 actual Chromium MV3 cases**. Ruff/Mypy,
six prompt evaluations, generated-contract drift checks, production build, dependency
audits, secret scanning and container build passed. The companion remains disabled,
with zero host grants and synthetic localhost tests only. Migration round-trips include
PostgreSQL and SQLite; the production upgrade is independently recorded above.

The [final public browser smoke](evidence/2026-10-08-admission-hosted-responsive.json)
passed **20/20 homepage, pricing, login and anonymous job-handoff cases** at 320, 390,
768, 1024 and 1440 px. Keyboard/tap navigation, 44 px mobile controls, focus, reduced
motion, pricing, overflow and accessibility smoke passed with zero runtime/console errors.
Visual review found no clipping. Two fresh public catalogs returned 200 in 1,669/366 ms.
Route-median LCP was 624/864/476/864 ms; maximum observed CLS was 0.092. These are
unthrottled local-network observations, **not field p75, INP, load or a complete accessibility audit**.
An authenticated catalog observation took 4.810 seconds; its cause and percentile are
unproved. The [bounded latency diagnosis](evidence/2026-10-08-admission-latency-diagnosis.json)
does not measure Cloud Run-to-database latency or authorize a topology change.

A [real Cloud Tasks public-feed refresh](evidence/2026-10-08-admission-production-refresh.json)
completed with one dispatch and one execution; the Razorpay source was healthy without
an error at `16:25:02.411087Z`. This refreshed the discovery index and audit state only.
The [bounded log scan](evidence/2026-10-08-admission-bounded-errors.json), from
`16:11:22Z` through `16:31:54.084355Z`, found zero ERROR-or-higher or HTTP-500-or-higher
entries across the API and both workers, without reaching its 1,000-entry limit.
No customer payment, application or employer submission was used for verification.

## Incidents and recovery

First build `db5774d3-8183-40a4-9fbc-35d56fb53251` built the image but failed at registry
upload with HTTP 503. Migration/deployment did not start, and the previous serving release
remained healthy. Retrying the same source commit and gates produced the successful build
above. [Incident metadata](evidence/2026-10-08-admission-upload-incident.json) preserves both attempts.

The first public smoke blocked the public NextAuth providers GET and interacted before
bootstrap finished, creating console artifacts and two mobile-menu timeouts. Correcting
only the harness's public GET allowlist/readiness checks produced a clean 20-case run
without retries. [Initial evidence](evidence/2026-10-08-admission-hosted-initial.json) is
retained separately. Fifteen aborted public route-prefetch GETs in the final run are
recorded as resource warnings; no document/script/style/font functional failure was observed.

## Backup and remaining limits

The [protected pre-release backup](evidence/2026-10-08-admission-protected-backup.json)
records schema `0008`, creation `15:33:35Z`, generation `1791473615226134`, 845,698 bytes
and SHA-256 `b36187c2d731fe04fa330e2c54a52b12bbee3c3ca8e51f78acdecbf1cd3923c7`.
Archive-format/catalogue checks passed. **It was not restored and proves no RPO/RTO.**
Outbound execution must remain stopped during any rollback that removes the new controls.

The reviewed live catalog remains **four employers**, with 356 open postings at the
recorded check, all in manual mode. The [16 reviewed proposals](PROPOSED_SOURCE_REVIEW_2026-10-08.md)
remain unenrolled. Research counts are not live availability or greater-than-95% recall.
Production device authority, independent recovery epochs/tombstones, restore quarantine,
tenant write grants, complete forms/receipts, historical identity remapping, hostile-file
isolation, fleet fairness, field performance and broader coverage remain open. The independent
recovery guard is a proposed design, not provisioned infrastructure. This release completes
the bounded admission foundation; it does not complete worldwide automatic applications.
