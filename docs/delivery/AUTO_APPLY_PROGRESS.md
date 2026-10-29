# HireWiz development and deployment progress

Updated **29 October 2026 (IST)**. The schema25 base release is deployed.
The active October27 goal implements the credit/cost/quality requirements across
the existing eight stages in `UNIFIED_CREDIT_ROLLOUT.md`; it is not complete.
The six accepted October26 pilot outcomes below are preserved history, not the
completion definition for this later goal.
Public mobile/desktop and SEO acceptance passed. Current signed-in account and
companion acceptance remain pending: maintenance closed the native account
lifecycle switch, and the first pilot reopening omitted it. The separately linked,
same-image correction is now applied on API generation350; authenticated health
and website registration/enrollment availability passed. The current real-account
journey still requires manual password sign-in and Mac unlock. Do not report the
current release as fully accepted until that journey passes.

The six outcomes below record **historical acceptance on 26 October**, within the
approved twenty-origin pilot. They are not fresh evidence that every feature is
currently available. New AI generation/tailoring, unified credits, free-core
processing, checkout and final employer submission remain closed. Worldwide
coverage, commercial launch and the new credit/quality program retain their
separate requirements gates.

## Latest verified releases

| Component | Current release | Verification |
|---|---|---|
| API and application workers | Source `8cbdfaeb9bb6cf6674c18ca8d59bc123fbf73a2a`; image `sha256:956735cd476c1159397ef21143b497f0b4d40df4d47f6b5d54cd8818a544eafa`; schema `20261028_0025` | Immutable build `45473be6-d177-49e1-a8a3-7970df8271db`, exact-source CI `40784200833`, checked stage/promotion journals, separate six-flag pilot reopening and the linked account-lifecycle correction on API generation350. Fourteen new tables and retained data, financial digests and ACL were verified. Current account acceptance remains pending. |
| Frontend | Source `b0c4581fe66b88369fca0219cabca8d1229684ba`; `dpl_CBiaNHpwCJHGbWcBeD3t7z9Kx3t3` | Production READY and canonical alias promoted. Exact-source CI `40840161705`. Public pages explicitly disclose the limited pilot and unavailable purchases/AI generation. |
| Document inspector | Configuration source `b281118da4ff810cadff196dbe8bc4cade3cecf3`; image `sha256:5811233d8dd35b1fadc73f4c1a43783c83edde47c5e90142a1e96c1428312db9` | Fresh native ten-case corpus and V2 to V3 transition; read-only reconciliation run `40811330747`. |
| Companion | Historical acceptance **0.8.4** | The October26 exact-file review/cancel/revoke evidence below is preserved. Current installation and account journey require fresh verification after the account repair. |

The final filtered configuration readback found all five services Ready, three
worker queues RUNNING, the application queue PAUSED and three schedules ENABLED.
The coordinator retains its own captured source `7a00839d`; it is not relabeled as
the document configuration source. Temporary health-check IAM access was removed
and independently read back absent after both the original release and the
account-lifecycle correction.

Public checks at widths 390/1440 passed layout, images, purchase closure, canonical
metadata, robots and sitemap. All observed CLS values were 0. The homepage's first
mobile LCP was 7.388s; a focused diagnostic was 1.452s. Both remain in the record;
the first slow result has no proven cause. Pricing mobile LCP was 1.760s. These
samples do not establish field p75 or capacity qualification.

The Gemini provider monthly spend cap is now **INR450**. Provider enforcement can
lag by approximately ten minutes; this is secondary protection, not a substitute
for application cost reservations or reconciled financial qualification.

[Current release evidence](evidence/2026-10-29-schema25-production-base.json) and
[the eight-stage credit/quality checklist](UNIFIED_CREDIT_ROLLOUT.md) distinguish
completed deployment work from remaining functional and commercial acceptance.
CI and regression checks are evidence, not additional development checkpoints.
No original effort totals are available; missing estimates remain null.

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
