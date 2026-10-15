# Catalog snapshot and disabled pairing source release

Status: **`e7d8f2b` is verified on GCP and Vercel after all four exact CI jobs passed**.
The authenticated catalog uses one owner/source database statement and explicit v2
timing phases. Pairing and recovery source is included, with production adapters
unavailable. Automatic submission remains disabled. The full development goal is active.

## Delivered behavior and validation

The [catalog snapshot](CATALOG_SNAPSHOT_2026-10-09.md) independently revalidates the
bearer, reads fresh owner existence/balance with bounded enabled sources, and retains
401, empty-source, private/no-store, pricing and financial-lock contracts. It emits
`catalog_latency_v2`: combined SQL/ORM loading and in-memory materialization have
distinct names; legacy owner/source query phases are null on this route. There is no
response or balance cache. The generated frontend API contract is unchanged.

The [pairing-only core](PAIRING_ONLY_FOUNDATION_2026-10-09.md) adds exact challenge
ordering, Python/Node signature interoperability, permanent key ownership, revocation
and process-race fixtures. It supplies no production store, recent-authentication
adapter, claim issuer, HTTP route or shipping browser transport. The [Razorpay
structural fixtures](RAZORPAY_FIXTURE_2026-10-09.md) are included but remain synthetic
localhost tests, with no real form fill, attachment, employer receipt or charge.

[CI 37839872258](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37839872258)
passed against `e7d8f2ba0435fd3d4a504ccdce83fac365ec6835` on attempt 1:
**930 backend tests without skips, 72 frontend units, 190 responsive browser cases,
56 companion protocol cases, 14 actual Chromium MV3 cases, and 5 unit/35 browser
Razorpay fixture cases**. Ruff, 61-file Mypy, compilation, migration checks, six
prompt evaluations, generated-contract/security checks and builds pass. Three
existing backend dependency deprecation warnings remain. [Exact CI evidence](evidence/2026-10-09-catalog-snapshot-ci.json)
retains each job and the full log checksum. The previous fixture-only `a72d9a2`
font-build failure/retry remains documented separately; it was not part of this run.

## Immutable rollout

Both builds came from the tracked archive of the tested commit, tar SHA-256
`b6d75cca4919abafd53416036d8b96c8df69aa5b566eefba141a4ef5f440ef50`.
The fresh [protected PostgreSQL backup](evidence/2026-10-09-catalog-snapshot-protected-backup.json)
has generation `1791491750832553`, 918,591 bytes, schema `20261008_0009`, checked
dump format/catalog and matching remote size/hash. Restore, RPO/RTO and immutable
retention are unproved by these checks.

[Cloud Build `7bcf909b-c9d1-40ae-8431-25324ac5c33a`](evidence/2026-10-09-catalog-snapshot-cloudbuild.json)
succeeded at **20:52:40 UTC on 8 October** (9 October in India). The controlled
private [migration `hirewiz-schema-migration-6267x`](evidence/2026-10-09-catalog-snapshot-migration.json)
completed successfully; the schema remains `0009`. All three ready services serve
100% of traffic using the same pinned digest:
`sha256:cf2cb125a72c79a7b41f9c263c1a999521363d6c944affb3c017e0dc5e112f4d`.

| Service | Ready revision | Traffic |
| --- | --- | --- |
| API | `ai-resume-parser-00267-xot` | 100% |
| Analysis worker | `hirewiz-analysis-worker-00021-5zd` | 100% |
| Employer worker | `hirewiz-employer-worker-00012-lmz` | 100% |

[Runtime checks](evidence/2026-10-09-catalog-snapshot-runtime.json) confirm matching
public API health, anonymous worker HTTP403, authenticated operator worker HTTP200,
no public worker IAM grants, the exact task-principal invoker binding, separated
topic scopes and no LLM/payment credentials on the employer worker. An initial
task-principal impersonation command failed before the private health request;
[the failed verifier](evidence/2026-10-09-catalog-snapshot-verifier-failure.json) is
retained. Verification used the existing operator identity and checked the task
principal's IAM separately. No IAM change was made, and actual task OIDC delivery
was not exercised in this release check. The legacy deployment trigger is disabled.

