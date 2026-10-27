# Unified credit release runbook

Status: implementation and local verification in progress. This runbook does not
certify production readiness, grant a financial budget, or enable checkout.
The controlling product contract is
[`CREDIT_COST_QUALITY_V1_1.md`](../requirements/CREDIT_COST_QUALITY_V1_1.md).

## Changes in this release

The new wallet uses expiring credit lots and a durable event history. Existing
analysis units and service credits retain their original terms and balances;
there is no numerical conversion between them. Previously accepted work and
payment callbacks keep their recorded contract.

New paid actions require an owned, current quote for the complete inputs and
deliverable. Acceptance reserves customer credits and the entire bounded
execution cost before dispatch. Replays recover the original accepted work.
Delivery validation commits the credit charge; failed delivery restores it while
provider and worker liabilities remain reserved until measured reconciliation.
Saved-result reads do not need a new funded execution route.

The implemented tariffs are enrichment 2, enhanced match 3, eight interview
questions 5, standard tailoring 8, quoted extended tailoring 15, bullet rewrite 2,
enhanced Q&A 2, learning strategy 4, market report 1, and job discovery 1 credit.
Discovery means one completed batch containing one to five new qualifying jobs;
a partial batch is disclosed before acceptance. Empty batches restore the credit.
It is a bounded employer-origin pilot, not a worldwide completeness guarantee.
The future application-submission tariff does not enable final submission.

## Delivery and financial boundaries

- `credits.service` owns lots, reservations and wallet events. PostgreSQL locks
  serialize conflicting wallet changes, including payment replay and refunds.
- `credits.service_quotes` and `credits.runtime` bind exact input fingerprints,
  supported limits, route versions and reviewed cost envelopes.
- `credits.admission` reserves the full operation and global/account funding
  allowance. `credits.legacy_funding` counts retained legacy obligations in the
  same monetary limits; pausing rollout cannot escape through an older route.
- `credits.execution` records provider attempts and their actual or uncertain
  outcomes. Native estimates are not automatically relabeled as measured costs.
- `credits.analysis`, `credits.synchronous` and `credits.discovery` adapt actual
  product operations to these contracts. A synchronous retry never starts a
  second model call after an uncertain result.
- `free_core` has both per-account limits and a funded global allowance. Security,
  saved work and export remain available when new optional processing is paused.
- `credits.privacy` erases owner-bound quote/discovery data before employer and
  user records are removed. It detaches retained accounting without erasing
  unknown obligations. Telemetry contains aggregate amounts, not resume text.

## Eight existing delivery stages

| Stage | Current work | Evidence still required to finish |
|---|---|---|
| 1. Baseline and measurable contracts | Versioned requirements, tariffs, explicit limits and current release observations | Representative baseline workload, current vendor allocation and supported-cohort qualification |
| 2. Cost and billing foundations | Wallet, immutable quotes, capture/refund replay, protected cost reservations and concurrent admission | Final integrated review and reconciled production finance evidence |
| 3. Deterministic processing and reuse | Basic/curated paths, owned saved-result reuse and employer-origin batch discovery | Complete application journeys with real supported sources; no unsupported coverage claim |
| 4. Qualified AI and document routes | Paid operation adapters, complete-input checks, factuality/count contracts, offline evaluation packet | Actual funded model outputs, native format/render receipts, blinded human comparison and statistical evidence |
| 5. Capacity privacy and responsive experience | Account export/erasure, funded free limits, responsive review screens | Representative load/performance measurements and integrated privacy/security review |
| 6. Shadow accounting and migration preparation | Read-only accounting report, additive migration rehearsal, legacy balances preserved | Production-compatible release ports, retained migration/rollback proof and representative shadow observation |
| 7. Controlled deployment and commercial decision | Release gates fail closed; payment activation remains separate | Tested immutable build, guarded migration, opted-in canary, commercial hosting and all payment gates |
| 8. Ongoing quality and economics | Stop conditions and accounting signals defined | Operational alert ownership, regular invoice/quality review and measured ongoing evidence |

Machine-readable status and evidence are in `CREDIT_QUALITY_PROGRESS.json` and
`evidence/2026-10-27-unified-credit-local-integration.json`. Test runs are evidence
for these stages, not separate development checkpoints. Expected versus actual
effort is recorded only where measured; missing timing is not a zero-hour claim.

## Safe release sequence

