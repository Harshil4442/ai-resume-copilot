# HireWiz development and deployment progress

Updated **25 October 2026 (IST)** after the compatible 0.8.2 frontend/package
release and accepted manual document-maintenance recovery. Original outcomes
**1, 2 and 4 are COMPLETE within the approved free twenty-origin pilot**; the other
three remain **OPEN**. Global
coverage, sales, real payments and commercial profitability remain follow-on
work. The goal remains unfinished until the other pilot outcomes are accepted.

Schema **`20261023_0021`** is committed. The canonical API serves accepted
request-capacity source **`0abc7d53`**, immutable image **`358c9db6`**, at generation
**331** after the scanner-pin transition. The application image and source are
unchanged. Analysis generation **63**, employer generation **31** and coordinator
generation **22** retain accepted source **`7a00839d`** and image **`c1ce5b32`**.
Inspector generation **14** serves immutable image **`9e402666`**, with definitions
expiring **27 October 2026, 06:26:10 UTC**. Manual maintenance run **40109020215**
passed current local/native acceptance; independent serving and caller-pin
verification passed at **16:10:27 UTC**, and restoration of captured admission
flags passed at **16:11:46 UTC**. The earlier API staging and promotion evidence
remains preserved below. Reopening is accepted: the three reviewed worker queues
are RUNNING and the three existing schedules are ENABLED. The employer-application queue remains PAUSED;
sales, checkout and employer submission remain held.

Production frontend **`dpl_HZ2Dg7ns252WaHbcQPU14Da5uJxh`** serves reviewed
source **`5e2e957d11e3d99b7d7871d59afbf07af443ad6c`**, normally merged in
**PR49** as **`aa3e6768`**. Both canonical aliases targeted this deployment at
**24 October 2026, 18:40:35 UTC**. The reviewed 0.8.2 ZIP is **67,162 bytes**,
SHA-256 **`c952be0f5cf7411a0a5a314b6624fa0264762b0c5668f7eb45805b257de53107`**;
its staged download and the unchanged 0.8.1 archive returned HTTP 200. Canonical
0.8.2 download and all eighteen installed source files were verified at
**18:44:53 UTC**. Native protocol **2** and executor **`companion_0_8_1`** remain
unchanged; this compatible client recovery patch creates no new native admission
and changes no backend image, native configuration or employer-write control.
It supersedes frontend875 while preserving the earlier request-capacity,
upload-reconciliation and billing evidence below. Reset-token privacy headers
passed at the staged observation. Actual password reset, native enrollment and
fresh native password sign-in have passed; companion pairing remains pending. The final
canonical ZIP upload passed,
and its downloaded original matched the uploaded bytes exactly at
**23 October 2026, 23:31:41 UTC**.
A single fresh native-password-session upload POST received upstream **503** at
**22:48:59.327 UTC**, before a known upload ID was returned. Browser-device
preparation also received upstream **503** at **22:42:19.214 UTC**, without a
known creation or pairing ID. A bounded database lookup found no committed upload
for the failed request; only after that reconciliation was one fresh upload made.
These failures are separate from the deployed 429 status-reconciliation correction.
The earlier companion attempt and its evidence are preserved. The previously
reloaded 0.8.1 runtime recognized the retained ID-less attempt and original key.
A genuine signed inspection returned **UNCLAIMED**; fresh reinspection preceded
explicit archival, then one separate Start prepared a creation whose website
fingerprints matched. That request passed its short creation deadline while
persistent-grant approval remained pending. The compatible 0.8.2 source files now
match the reviewed archive, preserving the same extension key, permissions,
profile and IndexedDB. The human reloaded the same extension, and its enabled
**Check connection status** control verified the new runtime. Signed inspection
of the exact retained request reported **EXPIRED**, with the same key **UNCLAIMED**.
An initial archive attempt correctly refused after its ephemeral inspection was
lost, before any checkpoint write. Fresh inspection followed by immediate explicit
archival succeeded, preserving the key, profile and history. The human then
approved the bounded persistent-device/Greenhouse-host test scope. One separate
fresh Start created a known request with the same key and new request fingerprint.
One candidate challenge returned upstream **503** before an approval form appeared;
no confirmation, device/host grant, paired device or employer action followed.
The existing request must be reconciled before any further connection ceremony.
Deployment and expired-request recovery do not close an original outcome.

