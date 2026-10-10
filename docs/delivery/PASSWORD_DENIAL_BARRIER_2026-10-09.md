# Protected password denial closure and bounded closed inventory

Status: historical first source stage, **HOLD** after independent final-ACK race review.
The original frozen patch and failing probes are preserved. Its repeated inventories
did not exclude authorized late publication and must not be accepted as a complete cut.
The subsequent disabled all-port transaction repair is documented in
[Protected publication boundary](PASSWORD_PUBLICATION_GATE_2026-10-09.md). The remainder
of this document records the first stage's intended protocol and local checkpoint;
it does not establish that stage's inventory completeness. Production remains unavailable.

The existing native password reader checks its external fence before each native transaction
and after its read, and signing admission checks it again before any signing permission can
escape. The lifetime writer already protects complete fixed before/after images, but a
journal-only denial followed by an unpersisted unknown native Commit leaves the old OPEN
projection intact. An OPEN witness alone cannot detect that pending denial.

This stage makes closure independently visible before a denial intent is created. A
create-only object in the operator-pinned **witness bucket**, at a deterministic path for
the complete database/UID/authority/incarnation/epoch pin, permanently closes that pin.
The actual GCS witness checks absence freshly before and after its existing identity and
exact-generation OPEN witness reads. Any object, unreadable result, permission failure,
timeout or malformed response denies admission. Only a real strongly consistent NotFound
response establishes current absence. A cached observation never does. This relies on
private authenticated metadata reads and Google's documented strong object-read/list
consistency, not public cache headers or IAM propagation as a fence.
[Cloud Storage consistency](https://docs.cloud.google.com/storage/docs/consistency).

Before any close write, the typed closer checks the existing independent binding,
subject/session registration and exact ownership/generation in a read-only native
snapshot and with fresh witnesses. This creates no plan or permission: an invalid denial
scope cannot pause unrelated subjects. After its own verified generation-zero create acknowledgement, the closer atomically
changes native control to CLOSED and records its exact protected receipt and complete
native head cut. This shares the native control record with existing reader/admission and
runtime write paths: a transaction that observed OPEN conflicts with close; a retry sees
CLOSED. No SQL or user database transaction spans Storage or authority networking. Unknown
close with or without native persistence returns no context, while the protected closure
already prevents ordinary old readers from returning authority.

Only the exact owning denial command can allocate a new fixed-cut lifetime plan under the
closed fence. Granting account/session commands remain rejected. The denial uses fresh
identity, OPEN witness bytes, exact closure generation/bytes and the CLOSED native control
binding; these checks authorize retaining a denial, never browser action or signing. Its
intent retains the CLOSED control precondition, complete effects and fixed head cut. The
V2 coordinator-owned canonical bytes, detached exports and mutation checks remain the
execution authority. Unknown denial retains the closure and complete protected intent;
no context, permission or reopening is reconstructed.

This first closure deliberately pauses the **whole pinned cohort**, including unrelated
subjects. It provides a conservative emergency/recovery primitive, not acceptable steady
state logout availability. A later independently reviewed partition closure protocol must
preserve the same pending-denial guarantee before normal per-subject revocation can use it.
There is no method to clear/delete a close marker or reopen native control in this stage.
Already verified historical operation receipts can remain COMMITTED as status only;
they return no session context or signing/admission authority under closure. A logout
that indefinitely stops unrelated candidates is not launchable product behavior.
Reusing OPEN witness bytes or restoring SQL/native OPEN rows cannot remove the closure.

While CLOSED, a bounded inventory reads every protected authority-intent path across all
partitions, verifies each exact generation and canonical typed bytes, records full prefix
coverage, then repeats the listing and verifies unchanged native control/head. A gap,
duplicate, unsupported record, oversized inventory, changed generation, concurrent write,
unknown read or pin mismatch produces no cut. The resulting create-only manifest is a
**closed inventory proof**, not a permission or projection-complete restore proof: opaque
pairing input hashes still need independent effects reconstruction, all pending denials
must be replayed, old writers/egress fenced, and a new pinned database/epoch independently
validated before any later opening. Finite bounds fail closed rather than sampling a
large journal. Production partitioning and checkpoint/retention horizons remain required.
The present development bound is 64 unique objects, five pages per complete listing,
64 KiB per object, 4 MiB of verified object bytes and a 30-second admission deadline
checked between finite Storage RPCs. Retries are disabled. Individual SDK timeouts bound
RPC/inactivity behavior, not a hard wall-clock bound for arbitrarily slow streaming.
Three identical complete inventories bracket exact-byte reads, manifest creation and
final acknowledgement. The native head may remain at the exact close cut or advance by
one event for the exact owning denial. An advanced cut additionally requires its complete
protected denial before/after images, matching event chain, and verified retained native
operation/effects/receipt. A replaced digest, skipped sequence or missing operation produces
no cut. This bound is not a production scale or availability claim.

Generation-zero preconditions check absence of a live object; retention and runtime
nondelete/nonreplace permissions must prevent name reuse. Exact generations protect
the bytes read. None of those production policies is proved by an emulator or mock HTTP.
[Storage request preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions).

Required ordering: authenticated typed denial → protected closure own ACK → native CLOSED
own ACK → fixed denial plan → protected complete intent own ACK → native denial own ACK →
status only. No lost ACK is converted into a fresh owner or grant. The initial GCS close
marker may be retained even when the native close did not persist. Every admission path
uses the real closed-aware GCS witness; injecting a test-only OPEN fence is not production
configuration or evidence. In-flight operations that already crossed their final check
cannot be recalled; physical egress/action freshness and honest browser lifecycle remain
separate gates.

The next normal-operation protocol should use independently retained permanent deny
targets for the stable subject/account/session and authentication generation, with fresh
typed scope checks on every relevant reader, grant and consuming admission. A session
denial can then block that session permanently while unrelated candidates and a genuinely
fresh allowed lifetime continue. This needs new context-bearing fence seams, independent
deny high-water/lineage proof, race coverage and partitioned event cuts; skipping a prior
deny by picking a new epoch, SQL ID or prefix is forbidden. The whole-pin emergency close
remains necessary during restore. Opening a new pin requires a fenced complete inventory,
conservative orphan-denial/possible-action inclusion, all deny/ownership/consumption
projections, independently verified destination UID/epoch and protected witness publication.
This stage implements neither normal partition availability nor that opening procedure.

Remaining production gates: complete independent restore and deny/claim projection proof;
protected bucket IAM/retention/WORM and old-writer fencing; real identities and witness
provisioning; partition scalability/load proof; KMS/action-boundary freshness; actual
HTTP/BFF/MV3 pairing and permitted portal integration. Production remains unavailable.

## Frozen local verification

The connected 13-file security selection passed **665 tests**, with no failures, skips or
deselections, including all 65 V2 lifetime cases, 39 new closure cases and all three actual
PostgreSQL credential cases. The final run took 53.60 seconds and emitted 235 existing
Passlib deprecation warnings. The exact C253 CI Mypy selection passed for 88 source files;
Ruff passed for the recovery domain and every changed/new test. These are local source
proofs, not cloud policy, deployment, restore or steady-state availability evidence.

Tests use uniquely named native Firestore databases on this agent's pinned private emulator
at `127.0.0.1:58880`, AnonymousCredentials, and intercepted actual Storage SDK HTTP. The
identity fixture supplies a synthetic immutable UID; it does not prove production identity
lookup, IAM or retention. The emulator image, JVM/container budgets, exact source hashes,
JUnit reports and transport-only endpoint difference are recorded in the freeze manifest.
No existing emulator database was reset or deleted, and no cloud/provider/employer/KMS call
was made. The private endpoint fixture change is excluded from the incremental patch.

Adversarial coverage includes all four denial types, unknown close/denial commits with and
without persistence, lost protected create ACKs, an injected native OPEN row after unknown
denial, real reader/signing-admission races, stale issued grants, invalid independent denial
scopes, immutable fixed-plan mutation, corrupt/403/timeout closure observations, complete
multi-page all-partition listing, 64/65-object boundaries, page gaps/duplicates, changed
generations, deadline loss, and tampered or unproved native head transitions. Actual old
readers and consuming signing admissions are exercised; their behavior is not replaced.

An earlier full run invoked pytest through stdin, causing multiprocessing spawn failures,
and one sanitized child used the old emulator endpoint. That failed report is preserved.
The corrected run used the actual pytest executable and an explicit private endpoint in
the subprocess-imported fixture. The final passing report is a separate immutable file.

Review checklist:

- [x] Protected close retained before allocating or retaining a denial intent.
- [x] Every ordinary real GCS witness read/admission checks fresh close absence on both sides.
- [x] Unknown native denial leaves old reader/admission authority denied.
- [x] Grants, retargeted denials and lost-ACK ownership are rejected while closed.
- [x] V2 immutable canonical fixed-cut plans and status-only historical receipts are preserved.
- [x] Bounded whole-prefix inventory fails closed on uncertainty and supplies no restore grant.
- [ ] Independent complete restore of all partitions, denials, ownership and consumed actions.
- [ ] Safe per-subject normal-operation barrier and unrelated-candidate availability.
- [ ] Provisioned immutable resource identities, IAM/retention/WORM and old-writer/egress fences.
- [ ] Complete HTTP/BFF/MV3 recent-password/KMS/action boundary and permitted portal workflow.
- [ ] Separate review and rollout evidence before any production activation.
