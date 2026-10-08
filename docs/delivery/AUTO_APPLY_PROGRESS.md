# Employer search and application delivery

Development and deployment authorized on 8 October 2026. The full requirements remain
in [the acceptance register](../requirements/JOB_SEARCH_AND_APPLICATION.md). This file
tracks implementation evidence and does not replace the wider worldwide coverage target.

The [first launch path](INITIAL_BROWSER_ASSISTANCE_2026-10-08.md) is candidate-reviewed
browser assistance, selected on 8 October. Final submission remains candidate-controlled.

## Implementation checkpoints

- [x] Verify latest remote baseline d5c2ea8 and existing GCP/Vercel identities.
- [x] Add independent paid service balance and captured-payment pack fulfillment.
- [x] Verify Premium purchases, capture replay, proportional refunds and refunded-spend debt:
  `backend/tests/test_razorpay_billing.py` (21 tests passed locally on 8 October).
- [x] Add transactional dispatch with durable pending state, lease claims and bounded recovery.
- [x] Verify rollback, queue outage recovery, duplicate delivery, publisher/worker race,
  live leases and no candidate content in task payloads: `test_dispatch_outbox.py` (9 passed).
- [x] Implement deterministic employer-index search, configured result counts and separate prices.
- [x] Implement original/custom/approved-tailored sealed packages, supported forms and exact approval binding.
- [x] Verify cancellation, possible-sent recovery and confirmed-only settlement with synthetic fixtures.
- [x] Verify four live public employer feeds and seed audited employer origins; this closes only the pilot seed scope.
- [ ] Expand connector catalog and independently measure declared-scope recall.
- [ ] Complete permitted browser companion, device claims and human checkpoints.
- [x] Integrate the disabled Python recovery core with 85 focused local cases and the
  complete 630-test backend suite. Production authority, pairing and multi-field steps remain open.
- [ ] Verify all mandatory AI-disabled fresh-user workflows and optional enhanced modes.
- [x] Verify connected responsive flows at all five declared widths with local production-build evidence.
- [x] Validate migration round-trip through `20261008_0008` on PostgreSQL 17 and regenerate API contracts.
- [x] Complete pilot private queues/workers, controlled migration job and scheduled recovery.
- [x] Run full pilot CI, security/container checks and staging end-to-end tests.
- [x] Stage and promote the tested initial production backend and frontend release.
- [x] Confirm required pilot environment, worker IAM, Scheduler and production health after repairs.
- [x] Monitor the initial release and record deployment IDs, commit and observations.
- [x] Promote follow-up configuration, pricing-timeout, mobile-control and dispatch recovery hardening; record bounded post-rollout observations.
- [x] Promote admission/batch and locked-balance foundation `7cc49c2`, actual schema `0009`,
  private workers and exact Vercel alias; verify exact-commit CI, production refresh and
  20 public route/width cases. Full browser-executor and recovery acceptance remains open.
- [x] Promote pacing/catalog follow-up `ec52279`; verify 545 backend tests with no skips,
  exact GCP/Vercel identities, private workers, a completed public refresh, bounded logs
  and six public mobile/desktop cases. Lever remains unenrolled pending runtime coordination.
- [x] Promote frontend-only `7c59578` after all four exact-commit CI jobs passed (630 backend
  tests, no skips); verify the custom alias and homepage at 320/390/1440 px. GCP remains
  `ec52279`; the disabled recovery core is committed source, not serving production code.
- [ ] Complete sustained field-performance, load and wider operational recovery/security validation.
- [ ] Complete immutable GCS storage and deletion/restore/security drills from the register.
- [ ] Prove every requirements gate before marking the full development goal complete.

## Verification before the first pilot release

On 8 October 2026 the whole backend suite passed **418 tests**, modernized backend Ruff
passed, Mypy passed for **46 files**, and all six prompt evaluation cases passed. The
PostgreSQL 17 sequence upgraded to head, downgraded to `20260803_0001`, then upgraded
to head again. Public feed adapters include Greenhouse, Lever, Ashby, SmartRecruiters,
Workable, Personio and Pinpoint; fixture proof does not enroll unreviewed employers.
A live unauthenticated Razorpay feed read returned 29 openings, all from the verified tenant.

