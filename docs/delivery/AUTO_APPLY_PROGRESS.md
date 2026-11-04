# HireWiz development and deployment progress

Updated **5 November 2026 (IST)**. The current API and both application workers
serve DB8 on schema26. Their original promotions are healthy, the original
schedules and coordinator are restored, and the joined read-only proof passed.
Frontend 751/Hohsg serves companion 0.8.9. The active 27 October credit/cost/quality
goal across the existing eight stages remains incomplete; the six accepted
26 October outcomes below remain historical acceptance for their named releases.

The current browser identity check passed with no application action enabled and
zero fields filled. Fresh original-file review, cancellation before Fill and
exact-device revocation remain pending. The website session expired on a fresh
base-page GET; no new Fill, upload, submission or revocation was performed.
The current identity check does not renew the historical application acceptance.

New AI generation/tailoring, unified credits, funded free-core processing,
checkout and final employer submission remain closed. Paid native document QA
(80 documents/nine jobs) and ordinary 17-case scanner maintenance are both on
**OWNER HOLD**. Broader native/model quality qualification remains pending.
Human Reviewer 2, representative shadow accounting, 10,000-user capacity,
commercial/finance gates and full R03/RTO qualification remain pending.

## Latest verified runtime and pending successor

| Component | Current release | Verification and boundary |
|---|---|---|
| API and application workers | Source `db8a25adefaf3bc2e00a61f7543f8a1862597adf`; image `sha256:854c0361b7af3fb095f8869e02f156910ad6292d90b17c30e0d15efa53d01cbe`; schema `20261031_0026` | Build `05fafcae-934e-448f-9790-26a60b6f3b64`, exact-source CI `41650961963`, committed schema26 and retained promotion/restoration proofs. All three application services are healthy. The 4 November UTC / 5 November IST traffic observations show three aliases per service, all reaching that service's same DB8 revision; aliases were not changed. |
| Frontend | Source `751319a19420d5f141697643f68b6853ea2da5c6`; `dpl_Hohsgv6Y5CPPN2295GUvJNs45YZH` | Exact-source CI `41750684597` and normal PR99 merge; retained production alias/readback and public companion 0.8.9 archive SHA-256 `b17b7a75f3d6a81ca91d2a90c88bd5d79409b965977ecb5f7340f69025964362`. Frontend/root build inputs are unchanged through 9b9, so the existing verified deployment is retained without another frontend build or alias mutation. |
| Document inspector | Accepted image `sha256:562127e194be4479be9f52756dcd21830b6e64b8b250d39237643ead2c453e36` | Retained definition proof expires `2026-11-05T06:24:10Z`. A metadata-only configuration check passed; it performed no document processing or new native acceptance. Both the 17-case maintenance and paid 80-document QA runs remain held, with no expiry waiver. |
| Companion | Public/source package **0.8.9** | The existing browser identity's current-connection check passed with no action enabled and zero fields filled. Fresh original-file review/cancel/revoke remains pending; historical acceptance below is not relabeled as current. |
| Application successor — built only | Source `9b9d9c33dfae519b95fbc380fc99ba6bcf68e8a4`; image `sha256:34205f3ddc6c48215b682ac518c21d25d66243e5286c986010fcbc4eda084be3` | Build `3447322a-4f1b-41b8-afe5-43307ada80cd` succeeded and official Source26 verification passed against exact-source CI `41853042750`. **Not deployed.** The independent alias-capture controller fix `a206f5408c0cbd7abece0cd43add143bcb1883bc` is in PR103 / CI `41898489779`, pending qualification. Registry PR102 is normally merged at `deaed009fd9b1a6b6db2e893859c8dc19433ac17`; its CI `41893799752` completed successfully, while runtime provenance remains bound to the separate 9b9 source and Build. |

The original S/C restoration and joined final proof used their retained custody;
temporary health IAM access was removed and ordinary post-restoration coordinator
requests returned HTTP200. These scoped observations do not establish full R03,
RTO, representative capacity or paid native document quality acceptance.

The successor capture stopped before creating a plan or journal and before any
service mutation. Repeated traffic aliases exposed a controller proof-collection
bug; PR103 preserves every alias and pins each distinct revision once per service.
The target application bytes are unchanged by that fix, so it needs a qualified
controller rather than a new application image. Neither a successful Build nor
pending CI authorizes deployment or new paid work.

The Gemini monthly spend cap remains **INR450**. Provider enforcement can lag;
application cost reservations and reconciled financial qualification remain
required. Retail purchases are unavailable.

