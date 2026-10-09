# Version 3: normal revocation and partitioned protected publication

Status: authored disabled source; independent review, integration and deployment pending. Existing V1/lifetime V2 and
the independently cleared 63-record publication foundation remain frozen. This introduces
a separate versioned authority protocol; no source or policy presently enables production.
It maps FR09, EX05–08, SE05–06, DP05 and G04/G05/G06 recovery acceptance requirements.

## Normal candidate controls

An independently pinned protected Firestore authority owns current subject/account/session
denial and consuming BEGIN, together with full intent publication. Normal logout does not
close the authority incarnation or a publication lane. A single-session logout atomically
registers the complete canonical denial and a scope-specific pending fence before applying
its permanent session tombstone. Other sessions
and unrelated subjects remain eligible. Reset/all-session logout advances only that
subject's protected authentication high-water; deletion retains its permanent subject and
account tombstones. A new session must have a new server ID and the current generation.
Revoked IDs, ownership, consumed challenges and old generations are never reused.

The protected pending-registration transaction is the denial linearization point. Final
tombstone/high-water application verifies that exact registered command in another protected
transaction. Readers and consuming BEGIN consult pending scope in their own transaction.
Pending generation denial is keyed by subject AND the old generation: a future verified new
generation is not permanently blocked by an old pending row. A retained pending denial
remains effective if GCS
export or ordinary native/SQL projection Commit is unknown. Missing protected authority
denies permission. A replay may verify a retained denial as status, but never recreate a
grant or adopt a lost execution ACK. Native and SQL mirrors are completed separately;
their earlier OPEN rows cannot supply current permission. Acknowledgement distinguishes
protected revocation from pending projection/erasure work. UNKNOWN/no-persist final effect
Commit is blocked by its retained pending fence. UNKNOWN/no-persist INITIAL registration
has no retained intent/fence, no GCS POST and no success ACK; it is honestly reported as an
unachieved request, not durable revocation.

Current password identity readers must consult the protected subject/account/session
resources before returning authenticated identity. This requires an actual versioned
composition, not a test replacement of reader results. Identity/status is not action
permission. Signing/action BEGIN must atomically check those SAME protected resources and
retain its own complete consumption record within that authority transaction. Two databases
with pre/post guard reads do not serialize BEGIN versus denial. If BEGIN wins first its
already-begun operation may remain uncertain; if denial wins first BEGIN must fail. A
subsequent signature or disclosure still needs the exact consuming transport, expiry and
actual executor boundary; those production gates remain separate.

## Publication lanes, segments and census

Home lane is a deterministic hash of the strict server-owned operation UUID and pinned
protocol configuration. Callers cannot choose a lane, segment, sequence, quota bucket or
new epoch. The original canonical intent bytes and original GCS path remain unchanged.
The new canonical envelope adds derived lane/segment/ordinal, exact kind, full record and
strict server-derived affected scope bindings. Per-subject routing is not the enumeration
authority: unbound identity commands, global-key ownership and future multi-subject intents
must also remain in the complete global census.

Each publication transaction reads the read-mostly emergency root, its lane/current
segment, relevant subject/session denial resources and record absence. It atomically
retains the complete immutable canonical record and contiguous segment slot, then updates
only that segment/lane's count and rolling digest. Segment rollover atomically registers
the new segment in one fixed hash-sharded directory counter/slot before the same transaction
adds its first complete record. Registration also reads the emergency root. No global
per-operation counter/head or mutable GCS CAS name is introduced.

Generic intents derive their scope from typed effects. Password lifetime scope derives
from its immutable subject, typed command and exact ownership before-images. Existing
pairing intents contain only an input hash and cannot independently prove an authenticated
subject; a trusted versioned issuer must retain its resolved scope descriptor. A missing
descriptor must fail closed for candidate-bound authority rather than accepting a caller
declaration. Public unbound pairing preparation may remain explicitly identity-only.
Bounded multi-subject operations read every declared affected scope in one transaction;
they cannot split the operation to evade denial or appear only in one subject's census.

