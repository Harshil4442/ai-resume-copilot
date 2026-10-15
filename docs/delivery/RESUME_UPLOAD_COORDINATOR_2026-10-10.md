# Direct-resume scan and cleanup coordinator

Status: private implementation; disabled by default. No service, Scheduler job,
identity, grant or production invocation was created for this packet. These are
source/runtime checks, not proof of Cloud Run IAM, GCS retention or global fencing.

## Runtime and durable behavior

`app.resume_upload_worker:app` is a separate FastAPI app. It imports neither the
legacy task dispatcher nor candidate authentication, and never runs migrations.
`POST /internal/resume-uploads/scan` processes at most one due upload through the
reviewed `tasks.process_due(limit=1)`. `POST /internal/resume-uploads/cleanup`
first invokes `privacy.sweep(limit=100)` and then `privacy.cleanup_due(limit=1)`.
The existing domain, scanner receipt, storage generation pins and schema14 bytes
are unchanged. Parsing/scanning remain in the private document inspector; this
coordinator does not parse files locally, enrich with AI or charge units.

Each authenticated tick owns one child process/session. A 240-second monotonic
wall deadline includes child creation, domain work and bounded result reading.
Stdout is limited to 1,024 bytes and accepts only exact integer aggregate counts;
stderr is discarded. Timeout, nonzero exit, malformed output and cleanup errors
return fixed `resume_coordinator_unavailable`/503. The runtime kills only its owned
process group and waits up to three seconds for cleanup; it does not retry a
possibly completed write. This is not proof that remote work has stopped. Domain
300-second leases, generation preconditions and durable states govern later recovery.
A scan interruption can remain `inspecting` until lease expiry; failed scans use
existing 30-second delays and a three-attempt cap. Cleanup failures retain existing
60-second backoff and closure-marker work. A later successful tick reconciles these
records. One in-process lock refuses overlap; native instance/concurrency bounds
are still required. Cleanup remains available while scanning/admission are paused,
provided the independent coordinator-wide flag remains enabled.

No request body, query, upload ID, filename or signed URL is accepted. App outputs
contain fixed status/role/flags or aggregate counts, with `Cache-Control: no-store`.
The proposed command disables Uvicorn access logs. Cloud/provider logging and
platform isolation remain native activation checks; this packet does not certify them.

## Configuration and authentication

All flags default off and accept only the literal `true`:

| Setting | Required meaning |
| --- | --- |
| `SERVICE_ROLE` | Exact `resume-upload-coordinator`; other roles remain disabled |
| `RESUME_UPLOAD_COORDINATOR_ENABLED` | Explicitly enable the separate runtime |
| `RESUME_UPLOAD_COORDINATOR_SCAN_ENABLED` | Additional scan-only switch |
| `RESUME_DIRECT_UPLOAD_ENABLED` | Existing admission/domain scan switch; cleanup does not depend on it |
| `GOOGLE_CLOUD_PROJECT` | Valid project ID used to bind caller account |
| `RESUME_UPLOAD_COORDINATOR_AUDIENCE` | Canonical HTTPS coordinator `run.app` service URL, without path/query/port |
| `RESUME_UPLOAD_COORDINATOR_CALLER_EMAIL` | Exact dedicated Scheduler service-account email in that project |
| `RESUME_UPLOAD_COORDINATOR_CALLER_SUBJECT` | Exact native 21-digit service-account unique ID; obtain and bind from IAM metadata |

Google's existing `verify_oauth2_token` implementation verifies token signature,
expiry/issued time and audience. Additional fixed issuer, exact subject/email and
strict `email_verified=true` checks bind the intended workload. Scheduler/task
headers are not credentials. Duplicate, missing and malformed Authorization headers
refuse before a child starts. The certificate-only request uses the fixed Google
certificate endpoint, certifi TLS/hostname verification, no environment proxy,
no TLS key logging or redirect, and a five-second socket/request timeout. This is
not a separately proven hard total deadline for the Google SDK certificate path.
Native Cloud Run invoker protection and authenticated caller grants remain mandatory.

The child uses existing `DATABASE_URL` and `DB_POOL_*`, and the reviewed storage
configuration (`RESUME_UPLOAD_QUARANTINE_BUCKET`, `RESUME_UPLOAD_CLEAN_BUCKET`,
`RESUME_UPLOAD_SIGNER`, `RESUME_UPLOAD_READ_SIGNER`, project). The signer identities
are fixed target configuration; scan/cleanup never request signing credentials or
call `signBlob`. Scans also require the already reviewed immutable document-worker
URL/image/policy bindings. The child inherits the coordinator service environment;
this is a credential-bearing orchestrator, not a sandbox. No provider/payment/JWT/
legacy task credentials should be installed on it. Resources are constructed lazily
only after enabled-mode checks; disabled app health and requests create no SQL/GCS
resources.

