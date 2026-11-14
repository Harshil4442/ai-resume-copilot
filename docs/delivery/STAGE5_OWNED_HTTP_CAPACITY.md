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

The `stage5-owned-http-v2` report measures five real owned GETs: Dashboard,
resume metadata page, opportunity metadata page, skill ROI and wallet. Dashboard
uses the consolidated account read. The metadata pages use the same first-page
size of 20 as the frontend. The complete shipping FastAPI app and middleware run
for every request. Before measurement, bootstrap must return 401 without a bearer
and a valid owned response with one. The foreign resume must return 404. Measured
SQL connections inherit server-side read-only transactions. Counts, response
contracts and balances are checked. A 200 with wrong ownership or content fails.
Synthetic addresses use reserved `example.com` and the real public profile email
validator, without DNS or email delivery. The metadata oracle checks the exact
seeded public IDs, totals and response fields. The 500-role account must receive a
continuation cursor. This workload measures first-page reads. Later pages and
search remain covered by their separate API and browser tests.
No order, upload, search, application, email, employer, model or native cloud call
is generated. ADC, external requests/httpx, native TLS channels and non-owned
Python socket connections deny before send. The emulator's Docker network has no
external route. Setup downloads in GitHub Actions are separate public dependency
provisioning, not application/provider workload calls.

