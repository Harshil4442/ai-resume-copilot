# Fresh-user no-AI browser journey

This opt-in proof uses actual Chromium, a production-built Next.js server, NextAuth credentials login, the Next BFF, the complete FastAPI application, durable dispatch and a uniquely migrated PostgreSQL schema. It supplements `test_fresh_user_no_ai_journey.py`; it does not change or replace that backend proof.

## Isolation and provider boundaries

Run from a disposable credential-free source archive. The orchestrator accepts only PostgreSQL at `127.0.0.1:55433/hirewiz_admission_test`, creates a uniquely named `cold_browser_...` schema, and drops only that schema in cleanup. Other schemas, root source, containers and production services remain untouched. Every invocation starts fresh server processes and a browser context; initial users, resumes, postings, skill coverage, searches and model events must be empty. A real authenticated synthetic operator adds a synthetic source and requests refresh; the real outbox worker then indexes its finite feed before candidate search. Redis is unused; no shared keys or caches are reset. Cold refers to application/user data and runtime contexts, not an empty npm/Next build cache.

The harness never overrides application dependencies, JWT identity, ownership, SQL, parsing, ranking, search/credit services, payment verification, package approval or execution results. Browser BFF/backend routes are never intercepted. Only external boundaries are synthetic:

- Strict finite Greenhouse public GET feed/form transport; no employer POST or authenticated employer call is allowed.
- Synthetic Razorpay order HTTP transport, a synthetic external checkout script and a correctly signed synthetic captured webhook. These exercise actual order/callback/webhook/accounting code without a real transaction. The browser callback must not grant credits; replaying the webhook must grant once.
- Lifecycle email delivery is disabled. Model keys and cost-policy overrides are absent; optional generation and automatic submission are disabled. Model/SDK/attempt-admission calls fail and are counted. Unexpected provider HTTP or non-SQL backend socket egress fails. All browser contexts block unexpected external network requests.

Reports contain counts, static/sanitized paths, hashes and states, not passwords, session tokens, request bodies or private candidate content. DOCX files and screenshots are explicitly synthetic local fixtures.

## Journey and review boundary

The candidate registers through the real UI, checks current terms/age consent, uploads an owned DOCX with AI enrichment unchecked, saves available role/location/skill preferences, and explicitly fills the search form. This proves the existing preference subset; it does not implement the full salary, language, sponsorship or relocation requirements.

The candidate selects three jobs at a backend-sourced two-credit test price, purchases the server's 500-credit product through synthetic provider boundaries, receives two date/role-qualified jobs, and verifies reserve six/charge four/release two. Two nonqualifying finite feed rows test filtering and partial settlement.

The manual review UI displays the saved exact file/hash, resume/version, destination, saved answers, consent controls and handoff actions. It requires an explicit review before calling existing approve (`fill`, `upload`, **never `submit`) and execute contracts. The backend records `manual_handoff`. This preparation performs no employer upload, autofill or send; the final employer link is exposed after handoff and is not clicked by the test. No employer receipt or completed-application claim is synthesized, and no automatic-application credits are reserved or charged.

The candidate downloads exact original bytes, changes to a separate owned custom DOCX, saves, and must review again. An old digest cannot execute; the first approval is revoked, the current approval binds the custom artifact, and exact custom bytes are verified. A separately registered real browser identity cannot retrieve the first owner's source, package or artifact.

The browser checks the real review dialog at 320, 390, 768, 1024 and 1440 px for horizontal overflow and WCAG A/AA Axe violations, and records synthetic desktop/mobile screenshots. Local synthetic checkbox automation verifies the UI gate and server state; it is not proof of a human reading a DOCX in an editor or of real employer form completion.

## Explicit developer/CI orchestration

Prerequisites in the **archive only**: backend locked Python environment, Node 24, `npm ci --ignore-scripts`, `npx playwright install --with-deps chromium`, and the dedicated local PostgreSQL 17 test database. Do not copy `.env` files or credentials. Use the existing explicit disposable credentials; no production secrets are needed.

From `backend`:

```sh
.venv/bin/python scripts/run_cold_browser_journey.py \
  --allow-disposable-postgres --evidence /tmp/hirewiz-cold-browser-evidence
```

The orchestrator is outside pytest discovery; it runs explicitly and must pass in its own CI job. Ordinary frontend Playwright uses `e2e/`; this actual-stack journey lives in `journey/` and uses `playwright.cold.config.ts`. A separate CI job must install both runtimes/browser, provision only the dedicated disposable PG database on port 55433, and run the command above. The dedicated cold-browser-journey CI job provisions its own ephemeral PostgreSQL service and runs the same command; cloud configuration and deployment are unchanged. The orchestrator owns production build, bounded server readiness and process-group cleanup; it does not silently reuse a running server or set fabricated session cookies.

Failed startup or a failed browser run records a failed phase with source hashes and makes no zero-call success assertion. Previous evidence is cleared only in a directory marked as owned by this harness. Focused process tests cover the actual exited-command/live-child inherited-output timeout, successful commands leaving children, already-vanished groups, refusal to signal a reaped/reusable group ID, bounded SIGTERM/SIGKILL escalation, exited-server refusal and protection of an unowned evidence folder. The runner retains each session leader as an unreaped owned child until all live group members stop; this pins the group identifier against reuse. It records group-level verification before reaping, never reports cleanup from parent polls alone, and fails the run if cleanup cannot be verified. This POSIX harness verifies its owned groups; it does not claim to contain a malicious descendant that deliberately creates a different session.

Final evidence and source hashes accompany the frozen incremental patch. Production, real payment/provider authorization, employer permission, browser companion pairing, real portal autosave/upload, final submission and worldwide coverage remain outside this local proof.
