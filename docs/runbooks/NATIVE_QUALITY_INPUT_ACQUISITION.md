# Native quality input acquisition: first usable scope

This local operator tool captures actual responses from the **existing private
Cloud Run services**. It creates no Cloud Run Job, database or model
request, IAM binding, key, account or customer credit. The optional `execute`
command sends bounded HTTP document-inspection/compiler requests; it therefore
incurs native processing expense. Plan and dry-run perform local checks only.

The initial scope is all **40 frozen development cases**, with original source
bytes and case identities preserved. The complete plan retains all 200 case
IDs/hashes and the 40/160 split. Held-out acquisition is deliberately unavailable
in this first version; the final 200-case requirement remains pending. Do not
inspect held-out content to repair this tool or the parser. Do not rebuild the
sources, change the oracle, exclude difficult cases or call 34/40 qualified.

## Local acquisition permit

Use a separately reviewed private JSON instance of
`native_input_acquisition.Authorization`, with `schema_version` equal to
`development-native-service-acquisition-v1` and purpose
`unreviewed_native_input_capture_no_model_no_retail`. The closed Pydantic model
declares the exact fields; it grants no backend/provider identity or IAM access.
The existing model-evaluation permit depends on already-created capture hashes
and cannot honestly serve as this earlier input-acquisition permit.

The local permit binds:

- A draft/authorized state, named operator, experiment ID and timezone-aware
  window of no more than 24 hours.
- The exact absolute journal path. The same permit cannot choose another
  journal to bypass an existing intent. Keep its entire directory and any
  unknown outcomes; never delete/reset it to restart.
- The exact frozen packet manifest and materialization-manifest hashes.
- The private `/inspect` and `/compile` URLs, immutable image digests and
  corresponding policy hashes, reviewed against the deployed native custody.
- A hash-bound real funding-evidence artifact, positive INR reserve per request,
  request count and total native allocation covering the entire selected split.

These are reserved allocations, not a measured bill or a guarantee of maximum
infrastructure cost. Actual native cost stays unknown until separate invoice
reconciliation. No synthetic funding document from a test is live authorization.
The permit supplies no credentials. Original production clients obtain their
existing private service identity in memory; no token is printed or retained.

## Commands and one-send behavior

Run from `backend`, using its installed dependencies and private reviewed paths.
The example paths below are descriptive, not existing permits or receipts.

```sh
python scripts/acquire_native_quality_inputs.py plan --authorization /private/review/acquisition.json --native /private/original-sources
python scripts/acquire_native_quality_inputs.py dry-run --authorization /private/review/acquisition.json --native /private/original-sources
python scripts/acquire_native_quality_inputs.py execute --authorization /private/review/acquisition.json --native /private/original-sources --journal /private/exact-journal --initialize --maximum-requests 1
python scripts/acquire_native_quality_inputs.py status --authorization /private/review/acquisition.json --native /private/original-sources --journal /private/exact-journal
python scripts/acquire_native_quality_inputs.py export --authorization /private/review/acquisition.json --native /private/original-sources --journal /private/exact-journal --destination /private/new-unreviewed-export
```

Initialize once. Subsequent execute commands use the same journal without
`--initialize`. Each command permits 1–10 never-sent HTTP requests, default one.
An exclusive local lock prevents simultaneous senders. An exclusive fsynced
intent is written **before** acquiring a service token or invoking the transport.
The intent binds source/original-source/request/permit hashes and its held native
reserve. Exact raw successful response bytes and their hash are retained before
recording a validated, unreviewed result. Original response bytes are not
reconstructed from a parsed dataclass or expected fixture text.

PDF and DOCX use original inspection. TeX and TeX ZIP use original blank source
admission, original compilation and a separate inspection of the exact compiled
PDF. Requests use the existing production serialization, transports and pure
response/import decoders. All original size/format/image/policy/scanner and
compilation request/hash checks remain. No local parser or compiler fallback
replaces the service. The TeX transport's raw-capture wrapper shares its existing
single request/validation path; ordinary production callers still receive the
same decoded dictionary.

