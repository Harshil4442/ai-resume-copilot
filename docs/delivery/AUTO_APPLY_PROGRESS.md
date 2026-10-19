# HireWiz development and deployment progress

Updated 19 October 2026 (Asia/Kolkata). These are the original six reporting
outcomes. All remain **OPEN** until their development and production acceptance
requirements are met within the explicitly approved pilot scope. Builds, tests and diagnostics are supporting evidence
within these outcomes, not additional checkpoints.

| Original outcome | Status | Remaining work |
|---|---|---|
| Official job search, ranking and user-selected job counts | OPEN | Seed and activate the 20 reviewed employer origins; verify freshness, closed-job removal, matching, requested counts and charging in production; show the twenty-origin pilot scope and verify source availability without a worldwide or >95% claim. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | OPEN | Complete private upload/scan/cleanup custody; verify truthful supported edits, preserved formatting, review, original/custom/tailored selection and downloads through the deployed user journey. |
| Candidate-reviewed direct company applications | OPEN | Release schema19 and provision native browser authority; publish the configured companion; verify ordinary enrollment/pairing, required and conditional fields, approved upload, separate final approval, one-shot submission and receipt reconciliation in production. |
| Paid search/application credits, profitable finite packs and payment recovery | OPEN | Activate finite packs and quoted prices after the launch hold is lifted; verify production payments, separate search/application charges against the service-credit balance, the independent AI-unit balance, holds, settlement, refunds and interrupted/duplicate recovery; calibrate estimated costs against actual usage. |
| Fast responsive pages and SEO | OPEN | Publish the frontend and verify complete desktop, laptop and mobile journeys, production loading performance and SEO. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Verify the restored builder with a native build, activate required private coordinator and browser services, deploy the final application candidate and frontend, and verify monitored production behavior. The preserved-data F7 GCP cutover is complete. |

No whole outcome is newly complete. There is no dependable remaining completion
estimate. The user has explicitly kept the paid launch on hold; development and
local verification may continue, but no Vercel upgrade or paid-service activation
is authorized by that continuation.
When an outcome closes, report its original estimate and actual elapsed time
against that same row.

## Approved release scope

On **16 October 2026**, the user selected: “Launch and verify the clearly labeled
pilot; keep worldwide expansion as follow-on work.” This release therefore covers
the twenty reviewed employer origins and the Razorpay/Greenhouse application pilot.
Worldwide coverage and its independent >95% assessment remain future work; they
must not be advertised as achieved. Payment, resume, candidate approval, privacy,
recovery and genuine production acceptance remain required for the same six rows.

## Paid launch hold

After the pilot-scope decision, the user selected **“Keep launch on hold”** when
asked about upgrading the existing Vercel Hobby team to Pro. Do not purchase an
upgrade, activate paid services or report the pilot launched. Resuming development
does not reverse this spending decision. The release still needs a reviewed
commercial hosting arrangement and fresh acceptance before launch.

Read-only Cloud Run metadata at **19 October, 00:31 UTC / 06:01 IST** confirmed
that API277 and the existing analysis/employer worker revisions still receive
100% production traffic, with checkout, optional AI, browser assistance and
automatic submission explicitly disabled. This is a configuration observation,
not an authenticated customer-journey check. The coordinator metadata request
was unavailable, so that read does not prove the service absent or ready.

The earlier Tasks-account verification grant expired on **16 October, 13:14 UTC**.
The last accepted scanner definition window expired on **17 October, 06:24 UTC**;
the inspector still references that same immutable image. Do not reuse those
time-bound checks or widen their limits. Fresh verification authority and accepted
scanner definitions are required before processing can be reactivated. The source
maintenance automation has not been deployed or accepted.

## Current deployment state

As of **16 October 2026, 09:31 UTC / 15:01 IST**, the preserved-data release
**F7 (`b281118`)** has completed its GCP cutover and production traffic promotion.
The previously CI-verified paid-application candidate is `e25b5be`; it has not been
deployed. Recent commits `2504a47`, `ab6121c` and `c1cced0` add definition-maintenance
source, explicit pilot labels and the selected-resume correction. This source is
being prepared for review in draft PR #23; the fresh whole-branch CI is pending.
Publishing a review branch does not authorize a production release or paid launch.