[Current runtime metadata and proof boundaries](evidence/2026-11-05-runtime-status.json)
and [the eight-stage credit/quality checklist](UNIFIED_CREDIT_ROLLOUT.md) distinguish
completed deployment work from remaining functional and commercial acceptance.
All eight existing stage rows and all 83 requirement statuses remain unchanged.
CI checks are evidence, not additional development checkpoints. No original total
effort estimate is retained; missing estimates remain null. Earlier dated reports
and observations below remain scoped to their original releases.

## Original six outcomes — historical acceptance on 26 October

| Original outcome | Status | Accepted evidence and remaining work |
|---|---|---|
| Official job search, ranking and user-selected job counts | **COMPLETE — free pilot** | Twenty reviewed employer origins; an actual requested three-job Software Engineer search returned **3/3 engineering roles**, quoted 1 credit per new job, charged 3, released 0 and left no hold. Deterministic fit is explained without treating it as hiring probability. The earlier wrong-role search and later replacement-job QA are separate records. Worldwide coverage and greater-than-95% recall are unverified follow-on work. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | **COMPLETE — free pilot** | Real PDF Version 3 retained four safe edits, one page, nine links and 147 unedited positions; an unsafe proposal was omitted. Actual native V2 edited-DOCX cases prove fit-preserving editing and unsafe link/reflow refusal. Owner DOCX/TeX/ZIP uploads and exact source snapshots, plus sealed TeX/ZIP preview/download equality, are separately verified. Snapshots do not prove new tailoring. Supported-layout limits and conservative original/custom-file fallbacks remain; universal format, ATS or selection guarantees are not claimed. |
| Candidate-reviewed direct company applications | **COMPLETE — free pilot** | The actual Razorpay/Greenhouse portal was inspected using the genuinely paired 0.8.4 companion. The review bound the original file and only the saved first-name answer; other required answers remained manual. The original download matched its exact hash. Approval was followed by cancellation **before Fill**, then actual native device revocation. The exact-ID read-only SQL observer passed all ten checks, with all seven execution/accounting counts zero, no application charge and no employer receipt. Chrome now withholds effective Greenhouse site access; HireWiz access remains on, automatic all-site access off. No application was submitted. |
| Paid search/application credits, profitable finite packs and payment recovery | **COMPLETE — free pilot** | Three finite packs, separate service/AI balances, separate search/application quotes, reservations, idempotent settlement and recovery controls are implemented. Actual engineering-search settlement proves 3 reserved/charged, 0 held/released. Expense policy 3 and model policy 2 retain accepted authority. Sales, real capture/refund acceptance and measured profitability remain held follow-on work; unresolved provider expense must not become zero cost. |
| Fast responsive pages and SEO | **COMPLETE — free pilot** | Prior public/authenticated viewport, keyboard/navigation, metadata and reset-token privacy observations remain accepted. Companion-route navigation suppresses unrelated profile/features and cached-balance requests. The repaired authenticated connection, exact-file review, approval, cancellation and disconnection journey passed on the deployed website and installed companion. Mobile retains the responsive website and manual portal path. Complete accessibility and field p75 LCP/INP/CLS remain unmeasured follow-on work. |
| GCP/Vercel deployment, preserved-data migration and monitoring | **COMPLETE — free pilot** | Schema 21 migration, reopened worker services, API 338 and frontend BgUZ publication/readback are accepted. The actual native canary and exact-ID zero-effects/accounting observation pass. Final readback confirmed all five services healthy at expected sources/images, three worker queues RUNNING, application queue PAUSED, three schedules ENABLED, public health 200 and extended-action/checkout holds preserved, with no drift. Configured alerts and scheduler transport remain distinct from condition-specific delivery; one completed natural refresh does not establish stable daily cadence. |

## Capacity, retention and scope

The API uses an explicit maximum **30-second**, operation-local observation of
exactly two pinned Firestore RPC identities. Authorization, witness, closure,
session and revocation reads stay fresh, with entry/exit and privileged-boundary
identity checks. The [documented tradeoff](../adr/010-bounded-browser-identity-observations.md)
allows internal ledger writes before UID-loss detection while quarantining outputs
and withholding authority on failure. It does not retain the old no-private-write
timing guarantee.

SDK-intercepted observations reduced confirmation 54→8, device completion 36→22
and revocation 71→20 metadata calls. These are synthetic production-path counts,
not measured whole-HTTP production budgets. The shared **60/minute** project
quota still limits concurrent capacity; no quota increase is claimed.

