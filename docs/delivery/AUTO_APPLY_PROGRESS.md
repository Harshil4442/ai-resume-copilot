# HireWiz development and deployment progress

Updated **23 October 2026 (IST)** after the accepted runtime repair, canonical
preview correction and the funded tailoring diagnosis. The original six outcomes below remain **OPEN** until their
complete development and production acceptance requirements are met within the
owner-approved pilot. A deployed service, a successful migration, source
validation or a passing build is supporting evidence within an outcome; it is
not an additional checkpoint or proof of a complete customer journey.

The runtime proxy, native clock and Gemini request-compatibility corrections are
now deployed. The canonical website's account-availability endpoint returns
**HTTP 200** with both registration options strictly `true`. Fresh ordinary
Google sign-in passed. Native Google verification reached the owner, but human
password submission, completed native enrollment and browser pairing remain
unverified. Existing original/approved-tailored PDF review and a fresh
PDF upload/parse succeeded. The earlier HTTP 402 failure is retained as
historical evidence. After the owner reported a **₹500 AI Studio top-up**, one
bounded UI tailoring request completed two successful Gemini calls, with
**US$0.063133** recorded model cost from actual token usage. Both proposed plans
then failed the deterministic PDF editor with **`unsupported_pdf_text_stream`**.
No version was saved, no user AI units were committed and the balance remains
**7**. Provider funding is now usable; actual wallet balance and final provider
invoice/tax were not independently verified. Local PDF stream diagnosis is in
progress before another paid generation.
Expense-policy secret version **3** is now accepted on all three application
services, with canonical traffic verified and renewal rollout tags cleaned.
Model-policy secret version **2** is unchanged. The finite expense-policy
expiry is **29 October 2026, 20:28:54.855717 UTC**. Prices, economic caps and
retained spending history are unchanged. **The overall goal is not complete.**

| Original outcome | Status | Completed work within this outcome | Remaining acceptance |
|---|---|---|---|
| Official job search, ranking and user-selected job counts | OPEN | Deterministic search/ranking, selected counts and settlement safeguards are deployed. All 20 reviewed origins are enabled and healthy; all refreshed after activation. The recorded aggregate shows 1,366 open postings freshly checked. Authenticated search configuration and mobile layout were observed. | Verify preferences/resume matching, pagination, requested counts, fresh/closed-job handling and per-delivered-result charges/recovery in a completed candidate search. The owner account has no service credits and no paid search was launched. Keep the twenty-origin scope visible; worldwide expansion and an independent >95% coverage assessment remain follow-on work. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | OPEN | Source-preserving document engines, private rendering/scanning, upload coordination and source-selection/review safeguards are deployed. Existing original/approved-tailored PDFs preview and download with one A4 page, nine annotations and visually preserved fonts/sections/layout. A fresh deterministic PDF upload/parse retained byte-identical original download and passed 26 persisted scan/custody checks. After the owner-funded top-up, Gemini successfully generated two plans within one bounded UI request. Both were rejected by deterministic PDF stream editing; no version or customer AI charge was committed and the balance remains 7. | Repair and locally verify `unsupported_pdf_text_stream` before another paid generation; fail deterministic incompatibility before model calls. Complete truthful new tailoring, evidence review, original/custom/tailored selection and deletion/cleanup across PDF, DOCX and LaTeX. The current failure is in PDF text-stream editing; existing PDF review does not establish complete new-generation acceptance. |
| Candidate-reviewed direct company applications | OPEN | Schema19, native Firestore/KMS account/device infrastructure, website transport/pairing configuration and the Razorpay/Greenhouse companion implementation are deployed or packaged. The proxy/clock repair is accepted, canonical account availability is HTTP 200 with both options true, and fresh ordinary Google sign-in passed. Native Google verification was received. The owner reports companion 0.8.0 installed; pairing remains unverified. | Complete human password submission/native enrollment and browser pairing, then verify required/conditional form parity and the candidate-reviewed upload/receipt/recovery journey. Receipt of verification does not prove enrollment or pairing. Actual attachment, native upload, final submission and typed-control gates remain held; real employer writes remain follow-on work. |
| Paid search/application credits, profitable finite packs and payment recovery | OPEN | Three finite packs, separate service-credit/AI-unit balances, exact quotes, reservations, settlement/refunds and interrupted/duplicate recovery are deployed. New unlimited offers are removed. Expense-policy secret version 3 is accepted on all three application services through 29 October; model-policy secret version 2 is unchanged. Prices, expense caps and retained spending history are unchanged. | Paid sales/checkout and real payment/application charging remain held. Production payments, refunds/recovery and actual-cost calibration are unaccepted; estimate-based margins are not proven profit. Resolve the commercial hosting arrangement before selling. |
| Fast responsive pages and SEO | OPEN | PR30 main `523d743d` is canonical on both domains and retains the SEO corrections. Its actual owner UI verifies exact TeX/DOCX source selection and no fallback for an unavailable requested file. Public homepage checks at requested widths of 390, 768 and 1,440 pixels show no horizontal overflow and working mobile navigation. Authenticated search/billing views were also observed at 390 pixels without horizontal overflow. Canonical metadata checks verify pricing-specific sharing metadata, noindex on login/registration and authentication routes absent from the sitemap. | Verify complete authenticated laptop/PC/mobile journeys, production accessibility/SEO and measured loading performance. The observed pages and server timings do not establish complete usability acceptance, Core Web Vitals or mobile field-data performance. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Preserved-data cutover and schema19 migration remain intact. PR28 runtime fixes are accepted: API/analysis retain the exact c67d144 image; PR30 frontend deployment wX38 is canonical on both domains. Expense-policy-3 revisions now serve 100% on all three application services, with renewal tags cleaned and model policy 2 unchanged. Fresh ordinary Google sign-in and canonical account availability passed. Consumer queues/schedules, reviewed-source ingestion and selected owner-inbox monitoring delivery were previously verified. | Complete human native enrollment/pairing, repair and verify truthful new tailoring and the remaining pilot journeys. Verify unexercised alert routes and required operational acceptance. Preserve commercial and employer-write holds; deployment completion alone does not close all six outcomes. |

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

