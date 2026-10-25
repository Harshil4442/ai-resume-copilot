# HireWiz development and deployment progress

Updated **25 October 2026 (IST)**. The bounded-identity API repair and companion
0.8.3 are deployed. Original outcomes **1, 2 and 4 are COMPLETE within the approved
free twenty-origin pilot**; **3, 5 and 6 remain OPEN** for the live native-browser
acceptance below. Deployment alone does not complete that acceptance.

## Latest verified releases

| Component | Accepted release | Verification |
|---|---|---|
| API | Generation **335**, revision `ai-resume-parser-id25-23658ba44cb3`; source `436585cdd92a9c1e09c0d630eb73e9c61e9b1a78`; image `sha256:2440572bc905d228f0f3e215d8d22b98e54a6d46942ac4382d88f3452bcef029` | Stage, promotion and independent readback passed; previous generation 333 revision retained for rollback. Normally merged [PR51](https://github.com/Harshil4442/ai-resume-copilot/pull/51). |
| Frontend | `dpl_BPxz9EA5kySSLWhfAK1sQsTKPH8b`, source `204c295221d84c43785a0a3c326b4146e7458a5c` | Production/READY; both canonical aliases verified at **03:02:28 UTC**. Apex-to-www 308 routing and disabled automatic domain assignment remain intact. Previous 3D4 deployment retained for rollback. Normally merged [PR52](https://github.com/Harshil4442/ai-resume-copilot/pull/52). |
| Companion | **0.8.3**, **67,235 bytes**, SHA-256 `8f5597efa2549fa0b6ecf76545ce4cbc9b78f9e1488f7ad8c1fd74ba6c051d45` | Anonymous canonical download returned 200 and matched the reviewed ZIP. The public setup page linked to 0.8.3 at **03:12:10 UTC**. All 18 source files in the existing installation match; actual reload and native canary remain pending. |

A fresh five-service observation at **03:08:30 UTC** found API 335, analysis 63,
employer 31, coordinator 22 and inspector 14 Ready at their expected revisions.
Schema remains **`20261023_0021`**. The worker/coordinator application release
retains source `7a00839d` / image `c1ce5b32`; the accepted inspector image is `9e402666`,
with definitions valid until **27 October, 06:26:10 UTC** at its accepted observation.
The API-only release preserved the other services, native configuration, IAM,
queues and schedules. Three worker queues remain enabled; the employer-application
queue and sales/checkout/employer-write controls remain held.

Required exact-source release checks passed for
[API source 436](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/40148931093)
and [companion/frontend source 204](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/40153950942).
These are release evidence, not additional development checkpoints. The current
application release remains immutable when this status documentation changes.

## Original six outcomes

| Original outcome | Status | Accepted evidence and remaining work |
|---|---|---|
| Official job search, ranking and user-selected job counts | **COMPLETE — free pilot** | Twenty reviewed employer origins; an actual requested three-job Software Engineer search returned **3/3 engineering roles**, quoted 1 credit per new job, charged 3, released 0 and left no hold. Deterministic fit is explained without treating it as hiring probability. The earlier wrong-role search and later replacement-job QA are separate records. Worldwide coverage and greater-than-95% recall are unverified follow-on work. |
| Resume tailoring preserving PDF, DOCX and LaTeX formatting | **COMPLETE — free pilot** | Real PDF Version 3 retained four safe edits, one page, nine links and 147 unedited positions; an unsafe proposal was omitted. Actual native V2 edited-DOCX cases prove fit-preserving editing and unsafe link/reflow refusal. Owner DOCX/TeX/ZIP uploads and exact source snapshots, plus sealed TeX/ZIP preview/download equality, are separately verified. Snapshots do not prove new tailoring. Supported-layout limits and conservative original/custom-file fallbacks remain; universal format, ATS or selection guarantees are not claimed. |
| Candidate-reviewed direct company applications | **OPEN** | Sign-in, email reset, native enrollment and original-file/first-name-only application preparation passed. The retained connection now has actual signed EXPIRED/same-key-UNCLAIMED evidence. Its attempted archive refused the old four-record capacity; 0.8.3 fixes that bound without dropping records. Still required: reload the same installation, fresh signed reconciliation/archive, one separate native pairing, exact Razorpay portal inspection, exact original-file/answer review, approval and cancellation **before Fill**, native device revocation and the exact-ID zero-effects/zero-application-charge readback. No identity seed or employer-submission smoke test is permitted. |
| Paid search/application credits, profitable finite packs and payment recovery | **COMPLETE — free pilot** | Three finite packs, separate service/AI balances, separate search/application quotes, reservations, idempotent settlement and recovery controls are implemented. Actual engineering-search settlement proves 3 reserved/charged, 0 held/released. Expense policy 3 and model policy 2 retain accepted authority. Sales, real capture/refund acceptance and measured profitability remain held follow-on work; unresolved provider expense must not become zero cost. |
| Fast responsive pages and SEO | **OPEN** | Prior public and authenticated viewport, keyboard/navigation, metadata and reset-token privacy observations remain accepted. Deployed companion-route navigation suppresses unrelated profile/features and cached-balance requests. Remaining authenticated connection/review, expiry/error and privacy behavior must pass in the same live journey. Complete accessibility and field p75 LCP/INP/CLS remain unmeasured. |
| GCP/Vercel deployment, preserved-data migration and monitoring | **OPEN** | Schema 21 migration, reopened worker services, API 335 and frontend BPx deployment/readback are accepted. All five services are Ready; rollback revisions and application holds are preserved. Final signoff still requires the same live native journey, exact accounting/effect observation and final held-control reconciliation. Configured alerts and scheduler transport are distinguished from actual condition-specific delivery and downstream completion. |

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

Companion 0.8.3 preserves native protocol 2, executor `companion_0_8_1`, extension
identity, optional permissions and version-two checkpoints. It retains up to
**64 prior records / 256 KiB**, refuses archival beyond that history bound and
refuses oversized checkpoints. It never truncates history. Fresh signed EXPIRED/UNCLAIMED checks remain mandatory before archival;
Start is a separate explicit action. The old 0.8.2 archive is retained. See the
[package and existing-install update guide](../../browser-companion/README.md).

The approved completion scope is the **free-prelaunch, twenty-origin,
Razorpay/Greenhouse reviewed-browser pilot**, using GCP and Vercel Hobby until
sales. No real payment or employer application is a smoke test. Paid activation,
worldwide expansion, independent coverage assessment and commercial economics
remain follow-on work. Mobile users retain the website and manual company-portal
path; the companion requires Chrome desktop.

Manual document maintenance run 40109020215 passed local/native acceptance and
restored admission. Natural expiry-monitor runs, including 40151669591, succeeded
without executing definition-refresh steps. Scheduler transport success does not
establish downstream completion or stable unattended daily definition refresh;
that cadence remains unproven.

Reliable original estimates and aggregate actual durations for the six outcomes
were not retained. No expected-versus-actual totals or dependable completion ETA
are reconstructed from agent runtime or unrelated test timings.

## Preserved history

The [full prior board at its immutable Git revision](https://github.com/Harshil4442/ai-resume-copilot/blob/520df44e9fd7fe4e83f4835478552b76e41034fe/docs/delivery/AUTO_APPLY_PROGRESS.md)
retains correctly resolved historical Markdown links. Its exact bytes are also
preserved in the [raw archive](archive/AUTO_APPLY_PROGRESS_BEFORE_2026-10-25_STATUS.txt).
Earlier dated reports remain historical evidence, not current rollout authority.
