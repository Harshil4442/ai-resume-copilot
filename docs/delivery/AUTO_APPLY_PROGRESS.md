# HireWiz development and deployment progress

Updated 21 October 2026 (Asia/Kolkata). These are the original six reporting
outcomes. All remain **OPEN** until their development and production acceptance
requirements are met within the explicitly approved pilot scope. Builds, tests and diagnostics are supporting evidence
within these outcomes, not additional checkpoints.

| Original outcome | Status | Remaining work |
|---|---|---|
| Official job search, ranking and user-selected job counts | OPEN | Seed and activate the 20 reviewed employer origins; verify freshness, closed-job removal, matching, requested counts and charging in production; show the twenty-origin pilot scope and verify source availability without a worldwide or >95% claim. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | OPEN | Complete private upload/scan/cleanup custody; verify truthful supported edits, preserved formatting, review, original/custom/tailored selection and downloads through the deployed user journey. |
| Candidate-reviewed direct company applications | OPEN | Release schema19 and configure the provisioned native browser authority; publish the configured companion; verify ordinary enrollment/pairing, required and conditional fields, approved upload, separate final approval, one-shot submission and receipt reconciliation in production. |
| Paid search/application credits, profitable finite packs and payment recovery | OPEN | Activate finite packs and quoted prices after the paid-sales hold is lifted and commercial hosting is resolved; verify production payments, separate search/application charges against the service-credit balance, the independent AI-unit balance, holds, settlement, refunds and interrupted/duplicate recovery; calibrate estimated costs against actual usage. |
| Fast responsive pages and SEO | OPEN | Publish the frontend and verify complete desktop, laptop and mobile journeys, production loading performance and SEO. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Activate the configured private coordinator and browser services, deploy the final application candidate and frontend, and verify monitored production behavior. The preserved-data F7 GCP cutover is complete. |

No whole outcome is newly complete. There is no dependable remaining completion
estimate. The owner has authorized continued prelaunch development, integration
and deployment preparation using the existing free Vercel plan, subject to its
usage eligibility. No Pro purchase or paid-sales activation is authorized.
When an outcome closes, report its original estimate and actual elapsed time
against that same row.

## Approved release scope

On **16 October 2026**, the user selected: “Launch and verify the clearly labeled
pilot; keep worldwide expansion as follow-on work.” This release therefore covers
the twenty reviewed employer origins and the Razorpay/Greenhouse application pilot.
Worldwide coverage and its independent >95% assessment remain future work; they
must not be advertised as achieved. Payment, resume, candidate approval, privacy,
recovery and genuine production acceptance remain required for the same six rows.

## Prelaunch continuation and paid-sales hold

On **21 October 2026**, the owner directed continued prelaunch work on the free
Vercel plan, with a Pro upgrade planned when selling. This supersedes the earlier
blanket launch hold for prelaunch development, integration and deployment
preparation. Do not purchase Pro, activate paid sales or report the commercial
pilot launched under this instruction.

