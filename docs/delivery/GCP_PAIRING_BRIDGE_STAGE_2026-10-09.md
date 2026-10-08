# Connected pairing bridge: isolated development proof

Date: 2026-10-09. This is a development slice, not production enablement.

The isolated snapshot was copied from tracked source
`772634d9ce8445acd559564f9b33a503ee7f0943`, including the parent's uncommitted
corrected GCP authority foundation. The bridge patch must be applied after that
foundation, not directly to a source tree missing `gcp_rpc`, `gcp_buffer`,
`gcp_journal`, `gcp_media`, `gcp_contracts` and `gcp_service`. A baseline hash
manifest accompanies the patch. No shared source, cloud service, SQL/Redis
authority or browser/candidate operation was changed by this work.

## What is connected

`GcpPairingCoordinator` executes the existing `PairingService` and its actual
cryptographic and lifecycle validation through a restricted transaction adapter.
It does not introduce a second pairing lifecycle. The only core seam is an
optional UUID allocator whose default still calls the existing `uuid4`.

The coordinator supports exactly these commands: `prepare_request`,
`create_request`, `candidate_challenge`, `confirm_candidate`, `device_challenge`,
`complete_device`, `refresh_challenge`, `refresh_claim`, `revocation_challenge`
and `revoke_device`. Each command has an exact parameter-key set, exact UUID
allocation count, bounded canonical JSON input and namespace write whitelist.
It cannot grant a browser action, submission permission or employer access.

Each locally issued plan contains a private nonce/ID sequence allocated before
any native retry. Its public `PairingJournalIntent` contains the command, exact
input digest, pinned registry/epoch generation, public IDs, nonce commitments and
a lifetime of at most 120 seconds. Raw inputs, candidate assertions, private
nonce values and signing keys are absent from that intent. Do not log,
serialize, persist or return the private `PairingCommandPlan`.

Execution takes one deep canonical JSON snapshot of the supplied inputs before
validating the intent digest, and calls the core only with that snapshot.
Mutation of the caller's nested JWK or assertion after validation cannot change
the journal-bound command, including during a definite-abort retry.

## API and sequencing

An explicitly injected coordinator requires a `BufferedRegistry`, `GcsJournal`,
`RegistryPin` and exact positive pairing epoch generation. The default fence,
assertion verifier and claim issuer remain unavailable. There is no production
factory, HTTP route, plan deserializer or automatic ADC/client construction.

Illustrative server-private flow, with independently verified adapters supplied:

```python
plan = coordinator.allocate("prepare_request", {
    "key": device_public_jwk,
    "extension_id": approved_extension_id,
    "revision": approved_executor_revision,
})
outcome = coordinator.execute(plan, exact_parameters)
# A COMMITTED outcome may return the core's one-time challenge/identity result.
# UNKNOWN returns neither result nor journal receipt.
# Reconciliation uses only the public intent, never regenerated nonce output.
receipt_status = coordinator.status(plan.intent)
redacted_lifecycle = coordinator.pairing_status(pairing_id)
```

`allocate` is local and performs no remote IO. `execute` checks the exact input,
pin, generation and issuing coordinator capability. A lock marks the logical
operation ID as attempted before journal/native work; the lock is released
before all fence, Storage and Firestore IO. Same-operation copies of a plan are
status-only after an attempt starts. An unissued plan or plan borrowed by a new
coordinator cannot execute. An in-flight clone sees status only, including
UNKNOWN while no receipt is retained. Failed first attempts consume that local
plan; a plan is never a public retry credential.

The first attempt checks the fresh fence/lifetime, writes and verifies its typed
create-only journal intent outside any Firestore transaction, then invokes the
actual core. `BufferedRegistry.before_attempt` checks the fence/lifetime outside
each native transaction, including a retry. Only a definite `ABORTED` response
can rerun the bounded transaction callback; UUID cursor rewind then reuses the
same allocation sequence. Other sent-Commit failures produce UNKNOWN and never
replay that mutation.

The native transaction maps the core's `control/current` to exact, separately
operator-initialized `control/meta` and `pairing_control/current`. The latter
pins the registry, protocol version, OPEN state and generation. The core's
`clock/observed` maps to an operator-initialized `pairing_clock/observed`; strict
integer, nondecreasing time and CAS are required. Missing/corrupt controls,
clock or event head fail closed. Runtime cannot bootstrap, repair or arbitrarily
write operator control, legacy clock/head or unrelated namespaces.

The restricted adapter forces key-owner, revocation, device-tombstone,
device-request, assertion and confirmation namespaces immutable. It stages only
the command's allowed lifecycle changes, one new exact native event and its
head advancement. It stores an immutable operation receipt and canonical
`pairing_projections` changes/event record in the same native Commit. Original
reads/absence precede writes, and each write uses the existing update-time or
`exists=False` precondition. The foundation limits transaction reads/writes to
64, record bytes to 65,536, RPC timeout to at most ten seconds, and definite-
abort attempts to at most three within a bounded registry deadline.

