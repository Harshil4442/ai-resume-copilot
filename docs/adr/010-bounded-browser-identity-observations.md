# Bounded identity observations for native browser operations

Status: implemented locally; exact-source review, deployment and live pilot
acceptance pending. API 333 (source `c4b7d78`, image `41af169`) and frontend HZ2
remain canonical. Acceptance belongs to existing outcomes 3, 5 and 6.

## Context

Firestore Admin GetDatabase uses the project's global
`DatabaseOperationsPerMinutePerProject` quota,
`firestore.googleapis.com/database_operation_requests`. The retained actual limit
is 60/minute and Cloud Quotas reports increase eligibility `NOT_SUPPORTED`.
A 600/minute increase has not been obtained. The newer request `577e…` returned
challenge HTTP 200, then one confirmation HTTP 503 with actual GetDatabase ResourceExhausted;
its signed status was unavailable. Preserve that paused, unreplayed operation.
A browser error is not permission to clear uncertainty, resend confirmation or
adopt a previous grant.

The prior narrow fence coalescing left confirmation at 54 metadata calls. Repeating
immutable database identity reads throughout one operation leaves little headroom
for its full HTTP ceremony and other project consumers. Idle current-version
hidden pages do not poll profile/features, but a fresh companion page formerly
requested both unrelated header resources. Their removal is part of the local
change; ordinary navigation, sessions, cached feature filtering and normal routes
remain unchanged.

## Decision

Use an explicit operation-local observation of exactly two Firestore RPC objects
and their exact RegistryPins, with a maximum monotonic lifetime of 30 seconds.
Fresh entry observations validate the database resource name, expected UID and
FIRESTORE_NATIVE type. Identity reuse is limited to that same operation, exact
RPC objects and immutable pins; it is neither a TTL/global cache nor a cached
positive authorization decision.

The scope validates its exact Context token, thread/task, current scope and
deadline on use. Nested or copied contexts cannot borrow it. A caught identity
failure poisons it permanently. Success/failure cleanup clears dependency and
callback references; failed scopes run bounded exact-token quarantine cleanup.
Normal native validation outside an explicit scope keeps its existing fresh
identity behavior.

Keep all existing fresh native authorization, witness, closure, epoch, session,
credential-generation, ownership, challenge, cancellation and revocation reads.
The scope does not replace them with observations or cached allow decisions.
Fresh identity barriers remain at operation entry and exit, KMS pre/post signing,
protected-grant boundaries, SQL activation and denial changes, own-acknowledgment
acceptance and capability/result export. Exact original intent, version, one-use
claim and native own-ACK rules remain required. Missing, changed, unavailable,
expired or poisoned identity observations refuse output; an old or uncertain
acknowledgment cannot become new authority.

## Gateway admission during identity observations

The [pairing admission candidate](../delivery/BROWSER_GATEWAY_ADMISSION_CANDIDATE.md)
separates a valid five-second incoming gateway assertion from the already-admitted
operation's existing thirty-second processing bound. Its opaque verifier-issued
receipt is native-instance/thread/task/request bound and single-use, with fixed
wall/monotonic deadlines and rollback refusal. Atomic gateway nonce retention may
cover the original signed expiry plus thirty seconds; a new invocation still
requires an unexpired original signature. This explicitly changes final-consume
freshness semantics and does not cache a positive native authorization decision.

Current identity/control/session/fence checks, actual human review/password and
pending-pairing TTLs, original acknowledgements and all fresh signing/export/entry/
exit barriers remain required. Pairing's existing local clock is not the separate
provider-clock authority in ADR008. Pure pinned admission precedes cold native
composition; its construction-owner slot binds once to the exact native/verifier
under the same processing deadline. Platform delay before admission remains
subject to the original signed TTL and must not be hidden by a retry or extension.

The exact retained session snapshot validated in the nonce transaction is also
bound to the opaque gateway admission as a restrictive issued/expiry window.
Every later pure admission barrier retains that original bound, including after
Commit/fence, command/reply and observation exit. A longer processing lease never
extends a session; expiry withholds output while preserving any consumed nonce.
This uses the buffered native snapshot, not an authorization cache or reread loop.