Frontend verification covers search quotes, original/custom/tailored choices, exact preview,
required answers, approvals, manual/unknown states, cancellation and account-switch
isolation. BFF and Google-consent mutation guards passed real built-server compatibility
checks, and tests prove rejected financial/application mutations cannot reach the backend.
The employer flow passed 65 browser cases at 320, 390, 768, 1024 and 1440 pixels;
the complete frontend suite passed 155 cases across those widths.

Initial release `7980b624d2fd5a93994621538eb610e2ee4e5181` was promoted on GCP and
Vercel. Follow-up release `3565655712c2ceee431bee8306678a020b2f462d` was subsequently
promoted, then superseded by the admission foundation `7cc49c2` described below.
All four reviewed production sources completed scans and held 356 open postings at the
recorded check. The Scheduler and actual Cloud Tasks executions were observed after
repairing missing legacy publisher configuration. Exact release identities, independent
hosted browser evidence and rollout findings are recorded in
[the production release report](PRODUCTION_RELEASE_2026-10-08.md).

[Thirty synthetic local measurements](evidence/2026-10-08-responsive-lab.json) observed
LCP 72–264 ms and CLS at most 0.0133 after layout stabilization. They use mocked backend
responses without CPU/network throttling and **do not prove field p75 performance**.
Consent-gated Core Web Vitals events collect LCP, INP and CLS with static route/device/
connection groups. Payloads exclude raw route IDs, queries, answers and resume content;
withdrawn consent stops events. Use the latest sample per metric ID when calculating p75
and report sample counts and consent-selection bias. The existing GA measurement-ID
environment name is now recognized. Real-user targets remain unverified until there is
enough production data. Heavy chart loading, route loading states and owner-bound caches
are implemented; the API release keeps one service-level minimum instance for idle latency.

The full acceptance register remains open where there is no independent proof. In
particular, production companion authority, full daily/batch acceptance, broader scan/catalog operations,
permissioned live receipts, independent recovery epochs/tombstones, security/isolation
drills and greater-than-95-percent recall are tracked in
[the remaining requirements register](REMAINING_REQUIREMENTS.md). Automatic submission
must remain disabled until the relevant tenant and safety gates pass.

## Promoted follow-up evidence

Exact-commit [CI 37789610598](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37789610598)
passed all three jobs with **455 backend tests**, **45 frontend unit tests** and **155
browser cases**. Forty-six focused dispatch cases and
[seven independent real-PostgreSQL scenarios](evidence/2026-10-08-followup-dispatch-postgres.json)
cover configuration rejection, uncertain publish recovery, fresh task generations,
publisher/worker races and bounded actual-failure attempts. The PostgreSQL drill used a
synthetic task client and no live employer requests.

The protected backup and exact old-serving-image preparation succeeded before migration
`hirewiz-schema-migration-tsf5n`. The controlled migration remained at head `0008`.
Cloud Build `664bcd48-d110-40bb-b2f9-1583bad245c9` succeeded, API revision
`ai-resume-parser-00247-val` received 100% traffic, both private workers were updated and
Vercel deployment `dpl_HAp5ndXqK92w6rAURv57qF128yoF` was promoted to the custom domain.
[Release metadata](evidence/2026-10-08-followup-release.json) records exact times and the
immutable image digest. Backup creation and archive checks do not prove a restore drill.

[Hosted follow-up browser evidence](evidence/2026-10-08-followup-hosted-responsive.json)
passed 20 route/width cases. The mobile/tablet menu controls measured 44 × 44 px, both
pricing cards were current at all widths, and no unresolved runtime, console, overflow or
automated accessibility-smoke failure remained. Hosted-lab route-median LCP ranged from
560 to 1,100 ms; maximum observed CLS was 0.029. These are unthrottled lab observations,
not field p75/p95, INP, load or a complete accessibility audit. Two fresh public catalog
GETs returned 200 in 738 and 361 ms. Two authenticated catalog observations took 4.902
and 1.427 s; this limited sample does not establish a percentile or timing cause.

A [real post-promotion public-source refresh](evidence/2026-10-08-followup-production-refresh.json)
completed with one dispatch and one execution and source status healthy at 14:39:32.516Z.
The bounded API/worker log observation found no ERROR-or-higher or HTTP-500-or-higher
entries between 14:26:17Z and approximately 14:37Z. No live payment or employer
application was performed. Automatic submission remains disabled.

