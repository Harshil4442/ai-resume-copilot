# Protected publication boundary: disabled bounded source stage

Status: implemented locally for independent review. No production initializer, factory,
route, credentials, IAM changes, reopen operation, signing, application submission or
traffic activation is supplied. The held first denial patch and its two failing race
probes remain preserved. This incremental stage addresses their publication gap.

## Problem and concrete result

The earlier close object correctly denied old readers after a retained account/session
denial even when the ordinary native Commit was unknown and persisted nothing. Its
inventory implementation could still omit an authorized writer's intent after the
last list. The independent probe published an owning denial during the final manifest
GET. A separate author probe paused an ordinary writer at SDK POST, completed close
and inventory acknowledgement, then allowed that POST to retain an omitted intent.
Additional lists or a finite waiting period cannot exclude either late publication.

Every generic, pairing and password-lifetime intent upload now first retains its whole
canonical intent in a separately pinned transactional publication authority. Admission
and sealing read and update the same protected native gate. Seal captures a fixed
complete set of retained record identities, then exports and verifies every full
record before acknowledging a complete journal-prefix inventory. An already admitted
POST has a name and complete bytes in that captured set; an unadmitted writer cannot
start its POST after the gate seals. This is journal publication exclusion, not a
claim that every execution projection or external action has quiesced.

## Exact resources and trust boundary

`PublicationPin` binds two complete `RegistryPin` values: independent publication
database resource/immutable UID/authority ID/incarnation/epoch/protocol and ordinary
execution authority pin. The database names and immutable UIDs must differ. The same
pin binds the journal bucket. An operator-supplied exact-generation canonical GCS
resource object at `authority-publication-resources/{target-pin-digest}.json` retains
this full binding. Its generation, digest and exact bytes are checked with fresh
independent database identity outside each protected transaction and after ACK.

Runtime never creates this resource, initializes its OPEN gate, switches an epoch,
adopts a foreign database or supplies ambient credentials. The fixture alone seeds an
OPEN gate through the actual emulator. Its operator UID is synthetic; actual production
resource identity and protection remain deployment gates.

The publication authority must be preserved independently of ordinary SQL and native
projection restores. The code detects missing records, changed UID, witness generation,
corrupt state and observed rollback while verifying a sealed cut. It cannot prove that
a privileged same-UID import erased an acknowledged row **before** sealing while an
old GCS POST remained paused. Independent IAM/import/delete/restore restrictions and
preserved history must make that schedule impossible before activation. A same-UID
rollback is not automatically detected merely because a resource witness exists.

## Protocol invariants and ordering

1. A publication record contains the complete canonical typed intent, exact target
   pin, kind, partition, operation UUID, derived GCS name, length and digest. The full
   stored envelope is bounded to 64 KiB. A digest-only reservation or lease is absent.
2. One pure native transaction reads the admission gate and record absence, then
   atomically retains the full immutable record and updates the gate's complete ID
   cut. All successful uploads require this writer's own complete publication ACK.
3. OPEN admits all three supported intent kinds. DENY_ONLY admits only the exact
   owning password denial whose command fingerprint and before-image bind the
   acknowledged close receipt and native CLOSED control. SEALED admits nothing.
4. Protected closure obtains its own GCS generation-zero creation ACK first. The
   independent gate then changes OPEN to DENY_ONLY with its own native ACK; ordinary
   execution control then changes to CLOSED with its own ACK. Any uncertain step
   withholds the closed writer context. The retained GCS closure continues to block
   old readers even if later native Commit is unknown or unpersisted.