Any lost response, interruption or storage failure after intent creation leaves
that step **UNKNOWN** with its native reserve held. All new sends then stop.
Status inspects retained local evidence only. There is no original result lookup
or idempotency endpoint in either service, so no automatic reconciliation,
reconstruction, retry or new journal is a valid recovery. Even a response saved
before an interrupted acknowledgement remains unknown in this conservative
version. A verified service refusal and invalid response remain failed and
cannot be retried; other untouched cases may proceed. Compiler unavailability is
unknown, and is never mistaken for a verified refusal.

Resume/status/export recheck the exact original plan, sources, intent and
retained response bindings. A result label without its intent/raw response
cannot unlock a later phase. Changed sources, policy bindings, code, journal
path, raw bytes or result metadata refuse further work. Local files/directories
are private and owner-bound; symbolic/hard-linked journal artifacts are refused.
This is operator custody, not protection against an administrator replacing an
entire local evidence tree.

No Job/Execution ID, remote cleanup or successful deletion is claimed. Local
intent IDs are explicitly **local correlation**, not provider operation IDs.
The tool has no cleanup/reset command; preserve original sources and the journal.

## Unreviewed export and remaining acceptance

Export requires a fresh private directory and copies actual hash-bound source
responses, compiler responses/PDFs and PDF inspection responses. The unchanged
native importer validates the composite. It records the existing strict
development reading-order result without editing expected text or accepting
visible hyphens as equivalent. The six retained development failures remain
failures until actual independently verified remediation resolves them.

The registry custody is intentionally `unreviewed_service_http_capture_v1`, not
the existing trusted `operator_reviewed_native_capture_v1`. It contains no
reviewer identity or fabricated native approvals. Partial/failed/unknown cases
remain in the accompanying status, and no denominator is reduced.

An operator must separately verify the capture provenance and supply the
existing native validation map: actual extracted text, genuine rendered PNGs,
all structure/render/reading-order/link/layout/source-preservation checks, and
reviewer identity/time bound to **every page hash**. This tool does not fabricate
or auto-approve those artifacts. Only that real review may produce the existing
trusted registry and native map for `run_supported_evaluation.py dry-run`.

Complete native development input approval is required before funded model
execution. Two independent blinded reviewers plus a distinct adjudicator,
complete development exports and an immutable reference/model/prompt/rubric
lock are still required before held-out work. Actual held-out acquisition,
model quality, cost reconciliation, retail qualification and public launch
remain separate pending requirements; source-exact captures alone satisfy none
of them. Tests use explicitly synthetic transports and prove only local custody,
one-send behavior and protocol integration.

## Source-only durable object adapter

`backend/scripts/native_acquisition_channel.py` supplies the operator adapter seam
for an ephemeral runner. It is not a Job dispatcher, new execution permit, native
proof issuer or page renderer. No actual runner, supplier allocation, principal,
bucket grant, native Job/Execution or private-service journey is qualified by this
source change. The original local CLI remains available for its original scope.

The existing acquisition function has a small optional channel hook. It binds a
durable journal marker under the same exclusive local lock, acknowledges an
immutable phase intent **before** the original `ServiceTransport` may send, then
publishes the exact original raw response/retained/result/PDF files before the
next phase. A journal bound to this channel refuses execution through the
unwrapped CLI; it cannot silently lose its remote one-send fence.
The original trusted authorization window is checked again after the durable
phase ACK, immediately before calling the original processing transport. A late
ACK never extends that window: equality or expiry withholds the POST and leaves
the existing intent, UNKNOWN state and native reserve intact, without replay.

