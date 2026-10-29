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
| 4. Qualified AI and document routes | Paid operation adapters, complete-input checks, factuality/count contracts and 200 materialized synthetic native inputs | Actual funded model outputs, complete native runtime/render receipts, blinded human comparison and statistical evidence |
| 5. Capacity privacy and responsive experience | Account export/erasure, funded free limits, responsive review screens | Representative load/performance measurements and integrated privacy/security review |
| 6. Shadow accounting and migration preparation | Read-only accounting report, native schema25 operator and PostgreSQL17 migration/previous-app rollback rehearsal; legacy balances preserved | Exact production source/image capture, retained migration proof and representative shadow observation |
| 7. Controlled deployment and commercial decision | Draft PR57 and protected Vercel preview; release gates fail closed | Complete candidate CI, tested immutable backend build, guarded migration, opted-in canary, commercial hosting and all payment gates |
| 8. Ongoing quality and economics | Stop conditions and accounting signals defined | Operational alert ownership, regular invoice/quality review and measured ongoing evidence |

Machine-readable status and evidence are in `CREDIT_QUALITY_PROGRESS.json` and
`evidence/2026-10-27-unified-credit-local-integration.json`. Test runs are evidence
for these stages, not separate development checkpoints. Expected versus actual
effort is recorded only where measured; missing timing is not a zero-hour claim.

The follow-on evidence in `evidence/2026-10-28-unified-credit-release-preparation.json`
separates the locally connected legacy-order recovery journey from the new
single-credit purchase/discovery journey. Their external payment and employer
feeds are finite synthetic fixtures. Neither is a live commercial transaction or
an employer application. The preview serves the new frontend but is not proof of
the new production API contract.

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
   The schema25 deployment deliberately keeps the earlier pilot admission flags
   closed as well as the new credit flags. Do not describe resume upload,
   employer discovery, browser assistance or optional AI as available merely
   because the new services are healthy. The pre-maintenance snapshot records
   what was running; it is not authorization to reopen a route under new code.
   After the completed deployment, restore the captured queues, schedules and
   coordinator first. Reopen only individually reviewed pilot routes through a
   separate, journaled same-image native change tied to that exact deployment
   and the fresh pre-maintenance snapshot. Require the prior flag to have been
   `true`, the candidate revision to be healthy and 100% serving, current
   document-worker/caller pins and quality/funding policy to pass, and an exact
   readback after the change. Keep checkout, final employer submission and all
   new unified-credit/free-core flags off. If this separate reopening cannot
   be proved, leave the route closed and report the pilot as unavailable.
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

## Native schema25 operator

### Restore the existing pilot after base promotion

`infra/gcp/schema25_pilot_reopen.py` is a separate, explicit operator. It does
not run during schema migration or the three-service release. Capture its plan
only **after** the exact schema25 deployment has completed, the maintenance
queues and schedules have been restored, and the coordinator's original state
has been restored. Supply the exact private plan paths, SHA-256 hashes and
journals from all three phases, plus a new 32-hex nonce and an explicit comma
separated list of pilot flags. A plan is not a customer entitlement or a new
paid-route qualification.

The capture reads the prior true flags from the exact fresh pre-maintenance
revisions and verifies the current schema25 image, source, health and completed
deployment journals. Every `advance` and `reconcile` recomputes each selected
flag from those hashed prior revisions; a valid-looking edited plan cannot add
a flag that was previously closed. `advance` rechecks the retained restoration
evidence and current policy; upload-related flags also require current native
document acceptance and exact caller pins on the schema25 tagged serving
revisions. It changes only requested flags that were
previously true, retaining the same image, source, secrets and all other
configuration. The native PATCH is journaled once per service, workers before
API; a lost acknowledgement requires `reconcile` with the same plan and journal
before any remaining action. A changed etag, traffic allocation, source,
revision, policy, scanner proof or resource state stops the operation. Confirm
the new revision's health and the user's actual pilot journey before reporting
it available. This operator cannot reopen optional AI, checkout, new credits,
free-core processing or employer final submission.

**User-facing limitation:** schema25 base promotion closes
`OPTIONAL_AI_GENERATION_ENABLED`. If legacy AI generation is currently running,
it will stop after the base deployment and this pilot operator will not restore
it. Do not report the full existing service as restored while that route is
closed. Reopening AI requires a separately reviewed release with funded model
access, expense and quality gates, compatible schema25 behavior, and an exact
native readback; no flag edit to this pilot plan is sufficient.

The permitted finite flag set is direct resume upload, employer discovery,
browser assistance, its typed controls, attachments, native upload/pairing and
candidate ingress. Review the exact selected flags against the captured
pre-maintenance state before capture. If a needed feature is outside this set,
leave it closed for a separate release; do not edit the captured plan or add a
generic flag override. Preserve the original private journals as the rollback
and audit record. The pre-activation schema25 traffic-only rollback remains
separate and must be reviewed again after any pilot reopening.

### Maintenance before capture

`infra/gcp/schema25_maintenance.py` closes the existing three-service admission
flags without changing their images. It reuses the native one-send Cloud Run
adapter and private journal; it cannot deploy a candidate or perform DDL. Its
queue and scheduler controls are restricted to the four existing HireWiz queues
and three schedules. Capture preserves original resource states, physical
revision allocations, zero-percent tags, source/image and unrelated settings.
Tagged revisions with different configurations require separate review; they
are not silently consolidated into another configuration.

Finish any accepted document-worker promotion before capturing maintenance.
Document promotion changes API, analysis-worker and coordinator pins, so plans
captured before it become stale. After successful candidate CI/build and
policy/document/bootstrap preflight, freshly capture **both** the maintenance
and coordinator plans. Use an owned private directory and a separate `0700`
journal directory. Run:

