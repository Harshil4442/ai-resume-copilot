# Public employer-feed compatibility — 9 October 2026

Status: the inherited-query defect is repaired, independently rechecked and integrated
locally. Source-use review, new exact-source CI and production enrollment remain pending.
This checkpoint does not establish worldwide coverage or enable applications.

## Workable discovery repair

Actual public reads of two independently located employer careers tenants found that
Workable's documented `www.workable.com/api/accounts/{account}?details=true` URL returns
HTTP 302 to `apply.workable.com/api/v1/widget/accounts/{account}?details=true`. The
destination returns the existing public `jobs` collection. The unchanged adapter rejected
both feeds with `source_http_302`; those observations are retained.

[Workable's official public-feed reference](https://workable.readme.io/reference/jobs-1)
describes the original URL and jobs collection. Its
[careers integration guide](https://help.workable.com/hc/en-us/articles/115012771647-Using-the-Workable-API-to-create-a-careers-page)
shows redirect-following public GET examples. The exact new account-scoped destination
was established by actual response headers, rather than guessed from a tenant name.

The repair accepts one HTTP 302 only when its complete Location equals the fixed
HTTPS Workable widget path for the configured account and `details=true`. Arbitrary
hosts, ports, credentials, accounts, paths, query changes and second hops stay refused.
The originating stream closes before the destination GET. Authorization, cookies and
vendor credentials are stripped again; the same total deadline, pacing, response bounds,
normalization and two-read complete-snapshot check remain in force. Other adapters retain
their original redirect behavior. No authenticated Workable SPI or candidate endpoint is used.

## Observed origins and feeds

| Employer | Official employer origin proof | Public feed result |
| --- | --- | --- |
| Workable | [About page](https://www.workable.com/about) links exact `apply.workable.com/careers` | Repaired adapter: 2 normalized openings, two complete reads |
| Common App | [Careers page](https://www.commonapp.org/careers/) links exact `commonapp.workable.com` | Repaired adapter: 4 normalized openings, two complete reads |
| Pinpoint | [Company website](https://www.pinpointhq.com/) links exact `workwithus.pinpointhq.com` | Unchanged adapter: 3 normalized openings, two complete reads |

These **9 observation-time openings** are proposed discovery records, not a production
job count or recall percentage. Workable publication dates are retained as provider dates;
Pinpoint publication dates remain unknown. A remote label does not establish eligibility
in India or any other jurisdiction. The three proposals remain separate from the existing
four-source reviewed registry, with all submission controls disabled.

Innerspace and entero's official pages link exact Personio tenants, but their XML feeds
returned HTTP 404. Those two candidates remain deferred. A public careers page does not
prove its optional XML interface is enabled; the
[Personio integration guide](https://support.personio.de/hc/en-us/articles/207576365-Integrate-jobs-from-Personio-into-your-website-via-XML)
requires the employer to activate that interface. This research neither closes existing
jobs nor enrolls an unavailable feed.

## Verification and remaining work

The feed suite passes **69 cases**, including **15 new redirect-boundary cases**, with
zero skips. Ruff and configured Mypy pass. The original broader run's missing disposable
PostgreSQL/Redis configuration and 19 skipped cases are retained; the configured rerun
passes **all 123 affected cases**, with zero skips, using actual PostgreSQL and Redis
and uniquely owned schemas/keys. Actual public reads use the shipping adapter and the root patch,
without mock HTTP responses or AI calls.

Exact source hashes, initial failures, public GET observations, partial/error-body
semantics and configured test results are in
[the evidence record](evidence/2026-10-09-workable-feed-compatibility.json). Full public
response bytes remain in the owned local evidence directory, rather than the repository.

- [x] Verify exact employer-to-tenant links and public feed behavior.
- [x] Repair the observed account-scoped Workable redirect and verify refusal boundaries.
- [x] Save three discovery proposals with no submission grants or production writes.
- [x] Complete all five exact `4911d1f` CI jobs, including 2,002 backend cases.
- [x] Independently review and repair the inherited HTTP-client request defaults.
- [x] Recheck the exact repair: 73 feed cases, 16 unchanged independent probes and six
  new independent cases pass; Ruff and Mypy pass.
- [ ] Verify new exact-source CI and applicable source-use scope.
- [ ] Complete an explicitly reviewed enrollment/release.
- [ ] Expand the employer inventory and measure independently defined recall.

The successful `4911d1f` [CI run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37933390559)
covers the initial patch. Independent review reproduces all 69 feed cases and 14
additional checks, but two intended assertions fail: an injected HTTPX client's default
query parameters can change the otherwise fixed public GET URLs. One controlled input
contains a synthetic query credential; this is not an observed production leak.
The original failing probes remain retained. The subsequent two-path repair builds a
standalone public request with its exact URL, declared query and headers, explicit
timeout and no authentication. It excludes injected client's default query, headers,
Host, cookies and authentication before either Workable GET. The unchanged independent
probes now pass, as do new cross-adapter and retry cases; all 123 affected PostgreSQL/
Redis cases pass. Client transport, event hooks, mounts and proxy settings still run;
this is a bounded public-request repair, not arbitrary client isolation. Root verified
the frozen patch and all 23 independent artifacts before integration. The earlier
public feed observations above were not rerun or relabeled as new observations.
Green CI does not authorize source enrollment.
No production enrollment, deployment, payment, application, model call or candidate
disclosure occurred during this checkpoint.
