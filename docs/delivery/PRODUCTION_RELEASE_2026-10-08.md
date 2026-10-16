# HireWiz pilot release — 8 October 2026

Status: historical initial pilot and follow-up hardening release record. Current release
`ec52279` and schema `0009` are recorded in [the pacing and catalog release](PACING_AND_CATALOG_2026-10-08.md).
The earlier follow-up commit was `3565655712c2ceee431bee8306678a020b2f462d`. This report preserves the initial
rollout history and records observed results; it does not close the full
[requirements register](../requirements/JOB_SEARCH_AND_APPLICATION.md).

## Promoted follow-up identity

| Item | Recorded identity / result |
| --- | --- |
| Application commit | `3565655712c2ceee431bee8306678a020b2f462d` |
| Exact-commit CI | [37789610598](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37789610598), all three jobs succeeded |
| Regional Cloud Build | `664bcd48-d110-40bb-b2f9-1583bad245c9`, SUCCESS at `2026-10-08T14:26:16.401Z` |
| Backend image digest | `sha256:7cbb1bd8c78ffc0266e8b6796397102ec3877c7673624dd53677d4083694b924` |
| Controlled migration | `hirewiz-schema-migration-tsf5n`, succeeded at `2026-10-08T14:24:31.190Z`; production head remains `20261008_0008` |
| Promoted API | `ai-resume-parser-00247-val`, 100% traffic |
| Promoted analysis worker | `hirewiz-analysis-worker-00017-tk5` |
| Promoted employer worker | `hirewiz-employer-worker-00004-bbc` |
| Promoted frontend | `dpl_HAp5ndXqK92w6rAURv57qF128yoF`, Ready and promoted to the custom domain; Next.js 16.4.0 |
| Automatic submission | Disabled |

The [follow-up rollout record](evidence/2026-10-08-followup-release.json) includes the
protected backup metadata, migration and deployment identities, bounded log observation
and read-only catalog timings. The protected pre-migration backup was created at
`2026-10-08T13:49:00Z`, generation `1791467340797255`, with 843,805 bytes and SHA-256
`0645174330c7e84911df46dcb42992539ac32334ec6f12006f38d5aef8238168`.
Archive-format and catalogue checks passed; restore verification remains false.

## Initial pilot identity (historical)

