# Schema0009 NOLOGIN role preparation

This is an isolated preparation action for review, based on commit `1c2519bc3989c60405dce5154d8e6bd73067d826`, tree `42566b73b8f36686bbe22d0890cd8e53582121b8`. It is separate from deployment, migration, ownership transfer and legacy-account retirement. One separately reviewed bounded production preparation transaction was acknowledged on 10 October at approximately 04:19 UTC, as recorded below. Existing development/deployment authorization covers this reversible stage; this document adds no new approval flow.

## Evidence and corrections

Retained authenticated Neon metadata, observed October 9 UTC at 23:20:39–23:55:50, records schema `20261008_0009`,40 public tables,9 sequences, NULL relation ACLs, no RLS policies/default ACLs/event triggers,80 associated types and269 old-owned relations including index/TOAST dependencies. The old SQL owner has CREATEROLE/CREATEDB but is not superuser and lacks ADMIN on itself. These are dated observations, not fresh production checks.

The installed native `pg_control_system()` has a NULL function ACL: default PUBLIC EXECUTE. The separately reviewed PG17 public-session-ID observer requires neither `pg_monitor` nor `pg_read_all_stats`. Lack of function GRANT OPTION does not imply lack of execution. The earlier statistics-membership sentence in `MONETARY_PREFLIGHT_2026-10-09.md:74–76` is superseded by its PG17 section at 212–229; historical bridge/statistics templates remain immutable. [PostgreSQL defaults](https://www.postgresql.org/docs/17/ddl-priv.html).

## Bounded preparation

The new action takes an existing genuine psycopg SQL connection and a strict privately reviewed manifest. It creates exactly three distinct ordinary roles: runtime, migration and observer. All are NOLOGIN, NOINHERIT, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION and NOBYPASSRLS. SQL creation avoids Neon's automatic `neon_superuser` membership for console/API-created roles. [Neon role contract](https://neon.com/docs/manage/roles).

Runtime receives SELECT on40 tables, INSERT/UPDATE on39 excluding `alembic_version`, DELETE on only the26 current tables in the pinned release source's `ERASABLE_TABLES`, and USAGE on9 sequences. Observer receives SELECT on40 tables and9 sequences, covering the existing whole-database dump path. Migration receives only namespace USAGE/CREATE; all three receive CONNECT. No passwords, LOGIN, ownership transfer, default grants, old-role alterations or provider/cloud calls exist in this action. Payment/ledger DELETE remains absent, and existing merchant credentials and settlement handling stay intact.

`createrole_self_grant=''` keeps automatic creator SET/INHERIT off. PG17 still creates three creator ADMIN edges; the report records these and explicitly denies isolation/release/cutover authority. [Creator membership](https://www.postgresql.org/docs/17/role-attributes.html).

## Prechecks, postchecks and local proof

Before mutation, bind actual protocol user/PID/PG17 to SQL current/session identity and native function, cluster hash, database and actor OIDs. Require the exact 0009 marker, namespace identity and all 49 reviewed name/OID/kind/owner/ACL pins. Compare complete bounded column/dependency, old-owner object/type, default/policy/provider-object, role/membership and effective ACL fingerprints. Refuse missing/extra objects, role-name collisions, bounds, unknown metadata or drift. Hold bounded table locks and recheck before CREATE.

One transaction then creates/grants and compares the exact allowed deltas: three nonlogin object-free roles, three known bootstrap ADMIN-only edges and precisely the requested ACL entries. All prior roles/memberships, owners, old/PUBLIC permissions, column/default ACLs and dependencies must remain unchanged. Any discrepancy rolls back. Failed/uncertain COMMIT or cleanup returns a fixed unknown-outcome refusal; it never retries or automatically deletes state.

Required local proof uses only the existing disposable PG17.11 endpoint, random-owned schemas/roles and a genuine non-super CREATEROLE actor. Cover exact grants and denied login, protocol/identity mismatch, object/ACL/column/default/dependency/marker drift, collisions, mid-grant failure, hostile poststate and rollback, replay refusal, fixed-error secrecy and uncertain commit. Verify zero owned role/schema/prepared-work leftovers. These local proofs are separate from the later acknowledged production preparation.

## Actual bounded preparation acknowledgment

The preceding read-only V5 capture returned metadata for 40 tables and nine sequences
without subject rows or SQL/cloud mutations. A separate one-shot reviewed operator then
acknowledged a transaction creating exactly three restricted NOLOGIN roles and the
specified current grants. Independent actual-receipt review confirms zero passwords or
LOGIN activation, ownership transfer or default privilege changes; legacy permissions
and merchant/settlement handling remain intact. Three creator ADMIN edges remain, with
zero SET/INHERIT edges. Replacement isolation, legacy/provider retirement, global fencing
and deployment are unproved. The receipt hash is
`a0309c82833b7cc6cc2bb56ef460403339d049ac9281553ba55fdb8e0c3bed2b`;
[checkpoint evidence](evidence/2026-10-10-preflight-checkpoint.json) retains the separate
root and independent acknowledgments. No subsequent credential or LOGIN activation is
included in this result.

## Later steps remain separate

Provision private distinct credentials and review LOGIN only after preparation; independently authenticate each identity before runtime use. For ownership transfer, directly grant the old creator a bounded temporary SET-to-new-migrator edge, transfer each approved table and only independently owned sequences, and separately review database transfer. PostgreSQL moves linked indexes/TOAST/rowtypes/sequences with tables; no fourth shared NOLOGIN bridge, blanket REASSIGN/DROP OWNED/CASCADE or internal-provider-role edits are needed. [Table transfer](https://www.postgresql.org/docs/17/sql-altertable.html), [database transfer](https://www.postgresql.org/docs/17/sql-alterdatabase.html).

Revoke the exact temporary SET edge afterward. This does not remove the bootstrap-granted creator ADMIN edges; their removal needs supported grantor authority or independently verified provider retirement of the old role. Set future defaults from the actual migration identity, with future DELETE still explicit. Old issuer/shared-key consumers, ownership dependencies and active sessions must be resolved before legacy retirement and global fencing. The current release fence remains refused. [Creator-specific defaults](https://www.postgresql.org/docs/17/sql-alterdefaultprivileges.html).
