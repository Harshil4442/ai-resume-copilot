# HireWiz development and deployment progress

Updated **24 October 2026 (IST)** after the schema21 rollout, pilot reopening and
canonical frontend promotion. Original outcomes **1, 2 and 4 are COMPLETE within
the approved free twenty-origin pilot**; the other three remain **OPEN**. Global
coverage, sales, real payments and commercial profitability remain follow-on
work. The goal remains unfinished until the other pilot outcomes are accepted.

Schema **`20261023_0021`** is committed. The API, analysis worker, employer worker
and resume coordinator now serve accepted source **`7a00839d`**, immutable image
**`c1ce5b32`**. Reopening is accepted: the three reviewed worker queues are RUNNING
and the three existing schedules are ENABLED. The employer-application queue
remains PAUSED; sales, checkout and employer submission remain held.

Production frontend **`dpl_BRu51zQTN7zUESLPML34PdvB8eMg`** contains the
upload-reconciliation correction from source **`7529c041`**, merged normally as **`533fc005`**. It was promoted at
**23 October 2026, 22:46:17 UTC**; both canonical aliases were verified at
**22:46:23 UTC**. It supersedes the earlier H18/f3 billing release. Actual
password reset, native enrollment and fresh native password sign-in have passed;
companion pairing remains under diagnosis. The final canonical ZIP upload passed,
and its downloaded original matched the uploaded bytes exactly at
**23 October 2026, 23:31:41 UTC**.
A single fresh native-password-session upload POST received upstream **503** at
**22:48:59.327 UTC**, before a known upload ID was returned. Browser-device
preparation also received upstream **503** at **22:42:19.214 UTC**, without a
known creation or pairing ID. A bounded database lookup found no committed upload
for the failed request; only after that reconciliation was one fresh upload made.
These failures are separate from the deployed 429 status-reconciliation correction.
The existing companion attempt must still be reconciled before a fresh connection.
Deployment alone does not close an outcome.

## Scope and owner controls

The owner-approved **free-prelaunch pilot** covers twenty reviewed employer
origins and candidate-reviewed Razorpay/Greenhouse assistance. Worldwide expansion
and an independent greater-than-95-percent coverage assessment remain follow-on
work. Discovery, preparation, assistance and confirmed submission are separate
measures.

Paid sales/checkout and real employer writes remain held. No real payment or
employer application may be created as a smoke test. Existing controlled fixtures
cover accounting, submission and recovery boundaries; they are not production
payment or employer receipts. Commercial activation requires its own hosting,
provider and economic acceptance. These deliberate holds remain separate from
unfinished free-pilot journeys within the same six outcomes.

