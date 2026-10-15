# Recovery source and catalog diagnostics release

Status: **release `6f38020` passed all four CI jobs and is verified on GCP and Vercel**.
This historical release is superseded by [verified `e7d8f2b`](CATALOG_SNAPSHOT_RELEASE_2026-10-09.md).
The catalog diagnostics are active. The recovery modules are included in the image but
remain unavailable for production execution: no routes, pairing, independent store or
browser bridge is enabled. The wider development goal remains active.

## Delivered behavior

The [bounded sequence core](RECOVERY_SEQUENCES_2026-10-09.md) retains the permanent
opening claim while its owning attempt advances up to eight reviewed static fields.
Actual process races, kill-after-commit and application-only restore cases exercise its
local fixture boundary. This does not establish production recovery authority.

The [catalog diagnostics](CATALOG_TIMING_2026-10-09.md) measure existing authentication,
database acquisition, source lookup and rendering without changing their contracts.
The BFF filters timing headers and preserves streaming and private/no-store behavior.
A synthetic companion fixture now captures one timestamp when signing its envelopes;
the shipping verifier's strict maximum lifetimes remain unchanged.

## Immutable CI and rollout

[CI 37831706133](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37831706133)
passed against `6f380203c55b8574d2d345420de6ac5e3da1b784`: **780 backend tests with no
skips, 71 frontend units, 190 responsive browser cases, 56 companion protocol cases and
14 actual Chromium MV3 cases**. Ruff, 58-file Mypy, migration round-trip, six prompt
evaluations, generated-contract checks, security checks and builds passed. Three existing
backend deprecation warnings remain. [Exact CI metadata](evidence/2026-10-09-sequences-catalog-ci.json)
preserves each job's identity and result. The earlier `61bdd45` run failed in its synthetic
envelope fixture and was not promoted; its failure remains documented in the timing report.

The release came from a tracked Git archive of that tested commit. A fresh protected
PostgreSQL dump was checked for format, catalog, remote generation, size and checksum
before deployment. [Backup evidence](evidence/2026-10-09-sequences-catalog-protected-backup.json)
records generation `1791488131404880`, 880,488 bytes and schema `20261008_0009`.
Creating and checking this backup does not prove restore, RPO/RTO or immutable retention.

[Cloud Build `46868635-7551-4492-8679-e0897e72ef98`](evidence/2026-10-09-sequences-catalog-cloudbuild.json)
succeeded at 19:50:05 UTC on 8 October (9 October in India). The controlled private
migration execution `hirewiz-schema-migration-dr4jh` succeeded, retaining schema `0009`.
All three services share digest
`sha256:93905744d294e459f282c7db9934755f36d404d0d5c70c25bd611194cbbcfe49`:

| Service | Ready revision | Serving traffic |
| --- | --- | --- |
| API | `ai-resume-parser-00262-fim` | 100% |
| Analysis worker | `hirewiz-analysis-worker-00020-kkx` | 100% |
| Employer worker | `hirewiz-employer-worker-00010-xsx` | 100% |

[Runtime inventory](evidence/2026-10-09-sequences-catalog-runtime.json) records selected
nonsecret settings and the migration execution. [Independent verification](evidence/2026-10-09-sequences-catalog-production-verification.json)
confirms exact API health, authenticated worker health, anonymous worker HTTP403,
separate allowed topics, unchanged schema and no LLM/payment credentials on the employer
worker. Discovery is enabled; automatic submission is false. The four healthy sources
remain manual-only, with 356 open postings at this check. This is a catalog snapshot,
not measured worldwide recall.

The exact Vercel snapshot was staged with `--skip-domain`, checked READY and promoted
after backend verification. [Authenticated alias metadata](evidence/2026-10-09-sequences-catalog-vercel.json)
at 19:54:18 UTC confirms `www.hirewizhq.com` points to
`dpl_6SXQf3MekLzV2dFtFWRdQXgjo5Qi`, whose commit matches CI. Protection remains
`all_except_custom_domains`. The staged hostname required protection bypass; its
unauthenticated catalog BFF returned HTTP401. No candidate session was manufactured.

## Post-promotion observations

[Hosted homepage checks](evidence/2026-10-09-sequences-catalog-hosted-responsive.json)
passed **3/3 at 320, 390 and 1440 pixels**. Counters/copy, overflow, mobile menu
keyboard/tap controls, 44px targets, focus and reduced motion passed. All nine screenshots
were visually inspected. Console/runtime errors, attempted writes, private reads and
foreign requests were zero. Cookie choices were untouched. This public lab scope does
not prove authenticated flows, field performance or complete accessibility.

Five sequential direct authenticated catalog GETs returned HTTP200. Exact generated-ID
completion events correlated with the verified API revision; [samples](evidence/2026-10-09-catalog-timing-production-samples.json)
and [correlation](evidence/2026-10-09-catalog-timing-production-correlation.json) retain
only fixed metadata and bounded numeric phases:

| Read | Client total ms | Backend headers ms | Composite acquisition ms | Owner query ms | Source query ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 1198.396 | 881.2 | 217.7 | 436.2 | 221.1 |
| 2 | 1303.001 | 991.9 | 264.1 | 501.9 | 221.2 |
| 3 | 1196.537 | 878.8 | 217.6 | 436.8 | 219.4 |
| 4 | 1206.645 | 878.4 | 217.4 | 434.9 | 219.1 |
| 5 | 1234.776 | 883.8 | 220.1 | 435.6 | 221.5 |

Database-related phases dominate these observations. Connect, ping, pool wait, RTT,
cold start and Neon suspension remain unmeasured; five reads establish neither their
cause nor a percentile or improvement. Authenticated BFF timing remains unproved because
no existing operator browser session was used. The diagnostic JWT file was owner-only,
short-lived and deleted after the single collection; no token or balance value is retained.

[Bounded log observation](evidence/2026-10-09-sequences-catalog-bounded-errors.json)
found zero matching ERROR-or-higher / HTTP500-or-higher entries for the three exact new
revisions between 19:50:05 and 19:56:29 UTC. This is not sustained availability, load or
incident-recovery evidence. No payment, candidate application or employer submission was
used for release validation.

## Remaining development

- [x] Verify immutable CI, protected backup, controlled GCP rollout and matching Vercel alias.
- [x] Check private workers, public responsive rendering and bounded catalog diagnostics.
- [ ] Ship authenticated pairing and independently retained current action authority.
- [ ] Integrate the permitted Razorpay adapter with sealed packages and actual browser lifecycle.
- [ ] Prove staging/security, deletion/restore and operational acceptance before cohort enablement.
- [ ] Expand reviewed sources and measure declared-scope recall independently.
- [ ] Verify all required no-AI workflows, format preservation, field performance and load.

The next [Razorpay fixture stage](RAZORPAY_FIXTURE_2026-10-09.md) is additional test source,
not part of this immutable release. Native attachment and final submission remain manual
in the chosen browser-first launch path. Full requirements are tracked in
[the remaining register](REMAINING_REQUIREMENTS.md); this foundation does not complete them.