The original database is preserved. Its copy passed the complete source/target
content and catalog comparison across **49 tables and 9 sequences** before
upgrading. All **nine native Cloud Run migrations have succeeded**. The target
is now schema18 with **74 tables, 969 columns and 9 sequences**, and its runtime
and observer grants were verified complete. Completed transfer, migrations,
credential issuance and retirement actions must not be repeated.

The API revision `ai-resume-parser-00277-kur`, analysis worker revision
`hirewiz-analysis-worker-00024-foc` and employer worker revision
`hirewiz-employer-worker-00013-gex` now each serve **100% of production traffic**.
The execution passed its final serving and privilege-closure checks before
promotion. The original full candidate API contract had passed at **07:26 UTC**,
including configuration, image, numeric secrets and authenticated health.

An earlier attempt stopped at **08:09 UTC** during a fresh API health check,
before changing either worker. Exact server request metadata shows HTTP200 after
**12.416 seconds**, while the client allowed **10 seconds**. The bounded repair
changes only that socket timeout to **30 seconds**; it preserves TLS,
authentication, response identity checks and the overall deployment deadline.
The repair is committed in `75178fe` and was applied to the successful deployment
continuation. This timing evidence does not establish why the server was slow.

Read-only reconciliation after that stop verified unchanged service metadata,
no active regional deployment operations, no new backup from that attempt, and
retained previous journals and protected backups. The corrected continuation
reused API277 and completed the remaining worker stages, fresh acceptance gates,
migration-access closure and traffic promotion.

The corrected continuation started at **08:38 UTC** and passed the previous
health failure point. Its new source backup was created at **08:55 UTC**, with
Locked retention through **23 October, 08:55 UTC**. Native metadata at **09:04 UTC**
confirmed the new analysis worker revision, `hirewiz-analysis-worker-00024-foc`,
ready at zero production traffic. The employer worker was still on its prior
revision at that observation. The execution subsequently completed successfully
at **09:31 UTC**, promoting all three new revisions. The original source and
protected backups remain preserved.

The build identity, all three scoped grants and enabled deployment trigger passed
native readback at **10:02 UTC**. A real build under that identity remains to be
verified. The private coordinator’s production inventory and catalog were reviewed
on 16 October. An exact raw API comparison resolved the project-policy fingerprint
discrepancy as standard versus URL-safe base64 spelling of the same version token.
The restricted SQL role and numeric secret version were subsequently created,
and an actual ordinary login succeeded. Do not repeat that issuance or activation.
A read-only reconciliation then passed the role/authentication stages but stopped
at the final running-service guards. It did not emit a completed SQL descriptor;
no sequential coordinator workload provisioning was launched. The exact final
guard failure remains unresolved, and the old time-bound contract is now stale.

With F7 complete, remaining release work includes completing the private upload/scan/cleanup coordinator and native browser
resources, releasing schema19 and the configured companion, then deploying the
final GCP/Vercel configuration and verifying complete customer journeys. The
expired Tasks-account verification binding still requires exact-policy cleanup;
its expiry must not be extended silently to reuse the old release contract.

Queues, maintenance scheduling, checkout and new-feature admissions remain held.
Production login, paid search, tailoring and applications are not yet accepted.
See the [cutover procedure](FRESH_CUTOVER_EXECUTOR_2026-10-14.md) and
[complete read-only export repair](SOURCE_COMPLETE_READONLY_EXPORT_2026-10-15.md)
for the preserved-data release requirements.

## Implemented source and remaining rollout

- **Discovery:** deterministic search/ranking and candidate-selected counts are
  implemented. The [20-origin register](EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md)
  spans three ATS families; production seeding, activation and end-to-end
  acceptance remain. Worldwide coverage and greater-than-95% recall are
  unproven; follow the [coverage audit protocol](COVERAGE_AUDIT_PROTOCOL_2026-10-09.md).