| Original outcome | Status | Accepted evidence within this outcome | Smallest remaining pilot acceptance |
|---|---|---|---|
| Official job search, ranking and user-selected job counts | COMPLETE (free pilot) | Twenty reviewed origins were enabled and refreshed; the retained aggregate recorded 1,366 open postings at that observation. The earlier wrong-role search and its separate audited quality adjustment remain preserved. After reopening, a genuine three-result Software Engineer search returned **3 of 3** appropriate engineering roles. Its quote was **1 credit per job**; it charged **3**, released **0**, left **0 held** and **0 available**, while AI stayed **7**. | Approved free-pilot search acceptance is complete. Worldwide expansion and the independent greater-than-95-percent coverage target remain follow-on work. Preserve exact completed search and settlement history; no additional testing campaign is implied. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | COMPLETE (free pilot) | Real PDF Version 3 retained four safe edits and omitted one unsafe proposal; its download preserves one page, nine links, fonts and 147 unedited positions. Native DOCX, TeX and TeX ZIP original downloads match their source hashes. The unchanged DOCX snapshot is byte-identical. TeX and ZIP sealed PDFs were visually checked and matched to their previews; both retain two links. The exact three database records pass source, ClamAV, native-compilation and recorded cleanup-generation checks. After reconciling the failed POST as uncommitted, one fresh canonical ZIP upload parsed successfully and its original download matched all 917 bytes. | Approved free-pilot resume acceptance is complete. An independent release-user GCS marker read remains permission-unavailable and is not claimed as PASS; no IAM expansion is required by SD03. Unsupported layouts still receive conservative refusal or original/custom options. Unchanged snapshots remain labeled unchanged and do not prove new tailoring. |
| Candidate-reviewed direct company applications | OPEN | Ordinary Google sign-in passed; the human reports foreground sign-in within five seconds. The human completed password reset; the password-changed email reached INBOX at **23 October 2026, 22:28:41 UTC**. Native enrollment and a fresh native password sign-in both reached the Dashboard. The first reset email also reached INBOX with SPF, DKIM and DMARC passing. Companion 0.8.0 installation is reported. A single browser-device preparation attempt received upstream 503 at **22:42:19.214 UTC**, without a known creation or pairing ID. | Diagnose/reconcile the existing companion attempt without another Start or write, then verify candidate-reviewed Razorpay assistance/recovery within the held employer-write controls. Do not seed an identity/device or bypass proof; employer attachment, upload and final submission are not smoke tests. |
| Paid search/application credits, profitable finite packs and payment recovery | COMPLETE (free pilot) | Three finite packs, separate service/AI balances, exact quotes, reservations, idempotent settlement and recovery controls are implemented. The promoted billing page displayed the actual **3** service credits. The quote of **1 per job** delivered **3 of 3** Software Engineer roles, charged **3**, released **0**, and left **0 held / 0 available**; AI remained **7**. Expense-policy **3** and model-policy **2** retain their reviewed authority. | Approved free-pilot credit, quote and settlement acceptance is complete. Paid checkout, real payment/refund receipts, commercial activation and actual-cost profitability remain explicitly unaccepted follow-on work. Unknown provider liabilities must not become zero cost. |
| Fast responsive pages and SEO | OPEN | Public views were observed at 390, 768 and 1,440 pixels without horizontal overflow; signed-in search/billing views were also observed at 390 pixels. Fresh signed-in workspace and resume review observations at 320 and 1,024 pixels show no horizontal page overflow. Keyboard focus, mobile navigation and horizontally scrolling section access passed. Exact-source preview and canonical pricing/auth metadata corrections were accepted. The reset form passed desktop/mobile layout, fragment-removal and normal-session stability checks. Both domains serve the billing/upload corrections, and the canonical upload passed. | Finish companion recovery, pending states and actionable errors within the authenticated pilot journey. Preserve noindex/no-referrer and reset-token privacy. These observations do not establish complete accessibility, Core Web Vitals or mobile field performance. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Schema21 migration, all four source7a/imagec1ce services, final reopening and canonical health are accepted. Three worker queues are running and all three schedules produced natural HTTP 200 runs; the application queue remains paused. The H18/f3 billing release and its successor upload-reconciliation release are promoted and both canonical aliases verified. The cron mitigation was normally merged in PR41; existing alert/channel mapping and prior inbox delivery are recorded below. | Deploy and accept companion connection recovery, then verify the final serving versions and held controls. Retain honest natural-versus-manual monitoring evidence. Preserve alert handles and distinguish configured routes from actual condition-specific incident/inbox delivery. No deployment-only completion claim. |

## Current rollout state and preserved authority

The accepted backend source is
**`7a00839dc90a93caebb0b3b5f2e9e03c51e941e9`**. Its normal merge into main is
**`9a60f2ac9c484de431628b77c48074255c8f6df4`**, with the identical Git tree
**`01e25ec0831d89c9bb4e05641650ff76f46136f0`**. All four services use accepted
immutable image **`sha256:c1ce5b32…`** and a single untagged 100-percent route.

| Service | Accepted canonical revision |
|---|---|
| API | `ai-resume-parser-activate-387054174fd0` |
| Analysis worker | `hirewiz-analysis-worker-activate-d2598748799e` |
| Employer worker | `hirewiz-employer-worker-activate-f5d5e2474f5c` |
| Resume coordinator | `hirewiz-resume-coordinator-activate-a48ebe3f111c` |

Canonical API, analysis and employer health checks passed. Natural scheduler
attempts independently matched HTTP 200 on the final revisions: scan at
**21:53:16 UTC**, cleanup at **21:50:21 UTC** and maintenance at **21:50:16 UTC** on
23 October. The analysis, employer-ingestion and employer-search queues are
RUNNING. The cleanup, scan and maintenance schedules are ENABLED. The
employer-application queue remains PAUSED. No forced scheduled invocation or
employer submission was used to obtain these results.

The additive migration preserves prior data and the single revision chain,
including the native upload-format change. Original intents, actual operation
acknowledgments, financial/retained-data comparisons and reconciliation history
remain authoritative. Completed writes must not be replayed for cleaner receipts.
The one reviewed conditional recovery preserved the original unknown attempt and
accepted only an actual provider acknowledgment before original reconciliation.

