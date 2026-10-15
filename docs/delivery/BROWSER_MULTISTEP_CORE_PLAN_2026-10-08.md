# Browser multi-step recovery core: smallest next slice

Proposed 8 October 2026. Design only; no source changes, deployment, employer
requests, pairing, permission grants or enablement. Extend the disabled Python
core and its local durable proof first. Connect the local synthetic browser
fixture only after that core is reviewed. Final submit and credit settlement
remain absent.

The candidate reviews one exact resume, destination and ordered set of answers,
acknowledges that filling can autosave, and approves once. Each field then gets a
fresh online decision; there is no approval dialog for every field. Finish with
“Answers filled. Review the portal, upload your file and submit yourself.” Filling
does not mean an application was submitted, received or charged as completed.

## 1. Permanent opening claim; one sealed attempt owns the sequence

Keep the existing key byte-for-byte:
`SHA256(canonical({subject_uuid, employer_key, tenant_id, opening_key}))`.
Do not add epoch, attempt, command, device, package, approval, step or field to
that key. First step begin permanently claims the opening for an immutable
`attempt_id` and `sequence_digest`. Subsequent fields may use that existing claim
only within its exact owning attempt. No second attempt can adopt it, even after
all fields finish, cancellation, epoch advance, new review or changed package.

Keep one sequence of 1–8 fields, one device/key/executor, one selected top-frame
document and one static form. Every step is `fill`; file upload, navigation,
submit buttons, hidden/password/file fields and unsupported controls are excluded.
Checkbox/consent changes are permitted only when explicitly reviewed as typed
boolean values with the exact policy text/version bound in the form digest.
Conditional questions or a changed document stop the sequence and hand off to the
candidate. Dynamic branching, device transfer, new-epoch continuation, automatic
restart recovery and adding fields to a partially executed sequence are deferred.

## 2. Add an explicit version 2 contract

Preserve the current `Binding` and its hashes as version 1; do not add permissive
defaults or silently reinterpret old approvals. Add strict, extra-forbidden
models for `SequenceContext`, `FillStep`, `FillManifest`, `StepPermit` and
`StepDecision`, with an explicit `protocol_version=2`. The authority generates
attempt and permit IDs; a client cannot choose a new namespace to reset progress.

| Immutable record | Required binding |
| --- | --- |
| Sequence context | Existing authority ID, stable subject UUID, epoch ID/generation, authenticated candidate identity digest, device ID/key hash/executor revision, employer/tenant/opening/application, approval ID/revision, admission ID, policy/pricing hashes, grant ID/revision, package/review digests and fixed approval deadline; plus attempt ID and sequence digest |
| Exact artifact | SHA-256 and immutable generation string; sealed descriptor digest covers native filename, MIME type and size. Verify actual bytes before review and before execution. The file remains local; this grants no upload action |
| Target/form | Exact origin, approved URL/route digest, selected tab/top frame and document ID, verified adapter ID/version, form version and schema digest. Form digest covers field identity, labels/types, required flags, options and consent policy text/version; it excludes current field values |
| Ordered step | Zero-based index, unique step ID, unique field ID, allowed control type, field descriptor digest, typed value hash and expected-before-value hash. No repeated field, skipped index, duplicate step or unsupported action |
| Step permit/decision | Full context digest, sequence digest, attempt ID, exact step digest/index, permit ID, predecessor outcome event/ack reference, current epoch/device/approval bindings, issue/expiry times and begin event sequence. Authority stores only the winning decision nonce hash |

Specify digest construction without a circular reference: (1) build the base
context without sequence/review digests and build ordered entries without derived
step digests; (2) hash that base, artifact/target/form descriptors and ordered
entries with `hirewiz.fill-sequence.v2`; (3) derive review digest from that sequence
digest, exact rendered-review payload digest and disclosure/action acknowledgement
version; (4) derive final context and per-step digests, binding the sequence digest.
Derived hashes are not fed back into their own inputs. Publish canonical JSON
test vectors. Hash typed values (`true` differs from `"true"`); use safe integers
for time/index and strings for storage generations. Include Unicode/control
character vectors for Python/JS parity. A changed file, field, value, order, form,
consent, price/permission snapshot or destination produces a different sequence;
it cannot replace an already claimed attempt.

