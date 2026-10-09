# Read-only monetary database preflight

This tool supplies one operational gate for the 0009 → 0010/0011 rollout in
[the coordinated cutover sequence](MONETARY_CUTOVER_PREPARATION_2026-10-09.md).
It does **not** enable generation, deploy a release, authorize migration or retire a
credential. The legacy `infra/gcp/release.sh` guard remains unchanged and still
refuses monetary schema 0010 and candidate schema 0011. No production observation or mutation is included in
the local development proof for this tool.

## Inputs and execution

The operator prepares a private JSON inventory reviewed within the last 24 hours,
then freezes its exact SHA-256. Use the strict `Inventory` schema declared in
`backend/scripts/monetary_cutover_preflight.py`; unknown keys and duplicate JSON keys
are rejected. Include:

- `format_version: 1`, UTC `reviewed_at`, and
  `scope: "named_project_and_declared_external_consumers"`.
- Full `serving_commit`, `candidate_commit`, immutable `candidate_image` digest,
  explicit `candidate_schema` (`20261009_0010` or `20261009_0011`), and the expected
  current database schema (`20261008_0009` before migration, or the exact reviewed
  `20261009_0010`/`20261009_0011` afterward). A candidate pinned to 0010 cannot
  silently adopt an observed 0011 database.
- Independently recorded `expected_system_identifier_sha256`,
  `expected_database_oid` and `database_namespace`. Obtain these through the reviewed
  database inventory; accepting values observed for the first time by this command
  as its own expected pins defeats the identity check.
- Explicit `consumer_inventory_complete`, `shared_credentials_resolved`,
  `unknown_consumer_count`, `provider_fencing_verified`, `queue_admission_closed`
  and `protected_backup_verified`. These are **operator assertions**, not observed
  proof. Do not set unresolved facts to true to obtain a green result.
- A bounded `consumers` list: opaque `label`, `kind` (`api`, `analysis`, `employer`,
  `migration`, `developer` or `other`), credential class, full release, state and
  `generation_disabled`. Include at least all four deployed service/job kinds plus
  identified external/local consumers. Retired consumers must assert `fenced`;
  replacement consumers must bind the exact candidate commit. An empty list,
  unknown consumer or unclassified state fails its database subgate.
- Three distinct `role_env` aliases for `retired`, `replacement_runtime` and
  `replacement_migration`, with names beginning `HIREWIZ_CUTOVER_ROLE_`.
  `database_operator_role_env` lists any separately reviewed database operator
  aliases beginning `HIREWIZ_CUTOVER_OPERATOR_`; these are distinct from application
  roles. Do not label a legacy application role as an operator to suppress it.

Supply role names and the **direct** PostgreSQL URL only in the process environment
through existing secret handling. `HIREWIZ_CUTOVER_DATABASE_URL` is separate from
application configuration. The tool never imports app settings, reads `.env`, logs
connection errors or prints role names, URL values, session queries or candidate IDs.
Do not place credentials in command arguments, shell history or review artifacts.
Disable SQL/debug logging in any surrounding runner. Store output under a private
`0700` directory with `umask 077`.

From `backend` with the frozen environment already loaded:

```sh
.venv/bin/python scripts/monetary_cutover_preflight.py /private/reviewed-inventory.json \
  --inventory-sha256 <reviewed-exact-sha256>
```

Exit 0 means the **point-in-time database subgate** passed. Exit 65 means a required
observation/input is unavailable or a database blocker was found. Both outputs always
contain `cutover_ready: false`. The result must never be consumed as a shell condition
for migration or promotion. No source/CI/image/cloud metadata is verified by this
command; release pins are explicitly labeled unverified caller input.

## Observations and limits