Sanitized GCP HTTP 500 traces on resume listing, employer catalog and features
show **`RuntimeError: deque mutated during iteration`** during a SQLAlchemy pool
connection. Repeated candidate-factory engine creation added listeners to that
shared pool while another request was iterating them. A deterministic real
QueuePool test reproduced the same failure. The private engine-view repair
preserves the pool, dialect, options and redaction. Its initial focused coverage
missed the pairing projection's exact-type guard; a real composition regression
reproduced that refusal before staging. The projection now admits exactly the
stock SQLAlchemy Engine or OptionEngine with redaction, while custom subclasses
and unredacted views remain refused. **104 focused pairing-production, factory
and private-view tests passed**, but **the API repair is not deployed yet**.
This demonstrated defect is separate from the earlier Firestore quota
observations below.

The request-capacity repair is **deployed**. Earlier actual provider
metrics show HTTP 429 / gRPC status 8 for Firestore Admin `GetDatabase`, and the
project's `DatabaseOperationsPerMinutePerProject` quota is **60 per minute**.
Both inspected quota forms allow only 0–60; the quota API reports that an
increase through that API is unsupported. No higher quota has been granted.
Two signed key inspections returned upstream 503 at **24 October, 01:40:56 UTC**
and **01:45:33 UTC**. Their individual provider exceptions remain unclassified;
aggregate throttling alone does not attribute every 503 to that cause. A later
single stock CLI metadata read succeeded. The earlier failed internal answer save
was preserved. A later explicit partial save succeeded, and the saved-answer UI
confirmed a nonempty `first_name`; the other answers remain blank. The exact
original file and preview remain intact, with **0 reserved / 0 charged** for the
internal application. The public Razorpay form remains blank with no employer
write. These observations do not prove completed pairing or form execution.

