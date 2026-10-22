# HireWiz development and deployment progress

Updated **22 October 2026, 19:20 UTC**; runtime observations below include the
subsequent owner upload, frontend promotion and native-clock diagnosis. This replaces the earlier
21 October operational state. The original six outcomes below remain **OPEN**
until their complete development and production acceptance requirements are met
within the owner-approved pilot. A deployed service, a successful migration,
source validation or a passing build is supporting evidence within an outcome;
it is not an additional checkpoint or proof of a complete customer journey.

The current application is deployed on GCP and Vercel. The twenty reviewed
employer sources have refreshed successfully. The owner's existing Chrome session
reached the authenticated dashboard with the preserved balance of **7 AI units**.
Existing original and previously approved tailored PDFs were previewed and
downloaded successfully. A fresh owner PDF upload and deterministic parse also
succeeded, with the balance unchanged at 7 AI units. Its persisted scan/custody
receipt subsequently passed all 26 owner-bound metadata checks. Fresh Google sign-in, native
enrollment and the complete resume, pairing, paid-search and application journeys
remain unaccepted. The HTTPS proxy and clock-hint corrections are staged at zero traffic.
Actual verification still returned HTTP 503: two successive authenticated clock
hints moved backward by 19,725 milliseconds. Firestore document read timestamps
cannot serve as the assumed fresh wall clock. A server-acknowledged clock-event
correction and a Gemini request-parameter correction are being prepared together.
Neither has been promoted. **The overall goal is
not complete.**

| Original outcome | Status | Completed work within this outcome | Remaining acceptance |
|---|---|---|---|
| Official job search, ranking and user-selected job counts | OPEN | Deterministic search/ranking, selected counts and settlement safeguards are deployed. All 20 reviewed origins are enabled and healthy; all refreshed after activation. The latest aggregate shows 1,366 open postings freshly checked. | Verify the authenticated candidate journey: preferences/resume matching, pagination, requested counts, fresh/closed-job handling and per-delivered-result charges/recovery. Keep the twenty-origin scope visible. No worldwide or >95% coverage claim is established. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | OPEN | Source-preserving document engines, private rendering/scanning, the upload coordinator and the deployed source-selection/review safeguards are available. Fresh scanner acceptance and genuine scheduled scan/cleanup endpoint successes are recorded. Optional AI capability is enabled behind reviewed cost/consent/quote controls. The owner's existing original resume 28 and previously approved tailored version 2 previewed and downloaded on the canonical site; both retain one A4 page and nine annotations, with fonts, sections and layout visually preserved. A fresh PDF upload created resume 29 and completed the reservation, safety-check, extraction and parsed UI stages without an AI call; the balance stayed at 7 AI units. Its original-download byte identity and 26 persisted scan/custody checks passed. | Complete approved-evidence review, new truthful tailoring, original/custom/tailored selection and deletion/cleanup across the supported formats. Existing PDF review and the new PDF parse do not establish new generation or complete PDF/DOCX/LaTeX acceptance. Scheduled HTTP 200 responses alone do not prove processing or deletion of an actual resume. |
| Candidate-reviewed direct company applications | OPEN | Schema19, native Firestore/KMS account/device infrastructure, website transport/pairing configuration and the configured Razorpay/Greenhouse companion implementation are deployed or packaged. The API account/ingress/pairing and basic assistance capabilities are enabled. The owner's existing Chrome session reaches the authenticated dashboard. | Publish and verify the staged proxy and new clock-synchronization corrections; independently verify fresh Google sign-in and native enrollment, companion installation/pairing, required/conditional form parity and the candidate-reviewed upload/submission/receipt/recovery journey. The account-enrollment page is unavailable and presents no password fields. Actual attachment, native upload, final submission and typed-control gates remain held; no real candidate submission acceptance is claimed. |
| Paid search/application credits, profitable finite packs and payment recovery | OPEN | Three finite packs, separate service-credit/AI-unit balances, exact quotes, reservations, settlement/refunds and interrupted/duplicate recovery are implemented in the deployed source. Numeric expense/model policies version 2 are installed with the matching application. New unlimited offers are removed. | Paid sales and checkout remain held. Production payment, paid-search/application charging, refunds/recovery and actual-cost calibration remain unaccepted. Estimated margins are not proven profit. Resolve the commercial hosting arrangement before commercial activation. |
| Fast responsive pages and SEO | OPEN | The SEO-corrected main-tree frontend is promoted. The live public homepage renders without horizontal overflow at 390, 768 and 1,440-pixel requested widths; mobile navigation opens and closes. Canonical HTTP checks verify pricing-specific sharing metadata, noindex on login/registration and authentication routes absent from the sitemap. | Verify complete authenticated laptop/PC/mobile journeys, current production accessibility/SEO and measured loading performance. A healthy homepage or earlier lab measurement does not establish complete production performance acceptance. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Preserved-data cutover, schema19 migration, the final application rollout, frontend promotion, coordinator activation, clean traffic routes, resumed consumer queues/schedules, genuine scheduled successes, fresh source ingestion and live monitoring are recorded. Selected GCP/GitHub alerts have reached the owner's Google inbox. | Resolve the native availability clock rejection, verify fresh Google sign-in/native enrollment and complete production resume, search, pairing and candidate-approved application/recovery journeys. Verify the still-unexercised notification routes and required operational acceptance. Preserve the paid-sales hold. |

