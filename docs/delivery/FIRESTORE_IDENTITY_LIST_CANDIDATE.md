# Request-local native identity List candidate

This is an inactive source candidate. `CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED`
and `CANDIDATE_METADATA_ADMISSION_ENABLED` default to `false`. List validation
requires both flags explicitly true. No IAM grant, runtime probe, deployment, flag activation or
10,000-user capacity qualification is provided by the local tests.

Only `ConfiguredCandidateAccounts.validate` can use the new transport. A genuine
complete project `ListDatabases` observes both separately pinned native database
UUIDs before the existing witness, closure, SQL credential and retained-session
reads. A second List starts after every existing read and configuration check,
before validation returns. Each request has a fresh scope. No UID survives the
request, no TTL grants authority, and no failed List falls back to Get or retries.
Definite-abort invalidation requires another fresh List before reuse; a caught
failure poisons the scope. Existing exact-context ownership, task/thread exclusion,
deadline and quarantine checks remain. Lifecycle mutations and pairing/signing
retain their old independent metadata barriers.

The installed SDK returns a `ListDatabasesResponse`, without a page token. A
nonempty `unreachable` field, duplicate name, foreign project, deleted pinned
database, wrong type/UUID, missing pin, oversized response or unavailable native
call refuses validation. Extra unrelated live database metadata is not authority.
The finite project database count is capped at 100 and the serialized response at
512 KiB; those bounds fail closed rather than truncate.

## Native evidence still required

On 2026-10-29, private read-only production evidence records an actual
`ResourceExhausted` failure at the existing `FirestoreRpc` GetDatabase identity
boundary. Current Monitoring and Cloud Quotas readbacks both report
`DatabaseOperationsPerMinutePerProject = 60`. Delayed method-labelled quota
metering records one `database_operation_requests` unit for the earlier single
ListDatabases probe. The two current native database UUIDs matched a fresh
operator-context complete List. These facts do not prove the runtime service
account can list the project or that representative peaks fit the quota.

The existing runtime's condition-bound custom role does not include
`datastore.databases.list`. Preserve it. A separate project-scoped custom role
with only that metadata-list permission is the least-privilege proposal; it adds
no database Get, document-read/write or service-account impersonation permission.
Policy Troubleshooter is disabled, so no effective runtime permission result is
claimed from that API. The private proposal prepares a one-task, zero-retry,
60-second metadata-only Cloud Run Job under the existing runtime account, using
the exact previously tested immutable API image and no secrets/API environment.
It performs one List, checks both real UUIDs, logs finite success/failure metadata,
then its exact nonce-owned Job must be removed after evidence is retained. Creating,
executing and deleting that Job, or changing IAM, requires the root's reviewed
dispatch. Unknown acknowledgements require readback, not repeated execution.

## Shared capacity gate before activation

Two ordinary List calls halve four Get calls but still consume the same finite
project quota. The existing client policy already refuses automatic retries for
429/503, disables focus refetch, and bounds visible progress polling. SlowAPI's
existing per-IP decorators do not impose a project-wide metadata budget.

The source now uses the existing shared Redis/`limits` moving-window Lua script
**before each physical metadata call**, across both List and retained Get callers.
It reserves exactly one unit on one fixed project key. Budget changes cannot
create another counter namespace. Explicit
`CANDIDATE_METADATA_CALLS_PER_MINUTE` must be between 1 and the reviewed source cap
of 40; absent, malformed or larger values refuse admission. Redis server TIME
executes inside the same atomic Lua invocation as the exact SHA-pinned installed
moving-window body. No client-supplied timestamp can become stale during a pause.
The installed body returns Lua true/false (RESP2 integer 1/nil); a narrow wrapper
converts those definite results to integer 1/0. Unlike the library's permissive
Boolean coercion, the adapter accepts only that exact integer acknowledgement.
An unreviewed installed script checksum refuses admission. Unknown outcomes
are never retried or refunded. No identity or successful admission survives the
call. Existing scope cache hits consume no new quota because they make no RPC.