SDK-intercepted production-path tests expose a separate demand defect: one
candidate confirmation previously used **68 metadata calls**, while normal session
validation uses four and key inspection uses eight. The deployed native repair
combines redundant same-pin fences around two immutable, read-only marker
checks, reducing confirmation to **54 calls**, without caching authorization or
metadata across requests. Transaction, signing, acknowledgment and standalone
password boundaries retain fresh checks. A 54-call operation remains expensive
under a shared 60-per-minute quota; this is a reduction, not accepted scalable
capacity. The deployed frontend shares owner-scoped profile reads, suppresses
automatic read retries on 429/503, and bounds progress polling with backoff,
hidden-page pause and explicit status recovery. Real pairing must still pass
before outcomes 3, 5 or 6 can
close. The approved Google support inquiry returned a form-submission error;
no case number or quota approval is claimed.

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
| Candidate-reviewed direct company applications | OPEN | Ordinary Google sign-in passed; the human reports foreground sign-in within five seconds. The human completed password reset; the password-changed email reached INBOX at **23 October 2026, 22:28:41 UTC**. Native enrollment and a fresh native password sign-in both reached the Dashboard. The first reset email also reached INBOX with SPF, DKIM and DMARC passing. The earlier reloaded 0.8.1 runtime passed signed **UNCLAIMED** inspection and explicit archival before one new Start with matching fingerprints; that creation later passed its deadline without a persistent grant. All eighteen installed 0.8.2 source files match the reviewed ZIP. The human reloaded the same extension; actual exact-request **EXPIRED** and same-key **UNCLAIMED** inspection preceded successful explicit archival, preserving key, permissions, profile and history. The earlier archive refusal after lost ephemeral inspection remains recorded. Human approval for the bounded persistent-device/Greenhouse-host test scope is received. One separate fresh Start created a known request with the same key and a new fingerprint; its single candidate challenge returned **503** before an approval form. No confirmation, grant, pairing or employer action is accepted. Actual GCP 500 traces exposed the shared-pool listener defect; its private-view/projection compatibility repair passes 104 focused tests and is prepared but not deployed. A genuine one-credit search returned Razorpay's Lead Software Development Engineer role in Bengaluru. The exact-original internal application now has one successfully saved ordinary `first_name` answer and a loaded preview; other answers remain blank, with **0 reserved / 0 charged**. The active public form remains blank and no employer write occurred. | Deploy the reviewed API repair, reconcile the existing known request without replay, then verify actual candidate confirmation/pairing, native portal inspection, exact-file/answer review, cancellation before Fill and native device revocation. Retain the separate native inspection/revocation responses and bounded zero-execution/accounting readback. Do not seed an identity/device or bypass proof; employer attachment, upload and final submission are not smoke tests. |
| Paid search/application credits, profitable finite packs and payment recovery | COMPLETE (free pilot) | Three finite packs, separate service/AI balances, exact quotes, reservations, idempotent settlement and recovery controls are implemented. The promoted billing page displayed the actual **3** service credits. The quote of **1 per job** delivered **3 of 3** Software Engineer roles, charged **3**, released **0**, and left **0 held / 0 available**; AI remained **7**. Expense-policy **3** and model-policy **2** retain their reviewed authority. | Approved free-pilot credit, quote and settlement acceptance is complete. Paid checkout, real payment/refund receipts, commercial activation and actual-cost profitability remain explicitly unaccepted follow-on work. Unknown provider liabilities must not become zero cost. |
| Fast responsive pages and SEO | OPEN | Public views were observed at 390, 768 and 1,440 pixels without horizontal overflow; signed-in search/billing views were also observed at 390 pixels. Fresh signed-in workspace and resume review observations at 320 and 1,024 pixels show no horizontal page overflow. Keyboard focus, mobile navigation and horizontally scrolling section access passed. Exact-source preview and canonical pricing/auth metadata corrections were accepted. The reset form passed desktop/mobile layout, fragment-removal and normal-session stability checks. Both domains serve the request-deduplication and bounded-polling correction, retaining the billing/upload fixes. The canonical upload passed. The deployed 0.8.2 website source adds visible review deadlines, expired-control clearing and explicit review renewal; the companion source adds bounded expired-create recovery. The same installed runtime was reloaded and passed live expired-request inspection and explicit archival. The first archival refusal correctly preserved state after its ephemeral inspection was lost. The approved fresh connection reached a known request, but a single challenge returned **503** before the review form. The demonstrated SQL pool-listener repair is prepared, not deployed; remaining authenticated review/renewal behavior is unaccepted. | Verify the repaired authenticated connection/review pending states and actionable errors within the existing pilot journey. Preserve noindex/no-referrer and reset-token privacy. Complete accessibility, device/connection-segmented field p75 LCP, INP and CLS, and mobile field performance remain unmeasured; these observations do not establish them. |
| GCP/Vercel deployment, preserved-data migration and monitoring | OPEN | Schema21 migration and reopening are accepted. API generation **331** retains source0abc/image358c9; analysis generation **63** and coordinator generation **22** retain source7a/imagec1ce after the scanner-pin transition. Employer generation **31** is unchanged. Manual maintenance run **40109020215** passed **25 local / 10 native cases** and restored admission; independent native serving/pin and admission-restoration readbacks passed at **16:10:27 / 16:11:46 UTC**. Inspector generation **14**, image9e402666, has definitions valid until **27 October, 06:26:10 UTC**. The historical five-file journal is preserved byte-for-byte; its candidate was neither adopted nor replayed. Three worker queues are running and the retained three natural schedule HTTP 200 observations precede this pin transition; the application queue remains paused. FrontendHZ2, source5e2e957 / PR49, is promoted on both canonical aliases at **24 October, 18:40:35 UTC**. The canonical 0.8.2 archive and all eighteen installed source files match the reviewed digest. Actual same-extension reload and expired-request archival passed; the approved fresh Start created a known request, but its candidate challenge returned **503** before any confirmation or grant. The API pool-listener and stock-view projection repair passes all 104 focused pairing/factory/private-view tests and is not yet deployed. Stable unattended document-maintenance cadence remains unproven. | Deploy and verify the reviewed API repair, accept the remaining live companion journey, then update the final delivery record using actual serving versions and held controls. Retain honest natural-versus-manual monitoring evidence and its observation times. Preserve alert handles and distinguish configured routes from actual condition-specific incident/inbox delivery. No deployment-only completion claim. |

