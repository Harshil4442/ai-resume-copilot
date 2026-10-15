# Bounded GCP authority adapter stage — 9 October 2026

Status: isolated development proposal, not a production authority or enabled restore fence.
Base: `e7d8f2ba0435fd3d4a504ccdce83fac365ec6835`; copied archive
`/tmp/hirewiz-gcp-authority-20261009.sb6byghh/ai-resume-copilot`. Root/cloud are untouched.
The existing `production_store()` stays unconditionally unavailable. This slice adds only
explicitly injected SDK transport/buffer and typed journal primitives, with status receipts;
it does not return may-act/continuation/device claims or wire existing HTTP/browser cores.

## What the smallest implementable slice proves

1. Actual pinned Python SDK request/protobuf construction, explicit retry disabling,
   transaction read-before-write buffering and bounded definite-abort replay.
2. Deterministic generation-zero journal creation and exact stored-byte/generation receipt
   verification, including 412 and timeout reconciliation without permission reconstruction.
3. Typed stable operation identity, conservative immutable opening/key ownership and deny
   records, plus an atomic operation receipt. Ambiguity remains status-only/UNKNOWN.
4. An injected restore-fence boundary that denies by default. A local assertion/mocked fence
   is not evidence of a protected production witness, IAM or database identity.

New code will never pretend the existing generic store callback is a production-ready
cross-store coordinator. Journal context must be allocated outside retries. The initial
coordinator deliberately returns only bounded status/receipts, never callback-generated
nonces, signatures, credentials or action decisions. Full semantic wiring is a later slice.

## Registry and current SDK behavior

