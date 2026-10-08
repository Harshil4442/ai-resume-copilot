# Bounded catalog latency diagnostics

Status: **exact-commit CI and GCP/Vercel rollout verified; five direct production catalog
samples completed and correlated**. Authenticated BFF runtime and improvement remain
unproved. This adds diagnostics to the authenticated
employer catalog read. It does not change pricing, search results, application permissions,
dependencies, schema, region, caching or automatic submission.

## Measurements and limits

Only GET `/api/v1/employer-jobs/catalog` gets a request-owned collector. Backend spans
cover middleware/dependency JWT validation, composite database acquisition, authenticated
owner lookup, catalog owner reuse, source lookup and rendering. The response header records
available phases before response-start. A fixed completion event records body-send,
cleanup and total ASGI time. ASGI send completion does not prove browser receipt.

The explicit connection acquisition occurs after valid JWT parsing, immediately before
the existing authenticated-owner SELECT, only for this catalog request. Normal catalog
reads retain two existing SQL executes. Connection establishment and ping are **unavailable**;
composite acquisition cannot be described as pool wait, connect or network time.

The BFF preserves streaming, no-store/private cache behavior, status and response bodies.
It filters backend Server-Timing through fixed numeric phase names, validates the generated
opaque ID and adds session and successful backend-header durations. A fetch failure or
timeout has session timing only: headers never arrived. Backend-header elapsed time combines
transport, platform and backend work and is not RTT. No extra telemetry request is made.

The new typed backend event is rebuilt from a fixed allowlist. It contains no token, owner,
answer, resume, query, client correlation ID, exception text or arbitrary log extra. Values
are finite, bounded and nonnegative; unavailable phases remain null. This narrow guarantee
does not certify preexisting general logging, Sentry or other telemetry.

## Local proof

[Frozen source and merged verification](evidence/2026-10-09-catalog-timing-local.json)
retain the repaired incremental patch hash and all ten source hashes. Independent review
found one timing-label defect: failed fetch elapsed time was described as backend headers.
The final patch omits that phase on failure and preserves the original 502/504 behavior.

Focused tests pass for concurrent owner isolation, zero acquisition on invalid JWT,
unchanged query counts, generated 500 responses, cancellation/send/cleanup boundaries,
late worker completion, logging failure and synthetic privacy sentinels. Frontend tests
cover stream identity, catalog-only handling and malformed, duplicate or injected headers.
Whole merged local verification passes **780 backend tests without failures/skips**,
**71 frontend unit tests**, Ruff, Mypy for 58 files, six prompt evaluations, compilation,
the production frontend build and zero backend/frontend generated-contract drift.

An initial Redis setup mismatch and an initial frontend build without its required auth
secret failed locally. The owned Redis container was renamed to the existing test fixture's
required name, and the unchanged build was rerun with the exact synthetic CI environment.
Neither repair changed application code or production credentials. Three preexisting
backend dependency deprecation warnings remain.

The first exact-commit [remote run for `61bdd45`](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37830875142)
found an existing synthetic companion fixture defect before its epoch-revocation assertion:
two clock reads could issue a maximum-lifetime command one millisecond too long. The
fixture now captures one timestamp. An advancing-clock regression verifies all five
envelope types at their exact limits and rejection at limit plus one millisecond. The
shipping verifier is unchanged. Local disabled-package checks, **56 protocol tests** and
**14 actual Chromium MV3 cases** pass after this repair. The integrated repaired commit
passed all four remote jobs as `6f38020`; the first commit was not promoted. The verified
[release report](RECOVERY_AND_CATALOG_RELEASE_2026-10-09.md) records backup, immutable
image/revisions, matching frontend alias and post-promotion observations. Five direct
catalog GETs returned HTTP200 with matching generated-ID completion events. Client totals
were 1196.537–1303.001ms; database acquisition/query phases dominate these bounded samples.
Connect/ping/RTT/pool/cold-start cause, percentiles and improvement remain unproved. No
existing operator browser session was used, so authenticated BFF runtime is not measured.

## Next evidence

- [x] Preserve catalog/auth/stream contracts and verify bounded numeric/private events.
- [x] Integrate frozen repaired source with merged local verification.
- [x] Pass all four remote CI jobs for immutable `6f38020`.
- [x] Promote that exact GCP image and Vercel deployment with backup and health evidence.
- [x] Collect five direct authenticated catalog samples and correlate generated IDs with events.
- [ ] Use measurements to select a fix and independently verify user-visible improvement.
- [ ] Prove field p75, sustained load, pool behavior and outage recovery separately.

The earlier slow authenticated reads motivated measurement; they establish neither a
latency cause nor a percentile. These diagnostics alone establish no performance gain.
