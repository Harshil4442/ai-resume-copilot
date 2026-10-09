# Remaining requirements after the discovery pilot

Review date: 9 October 2026 (Asia/Kolkata). This register distinguishes the promoted discovery and admission foundation from full development against `docs/requirements/JOB_SEARCH_AND_APPLICATION.md`. It does not close the full requirements checklist. Exact release IDs, whole-suite results and live checks belong in [the current component release report](CATALOG_SNAPSHOT_RELEASE_2026-10-09.md).

Earlier frontend `7c59578` passed exact CI and hosted homepage checks at 320/390/1440 px;
its GCP API/workers were `ec52279`. That release is superseded by `e7d8f2b` below. The
disabled v1 recovery core passed 85 focused cases and the merged 630-test CI suite. It supplies no production
storage, authentication, pairing, cloud restore or employer permission evidence. Browser
assistance is the selected first launch path, with human final submission; it remains
disabled pending these gates. Full execution/recovery requirements below remain open.

The promoted scope is a small employer-origin discovery and application-preparation pilot. Seven public read adapter families are implemented: Greenhouse, Lever, Ashby, SmartRecruiters, Workable, Personio and Pinpoint. The live reviewed catalog contains Razorpay, Freshworks, Figma and Supabase; 356 open postings were observed at the recorded production check. Sixteen additional sources are proposals, not enrolled employers. A permissioned Greenhouse submission implementation exists, but no actual employer write grant or authorized sandbox completion is supplied and automatic submission remains disabled. The admission/batch foundation is promoted as release `7cc49c2` with actual production schema `0009`. Follow-up `ec52279` retains that schema and adds shared Lever pacing plus an advisory catalog query optimization, with 545 backend tests and bounded production proof. Lever remains nonlive until its production coordinator and source-use gates pass. A standalone disabled browser companion passes localhost tests, but has no production pairing or authority. Neither worldwide coverage nor greater than 95% recall has been demonstrated.
Current `e7d8f2b` is verified on GCP and Vercel with schema `0009`, all four exact CI jobs,
930 backend cases without skips, 72 frontend units, three hosted widths and five
correlated direct v2 catalog reads. The first read took 4.272 seconds and the four
subsequent reads 0.959–0.967 seconds; these bounded observations do not establish
field percentiles or the first-read cause. Its v2 recovery source remains unavailable
for production actions. Additional
[Razorpay structural fixtures](RAZORPAY_FIXTURE_2026-10-09.md) pass 40 root cases but
are not shipping MV3/v2/authority integration. Pairing, independent retained authority,
real portal permission/parity and broader acceptance remain open.
The [pairing-only local source](PAIRING_ONLY_FOUNDATION_2026-10-09.md) now supplies
128 crypto/lifecycle/process cases, while its production store, recent authentication,
claim issuer and browser transport remain unavailable. The promoted
[catalog snapshot release](CATALOG_SNAPSHOT_2026-10-09.md) passes 930 merged backend
cases and 72 frontend unit cases; those results do not close production pairing.

## Gap register

The current reviewed root integration adds persisted model-cost ceilings, a fail-closed
pricing policy, generation quiescence, a GCP journal/registry SDK adapter, a coverage
audit harness, a fresh-user no-AI backend journey and a release guard. After correcting
the emulator's unsupported contention-schedule assumption, the default combined suite
passed **1,261 tests without failures or skips**. The subsequent reviewed connected
pairing bridge raises the complete root result to **1,279 passing tests without skips**,
with full-CI Mypy passing on 77 source files. Production authentication, durable
invocation/recovery and browser transport remain unenabled. The first expanded run's 1,235 passes
and one failure remain historical evidence. These additions are not serving production.
They do not establish browser pairing, real portal completion, a restore drill, provider
invoice caps or measured worldwide recall. See [root integration and staging](ROOT_COMPONENT_INTEGRATION_2026-10-09.md).