## Scope and owner controls

On **16 October**, the owner selected the clearly labeled pilot: twenty reviewed
employer origins and the Razorpay/Greenhouse application portal first. Worldwide
expansion and an independent >95% coverage assessment are follow-on work.
Discovery coverage and completed-application coverage are separate measures.

The owner authorized continued prelaunch deployment on the existing free Vercel
plan and plans to upgrade when selling. No Pro purchase or paid-sales activation
has been performed or is authorized by this continuation. Checkout remains off.
Provider eligibility must be resolved before commercial activation; the owner
instruction does not change provider terms. See the existing
[operations guide](../../infra/gcp/README.md) and
[full acceptance requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md).

Report free-prelaunch pilot acceptance separately from the intentionally held
commercial and real-employer actions within these same six outcomes. A real
payment or employer application must not be created as a smoke test. Existing
controlled browser/database/provider-boundary fixtures cover accounting,
submission and recovery behavior; they are not production payment or employer
receipts. The remaining base-pilot journeys include ordinary enrollment/pairing,
truthful current tailoring and authenticated usability. The sales hold is not a
reason to repeat migrations or keep deploying the same source.

## Current deployed state

The backend application source remains **`e36152ee4e991315c76f4baccb39b0363b966271`**.
Its immutable application digest is
`sha256:8516787317856d33080435478d772700b37930680fcf996cb26332e5340529ac`.
The SEO update merged as **`103e3baec4e6c44f3abb9ea81c4eb4e3f86f5f85`**;
its tree matches the tested pull request. READY Vercel deployment
**`dpl_3kp8p6xBzvJB2czYxeyoJ5yCk8bd`** was promoted and checked on the
canonical site. The preceding deployment remains available for rollback.
The canonical site and backend health returned HTTP 200. Full user-journey
acceptance is still pending.

The API, analysis and employer services now serve their respective activation
revisions at 100%, with one untagged current route each:

- `ai-resume-parser-activate-fb938848688e`
- `hirewiz-analysis-worker-activate-7fb74714188f`
- `hirewiz-employer-worker-activate-a89540c935a0`

The private coordinator serves
`hirewiz-resume-coordinator-act-a182af50858b`. It intentionally retains its
reviewed immutable F7 runtime image with the required configuration/flag changes;
it is not a new e361 coordinator image. Prior revisions remain retained.
Obsolete rollout/test tags were removed after all three application promotions;
that cleanup changed traffic only, not images, templates, IAM, secrets or flags.
The temporary health-verification grant was removed and its absence reconciled
without replaying the acknowledged removal.

Discovery and the analysis/employer consumers are active. Optional AI capability
is enabled on the API and analysis worker only; actual AI work still requires
its finite balance, current cost authority, consent and exact accepted quote.
Basic source matching, feed refresh, deterministic ranking and accounting do not
require an AI call. Coordinator upload/scan capabilities are enabled. The
application queue, automatic submission, checkout, browser attachment/native
upload/final-submit and typed-control gates remain held.

