# Tailoring recovery browser fixture correction

Recorded 11 October 2026, Asia/Kolkata. This checkpoint changes only
`frontend/e2e/resume-tailoring.spec.ts`; application runtime is unchanged.

Guidance commit `ca5ea7b` retains terminal FAILURE in
[run 38078938219, attempt 1](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/38078938219).
Backend passes 3,464 cases without skips, native document/compiler checks, 40 migration
transitions and API drift. Security, companion and connected cold-browser lanes pass.
Frontend passes 365 units, types, lint and its normal production build, but four old
banner scenarios at five widths give 20 responsive failures alongside 195 passes.
The original failed log has SHA256
`3fc3de8080b9245488f50c0f30040205bfbd30039f3df09defbdee9b031f1248`.
Root verifies 58 ordinary source hashes and all 34 retained artifacts. Backend log
transport retains only strict aggregate/known-summary projections; no raw backend
log or protected source trace is retained.

The old mocked responses omit `failure_guidance` and expect earlier exception-class
headings. The current UI correctly falls back to a fixed general explanation when
the finite reason is unavailable. The correction retains three omitted legacy
responses and one explicit-null response. It adds typed layout-fit, protected-link
and no-supported-change cases; settlement messages are checked separately from the
next step and original-preservation explanation. Existing nonfailure test bodies,
accessibility helpers and ordinary action checks remain unchanged.

Each scenario verifies that failure does not create a version or fetch a result,
and offers explicit unchanged-source and original/custom choices. An additional
browser flow switches resumes while the connection GET is deliberately delayed.
The banner's snapshot action stays disabled during that connection. After it settles,
one explicit click saves resume 2 with empty source edits and its own evidence; it
does not generate again or save the previous resume.

The primary agent runs the actual Next production build and Chromium fixture suite:
95 cases, 19 scenarios at 320/390/768/1024/1440 px, zero retries, failures or skips.
Each flow checks horizontal overflow, page errors and Axe accessibility. TypeScript
and edited-path ESLint pass. Two initial private build prerequisites are retained:
Turbopack refuses an external dependency symlink, then production build refuses a
missing synthetic authentication variable. A private APFS dependency clone and the
explicit existing dummy authentication fixture correct those prerequisites without
changing compiler settings, production authentication guards or test bodies.

Independent review verifies the original/postimage, eight author artifacts, all
95 structured browser results and 190 other tracked frontend files unchanged.
That count includes tests, configuration and assets; it is not a runtime-only count.
No duplicate full-suite browser rerun is made. Root verifies four independent artifacts
and integrates exactly one shipping file. Shipping SHA256 is
`00c4f1a70c71d4c5323ec504f9ecbf7cef457885ce0f4250d24f3f7fdb65b915`.

The fixture intercepts backend requests; this is browser interaction/accessibility
proof, not actual backend, model, employer, payment or production acceptance. The
earlier connected cold-browser journey separately uses actual local API/auth/SQL and
synthetic external providers. Neither closes the full customer resume workflow.

[Sanitized evidence](evidence/2026-10-11-tailoring-browser-recovery.json) records the
artifact hashes and scopes. The original failed CI is not rerun or relabeled green.

- [x] Preserve and classify the original 20 responsive failures.
- [x] Retain legacy fallback coverage and add finite guidance/current-source actions.
- [x] Pass 95 production-browser fixture cases across five widths.
- [x] Independently review and integrate the one-file correction.
- [ ] Publish and verify the corrected committed source in hosted CI.
- [ ] Verify the actual composed backend/source workflow and customer deployment.

The GCP scanner/storage gates, protected browser integration, monetary migration,
closed checkout and paused analysis dispatch remain unchanged.