The existing shared Redis connection must use TLS certificate/hostname validation,
finite one-second connect/socket limits and zero connection retries. A memory,
unknown or unbounded store refuses enabled admission. Capacity maps to HTTP 429
with `Retry-After: 60`; Redis/configuration uncertainty maps to 503/30. Types remain
distinct through the List/Get, observation, protected-publication, private-ingress
and account HTTP boundaries. A late refusal cannot export an earlier validation.
Lifecycle writes, native transactions, SDK retries and unknown-write reconciliation
are otherwise unchanged. The current source scan finds only two application
metadata call sites, both in `gcp_rpc.py`, and both are covered.

An illustrative 40-unit moving 60-second window leaves 20 of the observed 60
units for reviewed nonparticipating/operator work. It supports at most 20
ordinary two-List validations per minute, before lifecycle traffic, aborts and
other metadata use. Even the no-other-traffic 30-day ceiling is only 864,000
ordinary validations, below a one-million-request monthly target. It is not a
10,000-user activation qualification. Inventory all reachable
callers and old revisions first: an unpaced old revision can still consume the
shared provider quota. One Lua dispatch now includes server TIME, with extra
definite-NOSCRIPT loading on first use; the provider may meter commands executed
inside scripts separately. Do not assume this fits the current free provider
allowance. Redis command allowance, concurrent-window behavior,
late refusals, realistic page bursts and aggregate traffic must be measured.
Consolidating genuinely related owned reads within one authenticated HTTP request
may reduce demand; sharing a successful identity across separate requests may not.
If the measured budget cannot support intended demand, keep activation gated.
Active analysis polling must be included in that journey-level demand. The
companion connection-status loop is local extension messaging and does not
automatically poll the owned HTTP account endpoints; its explicit authenticated
actions still count when they reach those endpoints. The qualified payment poll
now allows eight adaptive automatic reads, plus one explicit manual read; the
historical 24-at-2.5-second estimate is superseded. Multiple streams, initial page
reads and lifecycle traffic still need aggregate measurement against the 40-unit
budget. The bounded owned-local plan is in `STAGE5_OWNED_HTTP_CAPACITY.md`; it does
not qualify provider peaks or the unchanged million-request target.

## Verification scope

Installed-GAPIC and actual account-factory tests intercept native transports
locally. They prove request shapes, `retry=None`, no Get fallback, two fresh
brackets per ordinary validation, unchanged dependent GCS/session checks,
replacement/partial-list refusal, sticky failure, abort invalidation and preserved
legacy/lifecycle behavior. They do not prove IAM, provider consistency under a
destructive restore, real quota headroom, live latency or deployment compatibility.
The separate `test_native_metadata_admission_redis.py` suite starts and terminates
only its own isolated loopback TLS/RESP2 Redis process. It proves the installed
body's nil denial, the integer wrapper's typed capacity refusal, actual concurrent
40-of-80 admission, and paused/out-of-order clock behavior. It never accepts a
provider/shared URI. Set `HIREWIZ_OWNED_REDIS_SERVER` to a TLS-enabled executable;
the mandatory compatibility environment refuses a missing executable. Local proof
used official Redis 7.2.7 source, built privately with the existing OpenSSL tooling.
It is not an Upstash TIME/EVAL, command-allowance or runtime-service-account proof.
The earlier split-TIME/inert-only pacing commit is superseded and must not activate.
Any frozen native/source compatibility change requires independent review and
the normal immutable release acceptance; do not silently repin an older protocol.

Primary references: [ListDatabases](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases/list),
[database UUID field](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases),
[Firestore IAM](https://firebase.google.com/docs/firestore/security/iam), and
[Cloud Run Job pricing](https://cloud.google.com/run/pricing). Job compute at the
published Iowa default rates is approximately US$0.00114 per 60 billable seconds
for one vCPU and 0.5 GiB, before free-tier credit, tax and logs. The full container
lifetime and minimum billing apply; this is not a hard cash cap or invoice cost.
