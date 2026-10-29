# Stage5 owned-local HTTP/native-auth workload

This implements the existing Stage5 measurement work. It does not add or replace
an acceptance checkpoint. The source remains a diagnostic candidate until the
actual owned Linux experiment succeeds and its results are reviewed. Production
configuration, native source/image pins, owner profiles and qualified de78 are
unchanged. No flag is activated by this tool.

## What executes

`backend/scripts/run_stage5_capacity.py` creates a new invocation-owned PostgreSQL
17 cluster/database/schema, applies the real Alembic head, starts the existing
owned TLS Redis fixture, and starts the exact existing Firestore emulator image
in a new internal Docker network. Ports are dynamically chosen and bound only to
127.0.0.1; 55433/55434 are explicitly excluded. No existing listener is adopted,
unblocked or stopped. Docker cleanup requires the retained container/network ID
and exact nonce label. Process cleanup reuses the retained-unreaped-leader guard
from the existing cold-browser harness. Unknown ownership refuses cleanup.

The fixture reuses the existing production account factory and operator baseline.
Eight synthetic actors are genuinely enrolled by that factory and obtain its
native session context. Actual signed bearer checks and `get_current_user` run on
every request, without an auth or database dependency override. SQL and native
transaction exclusion is observed per request, so another concurrent
request cannot cause a false exclusion failure. A fresh request ContextVar follows
AnyIO worker handoffs; a bare thread-local cannot safely track SQL cleanup on a
different worker. The outer observer grants no authority or cached identity, and
any leaked transaction guard state fails the report. Firestore data operations use the
real installed GAPIC over the fresh data emulator. Admin database UID replies and
the real Storage SDK's intercepted HTTP replies remain explicitly **synthetic**.
They do not establish provider identity, IAM, durability, native quota metering
or GCS latency. The existing fixture authority clock is fixed; TTL/expiry is not
qualified. Fixture passwords/JWTs are synthetic, never owner/provider credentials;
tokens stay in process memory and do not enter the report.

The migrated database contains 10,000 users/resumes, 100,490 opportunities/matches,
30,000 evidence rows and either 2,000 or 2,500 synthetic buyers. Each buyer has
three retained synthetic credit lots (90 available credits) and one synthetic
reservation. These are data-volume inputs, not payments, receipts, funding
qualification or permission to sell. There are 9,999 ordinary ten-opportunity
accounts and one 500-opportunity account. The eight sampled actors include free,
buyer and heavy accounts. Heavy traffic is intentionally oversampled, not a claim
about actual customer behavior.

Only five real owned GETs are used: common bootstrap, resume list, opportunities
page, skill ROI and wallet. The complete shipping FastAPI app/middleware is used;
only this finite path set is offered by the workload. Missing-bearer and foreign
resume controls must first return 401/404 through the real boundary. Measured SQL
connections inherit server-side read-only transactions. Counts, response contracts
and balances are checked; a 200 with wrong ownership or content is a failed result.
No order, upload, search, application, email, employer, model or native cloud call
is generated. ADC, external requests/httpx, native TLS channels and non-owned
Python socket connections deny before send. The emulator's Docker network has no
external route. Setup downloads in GitHub Actions are separate public dependency
provisioning, not application/provider workload calls.

## Bounds and metrics

The committed `backend/capacity/stage5-owned-http-v1.json` offers 50 requests per
phase at ten planned arrivals/second. There are six phases: concurrency 1/10/25
under the original Get/unpaced diagnostic path, then 1/10/25 under reviewed List
validation plus the **unchanged** real TLS Redis 40-unit governor. The List phases
share one project counter/window, without reset, hidden retry, refund, budget
increase or per-phase namespace. Later phases can therefore be correctly refused;
they are not comparable successful-throughput samples when the budget is spent.
Unpaced Get throughput cannot justify activating the governor or bypassing it.

The default run has at most 300 workload GETs plus three controls. Validated plans
allow at most 2,997 total HTTP requests including controls and 600 seconds of
measurement; concurrency is fixed at 1/10/25, and buyers at 20% or 25%. A global
deadline cancels in-flight requests and records unsent requests explicitly. No
new request is sent after it. Unknown outcomes never become retries.

Arrivals are scheduled before semaphore admission. Arrival-to-completion latency
includes the client queue; HTTP service latency and queue delay are reported
separately. Refusals (including 429/503), deadline/transport errors, failed owned
contracts, body bytes and successful responses are separate counts. Fast 429s do
not inflate successful throughput. Reports contain actual SQL statement counts,
pool acquisition composite timings, real data-plane RPC counts, synthetic Admin/
Storage SDK call counts and Redis server command deltas. The Redis INFO observation
itself adds a command; script internals/connection setup can add more. This is not
Upstash billing metering. Pool acquisition includes connect, pre-ping and waiting;
pure pool wait is explicitly unknown. Time to local server listen and the first
local bootstrap read do not measure Cloud Run cold starts.

## Execute locally or on owned Linux

Run from a credential-free checkout with the locked Python 3.12 development
environment. Do not use an owner session, cloud project or shared database.
An already-cached emulator image and native PG17/TLS Redis binaries are required.
The tool never installs gcloud components or downloads dependencies itself.

