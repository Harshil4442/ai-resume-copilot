# Environment and Deployment Contract

The [employer requirements](requirements/JOB_SEARCH_AND_APPLICATION.md) and
[delivery evidence](delivery/AUTO_APPLY_PROGRESS.md) distinguish implemented pilot
capabilities from wider coverage and permission-dependent application expansion.
GCP infrastructure is managed in `infra/gcp`; the immutable backend release runs
through `cloudbuild.yaml` and `infra/gcp/release.sh` after verification and backup.

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

Employer service variables:

| Variable | Production pilot value / purpose |
| --- | --- |
| `AUTO_DB_MIGRATE` | `false` on API; the isolated migration job upgrades first |
| `EMPLOYER_DISCOVERY_ENABLED` | `true` after verified source enrollment |
| `EMPLOYER_AUTO_SUBMIT_ENABLED` | `false`; enable only for separately validated tenant grants and receipts |
| `EMPLOYER_SEARCH_CREDITS_PER_JOB` | `1`, charged per new qualifying delivered job |
| `EMPLOYER_APPLY_CREDITS_PER_JOB` | `5`, charged per verified complete automatic application |
| `EMPLOYER_MAX_SEARCH_JOBS` | `100`, server-enforced count bound |
| `EMPLOYER_ARTIFACT_BUCKET` | Private, uniform-access, public-access-prevented GCS bucket |
| `EMPLOYER_SEARCH_TASKS_QUEUE` | `hirewiz-employer-search` |
| `EMPLOYER_INGESTION_TASKS_QUEUE` | `hirewiz-employer-ingestion` |
| `EMPLOYER_APPLICATION_TASKS_QUEUE` | `hirewiz-employer-application` |
| `EMPLOYER_*_WORKER_URL` | Corresponding private worker origin |
| `WORKER_ALLOWED_TOPICS` | Analysis worker: `analysis.run`; employer worker: employer topics only |
| `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` | API `4/2`, private workers `2/0`, migration `1/0` |
| `DB_POOL_TIMEOUT_SECONDS` / `DB_POOL_RECYCLE_SECONDS` | `10/300` |
| `RATE_LIMIT_STORAGE_URL` | Shared Redis rate limit store; defaults to existing `REDIS_URL` |

The employer worker uses its own `hirewiz-employer-worker` service account and has no
model, payment, or market-provider credentials. `DATABASE_URL`, task token and the
worker bootstrap key are Secret Manager references. API/analysis settings retain their
existing scoped configuration; never copy the API's complete environment to an employer worker.

The transactional dispatch table stores identifiers, not resumes or answers. A scheduled
maintenance sweep recovers committed work after queue failures. Artifact deletion is
generation-fenced and uses the same durable recovery path. Application `unknown` holds
require independently evidenced reconciliation; queue retries never initiate another send.

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
5 minutes with OIDC authentication. Cloud Scheduler supplies `X-CloudScheduler: true`;
`infra/gcp/configure_maintenance.py` configures the required defence-in-depth task token
from Secret Manager without writing it into local files or command arguments.

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
3. Run the one-task `hirewiz-schema-migration` job under an advisory lock and await success.
4. Release bounded private workers, their workload scope and queue URLs before API promotion.
5. Stage the API with zero traffic, verify HTTP health, commit and actual `K_REVISION`.
6. Promote that verified revision by its name; never use `--to-latest` after a health check.
7. Stage a Vercel production build with `--skip-domain`, verify it, then promote that exact deployment.
8. Seed reviewed employer sources with the audited CLI, then check complete origin-feed ingestion.
9. Configure private maintenance Scheduler OIDC, its required headers, and observe a real recovery run.
10. Monitor revision health, errors, queues and credit/state transitions; record deployment IDs and limits.

Seven-day GCS soft deletion is backup retention, not an immediate irrecoverability claim.
Before schema promotion, store and validate a protected PostgreSQL custom archive. Do not
restore production backups into generic development fixtures. Recovery drills must apply
independently retained deletion/revocation records before user access or execution resumes;
these wider recovery gates remain tracked separately until tested.