The [signed-device follow-on](../delivery/SIGNED_DEVICE_ADMISSION_CANDIDATE.md)
separates its five-second transport entry from the same bounded processing. A
timing-only capture precedes cold composition/current-key lookup, but cannot
consume a nonce or run a command. The exact native verifier must check the actual
ECDSA and fresh current origin/key, then issue a separate opaque admission. Known
IDs are reread in the nonce transaction; changed key/origin or inactive device
refuses. No positive identity observation is substituted for these checks. The
create/complete/refresh ten-second server proof deadlines are unchanged.

## Explicit security tradeoff

This weakens the timing guarantee for **private internal writes**. A pinned
execution/protected database could be replaced after entry and before the next
fresh barrier. Internal Firestore/GCS ledger writes may therefore precede
UID-replacement detection. They must remain untrusted/quarantined, never a source
of new signing permission, protected grant, SQL activation or exported authority
when a barrier fails. The implementation withholds authorization output after
failure and retains original uncertain history instead of replaying or adopting it.
This is not equivalent to the previous no-private-write guarantee.

Metadata reads, Firestore/GCS writes, KMS and SQL do not form one atomic
transaction. Fresh barriers narrow races; they do not make all side effects
atomic with database replacement. No new employer disclosure, host permission,
identity seed, security bypass, IAM grant, cloud product or paid plan is enabled
by this change. Existing final-action controls and employer-write holds remain.

## Evidence and consequences

The local intercepted SDK journey reports these operation-specific metadata counts:

| Operation | Previous | Local change |
|---|---:|---:|
| Candidate confirmation | 54 | 8 |
| Device completion | 36 | 22 |
| Device revocation | 71 | 20 |

Ordinary native validation outside the scope remains 4 calls. These are local
counts, not a production throughput measurement or a whole-HTTP-journey budget.
The aggregate 60/minute project quota still limits simultaneous operations and
other consumers. The exact companion-route Nav change omits profile/features
requests, credit-event invalidation and cached credit snapshots while preserving
normal routes; its 11 focused frontend cases passed. The affected backend suite
passed 142 cases before the final cancellation cleanup. After that change,
26 helper and 15 actual SDK failure-boundary cases passed. Local counts and
tests do not establish a completed live grant or acceptance of outcomes 3, 5 or 6.

The live replacement case is already delivered through a normal one-credit search:
Razorpay `4744101005`, with an actual internal application retaining original 28's
exact source hash and only first_name saved, with application reserve 0/charge 0. No employer
fill, attachment/upload or submission is accepted. After the reviewed exact-source
rollout, reconcile the existing `577e…` operation without replay, then complete the
original bounded pairing, native inspection, exact-file/answer review, approval
and cancellation before Fill, native revocation and exact-ID zero-effects/accounting
observation. Native inspection/revocation acknowledgments remain separate from SQL
readback. See [current pilot progress](../delivery/AUTO_APPLY_PROGRESS.md).

## Draft amendment: pre-send metadata cohorts (local candidate only)

This amendment is **not accepted or enabled in production**. Its constructor
option defaults to false, has no environment/HTTP selector, and applies only to
ordinary `ConfiguredCandidateAccounts.validate`. The Stage5 runner can explicitly
select it with `--billing-journey --pre-send-identity-batching`; the normal harness
and production factory remain unchanged. Browser pairing, lifecycle mutations,
signing, activation and other privileged paths retain their existing fresh calls.

The production factory currently constructs a new configured composition on each
request. Stage5 instead explicitly shares one configured account object. Therefore
the candidate can reduce metadata calls in that shared fixture, but establishes
**no production throughput improvement**. Caching a composition or private
authorization state is not part of this amendment. A production transport-sharing
design would need separate review and proof.

