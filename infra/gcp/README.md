# GCP employer service infrastructure

Keep the existing GCP project and `us-central1`. This module adds independent bounded
queues and a private application artifact bucket. It does not recreate the current API,
PostgreSQL, Redis, Vercel project or billing account. No secrets belong in Terraform input
files or state; service environments use existing Secret Manager references.

Use a dedicated private GCS Terraform state bucket with versioning and access controls.
The local plan must show additive changes before applying. Do not import a production
resource into a different environment's state or use `force_destroy` for customer storage.

```sh
terraform init -backend-config="bucket=YOUR_PRIVATE_STATE_BUCKET" -backend-config="prefix=hirewiz/production/employer"
terraform plan -out=release.tfplan
terraform apply release.tfplan
```

Supply project, artifact bucket and existing API/worker service-account emails through
protected environment-specific variable files. The checked-in provider lockfile records
the reviewed provider version; commit it after `terraform init -backend=false` validation.
Private worker URL and queue environment values are documented in the deployment runbook.

Cloud Run images are built through the repository Cloud Build configuration. The legacy
`release.sh` starts with an offline source preflight before any cloud call, including
Artifact Registry or backup metadata reads. It requires a Git checkout whose full HEAD
matches the requested release, with clean source and tracked file bytes matching that
commit. It parses local migration declarations without executing them, and accepts only
the single reviewed chain ending at `20261008_0009`. Monetary schema0010, unknown heads,
branches, incomplete ancestry and unverifiable source are refused. No environment flag
or local JSON assertion can waive the refusal. A separate approved monetary cutover entry
point with independently verified legacy-writer fencing is required and is not implemented.

Run this legacy script from the exact clean checked commit. Archive-only workspaces lack
Git identity and are refused; `.gcloudignore` intentionally excludes Git metadata, so an
archive-based Cloud Build deploy step cannot use this entry point unchanged. The parent
integration removes the third `migrate-stage-verify-promote` step from `cloudbuild.yaml`;
Cloud Build now builds and pushes the immutable image only. Migration, staging and
promotion require an explicitly controlled release after exact CI and backup checks.
No source archive can invoke the unsafe monetary cutover automatically. Merely
setting a `COMMIT_SHA` substitution does not verify source identity. Local source checks
also do not bind an already uploaded image to that commit: image provenance, exact CI,
digest verification and serving-release health remain separate required release checks.
This preflight makes no claim that old writer processes or credentials have been retired.
Permitting local schema0009 source does not verify the current database schema or make
this script a safe post0010 rollback entry point. After monetary cutover, rollback must
use the separately approved paused-image/fence procedure with accounting history retained.