The [16 additional source proposals](../../backend/resources/employer_sources.proposed_20261008.json)
remain `proposed_not_enrolled`; their 969 research-time openings are not production
availability or coverage claims. The subsequent admission/batch and disabled-companion
foundation is now deployed as recorded below. Device/independent authority,
permitted submission, wider recovery/security and worldwide recall gates remain open.

## Promoted admission foundation

[Release `7cc49c2`](PRODUCTION_ADMISSION_RELEASE_2026-10-08.md) passed exact-commit CI
with **511 backend tests (26 actual PostgreSQL cases, no skips), 55 frontend unit tests,
190 browser cases, 55 companion protocol tests and 14 actual Chromium MV3 cases**.
The controlled production migration reached `20261008_0009`. All three GCP services use
the same pinned digest; exact API/worker health, privacy/topic scopes and the promoted
Vercel custom-domain alias passed independent checks. A public-source refresh completed
through Cloud Tasks once, and bounded post-rollout API/worker logs contained no matching
ERROR/HTTP-500 entries. Twenty hosted public cases passed across all five declared widths.
These checks prove the bounded rollout, not field p75, sustained load or real applications.

The new controls refresh financial/analysis balances under the owner lock, bind immutable
policy/price/opening quotes, enforce daily/rolling/pending limits, and reserve an exact
batch atomically with its credit holds and outbox events. Unknown sends retain their claim;
cancelled batches cannot be revived from cached ORM state. The UI separates exact review,
approval and queue acceptance and exposes per-job outcomes. The companion package is
disabled and uses localhost fixtures only. All four live sources remain manual-only, and
automatic submission remains explicitly disabled. No test payment or employer send was used.

The [independent proposal review](PROPOSED_SOURCE_REVIEW_2026-10-08.md) keeps all 16
additional sources unenrolled. Shared Lever host pacing, source-use review and operational
evidence are required before enrolling its proposed tenants. Independent recovery/device
authority, permitted receipts, restore quarantine, field metrics and recall remain open.

## Promoted pacing and catalog follow-up

[Release `ec52279`](PACING_AND_CATALOG_2026-10-08.md) passed all four exact-commit CI jobs
with **545 backend tests, no skips**, including 30 host-pacing cases (eight actual Redis),
26 actual PostgreSQL cases and four advisory catalog ownership/query cases. It removes
one redundant authenticated catalog SELECT while preserving locked financial refreshes.
Lever discovery acquires a shared host permit for each page/retry and rejects incompatible
coordination; no production Redis or new Lever source was added.

The controlled rollout retained schema `0009`. All three ready services share the exact
build digest, workers remain private, and the frontend custom-domain alias is verified.
The normal public-source refresh completed once; a bounded log scan found no matching
ERROR/HTTP-500 entries. Six public mobile/desktop cases passed. Frontend and companion
code match the previous five-width release. The report retains the 2.82-second pricing
LCP sample and the 4.442-second authenticated catalog sample without claiming causal or
field performance improvement. Fresh backup creation is not restore evidence.

## Promoted browser-first frontend follow-up

[Frontend `7c59578`](BROWSER_FIRST_FOUNDATION_2026-10-08.md) passed all four exact-commit
CI jobs with **630 backend tests without skips**, 55 frontend units, 190 browser cases,
55 companion protocol cases and 14 Chromium MV3 cases. Its exact Vercel custom alias
was verified after promotion. The narrow homepage counter fix passed hosted checks at
320/390/1440 px and all nine screenshots passed visual review. GCP remains `ec52279`;
no SQL, backend or environment rollout occurred. The new recovery modules remain inactive
source outside that serving image. Browser assistance, production authority and final
automatic submission remain unavailable pending their full acceptance gates.

## Commercial defaults

Initial search price: 1 paid service credit per new qualifying delivered job.
Initial application price: 5 paid service credits per verified complete automatic submission.
Candidate requested result count: 1–100, bounded by server configuration.
Prepaid pack: 500 paid service credits for INR 499, one-time existing Razorpay checkout.
Optional tailoring: separately quoted existing analysis units. Original/custom file selection
does not invoke tailoring. Premium does not waive employer service pricing.

## External capabilities requiring evidence

Public ATS job feeds establish read access. Employer or partner write credentials, tenant
grants, current form parity and complete receipt contracts establish automatic API capability.
Keep automatic submission unavailable for tenants without these proofs. The browser route
must be tested and permitted per portal. A manual handoff is not completed auto-apply.
Discovery coverage and automatic application coverage are reported independently.