The subsequent [admission/authentication/KMS integration](PAIRING_AUTH_INTEGRATION_2026-10-09.md)
passes 1,459 combined backend cases without skips and the full-CI type selection
on 80 source files. Recent-password IO and KMS signing logging defects were
repaired and reviewed before integration. Protected durable pairing admission is
implemented locally; native candidate dispatch, retained account/session writers,
challenge-scoped admission before signing and actual cloud/browser proof remain
open. Account deletion also erases unknown model-cost holds in the current source;
a minimal independent financial ledger and late-settlement repair is required
before the monetary rollout. Production remains the earlier deployed release.

The subsequent [native candidate integration](NATIVE_PASSWORD_ROOT_INTEGRATION_2026-10-09.md)
connects dispatch and challenge-scoped signing admission locally. The default backend
suite now passes **1,504 cases without skips**, and full CI typing passes on 83 files.
The preceding exact `3b96c79` remote CI run is all four jobs green. Protected account/session
lifetime writers, complete restore effects, real cloud controls and browser transport
remain required. A real-stack browser journey passed the author's local run, but its
independent review found a process-group cleanup defect; that patch is held for repair.
Independent financial review also reproduced two PostgreSQL result/deletion deadlocks in
the first liability patch, which is held for repair. Neither held patch is integrated or
deployed, and no new monetary migration has been promoted.

The subsequent [financial/browser/lifetime integration](FINANCIAL_BROWSER_LIFETIME_INTEGRATION_2026-10-09.md)
clears those financial and process-cleanup defects and the independently reproduced
mutable lifetime-plan defect. Local checkpoints pass 1,539 default backend cases plus
65 additive lifetime cases, 90-file typing, 72 frontend units, 190 responsive fixtures
and the actual no-AI browser journey at five widths. The test/CI emulator isolation
repairs and prior failures are preserved. This source is not deployed. The protected
pending-denial barrier, complete restore, actual cloud authority, browser transport and
real portal acceptance remain open. The [monetary inventory and cutover sequence](MONETARY_CUTOVER_PREPARATION_2026-10-09.md)
does not claim legacy credential retirement or promote schema 0010.

Compatibility correction `b306f13` passed all four exact remote CI jobs and an immutable
GCP image build. Its API candidate is ready with generation disabled and **zero production
traffic**; the prior serving API retains 100%. No queue pause, worker retirement or
monetary migration has occurred. The old release path now refuses unreviewed revision
0010 before cloud access. Restoring two accidentally removed exact historical scan
exclusions produced empty local scanning and passing remote security CI; neither
exclusions nor green checks prove historical credential revocation. Production remains
the verified `e7d8f2b` release and schema `0009` described above.

**Missing** means the required capability was not found in the frozen code. **Unproved** means code or local tests support some of the requirement but the full acceptance evidence is outstanding. P0 is required before any real automatic send; P1 is the next development/evaluation priority; P2 is a scale or maintenance gate. A discovery-only pilot can proceed with these limits disclosed.

