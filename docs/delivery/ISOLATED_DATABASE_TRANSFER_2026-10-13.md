# Data-preserving isolated database transfer

This ordinary library prepares the existing checkpoints 4 and 6. It does not
authorize production cutover, replace `monetary_release.verify_fences`, acquire
credentials, rotate payment keys, create roles, upgrade the schema or promote
traffic. The production source remains `little-pond-36610622`; the new destination
is `shiny-cell-70214683`. The separate `bulk_action_platform` project is untouched.

## Executable scope

`scripts.database_transfer.transfer(source, target, tools, private_directory)`
performs a genuine complete rehearsal using independently observed typed native
identities and transient credentials supplied by the owner. Neither URLs nor
credentials are written to disk or command arguments. The library has no CLI,
JSON identity intake, provider account access or native credential collector.

It verifies genuine libpq transport, PostgreSQL17 cluster/database/actor identity,
refuses aliases and occupied destinations, holds a read-only repeatable-read
source transaction through `pg_export_snapshot`, exports the complete database
and restores in one transaction. It omits old ownership and ACLs. Only the exact
`public` schema CREATE entry is excluded from the archive TOC because the verified
empty destination already has that schema; every other archive entry is retained.
No source/drop/clean/create command, table filter, trigger bypass or automatic
restore replay is available.

An optional independently observed `NativeRestoreRole(name, oid)` supports
bootstrap authentication plus a NOLOGIN migration role. The genuine connection
first verifies the authenticated actor, performs target-only `SET ROLE` and
checks the actual effective role/OID while `session_user` remains unchanged.
Restricted cluster/control functions run only as the authenticated bootstrap
before SET; a fresh bootstrap connection renews that proof after restore. The
non-elevated migrator requires no `pg_control_system` EXECUTE or monitor role.
`pg_restore --role` creates objects under that same role, and a native post-restore
catalog check rejects any user relation/sequence owned by a different actor.
This library creates/grants no roles. Required SET membership, schema CREATE,
read permissions and subsequent CREATE revocation remain reviewed native
preparation; source role override is refused. The disposable fixture exercises
SET-only/INHERIT-false membership and a real NOLOGIN migration role.
Its fixture revokes PUBLIC control-system EXECUTE, grants only the bootstrap
actor, and proves that the effective migrator has no restricted permission.
Startup `search_path=pg_catalog` overrides a deliberately unsafe role default;
all customer relation identifiers remain fully qualified.

The comparison checks all supported schemas/relations, columns/defaults,
constraints, indexes, views, partitions/inheritance, triggers, encoding/collation,
database/provider locale/rules/recorded-plus-actual versions and every referenced
column/index collation's provider/determinism/locale/rules/current version,
sequence configuration/state and large-object IDs/content. Exact binary row
multisets preserve duplicate/no-PK rows, NULL, Unicode, bytea, JSON and numeric
values without reducing equality to counts or hashes. This includes profiles,
resumes, artifact references, credits, accepted payment terms, refunds, replay
ledgers and any financial liability rows actually present in the source schema.
Ownership/ACLs intentionally change and must be verified separately against the
reviewed replacement-role grants. External artifact objects remain in their
existing storage; new runtime access still needs independent verification.

The fixed maximum is 512MiB for the custom archive, 512MiB of source content plus
its destination comparison, 16MiB per row/large object, one million rows, 256
relations/sequences/large objects, 600 seconds per owned PostgreSQL client process
and a 1,800-second work deadline checked between work and COPY chunks. Native SQL
statements have a 300-second timeout; the final production owner must additionally
enforce its absolute whole-process deadline, including bounded cleanup. This
library does not claim an implemented hard outer-process owner. A filesystem-size
limit bounds child output. Exceeding
a bound refuses explicitly. Custom column types, non-plpgsql extensions, user
routines, event triggers and foreign tables/servers currently require a separately
reviewed compatibility implementation; they are never omitted. Actual catalog
shape must establish whether any of these apply to HireWiz before native transfer.

The caller uses a fresh owned 0700 directory. A fsynced once marker is retained
on success or failure. Temporary archive/row data is removed; the marker does not
certify restore state. A timeout, lost connection or nonzero restore result needs
native reconciliation before any separately reviewed new attempt. A changed or
occupied target cannot be overwritten. Rehearsal does not claim finality while
source writers continue; sequence movement explicitly refuses.

## Final integration prerequisites

`purpose="final"` requires verify-full transport and the authenticated native
release owner's `NativeCutBoundary`. That adapter must call the real complete
monetary fence/admission checks and return their original observation/cut times.
It cannot load a JSON assertion or substitute a paused queue/empty snapshot. The
library requires an observation within 30 seconds before and after each phase,
and an unchanged retirement cutoff. The existing missing native verifier remains
an unresolved dependency; no production adapter is supplied or activated here.

Before the final export: establish new numeric secret versions and distinct
service/database identities, close source writes, classify/drain admitted work,
preserve accepted settlements with tested retry/buffering and verify a protected
post-cut backup. Keep Razorpay merchant/mode/API and current/previous webhook
secrets unchanged. The transfer's temporary export is not the retained recovery
backup. Restore schema0009, verify equality, then run the existing guarded
0010–0016 chain with native ownership/grant checks. Stage and promote exact reviewed
GCP/Vercel revisions only after all existing gates pass. Never serve two writable
financial databases or route back to stale source data after destination writes.

## Verification and adoption

Pure checks can run without app imports or native calls:

```text
PYTHONPATH=backend python -m pytest --noconftest -q backend/tests/test_database_transfer.py
```

`test_database_transfer_postgres.py` creates and cleans only UUID-owned databases
and a role on the existing synthetic local PostgreSQL17 fixture. It verifies a
complete real dump/restore; a concurrent update excluded by the held snapshot;
occupied-target preservation; actual transactional restore rollback on a collision;
once/replay refusal; and exact credit, duplicate-row, sequence and large-object
drift detection. No app aggregate is imported. It needs the existing
`HIREWIZ_TEST_POSTGRES_URL` plus matching PostgreSQL17 client executables supplied
via `HIREWIZ_TRANSFER_PG_DUMP` and `HIREWIZ_TRANSFER_PG_RESTORE` when not on PATH.
Configured tests fail if clients are absent; an unconfigured local run skips.
The new source/tests must be added to CI's explicit Ruff/typecheck list in the
consolidated adoption, and actual PostgreSQL acceptance must precede production
execution. No native PostgreSQL test has been run during this preparation.

PostgreSQL17 documents the synchronized snapshot export and transactional restore:
[pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html).

Database and referenced column/index collation definitions compare provider,
determinism, locale, rules and recorded/actual versions. A known stale or missing
version baseline refuses; no REFRESH VERSION or locale normalization is performed.
The database-default collation delegates to the independently checked database
provider. Unicode data is supported without a C-only locale restriction. The
destination database locale is checked before restoration writes.

Reserve the sole empty production destination for the authoritative post-cut
transfer. Run live-write rehearsals on the UUID-owned disposable fixture or a
separately reviewed disposable target; this library offers no destination reset
or delta-replication path.

[PostgreSQL17 database locale metadata](https://www.postgresql.org/docs/17/catalog-pg-database.html),
[collation metadata](https://www.postgresql.org/docs/17/catalog-pg-collation.html),
[version and index safety](https://www.postgresql.org/docs/17/sql-altercollation.html).
