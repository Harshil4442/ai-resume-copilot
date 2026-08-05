# Environment and Deployment Contract

Never commit credentials. Vercel and Cloud Run hold production values; `.env` files are
for local development and are ignored by Git.

## Vercel Frontend

Required server-only variables:

| Variable | Purpose |
| --- | --- |
| `BACKEND_URL` | Cloud Run API origin, without `/api` |
| `NEXTAUTH_URL` | Canonical site URL |
| `NEXTAUTH_SECRET` | At least 32 random bytes |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | Optional Google sign-in |

Optional public observability variables:

| Variable | Purpose |
| --- | --- |
| `NEXT_PUBLIC_GA_ID` | Consent-gated Google Analytics |
| `NEXT_PUBLIC_POSTHOG_KEY` / `NEXT_PUBLIC_POSTHOG_HOST` | Consent-gated product analytics |
| `NEXT_PUBLIC_SENTRY_DSN` | Browser error reporting |
| `NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE` | Browser trace sample, initially `0.05` |
| `SENTRY_DSN` / `SENTRY_TRACES_SAMPLE_RATE` | Next.js server and edge errors |

Do not add `RAZORPAY_KEY_SECRET`, `JWT_SECRET`, LLM keys, database credentials, or the
backend bearer token to Vercel public variables. No frontend Razorpay key is required;
the server catalog returns the public checkout key only when checkout is eligible.

## Cloud Run API and Worker

Core required variables:

- `APP_ENV=production`
- `DATABASE_URL`
- `JWT_SECRET`
- `FRONTEND_ORIGINS=https://hirewizhq.com,https://www.hirewizhq.com`
- `LLM_API_BASE`, `LLM_API_KEY`, `LLM_MODEL=gemini-3.6-flash`
- `APP_RELEASE`, `LOG_FORMAT=json`, `LOG_LEVEL=INFO`
- `SENTRY_DSN` and an initial `SENTRY_TRACES_SAMPLE_RATE=0.05`

Async API variables:

- `ANALYSIS_TASKS_MODE=cloud_tasks`
- `GOOGLE_CLOUD_PROJECT`
- `ANALYSIS_TASKS_LOCATION`
- `ANALYSIS_TASKS_QUEUE`
- `ANALYSIS_TASKS_SERVICE_ACCOUNT`
- `ANALYSIS_WORKER_URL`
- `ANALYSIS_TASK_TOKEN` as defense in depth

The worker needs the database, LLM, observability, task token, and `APP_ENV` variables.
It runs with `SERVICE_ROLE=worker`. The API runs with `SERVICE_ROLE=api`.

AI cost and retention variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| `LLM_INPUT_COST_MICROS_PER_MILLION` | `0` | Input-token price in micro-units of the configured reporting currency per million tokens |
| `LLM_OUTPUT_COST_MICROS_PER_MILLION` | `0` | Output-token price in micro-units of the configured reporting currency per million tokens |
| `ANALYSIS_INPUT_RETENTION_DAYS` | `30` | Completed-run input payload retention |
| `ANALYSIS_RESULT_RETENTION_DAYS` | `90` | Completed-run result payload retention |
| `MODEL_TELEMETRY_RETENTION_DAYS` | `365` | Model-call telemetry retention |
| `NOTIFICATION_RETENTION_DAYS` | `90` | Sent or failed outbox-row retention |

Lifecycle and support variables:

| Variable | Purpose |
| --- | --- |
| `FRONTEND_URL=https://www.hirewizhq.com` | Links in lifecycle messages |
| `LIFECYCLE_EMAILS_ENABLED=true` | Enables durable welcome, onboarding, analysis, reminder, and receipt messages |
| `RESEND_API_KEY` | Server-only email provider credential |
| `EMAIL_FROM` | Verified sender, for example `HireWiz <updates@hirewizhq.com>` |
| `ADMIN_EMAILS` | Comma-separated accounts allowed to use protected support APIs |

Do not enable lifecycle email until the sender domain is verified. Invoke
`POST /internal/tasks/maintenance` on the private worker from Cloud Scheduler every
15 minutes with OIDC authentication. Configure the `X-CloudScheduler: true` header
and, when used, `X-HireWiz-Task-Token`.

Server rollout variables follow this pattern:

- `FEATURE_INTERNAL_EMAILS`
- `FEATURE_<KEY>_ENABLED`
- `FEATURE_<KEY>_ROLLOUT_PERCENT`
- `FEATURE_<KEY>_USER_IDS`

Current keys are `CAREER_WORKSPACE`, `EVIDENCE_TAILORING`, `ASYNC_ANALYSIS`, and
`REFERRAL_CREDIT`. Keep referral credit disabled until its conversion attribution and
fraud controls are implemented and approved. Production defaults fail closed when
rollout variables are absent; set each enabled key and percentage deliberately after
internal acceptance.

Billing variables and activation checks remain documented in
[RAZORPAY_GO_LIVE.md](RAZORPAY_GO_LIVE.md). Keep provider secrets in Secret Manager and
rotate the webhook secret with `RAZORPAY_WEBHOOK_SECRET_PREVIOUS` during overlap.

Optional market variables are `THEIRSTACK_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`,
`JOOBLE_API_KEY`, `REDIS_URL`, and `MARKET_CACHE_TTL_SECONDS`.

## Environments

- Production: live Razorpay only, explicit HTTPS origins, PostgreSQL, private worker.
- Preview/staging: test Razorpay only, separate database and secrets, worker queue suffix.
- Local/test: SQLite is allowed; analysis mode may be `inline` or `background`.

## Deploy Order

1. Back up and verify the production database.
2. Build the immutable image.
3. Run `python -m app.migrate` once; the API entrypoint does this under an advisory lock.
4. Deploy the API and smoke `/api/health`.
5. Deploy the private worker and smoke its authenticated `/api/health`.
6. Update `ANALYSIS_WORKER_URL`, then enable `ANALYSIS_TASKS_MODE=cloud_tasks`.
7. Deploy Vercel and run registration, workspace, failed-analysis, and billing smoke tests.
8. Roll out Career Workspace through server flags before making it universal.
9. Create the private maintenance scheduler, then verify one test lifecycle message and one retention dry run.