The analysis, employer ingestion and employer search queues resumed. The existing
scan, cleanup and maintenance schedules resumed. At **21 October, 22:30 UTC**,
all three produced matching Scheduler HTTP 200 and endpoint-specific Cloud Run
POST 200 observations on the actual serving revisions. These were genuine
scheduled runs, with no forced invocation. They may represent idle maintenance;
they do not prove an actual owner's resume was scanned or deleted.

## Live employer catalogue

The audited seed created sixteen sources and updated the original four,
preserving their IDs, the original 386 postings and retained customer/dispatch
state. After activation, a read-only aggregate at
**21 October, 22:36:34 UTC** verified:

- **20/20** reviewed sources enabled, healthy and successfully refreshed since
  the activation boundary; no source error code or active/expired scan claim.
- **20** new refresh dispatches completed. Actual successful source state was
  checked separately from dispatch completion.
- **1,366 open postings**, all freshly checked; **1,056 first seen** since the
  activation boundary. The open total is not a count of newly created postings.
- **46 postings closed** since the boundary; 1,442 total retained postings and
  76 marked closed in the complete snapshot.

This establishes live freshness for the twenty-origin catalogue. It does not
establish candidate-search/ranking/payment acceptance or worldwide recall.
See the [reviewed origin register](EMPLOYER_ORIGIN_REGISTRY_2026-10-13.md) and
[coverage protocol](COVERAGE_AUDIT_PROTOCOL_2026-10-09.md).

## Preserved data, documents and monitoring

The preserved-data F7 cutover completed on **16 October at 09:31 UTC**. Its
source/target comparison covered 49 tables and 9 sequences before the additive
schema18 migrations. The original database and the retained protected backup
remain preserved. Do not repeat the data transfer, credential issuance,
retirement or completed migrations as part of ordinary updates.

Schema19 was committed and freshly observed with retained data/ACL checks. The
closed-admission application rollout then completed with a joined schema19
observation. These operations did not authorize sales or complete the six user
outcomes. The original failed transaction journal, its known-rollback evidence
and all subsequent operation journals remain retained.

Document refresh run **`39662737698`** produced accepted inspector digest
`sha256:240f6df0df2c82d69067e162ca40870ab77069a0b80e3d8b64abcda312b5a78d`;
the inspector and all three caller pins were verified. Its definition validity
ends **24 October at 06:25:49 UTC**; use the existing maintenance workflow for
successors without extending stale definitions. The genuine refresh event and
an actual heartbeat metric point were observed. The heartbeat lacks a run/image
label, so its correlation with the accepted refresh comes from the independent
serving/artifact proof. See [document maintenance](DOCUMENT_DEFINITION_MAINTENANCE.md).

Using the owner's existing authenticated Chrome session on the canonical site,
the original resume **28** and previously approved tailored **version 2** both
previewed and downloaded successfully. Each PDF has **one A4 page and nine
annotations**. Visual comparison preserved the fonts, section structure and
layout. This establishes limited acceptance of existing PDF preview/download.

A subsequent fresh upload of the same **122,522-byte original PDF** created
resume **29** through the canonical site. The UI progressed through reserved
space, safety checks and extraction to **Resume parsed**. The operation used
deterministic extraction without an AI call, and the balance remained **7 AI
units** before and after. The UI retains the original source PDF. The new
original download is byte-identical to the upload. Subsequent read-only metadata
observation verified all 26 persisted receipt/custody checks; it did not retrieve
current object metadata. No new tailoring generation or DOCX/LaTeX upload
acceptance is claimed.
Private PDFs, rendered pages, the fresh-upload screenshot and
`fresh-upload.result.json` remain in the operator's `owner-resume-review` evidence
directory outside this repository.

The GCP notification channel routes to the owner's selected signed-in Google
account. Definition attention, missing heartbeat, API 5xx and latency policies
are configured; the subsequent terminal Cloud Build operation alert reuses that
same owner channel. The new Cloud Build rule covers its matching future terminal
operations, not every possible build or deployment failure.

