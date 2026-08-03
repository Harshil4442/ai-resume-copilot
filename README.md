# HireWiz

HireWiz is an evidence-backed career workspace. It helps a user preserve job snapshots,
compare approved resume evidence with a role, manage an application, create traceable
resume versions, prepare interviews, and decide which skills are worth learning.

HireWiz provides informational software, not recruitment, placement, or an employment
guarantee.

## Production Stack

- Next.js 16, React 19.2, TypeScript, Tailwind CSS 4, TanStack Query, and Radix UI on Vercel
- FastAPI, Python 3.12, SQLAlchemy, Alembic, and PostgreSQL on Google Cloud Run
- Google Cloud Tasks plus a private Cloud Run analysis worker
- Redis for optional caching, never billable or user-work state
- Razorpay hosted checkout with webhook-confirmed entitlements
- PostHog and Google Analytics after consent; Sentry in browser, API, and worker

The browser uses the Next.js same-origin BFF. Provider secrets and the backend JWT never
reach client JavaScript. See [the runtime architecture](docs/ARCHITECTURE.md).

## Product Capabilities

- Email and Google sign-in with per-user ownership controls
- PDF/DOCX resume parsing and review
- Career Workspace with opportunities, immutable role snapshots, stages, outcomes,
  contacts, reminders, and activity history
- Evidence Graph with explicit approval, provenance, and source-preserving deletion rules
- Durable asynchronous job match, interview coaching, market analysis, Skill ROI, and
  evidence-backed resume tailoring
- Versioned resumes whose generated lines cite approved evidence
- User-controlled Career Memory
- Append-only usage history with reserve, commit, release, and Premium waiver events
- Account data export and deletion
- Consent-gated funnel analytics, deterministic server feature flags, lifecycle messages,
  sensitive-payload retention, and audited support operations
- One-time 30-day Premium pass through Razorpay, with idempotent webhook fulfilment and
  refund-aware entitlement handling

Legacy backend APIs remain compatible during migration. The old `/jobs` and `/learning`
pages redirect into Career Workspace, where their replacement flows now live.

## Repository

```text
backend/
  alembic/                 Versioned database migrations
  app/domains/analysis/    Durable runs, task dispatch, operations, model telemetry
  app/domains/career/      Workspace, evidence, versions, reminders, memory, Skill ROI
  app/domains/notifications/ Durable lifecycle-message outbox and delivery
  app/domains/operations/  Scheduled retention and delivery maintenance
  app/domains/usage/       Audited unit reservation/commit/release
  app/billing/             Razorpay catalog, adapter, and ledgers
  app/main.py              Customer API
  app/worker_main.py       Private analysis worker
  openapi.json             Committed API contract
frontend/
  app/api/backend/         Authenticated same-origin BFF
  app/workspace/           Career Workspace
  lib/generated/api.ts     Generated OpenAPI types
docs/
  PRODUCT_ARCHITECTURE_UPGRADE_PLAN.md
  ARCHITECTURE.md
  adr/
```

## Local Development

Required runtimes are pinned in `.python-version` and `.node-version`.

```bash
python -m pip install uv==0.11.32
uv sync --project backend --all-groups
cd backend
APP_ENV=development DATABASE_URL=sqlite:///./app.db .venv/bin/alembic upgrade head
APP_ENV=development DATABASE_URL=sqlite:///./app.db .venv/bin/uvicorn app.main:app --reload
```

In a second terminal:

```bash
cd frontend
npm ci
BACKEND_URL=http://127.0.0.1:8000 \
NEXTAUTH_URL=http://127.0.0.1:3000 \
NEXTAUTH_SECRET=replace-with-at-least-32-random-bytes \
npm run dev
```

Open `http://127.0.0.1:3000`. FastAPI docs are at
`http://127.0.0.1:8000/docs` in local development.

For local asynchronous work, set `ANALYSIS_TASKS_MODE=inline` or `background`. Production
uses `cloud_tasks` only after the private worker is bootstrapped.

## Verification

```bash
cd backend
.venv/bin/pytest -q
.venv/bin/python scripts/run_prompt_evals.py
.venv/bin/ruff check app/domains app/services/guardrails.py app/routers/v1 \
  app/routers/worker.py app/worker_main.py app/observability.py app/feature_flags.py app/migrate.py scripts
.venv/bin/mypy app/domains app/services/guardrails.py app/routers/v1 \
  app/routers/worker.py app/worker_main.py app/observability.py app/feature_flags.py app/migrate.py

cd ../frontend
npm run lint
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Regenerate contracts after changing a backend route or schema:

```bash
cd backend && .venv/bin/python scripts/export_openapi.py
cd ../frontend && npm run api:generate
```

CI additionally validates migrations on PostgreSQL, detects contract drift, scans for
secrets, audits critical production dependency issues, and builds the production image.

## Deployment and Operations

- [Environment and deployment contract](docs/ENVIRONMENT_AND_DEPLOYMENT.md)
- [GCP async worker bootstrap](docs/GCP_ASYNC_WORKER_RUNBOOK.md)
- [Support and lifecycle runbook](docs/SUPPORT_AND_LIFECYCLE_RUNBOOK.md)
- [Product analytics contract](docs/PRODUCT_ANALYTICS.md)
- [Release checklist](docs/RELEASE_CHECKLIST.md)
- [Razorpay go-live checklist](docs/RAZORPAY_GO_LIVE.md)
- [Payment architecture](docs/PAYMENTS_ARCHITECTURE.md)
- [Product and architecture plan](docs/PRODUCT_ARCHITECTURE_UPGRADE_PLAN.md)
- [Implementation status and production gates](docs/IMPLEMENTATION_STATUS.md)
- [Local acceptance report](docs/LOCAL_ACCEPTANCE_REPORT.md)

Never commit `.env` files. Rotate any credential that has been exposed in a screenshot,
chat, terminal output, or Git history before production use.
