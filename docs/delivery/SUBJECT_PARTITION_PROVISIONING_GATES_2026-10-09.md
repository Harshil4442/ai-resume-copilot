# V3 constructor and provisioning requirements — production HOLD

This is an exact disabled-source integration checklist, not a cloud runbook or permission
grant. No initializer, ADC lookup, production client factory, reopen API, real KMS action or
employer transport is supplied. Author Native/Storage/SQL evidence uses anonymous local
SDK clients, synthetic operator metadata and owned disposable database/schema names.

## Resources and exact construction

1. A protected Firestore **Native** database independently pinned by full name, fresh
   immutable database UID, authority ID, incarnation and epoch. It must differ from the
   ordinary execution database by name AND UID. A new pin cannot be selected by a browser,
   candidate, operation retry or SQL row. The actual `FirestoreRpc` must target this name.
2. An ordinary execution `RegistryPin` with the exact unchanged lifetime V2/native plans.
   Do not retarget those plans to the protected database or invent a restored UID. A future
   execution V3 has a separate reviewed migration and cut protocol.
3. A dedicated exact Storage SDK 3.13.0 client and protected bucket. The existing
   `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true` denial gate is required so SDK
   background metadata work cannot bypass finite IO or enter transactions. No cached bucket
   metadata, implicit credentials or normal unpinned client is an authority fallback.
4. `PartitionedPin(protocol_version=3, authority=PublicationPin(registry=protected_pin,
   target=execution_pin, journal_bucket=bucket), lanes=64, directory_shards=8,
   segment_size=128)` is the default design configuration. Constructor alternatives are
   explicitly bounded powers of two; operator pins remain fixed for the incarnation.
   Native publication lanes derive from SHA256 of the strict server operation UUID. No
   caller-supplied lane, quota bucket, directory index or reset is accepted.
5. An immutable canonical whole-configuration resource object at
   `authority-publication-v3-resources/{SHA256(canonical execution pin)}.json`. Its exact
   generation, bucket and whole-body SHA256 form `PartitionedResource`. Every fresh check
   verifies actual Admin database name/UID/Native type plus exact generation/object bytes.
6. Operator-reviewed initial native root `v3_root/current = {pin, phase:OPEN, close_id:null}`,
   all configured zero-count lane heads and directory heads. Runtime source has no method
   that creates these resources or reopens a missing/closed authority. Native scan refuses
   unsupported rows instead of ignoring residue from an old protocol. Initialization must
   census its entire native collection and existing canonical GCS prefix before opening.
7. Inject `PartitionedPublicationCoordinator(actual_registry, exact_resource, bucket)` into
   **all three** `GcsJournal`, `GcsPairingAttempts` and `GcsPasswordLifetimeJournal` ports.
   Missing/unrecognized configuration refuses publication; no unregistered fallback. Do not
   activate by wiring only the newest writer while old workers/direct SDK paths remain.
8. `ScopedCandidateAuthority(publication, now_ms=trusted_clock)` owns current protected
   enrollment/session/pending/tombstone/credential history and component BEGIN. Construct
   `GcpProtectedCandidateLifetimes(scoped)` and the exact native lifetime issuer, then
   `NativeCandidateLifetimeRetention(issuer, protected_wrapper)`. Activation consumes only
   the issuer's original opaque own ACK, outside the protected transaction, and derives
   every scope field from full canonical intent/effects. SQL ACTIVE/cookie/session output
   requires its own exact protected ACK and current matching credential revision.
9. The optional `GcpPasswordCandidateBoundary(native_pairing, scoped=scoped)` uses the
   actual Native reader and rejects pending/denied scope. Its V3 signing/consuming methods
   remain unavailable until the complete challenge/approval/key/disclosure transaction is
   implemented; this source wrapper cannot authorize the old consuming path.
10. `PartitionedCutExporter(publication, deadline_seconds<=60)` requires an original owned
    emergency close. It returns complete inventory only after native registration/full-row
    census, all segment seals, full unchanged intent + native-row exports, complete GCS
    prefix pagination, chained chunk manifests and exact final acknowledgement. It neither
    reopens nor restores nor reconstructs session/action ownership after restart.

## Normal and emergency semantics