The adapter requires an independently reviewed channel binding and dispatch ACK
object, each with an exact object name, positive generation, size and SHA-256.
Its pure `binding(...)` function only proposes source/plan/byte-bound metadata;
it never writes those objects or authorizes processing. The closed dispatch
record binds a hash-checked external authority-review artifact, a named reviewer,
the exact acknowledged channel object, checkpoint, preceding completed batch and
one-to-ten-request cap. A JSON reviewer label is operator custody, **not** native
Job authority. The existing external native operator must first acknowledge and
bind the real immutable source/image, Job/Execution identity and original
create/run intent. No such live dispatch artifact is supplied by this patch.

The `GcsObjects` port accepts an already supplied, reviewed SDK client and an exact
`gs://bucket/quality-development/<experiment-id>/` namespace. It creates no client
or ADC session, lists no objects, and changes no IAM or resource. Writes use
generation-match zero with retries disabled; reads bind exact generations and
validate byte count/hash. It has no overwrite or delete operation. The binding
pins the actual acquisition plan, authorization hash, experiment, adapter source
hash and explicit output limits (at most512MiB and1024objects). Original source,
worker/compiler/scan/hash/size and importer checks remain unchanged. The512MiB
limit covers the development response/PDF channel, not a renderer/PNG channel.

Each batch first claims its immutable checkpoint key and finishes with an
append-only terminal manifest. The bounded parent chain retains the exact prior
dispatch ACKs and completed checkpoints. All80maximum development processing
requests retain their original monetary reservations; no actual supplier cost is
invented. Two independent runner replicas cannot both claim the same checkpoint
or phase. Request and output manifests contain local correlation only, without
invented Job IDs, execution UIDs or provider approval.

Any HTTP uncertainty, intent/publication ACK loss, interrupted result transfer
or missing terminal creates a private local stop marker and blocks new sends.
A remote phase intent without a complete terminal is restored as UNKNOWN, never
rebuilt from raw bytes. A partially published response/result remains retained
in the private prefix for investigation and cannot unlock a later phase. An
unfinished batch cannot become a parent for another dispatch. A consumed request
or held expense is never erased, replayed or treated as free.

`restore(...)` performs only exact-object reads into a fresh private directory,
rechecks the original journal/import contracts and adds quarantine metadata.
It does not authorize execution, reconstruct a missing terminal, erase original
evidence or approve the old dispatch for another run. `inspect(...)` and
`export_unreviewed(...)` likewise issue no processing POST. The unchanged export
retains partial/failed cases, all40selected IDs and pending native/page review.
Successful byte transfer never produces `operator_reviewed_native_capture_v1`.

There is one important boundary: an initial remote claim-create failure can lose
its ACK without creating an object, then the entire ephemeral disk can be lost.
No processing HTTP is sent without an acknowledged intent, but bucket absence
alone does **not** approve a new Job or replacement external dispatch. The native
operator's independently retained create/run intent must keep that execution
UNKNOWN and block fresh dispatch. Tests explicitly distinguish retained local
ACK-failure markers, immutable-object uncertainty and total local loss. This
adapter does not pretend to replace that external authority or repair uncertainty.

For a later approved runner, the operator may inject the original reviewed SDK
client, parsed channel/dispatch bindings and existing acquisition authorization
into `Channel(...)`, then call `execute(...)` once. No operational cloud CLI,
automatic ACK issuance, implicit client constructor or live example permit is
included here. The existing whole-split native funding requirement remains;
Gemini credits are unrelated. Current source/image/principal/IAM/internal-VPC
routing and actual native dispatch must be reviewed separately.

The original inspector HTTP protocol exports scanner/text JSON and the compiler
exports a PDF. Neither exports genuine PNG pages. Actual source-correlated page
rendering and per-page human review remain open, as do the six current development
failures and the full200-case requirements. This patch does not modify an oracle,
cohort, worker validator or held-out gate.

Qualification impact: the new operator module and its tests are separate from the
current immutable app release. The small hook changes
`backend/app/domains/analysis/native_input_acquisition.py`, so any future shipping
image containing these bytes needs a separate complete-app qualification. No
currently frozen candidate source, worker policy/image pin or deployment is
updated by preparing this adapter.
