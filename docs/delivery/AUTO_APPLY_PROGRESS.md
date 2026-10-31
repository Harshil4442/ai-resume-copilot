# HireWiz development and deployment progress

Updated **31 October 2026 (IST)**. The e23 schema25 API and application workers
are deployed; the refreshed document inspector and frontend companion 0.8.7 serve
alongside them. The active 27 October goal implements the credit/cost/quality
requirements across the existing eight stages in `UNIFIED_CREDIT_ROLLOUT.md`;
it is not complete. The six accepted 26 October pilot outcomes below remain
historical acceptance, not the completion definition for this later goal.

Current signed-in acceptance remains partial. The repaired 0.8.6 companion paired
successfully on the existing browser identity, and its current-connection check
succeeded. The 0.8.7 update fixes stuck progress and stale popup replies; its public
package and the same-folder local source update are verified. Native reload,
original-file review, cancellation before Fill and exact-device revocation await
Mac unlock. No employer form was filled and no application was submitted in this
current acceptance attempt. Earlier mobile/desktop and SEO observations remain
scoped to their original releases; they are not fresh field performance proof.

New AI generation/tailoring, unified credits, funded free-core processing,
checkout and final employer submission remain closed. Worldwide expansion,
commercial launch and the new credit/quality program retain their separate gates.

## Latest verified releases

| Component | Current release | Verification |
|---|---|---|
| API and application workers | Source `e23c940e6133323c3c1ff11e646a4ca9b2b0b165`; image `sha256:df4618fa19a05645384a4e0aba92ad691e5f40c55344b34da55c95fc1b44c77f`; schema `20261028_0025` | Immutable build `3a251d77-44e7-4b7a-9987-2c991c8e6d89`, exact-source CI `41095660943`, and accepted app staging/promotion readbacks. All three app services serve that release at 100%. Current owner journey acceptance remains pending. |
| Frontend | Source `8648118cace6284f5b7e7347607475d6cb4e0f92`; `dpl_9XTqBWiudei8KdkYikG6wV1YoK14` | Exact-source CI `41158935649`; the same staged production artifact was promoted on both public domains. Public readiness returns204; invalid Origin returns403. The 0.8.7 package matches its reviewed hash;0.8.6 remains unchanged. |
| Document inspector | Image `sha256:e2880102f6149cd62c800fbd33c775ed371c98a2ca74dce4935374685488fbef` | Accepted native continuation `41119586258`, original build `0d685ff9-d002-4e4b-912d-a8b64c6592ea`, and accepted serving proof. Definition proof expires `2026-11-02T06:26:48Z`. This is not the representative 200-case quality gate. |
| Companion | Public/source files **0.8.7**; native loaded version last observed **0.8.6** | Same browser installation, keys and permissions retained. Repaired pairing succeeded. The 0.8.7 reload and review/cancel/revoke require fresh verification; 26 October acceptance below remains historical. |

Schema26 remains source-only in PR83. Its latest exact-source check retained all 54
PostgreSQL cases and the prior v1/v2 checks, then stopped when the inner protocol
selection reached 240 seconds. Its active case was not retained. The reviewed
follow-up adds source-allowlisted timeout diagnostics without changing that limit
or removing required cases. Separate maintenance closure, retired-revision and
typed account-operation checks remain required before any database upgrade.

The earlier 29 October configuration and responsive measurements remain recorded
in the linked historical release evidence. The coordinator retains its separate
captured source `7a00839d`; it is not relabeled as an app/document release. The
current frontend update changes no backend configuration, database, credentials,
paid plan or activation gate. Temporary health-check IAM access was removed.
Manual frontend domain assignment was reapplied after promotion; a fresh native
settings-screen read remains pending while the Mac is locked.

The Gemini monthly spend cap remains **INR450**. Provider enforcement can lag;
application cost reservations and reconciled financial qualification remain
required. Retail purchases are unavailable.

[Current release evidence](evidence/2026-10-31-pilot-deployment-status.json) and
[the eight-stage credit/quality checklist](UNIFIED_CREDIT_ROLLOUT.md) distinguish
completed deployment work from remaining functional and commercial acceptance.
CI checks are evidence, not additional development checkpoints. No original total
effort estimate is retained; missing estimates remain null.

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
