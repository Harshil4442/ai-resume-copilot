# Public Lever GET host pacing

Scope: discovery and manual-form revalidation through documented public Lever GETs.
The implementation is promoted in [release `ec52279`](PACING_AND_CATALOG_2026-10-08.md),
with 545 backend tests passing locally and in exact-commit CI. No source enrollment,
submission permission or new production Redis coordinator was added. The employer-worker has no verified
Redis reference; **Lever must remain nonlive until the prerequisites below are verified**.

## Contract and bounds

- Every page and transient HTTP-status retry shares a fixed API-host key across tenants,
  worker threads and processes. `api.lever.co` and `api.eu.lever.co` have separate keys.
  The one-second policy is conservatively applied to both; US API robots explicitly
  advertises it. This does not grant EU enrollment or submission rights.
- A Redis Lua transition uses Redis `TIME`, a unique owner nonce, and server `run_id`.
  One owner holds a 30-second lease. Response stream completion is followed by at least
  one second of cooldown before another cooperative worker can acquire the host.
- The async HTTP transport has a total 20-second deadline, including headers/body,
  and up to two additional seconds for stream teardown. HTTPX's ordinary phase/inactivity
  timeouts alone are not used as a total deadline.
- Unknown stream closure/dispatch failure keeps the lease rather than releasing a normal
  cooldown. Missing keys, a changed Redis incarnation, or an expired owner quarantine the
  host for **23 seconds: 20-second HTTP bound + 2-second close bound + 1-second cooldown**.
  No key expiry is used. An old/expired/mismatched owner cannot release a newer lease.
- Immediately before each send, the worker verifies its nonce, incarnation and sufficient
  remaining lease. Acquisition waits at most 30 seconds and stays inside the source's
  240-second scan budget. There are at most three attempts for transient HTTP statuses;
  every attempt acquires a new permit. Long/malformed `Retry-After` is respected by deferring
  the scan rather than retrying earlier. Transport/closure ambiguity stops the current scan.
- Production has no memory/local fallback, automatic Redis retry, or cached permission.
  Redis connect/socket timeouts are 0.5 seconds. Coordination failure rejects the whole
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
2. All runtimes accessing the same API host use one authoritative Redis primary/key namespace.
   This includes the employer worker and API application-preparation revalidation.
   A split-brain deployment with two independently writable primaries is unsupported.
   Verify connectivity, TLS/credentials where required, and capacity before enrollment.
3. Prefer dedicated `EMPLOYER_HOST_PACING_REDIS_URL`; it takes precedence over existing
   `REDIS_URL` and Redis-valued `RATE_LIMIT_STORAGE_URL`. An explicit unusable authority
   never falls back to a different one. This avoids changing global cache/rate-limiter
   behavior merely to enable a feed. Supply the same reviewed authority on API and worker
   through secret references retained by the release declaration, and keep
   `EMPLOYER_HOST_PACING_MODE=redis` (the default). The Redis ACL must allow Lua `EVAL`,
   `INFO server`, `INFO memory`, `TIME`, `HGET` and `HSET` on the two fixed pacing keys.
   Managed services that forbid these commands are incompatible and fail closed.
4. Redis must report `maxmemory_policy:noeviction`; every gate checks this atomically.
   Pacing keys must not be administratively deleted/rewritten. Cold state or restart incurs
   the derived quarantine and can defer busy scans. No Redis rollout is included here.
5. `EMPLOYER_HOST_PACING_MODE=local` is accepted only for explicit `APP_ENV=test`,
   `development` or `local`. It coordinates threads within one process, with no distributed
   claim; it is rejected for production/staging/unknown environments.

The guarantee is bounded **cooperative host pacing under one Redis authority and normal
async cancellation/clock operation**. It is not an absolute physical HTTP-spacing guarantee
under arbitrary process suspension, Redis clock jumps, network split brain, or an employer
side effect protocol. A lease cannot fence an HTTP send that resumes after arbitrary
suspension between verification and dispatch. Automatic applications remain disabled.

## Local evidence

`tests/test_employer_host_pacing.py` covers pages/retries, other tenants, missing configuration,
credential/redirect isolation, stalled headers/trickled bodies, ambiguous close/dispatch,
failure preserving existing jobs, no SQL transaction during GETs, and bounded waits.
Optional real-Redis cases use only an explicitly disposable loopback Redis at
`127.0.0.1:56379`, an isolated synthetic key prefix, and the task-owned container name
`hirewiz-lever-pacing-20261008`. They cover spawned independent worker processes with the
production one-second cooldown, server-time cooldown, expired grants, actual restart/key
loss, socket timeout/unavailability and non-evicting configuration enforcement. No employer
requests or candidate data are involved. The container is removed after verification.

The integrated release-worktree snapshot passed **545 backend tests with no failures or
skips**, including 30 pacing cases, eight actual Redis cases, 26 actual PostgreSQL cases
and four catalog query/ownership cases. Exact expanded CI Ruff, Mypy for 50 source files,
compile checks and regenerated OpenAPI with zero drift passed. The test Redis container
was removed. [Retained local evidence](evidence/2026-10-08-pacing-catalog-local.json)
binds the source files and pinned test image; it is not remote CI or production proof.

Run scoped proof from `backend` with the project virtualenv:

```sh
EMPLOYER_PACING_TEST_REDIS_URL=redis://127.0.0.1:56379/0 pytest -q tests/test_employer_host_pacing.py
ruff check app/domains/employer/connectors.py app/domains/employer/host_pacing.py tests/test_employer_host_pacing.py
mypy app/domains/employer/connectors.py app/domains/employer/host_pacing.py
```

Primary references: [US Lever API robots](https://api.lever.co/robots.txt),
[Lever public postings API](https://github.com/lever/postings-api),
[HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/),
[Redis INFO](https://redis.io/docs/latest/commands/info/),
[Redis noeviction](https://redis.io/docs/latest/develop/reference/eviction/), and
[Redis lease/failover limitations](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).
