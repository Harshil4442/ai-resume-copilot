# Employer search and application delivery

Development and deployment authorized on 8 October 2026. The full requirements remain
in [the acceptance register](../requirements/JOB_SEARCH_AND_APPLICATION.md). This file
tracks implementation evidence and does not replace the wider worldwide coverage target.

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
- [ ] Verify all mandatory AI-disabled fresh-user workflows and optional enhanced modes.
- [x] Verify connected responsive flows at all five declared widths with local production-build evidence.
- [x] Validate migration round-trip through `20261008_0008` on PostgreSQL 17 and regenerate API contracts.
- [x] Complete pilot private queues/workers, controlled migration job and scheduled recovery.
- [x] Run full pilot CI, security/container checks and staging end-to-end tests.
- [x] Stage and promote the tested initial production backend and frontend release.
- [x] Confirm required pilot environment, worker IAM, Scheduler and production health after repairs.
- [x] Monitor the initial release and record deployment IDs, commit and observations.
- [ ] Promote and monitor follow-up configuration, pricing-timeout and mobile-control hardening.
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

Initial release `7980b624d2fd5a93994621538eb610e2ee4e5181` is live on GCP and Vercel.
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
particular, the companion, daily/batch admission limits, broader scan/catalog operations,
permissioned live receipts, independent recovery epochs/tombstones, security/isolation
drills and greater-than-95-percent recall are tracked in
[the remaining requirements register](REMAINING_REQUIREMENTS.md). Automatic submission
must remain disabled until the relevant tenant and safety gates pass.

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