```sh
python infra/gcp/schema25_maintenance.py capture \
  --plan=/private/release/maintenance.json --nonce=<32-hex-nonce>
python infra/gcp/schema25_maintenance.py pause-schedules \
  --plan=/private/release/maintenance.json --plan-sha256=<captured-sha256> \
  --journal=/private/release/maintenance-journal
python infra/gcp/schema25_maintenance.py close-api \
  --plan=/private/release/maintenance.json --plan-sha256=<captured-sha256> \
  --journal=/private/release/maintenance-journal
```

Use the separate existing `coordinator-capture`/`coordinator-pause` procedure
below, then invoke `close-workers` with the same maintenance plan/hash/journal.
Workers remain available while accepted work finishes. Before either worker is
closed, the native drain checks coordinator closure, actual queue contents,
unfinished Cloud Run executions and existing native schema21 SQL lease/work
invariants. An empty queue alone never proves drain. Do not purge accepted tasks
or erase unresolved provider liabilities to pass this check. Once both workers
are closed, `pause-queues` repeats drain and pauses the queues; an originally
paused application queue stays paused.

An uncertain action is reconciled by repeating its phase with `--reconcile`
and the same plan/hash/journal. Reconciliation only observes already-started
actions. If it returns `maintenance_phase_partial`, resume the ordinary phase
only after reconciliation has succeeded; it may then start the remaining
unattempted actions. A pending Cloud Run operation is polled under its original
operation identity. No new plan/journal authorizes replay of an uncertain write.

For a safe abort before a new deployment, run `restore-services`, then
`restore-queues`, then `restore-schedules`, using the original maintenance plan
and journal; restore the coordinator with its own original plan/journal. Only
actions actually started by this maintenance are restored. Service restoration
refuses after source/image/configuration drift, including a promoted new
candidate, rather than overwrite it.

Cloud Run can leave `latestReadyRevision` on the unserved maintenance-close
revision after restoring physical traffic to the captured original revisions.
The created restore revision can already be Ready but retired at zero traffic.
Reconciliation accepts that shape only with the exact acknowledged restore
generation, created revision and template, original physical traffic, and an
independently Ready restore revision. It records the created restore revision
without another PATCH. A later maintenance capture selects that Ready created
template, verifies every physical serving/tagged revision has the same source,
image, identity and configuration, and refuses if the stale ready revision is
still serving.

After a successful schema25 deployment, **do not use `restore-services`**.
The `restore-queues` and `restore-schedules` phases instead accept
`--deployment-plan`, `--deployment-plan-sha256` and `--deployment-journal` from
the exact completed schema25 release. The native proof rechecks successful
promotions, actual serving configuration/traffic, health, source/build/CI,
closed flags, financial state and schema. This resumes the captured resource
states without changing the deployed image or opening paid routes. Restore the
coordinator last. Enabling any legacy/new product admission remains a separate
reviewed rollout decision; this maintenance tool never opens new credit or
checkout flags.

`infra/gcp/unified_credit_schema25_release.py` provides explicit `capture`,
`observe`, `upgrade`, `migration-reconcile`, `advance`, `reconcile` and `rollback`
phases. Read its `--help` before constructing a plan. Plans and journals must be
owned private files; later phases require the exact plan digest. Capture requires
the commit, immutable image digest, successful build and CI identities, service
revision/source pins and exact secret-version metadata. It is not a general
database migration or arbitrary Cloud Run command wrapper.

The upload coordinator has a separate fixed-scope pause plan and journal. Use
`coordinator-capture` to record its exact source, image, configuration and serving
revisions, then `coordinator-pause` once. `coordinator-reconcile` observes an
uncertain result without repeating the write. The pause preserves the image and
closes the master scan/cleanup admission flag on every active or tagged route.
This does not prove existing invocations have finished: retain the independent
SQL lease and queue-drain checks. Global observation and the immediate pre-DDL
check both require fresh evidence that coordinator admission is closed.

Coordinator restoration applies the same physical-traffic readiness checks as
service maintenance: an unserved Ready restore revision may coexist with the
previous pause revision in `latestReadyRevision`. Only those two exact names
are accepted, with the created restore template and original physical routes
independently verified. Retired prior revision status/etag changes are accepted
only when the bounded immutable template, source, image, identity and original
admission flag still match. Pilot reopening reuses these checks rather than
requiring provider-owned status bytes to remain frozen.

Capture the initial migration plan against schema21 and verify fresh disabled flags and
drained work. `upgrade` preserves all old rows and permissions while adding the
14 credit/free-processing tables through the four reviewed migrations. A lost
acknowledgement requires `migration-reconcile` using the original journal, never
another DDL attempt. A fresh schema25 plan and separate deployment journal are
required before rolling out the new services. After a successful base rollout or
a safe abort, `coordinator-restore` uses its original plan and journal to restore
the prior flag and physical traffic allocations. Concurrent configuration changes
cause refusal; they are not overwritten. The API/analysis/employer rolling plan
still covers exactly those three services.

The rollback rehearsal runs the previous API snapshot from commit
`f0f973c9c6d989553b8f58b275fc06fd5685497a` against schema25. Its app files match
the observed production API source `436585cdd92a9c1e09c0d630eb73e9c61e9b1a78`.
The actual worker/coordinator source
`7a00839dc90a93caebb0b3b5f2e9e03c51e941e9` is rehearsed separately. The production
operator must still verify each observed prior image and build/CI provenance.
Rollback is traffic-only, keeps schema25 and is allowed only while every new
table is empty. Once the new wallet has activity, preserve its records and use a
separately verified compatible release; this pre-activation rollback cannot be
reused to discard obligations. Revision names alone do not prove source identity.

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
