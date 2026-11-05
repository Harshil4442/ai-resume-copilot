# Updating the existing schema18 deployment

The first preserved-data cutover and subsequent application releases have
different responsibilities. `fresh_cutover_docker.py` performs the reviewed
one-time transfer and credential isolation. After that succeeds, use
`rolling_native.py` for immutable updates to the existing API, analysis worker
and employer worker. Never repeat the fresh cutover to release ordinary code.

## Capture and prerequisites

Capture requires an exact clean Git commit, five successful CI jobs and native
Git-source application Build/digest. It reads the actual target database at
`20261015_0018`, current runtime UID, numeric runtime/observer secrets, disabled
migrator secret, private document worker contracts and existing service state.
It preserves the current financial row digest and checks financial invariants.
No credential values or raw service environments are saved in the plan.

The original cutover still transfers the preserved source at schema9, then
applies each additive migration through schema18 before retiring the migration
login. Schema18 adds global employer-host pacing state. Runtime access is
SELECT/INSERT/UPDATE; the table is outside candidate erasure and receives no
DELETE grant. A routine rolling release requires the completed schema18 target;
it never runs a schema17-to18 migration or enables a retired migration credential.

Use the existing normal host SDK session with file and HTTP logging disabled.
Run `backend/.venv/bin/python -I -B infra/gcp/rolling_native.py --help` for the
supported arguments. Capture produces an owner-only plan; subsequent operations
require its exact SHA-256 and a separate private journal directory. All values
must come from the completed native cutover. A template with invented values is
not a release plan.

## Renewing the candidate expense and model policy pair

An ordinary release may need current reviewed policies while the preserved prior
revision still references an expired policy. Do not patch the live service merely
to make capture pass. Both native capture CLIs accept the optional pair
`--candidate-expense-secret NAME:NUMERIC_VERSION` and
`--candidate-model-policy-secret NAME:NUMERIC_VERSION`. Supply both or neither;
`latest`, a changed model credential, primary model or API base are refused. For
the schema19 operator sequence, supply this pair on both the schema18 migration
capture and the fresh schema19 rolling capture, using actual currently accepted
versions. Policy publication is separate from capture.

Capture preserves the independently observed old policy references, payload
hashes and immutable prior templates. It separately verifies the candidate pair's
numeric versions, exact payload hashes, current expiry, funded prices/budgets and
all models in the candidate source before accessing SQL or emitting a plan. It
pins the exact target template hashes. Staging changes only the two policy
references alongside the normal immutable application release; model keys,
database references and unrelated configuration retain their captured values.

Every subsequent gate revalidates the candidate policies and accepts only each
service's exact captured prior template or exact staged target template, using
the corresponding numeric secret references. The old immutable revision remains
bound to the original references and is never rewritten. An expired prior policy
is retained solely with every cost-bearing admission closed. Traffic rollback
does not renew that policy or reopen work. If the candidate policy expires during
the operation, stop and reconcile the retained operation rather than bypassing
expiry or editing its plan. Missing acknowledgements retain the same one-send
journal and cannot authorize another PATCH. Default capture without this explicit
pair retains the existing same-policy behavior.

## Update and recovery

`advance` stages each existing service at zero traffic, keeping its old revision
and configuration. Current secret selectors resolve to observed numeric
versions. Each PATCH binds a fresh native preimage and etag and records an
exclusive durable marker before transmission. The transport refuses a second
physical PATCH. Accepted operations retain their exact native LRO handles.

All three staged revisions need native configuration, identity, health, schema
and financial checks before traffic promotion. Workers promote before the API.
A pending LRO remains a wait on that same handle. A lost acknowledgement uses
`reconcile`, which only observes existing actions and cannot start another
mutation. Never remove a marker to retry an uncertain request.

Explicit `rollback` restores checked old traffic, retaining both revisions and
the schema. It does not restore a database backup, reset financial state, revive
a credential or reopen queues. A changed payment or ledger digest stops the
sequence for reconciliation and must not be overwritten.

## Acceptance still required

- [ ] Complete the original preserved-data cutover and capture actual values.
- [ ] Demonstrate native staging, etag/LRO readback and actual tagged health.
- [ ] Verify restart, unknown acknowledgement and partial-promotion recovery.
- [ ] Demonstrate traffic rollback with the retained configuration and secrets.
- [ ] Complete feature activation, Vercel promotion and production monitoring.

Portable tests exercise ordering and failure handling; they do not prove native
deployment. Financial checks retain existing finite bounds. The additional row
digest is bounded to 250,000 rows and 30 seconds, with bounded individual SQL
statements. Larger histories need measured incremental reconciliation before
these limits become an availability constraint. A successful rolling release
reports infrastructure completion only; the full goal remains open until the
user-facing requirements are verified.

## Schema26: policy expiry after zero-traffic staging

`backend/scripts/schema26_app_successor.py` provides a separate restore-only
recovery contract for this specific state. Its original plan and journal remain
immutable. Recovery requires all three original stage intents and acknowledgements,
all three exact native operations completed successfully, no promotion or rollback
intent, and exact current staged templates, generations and traffic. An original
stage may lack its checked marker: recovery observes the real acknowledged operation
and never writes that missing marker into the old journal.

Use a separately qualified clean controller, target and prior source checkout.
`recovery-capture` requires the original plan file and exact SHA, its original
private journal, a fresh nonce and the controller's genuine CI run. It saves a new
private recovery plan. `recovery-restore` and `recovery-reconcile` require that new
plan and SHA, a separate private journal, and the same original plan file/SHA and
journal. Both journals are exclusively locked; the original lock is opened
read-only and must already exist. Recovery cannot be used as an ordinary release
plan or authorize forward promotion.

Each restore binds a fresh provider preimage and etag and saves one durable intent
before its only PATCH. It restores the exact immutable prior revision template and
original pinned traffic. Thus the original aliases and 100% prior allocation remain;
only the failed plan's owned zero-traffic staging alias is removed. Both old and
staged revisions remain. An acknowledged pending operation is observed on its
original handle; a missing acknowledgement permits reconciliation only, never
another send. Prior HTTP health is required before a checked marker or the next
restore. Source, schema26, SQL financial state, protected liabilities, resources,
numeric secrets, runtime UID, static service configuration and IAM are rechecked.

An expired policy is parsed, approval/hash checked and structurally audited only
as historical rollback configuration while every cost-bearing admission remains
closed. That is not current funding authority. After recovery is fully checked,
capture a fresh ordinary successor with the independently reviewed numeric pair
`--candidate-expense-secret NAME:VERSION` and
`--candidate-model-policy-secret NAME:VERSION`. Both are required together. Current
expiry, source model coverage, funded ceilings and catalog arithmetic must pass
before SQL capture and every forward gate. Model credentials, primary model and
API base cannot change through this pair. The old policy remains an immutable
rollback reference; its expiry is never extended in place.

Owned tests verify these failure and ownership boundaries. They do not prove
native restoration, a successful production renewal, document quality, capacity,
invoice reconciliation, or permission to enable payments.

Forward capture additionally requires at least four hours remaining on the exact
target expense policy and every primary/source-fallback model price quote. This
capture-only buffer applies to both same-policy and candidate-pair releases after
normal current funding verification and before SQL capture. It does not extend
any expiry or guarantee rollout duration; later gates still enforce current
expiry. Restore-only historical recovery does not use this forward buffer.
