# Prospective schema26 evidence writer fence

This is reviewed-source preparation, not a migration permit, deployment, native
PostgreSQL acceptance or statement that a new app is serving. The prospective
previous application is e23c940, image df4618fa, Build
3a251d77-44e7-4b7a-9987-2c991c8e6d89 and CI41095660943. Those historical source
qualifications do not prove current traffic. A future native capture must prove
the exact prior actually serves, independently of this source.

## Why the database fence is required

Old e23 can cache fact A, a new CAS writer can save fact B, and the old handler
can then approve B using its unversioned PATCH. Moving traffic or closing the
career feature does not cancel a request already admitted before its first SQL.
An idle connection sample, request timeout, delay, row lock in only the new app,
or successful old/new storage exchange does not prove this schedule safe.

Migration20261031_0026 adds the statement trigger/function, a purpose column for
password-reset/privacy tokens, and the protocol1–5 browser-review CHECK. Existing
reset rows receive only the default `password_reset` purpose; existing browser
review values, evidence, approvals, ledgers and credentials are preserved. Its transaction sets
lock_timeout4s and statement_timeout10s, takes ACCESS EXCLUSIVE on evidence_items,
and creates an ALWAYS BEFORE INSERT/UPDATE/DELETE trigger. Obtaining the lock
serializes transactions already touching that table. A late old handler that has
not reached SQL subsequently encounters the committed trigger and receives
SQLSTATE42501. The transaction commits its Alembic marker and trigger together.
A contested lock refuses; an unknown commit needs readback and must not be replayed.
The native wrapper first takes all three affected table locks in sorted order,
then compares the fresh locked row digests and exact owners/ACLs. Only the new
reset-purpose field is normalized in that comparison.

The fixed marker is a cooperative **source-version fence**, not authentication
against arbitrary SQL or a database administrator. Old source, request payloads,
connection options and defaults must not set it. The actual runtime must have no
table ownership, schema CREATE, TRIGGER/TRUNCATE, superuser, role creation,
replication or bypass privileges. Exact trigger/function/catalog source and
role/database defaults are independently checked; no old25 receipt becomes26.

## Explicit new write adapters

- Career creation checks owned resume/new evidence before its finite flush.
- Resume import locks the owned original source, deduplicates exact section
  references and flushes only its new evidence rows.
- PATCH locks/refreshes the owned row and compares the displayed fingerprint
  before editing or approving.
- Individual DELETE also requires expected_fingerprint of the shown row. Missing
  or stale versions return409; foreign rows remain404. The existing UI uses
  versioned rejection rather than an individual delete control and remains so.
- Explicit transcription review checks owned source, both shown versions and
  the complete prospective review before its finite flush.
- Whole-account erasure uses the existing independent account/lifetime checks,
  then scopes only the exact owner-filtered evidence DELETE. It has no per-row
  CAS because the explicit intent covers the whole account. An error rolls back
  SQL erasure; any already-committed independent native account denial remains
  subject to its existing reconciliation, never a false deletion-success claim.
- The already-authorized private evaluation initializer installs and verifies
  the identical real fence in its owned schema. It never fabricates an Alembic
  catalog or bypasses the trigger. Its frozen operation source digest includes
  both new fence helpers; fresh captures are required after source changes.

The marker is SET LOCAL only around each exact flush/statement and cleared before
remaining transaction work. Exceptions force rollback. It is not set in HTTP
middleware, a session constructor or a pooled connection. Later unvalidated
pending evidence cannot borrow the marker from an earlier authorized flush.
Normal PostgreSQL adapters require the exact installed fence and never install
or repair it themselves. SQLite behavior is local development/test behavior and
is not a native security proof.

## Required qualification and production sequence

1. Freeze the actual successor source/app digest and additive HTTP contract.
   Historical v1/v2 source and tests remain unchanged. Run the reviewed retained
   v2 runner in its own exact e23 checkout and separate schema25 database.
