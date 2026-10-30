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
