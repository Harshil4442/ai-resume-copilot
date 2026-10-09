# Protected password account and web-session lifetime writer foundation

This is an additive, explicitly injected foundation. No production factory, HTTP/BFF/MV3
route, SQL login/logout/reset/delete integration, cloud resource, credentials or OPEN witness
is created. The existing production authority remains unavailable. It adds no employer,
resume, application, payment, approval, upload, submit or action capability.

The parent native password adapter was independently reviewed and integrated as
`c253038e7ef5d1466b057aebc0cb1a0f3dd659d7`. This author worked in a private archive of
`3b96c795bf6d87f82af394a3ed99b754fe0dbfa2` plus the exact reviewed native password patch;
all affected prerequisite source hashes agree with the integrated parent. No parent or cloud
files were modified by this author.

## Operation-specific behavior

- `bind_password_account` accepts only a typed, trusted server command identifying a new
  immutable binding UUID, numeric SQL account, and an already independently registered
  active subject/principal. It validates the existing `SUBJECT_REGISTERED` event, current
  subject lifetime, absence of permanent subject denial and account tombstone, and creates
  immutable account/subject and SQL-account ownership records. It commits the exact
  `PASSWORD_ACCOUNT_BOUND` event required by the existing reader. A fresh binding UUID
  cannot reuse a previously owned subject or SQL identifier. It never registers a subject,
  repairs provenance, or derives registration from email, browser bodies or bearer claims.
- `create_password_web_session` requires the exact bound active subject/principal/current
  authentication generation. A new server session UUID receives a retained registration,
  exact `PASSWORD_WEB_SESSION_CREATED` event and matching `pairing_sessions` projection.
  The issue/expiry interval is positive and at most one day. Only this invocation's definite
  Commit acknowledgement and fresh post-Commit witness can return a trusted server
  `CandidateWebSession`; it contains no cookie, bearer, password or hash.
- `revoke_web_session` checks retained session ownership and its exact registration event,
  retains an immutable session tombstone, and changes the matching pairing projection to
  inactive. It retains the original creation record and event as historical evidence. A
  revoked session UUID cannot be registered again, and a different account cannot revoke it.
- `advance_auth_generation` requires the caller's exact current generation and advances it
  by one. The subject and retained authentication high-water record move together. Old
  sessions become invalid through the existing core's generation check, without enumerating
  sessions or devices. Stale generation plans and a regressed subject/high-water pair fail.
- `tombstone_password_account` and `delete_subject` consistently retain the account tombstone,
  set its subject inactive, and write the permanent subject revocation read by existing
  pairing and recovery cores. Subject deletion must name the same bound subject. Neither
  operation frees immutable ownership or permits rebinding a deleted lifetime. A stale SQL
  or subject projection alone cannot remove the retained tombstone/revocation.

Generic pairing transactions still cannot read or write the new account ownership,
high-water, lifetime-operation or lifetime-effect namespaces. This coordinator exposes typed
operations rather than arbitrary caller callbacks or raw namespace mutation.

## Exact transaction and journal protocol

1. The server allocates an operation UUID and separate event UUID before protected IO.
   A bounded native read checks the fixed resource/UID/incarnation/epoch controls, operator
   clock, global event cut, independent subject and account/session ownership. It captures
   complete before-images and computes complete after-images and the exact chained event.
   The plan requires no password or credential and fixes a maximum 60-second interval.
2. The protected journal contains the full command, before-images, after-images, event and
   head transition, plus exact pin and IDs. These are replayable effects, not merely input
   or projection hashes. The record is bounded to 64 KiB and finite record counts. It
   includes private lifetime identifiers and therefore requires protected access and a
   reviewed retention policy; it is not anonymous data or browser output.
3. `GcsPasswordLifetimeJournal` issues one generation-zero create through the reviewed
   pinned Storage boundary. Only this request's fresh metadata acknowledgement plus exact
   generation-bound bytes yield a receipt. Existing objects, ambiguous creates, malformed
   acknowledgements and lost responses cannot be adopted, reconciled into authority,
   overwritten, deleted or retried by this invocation.
4. A fresh external witness and exact protected receipt are checked outside every native
   transaction, including each retry following a definite `ABORTED`. The native callback
   recomputes all typed effects from current state and requires their canonical bytes to
   match the original plan. All reads/absence checks precede the buffered native Commit;
   the original native versions provide CAS preconditions.
5. One Commit retains all lifetime after-images, the exact event/head transition, an
   immutable complete-effects projection and operation/receipt record. Only an owned
   definite acknowledgement followed by fresh witness, receipt and time checks can return
   success or a session context. A bounded definite-abort retry preserves IDs, bytes and
   preconditions. No transaction spans Storage or witness IO.
6. Unknown writes return status-only and no session context. Reusing an issued plan never
   creates another protected object or native mutation. A new coordinator cannot adopt
   another coordinator's private plan. Historical status checks exact retained effects,
   event, event cut, receipt and current witness; even a later `COMMITTED` status returns
   no session context and does not acknowledge a SQL administrative mirror operation.

A plan freezes the global event cut. Concurrent activity that changes it invalidates the
plan rather than silently retargeting already protected effects. The caller may prepare a
fresh explicit administrative plan after reviewing the pending outcome; the coordinator
performs no automatic workflow restart or POST replay. This conservatism adds abandoned
protected effects and reduces concurrency at the existing global-head boundary. A future
partition/sequence protocol requires its own proof before changing this trade-off. This
foundation does not establish production throughput, latency or load capacity.

## Mandatory activation and restore gates