A separate password-login improvement is prepared and source-reviewed. It combines
three protected post-activation finalization steps into one transaction, preserving
fresh identity, credential, mailbox and activation checks. Focused SDK-intercepted
controls measured account-path metadata calls falling from 29 to 25. This is not
a whole-HTTP or live speed measurement, does not change Google OAuth, and does not
establish the 10,000-user capacity target. Its own release qualification and
deployment remain pending.

Earlier connection attempts encountered genuine metadata-quota failures and
unresolved rejection/expiry outcomes. A later actual confirmation returned backend
HTTP 200 after **13.025 seconds**, while the old BFF stopped waiting after 12
seconds and surfaced 503. Release 0.8.4 uses bounded 35-second upstream and
40-second client/companion waits within a 45-second Vercel function limit.
Response-body limits, signed request freshness, challenge expiry and action leases
are unchanged; uncertain POSTs are never automatically retried.

The final actual journey completed on 25 October UTC / 26 October IST: pairing,
portal inspection, exact original-file download/review, first-name-only approval,
cancellation before Fill and native device revocation. SQL observed that same
owner/application/device/review at **18:31:46 UTC**: ten checks passed, all seven
execution/accounting counts were zero, the review was cancelled, the device revoked,
and no charge, reservation, employer outbox or receipt existed. Native pairing and
revocation acknowledgements were observed separately; SQL alone is not their proof.
The retained SQL evidence digest is
`d7f93101596f1e94382ed9a379c257589d80552f48eb997e376d5a0e7dabd6d6`;
the separately retained native acceptance record is
`1e50e049b6f016131d2adae2702c144cff715bf8bed809930ef0a646ae166a59`.
Chrome's effective Greenhouse site access was then **off/withheld**; this is not a
claim that its optional permission record was deleted. HireWiz access remained on,
automatic all-site access off, and the same extension/key/history were retained.

Companion 0.8.4 preserves native protocol 2, executor `companion_0_8_1`, extension
identity, optional permissions and version-two checkpoints. It retains up to
**64 prior records / 256 KiB**, refuses archival beyond that history bound and
refuses oversized checkpoints. It never truncates history. Fresh signed EXPIRED/UNCLAIMED checks remain mandatory before archival;
Start is a separate explicit action. The older 0.8.3 and 0.8.2 archives are retained. See the
[package and existing-install update guide](../../browser-companion/README.md).

The **October26 historical pilot** was bounded to twenty origins and
Razorpay/Greenhouse reviewed-browser assistance. The active October27 requirements
add the new credit, quality, cost and controlled-payment release work; their
remaining gates still apply. GCP and Vercel Hobby are retained until sales. No
real payment or employer application is a smoke test. Worldwide expansion and
independent coverage assessment remain separately deferred. Mobile users retain
the website and manual company-portal path; the companion requires Chrome desktop.

Manual document maintenance run 40109020215 passed local/native acceptance and
restored admission. Earlier natural expiry-monitor runs, including 40151669591,
succeeded without executing definition-refresh steps. The later scheduled run
40169964119 completed an actual refresh and restored admission; its downstream
result and current pins were independently reconciled. This is one observed
natural refresh. Stable unattended daily cadence and every condition-specific
alert delivery remain unproven.

On October29, monitor-only run `40868697702` observed the current immutable
inspector, followed by successful natural scheduled monitor `40879201504`.
Its native artifact reports fresh definitions through October31 06:24:17 UTC
and no promotion. The daily refresh failure `40836620660` remains distinct:
the reviewed consumed-history repair must be released and verified natively.
Monitor success does not establish successful definition refresh or email delivery.

Reliable original estimates and aggregate actual durations for the six outcomes
were not retained. No expected-versus-actual totals or dependable completion ETA
are reconstructed from agent runtime or unrelated test timings.

## Preserved history

The [full prior board at its immutable Git revision](https://github.com/Harshil4442/ai-resume-copilot/blob/520df44e9fd7fe4e83f4835478552b76e41034fe/docs/delivery/AUTO_APPLY_PROGRESS.md)
retains correctly resolved historical Markdown links. Its exact bytes are also
preserved in the [raw archive](archive/AUTO_APPLY_PROGRESS_BEFORE_2026-10-25_STATUS.txt).
Earlier dated reports remain historical evidence, not current rollout authority.