Use Firestore Native mode in a dedicated named database independent of application SQL.
Pin full `projects/<project>/databases/<database>`, Google's database UUID `uid`, application
authority UUID/incarnation and current epoch. Never infer `(default)` or use restored SQL to
create subjects/repair ownership. Data RPCs address the database name, not UID; checking UID
is therefore a useful rejection check, **not an atomic UID precondition**. This is an
inference from the API. A managed close/restore procedure and external witness are required.
[Database fields](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases),
[named databases](https://docs.cloud.google.com/firestore/native/docs/manage-databases).

Pin Firestore SDK 2.34.0 for this local SDK proof. Use public low-level `FirestoreClient`
`begin_transaction`, `batch_get_documents`, `commit`, `rollback`, and explicit Admin metadata
reads. Every call supplies `retry=None` and a bounded timeout. The high-level transaction's
`max_attempts=1` does not remove its internal GAPIC Commit retry policy, so it is insufficient.
Only a definite `ABORTED` may replay the full fresh callback; deadline/unavailable/cancelled
Commit cannot. [Pinned transaction source](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-firestore-v2.34.0/packages/google-cloud-firestore/google/cloud/firestore_v1/transaction.py),
[pinned transport policy](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-firestore-v2.34.0/packages/google-cloud-firestore/google/cloud/firestore_v1/services/firestore/transports/base.py),
[public client source](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-firestore-v2.34.0/packages/google-cloud-firestore/google/cloud/firestore_v1/services/firestore/client.py).

The buffered facade keeps original transactional snapshots separate from a copied overlay.
A first get/put reads the exact original document or confirmed absence in that transaction.
Subsequent gets see staged writes. Immutable puts compare exact canonical bytes/types, not
Python equality. No SDK write is queued until callback/validation finish; then one atomic
Commit carries updates/create preconditions and the operation receipt. Reject truncated,
duplicate or unexpected streamed read responses, unsafe paths, oversized data/read/write
sets and malformed Commit replies. No queries, historical `read_time`, BatchWrite, full
history scans, environment/ADC client construction or external callback effects exist.
[Transaction rules](https://docs.cloud.google.com/firestore/native/docs/manage-data/transactions),
[RPC contract](https://docs.cloud.google.com/firestore/docs/reference/rpc/google.firestore.v1).

Definite-abort retries have fresh buffers, original reads and time/deadline checks. Stable
operation and created-record IDs precede retries. The first attempted result is discarded.
After Commit is sent, an ambiguous response discards all computed values and never starts
another mutation attempt. Even a strongly consistent **missing** receipt immediately after
a timeout cannot establish abort: that in-flight commit may finish after the read. Exact
existing receipts can report committed status; conflict denies; missing remains UNKNOWN.
[Canonical RPC semantics](https://raw.githubusercontent.com/googleapis/googleapis/master/google/rpc/code.proto).

## Journal primitive and ordering

Typed intent contains version, stable operation UUID, fixed authority/database/incarnation/
epoch, subject partition, intent kind, exact minimal conservative safety effect and bounded
times. Effects are an opening hold, device-key ownership or deny scope; no raw file, answers,
resume, password, bearer or employer cookies. Deterministic paths contain authority/partition
hashes and operation UUID. Exact operation ID with different canonical intent is a conflict.
Prototype identities/scopes are explicit trusted caller inputs; genuine authentication and
sealed-server derivation remain missing, and no HTTP route accepts these objects.

Outside any Firestore transaction:

1. Verify current fixed witness/database identity and already trusted operation scope.
2. Create intent with `if_generation_match=0`, `retry=None`, small bounded bytes and timeout.
3. On success/412/uncertain upload, obtain a concrete generation and validate fresh metadata
   for that **exact generation** with a matching precondition. Require canonical raw wire
   generation and size, with size equal to the expected positive bytes (at most 65,536).
   Missing, malformed, oversized or differing size denies before a media GET. Download the
   exact generation with an explicit bounded Range and compare canonical bytes and SHA-256.
   A missing/unreadable/mismatched object never supplies a receipt. Keep generation as a
   canonical decimal string.
4. Execute a fresh bounded Firestore transaction that rechecks current pinned control and
   atomically attaches the exact journal receipt to immutable safety effect + operation record.
5. Require confirmed Commit and a fresh current external fence check before reporting success.
   A changed/missing witness or UID means no success value, even if records committed.

`ifGenerationMatch=0` means no live object, not eternal name uniqueness; IAM/retention must
prevent reuse/deletion. Metadata is mutable under retention, so content verification uses
recorded generation bytes, not a metadata hash. Keep all journal IO outside Firestore locks.
[Generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions),
[retention metadata limits](https://docs.cloud.google.com/storage/docs/bucket-lock).

The SDK upload path may try to delete after multipart checksum corruption. This stage caps
small intents and verifies exact bytes independently; it must neither require nor grant
runtime delete rights to reconcile a bad object. Corrupt immutable evidence denies and
needs operator handling. Storage SDK is pinned separately for the actual call-shape tests.
[Blob upload/download behavior](https://docs.cloud.google.com/python/docs/reference/storage/latest/google.cloud.storage.blob.Blob),
[retry/precondition behavior](https://docs.cloud.google.com/storage/docs/retry-strategy).

Journal-only opening/key ownership becomes a conservative permanent hold during closed
replay; journal-only revocation becomes a deny. Missing completion markers never prove no
begin. Denies/key ownership/uncertain holds do not expire with a permit. New epochs/devices
cannot namespace away old ownership. A revoked key remains owned by its original lifetime.
This stage has no automatic journal replayer or claim/refund/application retry.

## External restore fence and closed cut

The mutable live witness must be **separate from the retention-locked create-only journal**.
A retained live object cannot simply be replaced on each state change. Proposed operator-
controlled witness is a separate GCS object/bucket with guarded compare-and-swap generations;
its signed body identifies database resource+UID, authority incarnation/epoch, OPEN/CLOSED
state and the protected replay-manifest receipt. Guard runtime has read-only witness access.
Its deployment pin names the exact live witness generation/content digest and signing trust.

Every authorizing/identity mutation checks the live unversioned witness against that pinned
live generation before and after Commit, with no cache/SQL fallback. Reading only an old
versioned witness would be unsafe because it can remain readable after close. Generation-
bound body reads follow a live-generation check. UID metadata is rechecked outside the data
transaction; that does not provide an atomic cross-service fence. Warm ordinary read-only
product pages/catalogs never call the authority. Cost/latency remain unmeasured.

Managed restore order:

1. Operator advances witness CLOSED; old pins fail. Independently close the old registry,
   drain bounded guard operations/short decisions, and apply the declared executor fencing.
2. Seal a protected replay cut only when authoritative writers are fenced. Inventory every
   partition/checkpoint/intention generation/digest, all current denies/ownership/unknown
   effects, unresolved intents and declared retention horizon. Cloud Storage listing is
   strongly consistent, but an unfenced multi-page scan is not one atomic snapshot.
3. Restore into a **new** named database; copied OPEN/control/head is never sufficient.
   Replay all conservative journal effects and retain tombstones/key/opening ownership.
4. Verify manifest coverage/projection under CLOSED, exact new resource+UID/incarnation and
   no omitted partition/gap/older-than-horizon backup. Only the operator creates a new OPEN
   witness/config pin. Old database stays CLOSED; old workers cannot acquire a new pin.

[Storage consistency](https://docs.cloud.google.com/storage/docs/consistency),
[restore destination](https://docs.cloud.google.com/sdk/gcloud/reference/firestore/databases/restore).

IAM propagation is delayed and is not immediate physical egress fencing. Late in-flight
journal intents cannot be treated as proof of a later permission; recovery must conservatively
cover any possibly issued effect and reject old incarnations. Unannounced import/rollback
that bypasses this procedure, a compromised guard/admin, and unrestricted paused executor
network access remain outside the proven model. Neither a UID check nor copied local head
establishes immunity to all authority rollback. Existing browser autosave cannot be recalled.

## Explicit core and scaling limits

- `AuthorityStore.transact` has no typed journal context/ambiguity interface. Created IDs
  inside existing callbacks cannot identify a prewritten protected intent across retries.
- Existing `head()` and `clock/observed` are global. This prototype preserves that meaning
  and declares a low-scale contention point; it claims no production throughput. Partitioning
  needs explicit service/schema/replay changes, retaining shared subject/delete and exact
  opening/key ownership race points. It must not silently change head/barrier semantics.
- Existing `RecoveryGuard.close` drops `incarnation`; legacy `open/barrier` only prove a local
  head. They must not control a production replay fence. Runtime adapter denies unsupported
  control/meta writes; future operator recovery preserves all identity/incarnation fields.
- Replica clock skew/trusted time, postcommit fixed decision expiry and future context/receipt
  integration need explicit proofs. Advisory/read-only pages remain outside this work.

[Scale/load guidance](https://docs.cloud.google.com/firestore/native/docs/best-practices),
[transaction quotas](https://docs.cloud.google.com/firestore/quotas).

## IAM and evidence needed before any factory enablement

Use separate guard, SQL API/worker, replay operator and retention/IAM identities. Only the
trusted guard may get/create/update authority records in the named database; no delete,
import/restore, IAM change or database lifecycle permission. SQL workers/clients receive no
authority write credentials. Firestore server SDKs use IAM rather than client Security Rules;
database IAM does not establish arbitrary per-document immutability. Trusted guard code and
protected external evidence remain necessary. [Server IAM](https://docs.cloud.google.com/firestore/native/docs/security/iam).

Journal writer needs only narrow create/get access; replay can list/get; runtime cannot
update/delete/restore/override retention or change policy. Witness write/CAS and retained
manifest creation belong to the recovery operator, never normal guard runtime. Retention/IAM
administrators remain separate. Record an actual staging identity matrix with allowed and
forbidden SDK calls, role conditions, exact generations/retention and denial results; a
mock PermissionDenied is not that proof. [Storage role permissions](https://docs.cloud.google.com/storage/docs/access-control/iam-roles).

Local evidence plan: actual pinned SDK request/response construction; buffered read order,
absence/immutability/type/alias conflicts, abort/fresh-time retries, truncated streams,
commit-then-timeout/pending-commit status, zero results/signatures after ambiguity, exact
GCS receipt/mismatch handling, immutable holds/tombstones and disabled default factory.
Native Java/Firestore emulator were unavailable in the initial local inventory. Root later
provided an owned, credential-free Docker emulator on `127.0.0.1:58877`, synthetic project
`hirewiz-local-authority`, CLI `573.0.0-emulators`, image digest
`sha256:1b083a6a9647024a98d4f38c2b51906a0f61ca36adcba11672b8933544962efe`.
Explicit insecure localhost transport and AnonymousCredentials establish data-RPC evidence
only. Historical v1 evidence covered six cases across two runs (five buffered/race cases,
then one coordinator case). V2 changes the coordinator's intercepted Storage response
fixture to supply exact size metadata and an additional generation-pinned metadata GET;
all six v2 emulator cases are pending the owner's combined rerun after integration. No
emulator call was made for this repair. Neither version's emulator evidence proves database
UID, IAM, retention, or production locking/durability. Intercepted/scripted SDK transports
remain separate contract tests.
The emulator differs in locking/limits/indexes and proves neither IAM nor retention. [Emulator limitations](https://docs.cloud.google.com/firestore/native/docs/emulator).

Required later staging proof: independent actual Firestore replicas + GCS journal; true
races/cancelled responses/process deaths, identity/IAM/retention, complete managed authority
and app-only restore drills, omitted-partition/late-intent/horizon rejection, load/clock/RPO/
RTO evidence and synthetic Chrome transport. Production stays unavailable until these gates
and genuine authentication/sealed review interfaces are satisfied. No provisioning,
credentials, resource mutations or employer actions are part of this stage.


## Implemented isolated boundary and observed corrections

Six new Python modules provide typed pins/intents/receipts, injected SDK RPCs, bounded
read buffering, create-only journal/live witness reads, and status-only conservative
coordination. The standalone `backend/requirements-authority-sdk.txt` pins the proof SDKs;
application dependency/deployment configuration is unchanged. No legacy v1/v2/pairing
file, API route, browser extension or `production_store()` is modified. Genuine auth,
sealed-server scope derivation, complete protected manifest/replayer and existing core
operation contexts remain prerequisites for a later bridge. Records are holds/ownership/
tombstones; this coordinator does not resolve action grants or emit may-act values.

Known permanent namespaces enforce immutable writes even when callers omit the optional
flag. Stable operation receipts require their exact retained effect/event and a compatible
bounded global head. Missing partial projections fail closed; runtime does not repair an
operation receipt from surviving records. Status reads inspect a fixed set of five records
and exact journal generation bytes, never all history. A missing receipt after uncertainty
remains UNKNOWN. This is bounded projection consistency, not full replay/restore proof.

The emulator's zero-write Commit reply omitted `commit_time`. The two failing orchestration
assertions and diagnostic reply shapes are retained. Strict post-send mutation reply
validation remains intact. A verified exact replay now ends as a read-only snapshot using
rollback, returning no callback value and creating no mutation/permission receipt. Actual
writes still require confirmed complete Commit evidence and a fresh external fence check.

Storage SDK 3.13.0 can start a background bucket-metadata lookup with default retry/10s
timeout despite object methods passing `retry=None`. Its pinned implementation provides
`DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA`. The adapter requires exact `true` for
real injected SDK buckets before IO, a pinned SDK version, and an empty metadata cache
for that bucket. Tests prove no incidental bucket GET or DELETE on the intercepted call
paths. A previously populated cache is rejected because 404 eviction can still probe a
bucket. This stage checks a private pinned cache API, which is version-sensitive; it is
not a public compatibility promise. A fresh dedicated client, no earlier/shared in-flight
metadata work, and actual credential/transport/configuration verification remain factory
gates. No environment flag enables production authority.
[Pinned metadata flag](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-storage-v3.13.0/packages/google-cloud-storage/google/cloud/storage/_opentelemetry_tracing.py),
[pinned helper behavior](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-storage-v3.13.0/packages/google-cloud-storage/google/cloud/storage/_helpers.py),
[pinned cache behavior](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-storage-v3.13.0/packages/google-cloud-storage/google/cloud/storage/_bucket_metadata_cache.py).

V2 repairs a confirmed v1 media-read defect: v1 downloaded an entire preexisting object
before its exact-byte comparison. V2 validates fresh live and exact-generation metadata
before media IO. It rejects raw size/generation values that the SDK's public integer
accessors would otherwise normalize. The media request pins generation and precondition,
uses `Range: bytes=0-N` for expected length N (one extra sentinel byte), and disables
redirects and SDK retries. A per-download client view owns only its returned response;
the shared client/session is never replaced, reconfigured or closed. Its raw-body proxy
reads at most N+1 application bytes, never delegates the SDK's larger streaming chunk,
and denies any extra byte before it reaches a sink capped at N. Truncated or different
bytes deny. The proxy rejects preconsumed, encoded, redirect and error responses without
materializing their bodies and closes owned responses on both success and exceptions.

This bound applies to application media-body reads and the sink, not kernel/TLS/socket
read-ahead, SDK metadata/upload response bodies, or a trusted injected transport that
has already materialized a response (which is rejected). Private SDK `_properties`,
client `_http` and response `raw` interfaces are used deliberately with Storage 3.13.0
pinning and intercepted real-SDK regressions; they are not a public compatibility promise.
[Pinned download implementation](https://raw.githubusercontent.com/googleapis/google-cloud-python/google-cloud-storage-v3.13.0/packages/google-cloud-storage/google/cloud/storage/_media/requests/download.py).

The supplied Storage timeout is a connect/read inactivity timeout, **not a hard total
download or operation deadline**. A peer delivering bytes slowly can exceed that value
in total elapsed time. Registry retry deadlines are checked between RPCs; they do not
cancel an in-flight Storage request. Journal IO remains outside Firestore transactions,
and subsequent freshness/fence checks cannot turn a slow call into a wall-clock bound.
Actual transport cancellation, credential/configuration verification and a proved total
operation budget remain factory-enablement gates. This repair claims bounded media reads
and fail-closed receipt validation, not a hard wall-clock deadline.
[Requests timeout semantics](https://requests.readthedocs.io/en/latest/user/quickstart/#timeouts).

Generation/revision wire values in this new slice are canonical positive decimal strings
bounded by Storage's signed 64-bit generation field; they remain strings above JavaScript's
safe integer range. Legacy integer revocation/schema compatibility needs a separately
reviewed bridge. The global event counter is bounded by the core's canonical safe-integer
range and cannot reset silently.
[Storage generation fields](https://raw.githubusercontent.com/googleapis/googleapis/master/google/storage/v2/storage.proto).

Final evidence records the synthetic faults, initial fixture/emulator test assumptions,
SDK wheel-ABI repair in the isolated target, source hashes and preserved baseline. It
does not claim production authority, IAM separation, protected retention, genuine IdP
pairing, paused-worker fencing, clock/load limits, a closed-cut restore drill or employer
actions. Those requirements must pass in staging before the unavailable factory changes.

## Root integration and corrected emulator proof

The reviewed v2 patch was integrated into the current root. Root independently inspected
the metadata size checks, per-download client view, raw-reader byte limit and response
cleanup. The combined four-file test command passes **196 cases without skips**, including
all **six corrected Firestore data emulator cases**. Ruff passes on the ten new source/test
files and Mypy on the six new modules. The
[root evidence](evidence/2026-10-09-gcp-sdk-root-integration.json) records the frozen patch
hash and integrated source hashes. Historical v1 proof is retained separately.

Root adds `HIREWIZ_AUTHORITY_EMULATOR_REQUIRED=true` in CI: absence of the disposable
localhost emulator fails the test instead of silently skipping. Optional local development
without that flag can still skip with a stated reason. This changes only the test fixture.
Root's canonical `backend/pyproject.toml`, `uv.lock` and exported `requirements.txt` now
include Firestore 2.34.0; the existing Storage 3.13.0 lock is retained. The standalone SDK
requirements file records the isolated proof environment and is not an alternate
application installation path. Whole merged verification and immutable release checks
remain separate from this focused integration result. Production authority stays unavailable.

## Final root component integration

The corrected default root suite passed **1,261 tests with no failures, errors or skips**.
[The integration report](ROOT_COMPONENT_INTEGRATION_2026-10-09.md) retains the earlier
failed stage and distinguishes local proof from zero-traffic compatibility staging.
Production money cutover and browser authority remain unenabled.