The accepted runtime repair is pull request **28**, tested at
**`efb35866dd850341e4f4f1f08da67323f5269d88`** and merged as
**`88ed491742030a8553c2419d9340a803b397e572`**. The merged and tested Git trees
are identical. API and analysis use immutable image digest
`sha256:c67d144ee7a02efa7df44ee78babf1ad3b562bc2f8d5c12e81a32804117eb64e`.
At the runtime-repair acceptance boundary, both served 100% canonical traffic:

- `ai-resume-parser-runtime-a30b52b32136`
- `hirewiz-analysis-worker-runtime-a30b52b32136`

Their old runtime rollout tags were cleaned after acceptance. The employer
worker retained its reviewed e361 image and behavior. The private coordinator
retains its reviewed F7 runtime image; the runtime repair did not replace it.
Prior revisions remain available for rollback. No schema, customer balance or
spending history was reset by this repair.

READY Vercel deployment **`dpl_A8eCkxaZjvK3d7rAVUKYTPodqQKV`** was promoted
once. Fresh actual alias records verify both `www.hirewizhq.com` and
`hirewizhq.com` on that exact deployment, with the existing apex-to-www 308
redirect retained. Automatic custom-domain assignment remains disabled. At
**22 October, 20:42:41 UTC**, the canonical same-origin account-availability
GET returned HTTP 200 and exactly `fresh_registration: true` and
`legacy_enrollment: true`.

PR30 subsequently merged as **`523d743d02fbb09037a2aca88cb0e07d3d714203`**,
with the same Git tree as its tested head. Production deployment
**`dpl_wX38bqW2nyU8QjkV1i8MKhqKp4WH`** was promoted once and both actual
domain records point to it; the apex 308 redirect and disabled automatic domain
assignment are retained. At **22 October, 22:23:49 UTC**, canonical account
availability again returned exactly both options `true`. Actual authenticated
UI checks verified unavailable-ID, TeX31 and DOCX30 selection without another
file's download controls. GCP images, database and expense/model policies were
unchanged by this frontend correction.

The following configuration-only expense-policy renewal is accepted. All three
services use expense-policy secret version **3** and unchanged model-policy
secret version **2**, retain their existing images and prices, and serve 100%
canonical traffic on these revisions:

- `ai-resume-parser-expense-855b625c68d8`
- `hirewiz-analysis-worker-expense-855b625c68d8`
- `hirewiz-employer-worker-expense-855b625c68d8`

