# Local Acceptance Report

Date: 2026-08-03

This report records what was verified before opening the architecture-upgrade pull request. It deliberately separates local proof from production behavior that requires HireWiz infrastructure, provider credentials, or real traffic.

## Automated Verification

| Gate | Result |
| --- | --- |
| Backend tests | 55 passed |
| Prompt contract evaluations | 4 passed |
| Modern backend Ruff scope | Passed |
| Modern backend mypy scope | 28 source files passed |
| Backend bytecode compilation | Passed |
| Frontend ESLint | Passed with zero warnings |
| Frontend strict TypeScript | Passed |
| Frontend Vitest | 1 passed |
| Next.js production build | Passed; 41 routes generated |
| Responsive Playwright and Axe | 20 passed across two runs at 320, 390, 768, 1024, and 1440 pixel viewports |
| PostgreSQL migrations | Upgrade to head, downgrade to baseline, and re-upgrade to head passed on PostgreSQL 17 |
| Production container | Built from the locked runtime, excluded development tools, migrated on startup, and returned a healthy API response |
| Secret scan | Current source passed; two historical fingerprints were baselined pending credential rotation |
| Critical production dependency audit | Passed |

The production audit still reports two high-severity advisory records through Next.js's optional Sharp 0.34 line. The patched Sharp release is outside Next.js's declared compatible range. Safe DOMPurify, Lodash, Picomatch, and PostCSS updates were applied; forcing the remaining audit fix would downgrade Next.js and is not accepted.

## Full-Stack Journey

The acceptance environment used a disposable SQLite database outside the repository, a local FastAPI API, and a local Next.js server. A private local resume was parsed for the journey; its binary and extracted personal content were not added to the repository.

The following customer flow passed:

1. Register through the same-origin BFF and establish a NextAuth session.
2. Upload and parse a PDF resume.
3. Create an opportunity with an immutable role snapshot.
4. Import resume evidence, approve a fact, edit it, verify that approval resets to `pending`, restore it, and reapprove it.
5. Save and approve a role-specific resume version.
6. Add a reminder and contact.
7. Move the application to `applied` using the exact submitted resume version.
8. Export the private opportunity record.
9. Start an analysis without an LLM credential, observe a durable failed state, and verify that reserved usage is released.
10. Record an outcome and confirm the activity history.
11. Load billing with no recent order and inspect Premium and usage-ledger states.

Signed workspace and billing checks showed no horizontal overflow at 390 and 1440 pixels and no automatically detected Axe findings. Public responsive coverage is automated in Playwright.

## Not Proven Locally

- Cloud Tasks delivery, OIDC, private worker IAM, queue retries, and Cloud Scheduler execution in the production GCP project.
- Vercel preview behavior with production-style domains and secrets.
- Live Razorpay checkout, webhooks, replay, refund, and entitlement delivery.
- Resend domain verification and lifecycle delivery.
- PostHog, Sentry, and Cloud Logging data arrival in production projects.
- Real traffic latency, Core Web Vitals, conversion, retention, AI margin, and support-rate targets.

These remain explicit owner steps in the deployment runbooks and release checklist.