Only opaque IDs, digests, deadlines and state enter authority records/events.
Answer strings and file bytes remain in the sealed candidate/device package,
not in checkpoints, audit logs or the recovery journal. Hashes are still
pseudonymous data requiring a retention policy.

## 3. Add state, not a second ledger

Use the existing transactional `AuthorityStore` boundary; the test file can add
namespaces without any application PostgreSQL migration:

| Record | Mutable state / rule |
| --- | --- |
| `attempts/{attempt_id}` | Immutable context/manifest plus `READY`, `ACTIVE`, `BLOCKED_UNKNOWN`, `CANCELLED`, `HANDOFF` or `LOCAL_SEQUENCE_FILLED`; `next_index`, `in_flight_step_id`, completed-prefix event references. No status grants disclosure |
| `steps/{attempt_id}:{index}` | Immutable step digest and `UNBEGUN → BEGUN → LOCAL_FILLED` or `BEGUN → UNKNOWN`. Winning begin nonce hash and completion continuation nonce hash; no executable reply persisted. Exact terminal outcomes are idempotent; conflicting outcomes are denied |
| `permits/{permit_id}` | Non-authorizing PREPARED permit for the exact next step, then permanently consumed begin. Expired unbegun permits can be replaced for that same unbegun step; consumed ones never can |
| `claims/{stable_opening_key}` | Write-once version-2 attempt ID, context/sequence digest and first-begin event. It never becomes free and its owner never changes |
| Approval/tombstones | Version-dispatched approval stores the full sequence and exact candidate identity/challenge; existing subject/approval/device/grant/artifact tombstones apply to every step. Add an attempt cancellation tombstone |

Keep the cursor and immutable sequence separate. A cached cursor or browser
`filled_count` is observational. Derive the valid prefix from retained step begin
and local-outcome events; missing/mismatched claim, attempt, step, approval,
consumed challenge, cancellation or epoch projection makes authority unavailable.

## 4. Logical service API and transaction boundary

These are core methods and future transport contracts, **not live HTTP routes**:

1. `seal_sequence(candidate, base_context, ordered_manifest)` creates an immutable
   non-authorizing draft, allocates the attempt ID, derives/returns exact digest
   inputs for review, and makes no opening claim. It returns SEALED_DRAFT only.
   `sequence_challenge(candidate, attempt_id, sequence_digest, review_digest)`:
   current authenticated candidate/subject and epoch; one-use challenge also binds
   the exact proposed review. A device claim or restored SQL approval alone cannot
   register candidate approval.
2. `register_sequence(candidate, challenge_id, attempt_id, exact_review_digest)`:
   validates the complete bounded manifest and all bindings, atomically consumes
   the challenge and approves the exact presealed draft. It returns IDs/digests
   and READY only. It neither claims an opening nor grants DOM mutation.
3. `prepare_step(device, attempt_id, expected_index, sequence_digest,
   predecessor_continuation_nonce)`:
   validates current identity, epoch, approval, grant, revocations and exact next
   unbegun step; returns PREPARED with at most the existing 10-second lifetime.
   It grants no mutation. Step zero needs no predecessor nonce; later steps must
   prove receipt of the preceding completion acknowledgement, against its retained
   hash. Progress never renews the fixed approval deadline.
4. `begin_step(device, permit_id, expected_context_and_step_digest)`:
   one authority transaction checks all current bindings again; ensures the prior
   steps are a contiguous LOCAL_FILLED prefix and no step is in flight; validates
   the cursor/index and deadlines. Step zero inserts the stable opening claim;
   later steps require its exact immutable attempt/sequence owner. Atomically write
   step BEGUN, permit and predecessor-ack consumption, winning decision nonce hash,
   ACTIVE/in-flight state and the append-only begin
   event. Only the winning confirmed commit returns a decision valid for at most
   the existing two seconds. Duplicates, lost replies and expired/delayed replies
   return status only; never reconstruct or mint a fresh may-act response.