Publication, staged verification, all three promotions, joined canonical
acceptance and renewal-tag cleanup passed. The expense-policy window ends
**29 October 2026, 20:28:54.855717 UTC**. Retained expense-policy-2 revisions
are not usable after **23 October, 05:07:52 UTC** unless the rollback
configuration attaches a valid reviewed expense policy. Keeping an old revision
does not extend its policy authority. No balance, spending history, price or
economic cap was reset by the renewal.

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
the original resume and previously approved tailored PDF both
previewed and downloaded successfully. Each PDF has **one A4 page and nine
annotations**. Visual comparison preserved the fonts, section structure and
layout. This establishes limited acceptance of existing PDF preview/download.

A subsequent fresh upload of the same **122,522-byte original PDF** created
a fresh source record through the canonical site. The UI progressed through reserved
space, safety checks and extraction to **Resume parsed**. The operation used
deterministic extraction without an AI call, and the balance remained **7 AI
units** before and after. The UI retains the original source PDF. The new
original download is byte-identical to the upload. Subsequent read-only metadata
observation verified all 26 persisted receipt/custody checks; it did not retrieve
current object metadata. That observation did not establish new tailoring or
DOCX/LaTeX upload acceptance.
Private PDFs, rendered pages, the fresh-upload screenshot and
`fresh-upload.result.json` remain in the operator's `owner-resume-review` evidence
directory outside this repository.

Later canonical checks uploaded synthetic **36,890-byte DOCX** and **933-byte
TeX** resumes. Both parsed deterministically, with the AI balance unchanged at
**7**. The DOCX's owner-bound receipt records release after one ClamAV inspection
attempt, with no error and matching source size/hash. Both original downloads
are byte-identical to their inputs. The TeX source uses the existing separate
deterministic route; this is not evidence of TeX quarantine/scan, compilation,
new tailoring or deletion/cleanup acceptance.

That journey exposed a transient preview-selection defect: the URL for the new
TeX source briefly displayed the previous cached DOCX while its list refreshed.
The frontend correction binds explicit resume requests to that exact source,
refreshes the list on entry and resets selector overrides when owner or document
changes. Missing or malformed explicit IDs cannot select another file. Deferred
list and late-PDF-response regressions cover this boundary. This source change
does not establish a successful new tailoring operation.

### Separate native upload work awaiting acceptance (PR31)

