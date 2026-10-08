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

Cloud Run image releases run through the repository Cloud Build configuration and
`release.sh`. Build a commit image and resolve its digest, execute one migration job,
release private workers, stage and verify the API, and promote the exact checked revision.
Stage the Vercel production deployment separately and promote it after verification.
The API keeps one service-level minimum instance to reduce idle cold starts, with its
existing request/concurrency cap and a bounded SQL pool. Idle capacity has a recurring
infrastructure cost; private batch workers can still scale to zero. This is not a latency
guarantee, and production Web Vitals are evaluated separately from local mocked checks.
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

Authentication contracts:
[Scheduler OIDC](https://docs.cloud.google.com/scheduler/docs/http-target-auth),
[private Cloud Run targets](https://docs.cloud.google.com/run/docs/triggering/using-scheduler).

Official resource contracts:
[Cloud Tasks](https://registry.terraform.io/providers/hashicorp/google/7.27.0/docs/resources/cloud_tasks_queue),
[queue IAM](https://registry.terraform.io/providers/hashicorp/google/7.27.0/docs/resources/cloud_tasks_queue_iam),
[Cloud Run](https://registry.terraform.io/providers/hashicorp/google/7.27.0/docs/resources/cloud_run_v2_service).