Normal logout first registers the full canonical command and a session-specific pending
fence, then verifies that exact command while applying its tombstone. Normal reset uses a
subject/OLD-generation pending fence; deletion also retains permanent subject/account
tombstones. BEGIN consults those exact resources within the SAME protected native
transaction. Other sessions/candidates stay usable. Unknown final effect leaves pending
denial blocking old scope; unknown INITIAL registration with no persistence has no retained
fence and no success acknowledgement. No GCS POST is emitted from unknown publication.

Emergency close writes an immutable protected close marker first at
`authority-publication-v3-closures/{SHA256(canonical whole V3 pin)}.json`, generation-match
zero, requiring original definite create ACK. Then native root OPEN→CLOSED must have its
own definite ACK and conflict atomically with registrations/publications that read OPEN.
Any retained/uncertain marker denies new authorizing returns even if native close did not
persist. Existing or unknown close cannot be adopted into a complete-cut owner. Recovery
of that state remains a separate status-only/full-restore gate; no availability claim.

## Custody and threat boundary

Resource UID plus resource-object bytes are identity pins, **not rollback witnesses**.
The concrete earlier independent negative probe restored only a publication gate under
the SAME UID while its full row survived. V3 exhaustively queries the actual flat native
collection, checks every registration/slot/cardinality/hash and refuses that mismatch.
Its credential reader refuses a regressed current generation when later immutable history
survives. It does not prove privileged deletion/import/overwrite of both current and full
history impossible. Full-state destructive restore under the same UID remains excluded
unless independently prevented or detected by a separately retained monotonic checkpoint.

Before opening a production pin, independently prove all of the following with actual
cloud policies and adversarial tests, never source comments or fixture metadata:

- Runtime publication/read identities can use only the intended protected database and
  bucket prefixes. No authority-resource provisioning, database restore/import/clone/delete,
  bucket deletion, object/history deletion, lifecycle/retention weakening, key destruction
  or IAM editing. Ordinary application/migration identities cannot access protected history.
- Firestore native CRUD permission alone does not enforce application immutable-row
  contracts or exactly one writer implementation. A trusted mediated writer boundary,
  immutable history custody and administrative restore exclusion must be reviewed. Do not
  claim that application `immutable=True` is an IAM/WORM policy or that a broad updater
  cannot overwrite authority rows. An independent monotonic retention protocol is needed
  if those overwrite/restore powers remain in the declared threat model.
- Protected intent/close/cut/resource objects are create-only with exact retained bytes,
  retention-policy lock and actual lifecycle/noncurrent-generation/deletion controls.
  Bucket versioning by itself permits destruction of needed history and bucket deletion.
  Retention duration, legal/privacy deletion and minimal evidence/key custody need a
  reviewed policy; this stage asserts no indefinite WORM guarantee.
- Old Native/GCS writers, deployments, workload identities, data paths and employer egress
  are physically excluded before V3 OPEN. A flag or new constructor does not revoke old
  credentials. Unsupported/unregistered canonical GCS objects must prevent completeness.
- Operator/provisioning/replay identities are separate from normal runtime. Fresh resource
  pins are approved after full census and old-writer exclusion. Replay cannot create a
  candidate grant from status, public input hashes, SQL projections or a lost ACK.
- Genuine KMS keys/issuer audience, candidate credentials, monotonic clocks, capacity and
  secret-free audit/incident handling are reviewed before any action transport is wired.

## Explicit release blockers

- Independently review this exact freeze and combined lifecycle/transport sources.
- Replace lifetime V2's global event head with genuine versioned execution cuts; preserve
  every frozen existing plan rather than retargeting it.
- Add trusted authenticated pairing scope publication and all challenge/approval/key/
  opening/disclosure guards in the SAME protected consuming authority transaction.
- Implement exact native credential generation+digest projection consuming the original
  scoped denial fence. The wrapper's reset method currently reports unavailable and leaves
  SQL/native credentials untouched; a protected fence alone is not reset success.
- Prove full independent restore/replay/reopen, cold restart ownership behavior, protected
  ACK-map bounds, resumable large-history census/export, load/capacity and recovery time.
- Complete actual cloud IAM/retention/old-writer exclusion, Linux CI, cold browser→BFF→
  native flows, staged deployment and reviewed promotion. None are completed by the local
  emulator tests, this document or resource identity pins.

GCP remains the selected platform. Sharded native updates avoid the prior global 63-entry
hot gate and a mutable global GCS CAS object; Cloud Storage documents one write/second per
object name. Source guarantees are bounded and fail closed. No scalable production
availability or full-goal completion is claimed from these tests.
