# Updating the existing schema17 deployment

The first preserved-data cutover and subsequent application releases have
different responsibilities. `fresh_cutover_docker.py` performs the reviewed
one-time transfer and credential isolation. After that succeeds, use
`rolling_native.py` for immutable updates to the existing API, analysis worker
and employer worker. Never repeat the fresh cutover to release ordinary code.

## Capture and prerequisites

Capture requires an exact clean Git commit, five successful CI jobs and native
Git-source application Build/digest. It reads the actual target database at
`20261014_0017`, current runtime UID, numeric runtime/observer secrets, disabled
migrator secret, private document worker contracts and existing service state.
It preserves the current financial row digest and checks financial invariants.
No credential values or raw service environments are saved in the plan.

Use the existing normal host SDK session with file and HTTP logging disabled.
Run `backend/.venv/bin/python -I -B infra/gcp/rolling_native.py --help` for the
supported arguments. Capture produces an owner-only plan; subsequent operations
require its exact SHA-256 and a separate private journal directory. All values
must come from the completed native cutover. A template with invented values is
not a release plan.

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