A fresh, unpooled SQL connection starts one repeatable-read **read-only transaction**.
Connect timeout is five seconds; each SQL statement is bounded to one second and lock
waits to one second. The inventory is capped at 128 KiB, 128 consumers, 16 operator
aliases, and 256 database roles/relations/sessions/liabilities and SECURITY DEFINER
routines. The routine inventory spans **all namespaces in the current database**.
Each detached quote is bounded to 8 KiB before transmission. Crossing a bound refuses rather than
truncating evidence. An operator must review a future paginated implementation before
larger installations use it. The observer requires visibility equivalent to
`pg_read_all_stats` and access to `pg_control_system()`; privileges are not granted by
the tool. Use a separately approved SELECT/statistics-only observation identity.
A writable, membership-administering or SECURITY DEFINER-executing observer fails
even if its role was explicitly listed as an operator. The tool grants no privileges.

The sanitized `observed_database` records:

- Cluster/database/schema pin matches and presence of required monetary/application
  tables. A 0009 marker with a liability table, or 0010/0011 without one, is refused.
  Revision 0011 additionally requires both physical candidate projection tables and
  their exact nonnullable column names/types. A 0009/0010 marker cannot hide either
  candidate table. Only catalog metadata is read: no candidate row, email, password
  hash or native identity is loaded. This narrow shape check does not certify
  ownership triggers, lifecycle activation, native authority or migration permission.
- Actual retired-role LOGIN, elevated/direct/member capabilities, effective table and column
  writes, all currently reachable `SET ROLE` paths, schema CREATE and **existing sessions**. Any reachable `ADMIN OPTION` membership is conservatively treated as a capability to enable writes, even with INHERIT and SET initially disabled. NOLOGIN alone never passes an old
  active-session check. Scan all LOGIN roles for effective table/schema writers;
  unreviewed writers or sessions block. Reviewed operator aliases remain explicitly
  caller-classified, and their inclusion does not prove their external consumers.
- Every reachable executable SECURITY DEFINER function/procedure counts as
  unresolved writer authority for the observer, retired role and unclassified LOGIN
  roles. EXECUTE through PUBLIC, inherited effective privileges and direct/transitive
  SET ROLE paths is included. A routine outside the application namespace can write
  its tables, so schema placement is never an exclusion. Known owner table/column,
  schema, elevation and membership-administration capabilities are counted separately;
  their absence **never** certifies harmless effects. The tool reads only catalog
  authority metadata, not routine source, names, parameters or bodies. It accepts
  bounded trusted SQL/PLpgSQL function/procedure metadata; missing owners/namespaces,
  inconsistent counts, unsupported metadata/languages and overbound inventories refuse.
  Even an apparently harmless SELECT-only definer with a nonwriter owner is rejected
  if executable. There is no body-parsing, name, volatility or caller-asserted safety
  exception. A genuine SELECT/statistics observer can pass when no definer routine
  is executable by it, including when a separately owned, unexecutable routine exists.
- Nonterminal analysis work, unknown terminal-run financial states, attempted/unclassified model-call and financial states, uncertain
  application rows/attempts, unbalanced/open service-credit holds and prepared
  transactions. Unknown future states are retained as blockers.
- On 0010 and 0011, detached liabilities are counted and their reserved/settled monetary values
  reported without touching them. Reserved, unknown, unavailable, overrun, missing
  usage/settlement, unknown provider/provenance, invalid frozen quotes and inconsistent amounts remain blockers. A settled label needs complete nonnegative returned tokens, a closed known-provider provenance, the exact bounded quote fields and reserve/settlement arithmetic matching that quote. Reported monetary totals are estimates, never invoice proof. Even a settled row stays
  in the ledger; this tool never downgrades or deletes financial evidence.
  Detached financial groups must have a complete consecutive attempt sequence.
  For each ordered admission, the earlier unsettled reservations (or strictly
  earlier recorded settlement amounts) plus its new reservation must fit **that
  admission's recorded ceiling**. Cumulative settled prefixes also must fit that
  ceiling. Later cap increases or decreases are evaluated at their own admission;
  a blanket minimum across all historical caps would reject legitimate changes.
  Equal settlement/admission timestamps do not prove the earlier hold was released.
  Missing attempts, reversed admission timestamps, invalid identities/quotes and
  unprovable sequences block the entire group. Full conservative holds and recorded
  individually valid settlement totals remain visible separately; no row is changed.