Provider eligibility is a separate requirement. Vercel's
[commercial-usage guidance](https://vercel.com/docs/limits/fair-use-guidelines#commercial-usage)
defines commercial use by financial purpose, including advertising services for
sale before the first payment. Having no sales does not establish Hobby
eligibility. Resolve the permitted hosting arrangement before commercial
deployment; this owner decision does not waive provider terms or current
technical acceptance requirements.

Read-only Vercel metadata on **21 October** confirmed the team remains on active
Hobby and an existing preview for source `8eb5f86` is READY. No new deployment was
performed in that check. The authenticated CLI check returned HTTP 200 with
HireWiz homepage content after correcting a CLI argument-forwarding issue;
deployment protection was unchanged. Sign-in and complete user journeys remain
unverified, so this is not production acceptance.

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

### 21 October integration work

The private resume coordinator was first staged as
`hirewiz-resume-coordinator-disabled-202610211605-2a99` and native Cloud Run
readback confirms READY. It uses the existing immutable F7 image, internal ingress,
IAM checks, its dedicated keyless service account, concurrency one and a maximum
of one instance. No database secret is installed, the service has no invoker
bindings, and all processing/admission flags are explicitly false. This is a
staged resource, not accepted upload, scan or cleanup behavior.

Subsequent native readback confirmed configured revision
`hirewiz-resume-coordinator-configured-disabled-202610211613` READY. It now has
only its dedicated numeric database secret reference, reviewed storage/document
pins and the actual Scheduler caller identity. Resource-scoped grants allow only
that caller to invoke it, the coordinator to access its dedicated secret and the
coordinator to invoke the document inspector. Both scan and cleanup schedules
exist and are PAUSED. Processing remains disabled; fresh definitions and genuine
scan/storage/cleanup acceptance are still required.

A fresh independent read-only check verified the existing coordinator SQL role,
numeric secret version, ordinary authentication and exact schema/grants. Reissuing
the role or credential is unnecessary. Final service configuration, invocation,
fresh scanner definitions and actual custody/cleanup acceptance remain pending.
Paused queues contain retained work; the earlier claim that all queues were empty
must not be reused for a new release.

The rolling-release source now supports an explicit numeric candidate expense and
model-policy pair while preserving the prior revision and its original rollback
references. Candidate validity and exact prior/staged templates remain checked;
this does not reopen admissions or update production policy references. The repair
has passed focused verification and independent review; exact-source publication
and native deployment remain required.

The account/device native bootstrap has now completed all twelve acknowledged
operations in **37.93 seconds**: three named Firestore databases and baseline
records, immutable journal witnesses, a KMS key ring and two signing keys. The
owner-approved 24-hour journal retention lock was verified natively before
initialization. No user/device authority was seeded and all activation flags
remain false. Runtime permissions and three dedicated transport secrets have since been
provisioned and verified. Schema19, runtime configuration, deployment and
ordinary-user acceptance remain necessary. The companion source
and reproducible 0.8.0 ZIP now contain the actual public claim key; publishing
that package alone grants no account or device authority.

The single retained basic job-match request was fulfilled through its original
Cloud Tasks worker, with zero AI calls and zero credits charged. The analysis
succeeded, its dispatch completed and its task retired. All four queues remained
PAUSED; no request was cancelled or purged to satisfy release checks. New release
capture must use the current financial snapshot after this legitimate fulfillment.

## Current deployment state

The preserved-data F7 GCP cutover completed on **16 October at 09:31 UTC**.
The original database remains preserved. Complete source/target comparison
covered **49 tables and 9 sequences** before nine successful additive migrations
to schema18. The target has 74 tables, 969 columns and 9 sequences with verified
runtime/observer privileges. Do not repeat the transfer, migrations, credential
issuance or retirement. The protected backup remains Locked through
**23 October, 08:55 UTC**.

The API revision `ai-resume-parser-00277-kur`, analysis worker
`hirewiz-analysis-worker-00024-foc` and employer worker
`hirewiz-employer-worker-00013-gex` are the existing F7 serving baseline.
Checkout, optional AI and automatic submission remain disabled. All four task
queues are paused. A fresh 21 October read found that employer discovery was
still enabled on the API/employer worker; admission must be closed explicitly
before the source-seeding and release sequence. Do not describe every admission
as closed based only on queue state.

The private resume coordinator is READY with its dedicated numeric database
secret, resource-scoped invocation grants and two PAUSED schedules. Its processing
flags remain false. Fresh accepted scanner definitions and actual upload,
scan and cleanup acceptance are still required; the original definitions expired
on **17 October at 06:24 UTC**. The maintenance service account, GitHub-main WIF,
main-only environment and scoped native maintenance permissions are provisioned.
The maintenance workflow still needs publication and a successful native refresh.

The account/device authority now has its three actual Firestore databases,
locked journal witnesses and two KMS keys. All nine planned runtime IAM grants
have native readbacks. The initial GCS rejection was a definite, transient
new-role propagation failure; only the four remaining grants were subsequently
applied with the original scopes. Three dedicated backend transport secrets and
the matching two Vercel production Secrets are published. The public origin/key
identifier are configured in Vercel. Native runtime flags remain false; no
candidate or device was seeded during bootstrap.

Application Cloud Build **`bdda589b-b364-4189-b08a-5ca216714e0f`** succeeded on
21 October at **16:57:56 UTC** for source **`2bb4ae79`**, producing immutable
digest `sha256:f738f8c805b9652aa80066c12d35add98742596e51f5eea0849d00254576e0f8`.
This verifies the restored builder. It is not a production deployment or a
completed original outcome. The later source still needs complete CI,
schema19, fresh document acceptance, final GCP/Vercel publication and full
customer journeys. The retained old OIDC verification grant was removed; the
replacement narrow, temporary health grant expires on **21 October at 20:00 UTC**
and must be removed when verification finishes.

Production source inventory on 21 October contained **4 sources**, 386 postings
and 353 marked open, with none checked in the prior 24 hours. Current review
verified all twenty pilot employer pages, ATS feeds and tenant joins. Seeding,
full pagination, fresh ingestion and customer search acceptance remain required.
HTTP success alone does not establish fresh or complete job coverage.

## Implemented source and remaining rollout

- **Discovery:** deterministic search/ranking and candidate-selected counts are
  implemented. The [20-origin register](EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md)
  spans three ATS families; production seeding, activation and end-to-end
  acceptance remain. Worldwide coverage and greater-than-95% recall are
  unproven; follow the [coverage audit protocol](COVERAGE_AUDIT_PROTOCOL_2026-10-09.md).
- **Resumes:** document rendering has [native acceptance evidence](DOCUMENT_RENDERING_ACCEPTANCE_2026-10-14.md).
  The restricted upload coordinator has verified SQL access, a READY workload
  and scoped invocation grants; it still needs genuine custody/scan/cleanup checks before the full
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
  acceptance. Ordinary Chrome installation, configured runtime publication and normal-user
  enrollment/pairing remain; native Firestore/KMS authority and configured
  packaging have been provisioned. See
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

Source **`2bb4ae79`** is published in open draft
[PR #23](https://github.com/Harshil4442/ai-resume-copilot/pull/23). Its application
Cloud Build succeeded. [CI run 39638587744](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/39638587744)
passed frontend, security/container and cold-browser jobs; backend document-image
build and the configured-companion UI expectation failed. Subsequent companion and bounded-build diagnostic repairs are committed in
`e1324ef2` and passed focused verification, including one successful controlled
AMD64 document-image build. The old CI build failure was not reproduced; the
pinned base is unchanged. A new integrated CI run is required before release. The previously green
[CI run 39182062183](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/39182062183)
applies only to source **`8eb5f86`**, not the current candidate. These are supporting
checks within the original six outcomes, not additional checkpoints.

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