## Current rollout state and preserved authority

The schema21 application release used source
**`7a00839dc90a93caebb0b3b5f2e9e03c51e941e9`**, normally merged as
**`9a60f2ac9c484de431628b77c48074255c8f6df4`**, with identical Git tree
**`01e25ec0831d89c9bb4e05641650ff76f46136f0`**. Its three worker/coordinator
services retain immutable image **`sha256:c1ce5b32…`**. The later diagnostics-only
API release used source **`65d743a9b63f32e5509da381279b376804bdb606`** and image
**`sha256:2077e476…`**. Its accepted 0.8.1 successor used source
**`47169c10c970a87a48bd5d26ece9b146996cd400`** and image **`sha256:bf23b42b…`**.
The current request-capacity successor uses source
**`0abc7d530dcd0115a9f2af0c2961f89c55c41077`** and image
**`sha256:358c9db69074aa7fa383a1562f64742261837d91ebcf8d9593c0a4a97fb8f894`**.
That API-only promotion left the other three services, queues and schedules
unchanged. The later document-maintenance transition repinned the inspector and
three callers as recorded below. Each canonical service has one untagged
100-percent route.

| Service | Generation | Accepted canonical revision |
|---|---|---|
| API | 331 | `ai-resume-parser-d-4d2e81c43b97-open` |
| Analysis worker | 63 | `hirewiz-analysis-worker-d-4d2e81c43b97-open` |
| Employer worker | 31 | `hirewiz-employer-worker-activate-f5d5e2474f5c` |
| Resume coordinator | 22 | `hirewiz-resume-coordinator-d-4d2e81c43b97-open` |
| Document inspector | 14 | `hirewiz-document-inspector-d-4d2e81c43b97-worker` |

The request-capacity API was originally staged at generation **327** and promoted
at generation **328**; its independent canonical verification passed in
**37.26 seconds**. That is the duration of this earlier check, not page-load
performance or total development time. The later accepted document-maintenance
transition produced the current revisions above without rebuilding application
images. Its API health readback matched source0abc and the new revision at
**24 October, 16:10:27 UTC**. Its earlier frontend
**`dpl_875XxYMHwUJ3F9pPmVzWY4Sc1fGm`** was promoted and both aliases verified at
**03:16:06.122378 UTC** and again at **15:36 UTC**. The compatible 0.8.2 frontend
**`dpl_HZ2Dg7ns252WaHbcQPU14Da5uJxh`**, source5e2e957, superseded it on both
aliases at **18:40:35 UTC** without an API or native configuration change.
No schema, IAM, global authority epoch,
signing key, executor configuration or employer-write hold is changed by this
request-capacity release.

Before this maintenance transition, canonical API, analysis and employer health
checks passed. Retained natural scheduler attempts independently matched HTTP 200
on their then-serving revisions: scan at
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

The V2 inspector now uses immutable image
**`sha256:9e402666dbe7e518b2d7e5f7893b2d876b5682e296630af1f082e5a6c734e2f4`**,
with the reviewed **`0a7e…`** renderer parent, **25 local cases and 10 native cases**,
and inspector/caller pins joined. Definitions expire
**27 October 2026, 06:26:10 UTC**. A successor requires
the existing reviewed maintenance workflow; stale validity or arbitrary renderer
hashes must never be substituted. Inspector acceptance remains separate from
rendered native-upload QA and cleanup evidence.

