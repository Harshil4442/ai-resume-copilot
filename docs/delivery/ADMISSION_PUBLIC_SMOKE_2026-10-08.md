# Promoted candidate public UI smoke

Release: `7cc49c2c08a296b33cb30f96e383b959caf214c0`. Vercel deployment: `dpl_Ddh2JvT25wWFBFbQWiibfES9B37e`.
Origin: https://www.hirewizhq.com. Completed: 2026-10-08T16:35:01.734Z.
Identity attribution: Supplied by release owner after exact deployment promotion; public UI alone does not prove the commit.

20 homepage/pricing/login/employer-job-handoff cases at 320, 390, 768, 1024 and 1440 px. No unresolved failures observed.

Checks cover the 44px mobile menu control and desktop replacement, keyboard/tap navigation, visible focus, reduced motion, overflow, runtime/console errors, both pricing cards and labeled login fields. Two separate public catalog GETs record current product values and response cache policy. No account, payment, application or consent mutations were made.

| Route | DCL median (ms) | LCP median (ms) |
| --- | ---: | ---: |
| home | 433 | 624 |
| pricing | 730 | 864 |
| login | 455 | 476 |
| jobs-handoff | 514 | 864 |

Maximum observed CLS: 0.092. Measurements are unthrottled local-network lab observations, not field Web Vitals, production percentiles, INP or a load test.

The [committed aggregate](evidence/2026-10-08-admission-hosted-responsive.json) retains sanitized outcomes and measurements. Case-level `results.json`, `summary.json`, all 20 full-page screenshots, readable top crops and four overview contact sheets remain in `/tmp/hirewiz-public-smoke-next-7cc49c2-1791477238926/`. Those local temporary files are supplementary, not durable repository evidence. Environmental resource warnings are preserved separately from functional failures.

The [first-run aggregate](evidence/2026-10-08-admission-hosted-initial.json) is retained in the repository; its detailed report remains at `/tmp/hirewiz-public-smoke-next-7cc49c2-1791476705670/REPORT.md`. That run incorrectly blocked the public NextAuth `GET /api/auth/providers` configuration read, causing login bootstrap console errors and attempted error-log POSTs which the write guard blocked. The public provider configuration route was verified from the installed NextAuth source before adding it to the public GET allowlist. The harness now waits for anonymous session and login-provider bootstrap responses to finish before menu interaction. Two initial 320px menu timeouts did not recur after that readiness correction. These are harness corrections; no product source or production settings changed between the runs. The final run's 20 cases all passed without retries.

Visual review covered all 20 readable top crops, four full-page overview contact sheets, and representative open-menu screenshots at 320, 390, 768 and 1024px. No page or navigation clipping was found. The untouched first-visit cookie preference banner is visible in the screenshots and overlays content by design; consent was not changed in this read-only smoke.

The 15 remaining resource warnings are fetch `net::ERR_ABORTED` events on public `/pricing`, `/login` and `/register` routes during navigation/prefetch. All document loads and functional checks passed; there were no failed document/script/style/font loads classified as a functional failure. Final evidence contains zero attempted writes and zero blocked private reads. No further optional tests or source changes followed visual review.
