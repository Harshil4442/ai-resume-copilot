# HireWiz development and deployment progress

Updated **14 November 2026 (IST)**. The API, analysis worker and employer
worker each send 100% of their traffic to DB8 on schema26. The separate résumé
coordinator serves its retained 7a00839 image. The live frontend reduces
sign-in and Dashboard requests, checks that a browser-review download matches
the selected résumé, restores keyboard focus when application review closes,
and guides users through secure sign-out when their backend session expires.
Jobs now paces its first reads and offers a single retry after a capacity refusal.
The merged saved Workspace and interview history work has a matching GCP image
and staged frontend. [Serving source](https://github.com/Harshil4442/ai-resume-copilot/commit/56e0e6ce0408ad93233e71eaf097cb39da2b719f) ·
[Merged Workspace changes](https://github.com/Harshil4442/ai-resume-copilot/pull/125)
The active 27 October credit/cost/quality goal across the
existing eight stages remains incomplete. The six accepted 26 October outcomes
below retain their original historical scope.

A fresh owner journey on the deployed website and existing companion reviewed
the exact original resume and only the saved first-name answer. The original PDF
download matched its retained digest and its one-page layout was visually intact.
Assistance was cancelled **before application approval, Fill, attachment upload or
submission**; revocation of the exact existing device was verified in the UI.
The matching read-only SQL observation passed all ten checks and returned zero
for all twelve linked execution and accounting counts. The scope ends at
review, cancellation and revocation.

New AI generation/tailoring, unified credits, funded free-core processing,
checkout and final employer submission remain closed. Paid native document QA
(80 documents/nine jobs) and ordinary 17-case scanner maintenance remain on
**OWNER HOLD**. Broader native/model quality qualification remains pending.
Human Reviewer 2, representative shadow accounting, 10,000-user capacity,
commercial/finance gates and full R03/RTO qualification remain pending.

Razorpay acknowledged HireWiz’s request for written confirmation of the proposed
credit passes and candidate-reviewed application assistance. Merchant-scope
confirmation and Razorpay Test evidence for capture, refunds and reconciliation
remain pending.

The production shadow observation for 6–13 November UTC completed under the
read-only database role. The window contained no qualifying operations or
discovery batches. The report retained two unknown and three settled provider
liabilities, with zero SQL mutations or model calls. Representative shadow
accounting remains pending. [Observation evidence](evidence/2026-11-13-credit-shadow-observation.json)

The local PostgreSQL 17 recovery rehearsal restored a populated employer catalogue
and verified the schema, 84 table contents, nine sequence states, original résumé
bytes and payment replay. An isolated Neon recovery and measured production
recovery times remain pending. [Recovery runbook](UNIFIED_CREDIT_ROLLOUT.md#schema27-database-recovery-rehearsal)

## Latest verified runtime and pending releases

| Component | Current evidence | Verification and boundary |
|---|---|---|
| Serving API, analysis worker and employer worker | Source `db8a25adefaf3bc2e00a61f7543f8a1862597adf`; image `sha256:854c0361b7af3fb095f8869e02f156910ad6292d90b17c30e0d15efa53d01cbe`; schema `20261031_0026` | Retained DB8 Build `05fafcae-934e-448f-9790-26a60b6f3b64` and CI `41650961963`. Neither the new application image nor the new policy pair has replaced these serving revisions. |
| Résumé coordinator | Source `7a00839dc90a93caebb0b3b5f2e9e03c51e941e9`; active revision `hirewiz-resume-coordinator-d-32d98b931d16-open` | 100% serving on the retained revision. The latest-ready pointer names a different revision. |
| Production frontend | Source `56e0e6ce0408ad93233e71eaf097cb39da2b719f`, deployment `dpl_6e54y62QAnMnFEopXyTUk4iaE5rn` | Secure sign-out, fresh sign-in and saved-account reads were verified in production. The four initial Jobs reads that had hit the shared limit returned 200 in sequence after the fix. Synthetic mobile and desktop tests verified that one capacity refusal pauses the remaining Jobs reads until the advertised retry window ends and the user retries. The shared capacity limit remains in force. |
| Document inspector | Accepted image `sha256:562127e194be4479be9f52756dcd21830b6e64b8b250d39237643ead2c453e36` | Definition proof expired `2026-11-05T06:24:10Z`. The 17-case maintenance and 80-document/nine-job QA holds remain. |
| Companion | Public/source package **0.8.9** | Review `8a459084-d4ff-4819-adf1-8c1184feeb46` bound resume `28`, original SHA-256 `054559b40df2511d7ba6561b04e7e50b208a70fb4cca45d2382191a102a8dc58`, and only `first_name`. Cancellation preceded application approval, Fill, upload and submission. Device `23d49cfd-0140-4254-afd8-87881b56b423` was revoked. The exact review and revocation match the read-only SQL observation. |
| Restored prior Cloud Run templates | Original staged source `9b9d9c33dfae519b95fbc380fc99ba6bcf68e8a4` | The qualified controller completed all three DB8 template restores on 5 November. The 13 November read confirmed the original aliases, sole 100% DB8 traffic and removal of the 9b9 staging aliases. |
| Bridge application image | PR125 merge `4dc9aea40e11a55f2d137abbdd446edcd9051e98`; image `sha256:08888d36aacf24a8a5cde68935abac7ff69e5fb2c1062badc60786df5a1321a9` | Build `2bccad42-17dd-4f95-adb4-9bfe318739a0` succeeded. The merged tree equals the tested PR tree. Exact main CI and full release qualification remain pending. Cloud Run staging requires current scanner verification and funding evidence. [Main CI](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/43582145259) |
| Bridge frontend | PR125 merge `4dc9aea40e11a55f2d137abbdd446edcd9051e98`, deployment `dpl_3XeVpMRVV34CAB6HEeHxyMQYqTpL` | The build is ready at the Git main alias. The Workspace and résumé pagination routes require their matching API before canonical promotion. |
| Reviewed policies | Expense policy **5**, model policy **3** | Both expired on 11 November at 22:34 UTC and remain unreferenced by the serving services. The replacement pair remains unpublished. |

The PR114 source-image verification record has SHA-256
`6d851cbaf7077930c7c74c5959479965bc3e037737f261da45a44ff4e27129e2`.
The current owner review/cancel/revoke UI record has SHA-256
`b73c37dcd010d24df5b2af19c8d9a9572110284bf91f078675ab8be563286afb`.
The matching SQL observation has SHA-256
`c716b36be4b13666ae39744d4fb2591736ce5bf820111f51bd56ec4c79239875`.
The completed restore receipt has SHA-256
`0db12c8baa4ae658cf821f5d5edb69048e55cef4a9c46f0911294fea205a6511`.
The old staging plan and journals remain under their original custody. The next
forward rollout needs exact release qualification and current policies with at least
four hours of validity at capture. New traffic promotion, rollout financial
readback and full R03/RTO qualification remain pending.

The Gemini monthly spend cap remains **INR450**. Provider enforcement can lag;
application cost reservations and reconciled financial qualification remain
required. Retail purchases are unavailable.

The [earlier runtime snapshot](evidence/2026-11-05-runtime-status.json) remains
scoped to its recorded observation time. The [eight-stage credit/quality checklist](UNIFIED_CREDIT_ROLLOUT.md)
retains all eight stage rows and all 83 requirement IDs/statuses unchanged.
CI checks are evidence, not additional development checkpoints. Missing original
effort estimates remain null; no aggregate completion ETA is reconstructed.
Earlier dated observations below retain their original release scope.

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

The password-login improvement is included in qualified source `01b80b1` and
its verified application image, but is not yet serving. It combines three protected
post-activation finalization steps into one transaction, preserving fresh identity,
credential, mailbox and activation checks. Focused SDK-intercepted controls measured
account-path metadata calls falling from 29 to 25. This is not a whole-HTTP or live
speed measurement, does not change Google OAuth, and does not establish the
10,000-user capacity target. The same qualified source contains bounded resume
metadata pagination and the exact-session GET timestamp-hint improvement.
Reviewed header-read deferral on the four public authentication pages is prepared
for the matched frontend release; it changes no owner cache or backend authority.
Production publication and whole-journey capacity acceptance remain pending.

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