Within one exact shared composition, a metadata caller may wait up to a nominal
2ms collection window. Scheduling can delay wakeup; that delay consumes existing
deadlines rather than extending them. Cohorts contain at most eight callers;
there are at most 32 pending callers and eight collecting/in-flight cohorts. The
cohort is detached from the open queue **before** Redis admission and the SDK
request. A caller arriving after sealing never joins that request, even while its
response is pending. No completed result is available to future calls. Values are
immutable inside the cohort and returned as distinct copies to its sealed callers,
then cleared when those callers leave.

The cohort binds the exact RPC objects, data/Admin clients and transport, both
complete immutable RegistryPins, database names, project, fixed request shape,
timeouts and admission configuration. Each physical SDK List still uses the
original `before_metadata_rpc(required=True)` admission, no retry, and its original
timeout. The global Redis moving window remains 40 **physical metadata RPCs** per
minute. A shared RPC consumes one slot; unknown acknowledgments, quota refusal,
changed/missing identities, partial metadata and RPC failures fail closed for all
affected callers, without a retry or fallback.

Every caller keeps its own Context/thread/task-bound observation scope and
original 30-second deadline, UID comparison, poison/quarantine state, entry and
exit barriers. In particular, its exit ticket is created only after its own
dependent work finishes; it cannot reuse its entry ticket. This extends sharing
of a *fresh physical metadata read* to already-waiting concurrent callers. It does
not share an observation scope or a positive authorization decision. Fresh
session, credential, closure, revocation, witness, journal and SQL reads remain
independent. A revoked caller can still be denied while another caller sharing
the same public metadata succeeds.

Installed-SDK/intercepted-transport tests can establish cohort ordering, request
counts and refusal behavior. They do not prove real provider identity/IAM,
Firestore durability, distributed Redis admission or Stage5 HTTP capacity. Any
candidate Stage5 report remains local/synthetic, binds actual source bytes, and
sets `production_transport_sharing_proven=false`. Separate instances, staggered
barriers or low concurrency may yield little or no batching benefit.

The opt-in experiment reports aggregate validation entry/completion/failure and
logical barrier/cohort counts alongside the existing actual physical SDK count.
Its separate candidate validator requires fresh entry and successful exit for
every successful validation, closed accounting, no pending cohorts, at most eight
successful barrier observations per physical List, and the original physical
40-RPC ceiling/effect/ownership checks. It retains the complete original baseline
validation result, whose unbatched two-physical-RPC-per-response predicate is
unchanged. Neither physical counts nor refusal counts are rewritten. These
aggregate experiment counters are not native proof or authorization receipts.
## Proposed process-owned metadata transport (default off)

`CANDIDATE_ACCOUNT_SHARED_METADATA_ENABLED=true` selects a local production-factory
candidate. Each factory call still returns a new account object. Its first ordinary
validation builds a new composition, data SDK client, dedicated Storage client,
service, witness and request-local observation scope. Only one lazy process-owned
Admin SDK client and its pre-send metadata coordinator are shared. Importing the
module or calling the structural factory constructs no SDK clients. The original
constructor-only experiment remains a separate selection.

The trusted factory creates one Admin transport and credential object. An unresolved
metadata-service principal is resolved through that same SDK credential before
publication. The owner pins its process ID, full native configuration and registry
pins, credential object/principal/scopes/quota binding, endpoint/options and relevant
environment. Changed bindings or inherited fork state refuse use. Ordinary token
refresh remains possible; no token or candidate authorization result is a pool key.

Each sealed caller retains its own RPC/data-client ticket and unchanged scope,
deadline, fresh configuration, witness, session and revocation checks. Metadata is
mapped by canonical database name back to that caller's RPC identities. The process
coordinator retains no request RPC/data-client references, and completed metadata is
released after its sealed waiters return. New arrivals cannot join after send.
Known local pending/cohort limits return a capacity refusal and increment an
aggregate diagnostic counter; UID/configuration/deadline/unknown failures remain
unavailable. This does not identify the cause of the earlier experiment's lone 503.

Installed-SDK tests can verify fresh-factory object privacy and transport ordering.
No Cloud Run, IAM, hosted latency, invoice or production-capacity qualification is
established by this candidate. The operator flag remains off in deployments.