5. `record_step_outcome(device, attempt_id, step_id, permit_id, begin_event_sequence,
   status, event_id, observation_digest, winning_decision_nonce)`:
   the original authenticated device can record LOCAL_FILLED or UNKNOWN, even
   after cancellation as a non-authorizing late observation. LOCAL_FILLED advances
   the cursor exactly once and clears that in-flight reference atomically, only
   when step/index/begin/predecessor match and the supplied nonce matches the
   winning begin nonce hash. UNKNOWN needs no winning nonce and never becomes
   LOCAL_FILLED in this slice. Bind the observation hash to attempt/sequence,
   step/index/value hash, document, permit and original begin event. Do not accept
   an unbound count or a bare “complete” assertion as progression proof.
   For a nonfinal locally filled step, atomically retain a new continuation nonce
   hash and return the nonce only in the first confirmed outcome response. It is
   not permission to fill; next prepare/begin still recheck all current authority.
   A lost/duplicate outcome response returns status only, never reconstructs that
   nonce, so an absent acknowledgement cannot silently unlock the next field.
   The following begin consumes it exactly once. An already cancelled/blocked
   attempt remains blocked; if current epoch/approval/grant/device/deadline checks
   no longer pass, retain the late local observation but hand off and issue no
   continuation. Final local success means LOCAL_SEQUENCE_FILLED only.
6. `cancel_sequence(candidate, attempt_id, event_id)` and `sequence_status(...)`:
   cancellation persists a monotonic attempt tombstone, serialized against every
   begin. Status exposes redacted progress and no decision. Cancelling before a
   step begins denies it; cancelling after begin cannot recall an autosave or free
   the opening claim. Global/subject/approval/grant/device/artifact revocation
   continues to apply without a new cancellation endpoint.

All authority callbacks remain free of browser, employer, file-storage and
application SQL networking. A future production adapter must provide atomic
multi-record transactions and the independently retained recovery evidence;
the existing unavailable production adapter stays unchanged in this slice.

## 5. Browser checkpoints and interruptions

Before each begin request, durably write a version-2 local checkpoint bound to
subject, device/key, attempt/sequence digest, document, exact step/index and permit
ID, with `action_in_progress`. Reinspect the actual account, document, exact URL,
frame, form and selected control; after the fresh begin reply, synchronously
recheck identity/schema/control/expected-before value and deadline immediately
before the setter and input/change events. Read-only inspection does not fill,
select a file or trigger a synthetic submit. One step's events may autosave;
their receipt is not implied by the adapter returning “filled”.

After DOM success, write `local_filled_pending_report` before reporting the local
observation. Keep winning decision/continuation nonces in the live session only;
do not cache them as restart authority or persist plaintext in the journal.
Write `step_filled` only after the authority acknowledgement. The
next field needs a new online prepare/begin and current cancellation check. Local
success does not itself authorize progression; nor does a cached signed command,
approval, claim, cursor or permit.

Crash after begin, ambiguous reply, failed DOM call, checkpoint write failure or
uncertain completion acknowledgement stops all further automatic filling. Keep
`unknown`/in-progress evidence; no clearing it via Cancel or a new command. A
duplicate outcome report can establish what the authority already recorded, but
cannot return the continuation nonce lost with the original acknowledgement. The
smallest slice does **not** automatically resume after any ambiguous checkpoint
or browser restart. The candidate finishes manually. Even a status showing
LOCAL_FILLED cannot reconstruct a lost begin decision. Navigation, login, MFA,
CAPTCHA, unsupported/conditional fields and changed before-values hand off rather
than skipping a step or making a new sequence under the same occupied opening.

Preserve the decision expiry calculated at the winning commit; never start a new
two-second lifetime when signing/delivering a delayed response. Honest-browser
freshness checks do not prove physical egress fencing or recall an already-begun
autosave. A compromised extension and independent manual user actions remain
outside this guarantee.

## 6. Migrate the proof without upgrading existing permission

Keep all 85 version-1 regressions. Add version-dispatched record/event validation
and new sequence methods/models; retain old binding digests and journal bytes.
Version-1 claims remain opaque, permanently occupied one-fill claims. Never
backfill a manifest, attach more fields or turn a historical LOCAL_FILLED into
an unclaimed opening. Version-1 records can expose status; they cannot be adopted
by a version-2 attempt. Missing version-2 fields fail closed.