| Item | Recorded identity / result |
| --- | --- |
| Application commit | `7980b624d2fd5a93994621538eb610e2ee4e5181` |
| Git tree | `96b4aa7ee0092c93087f4bb70ff5e0e54ff81353` |
| Implementation commit | `8c246dcad0cc20eacf48f266a5ec85fcb8a05074`; identical tree |
| Pull request | [#21](https://github.com/Harshil4442/ai-resume-copilot/pull/21) |
| Exact-commit CI | [37777386850](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37777386850), all three jobs succeeded |
| Regional Cloud Build | `2fc59fa9-323e-4105-8ba9-02aee1c2a3c6`, submitted from an isolated `git archive` of the release commit |
| Cloud Build result | SUCCESS, completed `2026-10-08T12:54:57Z` |
| Backend image digest | `sha256:e12c5f28de4711bd088baa2ccd651c0c65b6d342a7286ba68a5e87d527e3afea` |
| Controlled migration | `hirewiz-schema-migration-66fbl`, succeeded; production PostgreSQL head `20261008_0008` independently checked |
| Promoted API | `ai-resume-parser-00243-qep`, exact release health 200, image digest pinned |
| Promoted frontend | `dpl_FjoBZciN93ukkuXdH2xYcooX5FhM`, custom-domain promotion succeeded, Next.js 16.4.0 |

The first frontend attempt was blocked because Git used an unrecognized machine email.
A new metadata-only commit used the authenticated repository owner's verified GitHub
noreply identity. No implementation or Git history was rewritten. The blocked deployment
was not promoted.

## Verification before initial promotion

- Whole backend suite: **418 passed**; Ruff passed; Mypy passed for **46 files**;
  six prompt evaluation cases passed.
- PostgreSQL 17 migration: head `20261008_0008` → `20260803_0001` → head passed
  in a disposable local database. Production migration is recorded after execution.
- Frontend lint, typecheck, production build and **37 unit tests** passed.
- All **155 browser cases** passed against the production build at 320, 390, 768,
  1024 and 1440 pixels. Provider responses in these cases are synthetic fixtures.
- Exact-commit remote CI passed backend, frontend and security/container checks.
- Protected hosted frontend returned 200 for the HireWiz homepage and the expected
  mobile viewport declaration. Authenticated `vercel curl` verified unauthenticated
  protected reads and legitimate unauthenticated mutations return 401, while foreign
  origin mutations and Google-consent requests return 403.
- Development OIDC access to protected Production returned 403. No Trusted Sources
  permissions or Deployment Protection settings were broadened. Public-domain browser
  checks are required after promotion.

## Follow-up verification and observations

- Exact-commit CI passed all three jobs: **455 backend tests**, **45 frontend unit
  tests** and **155 browser cases**. Focused dispatch verification passed **46 cases**.
- [Seven independent PostgreSQL 17.11 dispatch scenarios](evidence/2026-10-08-followup-dispatch-postgres.json)
  passed with actual transactions and task-name SQL, using a synthetic Cloud Tasks client.
  They cover uncertain creation, expired publishing, fresh execution-recovery generations,
  early-worker races and bounded actual-failure exhaustion. The scratch schema was removed;
  no real Cloud Task or employer request was made by this drill.
- The release preparation actually ran the exact old serving image with a direct ASGI
  command and observed it healthy without startup migration before the controlled
  migration and candidate promotion.
- [A subsequent real public-source refresh](evidence/2026-10-08-followup-production-refresh.json)
  completed with one dispatch and one execution, a persisted real Cloud Task name ending
  in `-dispatch-1`, no error and source status `healthy` at
  `2026-10-08T14:39:32.516319Z`. This was job-feed GET discovery, not an application.
- A bounded Cloud Run log scan from `2026-10-08T14:26:17Z` to approximately
  `2026-10-08T14:37Z` returned no entries matching severity ERROR or higher or HTTP
  status 500 or higher across the API and both workers. Expected anonymous-worker 403
  warnings were excluded. This observation does not replace sustained monitoring or
  failure/recovery drills.
- A [second bounded observation](evidence/2026-10-08-followup-extended-errors.json)
  from 14:39:33Z to 15:10:06Z also returned no matching API/worker ERROR or HTTP 500
  records. Only timestamp, severity, service and HTTP status fields were fetched. This
  does not establish financial concurrency correctness; the subsequent credit repair
  has separate regression and release gates.
- Promoted custom-domain public browser verification is recorded below. No customer
  write, test payment or employer submission was used to verify this release.

## Page speed and device support

Heavy market charts load on demand, removing approximately 98.9 KB gzip from the initial
market entry. Navigation reuses shared profile data. Route loading states, footer/layout
stabilization, responsive menus and controls, and owner-bound cache resets are implemented.
The backend release configures one warm API instance; this adds idle infrastructure cost.
Workers continue to scale down independently.

[Thirty local production-build measurements](evidence/2026-10-08-responsive-lab.json)
observed LCP 72–264 ms and maximum CLS 0.0133. These used mock backend responses and
unthrottled local hardware, so they do not establish production speed or field p75 targets.
Consent-gated production telemetry records LCP, INP and CLS using static route/device/
connection groups, without raw URLs, query strings, resume contents or application answers.
Consent withdrawal stops collection. Field reports must show sample counts and consent
selection bias, use the latest sample per metric ID, and distinguish targets from measured
results.

[Hosted public UI verification](evidence/2026-10-08-hosted-responsive.json) passed all
20 route/width cases after promotion, with a separate visible-field follow-up for a
transient 320 px login remount. Homepage, pricing, login and employer-job login handoff
returned 200. Both pricing products rendered; callback destinations, keyboard/tap
navigation, visible focus and reduced motion worked. There were no unresolved runtime,
console, horizontal-overflow or automated accessibility-smoke failures. Local-network
median LCP ranged from 548 to 1,028 ms across the four routes; maximum CLS was 0.046.
These are unthrottled hosted lab observations, not field p75, INP or a load result.

The follow-up frontend change bounds the uncached public pricing-catalog request and
response body to 2.5 seconds, aborting the upstream request and using the existing
unavailable state on failure. It does not reuse stale prices or checkout-enable state.
Eight focused timeout/failure/freshness tests, lint, typecheck and a production build
passed. These changes are now promoted in the follow-up release.

[Follow-up hosted public UI verification](evidence/2026-10-08-followup-hosted-responsive.json)
passed all **20 route/width cases** for homepage, pricing, login and employer-job login
handoff at 320, 390, 768, 1024 and 1440 px. All 16 mobile/tablet menu controls measured
**44 × 44 px** and passed keyboard open/close and tap navigation; the four desktop cases
showed primary navigation. Both current pricing cards rendered at every width. There were
zero unresolved failures, runtime errors, console errors, horizontal-overflow cases or
Axe smoke violations. Visual review found no clipping or bad wraps. Seventeen resource
warnings were aborted Next.js route-prefetch GETs, with no observed core-asset/HTTP failure.
Consent was not changed and browser mutations were blocked during the smoke.

Median hosted-lab LCP was 704 ms for home, 1,100 ms for pricing, 560 ms for login and
568 ms for the job handoff; maximum observed CLS was **0.029**. There was one navigation
per route and width on unthrottled local-network Chromium. These measurements do not
establish field p75/p95, INP, load capacity or a complete accessibility audit.

Two fresh public catalog GETs returned 200 in **738 ms and 361 ms**, with
`private, no-store, max-age=0`; both exposed the current `inr-2026-10-08-v2` prices.
Two separate read-only authenticated owner catalog observations took **4.902 s and
1.427 s**, with server-reported time **4.5278 s and 1.0654 s**. These limited samples
establish neither a latency percentile nor the cause of the timing difference.

## Rollout findings and repairs

The legacy API entrypoint ran Alembic on every cold start. After the controlled schema
upgrade, the older image could not resolve the new migration head, producing restart
failures while the candidate was staged. The verified new revision was promoted and
has `AUTO_DB_MIGRATE=false`. The future release script now prepares and verifies the
exact serving image with a direct ASGI command before advancing the schema, checks
there is no unmatched staged revision, and clears command overrides on the new candidate.
That preparation path passed syntax and independent review, then executed successfully
for the follow-up release as recorded above.
Releases must be serialized; these checks are not a distributed deployment lock or a
proof of arbitrary schema-change compatibility.

Scheduled maintenance initially inherited missing publisher settings from the analysis
worker. Dispatch therefore defaulted to inline execution on an analysis-only worker;
the four employer refreshes stayed recorded as dispatched without starting a scan.
The Scheduler was temporarily paused while the explicit Cloud Tasks publisher environment,
task-token reference and required narrow identity permissions were repaired. The analysis
worker became `hirewiz-analysis-worker-00016-z29`. Eight unsent public-source refresh
intents were repaired in a guarded, audited transaction: the latest four were requeued and
four superseded duplicates were cancelled. All had zero execution attempts; no application,
credit or possible-send state was changed. Scheduler resumed at five-minute intervals.

Cloud Tasks ingestion and eight completed refresh operations were then observed. The
[database evidence](evidence/2026-10-08-production-dispatch.json) records eight real
Cloud Task names, one execution each, no local task names and four superseded cancellations
with zero executions. All four
sources were healthy. At `2026-10-08T13:45Z`, production held **356 open jobs**: Figma 150,
Freshworks 126, Razorpay Software Private Limited 27 and Supabase 53. Counts can change
as employer postings change. No source has submission permission. Read-only authenticated
catalog checks returned 200, search/apply prices 1/5, four manual application modes and
`worldwide_recall_verified=false`. Private worker checks returned anonymous 403 and
authenticated 200; the deliberate anonymous probes explain matching warning log entries.

The follow-up dispatch code requires explicit production mode and complete selected
Cloud Tasks routes before claims. It rejects production inline execution and employer
fallback to an analysis destination. Local inline scope rejection retains the publishing
fence and returns the intent to pending with an error.

Independent review also reproduced a crash-recovery defect: the 16-minute outbox lease
could expire while the 20-minute analysis lease was still active, and dispatch completed
when the handler returned `running`. Nonterminal results now retain retry intent without
consuming an execution attempt. Physical task names are persisted before the publish RPC;
uncertain publisher recovery reuses that name, while later execution recovery uses a fresh
dispatch generation. Unknown application and deferred artifact outcomes keep their domain
handling. Tests cover real analysis-handler recovery at 17 and 21 minutes, task tombstones,
publisher/worker races, busy observations and the unchanged bounded actual-failure budget.
The complete backend suite passed **455 tests**; focused dispatch tests passed **46 cases**.
This recovery hardening is deployed in follow-up commit
`3565655712c2ceee431bee8306678a020b2f462d`, whose exact-commit CI and rollout are
recorded above. The earlier hardening candidate
`5dda7a85a26517f81c744629f457d065bcf3cdb5` passed CI but was not deployed.

## Recorded production verification

- [x] Protected fresh backup gate and controlled migration execution succeeded.
- [x] Immutable image digest and exact healthy API revision promoted.
- [x] Private workers reject anonymous requests and have their intended topic scopes.
- [x] Five-minute Scheduler recovery and actual Cloud Tasks ingestion succeeded.
- [x] Four reviewed employer origins seeded; completed production scans and opening counts recorded.
- [x] Exact staged frontend promoted, custom domains checked and five-width public browser checks passed.
- [x] Read-only authenticated API checks passed without customer writes, payments or employer submissions.
- [x] Initial pilot post-release queue outcomes and health observed and recorded.
- [x] Follow-up exact-commit CI, backup, serving-image preparation, controlled migration and backend/frontend promotion recorded.
- [x] Follow-up real public-source refresh, bounded log observation and five-width public UI checks recorded.
- [ ] Sustained field-performance, load, independent restore/recovery and broader security drills from the requirements register remain open.

## Limits of this pilot

Discovery is limited to explicitly reviewed employers. Seven public read adapter families
do not establish worldwide coverage or greater than 95% recall. Automatic submission
remains disabled: public job-feed access supplies no employer write permission. The
companion, durable daily/batch admissions, independent recovery epochs and tombstones,
file quarantine/isolation, broader coverage evaluation and authorized receipt drills remain
open in [the remaining requirements register](REMAINING_REQUIREMENTS.md).

At this earlier release, the admission/batch and disabled-companion foundation was still
under development and schema head was `0008`. Its later separate promotion to schema
`0009` is recorded in the admission foundation report above; full acceptance remains open. The
[16 additional employer proposals](../../backend/resources/employer_sources.proposed_20261008.json)
record 969 openings observed during public research, but are explicitly
`proposed_not_enrolled`. They do not enlarge the live four-source catalog or prove recall.

No real employer application, production test payment or artificial receipt is part of
release verification. The pre-release backup is protected and expires under the bounded
release-backup policy; its creation is not evidence of a successful restore drill.
