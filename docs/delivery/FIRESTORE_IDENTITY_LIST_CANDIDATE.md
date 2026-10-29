# Request-local native identity List candidate

This is an inactive source candidate. `CANDIDATE_ACCOUNT_LIST_IDENTITY_ENABLED`
defaults to `false`. No IAM grant, runtime probe, deployment, flag activation or
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

Before activation, use the existing shared Redis/`limits` mechanism for an atomic
project-keyed moving-window budget **before each physical metadata call**, across
both List and Get callers. The installed `MovingWindowRateLimiter.hit(...,
cost=1)` and Redis `acquire_entry` support atomic finite admission. This is request
pacing, not cached identity authority. Missing/unavailable shared storage must
refuse admission, with no memory fallback. A distinct typed capacity refusal can
map ordinary HTTP validation to 429 plus bounded `Retry-After`; unavailable Redis
remains a fail-closed dependency error. A late capacity refusal must not let an
earlier identity or dependent read escape as successful validation. Native
transactions, SDK retry policy and unknown-write handling stay unchanged.

An illustrative 40-unit moving 60-second window leaves 20 of the observed 60
units for reviewed nonparticipating/operator work. It supports at most 20
ordinary two-List validations per minute, before lifecycle traffic, aborts and
other metadata use. It is not a final activation value. Inventory all reachable
callers and old revisions first: an unpaced old revision can still consume the
shared provider quota. Redis command allowance, concurrent-window behavior,
late refusals, realistic page bursts and aggregate traffic must be measured.
Consolidating genuinely related owned reads within one authenticated HTTP request
may reduce demand; sharing a successful identity across separate requests may not.
If the measured budget cannot support intended demand, keep activation gated.

## Verification scope

Installed-GAPIC and actual account-factory tests intercept native transports
locally. They prove request shapes, `retry=None`, no Get fallback, two fresh
brackets per ordinary validation, unchanged dependent GCS/session checks,
replacement/partial-list refusal, sticky failure, abort invalidation and preserved
legacy/lifecycle behavior. They do not prove IAM, provider consistency under a
destructive restore, real quota headroom, live latency or deployment compatibility.
Any frozen native/source compatibility change requires independent review and
the normal immutable release acceptance; do not silently repin an older protocol.

Primary references: [ListDatabases](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases/list),
[database UUID field](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases),
[Firestore IAM](https://firebase.google.com/docs/firestore/security/iam), and
[Cloud Run Job pricing](https://cloud.google.com/run/pricing). Job compute at the
published Iowa default rates is approximately US$0.00114 per 60 billable seconds
for one vCPU and 0.5 GiB, before free-tier credit, tax and logs. The full container
lifetime and minimum billing apply; this is not a hard cash cap or invoice cost.