| Priority | Status | Requirement and gates | Current evidence and remaining work |
| --- | --- | --- | --- |
| P0 | Missing | EX02, EX06, DP05; G04A, G05B, G06D | Persisted application status fences ordinary duplicate tasks, but there is no independent recovery epoch outside the restored database. Implement durable launch permits for API and browser executors, epoch checks immediately before disclosure/send, restore-window quarantine and fresh approval after recovery. Test old queues and workers against restored data. |
| P0 | Missing / unproved | SE05, DP05; G05C, G06D | Durable artifact cleanup and account deletion are present. Independently retained revocation/deletion tombstones and a tested restore procedure that prevents account, file and approval resurrection are absent. Publish retention periods separately for resumes, artifacts, commands, logs and employer copies. |
| P0 | Promoted foundation; full gate unproved | BI06, NF01–02, FR07; G05D–E | Promoted `7cc49c2` adds durable candidate/day/rolling/pending allowances, immutable price/policy quotes, exact atomic batches, and verified requisition or conservative provider identity. Exact-commit CI includes 26 actual PostgreSQL cases covering admission races, locked balances and creation/deletion ordering. Production schema/health and controlled rollout are recorded. Fleet fairness, off-platform attempts, policy waiting periods, canonical remapping and independent recovery remain open. See [the foundation evidence](EMPLOYER_ADMISSION_FOUNDATION.md). |
| P0 | Unproved; external prerequisite | CN02–04, EX01, FR06; G05A | Public read access does not authorize writing. Before enabling a tenant, record the exact employer/provider permit, credential scope, origin, expiry and supported actions; validate every conditional question, consent, file constraint and complete-application receipt in an authorized sandbox. HTTP success or candidate creation is insufficient. No production test applications are permitted. |
| P0 | Missing / unproved | SD03, SE02–04; G03B, G06D | Private generation-bound GCS artifacts, hashes and cleanup leases are present. A quarantine-and-release pipeline with antivirus or an equivalent documented file safety scanner is not present. Production document-worker isolation, egress restrictions, secret exclusion and malicious-file drills remain unproved. |
| P1 | Local fixture foundation; production missing | EX02, EX04–05, FR06, SE03; G04A–D | A disabled Manifest V3 package with zero host grants passes 56 protocol and 14 actual Chromium tests against a synthetic localhost portal. It reviews exact answers before any autosave, preserves unknown checkpoints across restart and never uploads or submits. Authenticated production pairing, independent current action authority, tenant-specific permitted adapters, permission revocation and real portal validation remain required. See [the companion evidence](BROWSER_COMPANION.md). |
| P1 | Missing / unproved | CV01–05, CN01; G00A, G02C, G06A–B | Seven adapter families and four reviewed tenants do not establish coverage. The local audit harness is integrated, but a genuinely independent stratified employer/posting/query reference, including unsupported eligible portals and holdouts, remains to be collected. Measure misses, original-date uncertainty, query recall and top-K usefulness separately. Claims require a clustered-sampling 95% confidence-interval lower bound above 95% for each claimed scope. |
| P1 | Missing / partial | FR01–04, AI02–03, CV05; G01A–B, G02D | Basic extraction and fit use deterministic taxonomy rules. The search request has role, a location string, remote, age and employer exclusions; it lacks the full versioned country/language/salary-currency/employment/sponsorship preferences. Evidence is not uniformly claim-level. Strict role-token matching and a 1,000-candidate ranking cap need independent recall evaluation, aliases and an honest view-all path. |
| P1 | Local connected proof; broader release unproved | AI01, AI03–06; G00B, G01A–D, G07B | Optional generation, shared three-attempt budgets and owner-scoped reuse exist. Persisted cost ceilings, retained detached liabilities and the real no-AI browser journey pass local review, including actual PostgreSQL result/erasure races and five widths. External payment/feed transports are synthetic. Monetary schema/policy remain unpromoted. Complete writer retirement, rollout and independent basic/enhanced quality and useful-completion cost measurement. |
| P1 | Partial / unproved | RS01–08; G03A–D | Conservative source-preserving PDF/DOCX patches and exact-byte sealed artifacts exist. Application artifacts accept PDF/DOCX; full native LaTeX project support is not delivered. A production multi-file TeX sandbox and a representative visual/extraction report for fonts, pages, links, wrapping, reading order and unsupported inputs remain required. Preserve original/custom alternatives and actionable failure guidance. |
| P1 | Hosted public scope proved; full flow/field scope unproved | FR10, NF06; G02D, G04D, G06E | Lazy loading, owner-bound cache reset and browser accessibility/layout fixtures support the UI. Twenty public hosted route/width cases pass, including 44px mobile controls, keyboard, focus and reduced motion; current lab LCP medians are 476–864ms. Employer launch fixtures intercept provider responses. Exact-release CI also proves 190 browser fixture cases, including the batch UI. These checks do not prove real submissions, field p75/INP or sustained load. Collect privacy-safe field metrics and validate each newly integrated launch flow. |
| P1 | Discovery rollout proved; broader safety unproved | DP01–04, SE01–06; G00C–D, G05B–C, G06D | The admission foundation has exact-commit CI, controlled migration to 0009, pinned GCP image/revisions, exact Vercel promotion, private worker checks, a completed Cloud Tasks feed refresh and bounded error monitoring. Full permission/secret review, hostile-file isolation, kill-switch/incident and recovery drills remain required. A protected backup alone is not a restore/RPO/RTO result. The bounded foundation release does not close these broader gates. |
| P2 | Missing / unproved | CV06–07, NF01–04; G06C, G07A, G07C | Read adapters have bounded requests, retries and completed-scan closure guards. Fleet-wide tenant/credential quotas, Retry-After-aware fair scheduling, sustained source freshness, load/pool/outage tests and monthly SLO/cost reporting remain required. Contract maintenance must record owners, checked dates and retirement decisions. |

