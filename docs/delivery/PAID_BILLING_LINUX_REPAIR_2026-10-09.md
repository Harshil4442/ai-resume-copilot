# Credit-pack purchase links and Linux browser workflow

This checkpoint follows source `aab73dae8d3311debc6d9dba2abe4362238ab18b`.
It repairs the real credit-purchase path and the newly exposed browser-test race.
The source hashes and bounded verification results are recorded in
`evidence/2026-10-09-paid-billing-linux-repair.json`. The complete application
service, monetary migration and native browser authority remain unfinished.

## User-visible behavior

The employer search page's **Buy credits** and **Review credit packs** links, and
the application review's insufficient-credit link, now open
`/billing?sku=job_service_500`. Billing selects that catalog product when available.
Previously these links opened generic billing, which selected the first product,
Premium. Supported deep links, manual product changes and catalog visibility are
handled locally; they make no AI request and start no purchase.

The candidate must still confirm billing country and explicitly start checkout.
The server owns product price, eligibility and payment fulfillment. A client callback
does not grant credits. Invalid or unavailable product links do not create orders.
The query reader sits in a small Suspense boundary; existing payment callback and
polling behavior is preserved. Independent review confirms unchanged contracts with
the serving `e7d8f2b` backend and schema `20261008_0009`.

## Linux failure and repair

Exact-source CI run `37865771266` ended with four successful jobs and one failed
`cold-browser-journey` job. Backend verification passed **1,604 tests** without
skips and typing on **87 files**. The initial uploaded browser summary identified
only the browser phase; it did not preserve a concrete assertion failure.

An owned Ubuntu ARM64 reproduction exposed a race: the page displayed **Checking
status** while loading account data. The test used an instantaneous optional count
of **Review purchase**, skipped selecting the credit pack, then attempted Premium.
The strict synthetic checkout rejected its price. The test now opens generic
`/billing`, waits for the enabled credit-pack action, selects it and checks **Selected**
before payment. This also tests manual selection independently of the new deep link.
Exact price, callback-before-webhook, replay, accounting, file and approval assertions
remain intact. Hosted Ubuntu x64 CI for the new commit is still required.

Failure-only diagnostics export fixed error categories and numeric positions in the
cold spec into the existing summary. Raw browser messages, stacks, request data and
cookies are excluded. The original CI logs and failed local reports are preserved
privately. Only a clearly labeled partial excerpt of the original Linux page snapshot
survives; the whole snapshot file was replaced by a later Playwright run.

## Verified scope

- Root runner checks: **11 passed**, Ruff and one-file Mypy passed.
- Changed frontend: full lint/types, **72 unit tests**, a production build through the
  actual cold runner, and **15 targeted responsive cases** at five widths with no retries.
- The corrected test clicks the actual employer page's **Buy credits** link, verifies
  the selected pack and exact order SKU, and confirms no automatic purchase. Other
  cases verify manual override, invalid SKU and Premium purchasing separate credits.
- One new root actual Chromium → NextAuth/BFF → FastAPI → isolated PostgreSQL journey
  passed all five widths. External payment/feed transports are finite synthetic fixtures;
  BFF/auth/customer database behavior is real in this local test.
- That journey requested three jobs, delivered two, reserved six fixture credits,
  charged four, released two and ended with 496 credits. It verified one webhook grant,
  exact original/custom files, stale-approval409 and cross-owner404. Manual handoff
  charged zero, created no employer send attempt/receipt and made zero model calls.
- Owned server groups and schema cleanup were verified. This is neither a real payment
  nor an employer application, native companion grant or production monetary rollout.

The first root lint checkpoint failed after moving query reading into the large
component; its report is preserved. A small query bridge preserves the compiler's
existing callback memoization. The complete corrected checkpoint passes.

## Earlier staged deployment and held backend proposals

The earlier exact `aab73da` frontend deployment `dpl_6FDvzKCvx79BPTC8GDjtzASjEdYo`
is READY as a production candidate with custom-domain promotion skipped. Existing
managed access was reused in memory; deployment protection and credentials were not
changed. Read-only checks reached the empty NextAuth session, four private BFF401
denials and 15 public views at five widths, with no overflow/Axe/runtime failures.
Mobile and desktop public screenshots were inspected. An initial public probe failed
without a useful diagnostic; both a diagnostic probe and an unchanged original-harness
confirmation then passed on the same immutable target. All three reports are preserved.
These results cover the earlier source, not the new credit-link repair or an authenticated
candidate purchase. The live custom domain still served its prior release at observation.

Two separate proposed backend patches are **held and absent from this source**:

1. Protected denial closure prevents old readers, but an authorized pending journal
   writer can retain an intent after a purported complete cut. Independent review and
   a second ordinary-writer reproduction invalidate completeness. A durable publication
   and sealing protocol is required; another final read does not resolve the race.
2. A read-only monetary preflight missed reachable SET ROLE/ADMIN and column-grant
   writers, invalid financial provenance, unknown cost states and a writable inspector.
   Ten independent required-refusal probes failed. Its SQL subgate must be repaired.
   The existing schema0009 release guard remains in place; no false-green result is
   used for production fencing or migration.

The permanent whole-incarnation denial proposal is also an intermediate disabled guard:
normal per-subject availability, full recovery, physical resource controls and actual
browser action authority remain required. A single global GCS journal object would
introduce a throughput bottleneck; Google documents one write per second for the same
object name. Any replacement must address partitioning and retained history explicitly.
[Cloud Storage objects](https://docs.cloud.google.com/storage/docs/objects),
[Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning).

## Release checklist

- [x] Reproduce the Linux selection race and preserve failures.
- [x] Repair awaited selection and sanitized diagnostics without weakening accounting.
- [x] Repair all actual service-credit links and review serving-backend compatibility.
- [x] Verify the changed UI, actual local stack, ownership, zero-AI and cleanup boundaries.
- [x] Verify public/unauthenticated behavior of the earlier staged deployment.
- [ ] Obtain all five exact-source hosted CI successes for the new release.
- [ ] Build and verify the new immutable frontend candidate, then promote that same build.
- [ ] Complete reviewed credential/writer retirement, monetary migration and monitoring.
- [ ] Complete native browser pairing/actions, permitted portal acceptance, resume-source
  fidelity, coverage, recovery and the remaining full-product requirements.