Confirmed native mutation, a post-transaction fresh fence/lifetime, a separate
pre-sign fence and a final fresh fence/lifetime are required before identity
output escapes. A fence/lifetime loss after Commit withholds output even when
the mutation is retained. Ambiguous completion never reaches the claim signer.
Reconciliation verifies the exact operation/projection/event, retained head and
generation-bound journal bytes; it returns receipt status only. It cannot mint
another claim, reconstruct a nonce, release permanent key ownership, undo a
deny record or extend a challenge lifetime. The existing core's separate status
method is restricted to read-only transactions and redacted lifecycle fields.

## Exact development evidence

The focused run completed with **336 passing tests**: 18 connected pairing
emulator cases, the existing 128 pairing service/crypto/process cases and the
existing Firestore RPC, Storage journal SDK and GCP safety coordinator cases.
JUnit output is retained in `proof-tests.xml`. Ruff passes the six changed/new
Python files; Mypy passes the five source modules. The proof manifest records
the pinned SDK versions, file hashes and precise command.

The connected fixture uses the actual existing `PairingService`, actual
Firestore SDK low-level calls and the existing parent-owned emulator at
`127.0.0.1:58877`. Every case creates a new unique
`projects/hirewiz-local-authority/databases/pairing-bridge-*` database. Only
synthetic records and P-256 identities are written; no existing database is
reset or deleted. `AnonymousCredentials` is explicit, ADC and secure/cloud gRPC
construction are denied, and no production endpoint is used.

Storage uses the actual pinned SDK with an intercepted synthetic HTTP boundary.
The fixture enforces create-only upload, exact allowed intent bytes, concrete
generation and bounded generation-pinned media reads. It asserts that Storage
and fence IO never occur inside a native Firestore transaction. Synthetic
fences, identity pins, operator control and authentication/signing fixtures are
test inputs, not real IAM, database-UID, candidate-authentication or KMS proof.

The connected cases cover the complete request/candidate/device lifecycle,
claim refresh, revocation and permanent key ownership; exact stable-ID replay
after definite ABORTED; successful native Commit followed by response loss;
ambiguous Commit with no known retained row; copied/reconstructed/concurrent
plans; nested input mutation; unavailable defaults; exact input/plan scope;
clock rollback and corrupt control; post-Commit fence/lifetime loss; and a
fresh fence closing before a definite-abort retry.

## Remaining release gates

This patch deliberately leaves `production_store()` unavailable. It does not
prove or configure a production database UID/resource incarnation, native SDK
endpoint policy, IAM separation, authenticated assertion provenance, approved
extension registry, KMS signing, deployment lifetime, revocation propagation or
a restore-witness operator workflow. Those are required before any real
identity output or browser authority is enabled. No candidate or application
submission is covered by this slice.

The one-shot operation latch and issuing capability are coordinator/process-
local. New coordinators cannot execute old private plans, and only public
status reconciliation is supported after process loss. This is not a durable
invocation ledger or multi-process lifetime proof. There is no production plan
loader. A production integration must independently establish protected durable
invocation/lifetime and resource policy; it must not reconstruct an old plan
and treat an absent receipt as permission to retry an unknown mutation.

Protected command intents contain digests and allocation commitments, not the
complete replayable key-owner, subject and device-deny effects. Those effects
currently reside in native `pairing_projections`. Journal intents alone cannot
reconstruct permanent ownership or tombstones after lost/old authority state.
Closed-cut completeness, protected projection retention, restore replay and
all required witness/manifest coverage remain explicit unfulfilled production
gates. This slice does not substitute a replay engine or claim that a retained
command intent proves restoration safety.

The patch is independently reviewable against its isolated baseline and makes
no dependency, CI, deployment, migration, credential or live-service changes.

## Parent integration

The parent verified all original baseline/dependency hashes and integrated the frozen
seven-file patch. Independent QA reran all 18 connected emulator cases, passing in
4.25 seconds. The parent's actual whole-CI type selection found four Optional-dictionary
errors missed by the isolated import context; an explicit None-safe control comparison
repaired them without relaxing missing-control refusal.

The complete default parent suite now passes **1,279 tests, zero failures, errors or
skips**, in 75.80 seconds. Full-CI Mypy passes on 77 source files, scoped Ruff passes
and all six prompt evaluation cases pass. [The parent proof](evidence/2026-10-09-gcp-pairing-root-integration.json)
records the original frozen patch and integrated Python hashes. This is connected local
source evidence; durable invocation/recovery, real recent authentication, cloud IAM/KMS
and browser transport still prevent production enablement.