- On 0009 the independent liability table is absent. Its counts and sums are `null`,
  **not zero**. Legacy model telemetry is reported as historical count, never proof
  of actual provider costs or absence of erased/unrecorded obligations.

A snapshot cannot stop reconnects, revoke provider keys, pause dispatch, drain traffic,
verify old retained revisions, prove invoice reconciliation, validate an immutable
backup/restore drill or establish completeness across other projects/devices. It also
does not certify arbitrary invoker-routine or external-system authority. The definer
inventory conservatively blocks execution instead of proving absence of side effects
through static body analysis. SQL statements
cannot turn an operator's provider/queue/backup assertions into independent proof.
Consequently the **global cutover-ready gate always remains false**. Preserve the
report beside the separately verified CI/image/cloud/credential/backup evidence and
rerun after actual fencing and immediately before the reviewed cutover operation.
Do not clear a blocker by resetting attempts, deleting holds or marking unknown sends
successful. Reconcile through the existing documented provider/accounting procedure.

## Checklist

- [ ] Resolve the pending shared-credential consumer question and independently finish
  retained-revision, queue, job, external-app and local-tool inventories.
- [ ] Freeze exact CI/release/image/rollback/backup evidence and the reviewed JSON hash.
- [ ] Prepare separate replacement runtime/migration identities and approved read access.
- [ ] Independently review whole-database routine/EXECUTE authority, including PUBLIC,
  inherited/SET paths, owner capabilities and unsupported languages. Resolve any
  executable observer/retired/unclassified definer authority through the separately
  reviewed fencing process; this command changes no routine, grant or role.
- [ ] Perform separately reviewed old-writer/provider fencing; inspect ongoing sessions.
- [ ] Run this tool, retain sanitized evidence and reconcile every blocker.
- [ ] Independently review cloud/provider/queue/consumer/backup evidence. A passing
  database subgate alone authorizes no action.
- [ ] Execute the separate reviewed 0010/0011 migration/immutable rollout and rerun the
  post-migration SQL observation, preserving liabilities and unknown outcomes.


## Repair history

The first frozen local version passed its 23 author cases but independent review
reproduced false-green SQL subgates for SET ROLE, column-only grants, ADMIN OPTION,
missing/negative settled usage and unknown cost states. It also exposed a writable
observer flag that was not enforced. That V1 remains held and its patch, snapshots,
failed independent probes and author results are preserved. This V2 adds actual
capability writes and complete-usage positive/negative regressions; positive observations
use a genuine read-only SQL principal. Neither version makes global cutover readiness
true or claims any production fencing/deployment evidence.


V2 repaired the writer/observer/provenance defects and passed 44 author cases.
Independent review then retained V2 on HOLD for an unavailable call carrying an orphan
monetary reservation and a detached group whose individually valid attempts exceeded
its shared authorization. V3 adds complete legacy-shape checks and ordered per-group
admission/cumulative accounting, with positive changing-cap and exact-boundary cases.
V1/V2 source hashes, failed independent evidence and intermediate results stay preserved.
All release/cloud/invoice assertions remain outside this tool's authority.

V3's 71 author/43 independent cases passed their financial scope, then a supplemental
actual PostgreSQL probe demonstrated a SELECT/statistics observer committing a
synthetic credit update through EXECUTE on an administrator-owned SECURITY DEFINER
function. The explicit READ ONLY observation transaction correctly rejected the same
write, but the identity remained write-capable in a later transaction. Both the prior
scoped CLEAR and this subsequent HOLD remain preserved. V4 adds whole-database
definer authority inventory, actual function/procedure/PUBLIC/inherited/SET writes,
and missing/unsupported/overbound inventory refusals. No routine body is used to
certify absence of writes; known nonwriter owners still receive conservative treatment.
