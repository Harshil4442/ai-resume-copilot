# Pairing invocation admission: isolated connected proof

Date: 2026-10-09. Development only; no production factory, endpoint, candidate
session, cloud resource or browser authority is enabled.

This is additive to frozen bridge patch
`23cedee6961e9e8365b7f26c20e69449695d7435d23a595f91156c321b431a73` and its exact
GCP foundation. The private baseline also retains the parent's narrowly verified
None-safe `_controls_match` repair. The original artifacts and 336-case evidence
are preserved. Source hashes, exact patch and focused test evidence accompany
this stage separately; no SQL migration or dependency change is needed.

## Dispatch ordering

The existing actual `PairingService` still performs every lifecycle/cryptographic
check. The coordinator's local issuer/capability and atomic operation-ID latch
remain, but a local latch is no longer the only invocation consumption gate.
No private-plan deserializer, public resume credential, expiry/reclaim operation
or alternate pairing lifecycle is added.

1. Validate the original server-issued private plan, canonical immutable input
   snapshot, exact parameter keys, pin, generation and current fence/lifetime.
2. Create the original protected intent with `ifGenerationMatch=0`, `retry=None`.
   Only a valid fresh POST acknowledgement and exact-generation byte verification
   can permit progress. A 412, timeout, malformed response or uncertain create
   returns receipt/status only. Pairing dispatch does not adopt existing intents.
   The generic `GcsJournal.write` remains unchanged for other safety effects.
3. Recheck the independent fence/lifetime, then create a typed protected attempt
   marker, also create-only and requiring a valid fresh POST acknowledgement.
   The marker path is `authority-pairing-attempts/<authority-sha256>/<operation>.json`;
   it is independent of epoch, private nonce, subject declaration and input
   partition. Existing or ambiguous marker creation never grants admission.
4. Recheck the fence/lifetime and exact marker generation outside each native
   transaction. Native admission reads exact operator controls, the initialized
   monotonic pairing clock and invocation absence; it writes one immutable
   `pairing_invocations` record with an `exists=False` precondition. Only this
   invocation's complete create-Commit acknowledgement permits first dispatch.
   A matching existing claim is consumed status-only, not an acknowledgement
   that can be adopted. A conflicting/corrupt claim denies.
5. Recheck marker/fence/lifetime after admission. Every actual core transaction,
   including each definite-ABORTED retry and crypto preflight read, requires the
   same exact current invocation claim. The claim namespace is excluded from
   the core's read/write capability. Core writes, event/head advancement,
   projection and operation receipt remain one native Commit; the receipt also
   binds the exact invocation fingerprint.
6. Post-Commit, pre-sign and final-output checks verify current marker generation,
   independent fence and lifetime outside transactions. Unknown core outcomes
   cannot sign or return identity output. Public reconciliation verifies original
   intent, marker and native evidence and returns receipt status only.

The original protected intent must be positively retained before any marker
attempt. Therefore a marker timeout with **no retained marker**, or an ambiguous
native admission with **no retained claim**, cannot grant a new replica dispatch:
its attempt to create the already durable original intent receives 412 and is
status-only. This proof uses protected intent history, not native absence.

A first original-intent upload ambiguity with truly no persistence is different:
no marker, native admission, core mutation or signing could have begun. The local
plan is still consumed, and old plans are denied by a new coordinator; there is
no claim of permanently durable consumption when no object was persisted. A
separately legitimately issued identical operation that obtains the first fresh
intent acknowledgement can perform the first core mutation. Tests retain this
boundary explicitly rather than representing absent history as proof.

## Minimal retained records and failure behavior

`PairingAttemptMarker` retains only version/kind, operation ID, non-secret attempt
UUID, full authority/database pin, epoch generation, exact intent digest and the
generation-bound original journal receipt. It contains no nonce, signature,
assertion, public-key contents, candidate identifiers or private plan. Its typed
receipt binds bucket/path/generation/bytes. Native claims add only that marker
receipt. Raw values and authentication/signing keys are absent from both records.

Only definite `ABORTED` responses may retry native callbacks, at most three within
the registry's configured retry deadline. The original claim bytes/attempt UUID
remain identical across retries. Missing or incomplete Commit acknowledgement is
UNKNOWN; neither a later matching native read nor an existing GCS object can
replace the first invocation's own acknowledgement. Storage SDK retries are
disabled. SDK socket/RPC timeouts and checkpoints do not prove a hard end-to-end
wall-clock deadline; underlying socket read-ahead and metadata response bounds
remain the earlier foundation's documented limitations.

Crashes after acknowledged original intent, after marker, or after native claim
may conservatively strand the operation. There is no clear/reset/reclaim path.
UNKNOWN means no output is available; it does not assert whether the core Commit
was retained. COMMITTED status proves the bounded native/retained receipt evidence
and never reconstructs a challenge nonce or identity signature. Legacy inert
bridge operation rows lacking invocation binding reconcile as UNKNOWN; no live
production pairing data or factory exists to migrate.

## Evidence scope and remaining gates

The final focused run passed **360 tests** in 15.93 seconds: 24 new admission
cases, 18 existing connected cases, 128 existing pairing core/crypto/process
cases and 190 existing RPC/journal/safety foundation cases. Ruff passes the five
owned Python files and Mypy passes the three source modules. This is a bounded
development suite, not the application's merged CI or a production drill.

Focused tests use actual Firestore 2.34.0 data RPCs against the existing explicitly
owned `127.0.0.1:58877` emulator, one new unique `pairing-bridge-*` database per
case, `AnonymousCredentials`, and denied ADC/secure cloud channels. They do not
reset/delete databases. Storage 3.13.0 runs against intercepted synthetic HTTP,
with actual create-only parameters and bounded generation-pinned reads. Per-thread
transaction tracking proves Storage/fence IO is outside its own native transaction
even for simultaneous replica tests. Synthetic state-change fixtures are operator
fault injection in their own databases, not runtime bootstrap permissions.

Cases cover simultaneous independent server issuers and local clones; original
upload ambiguity with/without persistence; marker response loss/malformed ACK and
no retained marker; native claim ACK loss with/without persistence; crashes between
each gate and core dispatch; finite definite-abort retries; changed control,
claim, fence, lifetime and marker generation; complete lifecycle/refresh/revoke;
and fresh subprocess receipt/status reads without private plans or output
reconstruction. Subprocesses use a sanitized environment, only public synthetic
intent/object bytes, explicit localhost transport and GET-only Storage interception.
Actual tests/elapsed counts are recorded in `admission-proof-summary.json` and
`admission-proof-tests.xml`; earlier failed/superseded harness evidence is retained.

The factory/store remain unavailable by default. This does not prove real
database UID/endpoint identity, independent subject/account lifetime and recent
candidate authentication, KMS signer provenance, approved extension distribution,
IAM separation, protected bucket retention or a closed restore-witness workflow.
Production identities must be unable to overwrite/delete retained intent/marker
history; actual permitted/denied IAM operations have not been tested here.

Restoration still requires protected complete ownership/deny projections, plus
intent/attempt/claim coverage, closed-cut manifests and fail-closed operator replay
before access. Command/attempt digests alone cannot rebuild lost key ownership,
deletion/revocation tombstones or native claim history. A rollback/loss of both
independent protected history and registry is outside this proof. Deployment
incarnation, global clock/head contention, end-to-end latency and real retention
remain rollout gates. These checks apply to identity operations, not warm public
or candidate read-only pages. Device identity still grants no job, field,
artifact download, upload, autosave or submission authority.