1. Freeze the source revision, dependency lockfiles, generated OpenAPI and client
   types. Run the configured Linux CI, including genuine document engines and
   authorized local emulators. A macOS skip or synthetic output is not equivalent.
2. Keep `UNIFIED_CREDITS_ENABLED`, `UNIFIED_CREDIT_EXECUTION_ENABLED`,
   `FREE_CORE_ENABLED` and payment checkout off in the initial release. Preserve
   the current final-submission prohibition and candidate approval controls.
3. Rehearse schema 21 to 25 against a disposable PostgreSQL database. Verify the
   exact additive chain and old balances. Populated credit tables cannot be
   downgraded away. For rollback, pause new admission and retain the schema,
   ledger, quotes and accepted work.
4. Prepare the exact immutable Cloud Run image, prior image/revisions, secret
   metadata, native migration identity and source provenance in the existing
   guarded deployment process. Never substitute a runtime credential for a
   migration identity or bypass a failed catalog/permission check.
5. Migrate before serving code that reads the new tables. Stage API and worker
   revisions with new admission off. Verify health, owned reads, legacy callbacks,
   account export and source download without generating a real application.
6. Deploy the frontend only against the matching API contract. Public pricing
   uses server amounts. Unsupported/paused paid actions are unavailable, while
   saved-result lookup remains usable. Inspect mobile and desktop review flows.
7. Import a real, current runtime review only after the declared route envelope
   has passed quality and cost qualification. Use an explicitly funded opted-in
   cohort of at most 5% for the initial canary. Retain exact identities for all
   accepted work and reconcile any unknown outcome before retrying an action.
8. Observe representative shadow accounting for at least seven days and at least
   200 successful paid-equivalent operations across supported cohorts. The
   passage of seven calendar days, a synthetic dataset, or a report with the word
   “success” does not satisfy this gate alone.
9. Verify commercial hosting, tax/payment treatment, current provider prices,
   invoice allocation, legacy rights, quality/security and delivery evidence.
   `credits.release.ReleaseReview` requires retained artifact digests and a
   current financial policy; do not fabricate them to make checkout pass.
10. Activate the reviewed campaign and checkout only after those gates pass.
    Capture the first approved purchase/refund checks and monitor delivery cost,
    failure/unknown outcomes, refund rates and cash reserved for obligations.

## Shadow report

`backend/scripts/credit_shadow_report.py` reads aggregate operation and ledger
data using a read-only database transaction. Provide a reviewed nonsecret runtime
configuration and a completed timezone-aware window of at most 31 days. It
reports hypothetical tariffs, explicit discovery batch totals, retained provider
liabilities, protected execution exposure, unspent credits and accepted holds.
It always reports `release_qualified: false`; it cannot debit a wallet, create a
provider call, prove human quality, or reconcile an invoice.

The report does not replace provider invoices or resource metering. Do not
release native expense holds just because the customer received a result.
Record real attributed execution costs through the existing internal settlement
function only after the source measurement is verified. Unknown amounts remain
protected across month boundaries, cancellation and account deletion.

## Commercial guardrails

The proposed offer packs are ₹59/40 credits, ₹179/130 credits and ₹449/360
credits. New passes last 30 days and do not automatically renew. Expired source
access and data retention are separate policies. Failed accepted work restores
credits with at least seven days of usability where required by the contract.

The adverse model assumes 2,000 monthly buyers out of 10,000 users, all buying the
Starter offer and redeeming every credit, with ₹35,000 shared monthly overhead.
Tax and reserves are protected; estimated surplus is not recognized cash profit.
Include additional staff, review, marketing and agent costs before checking the
15% minimum margin. The gate cannot be relaxed by assuming more buyers or using
a smaller fixed-cost estimate. A funded operating period is required even if
there are no purchases.

Until genuine same-pack reference-price evidence is approved, display an honest
introductory price and real campaign end date. Do not use an invented crossed-out
former price or a resetting countdown. Freeze the eligible service list in each
purchase quote so a pass is never sold with an empty or unreviewed service claim.

## Stop and rollback

Pause the affected new route for fabricated claims, original-format corruption,
privacy leakage, an unauthorized application action, stale cost policy or lost
funding headroom. Preserve saved reads, export, security and accepted obligations.
An unknown model, payment or submission response is a reconciliation state, not
permission to repeat a POST. Roll back code only to a compatible tested version;
retain all populated accounting tables and do not restore an expensive route
without sufficient reserved funding.
