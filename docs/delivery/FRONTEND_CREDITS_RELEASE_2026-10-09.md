# Service-credit and mobile navigation frontend release

Released 9 October 2026 (Asia/Kolkata). The tested frontend is live at [HireWiz](https://www.hirewizhq.com/).

All three service-credit purchase links open the correct 500-credit catalog pack. Candidates can still choose a different product; a URL never creates a payment. The server controls price, eligibility and fulfillment, and purchase still requires billing-country confirmation and an explicit payment action. The mobile menu now becomes interactive after React attaches its handlers, preventing an enabled server-rendered toggle from accepting a premature action.

## Exact release and verification

- Commit: `06cc112278e94aadb358fbe347f5430e373c4eb1`; frontend tree: `bbb561d17b7d93362d3eccb9ea558c49f9e5295a`.
- Vercel production deployment: `dpl_5hkC8opsk2tt2SXLpBXJsYbn4GUu`. Both `www.hirewizhq.com` and `hirewizhq.com` point to it. The accepted production-target build was promoted directly without rebuilding.
- [Exact GitHub CI](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37871095792): all five jobs pass; 1,607 backend tests without failures or skips and 205 responsive browser cases. Current frontend units remain 72; the connected browser runner has 11 checks.
- Staged and live acceptance each pass 15 public route/width checks across 320, 390, 768, 1024 and 1440px, with no accessibility or horizontal-overflow failures. Four cookie-free private BFF routes return application 401 responses; the employer workspace redirects to login. Live mobile appearance was visually inspected. Public checks have no candidate login, payment, employer mutation or protection configuration changes.
- The exact CI connected journey exercises Chromium, NextAuth, BFF, FastAPI and disposable PostgreSQL with synthetic external payment/feed responses. It confirms zero AI calls, exact original/custom resume bytes, stale and cross-owner rejection, one credit grant, delivered-job accounting, unused-reservation release and zero automatic-application fees for manual handoff. It does not provide a real employer receipt.

The exact new Vercel deployment's requested 15-minute error-level scan returned zero entries, below its 50-entry cap. A separate GCP baseline over 01:23–01:53 UTC observed 24 HTTP 200 request entries and zero severity-at-least-ERROR entries across the three serving services, each below its 1,000-entry cap. Those small samples, background-worker durations and eventual logging limits do not establish sustained SLOs or field page performance. Raw log messages and candidate content were not retained in those monitoring reports.

## Backend and remaining full-service work

GCP health still identifies `e7d8f2b`, API revision `ai-resume-parser-00267-xot`; serving worker revisions remain analysis `00021-5zd` and employer `00012-lmz`. This release changes no backend traffic, credentials, queues or migration. The last verified production schema is `0009`; monetary `0010` remains unpromoted.

The financial SQL preflight remains outside root. Its V3 bounded review passed 71 author and 43 independent cases; an additional genuine EXECUTE-only privileged-function probe then found a writer-classification gap. That HOLD and the earlier scoped CLEAR are both preserved; V4 repair is underway. The independent journal-publication repair is also outside root pending review: it is a disabled 63-record prototype, not production recovery or scalable candidate availability.

Native paired browser assistance, permitted portal completion, full resume visual/LaTeX support, measured coverage, provider credential fencing, monetary rollout, restore/load/field-performance evidence and complete deployment remain open in [remaining requirements](REMAINING_REQUIREMENTS.md). The full goal is active. [Hash-bound release evidence](evidence/2026-10-09-frontend-credits-release.json) records actual runtime and proof scope.