The two historical failed/waived model runs retain **120,742 USD micros** of
unresolved cost holds. The schema21 guard permits only coherent terminal history
with its complete financial fingerprint unchanged. It neither settles those costs
nor changes customer credits, and it admits no active work through the drain check.

The V2 inspector uses immutable image **`0a7…`**, its reviewed exact-image renderer
profile, **25 local cases and 10 native cases**, with inspector and caller pins
joined. Definitions expire **25 October 2026, 06:24:11 UTC**. A successor requires
the existing reviewed maintenance workflow; stale validity or arbitrary renderer
hashes must never be substituted. Inspector acceptance remains separate from
rendered native-upload QA and cleanup evidence.

The coordinator retains its dedicated identity and reviewed limits. Actual
reopening is recorded above; sales and employer-write holds remain independent.
The H18 billing release used frontend source **`f3c05034`**. The later
upload-reconciliation source **`7529c0413f4bee797ee1203fd3f1119f0b4ea4d7`** was merged
as **`533fc005c0455119947e25a9f0ada2c9be17935f`** and promoted on 23 October.
Both `hirewizhq.com` and `www.hirewizhq.com` resolve to the accepted successor;
canonical alias readback passed at **22:46:23 UTC**. Automatic custom-domain
assignment remains disabled. These frontend-only corrections required no backend
image or schema change.

## Accepted product evidence and remaining human journey

The original PDF is **122,522 bytes** and remains byte-identical. Real Version 3
was created in one Gemini attempt with **33,041 USD micros** of recorded model
cost. Its actual **140,451-byte** download is byte-identical to regeneration
through the unchanged fidelity guard. The two-unit quote was waived by the
existing legacy Premium entitlement; AI balance remained **7**. Historical funding
and PDF-stream failures remain preserved, rather than current blockers or
zero-cost receipts. No agent approved or submitted that draft.

The wrong-document preview repair rejects explicit unavailable/malformed IDs
instead of showing another source. New native DOCX, TeX and ZIP source downloads
match their originals. The DOCX unchanged-source snapshot is byte-identical;
TeX/ZIP sealed PDF snapshots were downloaded and matched to their preview bytes.
Rendered QA passed: the plain TeX PDF has two clean pages and the ZIP PDF one;
each retains two clickable links. Persisted database records for all three native
formats include source identity, scan receipts and closed quarantine generations;
the final bounded database observer passed all three. TeX receipts include the
required native-compilation and nested PDF inspection bindings. The separate release-user GCS marker
read was permission-denied. Report that independent read as unavailable rather
than claiming marker PASS; SD03 does not require expanding release-user GCS access.

The ZIP UI's unavailable message followed actual GET 429 and DELETE 429 responses,
despite its source being saved. The two-file frontend correction now reconciles
status GETs with bounded polling and avoids an implicit cleanup DELETE on uncertain
status. The change was staged, normally merged as `533fc005` from `7529c041`,
and promoted as BRu at **22:46:17 UTC**, with both aliases verified. Its 78 focused
tests and two browser cases passed. One follow-up upload POST in a fresh native
password session received upstream **503** at **22:48:59.327 UTC** before a known
upload ID was returned. A later bounded owner/hash/size/time lookup found zero
committed records for that failed request. One fresh operation then parsed the
synthetic ZIP successfully; its original download matched the source hash and
all 917 bytes. No AI enrichment was requested. Final verification finished at
**23:31:41 UTC** and took **7 minutes 8 seconds**; the original outcome estimate
and total development effort cannot be reliably reconstructed. A saved source or
unchanged snapshot does not establish actual tailored output.

The previous forgotten-password Google loop is historical. The release directs
users to `/forgot-password` and `/reset-password`; it neither implicitly enrolls
nor auto-logs in after reset. The first real reset email reached INBOX at **23 October 2026, 22:11:13 UTC**,
with SPF, DKIM and DMARC passing. The human chose, entered and
submitted the new password, then received the password-changed email in INBOX at
**23 October 2026, 22:28:41 UTC**. Native enrollment completed in the UI, and a
fresh native password sign-in reached the Dashboard. The old timed Google
forgotten-password loop is deprecated; it must not be presented as a remaining
recovery step.