```sh
cd backend
.venv/bin/python scripts/run_stage5_capacity.py
.venv/bin/pytest --noconftest -q tests/test_stage5_capacity.py tests/test_stage5_native_fixture.py
HIREWIZ_STAGE5_PG_BIN=/usr/lib/postgresql/17/bin HIREWIZ_STAGE5_PG_REQUIRED=true \
  .venv/bin/pytest --noconftest -q tests/test_stage5_seed_postgres.py
.venv/bin/python scripts/run_stage5_capacity.py --execute \
  --pg-bin /usr/lib/postgresql/17/bin --redis-server /path/to/owned/redis-server \
  --output /new/private/stage5-run
```

`--output` must not exist. It is created with mode 0700; reports use 0600. No
database URL, Redis URI, destination, project, provider credential or arbitrary
route argument exists. Preserve `report.json`, `cleanup.json`, `failure.json`
(when present), and source/config hashes for review. Raw logs, scratch databases,
certificates and authentication bodies must not be published. An interrupted or
uncertain Docker create requires review of that invocation's `ownership.json` and
exact nonce label/ID before manual cleanup; do not repeat the create or delete by
an unverified name. Unknown cleanup prevents a successful completion report.
Owned PG scratch is removed only after its original process has stopped, the
creation nonce/path/native PG17 control identity match, and its state is shut
down. A failure cannot skip later Redis/PG finalizers. Egress remains blocked if
the HTTP worker has not become terminal; such a run cannot report success.

The separate manual `.github/workflows/stage5-owned-capacity.yml` prepares the
existing pinned Redis source/TLS build and exact emulator image, builds official
SHA-pinned PostgreSQL17.11 into RUNNER_TEMP only, and runs both 20%/25% data scenarios on
separate ephemeral runners. It has contents:read only, no WIF/id-token grant,
production environment, cloud credentials, secrets or provider action. It uploads
only sanitized numeric/source reports and cleanup/failure records, never logs,
keys, PostgreSQL files or the home directory. A missing native dependency or failed
emulator/SDK prerequisite is a failure, not a skip or mocked replacement. This
manual workflow has not been dispatched as part of source preparation.
The [official Ubuntu2404 runner inventory](https://github.com/actions/runner-images/blob/main/images/ubuntu/Ubuntu2404-Readme.md)
provides PG16, so no host17 path is assumed. The source build uses the
[official PG17.11 release/checksum](https://www.postgresql.org/ftp/source/v17.11/),
does not install packages/start a system service, and refuses build/version drift.

## Actual local evidence and limitation

The local macOS attempt migrated a fresh owned PG17 database and started the exact
TLS Redis/emulator dependencies, but did not reach the HTTP workload. After emulator
readiness, an in-container TCP check succeeded and `/proc/net/tcp6` showed port8080
listening on the all-interface address. The exact 127.0.0.1 host publication returned
ConnectionRefusedError; an explicit plaintext `grpc.insecure_channel` timed out.
The internal network and loopback host mapping were retained. No fallback to a
shared emulator, external network, TLS relaxation or auth override was attempted.
All created containers/networks/processes were verified removed/stopped. This is
an actionable host/emulator dependency limitation, not a measured latency result.

Separate actual fresh-owned PG17 checks passed for both buyer scenarios: real
migrations and production-factory enrollment over intercepted native transports,
10,000/100,490 cardinality, 500/10 owner counts, and 90/0 buyer/free balances. Those
checks are SQL/factory evidence only, not the blocked gRPC/HTTP experiment.
A later verification attempt hit filesystem ENOSPC and is retained as incomplete,
not a passing rerun. Its failed disposable cluster was removed separately only
after matching the retained native system identifier/nonce and observing its
original PID absent; the crash-recovery state remains in the failure evidence.
The current source/observer/finalizer checks are separate from actual Linux HTTP
execution, which remains pending.

## Original acceptance remains open

The existing target remains about one million authenticated requests/month plus
separately measured peaks. The shared Firestore quota bucket is
`sharedDatabaseOperationsPerMinutePerProject`, metric
`firestore.googleapis.com/database_operation_requests`, unit `1/min/{project}`.
Actual Service Usage readback reports effective/default 60 with no override; it
does not declare the bucket List-only or prove producer adjustability. Previous
429 and method-labelled metering are distinct evidence of relevant Get/List use.
Cloud Quotas access is not an approved increase, and no API is enabled here.

At 40 units/minute and two fresh Lists per ordinary validation, the no-other-use
30-day ceiling is 864,000 validations, below the unchanged million-request target.
Lifecycle/signing/other traffic further reduces it. The initial Get mode has four
synthetic Admin calls per ordinary validation and is only a diagnostic baseline.
Common bootstrap reduces actual journey requests without sharing authorization
across requests. The currently qualified payment poll is eight adaptive automatic
reads (0,5,15,35,65,95,125,155 seconds absent request time), plus one explicit manual
status read; upload polling is separately eight reads with a 240-second deadline.
The historical 24-at-2.5-second estimate is superseded. First-minute journey bursts,
multiple active streams, other callers and Redis command allowance still need
measurement. Local reads cannot qualify production p95, native SQL CU, Redis
allowance, autoscaling, browser p75 metrics, invoice costs, cost envelopes or
10,000-user peaks. List/admission activation and Stage5 acceptance remain gated.
