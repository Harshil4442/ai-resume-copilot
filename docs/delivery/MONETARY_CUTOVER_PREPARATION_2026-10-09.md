# Monetary migration: current inventory and release sequence

This is a read-only deployment checkpoint, not proof that legacy writers are retired.
Production continues to serve `e7d8f2b` with schema `20261008_0009`. The `b306f13`
generation-disabled compatibility candidates have zero traffic. The reviewed source
contains migration `20261009_0010`; it must not run against the production database
until the coordinated writer cutover is verified. The legacy `release.sh` entry point
deliberately refuses that migration before cloud access.

## Current verified inventory

An all-region list of project `ai-resume-parser-482412`, with the local region property
unset, returned three Cloud Run services and one job. This is the named project scope;
it does not enumerate other projects, developer processes or every retained revision.

| Resource | Current database configuration | Required cutover treatment |
| --- | --- | --- |
| `ai-resume-parser` | Inline legacy URL | Stage the tested compatibility release; close old generation entry points before replacing the writer identity. |
| `hirewiz-analysis-worker` | Same inline legacy URL | Pause analysis dispatch, classify in-flight/unknown attempts, then replace and fence the old writer. |
| `hirewiz-employer-worker` | `hirewiz-database-url:latest` | Preserve discovery/accounting; move to an exact new secret version and scoped runtime role. |
| `hirewiz-schema-migration` | `hirewiz-database-url:latest` | Use a separate migration identity; prevent old job executions from writing during cutover. |

The referenced `latest` currently resolves to the only enabled version, version 1.
Its value matches the API's inline database URL in memory. Credential values are not
retained in the evidence. Six visible migration executions are complete, with zero
running tasks; this bounded list does not prove all historical executors are absent.
Service-template flags describe the latest staged configuration, not necessarily the
revision receiving traffic.

Earlier retained-revision inventory found a shared database role with elevated
capabilities and multiple historical provider credential groups. Current routable
API/analysis revisions share one provider credential. Other applications' use of those
credentials is unresolved. Neither a disabled flag, an empty recent error log nor a
new secret version revokes a paused old process. Disabling a shared administrative role
or provider credential without establishing its consumers could interrupt other services.

Google documents that secret environment variables resolve at instance startup;
changing `latest` does not replace credentials already loaded by an instance.
[Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets).
PostgreSQL's LOGIN attribute controls initial connections; existing sessions need a
separate bounded termination and verification step.
[Role attributes](https://www.postgresql.org/docs/current/role-attributes.html),
[administration functions](https://www.postgresql.org/docs/current/functions-admin.html).

## Required execution sequence

1. Freeze the exact release commit, all five CI results, image digest, frontend source
   tree, compatibility candidates, schema head and rollback resources. Take and verify
   a new protected backup. A backup is not a restore drill or immutable-retention proof.
2. Enumerate and classify every writer in the deployment scope, retained tagged
   revisions, task queues, jobs and local tooling. Resolve shared credential consumers.
   Prepare distinct runtime and migration roles with the smallest required permissions,
   and exact Secret Manager versions. Do not alter the shared role during this inventory.
3. Promote the tested schema-0009 compatibility candidates with generation disabled,
   pause analysis launches and reconcile existing attempts conservatively. Preserve
   unknown financial obligations; do not reset an attempt or replay a provider call.
   Keep public discovery and billing available where their writers can be safely fenced.
4. Verify credential and database fencing, not just traffic drainage. Stop admission
   through old entry points, revoke the appropriate old credential scopes, prevent new
   old-role connections, and verify existing old sessions are gone. Provider revocation
   needs its own evidence. Do not migrate when consumer inventory or fencing is uncertain.
5. Run additive `0010` once through the separate migration identity; verify schema,
   legacy unquoted history and retained-liability tables. Stage the exact new API/workers
   with automatic migration off and optional generation still disabled. Check private
   worker IAM, queue destinations, origin routes, accounting and deployment identities.
6. Promote tested immutable candidates, then monitor errors, queue age, balances,
   duplicate settlement and source freshness. Enable generation only after a current
   explicit pricing policy, bounded provider configuration and required cost proofs pass.
   Browser disclosure/submission remains governed by its separate permission and
   recovery gates. Frontend-only compatible releases do not advance the SQL schema.

Rollback must not erase retained liabilities: `0010` refuses downgrade when that ledger
contains any row, including settled evidence. Use a verified forward-compatible image
or keep generation off while repairing forward. Do not reopen retired credentials or
restore an older database as an automatic rollback action.

## Checklist and evidence limits

- [x] Named-project all-region service/job inventory and current secret-reference match.
- [x] Visible migration executions checked; none running at the recorded observation.
- [x] Compatibility images staged with zero traffic and schema-0009 health evidence.
- [x] Separate detached-liability and result/deletion lock-order repair reviewed locally.
- [ ] Shared provider/database credential consumers resolved and writer inventory complete.
- [ ] Replacement runtime/migration identities and exact secret versions prepared.
- [ ] Queue admission closed, in-flight obligations classified and old writers fenced.
- [ ] Backup/restore procedure, migration and immutable rollout executed and monitored.
- [ ] Independent restore, provider-invoice/cost and broader application acceptance proved.

The sanitized inventory files in `evidence/2026-10-09-monetary-*` bind these bounded
observations. No production configuration, credential, queue or traffic mutation was
performed during inventory. Full development and deployment remain active requirements.