Read-only owner-mailbox evidence verified actual **INBOX** delivery of GCP
scanner-definition attention open/resolved messages and GitHub maintenance/main
CI failure messages. An attention-resolved email alone does not prove scanner
recovery; the separate successful native refresh provides that evidence.
Vercel failure delivery, the new native Cloud Build rule's live delivery and an
actual missing-heartbeat incident remain unexercised. No synthetic heartbeat,
forced production failure or test email was used to create this evidence.
Preserve the notification channel and policy resource handles listed in the
[operations guide](../../infra/gcp/README.md).

## Public-page checks and metadata follow-up

The live e361 homepage was visually checked at requested viewport widths of
390, 768 and 1,440 pixels. Each had one main heading, no horizontal overflow and
a working responsive layout. The mobile navigation opened and closed. The
browser override was reset afterward. This covers the public homepage, not the
signed-in application forms or complete accessibility acceptance.

One anonymous HTTP GET per route from the owner network measured first-byte
times of 125 ms for the homepage, 608 ms for pricing and 78 ms for login, with
all three returning HTTP 200. Cache state was uncontrolled. These are server and
transfer timings, not browser rendering, before/after improvement, Core Web
Vitals or mobile field-data scores. The separate official PageSpeed mobile lab
request returned HTTP 429; no score is claimed and it was not repeatedly retried.

The bounded public metadata audit found login/registration in the sitemap,
missing sign-in `noindex` and pricing inheriting homepage Open Graph metadata.
The frontend-only correction in this revision removes those authentication
routes from the sitemap, adds their `noindex,nofollow`, and gives pricing its own
Open Graph title, description and canonical sharing URL while retaining the
share image. The update is published; fresh canonical HTTP checks verify all three corrections. Backend images,
credentials, authentication behavior and layout are unchanged by this correction.

## Remaining owner acceptance

The owner's existing Chrome session is authenticated and shows the preserved
dashboard balance of **7 AI units**. The fixed authenticated eligibility endpoint reports
`GOOGLE_ENROLLMENT_AVAILABLE`, confirming the existing Google session. A fresh
Google callback has not been exercised in this continuation. The earlier rejected saved-password attempt is not
the current blocker, and no human password handoff is required by this evidence.
`/account-enrollment` is unavailable and shows no password fields. At
**21 October, 22:53 and 22:55 UTC**, backend availability checks returned
**HTTP 403** on `ai-resume-parser-activate-fb938848688e`. The deployment's proxy
configuration is being repaired. A canonical HTTPS GET to the known public
`/api/public/optimize_bullet/` route returned a 307 redirect with an HTTP location,
confirming that the application observes an HTTP ASGI scheme. The API lacks the
trusted-proxy setting for the observed Cloud Run peer; the exact-peer
configuration repair is staged with zero production traffic. Its health and
HTTPS redirect checks pass, and unsigned requests remain rejected. A fresh signed
availability check reaches the native guard but returns HTTP 503. Structural
configuration and signature-input binding checks pass. A bounded diagnostic
measured the signed issue time 11,747 ms ahead of the native provider-clock lower
bound. The temporary diagnostics were restored. The new source adds a bounded,
authenticated availability-only clock synchronization without loosening the
five-second expiry, custody checks or durable replay protection. Its deployment
and enrollment acceptance remain pending. Actual staged verification then found
that two fresh hints moved backward by 19,725 milliseconds. The hint mechanism
cannot compensate for a database snapshot timestamp that is not a reliable wall
clock; its failed verification remains retained and no traffic was promoted.
The next correction replaces that clock assumption with a fresh acknowledged
server event while preserving the existing expiry and replay guards. See the
[runtime diagnosis](RUNTIME_READINESS_2026-10-22.md).
Do not seed an account/device, replace the owner's identity, invent credentials
or bypass ordinary proof.

Existing original/approved-tailored PDF preview and download, plus a fresh owner
PDF upload and deterministic parse, succeeded as described above. The new
upload's original preview/download also succeeded, and the downloaded bytes
exactly match the uploaded 122,522-byte PDF. A read-only owner-bound observation
also passed all 26 persisted receipt/custody checks, including storage generation
pins, completed release and recorded quarantine cleanup. This did not independently
retrieve current object metadata. A new successful tailoring run,
other supported formats, ordinary browser pairing, paid search and candidate
submission remain unaccepted. Complete those
genuine journeys within the existing consent/price/credit/permission controls.
Keep unavailable submission capabilities visibly unavailable until their
respective acceptance and owner review are complete.

