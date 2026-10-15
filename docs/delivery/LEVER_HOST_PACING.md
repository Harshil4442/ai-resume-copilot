# Public Lever GET host pacing

Scope: discovery and manual-form revalidation through documented public Lever GETs.
Updated 15 October 2026. PostgreSQL coordination and schema18 are awaiting release;
**Lever remains nonlive until migration, runtime connectivity and enrollment are verified**.
The earlier Redis implementation is recorded in
[release `ec52279`](PACING_AND_CATALOG_2026-10-08.md). Neither version grants submission
permission or establishes complete job coverage.

## Contract and bounds

- Every page and transient HTTP-status retry shares a fixed API-host row across tenants,
  worker threads and processes. `api.lever.co` and `api.eu.lever.co` have separate rows.
  The one-second policy is conservatively applied to both; US API robots explicitly
  advertises it. This does not grant EU enrollment or submission rights.
- Short primary PostgreSQL transactions lock the host row, use a unique owner token
  and authority epoch, and check native server-start identity and database-clock
  high-water. One owner holds a 30-second lease. Response stream completion is followed
  by at least one second of monotonic cooldown before another cooperative worker
  receives a permit. No transaction or connection remains held during employer HTTP.
- The async HTTP transport has a total 20-second deadline, including headers/body,
  and up to two additional seconds for stream teardown. HTTPX's ordinary phase/inactivity
  timeouts alone are not used as a total deadline.
- Unknown stream closure/dispatch failure keeps the lease. Missing state, changed
  server-start identity or expired ownership requires a full **32-second monotonic
  recovery quarantine**, retaining the conservative previous lease-plus-close bound.
  An old, expired or mismatched owner cannot release a newer lease. A backward database
  clock observation invalidates ownership and refuses until the high-water is reached.
- Immediately before each send, the worker verifies its owner, authority, server-start
  identity and sufficient remaining lease. A monotonic latest-dispatch deadline is
  checked before and after verification, so a delayed database response cannot extend
  permission. Acquisition waits at most 65 seconds and stays inside the source's
  240-second scan budget. There are at most three attempts for transient HTTP statuses;
  every attempt acquires a new permit. Long/malformed `Retry-After` is respected by deferring
  the scan rather than retrying earlier. Transport/closure ambiguity stops the current scan.
- Production has no memory/local fallback or cached permission. Coordination statements
  have a 500 ms timeout and row-lock waits a 200 ms timeout. Coordination failure rejects the whole
  scan as a retryable `ConnectorError`. Existing `refresh_source` preserves open jobs and
  prior data, clears the scan claim, records unavailability and schedules recovery in
  15 minutes; durable dispatch also receives the retryable failure. This is a due time,
  not a completion guarantee: five-minute maintenance and bounded task retries recover
  eligible refreshes after that time.
- HTTP, coordinator waits and stream close occur after the source transaction is committed
  and closed. Responses remain bounded to 8 MiB; scans retain the 10,000-posting limit.
  Public requests strip authorization/cookie headers, disable client auth and redirects.

## Enrollment/runtime prerequisites

1. Operator review of the exact employer tenant, API host, applicable API-host pacing and
   source terms; careers-host robots are separate and must not be treated as API policy.
2. Apply migration `20261015_0018` and verify runtime SELECT/INSERT/UPDATE on
   `employer_host_pacing`, with no runtime DELETE. These two operational rows contain no
   candidate data and are outside candidate erasure. All callers use the same writable
   PostgreSQL primary and namespace through their existing database configuration.
   This includes the employer worker and API application-preparation revalidation.
   Replica reads and independently writable copies are unsupported. Keep
   `EMPLOYER_HOST_PACING_MODE=postgres`, which is now the default.
3. Before a restore, logical rollback, authority reseed or downgrade, pause all Lever
   consumers, drain their tasks and in-flight HTTP, and verify no caller remains active.
   Restore through the reviewed database procedure. An authorized owner then clears the
   two coordination rows; ordinary runtime credentials cannot do this. Resume callers
   only on the same primary after verifying schema and grants. Fresh claims perform the
   complete recovery quarantine. Do not restore historical pacing rows while callers run:
   a rollback within the same running server is not detectable from an in-row epoch.