**The writer and existing readers must remain disabled in production until the fresh
external witness/closed cut includes every pending protected lifetime denial and complete
restore proves old readers cannot act through an earlier lifetime.** Returning `UNKNOWN`
from a writer is not, by itself, a global denial fence.

The current `GcsWitnessFence` verifies an injected pinned OPEN body; it does not enumerate
new pending lifetime effects. If a deletion effect persists in protected Storage but its
native Commit persists nothing, an existing native reader can still see the old active
lifetime. The test suite explicitly reproduces this limitation. No test substitutes a
fixture fence for the missing production protocol or claims this situation is safe.

Required follow-on work includes an independently controlled close/admission barrier,
complete protected partition inventory/closed-cut manifest, effect inclusion rules and
fresh witness publication. All old readers/writers must lose execution authority during
unknown denial and recovery. SQL logout/reset/deletion acknowledgements and credential
mirror changes must remain pending until protected denial and the required native outcome
are established. Actual resource identity, retention/WORM, IAM separation, runtime pins,
network boundaries and rollback/old-writer fencing must be proved against real policy.

The full after-images make account/session/subject denial reconstructible from protected
records. They do not constitute a complete restore engine. Restore must combine these
records with independent subject registrations, every other event partition, pairing
projections, signing/invocation consumption, permanent ownership and deny histories. An
unacknowledged registration/session intent must never create an active lifetime merely
because its full after-image exists. A pending protected denial must be conservatively
included in the closed recovery procedure even when its old event-cut precondition changed.
Missing partitions, old SQL restoration and absent consumption records must keep the
incarnation closed. Data preview/replay alone never initializes or opens an authority.

The administrative transport must resolve the authenticated account and session server-side;
the typed command classes are not public request schemas. This slice does not prove that
transport or independent subject-registration provisioning. The real recent-password/KMS
boundary and all existing no-disclosure/action authorization gates remain mandatory.

## Bounded local evidence

New tests use only fresh uniquely named databases on the authorized root-owned Firestore
emulator at `127.0.0.1:58877`, with AnonymousCredentials and intercepted Storage HTTP. No
reset/delete, actual cloud, provider, payment, employer or KMS call is made. The initial
subject registration is an explicit operator fixture and is not protected provisioning.

The suite exercises actual native account/session writes and the actual registered-identity
reader, plus one complete lifetime-writer → SQL recent-password → protected signing
admission → actual native pairing confirmation path. Adversarial cases cover revocation,
reset/deletion races in both orders, concurrent coordinators, fresh-witness loss, definite
abort, unknown native commits with/without persistence, unknown protected creates,
malformed/existing acknowledgements, session/identity reuse, noncanonical type aliases,
corrupt provenance, tampered status evidence and namespace isolation. A closed data preview
shows complete denial reconstruction without opening the authority. Exact source and
verification results are recorded in the freeze manifest/proof summary separately.

V1 author checkpoint, superseded by independent review: 613 cases passed across the 12-file connected auth/pairing
selection, with 52 new lifetime cases, all three actual PostgreSQL credential cases,
zero failures/skips/deselections, and 235 existing deprecation warnings in 46.28 seconds.
The exact CI Mypy selection passed for 86 source files; the exact parent CI Ruff
selection plus the owned lifetime test passed. These are source/local-boundary proofs,
not a production deployment or complete restore/activation proof.


## V2 review correction: immutable plan authority

Independent review held V1 despite its passing checkpoint. The reviewer committed a real
session B after allocating session A, then used the Storage create callback to rewrite only
A's nested before/after/event dictionaries to the newer native cut. Pydantic's frozen models
did not deeply freeze these dictionaries, and V1 allowed A to commit with a changed digest.
The V1 patch, source snapshot, passing checkpoint and failing independent reproduction are
preserved; V1 is not a reviewed implementation to integrate.

V2 retains the exact allocated canonical bytes and their SHA-256 in a separate immutable
coordinator-owned record. The exported plan is checked against those bytes at entry and
throughout execution, but its mutable dictionaries are never execution authority. Each
native attempt reparses the original private bytes. Recomputed current effects must match
that original snapshot, and the committed operation and returned status use its original
digest. A changed cut requires a new explicit plan; no callback can retarget this one.

Each journal create/verify call receives a freshly parsed, deep detached export. No IO port
receives the internal working snapshot, another port's copy, or the public plan's nested
objects. Canonical-byte checks after calls reject any mutated export; checks around fresh
witnesses, reads, native attempts and Commit acknowledgements detect changed public plans.
Mutation after a native Commit withholds the session context and returns UNKNOWN under the
original digest. Historical status similarly freezes its input bytes and rejects changed
inputs or verification exports; it creates no mutation or execution permission.

Thirteen additional regressions exercise the exact independently reproduced retarget,
public mutation before entry and around fresh fences, detached create/verify mutation,
native-read mutation, mutation before/after Commit acknowledgement, definite-abort retry,
status-input/verification mutation and deep separation between every exported copy. The
complete pending-denial activation/restore limitation described above remains unchanged.
No close witness, runtime factory, SQL transport or cloud policy is manufactured by this
repair. V2's final checkpoint is recorded separately in the freeze manifest/proof summary.

V2 final author checkpoint: 626 cases passed across the same 12-file connected
selection, including 65 lifetime cases and all three actual PostgreSQL credential
cases, with zero failures/skips/deselections and 235 existing deprecation warnings
in 64.08 seconds. The exact C253 CI Mypy selection passed for 86 source files; its
Ruff selection plus the owned lifetime test passed. V1 remains held and preserved.
