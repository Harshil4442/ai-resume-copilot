# Source27 release after the Neon continuity cutover

This design covers the matching application bridge on schema26 and the additive
schema27 migration. The new database is already serving schema26. The standalone
continuity observer verified that database through the designated read-only role.
The existing release controllers are still bound to the previous Neon project.

## Freeze a new database scope before bridge capture

Use a distinct release scope for Neon project `divine-flower-30944770`, branch
`br-small-surf-bdqqplk8`, endpoint `ep-holy-sound-bdqtltxz` and database OID
`16396`. Bind the exact PostgreSQL cluster SHA-256 and the role OIDs recorded in
the role preparation and independently verified destination receipts. Reobserve
the destination SQL identity and serving Cloud Run state when capturing and
before acting on a plan. The cutover receipts establish lineage to the old
database and do not substitute for fresh readback.

Keep the old plan shapes, journal hashes, collectors and retained evidence as
recorded. The [continuity bridge plan](../../infra/gcp/schema27_continuity_bridge_plan.py)
uses a separate typed plan with
[continuity database custody](../../infra/gcp/schema27_continuity_scope.py).
The old `SuccessorPlan` requires numeric observer and retired secret fields
that the recovered database does not have. The new loader checks the supplied
file hash and kind, validates the source and service pins locally, and binds
the complete bridge metadata and database scope to the existing journal
format. The normal traffic builders accept the resulting immutable plan.
The [native bridge dispatcher](../../infra/gcp/schema27_continuity_bridge.py)
checks this custody before staging, promotion, or rollback. The separate
[migration dispatcher](../../infra/gcp/schema27_continuity_migration.py) binds
its plan to the original bridge journal and promotion receipts. The migration
intent and same-transaction audit include the physical database identity.

Validate the exact native Git SHA, successful CI, Cloud Build output digest,
bridge image and controller source. Keep scanner proof, funding and model
policy, admission, IAM and resource checks in the normal release path. The
database continuity scope does not qualify any of those gates.

## Bind the new database ports to existing service identities

| Role | Source and use |
| --- | --- |
| Runtime, OID `49154` | Verify `hirewiz-fresh-runtime-db-20261013:3` is the exact enabled version referenced by the API, analysis worker and employer worker. Authenticate through the fixed version, verify the new direct host and role, and use a read-only transaction for release observation. |
| Observer, OID `49156` | Use the existing private role bundle through a bounded in-memory credential read under the release operator. A separate independently authorized numeric Secret Manager version is needed only if the chosen runner cannot use this existing custody. Use the authenticated connection for the entire catalog and receipt transaction. |
| Resume coordinator, OID `49158` | Independently verify `hirewiz-resume-coordinator-db-20261015:2`, current revision and rollout state. Keep it under the external resource check while the three app services are rolled. |
| Migrator, OID `49152` | Verify `NOLOGIN`, no active migrator session, no persistent schema `CREATE` privilege and no extra role authority. The recovered role has no issuance history for a numeric migrator credential. Old disabled secret metadata, when retained for lineage, describes the old project. |
| Bootstrap, `neondb_owner` | Verify the direct endpoint and database owner through the normal Neon manager. Pin its native OID by readback before building the write connection. It can assume the exact NOLOGIN migrator for the reviewed transaction; establish that membership from native SQL. |

Capture a nonce-scoped commitment to the existing observer credential in the
private bridge plan. The controller must match the committed scope to the
journal before resolving the credential. The continuity reader opens the
same-user role bundle read only and refuses permission drift, symlinks, and
missing files. It verifies the commitment in memory before authenticating
the exact database observer. Keep the resolved credential and its private
plan out of logs and provider requests.

The [catalog observer](../../infra/gcp/schema27_continuity_catalog.py) verifies
TLS, the observer's identity, the exact schema, sessions and protected liabilities
in a single read-only snapshot. It uses the SQL backend PID for the session
check. The [runtime probe](../../infra/gcp/schema27_continuity_runtime.py) checks
its own numeric secret and SQL identity independently.

Replace the old database-only bridge port identified by
[`NativeSuccessorBoundary._database`](../../backend/scripts/schema26_app_successor.py).
Keep its schema26 catalog, permissions, evidence writer fence, role inventory,
session checks, and protected liability comparison. Use the current serving
revision and new DB versions as the captured rollback baseline for all three
app services. Verify the coordinator, queue, schedule, document worker and
IAM resource pins separately. Verify that no reachable or rollback app
revision points to the previous database, including tagged revisions.