The existing outcome 2 includes this deterministic upload work alongside PDF
stream repair and pending human enrollment. Separate [PR31](https://github.com/Harshil4442/ai-resume-copilot/pull/31) routes TeX/ZIP through the
existing owner-bound quarantine and cleanup chain. Source inspection precedes
project validation and isolated compilation; the exact compiler PDF receives
private inspection/extraction before candidate facts or a new seal are released.
The pure project validator is separated from compiler/transport code so it can
be included in the secretless inspector image without application clients.

The frontend uses direct native upload with exact format/MIME/hash/size checks;
it refuses new native uploads when secure upload is unavailable. Existing native
original downloads retain their owner-bound legacy compatibility. New native
multipart parses are refused to prevent bypassing quarantine. Service outages
remain retryable during upload processing; source refusals schedule cleanup.
An inspector/compiler outage during tailoring stops the run and waives customer
units instead of regenerating paid AI proposals or requeuing the same run. Original bytes, cancellation
leases, generation-pinned writes and deletion obligations remain intact.

Migration **`20261023_0020`** expands only the existing upload-format constraint.
Local PostgreSQL checks cover retained PDF data through upgrade/downgrade,
refusal of a lossy downgrade once native cleanup history exists, late results
after cancellation and detached cleanup after resume deletion. Focused frontend
checks cover native transport and source identity. New artifact preparation also
retains metadata for original/candidate source and PDF inspection while preserving
historical sealed downloads.

This separate implementation remains unmerged and is awaiting corrected
expanded-corpus/component fixture acceptance,
reviewed native image/policy rollout, schema20 application, coordinator/API
deployment and canonical synthetic TeX/ZIP acceptance with exact sealed review
and cleanup evidence. Production remains on schema19 and its previously accepted
scanner until that rollout is complete. No new full checkpoint is closed, no AI
test was initiated by this native implementation, and no paid sales or employer
writes were activated.

The GCP notification channel routes to the owner's selected signed-in Google
account. Definition attention, missing heartbeat, API 5xx and latency policies
are configured; the subsequent terminal Cloud Build operation alert reuses that
same owner channel. The new Cloud Build rule covers its matching future terminal
operations, not every possible build or deployment failure.

Read-only owner-mailbox evidence verified actual **INBOX** delivery of GCP
scanner-definition attention open/resolved messages and GitHub maintenance/main
CI failure messages. An attention-resolved email alone does not prove scanner
recovery; the separate successful native refresh provides that evidence.
Actual missing-heartbeat firing and prior resolved notices were subsequently
found in the owner's INBOX. The latest incident opened at **22 October,
21:37:10 UTC** after no hourly GitHub monitor runs materialized following the
successful 18:31 run. Existing monitor-only run **39860463269** then succeeded
at **21:51:29 UTC**, reverified the current inspector pin/accepted native receipt,
and emitted a genuine fresh heartbeat observed in native metrics. Refresh,
build and pin-transition steps were skipped. Incident closure and subsequent
cron cadence were not established by that observation. GitHub documents that
[scheduled events can be delayed or dropped](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule);
the cause of this particular run-creation gap remains unknown.

Vercel failure and the new native Cloud Build rule's live delivery remain
unexercised. API 5xx/latency policies are configured, without retained
condition-specific incident/inbox proof. No synthetic heartbeat, forced
production failure or test email was used to create this evidence.
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

Fresh ordinary Google sign-in now succeeds on the canonical website and
reaches the authenticated dashboard. The repaired same-origin availability
endpoint returns HTTP 200 with both registration options true. Native Google
verification was received, but the human password submission and completed
native enrollment remain unverified. The owner reports installing companion
0.8.0; independent pairing and the complete browser-assisted journey are still
pending. Do not seed an account/device, replace the owner's identity, invent
credentials or bypass ordinary proof. The earlier proxy/clock failures remain
retained as historical failures; the accepted successor and its bounded signed
handshake are documented in the [runtime diagnosis](RUNTIME_READINESS_2026-10-22.md).

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

The Gemini request-compatibility correction is deployed: Gemini 3+ requests
omit unsupported `candidate_count` while preserving output-token and budget
limits. Before the top-up, one post-repair owner-approved tailoring run reached Gemini
and failed with **HTTP 402** before creating a change plan or editing the document.
At that historical observation, AI Studio showed **Tier 1 Prepay / No credits**
and required a positive provider balance to resume service. The two reserved AI units were
waived, the verified balance stayed at **7**, and no new resume version was
saved. There was no automatic retry. No provider usage receipt was returned,
so the provider-cost outcome remains unknown rather than assumed free. This
billing refusal does not demonstrate a format-preservation failure. Internal
HireWiz credit adjustments cannot fund Google's provider account.

After the owner reported the **₹500** provider top-up, one new UI request
(`run_ff82dad704884d9b9cfdf2305b0d7d65`) reached **`gemini-3.6-flash`** and
completed two bounded generation attempts. Both have real token-usage receipts;
the recorded combined model cost is **63,133 USD micros (US$0.063133)**, not a
verified invoice total. The deterministic editor rejected both attempts with
**`unsupported_pdf_text_stream`**. The run terminated with waived user units,
balance **7**, and no saved version. No second UI generation request was made.
The earlier HTTP 402 run and its unknown provider-cost outcome remain unchanged.
The AI Studio wallet widget did not expose a usable balance; successful real
requests establish usable funding without asserting the remaining rupee balance.

A successful new generation remains unaccepted. Diagnose and repair the PDF
stream incompatibility locally and preflight deterministic support before another
paid attempt. Preserve original/custom-file alternatives, evidence checks,
exact quote and finite expense controls. The agent did not make a provider
purchase, enable auto-reload or change billing settings.


The minimal PDF correction keeps the existing scanner image/policy and schema19.
On the exact original PDF, a local trimmed passage reproduces the same public
stream error because native cursor precision exceeds the retained **0.1 point**
limit. A separate changed passage passes the complete fidelity checks. The
original failed run's proposals were not retained, so this reproducer does not
prove its internal stream-error subtype.

The operations correction probes at most twelve proposed edits, once per run,
when an unattributed PDF stream refusal occurs. It discards only individually
rejected changes and verifies the complete surviving batch before saving. No
survivors, interacting failures or failed final validation terminate without
another model call or an unchanged tailored version. The font, geometry, pixel,
source-content and cursor-precision guards remain intact. This is source under
review; production and successful real new tailoring acceptance remain pending.

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
The later PR28 frontend promotion completed in **5.52 seconds** at
**22 October, 20:37:48 UTC**, followed by independent canonical readback.
These independent phase timings must not be summed into an end-to-end duration
or presented as completion of a whole outcome.

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