The coordinator retains its dedicated identity and reviewed limits. Actual
reopening is recorded above; sales and employer-write holds remain independent.
The H18 billing release used frontend source **`f3c05034`**. The later
upload-reconciliation source **`7529c0413f4bee797ee1203fd3f1119f0b4ea4d7`** was merged
as **`533fc005c0455119947e25a9f0ada2c9be17935f`** and promoted on 23 October.
Its canonical alias readback passed at **22:46:23 UTC**. The later 0.8.1 recovery
frontend was verified on both domains at **24 October, 01:01:53 UTC**. The
request-capacity frontend subsequently superseded it on both `hirewizhq.com` and
`www.hirewizhq.com`. The current compatible 0.8.2 frontend supersedes that release;
both canonical aliases were verified at **24 October 2026, 18:40:35 UTC**.
Automatic custom-domain assignment remains disabled. The billing/upload corrections themselves required
no backend image or schema change; the 0.8.1 recovery API successor is recorded
separately above.

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
pairing. The original browser-device preparation received upstream **503** at
**22:42:19.214 UTC**, with no known creation/pairing ID, and remains preserved.
The accepted 0.8.1 API recovery change retains public connection references before
creation and inspects both
permanent key-ownership stores using a signed device proof. Only an actual
UNCLAIMED result can enable an explicit archive of the uncertain attempt;
starting again remains a separate candidate action. Claimed keys, unavailable
observations and uncertain completion remain blocked. This source implementation
and its controlled tests do not establish a paired device or successful
candidate-reviewed assistance. The earlier 0.8.1 source update and human reload
were completed. Its retained key returned **UNCLAIMED** in a real signed
inspection; fresh reinspection preceded explicit archival of the uncertain attempt.
One separate Start prepared a creation with matching website fingerprints, which
later passed its deadline without a persistent grant.

The compatible **0.8.2** package is now deployed and its **eighteen installed source
files** match the reviewed ZIP, as independently checked against the archive.
The same key, optional permissions, profile, IndexedDB and prior history are
preserved. The human subsequently reloaded that same extension. Its enabled
**Check connection status** control established the new runtime, and fresh signed
inspection reported **EXPIRED** for the exact retained pairing/device IDs plus
**UNCLAIMED** for the same key. The first archive attempt correctly refused after
its ephemeral inspection was lost, before a checkpoint write. Fresh inspection
and immediate explicit archival then succeeded, retaining its reconciliation
evidence and original history. The update and archival themselves created no
request or device/host grant. The human subsequently approved the bounded test
scope, and one separate fresh Start created a known request with the same key and
a new request fingerprint. Its single candidate challenge returned **503** before
the approval form appeared; no candidate confirmation or grant occurred.
A local deadline alone cannot authorize archival. Archival of a live
candidate-confirmed request or of completion/refresh uncertainty remains blocked.

The human's bundled persistent-device and Greenhouse-host test authorization is
**RECEIVED**; this authorizes the bounded test, not a claim that native access was
actually granted. The API pool-listener repair remains undeployed. After its
reviewed deployment, reconcile the existing known request before any new ceremony,
then verify actual candidate confirmation/pairing, native portal inspection,
exact-file/ordinary-answer review, cancellation before Fill, native revocation
and bounded zero-execution/accounting readback within original
outcomes **3, 5 and 6**, which stay **OPEN**. No employer fill, upload or final
submission is required as a smoke test.

The accepted three-result search followed a displayed three-credit balance and an exact
three-result quote at **one credit per job**. Three of three Software Engineer
roles were returned; exactly **3** service credits were charged, **0** released,
and **0 held / 0 available** remained, while AI stayed **7**. The older
irrelevant result and separate audited quality adjustment remain distinct history.
These actual results complete original outcomes 1 and 4 within the approved free
twenty-origin pilot. They do not establish worldwide coverage, paid sales, real
payment acceptance or commercial profitability.

