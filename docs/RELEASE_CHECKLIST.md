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

## Paid employer service

- [ ] Verify requested job count, configurable search/application unit prices, exact
  immutable per-job/batch quotes and a separate service-credit balance. See BI01–BI07
  in [the requirements](requirements/JOB_SEARCH_AND_APPLICATION.md).
- [ ] Verify captured-payment grants, concurrent reservations, deduplicated delivered
  jobs, unused-credit release and refunds against the actual release. Manual handoff,
  a saved draft or an unverified employer response cannot incur a successful automatic
  application charge.
- [ ] Verify original/custom/tailored sealed file bytes, destination, answers, consent
  and price bindings. Changes require fresh review before filling, autosave or upload.
- [ ] Verify candidate/device ownership, recent authentication, cancellation, permission
  revocation, unknown-send reconciliation and restore-safe admissions. Never retry a
  possible send as a recovery strategy.
- [ ] Verify real protected-resource identity, retained history, permissions, old-writer
  exclusion, per-subject availability and measured scalable recovery. A disabled local
  foundation or an immutable resource UID does not establish these deployment controls.
- [ ] Resolve credential consumers, provider fencing and queue drain before monetary
  migration. The read-only [database helper](delivery/MONETARY_PREFLIGHT_2026-10-09.md)
  checks a bounded SQL gate; its `cutover_ready=false` result cannot authorize rollout.
- [ ] Attach permitted portal form/receipt evidence, complete-flow device checks,
  measured coverage/performance and monitored immutable deployment evidence for each
  claimed scope. Keep unsupported portals and uncertain outcomes visible.
