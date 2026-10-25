# HireWiz development and deployment progress

Updated **26 October 2026 (IST)**. Companion 0.8.4 and the frontend transport
repair are deployed. All six original outcomes are **COMPLETE within the approved free twenty-origin
pilot**. The actual native review/cancel/revoke journey and final operational
readback passed. Commercial launch and worldwide coverage expansion remain
outside this completion scope.

## Latest verified releases

| Component | Accepted release | Verification |
|---|---|---|
| API | Generation **338**, revision `ai-resume-parser-d-a1fc222a7ed3-open`; source `436585cdd92a9c1e09c0d630eb73e9c61e9b1a78`; image `sha256:2440572bc905d228f0f3e215d8d22b98e54a6d46942ac4382d88f3452bcef029` | The original generation 335 repair passed stage, promotion and independent readback. Natural document maintenance subsequently updated the document pin and serving revision; the API image, source and pairing/account configuration are unchanged. Normally merged [PR51](https://github.com/Harshil4442/ai-resume-copilot/pull/51). |
| Frontend | `dpl_BgUZzrsgUgCeDm6Uwdsojh2svCqx`, source `02bcab79e28ab292dc6a8efc0c3a06bcd02c97fa` | Production/READY; both canonical aliases verified at **18:19:21 UTC on 25 October**. Apex-to-www 308 routing and disabled automatic domain assignment remain intact. Previous BPx/source 204 remains available for rollback. Normally merged [PR55](https://github.com/Harshil4442/ai-resume-copilot/pull/55), merge `0d2af52d`. |
| Companion | **0.8.4**, **67,399 bytes**, SHA-256 `a1324aeda3b168a519ed47ab7a2a13228c91d5e6aa5ab2bea9ab9ba0d09f8c40` | Anonymous canonical download returned 200 at **18:19:36 UTC** and matched the reviewed ZIP. All 18 installed source files match; native Chrome showed 0.8.4 after reloading the same extension. Actual pairing, exact-file review, approval, cancellation before Fill and native device revocation passed with the same key and retained history. |

The final five-service readback at **18:35:50 UTC on 25 October** found API 338, analysis 66,
employer 31, coordinator 25 and inspector 15 Ready at their expected revisions.
Schema remains **`20261023_0021`**. The worker/coordinator application release
retains source `7a00839d` / image `c1ce5b32`; the accepted inspector image is now
`b5d97436`, with definitions valid until **28 October, 06:24:12 UTC**. The scheduled
[document refresh run 40169964119](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/40169964119)
completed its build, actual native V2 ten-case proof, exact pin transition,
admission restoration and cleanup. Its updated caller revisions retain their
application images. Pairing/account configuration is unchanged. Three worker
queues are RUNNING, the employer-application queue is PAUSED, and all three
schedules are ENABLED. Public API health returned 200 for the exact release.
Checkout, extended typed controls, attachment/native upload and final/automatic
submission remain disabled; basic reviewed assistance remains enabled. The final
readback reported no drift. Its retained evidence digest is
`95c3a4521810d96849c11acffb7fe7375fab69487b505d2bc28af27e01c164cf`.

Required exact-source release checks passed for
[API source 436](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/40148931093)
and [companion/frontend source 02bc](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/40216952903).
These are release evidence, not additional development checkpoints. The current
application release remains immutable when this status documentation changes.

## Original six outcomes

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

The approved completion scope is the **free-prelaunch, twenty-origin,
Razorpay/Greenhouse reviewed-browser pilot**, using GCP and Vercel Hobby until
sales. No real payment or employer application is a smoke test. Paid activation,
worldwide expansion, independent coverage assessment and commercial economics
remain follow-on work. Mobile users retain the website and manual company-portal
path; the companion requires Chrome desktop.

Manual document maintenance run 40109020215 passed local/native acceptance and
restored admission. Earlier natural expiry-monitor runs, including 40151669591,
succeeded without executing definition-refresh steps. The later scheduled run
40169964119 completed an actual refresh and restored admission; its downstream
result and current pins were independently reconciled. This is one observed
natural refresh. Stable unattended daily cadence and every condition-specific
alert delivery remain unproven.

Reliable original estimates and aggregate actual durations for the six outcomes
were not retained. No expected-versus-actual totals or dependable completion ETA
are reconstructed from agent runtime or unrelated test timings.

## Preserved history

The [full prior board at its immutable Git revision](https://github.com/Harshil4442/ai-resume-copilot/blob/520df44e9fd7fe4e83f4835478552b76e41034fe/docs/delivery/AUTO_APPLY_PROGRESS.md)
retains correctly resolved historical Markdown links. Its exact bytes are also
preserved in the [raw archive](archive/AUTO_APPLY_PROGRESS_BEFORE_2026-10-25_STATUS.txt).
Earlier dated reports remain historical evidence, not current rollout authority.