No live recovery store exists, so no production data migration is required for
this core-only change. The file-backed test adapter can explicitly migrate its
record schema while CLOSED, in a transaction with an appended migration event,
preserving all old records/events; an interrupted/incomplete migration is unavailable. New test
files use version 2 from creation. Application schema, billing, admissions and
OpenAPI remain unchanged until a separately reviewed transport integration.

Proposed core files: recovery `contracts.py`, `service.py`, `store.py`; the existing
test-only `fixtures/recovery_authority.py`; new focused
`test_recovery_sequences.py` and `test_recovery_sequences_processes.py`, while
retaining the two version-1 test suites. Only after core review, adapt the local
browser `extension/protocol.js`, `transport.js` and fixture authority/tests to
the same version-2 contract. Keep disabled configuration, permissions and manifests
unchanged. The existing JS Maps/Sets fixture is not the durable authority.

## 7. Smallest meaningful acceptance proof

- Two different attempts/commands/approvals/devices race for one opening: one
  permanent attempt owner. Changed package, epoch or cleared browser state cannot
  create a replacement sequence after first begin.
- Independent processes race the same step and separate permits for that step:
  one permission. Adjacent/skipped/reordered steps cannot begin until the exact
  predecessor's local outcome is durably committed; completed steps never replay.
- A four-field uninterrupted sequence progresses in order with four fresh begin
  decisions; explicitly approved consent and typed values stay exact. No file
  control mutation, submit event, receipt or financial settlement occurs.
- Tamper each manifest/context/field/file/form/document/URL/consent binding;
  duplicate field IDs, dynamic question insertion, before-value change and unknown
  controls deny further mutation.
- Cancel/revoke/close epoch before step two and race them with step-two begin;
  serialize the winner and retain the opening claim and prior disclosure evidence.
- Prove that status/cursor alone and a LOCAL_FILLED report without the winning
  begin nonce cannot advance; lost completion acknowledgement never reissues its
  continuation nonce and blocks next-step prepare. Replay a consumed predecessor
  nonce against another index/attempt/device and reject it.
- Kill after step begin commit before reply, after DOM/local checkpoint but before
  outcome, and after outcome commit before acknowledgement. Restart/timeout/status
  never repeats the step or starts the next field from ambiguous state.
- Application-only backup restore retains attempt, cursor, claims and tombstones
  in the separate authority file. Missing/rolled-back cursor, step projection,
  consumed challenge or cancellation with intact journal fails closed.
- Legacy one-fill claim cannot become a multi-field claim; bounded clock/lease,
  unavailable store, expired/delayed signing, and Python/JS digest vectors preserve
  existing protections. Production defaults remain unavailable/disabled.

This extends **local durable protocol evidence only**. It does not establish
Firestore/GCS/IAM/retention, authority-file rollback detection, production auth or
pairing, tenant grants/real ATS adapters, broad human-checkpoint detection,
physical old-process egress fencing, throughput, real employer applications or
production readiness. Those remain distinct reviewed gates.

## Source anchors and concrete current constraints

- Root `backend/app/domains/recovery/contracts.py`: stable opening key and strict
  single-field binding. `service.py:236–313`: prepare/begin rejects any existing
  claim and records one field; `service.py:355–373`: non-reopening local outcomes.
- Root `browser-companion/extension/protocol.js:144–167`: existing field loop,
  per-field online exchange and one current checkpoint; this needs attempt/step
  binding rather than trusting filled_count.
- Root `browser-companion/fixtures/authority.js`: authorize uniqueness is scoped
  to command ID and begin uniqueness to permit ID. It lacks an authority-owned
  opening attempt/predecessor cursor. Its delayed begin signs a fresh lifetime
  after the delay, which must change to preserve commit-time expiry in the next
  local fixture proof.
- Existing `RECOVERY_FOUNDATION_PLAN.md` and `BROWSER_COMPANION.md`: fresh candidate
  approval, current per-action authority, retained claims/tombstones and explicit
  production/independent-journal limitations remain controlling constraints.