Reset tokens remain private, single-use and absent from the URL after handoff;
posted/uncertain confirmation must not auto-repeat. Existing acknowledgment and
PENDING rules remain intact. Companion installation alone does not establish
pairing: browser-device preparation received upstream **503** at
**22:42:19.214 UTC**, with no known creation/pairing ID. The existing attempt is
under diagnosis; another Start or write is not authorized. The 0.8.1 recovery
change retains public connection references before creation and inspects both
permanent key-ownership stores using a signed device proof. Only an actual
UNCLAIMED result can enable an explicit archive of the uncertain attempt;
starting again remains a separate candidate action. Claimed keys, unavailable
observations and uncertain completion remain blocked. This source implementation
and its controlled tests do not establish a deployed recovery or paired device.

The latest actual search followed a displayed three-credit balance and an exact
three-result quote at **one credit per job**. Three of three Software Engineer
roles were returned; exactly **3** service credits were charged, **0** released,
and **0 held / 0 available** remained, while AI stayed **7**. The older
irrelevant result and separate audited quality adjustment remain distinct history.
These actual results complete original outcomes 1 and 4 within the approved free
twenty-origin pilot. They do not establish worldwide coverage, paid sales, real
payment acceptance or commercial profitability.

Finite offers remain Starter **₹649 / 100 service credits + 2 AI units**, Growth
**₹1,099 / 300 + 6**, and Scale **₹2,199 / 700 + 15**. Prices remain one service
credit per newly delivered qualifying job, twenty per confirmed complete assisted
application and two AI units for optional tailoring. Unknown submission outcomes
retain their hold for reconciliation; another POST is not a recovery strategy.
Expense-policy3 expires **29 October 2026, 20:28:54.855717 UTC**. Estimate-based
margins and funding controls do not prove commercial profit or payment acceptance.

## Monitoring, history and reporting limits

The preserved-data cutover compared **49 tables and 9 sequences** before the
additive migrations. Original database, protected backup and operation journals
remain preserved. Prior revisions are rollback resources only while their
policies and dependencies remain valid.

Prior actual INBOX evidence established scanner-definition attention open/resolved,
missing-heartbeat and GitHub failure notifications. Fresh metadata confirms all
five existing GCP alert policies are enabled and still use the selected email
channel. This mapping and historical delivery do not prove API 5xx, latency,
terminal Cloud Build or Vercel condition-specific delivery; those remain
configured or unexercised where actual incident/inbox evidence is absent.

Document-definition monitoring is separate from the application maintenance
schedule. After the observed gap following **18:18 UTC**, a genuine scheduled
GitHub run materialized at **22:07:17 UTC** and completed successfully at
**22:07:56 UTC** on 23 October. It appeared after the manual recovery's fresh
preimage check, which had found no active run. The separately authorized
**manual** monitor completed at **22:08:35 UTC**, emitted a fresh
event at **22:08:27 UTC**, and produced observed positive heartbeat metric points.
The new positive metric points are fresh under the unchanged two-hour absence window.
No Build, native Job, deployment, scanner-validity extension or synthetic heartbeat
was performed. This manual recovery does not prove future cron reliability.

The one-line mitigation changes monitor cron from `17 * * * *` to
`17,47 * * * *`; daily refresh `37 1 * * *`, permissions, WIF, environment,
concurrency and alert thresholds remain unchanged. It adds a second hourly
opportunity and was normally merged in PR41 as `7c1910c8` at
**23 October 2026, 23:04:27 UTC**. Natural
cadence must remain honestly distinguished from the manual recovery. Do not
force a production failure or send a test email to manufacture alert acceptance.

There is **no dependable remaining completion ETA**. Do not invent original
outcome estimates, sum independent operator durations or add CI/deployment
checkpoints to these six outcomes. Retain earlier receipts in private evidence
and the existing [historical progress archive](archive/AUTO_APPLY_PROGRESS_THROUGH_F6_2026-10-15.md)
and [validation history](archive/AUTO_APPLY_VALIDATION_HISTORY_THROUGH_2026-10-13.md).
See the [requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md),
[operations guide](../../infra/gcp/README.md),
[document maintenance](DOCUMENT_DEFINITION_MAINTENANCE.md),
[finite packs](FINITE_PACK_IMPLEMENTATION_2026-10-09.md) and
[cost policy](COST_PRICING_POLICY_2026-10-09.md).