A separately approved, audited **one-credit unpaid QA promotion** supported one
later normal search. It returned Razorpay's Lead Software Development Engineer
role in Bengaluru, charged **1**, refunded **0**, and left **0** service credits
while AI stayed **7**. This is distinct from the original three-credit search
settlement and is not a payment or revenue receipt. The candidate then created
an internal application bound to the exact original resume. A later partial
`first_name` save succeeded and the saved-answer UI confirmed it; other answers
remain blank. The original source hash is unchanged, its preview loaded, and the
internal application retains **0 reserved / 0 charged**. The selected public
posting was active and its form remained blank. No employer write, completed
assistance or submission is claimed.

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
Those positive points were fresh at that observation. A later genuine natural
monitor run on source **`c6e6682d`** completed successfully at
**24 October 2026, 01:38:57 UTC**. At the bounded **02:42 UTC** observation its
age was **3,803 seconds**, within the unchanged **7,200-second** absence window.
This is a timestamped run-freshness observation, not a promise of current or
uninterrupted heartbeat delivery. The earlier gap and manual recovery remain
preserved; stable unattended cadence is still unproven. No forced Build, native
Job, deployment, scanner-validity extension or synthetic heartbeat was used for
monitor recovery. The existing stale-heartbeat alert and prior accepted
notification evidence remain recorded. A gap alone does not establish a new
incident state or a provider-delay cause, and manual recovery does not prove
ongoing natural cadence.

The one-line mitigation changes monitor cron from `17 * * * *` to
`17,47 * * * *`; daily refresh `37 1 * * *`, permissions, WIF, environment,
concurrency and alert thresholds remain unchanged. It adds a second hourly
opportunity and was normally merged in PR41 as `7c1910c8` at
**23 October 2026, 23:04:27 UTC**. It is a configured mitigation, not evidence
that later scheduled runs executed. Natural cadence must remain honestly
distinguished from the manual recovery. Do not
force a production failure or send a test email to manufacture alert acceptance.

At **24 October, 15:36 UTC**, the latest three natural monitor runs had failed
with the intended `document_refresh_expiry_alert`: the then-serving inspector's
definitions were due to expire at **25 October, 06:24:11 UTC**, within the unchanged 24-hour
warning window. The daily refresh run **40069909715** stopped earlier with
`document_refresh_prior_unknown_reconcile_only`, before creating a new Build,
because its bounded history included a five-file pre-native Build journal.
Read-only reconciliation found the acknowledged historical Build successful,
its deterministic Job absent, and the accepted serving inspector on a different
image. A bounded audit-log query found no matching Job activity within its queried
window. These observations supported the additive abandonment reader; they did
not establish native acceptance for that old candidate.

The repair was normally merged in **PR47**, source **`bc97d52b`**, merge
**`1b846c83`**. One normal **manual** maintenance run **40109020215**, from that
exact main revision, ran at **24 October, 15:53:20–16:06:48 UTC** and succeeded.
Its actual artifact retains the five original journal files byte-for-byte and
records their candidate as abandoned before native acceptance. The old Build
was neither adopted nor replayed. A fresh Build **`4a41de96`** produced image
**`9e402666`**, which passed **25 local / 10 native cases** before the bounded
inspector and three-caller pin transition. Independent read-only verification at
**16:10:27 UTC** accepted its native V2 receipt, serving revision, caller pins and
API health; the separate **16:11:46 UTC** readback verified all captured admission
flags restored. Current definitions expire **27 October, 06:26:10 UTC**.

This accepts the bounded manual recovery. It does not prove later natural
schedule cadence, a fresh condition-specific alert delivery, or completed browser
assistance. Original artifacts and the earlier warnings remain preserved; the
24-hour warning and three-day runtime expiry limits are unchanged.

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