## Proposed activation configuration, still uncreated

Use a new `hirewiz-resume-coordinator` service and dedicated least-privilege runtime
identity in `ai-resume-parser-482412/us-central1`. Use a separate
`hirewiz-resume-scheduler` caller identity; neither name is provisioned by this packet.
The direct ASGI command must bypass the existing image's migration entrypoint:

```text
python -m uvicorn app.resume_upload_worker:app --host 0.0.0.0 --port 8080 --no-access-log
```

Set `AUTO_DB_MIGRATE=false`, optional AI and employer auto-submit false. Keep service
private, IAM checking enabled, no public/conditional invoker inheritance, and grant
`roles/run.invoker` only to the intended Scheduler identity. Propose concurrency1,
maxInstances1, minInstances0, one process and request timeout270s. These values allow
the local 240s child budget plus auth/body/cleanup overhead; native timing must still
be observed. Internal ingress requires the supported Scheduler/VPC/project routing
and coordinator-to-document-inspector network path. No ingress bypass is proposed.

Two separate Scheduler POST jobs use empty bodies and OIDC audience equal to the
service URL, not endpoint paths: scan every minute, cleanup every five minutes.
Use attemptDeadline270s; retries are scheduling retries, not an immediate repeated
POST by this runtime. A conservative retry window at least300s avoids repeatedly
contending with an unknown child's lease. One-record ticks intentionally limit
throughput; cadence/scaling changes need observed queue age, latency and cleanup
metrics. No throughput/availability SLA is established here.

Provision a dedicated ordinary SQL login only after the global authority fence and
schema14 migration gates pass. Review SELECT/UPDATE on upload rows; SELECT/INSERT/
UPDATE on cleanup rows; Resume INSERT and sequence usage; and the User row-lock
requirement. PostgreSQL `SELECT FOR UPDATE` needs UPDATE privilege on at least one
column, so a SELECT-only User grant must not be presented as sufficient. Precise
column grants/ownership/native role tests remain unresolved, without granting
financial/provider/lifecycle privileges. No DDL, role creation, migrations, default
grants or financial ledger writes belong to this runtime.

Restrict storage identity to exact quarantine/clean buckets and required object
read/conditional-create/replace operations. Replacement of existing payloads may
require both create and delete permissions; verify this with real dedicated-role
operations. No bucket-admin/list/signer impersonation entitlement is implied.
UBLA, public-access prevention, versioning/soft-delete/lifecycle and retained closure
marker acceptance remain separate native checks. Grant the coordinator invoker
access to the private document inspector only after its Cloud Run sandbox proof.
The prior API-only inspector invoker proposal must be explicitly amended/reviewed
for this additional caller; it is not silently broadened here.

## Verification and remaining gates

- [x] Default-disabled requests/health avoid verifier and SQL/GCS resources.
- [x] Signature, time, audience and caller binding exercised through the real Google
  verifier with synthetic in-memory RSA/certificates; no Google endpoint contacted.
- [x] Actual owned child deadline, exit/error/oversize output and local overlap tested.
- [x] Eight actual PostgreSQL17.11 coordinator cases exercised release/replay,
  one-record batching, permanent refusal, durable retry/backoff, expiration,
  interrupted leases and cleanup while scan/admission are disabled.
- [x] Synthetic storage/scanner evidence remains distinct from native GCS/ClamAV proof.
- [x] Exact local fixture namespace/OID/owner inventory unchanged; no unrelated cleanup.
- [ ] Independently review this source packet, then integrate into a new immutable
  publication and complete required CI/static coverage (add these two runtime paths
  explicitly to targeted Ruff/Mypy CI lists; pytest discovers the new tests).
- [ ] Native schema14, dedicated SQL/GCS/Scheduler/IAM identities/grants, issuer and
  global fence, document sandbox, bucket retention and exact-source image gates.
- [ ] Root-owned private staging and real token/role/timeout/storage/recovery proof;
  enable only after these gates. No production activation is authorized by this file.

Primary contracts: [Cloud Run service-to-service identity](https://docs.cloud.google.com/run/docs/authenticating/service-to-service),
[Scheduler OIDC](https://docs.cloud.google.com/scheduler/docs/http-target-auth),
[PostgreSQL row locking](https://www.postgresql.org/docs/17/sql-select.html),
[Cloud Storage permissions](https://docs.cloud.google.com/storage/docs/access-control/iam-permissions).