The full emulator path requires a native Linux Docker host. Requested
`HostConfig.PortBindings` is not treated as an effective listener: the tool reads
`NetworkSettings.Ports`, verifies readiness inside the exact owned container,
then checks an effective published loopback port when present. If that publication
is absent/unreachable, it observes the same image/nonce/container/network again
and derives only its actual private IPv4 endpoint from the internal bridge IPAM
and current membership. A fixed stdlib child can then relay a newly owned
127.0.0.1 listener to that one endpoint at port8080. It cannot accept a destination
from HTTP clients, use DNS, forward outside the observed RFC1918 subnet, add an
external network or change Docker daemon policy. The application GAPIC still
connects only to its owned loopback endpoint and must genuinely become ready.
No transport switch occurs after a native request or failed GAPIC operation.
The relay has eight connection slots, a 128MiB byte cap and a 900-second lifetime;
the actual 600-second workload limit remains unchanged. This extra local hop is
included in local timing and is not provider latency. Desktop is refused rather
than relaxing isolation. Effective binding/readiness/selected-transport metadata
is retained in `emulator-transport.json`; it contains no bodies or credentials.
Docker documents host access to an internal bridge in its
[port-publishing guide](https://docs.docker.com/engine/network/port-publishing/)
and the different Desktop bridge boundary in its
[Desktop networking guide](https://docs.docker.com/desktop/features/networking/networking-how-tos/).
Those references do not substitute for this run's observed effective transport.

## Bounds and metrics

The committed `backend/capacity/stage5-owned-http-v1.json` offers 50 requests per
phase at ten planned arrivals/second. There are six phases: concurrency 1/10/25
under the original Get/unpaced diagnostic path, then 1/10/25 under reviewed List
validation plus the **unchanged** real TLS Redis 40-unit governor. The List phases
share one project counter/window, without reset, hidden retry, refund, budget
increase or per-phase namespace. Later phases can therefore be correctly refused;
they are not comparable successful-throughput samples when the budget is spent.
Unpaced Get throughput cannot justify activating the governor or bypassing it.

For a separate latency diagnostic, dispatch the workflow with `fresh_window`
enabled or add `--fresh-window` to the local command. After the usual three
controls, the runner uses only the governed List path. Each of the three phases
offers 16 reads together with a concurrency cap of 1, 10, or 25. The runner waits
at least 61 seconds before each batch, including the first. The existing shared
Redis window ages naturally, with no reset, separate key, or retry. These waits
count toward the existing measurement deadline. The total is at most 51 HTTP
requests including controls. All 16 reads and exactly 32 intercepted List calls
must succeed in each batch. Any capacity refusal fails this diagnostic. The report
records actual peak in-flight requests and cooldown durations. A batch of 16
cannot saturate the 25-connection cap or establish sustained throughput.

The default run has at most 300 workload GETs plus three controls. Validated plans
allow at most 2,997 total HTTP requests including controls and 600 seconds of
measurement; concurrency is fixed at 1/10/25, and buyers at 20% or 25%. A global
deadline cancels in-flight requests and records unsent requests explicitly. No
new request is sent after it. Unknown outcomes never become retries.
Entire later phases that cannot start are retained as `not_started_deadline`,
with their planned/unsent counts, no samples and null performance estimates.
They perform no extra Redis/native observations and cannot disappear from the
six-phase experiment denominator.

Arrivals are scheduled before semaphore admission. Arrival-to-completion latency
includes the client queue; HTTP service latency and queue delay are reported
separately. Refusals (including 429/503), deadline/transport errors, failed owned
contracts, body bytes and successful responses are separate counts. Fast 429s do
not inflate successful throughput.
The CLI preserves its report and exits nonzero for failed controls, any bad owned
200 body, missing phases/requests, unexpected statuses, transport/deadline errors,
or incomplete cleanup. A governed 429 is accepted only as an explicit refusal
after checking the actual capacity error body and Retry-After/cache headers.
Cache-Control requires exactly the case-insensitive `private` and `no-store`
directives, independent of order/ordinary whitespace; permissive additions,
parameters, duplicates or missing directives fail.
it remains outside successful-response throughput. Even a valid local workload
keeps `production_capacity_qualified` false.

Reports contain actual SQL statement counts,
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
down. The native system identifier is anchored immediately after the owned
initdb, before startup, and both SQL migration readback and shutdown control data
must match it. A failure cannot skip later Redis/PG finalizers. Egress remains blocked if
the HTTP worker has not become terminal; such a run cannot report success.
The native/factory/time/GAPIC fixture context also remains installed through
HTTP shutdown. A stuck worker retains that context for the failed CLI's lifetime;
it does not unwind those patches or restore unrestricted egress behind a live
worker.
The owned relay must stop before its Docker endpoint/network can be released.
Unknown process-stop outcomes retain that endpoint and fail cleanup rather than
allowing its address to be reused behind a live forwarder.

The path-filtered pull-request/manual `.github/workflows/stage5-owned-capacity.yml` prepares the
existing pinned Redis source/TLS build and exact emulator image, builds official
SHA-pinned PostgreSQL17.11 into RUNNER_TEMP only, and runs both 20%/25% data scenarios on
separate ephemeral runners. It has contents:read only, no WIF/id-token grant,
production environment, cloud credentials, secrets or provider action. It uploads
only sanitized numeric/source reports and cleanup/failure records, never logs,
keys, PostgreSQL files or the home directory. A missing native dependency or failed
emulator/SDK prerequisite is a failure, not a skip or mocked replacement. This
workflow runs only the reviewed owned-local experiments. Its PR trigger names
only explicitly enumerated Stage5 tooling/config/test and supporting runtime paths, so this Linux validation
can complete before merge without adding broader production authority.
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
The source/observer/finalizer checks are separate from actual Linux HTTP
execution and do not establish its response contracts or production capacity.

The first actual Linux PR run also stopped at GAPIC readiness in both buyer
scenarios after the pinned dependencies and finite/PG seed checks passed. No
HTTP workload was measured; its artifacts prove owned cleanup. That failure
motivated effective-listener diagnostics and the fixed internal-endpoint relay.
The second actual Linux run at source `5c6bb1d` reached the genuine GAPIC/emulator
and direct HTTPS workload through its owned fixed relay in both buyer scenarios.
Each produced 300 measured requests and verified cleanup, but 68 HTTP200 bodies
failed the original fixture oracle. The fixture's `.test` email was rejected by
the real profile schema, and it incorrectly expected private `user_id` fields in
the public opportunities response. Those original reports remain failed contract
measurements. Their green workflow exits exposed a missing CLI failure gate;
physical transport success is separate from workload acceptance. The corrected
fixture/oracle and mandatory failure gate need a fresh actual Linux result.
A subsequent run at `3eb7c053` produced 170 valid HTTP200 bodies and 130 governed
HTTP429 responses in each 300-request scenario, with valid owner controls and
verified cleanup. Its new gate correctly failed because the oracle compared
Cache-Control directive order literally: the actual shipping middleware emits
`no-store, private`. A transport-blocked regression reproduces that exact
middleware/helper response. Those failed reports remain retained; the bounded
semantic-header correction still requires a fresh actual Linux result.
A later Mac Docker image-inspect
probe timed out before any resource creation and was not repeated.

The [13 November Linux run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/43410352917)
measured the current Dashboard, résumé metadata and Workspace metadata routes
alongside Skill ROI and wallet. Each synthetic buyer cohort offered 300 reads,
with 170 valid HTTP 200 responses, 130 validated HTTP 429 refusals, zero
contract failures and zero provider calls. Ownership controls and cleanup passed.
In each cohort, the unpaced diagnostic accounted for 150 successful reads.
The first governed phase admitted 20 reads using 40 metadata calls. The two
later governed phases admitted zero because they shared that same minute's
allowance. The run therefore measures the limiter's behavior under a shared
burst.

In the [fresh-window follow-up](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/43412081101),
each cohort completed all 48 governed reads with no refusals or contract
failures. Each 16-read batch used 32 intercepted metadata calls after at least
61 seconds of natural cooldown. Actual peak concurrent requests were 1, 10 and
16. Cleanup was verified, and the run made no provider calls. Sustained hosted
throughput and concurrency above 16 remain unmeasured.

## Workspace Skill ROI read

The Workspace learning tab requests Skill ROI within its owned Workspace read.
The backend checks ownership before computing the existing ranking from
saved job snapshots, match skills, outcomes and approved evidence skills. The standalone endpoint remains available to existing
clients. During frontend and backend version overlap, the new frontend uses it
only after a successful Workspace response for the same opportunity omits the
new field. The current DB8 API predates the compact Workspace route, so the
frontend still requires the bridge API first. An unavailable projection or
capacity refusal is shown explicitly.

The local HTTP regression exercised real bearer validation through intercepted
native SDKs. One combined read used two fresh List requests and returned the
same result as the standalone endpoint. This saves one authenticated HTTP
request and two metadata calls for a cold learning-tab view on the matching
release. Production throughput and field latency remain unmeasured.

The separate `--skill-roi-journey` diagnostic (workflow input `skill_roi_journey`)
compares both paths on the same synthetic dataset and API build without changing
the default or fresh-window workloads. The eight enrolled actors, including
the 500-opportunity account, each visit a fixed owned learning tab in both arms.
The first arm makes the original two concurrent reads. The second uses the
consolidated read. Both use the original project governor and each waits at
least 61 seconds before sending traffic. The report must confirm all eight
journeys per arm, exactly 16 versus eight completed HTTP requests, 32 versus
16 synthetic Admin Lists, and equivalent complete Skill ROI ranks and Workspace
projections. The two extra access controls require 401 without a bearer and 404
for another owner's exact opportunity. The cap is 29 HTTP requests including
all controls. No real provider calls are made. The fixed-order, direct HTTPS
measurement does not establish browser or production latency.

The [hosted Linux run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/43469657852)
verified the expected counts and equivalent output in both the 20% and 25%
synthetic buyer cohorts. Each completed all eight journeys per arm, moving
from 16 to eight HTTP responses and from 32 to 16 intercepted Admin Lists.
There were zero provider calls. The [default workload](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/43469626412)
also reproduced its existing response counts in both cohorts.

## First-minute overlap of authenticated reads

Use `--first-minute` with the existing runner, or select only `first_minute` in
the owned-local workflow dispatch. Its dependency-free plan prints the exact
request ceiling before execution. This mode runs the normal authenticated
Dashboard, Workspace, unified order-status and upload-status GET routes using
fixture-owned accounts. The sink is the same local TLS Redis governor and
Firestore emulator as the existing workload. First-minute control reads check
that another actor cannot read the fixture Workspace, order or upload and that
the correct owners see the expected pending order and queued upload.

The window cools down naturally for at least 61 seconds and then offers 37 or
41 scheduled reads, depending on the synthetic buyer cohort. There are up to
eight control reads and at most eight concurrent measured reads. Pending orders
and queued upload rows are seeded in the isolated migrated database before the
server enforces read-only transactions. The diagnostic uses the first-minute
visible polling schedules of 0/5/15/35 seconds for payment and 5/15/35 seconds
for an already-queued upload. A valid refusal stops that payment stream or moves
the upload beyond the measured window via its 60-second retry delay. Fixed
Dashboard and Workspace arrivals continue to measure shared-budget contention.

The report preserves planned, sent, completed, refused and suppressed reads by
journey. It includes observed tail latency, response byte totals, exact
Retry-After validation and one counter sample per second. Redis INFO sampling
adds one local command per observation. Success requires exhausting the same
40-call shared List allowance and observing controlled refusal with no Get
fallback or SQL mutation during measurement. Because one request can consume
its first List before another uses the final allowance, success counts may vary.
Owned success responses, including the three positive preflight controls, must
carry exactly the `private` and `no-store` cache directives. Negative ownership
controls check the route's promised response status.
This is a bounded diagnostic with fixed arrival offsets and synthetic external
responses. It does not measure browser network timing or production peak
capacity, and it creates no provider order or upload object.

The [strict first-minute run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/43582472448)
replayed the saved Dashboard, Workspace, payment status and upload status reads in
an owned environment with 10,000 seeded users. With the same 40 synthetic Admin
List calls per minute, the 20% buyer cohort returned 19 successful reads and 16
capacity refusals, and the 25% cohort returned 16 successful reads and 21 refusals.
The client suppressed two and four repeat reads respectively. Both runs recorded
zero external provider attempts and zero contract failures. The observed refusals
limit the supported throughput under this workload.

## Original acceptance remains open

The existing target remains about one million authenticated requests/month plus
separately measured peaks. The shared Firestore quota bucket is
`DatabaseOperationsPerMinutePerProject`, metric
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
The historical 24-at-2.5-second estimate is superseded. First-minute behavior
under a production traffic mix, concurrent streams, other callers and Redis
command allowance still needs measurement.

On November 14, the signed-in Jobs page encountered four 429 responses under the
serving 40-call governor. After the window cleared, the same reads succeeded. The
live frontend now paces those four reads and shows one explicit retry after a
capacity refusal. A post-deployment signed-in read verified four sequential 200s.
In a bounded 24-hour Cloud Run log sample, each relevant route had only one or
two successful requests, with the longest successful response ranging from about
1.96 to 2.38 seconds across the six routes. This sample cannot support a production
p95. The remaining benchmarks include native SQL CU, Redis allowance, autoscaling,
browser p75 metrics, invoice costs, cost envelopes and 10,000-user peaks. Any
change to the live governor and Stage5 acceptance remain gated.