The four healthy discovery sources remain Figma, Freshworks, Razorpay and Supabase,
with 356 open postings at this snapshot. Every source is manual-only; this count
does not measure worldwide recall.

Vercel deployment `dpl_HwhNvBnH1hnKTmoVC8MP865zPVhT` was [staged and checked READY](evidence/2026-10-09-catalog-snapshot-vercel-stage.json)
with exact commit metadata before promotion. [Authenticated alias metadata](evidence/2026-10-09-catalog-snapshot-vercel.json)
at **21:01:39 UTC** confirms `www.hirewizhq.com` serves that exact deployment.
Project protection remains `all_except_custom_domains`, root `frontend`, Node24.
The protected staging homepage returned HTTP200 with managed bypass; an
unauthenticated catalog BFF returned HTTP401. No candidate browser session was created.

## Bounded live observations

[Hosted homepage checks](evidence/2026-10-09-catalog-snapshot-hosted-responsive.json)
passed **3/3 widths: 320, 390 and 1440px**. Counters/copy, overflow, mobile
keyboard/tap controls, 44px targets, focus and reduced motion passed. All nine
screenshots were visually inspected, with hashes retained. Console/runtime errors,
writes, private reads and foreign requests were zero. Expected cancelled navigation
prefetches are retained as resource observations. Cookie choices were untouched.
This public local Chromium check is not authenticated-flow, field-performance or
complete accessibility acceptance.

Five sequential direct authenticated catalog GETs returned HTTP200. The generated-ID
completion events match the exact serving revision and new phase contract; [samples](evidence/2026-10-09-catalog-snapshot-samples.json)
and [correlation](evidence/2026-10-09-catalog-snapshot-correlation.json) retain all five:

| Read | Client total ms | Backend headers ms | Composite acquisition ms | Combined owner/source SQL ms | Materialize ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| 1 | 4272.493 | 3940.0 | 3266.5 | 514.0 | 0.0 |
| 2 | 963.495 | 646.5 | 212.7 | 429.5 | 0.0 |
| 3 | 966.921 | 649.1 | 212.9 | 429.7 | 0.0 |
| 4 | 958.989 | 646.1 | 212.8 | 429.4 | 0.0 |
| 5 | 963.693 | 647.2 | 212.9 | 429.4 | 0.0 |

The slow first read is retained. Connect, ping, pool wait, RTT, cold start and
database suspension remain unmeasured. Rounded 0.0ms is not proof of zero work.
The previous v1 run used different query phase scopes; these sequential bounded
runs do not establish causation, field percentiles, load capacity or sustained
improvement. Authenticated BFF timing is unproved because no existing browser
session was used. A short-lived owner-only diagnostic JWT file was deleted after
the single collection; no token, balance or candidate body is retained.

[Bounded log checks](evidence/2026-10-09-catalog-snapshot-bounded-errors.json) found
zero matching ERROR-or-higher / HTTP500-or-higher entries for the three exact new
revisions from **20:52:40 to 21:03:33 UTC**. This is not sustained availability,
load, incident-recovery or full acceptance evidence. No employer application or
payment was performed during validation.

## Remaining work

- [x] Verify exact CI, protected backup/migration, pinned GCP rollout and Vercel alias.
- [x] Verify bounded worker access, hosted rendering and v2 catalog observations.
- [ ] Connect production pairing/authentication and independently retained authority.
- [ ] Integrate permitted Razorpay browser assistance with sealed packages and real lifecycle checks.
- [ ] Prove restore/deletion, security, file isolation, full no-AI flows and cost ceilings.
- [ ] Expand reviewed origins and establish independent declared-scope coverage evidence.
- [ ] Complete native-source resume, field-performance, load and operational acceptance.

The [remaining requirements register](REMAINING_REQUIREMENTS.md) retains the full
goal. This component release does not enable automatic submission or complete it.