5. Seal changes DENY_ONLY to SEALED in the same native boundary as full-record
   admission. A writer either commits before that seal and belongs to its fixed cut,
   or conflicts/retries against the sealed gate and cannot publish afterward.
   Firestore documents serializable native transaction isolation by commit time.
   [Firestore transaction isolation](https://docs.cloud.google.com/firestore/native/docs/transaction-data-contention).
6. A complete snapshot reads the exact SEALED gate and every captured full record.
   Recomputed canonical references must match the gate. There is no sampled query,
   truncated listing or assumption that an absent unknown record is empty.
7. The sealer exports all captured records synchronously, including an orphan that
   persisted after lost publication ACK. Each name is generation-zero and exact
   current-generation bytes are verified. Inventory export never adopts execution
   ownership. A delayed original POST can only compete on that already captured name.
8. Every GCS authority-prefix entry must correspond to a captured record, and every
   captured record must be present. Unregistered legacy or foreign objects deny a
   complete cut. The full sealed gate is separately retained in GCS; the manifest
   binds its exact receipt, publication resource and ordered entry digest.
9. Fresh native/pin/control/head/full-record and GCS checks surround manifest ACK.
   The actual exclusion proof is the durable SEALED transaction. Repeated lists are
   supplemental verification. An owning writer attempting publication during the
   final manifest GET fails before its journal POST.
10. Unknown protected publication Commit causes no ordinary GCS POST. Unknown seal
    Commit yields no complete-cut receipt. No read of an existing row substitutes
    for the original pairing/lifetime publication ACK. An exact generic safety replay
    retains its established idempotent status/effect semantics and cannot create an
    action ownership receipt. Changed bytes under the same UUID are unavailable.
11. No Storage or identity call executes inside publication, execution or user SQL
    transactions. Native definite-abort retries retain fixed canonical bytes, are
    limited to three attempts and a finite deadline; ambiguous outcomes are never
    retried into permission. SDK calls disable retry and use finite RPC budgets.
12. Lifetime V2 private immutable canonical plan bytes, fixed before/after/event cut,
    detached IO copies and historical COMMITTED status-only receipts are retained.
    SEALED permits exact historical status verification; it grants no fresh writer,
    reader permission, signature, envelope or reopen authority.

## All writer and reader seams

| Port or caller | Enforced integration |
| --- | --- |
| `GcsJournal.write` | Mandatory full publication before the generic authority-intent POST; missing configuration fails closed. |
| `GcsPairingAttempts.write_intent` | Same resource/gate; retained row or ambiguous ACK cannot be adopted by another replica. |
| `GcsPasswordLifetimeJournal.create` | Same resource/gate and full immutable plan; unavailable publication returns no journal owner. |
| `GcpPasswordCloseCoordinator.close` | Own GCS close ACK, own publication DENY_ONLY ACK, own native CLOSED ACK, then exact closed writer context. |
| `ClosedPasswordDenialFence` | Writer checks DENY_ONLY, status checks DENY_ONLY or SEALED; both verify exact retained closure/identity/witness. |
| Ordinary witness readers/admissions | Existing protected-closure absence checks remain mandatory; injected old OPEN witness cannot bypass a retained close. |
| `GcsClosedInventory.seal` | Own atomic SEALED ACK, exhaustive complete records, synchronous exact GCS export, full retained seal object and bound manifest. |
| Pairing markers, manifest/seal export | These are separately bound consumption/status evidence, not an unregistered authoritative intent-upload fallback. |
| Product factories/transports | Remain unavailable; no protected client/resource wiring or production enablement supplied. |

All authoritative intent writers must migrate together. An older process with direct
unregistered GCS intent-write permission invalidates a global completeness guarantee;
runtime writer rollout and IAM must exclude it. No legacy fallback is supported.

## Bounded scope and availability tradeoffs

The disabled gate carries at most **63 records per exact ordinary authority
incarnation**, allowing gate plus every record within the current 64-document read
budget. The inventory has a separate 64-object limit; publication's 63 bound is
stricter. Overflow returns unavailable, never partial completeness. Gate/record bytes,
SDK reads, native retries and inventory admission deadline are bounded. This control
document changes on each admission and will contend; no production throughput or
scalable availability claim is made.

Closure is permanent for the entire pinned incarnation and affects unrelated
candidates sharing it. It remains an emergency recovery proof stage. Normal logout
must use a separately reviewed per-subject barrier or verified complete-cut restore
and reopening protocol. GCS POST or publication ACK loss may strand a logical operation
even when ordinary native execution never occurred; another replica is deliberately
denied adoption. Availability/reconciliation is required separately.

A GCS mutable global CAS replacement was rejected as a launch design: Google documents
a same-object write rate limit of one write per second. Distributed generation-zero
intent names avoid that storage hot name; the current native cut gate still has its
own contention limit. [Cloud Storage quotas](https://docs.cloud.google.com/storage/quotas).

The later scale design must freeze partition registration, retain the exact protected
directory, seal every registered per-subject/partition admission boundary and verify
all full partition cuts before global acknowledgement. A read-mostly emergency root
gate may participate in each native transaction without a per-event global write.
Registration cannot enter a new unseen partition after directory close. Missing,
unknown, unsealed or truncated partitions deny a global complete cut. This partitioned
implementation and its capacity tests are not supplied by the 63-record prototype.

## Evidence and preserved failures

The baseline is exact `aab73dae8d3311debc6d9dba2abe4362238ab18b` plus the held denial
patch `d83ff958e4cdcd28b1a3381ea15ce18c3daab09a0f5cb42d8cd1753c8dde4616`.
That prerequisite includes exact reviewed lifetime V2. Its 665-pass run does not
establish the previously missing final-ACK cut property. The independent final-ACK
failure and the author paused-POST failure are preserved unchanged alongside it.

Actual emulator tests cover all three ordinary POST schedules, owning-denial
publication during both creation verification and final ACK, persisted/unpersisted
unknown publication Commit, missing witness/gate, changed generation/UID, missing full
record, observed gate rollback, export/seal retention failure, unregistered canonical
objects, absent configuration and actual concurrent publication-versus-seal native
transactions. Existing connected closure/lifetime/pairing/credential tests continue
to use their real coordinators/readers; no fake reader or authority bypass is inserted.

The fixture uses the pinned SDK with intercepted HTTP and synthetic operator UID;
native transactions run on an independently owned loopback emulator on port 58880.
Tests explicitly select `HIREWIZ_AUTHORITY_EMULATOR_PORT=58880` and
`HIREWIZ_DENIAL_TEST_EMULATOR_ENDPOINT=127.0.0.1:58880`. The container has JVM heap 1536 MiB
and memory 2 GiB. No shared database was reset and no production endpoint was contacted.
The frozen manifest records exact source/report/image hashes and observed counts.

Earlier checkpoints are retained, including failures due to the changed 63-record
bound, missing scoped SDK metadata suppression, updated duplicate-owner semantics
and obsolete two-POST pairing concurrency schedule. The latter now races actual
publication Commit calls; only the winning replica proceeds to GCS POST and native
pairing consumption. The failed forced-commit-order diagnostic was replaced with
genuine concurrent native contention, without assuming a particular lock winner.

## Gates still required before browser-assistance launch

- [ ] Independent review clears the exact frozen incremental source and both original
  race schedules; local test success is not that clearance.
- [ ] Independently provision and verify publication resource UID/genesis pins, IAM,
  nonrestore/import/delete restrictions, retained complete history and old-writer
  exclusion. Same-UID rollback before seal must be impossible or independently caught.
- [ ] Verify GCS retention/lock/lifecycle/version and bucket deletion controls and
  actual permissions; versioning alone permits noncurrent deletion and does not
  protect bucket deletion. [Object Versioning](https://docs.cloud.google.com/storage/docs/object-versioning).
- [ ] Build and independently verify full projection restore from the complete fixed
  journal cut, deny/ownership/event continuity and conservative pending unknown effects.
  The manifest still says `projection_complete=false` and grants no permission.
- [ ] Build per-subject normal denial availability or verified complete-cut reopen;
  permanent whole-cohort closure cannot serve normal candidate logout.
- [ ] Replace the 63-record hot gate with proven partition directory/close/seal cuts,
  complete cardinality evidence, capacity tests and bounded recovery pagination.
- [ ] Complete consuming action authority, actual protected resource/signing transports,
  exact package/form approval and full browser executor controls separately.
- [ ] Verify Linux CI on the exact reviewed source and staged release; no local evidence
  enables cloud factories or marks production deployment complete.
