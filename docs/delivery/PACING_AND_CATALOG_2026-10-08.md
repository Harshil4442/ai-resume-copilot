# Employer feed pacing and catalog read optimization

Status: **integrated and locally verified; separate rollout pending**. The live release
remains [admission foundation `7cc49c2`](PRODUCTION_ADMISSION_RELEASE_2026-10-08.md).
This stage adds no employer sources, grants, automatic submissions or production Redis.

## Changes

The authenticated job catalog previously selected the owner a second time after
authentication had already loaded that exact SQLAlchemy identity. The advisory catalog
now reuses that identity with `db.get`. A cold session still loads the requested owner;
a cached different owner cannot supply its balance. Existing source order, configuration,
prices and missing-owner behavior are preserved. Financial/admission operations continue
to refresh balances under their authoritative lock. This removes one avoidable SQL
round-trip on the normal authenticated catalog path; it does not establish a hosted
latency percentile or identify the cause of prior slow observations.

Lever pages and transient GET retries now acquire a shared host permit before dispatch.
Redis server time, non-evicting state, ownership checks, bounded HTTP/stream-close time and
restart quarantine coordinate cooperative workers across tenants. An unavailable or
incompatible coordinator rejects the scan before unpaced requests; incomplete scans
preserve existing jobs. Current Greenhouse, SmartRecruiters and Ashby pilot feeds remain
independent of this coordinator. See [the pacing contract](LEVER_HOST_PACING.md) for
bounds, failure assumptions, primary references and prerequisites.

Dedicated `EMPLOYER_HOST_PACING_REDIS_URL` takes precedence over legacy shared Redis
settings. An invalid explicit authority never falls back to another authority. Enrollment
requires one reviewed compatible primary on both API and employer-worker runtimes and
secret references retained by release configuration. No production coordinator was
configured by this stage; Lever remains nonlive. Public-feed coordination grants no
employer submission permission.

## Evidence and release checklist

[Integrated local proof](evidence/2026-10-08-pacing-catalog-local.json) records **545 passed,
zero skipped**, including 26 actual PostgreSQL cases, 30 pacing cases with eight actual
Redis tests, and four SQLite catalog query/ownership cases. It binds source hashes and
the same Redis image manifest pinned in CI. Ruff, Mypy across 50 source files, compile
checks and OpenAPI regeneration with zero drift passed. The task-owned Redis container
was removed; no production data or employer request was used by these tests.

CI now provisions a disposable loopback Redis with bounded readiness, test-only
configuration and cleanup, so its real coordination cases cannot silently skip.

- [x] Integrate only the bounded connector/coordination and advisory catalog changes.
- [x] Pass the complete merged backend suite, lint/type checks and unchanged contracts.
- [x] Preserve private workers, manual current sources and disabled automatic submission.
- [ ] Pass exact-commit remote CI including actual Redis/PostgreSQL cases.
- [x] Take a [protected fresh schema-0009 backup](evidence/2026-10-08-pacing-protected-backup.json) and verify metadata before promotion. This is not restore/RPO/RTO proof.
- [ ] Promote tested immutable images/frontend; verify identities, health and flags.
- [ ] Observe a completed public-source task and bounded post-release logs.
- [ ] Independently verify compatible shared runtime Redis before any Lever enrollment.

The wider device/recovery authority, permitted form/receipt, restore, field performance,
source-use and recall gates remain open. No greater-than-95% coverage, real auto-apply,
restore RPO/RTO or absolute physical network fencing guarantee is claimed.