2. Run genuine owned PostgreSQL17 tests: CAS2, explicit transcription5, consumer
   accounting2, actual archived e23 late-write/pre-first-SQL schedules, contested
   installer lock, exact catalog/data preservation and marker/pool reset. Pure
   ports and authored tests do not satisfy these gates.
3. Qualify distinct26 native source/CI/Build/transport/catalog/role observation.
   Bind changed neon_direct_identity and its execution-file transitive closure.
   Every previous purpose retains its exact old schema. Confirm the new source
   does not alter worker22/runtime20/parser/policy22a or the14 direct document
   qualifier files; unchanged hashes alone do not prove operational compatibility.
4. Review the concrete native migration plan and maintenance impact before any
   critical DB step. Preserve actual original native identities, one-send intent,
   unknown-outcome reconciliation and all original customer/financial guards.
   No production migration is authorized by this runbook.
5. After an approved atomic migration, require exact26 plus the real fence in
   every fresh native capture. Stage the immutable qualified new API at zero
   traffic, recheck source/secret/IAM/resource/DB/health guards, promote all
   captured API traffic/aliases, then the matching frontend. Old UI writes are
   intentionally refused. Evidence mutations on old API may fail during this
   maintenance interval; do not describe a seamless old-writer compatibility.
6. Keep all AI/sales and unrelated capacity/quality gates closed. Schema26 is not
   native full200/model/human quality, worldwide coverage or capacity approval.

## Operational blast radius and rollback

The distinct native entrypoint is `infra/gcp/schema26_native_migration.py`.
Its `capture`, `upgrade` and `reconcile` modes require the exact reviewed
maintenance plan, its hash and original journal; the SQL primitive is not a
standalone production command. The closed API baseline retains the original e23
image with candidate ingress and account lifecycle admission disabled. Traffic
movement or an empty SQL session sample is insufficient: every older API
revision must report retired execution resources, and the complete typed native
publication, account-operation/reset history and current SQL state must agree.
A generic denial is never accepted as a privacy-erasure intent or completion.

The upgrade caller persists create-only started and transaction-ready records,
uses the original authenticated bootstrap/physical target, and runs the exact
one-use guard again after table locks immediately before Alembic. Fresh SQL and
full revision-retirement checks precede DDL. Commit acknowledgement is recorded
separately from fresh read-only catalog, row/ACL and liability observations.
`reconcile` observes a known25 or26 outcome and never executes DDL. Missing,
changed or uncertain evidence stops the operation; it does not authorize another
upgrade attempt. Existing unknown financial obligations remain retained.

Original schema25 app, alias, pilot-reopen, deployed-maintenance and expense
reconciliation/canary operators correctly refuse26. They must retain that behavior.
Before using those operations on26, qualify distinct successor source/adapters,
plans and original native identities; do not modify an old journal's schema or
pretend an old observer collected26. The current nightly document refresh instead
requires a genuine new reviewed application-build registry entry and unchanged
native document WIRE; it cannot accept a prospective build pin. Ordinary scheduled
worker maintenance and account/export/witness reads have no25 marker check, but
their actual operational compatibility still needs qualification.

Rollback **retains the installed trigger and schema26**. Restoring an old e23
image does not authorize its evidence writes or restore whole-account deletion.
Use a qualified compatible replacement for full write service; a deliberate
restricted old-source recovery must disclose these refusals. The migration's
downgrade refuses before any DDL. No automatic DROP, trigger disable, marker
default, credential rotation, row rewrite or unsafe write reopening is provided.

## Cost and evidence limits

This introduces no model call, new hosted service or recurring subscription.
Migration preparation reads the existing GCP services through bounded read-only
ports; those reads have ordinary provider quotas and costs. A native evidence flush adds bounded catalog/marker SQL and the
database trigger; their latency/capacity/cost are not measured by local port tests.
Native lock/role/catalog and old/new process tests are authored but must execute
in the existing explicitly owned CI PostgreSQL environment. Production migration,
native26 operational acceptance and deployment remain pending.
