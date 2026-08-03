# HireWiz Runtime Architecture

## Production Topology

```text
Browser
  -> Vercel / Next.js 16
     -> same-origin /api/backend BFF
        -> authenticated Cloud Run API
           -> PostgreSQL (system of record)
           -> Redis (cache and short-lived coordination only)
           -> Razorpay, market providers, and identity providers
           -> Cloud Tasks
              -> private Cloud Run analysis worker
                 -> LLM provider
                 -> PostgreSQL transaction: result + usage commit/release
```

The browser never receives the backend JWT or a provider secret. NextAuth keeps the
backend token in its encrypted server-side JWT and the BFF attaches it to backend
requests. Razorpay checkout remains hosted by Razorpay; HireWiz stores references and
signed event state, not card, UPI PIN, CVV, or bank-login data.

## Domain Boundaries

- `billing`: catalog, provider adapter, orders, webhooks, transactions, entitlements,
  and refunds.
- `career`: opportunities, application history, evidence, resume versions, reminders,
  contacts, Career Memory, and Skill ROI.
- `analysis`: durable run state, Cloud Tasks dispatch, worker execution, prompt/model
  telemetry, and results.
- `usage`: append-only reserve, commit, release, and waiver events. PostgreSQL is the
  billable source of truth.
- Legacy resume, match, learning, and market endpoints remain compatible while using
  the durable usage ledger. New product flows use `/api/v1`.

## Data Rules

- Every user-owned record carries `user_id`; ownership is checked before billable work.
- A job snapshot is captured when an opportunity is created and is not silently
  replaced by provider data.
- Generated candidate claims must cite approved evidence. Missing evidence produces an
  explicit evidence-needed state.
- Terminal analysis state and usage commit or release occur in one database transaction.
- Redis may be discarded without losing money, entitlements, run state, or user work.

## API Contract

FastAPI produces [openapi.json](../backend/openapi.json). The frontend client types in
`frontend/lib/generated/api.ts` are generated from that artifact. CI fails when either
artifact drifts.

Architecture decisions live in [`docs/adr`](adr/README.md). Operational rollout is in
[GCP_ASYNC_WORKER_RUNBOOK.md](GCP_ASYNC_WORKER_RUNBOOK.md).