## Preserve the one-transaction migration on the new target

[`NativeMigration27Boundary`](../../infra/gcp/schema27_native_migration.py)
needs three new fixed input ports. Use the continuity observer for exact26 and
exact27 catalog reads, the continuity runtime reference for metadata
initialization with its application Engine still non-connecting, and the
new bootstrap via the existing native Neon manager for the one write
transaction. Resolve the manager credential directly to private memory and
verify effective TLS, exact database/owner OIDs, available `SET ROLE` and native
cluster before writing. The older bootstrap helper supplies credentials for
the previous Neon project.

There is an additional PostgreSQL permissions difference. The old bootstrap
had direct grants on `users`, `alembic_version`, and `admin_audit_events`.
The continuity restore replayed the runtime, observer and coordinator grants.
The new bootstrap has a self-issued SET-only membership in the migrator.
Neon's `cloud_admin` role issued a separate ADMIN-only grant to the
bootstrap. Both grants are non-inheriting, and the provider's grant confers
no SET privilege.
In [the current transaction](../../backend/scripts/notification_schema27_step.py),
old table reads and locks precede `SET LOCAL ROLE`. The controller then inserts
the audit row after the transaction helper has reset the role. Qualify a
continuity transaction that uses the migrator for the application table
catalog/data reads, marker and FK locks, Alembic DDL, and audit insert. Use the
bootstrap only for the physical-identity and schema-privilege operations that
require it. Preserve the existing advisory lock, lock timeouts and final
catalog/ACL comparisons. The
[successor SQL step](../../backend/scripts/notification_schema27_continuity_step.py)
prepares the DDL and detached audit under that transaction-local role. The
dispatcher writes and rereads the intent before passing its hash to the
transaction. The [owned PostgreSQL test](../../backend/tests/test_neon_continuity_migration_permissions_postgres.py)
uses a non-superuser, SET-only bootstrap without direct table grants and
verifies both atomic rollback after DDL and audit and a successful commit.

Retain the normal durable intent before DDL, exact bridge stage/promote
started/acknowledged/readback records, same-transaction audit row, and shared
rollback lock. Reconciliation observes exact26 or exact27 using independently
bound read-only connections. A verified exact-schema mismatch is the only
reason to probe the other schema. A role, transport, target, or permission
failure stops the operation. A migrated marker requires the matching detached
audit; an existing intent never causes DDL to be replayed.

## Verify the rollout in order

1. Run actual owned PostgreSQL 17 integration with the recovered role model.
   Verify schema26 protected data, exact ACL fingerprints, failed partial DDL
   rollback, a successful schema27 audit in the same transaction, lost-ACK
   reconciliation and pre-DDL bridge rollback. Exercise wrong host/role,
   stale version, mismatched scope and receipt, and wrong-schema refusals.
2. Capture the serving application and three new candidate revisions with the
   same new database runtime binding. Stage at zero traffic and verify
   compatible owned reads with the currently serving frontend. Promote the
   bridge using its journal, then promote the matched frontend.
3. Reverify source, policy, resources and exact schema26 and roll-forward
   recovery evidence. Run `python -m infra.gcp.schema27_continuity_migration capture`
   with the original bridge plan, its SHA-256 and its journal. Use a separate
   private journal for `upgrade` and `reconcile`, with the captured migration
   plan's SHA-256. Keep the original bridge journal on its original filesystem
   at its captured path. The bridge and migration share one lifecycle lock.
   A matching migration fence allows only that migration plan to continue if
   execution stopped before recording `STARTED`. Once `STARTED` is durable,
   run `reconcile` and leave notification flags closed, including when the
   observer reports schema26.

The actual Neon Free schema26 catalog, five protected liability groups and
the bootstrap SQL role were verified through read-only checks on November 15.
The traffic and migration dispatchers were exercised against local fixtures.
Hosted staging still requires an exact source build and current policy and
scanner evidence.

Before recording the migration fence, rollback uses the verified schema26
revision on the current database. A schema27 rollback or forward recovery
must have its own verified schema27-compatible release and
customer-obligation checks.
