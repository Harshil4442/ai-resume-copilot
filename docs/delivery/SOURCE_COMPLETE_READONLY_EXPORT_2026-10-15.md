# Complete source export permission

The former source transition granted the export observer `USAGE`/`SELECT` only
in `public`. A complete `pg_dump` also reads separately owned schemas such as
`neon_auth`; public-only access caused SQLSTATE `42501` before the full backup.
No schema is excluded by this change.

The original transition now checks the exact managed `neon_superuser` role and
its `ADMIN` authority for `pg_read_all_data` before consuming the transition's
once marker. The authenticated old owner must have the catalog `SET` path to
that surviving grantor. The SQL transaction then issues a **direct** `GRANT`
with `ADMIN FALSE, INHERIT TRUE, SET FALSE` to the export observer. It never
executes `SET ROLE neon_superuser`: Neon's independent provider hook rejects
that action despite the catalog's `SET` membership.

PostgreSQL chooses the reachable managed ADMIN grantor. The transition verifies
the actual membership's grantor is `neon_superuser` OID `16384`, so removing the
old owner cannot cascade away this export grant. The observer remains an
ordinary `LOGIN NOINHERIT` role with no elevated attributes and no other active
role membership. The membership's explicit PG17 `INHERIT TRUE` enables reads
without granting `SET` or role administration. The new owner remains `NOLOGIN`;
ownership, source fencing, provider deletion and once-only recovery rules are
unchanged. Failure before commit rolls back the complete SQL transition.

`pg_read_all_data` is a cluster-wide read capability wherever existing `CONNECT`
permissions allow entry; this is not a database-scoped role grant. This change
adds no `CONNECT` grant, write permission, RLS bypass or ability to administer
or `SET` the predefined role. Source export still fails rather than bypassing
an RLS or unsupported-object restriction.

The owned local PostgreSQL 17 regression reproduces public-only access and a
full backup failure, proves direct GRANT as an authenticated non-superuser,
forces a rollback, commits the original helper, deletes the temporary old actor,
and exports/restores **both** `public` and the independently owned `neon_auth`
schema with their original rows. It verifies denied data writes, schema creation,
sequence writes, `SET ROLE` and role administration. Production catalog/fence
admission and Neon's provider hooks are separate native verification boundaries.

`database_transfer._catalog` already includes all non-system schemas. Ordinary
tables using built-in types and only `plpgsql` need no schema exclusion. User
routines/event triggers, standalone/custom types, foreign tables and additional
extensions still require explicit transfer review.

The production source had already passed its original transition before this
fix was written. Do not rerun that transition: the root operator separately
repaired the live read-only membership and retained its native receipt. This
source change belongs in the next immutable operator image and does not claim
that the frozen F7 image has changed or that deployment is complete.
