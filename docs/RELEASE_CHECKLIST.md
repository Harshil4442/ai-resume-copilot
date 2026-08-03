# Release Checklist

## Before Merge

- [ ] CI passes backend tests, PostgreSQL migrations, OpenAPI drift, frontend checks,
  Playwright/axe, secret scan, audit, and container build.
- [ ] Migration upgrade and downgrade behavior was reviewed; backup/restore is understood.
- [ ] Authentication, ownership, billing, usage, and evidence tests cover changed behavior.
- [ ] New events, flags, environment variables, retention, and support effects are documented.
- [ ] No secret, resume body, payment credential, or unnecessary PII appears in logs.
- [ ] Production dependency audit has no critical issue; any accepted upstream-only advisory has an owner and review date.

## Before Production

- [ ] Preview/staging registration, Google sign-in, resume, workspace, failed run, duplicate
  task, export/delete, and billing journeys pass.
- [ ] Worker queue depth, API/worker error rate, provider latency, and payment webhooks have
  dashboards and alerts.
- [ ] Database backup completed and rollback owner named.
- [ ] Flag starts with internal/invited audience; primary and guardrail metrics are named.

## After Production

- [ ] Check health, error rate, queue age, run success/release, checkout, and webhook metrics.
- [ ] Verify one real account can finish first value without support.
- [ ] Record release SHA and observations. Pause rollout on auth, billing, ownership,
  factuality, or data-integrity regression.
