# Employer feed pacing and catalog read optimization

Status: **promoted and verified within the scope below**.
Application release `ec52279d236e82813bc486b9d5de9eb97a80408d` was promoted on GCP and
Vercel. It remains the serving GCP release; the frontend has since advanced to `7c59578`
as recorded in [the current component report](BROWSER_FIRST_FOUNDATION_2026-10-08.md).
This report supersedes the current-release pointer in the
[admission foundation report](PRODUCTION_ADMISSION_RELEASE_2026-10-08.md), while retaining
that release's incident and five-width browser history. All recorded times are UTC.
This stage adds no employer sources, grants, automatic submissions or production Redis.

## Changes

The authenticated job catalog previously selected the owner a second time after
authentication had already loaded that exact SQLAlchemy identity. The advisory catalog
now reuses that identity with `db.get`. A cold session still loads the requested owner;
a cached different owner cannot supply its balance. Existing source order, configuration,
prices and missing-owner behavior are preserved. Financial/admission operations continue
to refresh balances under their authoritative lock. This removes one avoidable SQL
round-trip on the normal authenticated catalog path; it does not establish a hosted
latency percentile or identify the cause of prior slow observations.

Lever pages and transient GET retries now acquire a shared host permit before dispatch.
Redis server time, non-evicting state, ownership checks, bounded HTTP/stream-close time and
restart quarantine coordinate cooperative workers across tenants. An unavailable or
incompatible coordinator rejects the scan before unpaced requests; incomplete scans
preserve existing jobs. Current Greenhouse, SmartRecruiters and Ashby pilot feeds remain
independent of this coordinator. See [the pacing contract](LEVER_HOST_PACING.md) for
bounds, failure assumptions, primary references and prerequisites.

Dedicated `EMPLOYER_HOST_PACING_REDIS_URL` takes precedence over legacy shared Redis
settings. An invalid explicit authority never falls back to another authority. Enrollment
requires one reviewed compatible primary on both API and employer-worker runtimes and
secret references retained by release configuration. No production coordinator was
configured by this stage; Lever remains nonlive. Public-feed coordination grants no
employer submission permission.

## Immutable deployment identity

| Item | Recorded result |
| --- | --- |
| Exact-commit CI | [37813652329](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37813652329), all four jobs succeeded |
| Regional Cloud Build | `41c3be1f-a5f9-4cd2-9daf-ecf320af9837`, SUCCESS at `2026-10-08T17:27:11.099940Z` |
| API and both worker image digest | `sha256:000ed7db0f4aefd59ed5fc708ae4c771052901a28a3aedf2a36f3e83a6f32257` |
| Controlled migration | `hirewiz-schema-migration-mvbhz`, completed at `17:24:17.535263Z`; actual production head remains `20261008_0009` |
| API revision | `ai-resume-parser-00257-tov`, ready revision with 100% traffic |
| Analysis worker revision | `hirewiz-analysis-worker-00019-z6v`, private; `analysis.run` scope |
| Employer worker revision | `hirewiz-employer-worker-00008-75j`, private; employer search/refresh/apply/artifact-delete scope |
| Vercel deployment | `dpl_JB49tvj5iVqA2Zf8BBN4Py5jxmZg`, READY production; exact commit metadata and custom-domain alias verified |
| Public domain | `www.hirewizhq.com` maps to that exact deployment |
| Automatic submission | Explicitly disabled on API and both workers |

[Build metadata](evidence/2026-10-08-pacing-cloudbuild.json),
[runtime inventory](evidence/2026-10-08-pacing-runtime-inventory.json),
[production verification](evidence/2026-10-08-pacing-production-verification.json), and
[Vercel alias evidence](evidence/2026-10-08-pacing-vercel.json) retain sanitized observations.
All services use production Cloud Tasks and the same digest-pinned image, which matches
the successful build. Anonymous worker health returned 403; authenticated health returned
200. The employer worker has no LLM, payment or email credential names. No production
Redis was added. The migration job ran the normal controlled upgrade; this release adds
no migration and changes no database location. The exact frontend promotion timestamp
was not separately recorded.

The four sources were healthy and manual-only at verification: Figma 150, Freshworks 126,
Razorpay 27 and Supabase 53 open postings, totaling 356 at that observation. Search/apply
prices remain 1/5 service credits per qualifying delivered job/verified complete automatic
application. The authenticated operator catalog returned 200 in 4,442 ms; this single
sample does not prove a latency improvement, cause or percentile. Original/custom choices
still avoid tailoring calls, and automatic application settlement was not exercised.

## Evidence and release checklist