4. Explicit `EMPLOYER_HOST_PACING_MODE=redis` retains the prior strict Redis adapter.
   It requires one reviewed authority with stable `run_id`, `noeviction`, and Lua
   access to `INFO server`, `INFO memory`, `TIME`, `HGET` and `HSET`. Its URI precedence is
   `EMPLOYER_HOST_PACING_REDIS_URL`, then `REDIS_URL`, then Redis-valued
   `RATE_LIMIT_STORAGE_URL`; an unusable authority never falls back. The current Upstash
   native read-only probe found no `run_id`, so it cannot serve this adapter. Upstash
   remains the login/cache provider. A static or stored epoch is not a replacement for
   verified coordination semantics: Upstash documents eventual consistency and
   partition conflict reconciliation. See [Upstash consistency](https://upstash.com/docs/redis/features/consistency).
5. `EMPLOYER_HOST_PACING_MODE=local` is accepted only for explicit `APP_ENV=test`,
   `development` or `local`. It coordinates threads within one process, with no distributed
   claim; it is rejected for production/staging/unknown environments.

The guarantee is bounded **cooperative host pacing under one primary and normal
process/cancellation operation**. A lease cannot physically fence an HTTP send after
arbitrary suspension between verification and dispatch. Same-running-server rollback
requires the pause/drain/reseed procedure above; writable split brain is unsupported.
This coordination does not authorize automatic applications. PostgreSQL row locks end
with their transaction; see [PostgreSQL locking](https://www.postgresql.org/docs/17/explicit-locking.html).

## Local evidence and historical verification

`tests/test_postgres_host_pacing.py` exercises the new primary-backed authority with
independent workers, cold-state waits, ownership loss, expired claims, clock high-water,
late verification, lock timeouts, exact bounded hosts and populated-downgrade refusal.
It accepts only the existing UUID-schema fixture in the explicitly disposable loopback
database; no real employer requests or production database access are part of this suite.
These checks do not establish deployed feed readiness.

`tests/test_employer_host_pacing.py` covers pages/retries, other tenants, missing configuration,
credential/redirect isolation, stalled headers/trickled bodies, ambiguous close/dispatch,
failure preserving existing jobs, no SQL transaction during GETs, and bounded waits.
Optional real-Redis cases use only an explicitly disposable loopback Redis at
`127.0.0.1:56379`, an isolated synthetic key prefix, and the task-owned container name
`hirewiz-lever-pacing-20261008`. They cover spawned independent worker processes with the
production one-second cooldown, server-time cooldown, expired grants, actual restart/key
loss, socket timeout/unavailability and non-evicting configuration enforcement. No employer
requests or candidate data are involved. The container is removed after verification.

The **8 October Redis snapshot** passed **545 backend tests with no failures or
skips**, including 30 pacing cases, eight actual Redis cases, 26 actual PostgreSQL cases
and four catalog query/ownership cases. Exact expanded CI Ruff, Mypy for 50 source files,
compile checks and regenerated OpenAPI with zero drift passed. The test Redis container
was removed. [Retained local evidence](evidence/2026-10-08-pacing-catalog-local.json)
binds the source files and pinned test image; it is not remote CI or production proof.

Run scoped proof from `backend` with the project virtualenv:

```sh
EMPLOYER_PACING_TEST_REDIS_URL=redis://127.0.0.1:56379/0 pytest -q tests/test_employer_host_pacing.py
HIREWIZ_TEST_POSTGRES_URL=postgresql+psycopg://hirewiz:hirewiz@127.0.0.1:55433/hirewiz_admission_test pytest -q tests/test_postgres_host_pacing.py
ruff check app/domains/employer/connectors.py app/domains/employer/host_pacing.py app/domains/employer/postgres_host_pacing.py tests/test_employer_host_pacing.py tests/test_postgres_host_pacing.py
mypy app/domains/employer/connectors.py app/domains/employer/host_pacing.py app/domains/employer/postgres_host_pacing.py
```

Primary references: [US Lever API robots](https://api.lever.co/robots.txt),
[Lever public postings API](https://github.com/lever/postings-api),
[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/),
[Redis INFO](https://redis.io/docs/latest/commands/info/),
[Redis noeviction](https://redis.io/docs/latest/develop/reference/eviction/), and
[Redis lease/failover limitations](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).