- **Resumes:** document rendering has [native acceptance evidence](DOCUMENT_RENDERING_ACCEPTANCE_2026-10-14.md).
  The restricted upload coordinator still needs final SQL reconciliation, workload,
  invocation grants and genuine custody/scan/cleanup checks before the full
  tailoring and download journey can be accepted. See the
  [coordinator plan](RESUME_UPLOAD_COORDINATOR_2026-10-10.md).
  A local application-review correction now binds the tailoring Workspace result
  to the chosen source resume. Changing the selection hides the previous file's
  link, including when that previous preparation responds late. This does not
  automatically generate, approve or attach another resume; it is not deployed.
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
- **Application image:** the pending final-source batch removes unused TeX
  compilation packages from both application Dockerfiles and explicitly retains
  the measured PDF font baseline. Compilation stays in the dedicated private
  worker. Actual final-image size, font substitution and PDF rendering checks
  remain required; the measured installed package footprint is not a verified
  compressed-image or page-load improvement.
- **Native setup:** the pending final-source batch gives first-use cloud writes
  a bounded 30-second acknowledgement window. Reads and operation polling keep
  their existing bounds. Lost acknowledgements remain unknown and cannot cause
  an automatic resend or activation. Focused SDK transport tests cover all five
  write families; the actual production setup remains pending.
- **AI cost bounds:** the pending batch removes the standalone
  `gemini-3.5-flash` fallback and its candidate policy quote because its current
  price could not be verified. The configured primary and remaining fallbacks
  retain their existing limits. Source and policy must ship together. A genuine
  read-only ledger review at **07:04:50 UTC on 16 October** found no known adverse
  variance or outstanding paid-work liabilities in the reviewed slice. The
  estimated expense policy was approved and published as numeric version **2**,
  paired with the four-quote model policy's numeric version **2**. Their payloads
  and checksums were read back. The three pack prices and finite expiry are
  unchanged. Activation with the matching final image remains pending; fixed
  expenses and invoices remain estimates and profit is not guaranteed.
- **Tailoring price review:** the pending batch displays the catalog's AI-unit
  price before tailoring and requires that exact version and quantity for a new
  paid run. A changed quote refreshes for another explicit review; lost-response
  retries retain their original request. The existing financial history retains
  the accepted terms after resume input is purged. Focused backend, UI and local
  browser checks passed; production acceptance remains pending.

- **Search settlement:** the pending batch rechecks the selected posting and
  employer-source revisions before charging. A changed revision is excluded
  from that accepted search, including a job that has become ineligible; unused
  credits are returned through the existing settlement path. Even a still
  eligible changed revision requires a later search rather than silently
  replacing the reviewed snapshot. The check uses two batched reads for up to
  100 results. Focused regressions and independent source review passed;
  production acceptance remains pending.
- **Application recovery:** negative API reconciliation now requires a recorded
  completed outcome for the original matching sender. An unfinished sender
  keeps its credit hold and canonical application reservation; elapsed time
  alone cannot authorize a refund or another submission. Late worker results
  preserve independently confirmed receipts and their accepted charge. Browser
  recovery requires its original native evidence and cannot use the API route
  to bypass that requirement. Focused synthetic transport tests and independent
  source review passed; production recovery acceptance remains pending.

## Supporting validation

On 19 October, focused local synthetic checks covered the selected-count and
credit admission paths, newly delivered search settlement, application receipt
reconciliation, and expense-policy/failure-budget enforcement: **109 passed**.
The application-review change passed **208 relevant frontend tests**, including
new source-switch and delayed-response regressions, plus TypeScript and targeted
lint checks. These checks made no provider calls or real applications and do not
close any of the six production outcomes.

The previously CI-verified candidate **`e25b5be`** in [PR #23](https://github.com/Harshil4442/ai-resume-copilot/pull/23)
passed all five CI jobs in [run 38875552034](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/38875552034)
on 16 October. This verifies the tested source and local native document checks;
it does not establish production acceptance or cover the later local
maintenance and pilot-label commits.

Earlier candidate **`83e30b9`** in the same PR
passed all five CI jobs in [run 38845781461](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/38845781461),
completing at 03:58:00 UTC on 16 October. It includes the observer classification,
bounded transfer diagnostics, temporary schema-restore permission repair,
standalone bootstrap-CLI correction, explicit psycopg3 driver selection and
PostgreSQL OS certificate trust in migration and serving paths.
These checks validate source in its tested environments; they do not complete a
production outcome or validate the later local commits.

The [requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md) define full
acceptance. Earlier operational history remains in the
[historical archive](archive/AUTO_APPLY_PROGRESS_THROUGH_F6_2026-10-15.md).
