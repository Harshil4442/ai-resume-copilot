# HireWiz development and deployment progress

Updated 16 October 2026 (Asia/Kolkata). These are the original six reporting
outcomes. All remain **OPEN** until their development and production acceptance
requirements are met. Builds, tests and diagnostics are supporting evidence
within these outcomes, not additional checkpoints.

| Original outcome | Status | Remaining work |
|---|---|---|
| Official job search, ranking and user-selected job counts | OPEN | Seed and activate the 20 reviewed employer origins; verify freshness, closed-job removal, matching, requested counts and charging in production; measure coverage against a declared reference set. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | OPEN | Complete private upload/scan/cleanup custody; verify truthful supported edits, preserved formatting, review, original/custom/tailored selection and downloads through the deployed user journey. |
| Candidate-reviewed direct company applications | OPEN | Release schema19 and provision native browser authority; publish the configured companion; verify ordinary enrollment/pairing, required and conditional fields, approved upload, separate final approval, one-shot submission and receipt reconciliation in production. |
| Paid search/application credits, profitable finite packs and payment recovery | OPEN | Activate finite packs and quoted prices; verify production payments, separate balances, holds, settlement, refunds and interrupted/duplicate recovery; calibrate estimated costs against actual usage. |
| Fast responsive pages and SEO | OPEN | Publish the frontend and verify complete desktop, laptop and mobile journeys, production loading performance and SEO. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Resolve the transfer failure; complete preserved-data migration and staged checks; restore the builder, activate required private services, promote GCP/Vercel releases and verify monitored production behavior. |

No whole outcome is newly complete. There is no dependable remaining completion
estimate. No additional action or approval is currently needed from the user.
When an outcome closes, report its original estimate and actual elapsed time
against that same row.

## Current deployment state

The first preserved-data release, **F7 (`b281118`)**, remains frozen in its
separate worktree. Its old writers are isolated, and the source database is
preserved. Existing replacement credentials must be reused without repeating
issuance or completed retirement operations.

The V12 continuation successfully created and verified the protected source
backup with **Locked retention through 22 October**. It then stopped at
`transfer_migration_staging` with
`fresh_final_transfer_not_verified_reconcile_only`, before `transfer.started`
was created. **No completed data transfer or traffic promotion is verified.**

Read-only reconciliation confirms that the target passes the exact empty-target
check. The source remains at schema9; its 49-table catalog, sequence, exported
snapshot and collation checks pass. Transfer diagnosis is in progress; these
checks do not establish a successful transfer. The original journal and backup
remain preserved. See the [cutover procedure](FRESH_CUTOVER_EXECUTOR_2026-10-14.md)
and [complete read-only export repair](SOURCE_COMPLETE_READONLY_EXPORT_2026-10-15.md).

Queues, maintenance scheduling, checkout and new-feature admissions remain held
until the required release checks pass. Login and complete customer journeys
still require production verification.

## Implemented source and remaining rollout

- **Discovery:** deterministic search/ranking and candidate-selected counts are
  implemented. The [20-origin register](EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md)
  spans three ATS families; production seeding, activation and end-to-end
  acceptance remain. Worldwide coverage and greater-than-95% recall are
  unproven; follow the [coverage audit protocol](COVERAGE_AUDIT_PROTOCOL_2026-10-09.md).
- **Resumes:** document rendering has [native acceptance evidence](DOCUMENT_RENDERING_ACCEPTANCE_2026-10-14.md).
  The restricted upload coordinator still needs its SQL identity, workload,
  invocation grants and genuine custody/scan/cleanup checks before the full
  tailoring and download journey can be accepted. See the
  [coordinator plan](RESUME_UPLOAD_COORDINATOR_2026-10-10.md).
- **Direct applications:** companion **0.8.0** implements the Razorpay/Greenhouse
  pilot's required controls,
  approved upload and separately reviewed one-shot submission receipts. Installed
  Chrome for Testing acceptance uses controlled HTTPS fixtures and actual SQL
  and cryptographic composition. It does not establish production employer
  acceptance. Ordinary Chrome installation, native Firestore/KMS authority,
  configured packaging and normal-user enrollment/pairing remain. See
  [hosted-network acceptance](BROWSER_HOSTED_NETWORK_ACCEPTANCE_2026-10-15.md),
  [candidate activation](NORMAL_CANDIDATE_BROWSER_ACTIVATION_2026-10-15.md) and
  the separate [schema19 release path](BROWSER_TYPED_CONTROLS_SCHEMA19_RELEASE_2026-10-15.md).
- **Billing:** source prices are **1 service credit per newly delivered job**,
  **20 per confirmed complete application**, and **2 AI units for optional
  tailoring**. Three finite packs replace unlimited usage. Production payment,
  settlement and recovery acceptance remain; estimate-based prices require
  actual cost calibration. See [finite packs](FINITE_PACK_IMPLEMENTATION_2026-10-09.md)
  and [cost policy](COST_PRICING_POLICY_2026-10-09.md). Unknown submission outcomes
  retain their hold for reconciliation and must not trigger another POST.
- **Release:** after the preserved-data F7 cutover, restore deployment automation
  and release the later source through its explicit schema19 procedure. Complete
  private service activation, Vercel promotion, production billing/resume/search
  journeys, SEO, performance and monitoring. The
  [rolling-release guide](ROLLING_RELEASE_2026-10-15.md) keeps ordinary updates
  separate from the one-time data transfer.

## Supporting validation

Remote candidate **`dd569`** in [PR #23](https://github.com/Harshil4442/ai-resume-copilot/pull/23)
passed all five CI jobs: backend **5,446 passed, 1 skipped, 243 subtests**;
frontend **803 unit checks** and **245 browser cases across five viewports**.
This validates that source in its tested environments; it does not complete a
production outcome. Local **`197deab`** adds optional safe transfer diagnostics
and has not been deployed.

The [requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md) define full
acceptance. Earlier operational history remains in the
[historical archive](archive/AUTO_APPLY_PROGRESS_THROUGH_F6_2026-10-15.md).
