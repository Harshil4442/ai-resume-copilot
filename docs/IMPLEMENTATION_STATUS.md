# Architecture Plan Implementation Status

Date: 2026-08-03

This document separates code-complete work from production outcomes that can only be measured after deployment. It prevents a feature existing in the repository from being mistaken for a validated business result.

## Code Complete in This Upgrade

- Supported Node.js, Next.js, React, Tailwind, Python, FastAPI, SQLAlchemy, Psycopg, Alembic, uv, Ruff, and mypy foundation.
- Same-origin Next.js BFF with server-held backend access tokens.
- Versioned PostgreSQL migrations and migration locking.
- Durable analysis runs, private Cloud Run worker, Cloud Tasks dispatch, idempotent worker claims, and usage reserve, commit, release, and waiver accounting.
- Correlation IDs, structured logs, Sentry integration, model-call telemetry, configurable cost estimates, versioned prompt checksums, and deterministic prompt contract evaluations.
- Career Workspace with immutable job snapshots, match runs, evidence review, evidence-backed tailoring, resume versions, exact submitted version, contacts, reminders, stage history, final outcome, Career Memory, Skill ROI, and private opportunity export.
- Evidence facts are editable; any factual edit to approved evidence returns it to `pending` until the user explicitly reapproves it.
- Account export and deletion, sensitive analysis retention, lifecycle outbox, payment receipts, and protected audited support operations.
- Server rollout flags and consent-gated product analytics contract.
- Vercel plus Cloud Run deployment documentation, private worker runbook, release checklist, incident template, and CI security, contract, migration, accessibility, build, and container gates.
- Product redesign for the application shell, home, authentication, dashboard, resume, market research, profile, Career Workspace, and shared state components. Legacy job-match and learning pages now route into Career Workspace.
- Billing now shows the server catalog, Premium expiry, append-only usage history, checkout state, and a quiet no-order state without relying on error responses.

## Local Acceptance Completed

The implementation was exercised end to end against a migrated local database and the real Next.js BFF/FastAPI boundary. Registration, authentication, private resume parsing, opportunity creation, immutable role snapshotting, evidence import and approval, approval reset after a factual edit, resume versioning, contacts, reminders, stage submission with an exact resume version, private export, outcome tracking, and activity history all passed.

A deliberately failed analysis run also proved reserve-and-release accounting: the run reached a durable terminal state and the user's balance returned to its starting value. Billing loaded its empty recent-order state without a browser error. Signed workspace and billing screens had no automatically detected Axe violations or horizontal overflow at the tested desktop and mobile widths.

Automated results, environment details, and production-only exclusions are recorded in [LOCAL_ACCEPTANCE_REPORT.md](LOCAL_ACCEPTANCE_REPORT.md).

## Requires Owner Deployment

- Bootstrap the private worker, Cloud Tasks queue, IAM bindings, and Scheduler maintenance job.
- Configure PostHog, Sentry, LLM cost rates, and production release identifiers.
- Verify an email sender and explicitly enable lifecycle email.
- Apply migration `20260803_0003` through the normal deployment path.
- Roll out Career Workspace, asynchronous analysis, and evidence tailoring through server flags.
- Run live Razorpay checkout, webhook replay, refund, and entitlement checks in the provider dashboard.
- Rotate every credential exposed in chat, screenshots, shell history, or earlier deployments.
- Track the current transitive Sharp advisory until Next.js accepts the patched Sharp line; do not use `npm audit fix --force`, which currently proposes an unsafe framework downgrade.

## Production Acceptance Still Needs Traffic

These are not truthfully verifiable from local code:

- Median time to first useful match below 10 minutes.
- AI operation success above 97 percent.
- Core Web Vitals and API latency targets under real traffic.
- Activated-user conversion, retention, payment support rate, and paid-cohort gross margin.
- No redesign regression to activation or checkout completion.
- Feature rollout from internal to 100 percent without guardrail breach.

Capture a four-week baseline after instrumentation becomes trustworthy, then rebaseline targets in the architecture plan.

## Deliberately Off Until Evidence Exists

- `referral_credit` is implemented only as a disabled rollout boundary. Do not grant credits until verified paid-conversion attribution, self-referral prevention, refund reversal, velocity limits, and support rules are approved.
- Job Sprint and recurring subscription packaging are not added to the live catalog. Test one package at a time after recurring usage and AI margin are known.
- New job sources, browser extension, automatic applications, international payments, expert marketplace, voice interviews, and B2B tenancy remain Phase 8 decisions.
- Original resume binary retention and generated-file storage remain off. Uploaded bytes are discarded after parsing; add private GCS storage only when a product workflow actually needs retained artifacts.

The architecture is ready to support these experiments, but shipping unvalidated pricing or fraud-sensitive incentives would work against the plan's sequencing and safety requirements.