For a permitted schema0009 release, build a commit image and resolve its digest, prepare the exact serving
image without startup schema migration, execute one migration job,
release private workers, stage and verify the API, and promote the exact checked revision.
Stage the Vercel production deployment separately and promote it after verification.
The API keeps one service-level minimum instance to reduce idle cold starts, with its
existing request/concurrency cap and a bounded SQL pool. Idle capacity has a recurring
infrastructure cost; private batch workers can still scale to zero. This is not a latency
guarantee, and production Web Vitals are evaluated separately from local mocked checks.
The API also needs an explicit trusted-proxy setting for native account transport.
Cloud Run terminates TLS before forwarding HTTP to the container; Uvicorn only
accepts forwarded protocol headers from configured peers. The peer observed on
21 October was `169.254.169.126`. The reviewed API-only repair sets
`FORWARDED_ALLOW_IPS=127.0.0.1,169.254.169.126`; it keeps the HTTPS, signed-request,
body, path and replay checks intact. Preserve this setting during environment
replacement and fresh provisioning. Stock rolling releases copy and pin the
current API environment. Before promotion, verify the staged HTTPS redirect,
unsigned-request rejection and a fresh signed account-availability request.
Neither provider promises this exact peer permanently: after a platform/network
change, reobserve it and fail closed rather than broadening to a wildcard or
subnet. Workers do not need this API trust setting. See the official
[Uvicorn proxy settings](https://uvicorn.dev/settings/#http) and
[Cloud Run TLS contract](https://docs.cloud.google.com/run/docs/container-contract#transport-layer-encryption-tls).
Serialize releases. The migration preparation refuses an unmatched staged revision,
so operators must resolve a previous candidate before starting another release. Its
direct ASGI command supports older images whose entrypoints ignore `AUTO_DB_MIGRATE`;
the new candidate clears that override. Schema changes must still be backward compatible
with the prepared serving application.
Account-deletion object purge uses exact
object generations. Seven-day GCS soft deletion is part of the declared backup retention.
Protected release database dumps under `releases/` expire after seven days and remain
recoverable in GCS soft deletion for seven additional days. This prefix-specific rule
does not expire active application artifacts and does not provide continuous database recovery.

After releasing the private workers, grant `hirewiz-scheduler` Cloud Run Invoker on
`hirewiz-analysis-worker`, then run `python3 infra/gcp/configure_maintenance.py --apply`.
The default invocation shows a redacted plan. This configures five-minute bounded recovery
with OIDC and reads the defence-in-depth task token directly from Secret Manager in memory.
No task token enters command arguments, local files, Terraform state or console output.
Restrict Scheduler read/edit permissions because its server-side HTTP headers include the
token. The recovery endpoint publishes committed outbox work and requeues unfinished
artifact purges even while job discovery is disabled. Source refresh still respects its flag.
The analysis worker must have explicit Cloud Tasks mode, project, location, queue,
task identity, worker destinations and a matching Secret Manager task-token reference.
It also needs enqueue permission on each target queue and `iam.serviceAccountUser` on
the task identity. Do not rely on its legacy environment to provide publisher settings:
inline maintenance is incompatible with an analysis-only execution scope for employer work.

Authentication contracts:
[Scheduler OIDC](https://docs.cloud.google.com/scheduler/docs/http-target-auth),
[private Cloud Run targets](https://docs.cloud.google.com/run/docs/triggering/using-scheduler).

Official resource contracts:
[Cloud Tasks](https://registry.terraform.io/providers/hashicorp/google/7.27.0/docs/resources/cloud_tasks_queue),
[queue IAM](https://registry.terraform.io/providers/hashicorp/google/7.27.0/docs/resources/cloud_tasks_queue_iam),
[Cloud Run](https://registry.terraform.io/providers/hashicorp/google/7.27.0/docs/resources/cloud_run_v2_service).

Operational alerts use the existing project's Cloud Logging and Monitoring APIs. On
2026-10-21, the two definition metrics and four policies below were created and read back
through the normal retained GCP manager, with the user's selected signed-in account as
the sole email destination. No test notification or synthetic heartbeat was sent.

`document_refresh_alerts.tf` retains opt-in defaults. Before importing these resources
into the reviewed private Terraform state, set `document_refresh_alerts_enabled=true`,
`api_alerts_enabled=true`, and `document_refresh_notification_channels` to
`["projects/ai-resume-parser-482412/notificationChannels/586119582964625421"]`.
Reconcile the resulting plan against these existing resources; applying the full
uninitialized stack is not a monitoring activation procedure. The email channel remains
an external reviewed reference, rather than a second Terraform-managed copy.

| Terraform resource | Existing native resource name |
| --- | --- |
| `google_logging_metric.document_refresh_expiry[0]` | `projects/ai-resume-parser-482412/metrics/hirewiz_document_definition_attention` |
| `google_logging_metric.document_refresh_heartbeat[0]` | `projects/ai-resume-parser-482412/metrics/hirewiz_document_definition_heartbeat` |
| `google_monitoring_alert_policy.document_refresh_expiry[0]` | `projects/ai-resume-parser-482412/alertPolicies/16873482599735560977` |
| `google_monitoring_alert_policy.document_refresh_silent[0]` | `projects/ai-resume-parser-482412/alertPolicies/3488339065445890745` |
| `google_monitoring_alert_policy.api_errors[0]` | `projects/ai-resume-parser-482412/alertPolicies/6821016490474288404` |
| `google_monitoring_alert_policy.api_latency[0]` | `projects/ai-resume-parser-482412/alertPolicies/6821016490474289498` |

The definition policies retain the unchanged three-day validity limit, attention at
24 hours remaining, and a two-hour missing-heartbeat window. After activation, observe
a genuine `hirewiz_document_definition_maintenance_v1` event from the maintenance
workflow and its heartbeat metric. A metric-absence policy needs an initial measurement
before it can detect missing data; never forge an event to arm it. New log metric
descriptors can take time to appear in Monitoring after Logging acknowledges creation.

The API policies use built-in Cloud Run metrics for `ai-resume-parser` in `us-central1`:
at least three 5xx responses in five minutes, and worst-series five-minute p95 latency
above 5,000 milliseconds for ten minutes. The latency threshold is an initial operational
warning to tune against representative traffic, not a published SLO. These policies
monitor runtime health and definition maintenance; deployment-job failures remain in
the deployment workflow's own notification scope.

The email channel readback is enabled with `VERIFICATION_STATUS_UNSPECIFIED`, which is
different from `UNVERIFIED`; Google did not request channel verification. Inbox delivery
was not tested. Preserve the channel and policy handles during future deployments.
See [channel verification semantics](https://docs.cloud.google.com/monitoring/api/ref_v3/rest/v3/projects.notificationChannels)
and [metric-absence behavior](https://docs.cloud.google.com/monitoring/alerts/metric-absence).