Emergency root CLOSE conflicts with every native publication and registration that read
OPEN. A transaction either committed before close and appears in the retained census, or
conflicts/retries and cannot publish afterward. Unknown Commit records are included when
they actually persisted; an absent ordinary projection is never a no-action certificate.
The sealer obtains a fixed closed directory cut and seals every registered segment.

Completeness uses two independent native representations: contiguous sharded registration
slots/segment publication slots with exact count/digest, and exhaustive strongly consistent
native pagination over the flat retained registration/full-record collections. The flat
census must include records even when a mutable head has been rolled back or a segment
parent/index is missing. Do not rely on a collection-group query that can hide orphaned
records behind absent parents or on an indexed-field filter supplied by the record itself.
Each page validates full document identity and canonical bytes, ordered cursor progression,
duplicates, stream completion and cardinality. A terminal page must be observed. Counts,
slot continuity, rolling hashes, registration uniqueness and all full rows must agree.
Truncation, bound exhaustion, missing rows, unexpected rows or ambiguous queries deny the
complete cut. Fixed page/RPC limits allow later resumable closed recovery checkpoints;
no partial checkpoint claims complete coverage.

Every retained canonical record and every flat native authority row is synchronously exported and generation-verified in GCS
before cut certification. An admitted POST paused until after acknowledgement competes
on an already captured canonical name. Exhaustive current GCS prefix pagination must agree
with the exact native registered names/generations. Unsupported or unregistered GCS prefix
names deny coverage. Bounded chained chunks and the final manifest carry exact cut pins,
sealed counts and hashes. Inventory certification remains status-only and cannot reopen,
issue a session, reconstruct a lost dispatch owner or permit an employer action.

## Rollback and provisioning boundary

The independent negative probe restored only an earlier mutable publication gate under
the SAME UID while leaving the full retained row intact. The V3 flat full-row census must
explicitly refuse this mismatch; resource UID is not a rollback witness. Immutable session
tombstones are checked directly, and high-water state must agree with independently retained
generation history. A surviving later generation or deny cannot be hidden by regressing a
mutable current row.

Privileged restoration or deletion of both indexes and full history can still erase the
evidence that code needs. Actual independent nonrestore/import/delete restrictions,
protected history, operator custody and retention policy must make this impossible before
activation, or a separately protected monotonic checkpoint must detect it. No local UID,
fixture, versioning toggle or source assertion proves those restrictions. Existing
independent negative probe bytes are preserved with the new source proposal.

## Execution heads and reopening

The publication global 63-entry head is removed from this versioned design. Lifetime V2
native effects still bind their original global execution head, and those bytes must not
be retargeted. A distinct execution V3 must introduce per-subject/resource event cuts and
bounded multi-resource graphs. Global immutable key ownership still serializes on its
specific key record; it is not made subject-local. Mixing old global-head plans with new
scope authority or letting old writers bypass publication is forbidden.

Normal revocation does not require reopening a closed incarnation. Emergency reopening
does: preserve the exact complete directory/segment/full-record cut, replay permanent
denies/ownership and possible consumed actions, verify the new execution projection and
fresh independent pins/epoch, exclude all old writers/egress, invalidate old approvals and
obtain fresh candidate approval. Pending grants never activate merely from publication;
pending denials conservatively block. No reopen API is part of the first V3 source stage.

## Tradeoffs and focused milestones

- Native transactional lanes spread publication contention and keep emergency root writes
  rare. Subject gates deliberately conflict on revocation, and per-lane heads still need
  load testing. The lane configuration is operator-pinned, not a user throughput knob.
- Sharded directory updates happen only on segment rollover. More/smaller segments reduce
  individual cut work but increase census/manifest cost; larger segments trade that cost
  for local contention and export latency. Initial fixture sizes are developmental bounds.
- Complete native census and GCS export are recovery work, not per-request history scans.
  The first source stage refuses explicit document/page limits; a later checkpointed
  recovery worker must finish the entire census before claiming a production RTO.
- Full protected BEGIN records provide the actual denial race boundary. Connecting them
  to KMS, candidate session minting and the browser executor remains independently reviewed
  work; source admission success does not enable signing or disclosure.