## Next independent development stage

Implement **controlled browser assistance and shared execution admissions**, with no real employer submissions. This stage can make substantial progress without external write grants:

1. Retain the now-promoted durable admissions, exact batch quotes, canonical claims and cancellation controls. Extend them with independent recovery authority and prove fairness, policy waiting periods and historical identity remapping before expanding execution.
2. Build the reusable device protocol: short-lived pairing, owner/device keys, tenant-specific origin grants, command nonce, package digest, allowed actions, expiry, current approval and independently stored recovery epoch. A cached or offline command cannot authorize a new disclosure or send. Do not transport browser cookies or passwords to the server.
3. Build a Manifest V3 companion against local employer fixtures. Keep sessions local; implement persistable checkpoints for service-worker termination, navigation and login; pause for CAPTCHA, MFA, assessments and unanswered eligibility questions. Require reviewed approval before any upload or autosave. Label the first release fill-only or candidate-submit.
4. Test hostile origin changes, cross-user/device replay, expired commands, altered files/forms/consents, cancelled batches, parallel workers, database restore, extension restart and revoked permission. Ordinary restart recovery must never replay a possible send. A later send implementation uses the same durable permit and recovery epoch as the API executor.
5. Produce a G04A–D evidence package from these fixtures and a list of tenant permits still required. Fixture success validates the protocol; a real adapter is released only after its permission and authorized form-parity tests. Keep automatic send disabled until G05A–D and recovery gates pass.

In parallel, build the **coverage audit harness and reviewed source catalog**. Freeze an India-first technical-role scope with global-compatible schemas; obtain product approval for any licensed discovery-only locator. Independently sample employers by geography, size, sector and ATS/custom-portal family, record official careers-to-tenant links, snapshot reference vacancies and review candidate queries. Add supported tenants based on verified origin evidence and measured marginal yield, then prioritize permitted Workday, SuccessFactors, Oracle/Taleo, iCIMS and regional/custom sources from measured misses. Do not remove unsupported public sources from the eligible denominator or substitute indexed-source counts for market coverage.

## Verification still required before broader release

- [ ] G00A–D: signed product scope/origin decisions, redacted runtime/cost baseline, reproducible setup and current permission inventory.
- [ ] G01A–E: whole cold-cache no-AI journey, independent parser/ranking corpus, persistent cost ceilings, outbox/provider fault evidence and exact concurrent billing outcomes.
- [ ] G02A–D: origin audit, complete-scan multilingual/date fixtures, independent posting/query recall report and accessible real-backend search flow.
- [ ] G03A–D: native source visual/extraction report, sealed-byte identities, truthful edits, full conditional forms and approval invalidation races.
- [ ] G04A–D: companion security/checkpoint/usability evidence plus per-tenant permits for real portal trials.
- [ ] G05A–E: authorized sandbox receipt contracts, pre/post-send crash matrix, daily/batch admissions, kill switches, exact quotes and unknown-outcome reconciliation without repeat sends.
- [ ] G06A–B: scoped recall confidence report and separate discovery/preparation/automatic-availability/confirmed-execution metrics that include unsupported jobs.
- [ ] G06C–D: sustained load, rate-limit fairness, source/Redis/database/queue outages, secret/redaction/retention checks and a measured restore drill with independent epochs/tombstones, quarantined uncertain sends and fresh approvals.
- [ ] G06E: five-width flow checks and measured field p75 LCP ≤2.5 s, INP ≤200 ms and CLS ≤0.1; synthetic timing remains supplementary.
- [ ] G07A–C: recurring contract/miss/permission review, independent basic/enhanced quality and outcome evaluation, documented cost decisions and updated native fixtures/runbooks.

Availability, queue-start time, six-hour source freshness and the proposed five-minute RPO/60-minute RTO are targets pending measurement. Root's release report should attach the exact immutable commit/image/frontend revision, migration results, full test counts and live discovery checks. This register must be revisited after those results; a safe discovery pilot is not completion of all development requirements.