[Integrated local proof](evidence/2026-10-08-pacing-catalog-local.json) records **545 passed,
zero skipped**, including 26 actual PostgreSQL cases, 30 pacing cases with eight actual
Redis tests, and four SQLite catalog query/ownership cases. It binds source hashes and
the same Redis image manifest pinned in CI. Ruff, Mypy across 50 source files, compile
checks and OpenAPI regeneration with zero drift passed. The task-owned Redis container
was removed; no production data or employer request was used by these tests.

CI now provisions a disposable loopback Redis with bounded readiness, test-only
configuration and cleanup, so its real coordination cases cannot silently skip.

- [x] Integrate only the bounded connector/coordination and advisory catalog changes.
- [x] Pass the complete merged backend suite, lint/type checks and unchanged contracts.
- [x] Preserve private workers, manual current sources and disabled automatic submission.
- [x] Pass [exact-commit CI 37813652329](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37813652329) on `ec52279d236e82813bc486b9d5de9eb97a80408d`: all four jobs succeeded, including 545 backend tests with no skips, 55 frontend units/190 browser cases and the disabled companion checks.
- [x] Take a [protected fresh schema-0009 backup](evidence/2026-10-08-pacing-protected-backup.json) and verify metadata before promotion. This is not restore/RPO/RTO proof.
- [x] Promote tested immutable images/frontend; verify identities, health and flags.
- [x] Observe a completed public-source task and bounded post-release logs.
- [x] Verify the public mobile/desktop boundaries for the unchanged frontend code.
- [ ] Independently verify compatible shared runtime Redis before any Lever enrollment.

## Post-release observations and backup

The [new public-source task](evidence/2026-10-08-pacing-production-refresh.json),
`dispatch_c059f1127df543f996f076a932072457`, completed with one dispatch and one execution.
Razorpay was healthy with no recorded scan error at `17:30:57.605899Z`. The source was
refreshed through the existing Scheduler/Cloud Tasks route. It used employer-origin GETs
and updated index/audit state only. No customer payment or employer application was used.

The [bounded metadata-only log scan](evidence/2026-10-08-pacing-bounded-errors.json),
from `17:27:12Z` through `17:32:29.645536Z`, found zero ERROR-or-higher or HTTP-500-or-higher
entries across the API and both workers, without reaching the 1,000-entry limit. It proves
only that declared observation window.

The [bounded public smoke](evidence/2026-10-08-pacing-hosted-responsive.json) passed six
homepage, pricing and anonymous employer-job handoff cases at 320/1440 px. HTTP/routing,
current prices, keyboard/tap menus, focus, reduced motion and overflow checks passed;
runtime/console errors and attempted writes/private reads were zero. Two fresh public
billing catalogs returned 200 in 2,104/411 ms. Two aborted `/register` route-prefetch GETs
remain resource warnings. There were no silent retries. Case-level JSON, six full-page
screenshots, six readable crops, three mobile menus and three overview sheets are retained
locally in `/tmp/hirewiz-public-smoke-bounded-ec52279-1791480638570/`; these temporary
files supplement the durable committed aggregate.
Visual inspection covered all six pages/crops and three menus without clipping. A
nonblocking 320 px homepage issue remains: the small `02`/`03` list counters wrap digits
vertically. It is recorded in the aggregate and queued for a focused presentation fix.

The frontend tree `96e977b101d6b2da85ce9f756fdd43b9d3befd80` and companion code are
identical to `7cc49c2`. The prior five-width, 20-case proof remains separate historical
evidence; six public boundary cases are sufficient for this backend-only follow-up absent
a new failure. Anonymous smoke does not exercise authenticated catalog identity reuse or
worker pacing. LCP samples were 2,144/640 ms on home, 816/2,820 ms on pricing and
1,324/560 ms on handoff; maximum observed CLS was 0.0581. The 2.82-second pricing
observation is retained. These unthrottled samples prove no field p75/p95, INP, load or
causal latency improvement.

The [fresh protected backup](evidence/2026-10-08-pacing-protected-backup.json) records
schema `0009`, creation `17:01:44Z`, generation `1791478904143733`, 880,087 bytes and
SHA-256 `2f2d822b0f3ac1479bad3eca92ca87c10c33d34094ca9d5f913317f06b9b16a4`.
Archive-format/catalogue verification passed. It was not restored and proves no RPO/RTO.

The wider device/recovery authority, permitted form/receipt, restore, field performance,
source-use and recall gates remain open. No greater-than-95% coverage, real auto-apply,
restore RPO/RTO or absolute physical network fencing guarantee is claimed.