- GCP remains the platform. A GCS global mutable CAS alternative is unsuitable because
  same-object writes have a one-write-per-second limit; versioning alone also does not
  establish protected retention. No AWS, Azure or Kubernetes migration is proposed.

Milestones:

1. Add disabled versioned native pagination/full-row census and partition registration/
   publication/seal contracts, preserving all existing canonical bytes.
2. Prove all three intent kinds, unknown commits, root-close/registration races, complete
   multi-page export beyond 63 records and refusal of the same-UID gate-only rollback.
3. Add protected normal session/subject tombstones and generation high-water; prove an
   unrelated candidate and unaffected session remain operational with real native state.
4. Add typed consuming admission in the SAME authority transaction and test both actual
   BEGIN-versus-denial orders. Keep unconnected transports explicitly unavailable.
5. Independently review exact source and fault evidence, then implement the missing trusted
   pairing scope issuer, execution V3 partition cuts and actual composed reader/transports.
6. Prove restricted cloud provisioning/old-writer exclusion/retention, full restore/reopen,
   capacity and exact Linux/staged release before any assisted cohort activation.

## Current authored checkpoint and precise gaps

- [x] Actual unfiltered Native SDK census, pagination/cardinality, all registration slots,
  full records and scope provenance; same-UID mutable-index-only rollback refusal.
- [x] All three actual journal ports register before GCS POST; fixed old intent bytes stay
  unchanged. Legacy generic/pairing/lifetime intents remain in the global census regardless
  of candidate/subject projection. Registration is not a grant and does not pretend that
  an old pairing input hash proves its current subject scope.
- [x] Genuine original lifetime ACK composition; full retained activation evidence and
  exact credential revision; scoped pending/session/account/subject denial and history.
- [x] Protected component BEGIN and pending denial share the same actual native authority
  transaction. Both orderings, final-effect UNKNOWN, stale prechecks and unrelated-session
  availability are tested. Component ACKs cannot sign or disclose.
- [x] Complete GCS + native-row export, including 80 original full intents, terminal native
  and GCS pages, paused POST and late final-manifest owning denial refusal.
- [x] Exact lifecycle wrapper and actual committed SQLite/PostgreSQL credentials→native
  enrollment→protected activation/login/logout. Restored old salted hash/generation refuses
  fresh login. All SDK IO is outside SQL credential transactions.
- [ ] Independent review of this exact freeze. These are author proofs, not review/deploy.
- [ ] New execution V3 event/resource cuts; trusted pairing scope descriptor and all current
  native challenge/approval/key/disclosure guards in the protected consuming transaction.
  The scoped legacy boundary READ uses the real old reader plus protected current scope;
  its SIGN/CONSUME methods intentionally remain unavailable. Pre/post reads alone cannot
  authorize the old cross-database consuming transaction.
- [ ] Actual normal native credential-generation projection consuming the command-bound
  protected fence. The lifecycle wrapper rejects password reset before changing state;
  protected high-water alone is never treated as complete native/SQL password change.
- [ ] Independent restore/replay/reopen, physical old-writer exclusion, enforced retained
  history custody and production IAM/WORM/retention evidence; none supplied by fixtures.
- [ ] Normal production availability, bounded plan/ACK map retention, load/capacity/RTO,
  restart recovery, cold NextAuth/BFF/executor composition and staged release acceptance.

The bounded exporter defaults to 4,096 flat native documents, 32 per Native page and
256 pages; hard scanner limits are 65,536 documents/4,096 pages, with terminal evidence
required. Export permits at most 128 chunks of 64 entries and 65,536 bytes per object,
including the full native cut. Exhaustion refuses a complete receipt; no 63-record global
hot gate is retained, but this is not a production large-history recovery/RTO promise.
The 60-second export deadline is checked around bounded IO; one already-started bounded
RPC/operation may overrun that deadline. A later resumable closed recovery worker remains
necessary. Original lifetime V2 still has its old global execution head, and own-ACK maps
are not yet a production capacity design.
