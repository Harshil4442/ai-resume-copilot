# GCP Async Worker Runbook

This is a one-time bootstrap for the project that already hosts the HireWiz API. Commands
use placeholders deliberately; review the active `gcloud` project before running them.

```bash
export PROJECT_ID="YOUR_GCP_PROJECT_ID"
export REGION="us-central1"
export QUEUE="hirewiz-analysis"
export TASKS_SA="hirewiz-tasks@${PROJECT_ID}.iam.gserviceaccount.com"
export WORKER_SERVICE="hirewiz-analysis-worker"
gcloud config set project "$PROJECT_ID"
```

## 1. Enable APIs and Create Queue

```bash
gcloud services enable run.googleapis.com cloudtasks.googleapis.com cloudscheduler.googleapis.com artifactregistry.googleapis.com
gcloud tasks queues create "$QUEUE" \
  --location "$REGION" \
  --max-dispatches-per-second 5 \
  --max-concurrent-dispatches 10 \
  --max-attempts 5 \
  --min-backoff 10s \
  --max-backoff 300s
```

The queue may already exist; describe it instead of recreating it.

## 2. Create the Task Identity

```bash
gcloud iam service-accounts create hirewiz-tasks \
  --display-name "HireWiz Cloud Tasks invoker"
```

The Cloud Run API runtime identity needs `roles/cloudtasks.enqueuer` and
`roles/iam.serviceAccountUser` for `TASKS_SA`. Grant the smallest project/service-account
scope available in your GCP setup.

## 3. Create the Private Worker

Deploy the same immutable image as the API with `SERVICE_ROLE=worker`. In Cloud Run:

- Require authentication; do not allow unauthenticated access.
- Copy only the backend database, LLM, task-token, Sentry, release, and logging variables.
- Keep minimum instances at zero initially and concurrency low (start at 4).
- Use a dedicated runtime service account with database/secret access only.
- Set request timeout above the longest supported provider call (start at 300 seconds).

Grant invocation only to the task identity:

```bash
gcloud run services add-iam-policy-binding "$WORKER_SERVICE" \
  --region "$REGION" \
  --member "serviceAccount:${TASKS_SA}" \
  --role roles/run.invoker
```

Obtain the worker URL:

```bash
gcloud run services describe "$WORKER_SERVICE" \
  --region "$REGION" \
  --format='value(status.url)'
```

## 4. Enable API Dispatch

Add the async variables from [ENVIRONMENT_AND_DEPLOYMENT.md](ENVIRONMENT_AND_DEPLOYMENT.md)
to the API. Generate `ANALYSIS_TASK_TOKEN` with at least 32 random bytes and store it in
Secret Manager on both services. Deploy with `ANALYSIS_TASKS_MODE=cloud_tasks` only after
the private worker health check succeeds.

## 5. Verify

1. Create a test opportunity and start a match.
2. Confirm the API returns `202` in under one second.
3. Confirm one Cloud Task reaches the private worker.
4. Confirm the run becomes `succeeded` and usage records `reserve`, then `commit`.
5. Force a provider failure in staging and confirm `reserve`, then `release`.
6. Replay the same task and confirm no second result or charge.

## 6. Schedule Lifecycle and Retention Maintenance

Use an authenticated Scheduler request to the private worker. Reuse a service account only
when its permissions remain minimal; otherwise create a dedicated scheduler invoker.

```bash
export WORKER_URL="$(gcloud run services describe "$WORKER_SERVICE" --region "$REGION" --format='value(status.url)')"
gcloud scheduler jobs create http hirewiz-maintenance \
  --location "$REGION" \
  --schedule "*/15 * * * *" \
  --uri "${WORKER_URL}/internal/tasks/maintenance" \
  --http-method POST \
  --oidc-service-account-email "$TASKS_SA" \
  --oidc-token-audience "$WORKER_URL" \
  --headers "X-CloudScheduler=true"
```

If `ANALYSIS_TASK_TOKEN` is enforced, add `X-HireWiz-Task-Token` from a protected
deployment mechanism rather than writing the token into repository files or shell history.
Verify the first response manually and alert on non-2xx executions.

## Incident Actions

- Backlog growing: pause new expensive runs with a feature flag, inspect oldest tasks,
  provider status, worker errors, and database saturation before increasing concurrency.
- Provider degraded: keep retries bounded; disable the affected operation, not billing or
  authentication.
- Worker inaccessible: verify Cloud Run IAM, OIDC audience, task service account, and URL.
- Duplicate concern: query the run and `usage_events` before manually changing any balance.