The fresh tailoring attempt failed before document editing and returned its
reserved AI units. A separate source review identified a definite request-contract
defect: Gemini 3+ does not support `candidate_count`, which the client always sent.
The compatibility fix omits that parameter for Gemini 3+ and aliases while retaining
the existing output-token cap and budget admission. Its focused regression checks
actual SDK request serialization without making a generation request. The old
generic error does not prove this was the only cause of that historical failure;
a new successful owner-reviewed generation remains required.

The three finite offers remain Starter **₹649 / 100 service credits + 2 AI
units**, Growth **₹1,099 / 300 + 6**, and Scale **₹2,199 / 700 + 15**. Deployed
prices are one service credit per newly delivered qualifying job, twenty per
confirmed complete assisted application, and two AI units for optional tailoring.
Unknown submission outcomes retain their hold for reconciliation and never
justify another POST. These are finite estimate-based offers, not an assertion
that production payments or profit have been accepted. See
[finite packs](FINITE_PACK_IMPLEMENTATION_2026-10-09.md) and
[cost policy](COST_PRICING_POLICY_2026-10-09.md).

## Timing and operator lessons

There is **no dependable remaining completion ETA**. Original per-outcome
estimates and measured whole-outcome durations were not recoverable from the
retained reporting documents/history. Do not invent them or reuse the withdrawn
conditional 2–4-hour release estimate. When one of these same six outcomes meets
its full acceptance, report its recorded estimate if available, measured elapsed
time and identifiable waits against that row.

Measured operation times are supporting evidence, not whole-outcome completion
times: schema19 migration **540.78 seconds**; closed-admission rollout
**1,761.94 seconds**; coordinator promotion **19.46 seconds**; read-only analysis
promotion reconciliation **18.27 seconds**; employer promotion **26.88 seconds**;
API promotion **65.22 seconds**; final traffic-tag cleanup **67.90 seconds**.
These independent phase timings must not be summed into an end-to-end duration.

- Run the Vercel CLI from a responsive local working directory. In this rollout,
  `vercel` 64 `api --help` from the Desktop repository hung for over two minutes
  at zero CPU and that read-only process was terminated; the identical command
  with its working directory set to `/tmp` completed in **0.082 seconds**.
  Subsequent Vercel API/promotion commands from `/tmp` succeeded. This is an
  observed working-directory-dependent hang; the particular underlying file or
  filesystem cause was not proved. Do not turn this into a `TMPDIR` diagnosis.
  Historical CLI flag-forwarding mistakes are a separate issue.
- Compare immutable revision configuration with the original reviewed template
  and same-plan journal. Native activity/scaling status can legitimately change
  after traffic moves; a full live Revision JSON hash is not an immutable
  configuration identity. Retain the original full hash as audit evidence.
- An untagged zero-traffic candidate can be Ready while the service's
  `latestReadyRevision` remains the old 100% serving revision. Reconcile the
  original acknowledged stage against the exact candidate/configuration and old
  route; do not restage. Postpromotion still requires the expected serving
  candidate and exact traffic/configuration proof.
- Google RPC success code zero can be omitted in protobuf JSON. Handle that
  omission only in the exact known status schema; retain independent matching
  Scheduler/Cloud Run evidence. Do not classify arbitrary missing fields,
  unknown operations or incomplete acknowledgements as success.
- An acknowledged mutation followed by a readback mismatch requires
  reconciliation of that same original operation. Keep intent/ACK journals and
  exact preimages; never create a new folder, repeat a promotion or issue another
  request merely to obtain a cleaner report.

Earlier deployment states, expired scanner pins, paused schedules, previous
candidate builds and validation counts are historical context, not the current
state. Preserve their records and use the existing
[historical progress archive](archive/AUTO_APPLY_PROGRESS_THROUGH_F6_2026-10-15.md)
and [validation history](archive/AUTO_APPLY_VALIDATION_HISTORY_THROUGH_2026-10-13.md)
for earlier evidence. Builds and regressions remain supporting verification,
not additional delivery checkpoints.
