# HireWiz pilot release — 8 October 2026

Status: initial pilot promoted; follow-up hardening release in progress. This report records observed results; it does not close the
full [requirements register](../requirements/JOB_SEARCH_AND_APPLICATION.md).

## Release identity

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

## Verification before promotion

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
passed. The mobile menu control now measures 44 × 44 px at 320, 390, 768 and 1024 px;
desktop navigation is visible at 1440 px. Independent local production-build checks
passed keyboard/touch activation and found no overflow or runtime errors. These follow-up
changes are not yet production evidence; their separate deployment is recorded after promotion.

## Rollout findings and repairs

The legacy API entrypoint ran Alembic on every cold start. After the controlled schema
upgrade, the older image could not resolve the new migration head, producing restart
failures while the candidate was staged. The verified new revision was promoted and
has `AUTO_DB_MIGRATE=false`. The future release script now prepares and verifies the
exact serving image with a direct ASGI command before advancing the schema, checks
there is no unmatched staged revision, and clears command overrides on the new candidate.
That new preparation path passed syntax and independent review but is not yet executed.
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
Its deployment and final exact-commit CI remain to be recorded. The earlier hardening
candidate `5dda7a85a26517f81c744629f457d065bcf3cdb5` passed CI but was not deployed.

## Production verification to record

- [x] Protected fresh backup gate and controlled migration execution succeeded.
- [x] Immutable image digest and exact healthy API revision promoted.
- [x] Private workers reject anonymous requests and have their intended topic scopes.
- [x] Five-minute Scheduler recovery and actual Cloud Tasks ingestion succeeded.
- [x] Four reviewed employer origins seeded; completed production scans and opening counts recorded.
- [x] Exact staged frontend promoted, custom domains checked and five-width public browser checks passed.
- [x] Read-only authenticated API checks passed without customer writes, payments or employer submissions.
- [x] Initial pilot post-release queue outcomes and health observed and recorded; follow-up monitoring is pending.

## Limits of this pilot

Discovery is limited to explicitly reviewed employers. Seven public read adapter families
do not establish worldwide coverage or greater than 95% recall. Automatic submission
remains disabled: public job-feed access supplies no employer write permission. The
companion, durable daily/batch admissions, independent recovery epochs and tombstones,
file quarantine/isolation, broader coverage evaluation and authorized receipt drills remain
open in [the remaining requirements register](REMAINING_REQUIREMENTS.md).

No real employer application, production test payment or artificial receipt is part of
release verification. The pre-release backup is protected and expires under the bounded
release-backup policy; its creation is not evidence of a successful restore drill.
