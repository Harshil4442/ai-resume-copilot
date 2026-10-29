# Supported-format quality evaluation v2

This operator-only runner prepares and executes genuine production interview and
resume-tailoring operations against the frozen synthetic v2 packet. It does not
qualify a retail route, activate AI, grant customer credits, approve a reference,
create a human score, or change a native image/protocol. The original ten-PDF v1
development runner remains unchanged.

## Fixed scope and current blockers

- The pinned packet has 200 cases: 50 PDF, 50 DOCX, 50 TeX and 50 TeX ZIP;
  40 development and 160 held-out cases. Each operation has 400 paired slots.
  Its complete metadata plan always includes all 200 case IDs and hashes.
- An execution authorization selects exactly one operation and one split, and
  both `reference` and `candidate` sides. Development execution has 80 slots;
  held-out execution has 320. It never silently samples ten easy PDFs.
- Development reads/hash-checks the full frozen byte stream but deserializes only
  its 40 development cases. Do not inspect held-out inputs or outputs for code,
  prompt, parsing, native-format or model tuning. The coordinator may export
  held-out results into a private review package only after its development lock.
- Both routes must pin this exact current production operation/prompt contract.
  Different approved existing Google model quotes can be compared. This runner
  does not execute old application code or silently label an unapproved output
  as an approved historical reference.
- Genuine v2 worker captures, rendered-page/native reviews, model outputs and
  independent human reviews have **not** been supplied by implementing this
  runner. Existing materialization/render observations alone are insufficient.
  The observed two-column reading-order failure remains a blocker. No repair
  reconstructs native text from expected fixture text to hide that failure.
- The other AI operation families, retail paid-route qualification, statistical
  noninferiority analysis, reviewer assignment/blinding, invoice reconciliation
  and production canary activation remain separate existing acceptance work.

## Trust and funding boundary

An operator reviews a finite authorization, its private funding/balance artifacts,
both exact provider quotes, current model and expense policies, native deployment
binding digest, corpus hashes and input capture registry. The JSON schema is
`backend/evals/unified_credit_v2/supported-evaluation-authorization.schema.json`.
`state=draft` may plan/preflight; execution requires an explicit `authorized`
allocation lasting at most one day and a fresh provider balance observation.
No example/test artifact is an actual funding or provider-balance receipt.

The runner only accepts `postgresql+psycopg` on `127.0.0.1`, an explicitly pinned
port/user and database named `hirewiz_quality_evaluation_v2`. Authorization binds
the server-observed `pg_control_system().system_identifier` and database OID.
Each experiment has its own `hwqv2_…` schema; the live provider scope also binds its
actual schema OID, process, thread, asyncio task and exact run. A stored v2 label,
development environment or disabled public canary is insufficient authority.
Do not point the runner at a customer database. Only the disposable local schema
is created; there is no production migration or cloud provisioning action.

Model calls reuse `persistent_run_budget`, current provider request limits and
detached `ModelCostLiability` rows. Only the side's exact quote is frozen for its
run, so an unreviewed fallback cannot generate. Interview permits one provider
attempt; tailoring retains the production maximum of three. Complete split
funding covers both sides at these declared bounds before any claim. Estimated
token/cost admission is not a claim that an invoice cannot exceed an estimate.

Each accepted slot also retains a positive INR infrastructure allocation in an
existing `FreeCoreReservation` row under `internal_quality_v2_not_retail`.
`settled_minor` stays unknown; neither success, failure nor deletion implies zero
native cost. The maximum is a reviewed **reserved allocation**, not a measured
native bill or a guaranteed infrastructure expense cap. These internal rows do
not grant public free-core entitlement and cannot qualify retail unit economics.
No native settlement/invoice amendment is fabricated by this runner. Use a
separately reviewed actual-cost reconciliation process when genuine receipts
exist; retained unknown liabilities must continue to count as held.

The local runtime must explicitly have `APP_ENV=test|local|development`,
`HIREWIZ_INTERNAL_EVALUATION_ENABLED=true`, the existing optional-generation gate,
reviewed cost policies and the selected model's private credential. The runner
does not set those flags, create keys, print credentials or open public traffic.
Existing native clients retain their own endpoint/image/isolation checks; their
deployment inputs are additionally hash-bound in `native_runtime_sha256`.

## Native imports

Inputs are original native bytes from the pinned materialization manifest plus:

1. A hash-bound native-validation map in the existing quality packet format:
   source/artifact hashes, actual extracted-text artifact, PNG render artifacts
   and a visual reviewer bound to every page hash. All required native checks
   must be approved.
2. A separately hash-bound capture registry with
   `custody=operator_reviewed_native_capture_v1`, `reviewed_by`, `reviewed_at`, the
   packet manifest hash and per-case `source_sha256`, `request_correlation_id`
   and `worker_response: {path, sha256}`. Response paths are relative to the
   registry's directory. This is an explicit **operator-reviewed capture trust
   boundary**, not a cryptographic provider signature or automated attestation.
3. The exact raw production worker response. The existing pure native decoder
   verifies source bytes, size, format, worker image/policy and scanner metadata.
   The reviewed extracted text must equal that actual response. Complete source
   paragraphs must occur in source order; positive JSON pass booleans cannot
   override interleaving or truncation.

TeX and TeX ZIP require a **composite capture**. The original source inspector
response must have the production blank admission shape: no text, sections,
skills, experience or non-null contact facts. It does not supply résumé content.
The per-case registry's `compilation` object must additionally contain:

- `source_sha256` of the exact original source/project and its own
  `request_correlation_id`;
- `worker_response: {path, sha256}` for the existing compiler response, including
  its exact request digest, renderer/image/policy and PDF bytes/hash/size;
- `pdf: {path, sha256}` for those identical compiled PDF bytes;
- `pdf_inspection_response: {path, sha256}` for the separate original native
  inspector response over that PDF.

Authorization separately pins `compiler_image` and `compiler_policy_sha256`.
The pure import reconstructs the unchanged compiler request from the original
validated project and verifies its request hash plus response contract. It does
not compile anything or invent a worker receipt. The compiled PDF inspection
provides the parsed tuple, and its exact text must equal the reviewed extraction.
The native validation record must bind `inspected_artifact_sha256` to that actual
PDF, while its original source/artifact hashes continue to identify the uploaded
TeX project. All source/compile/PDF-inspection/review artifacts remain under the
same explicit operator-reviewed capture trust; they are not provider signatures.
Missing compilation provenance, nonblank source-admission facts, a substituted
PDF or reading-order failure refuses the run. No native image/protocol changes
or source-text fallback are introduced.

The real Resume row is seeded from that decoded native output, **not** the
expected fixture's raw text. Existing Resume, Opportunity and approved Evidence
rows are then used by the actual production operation. Each call revalidates
their frozen source/job/evidence against the capture and case, even if somebody
recomputes a stored dependency fingerprint after editing them. Missing capture,
native review or reading-order evidence refuses prepare/execute before model work.

## Held-out lock and finite execution

Before authorizing held-out work, retain a private immutable lock artifact with:

- `state=locked_before_held_out`, exact `manifest_sha256`, `protocol_sha256`, `rubric_sha256`,
  operation and both complete route objects;
- `locked_at` no later than the held-out authorization's start, and `reviewed_by`;
- `development_export: {path, sha256}` referencing all 80 completed development
  slots from this runner and operation;
- `review_evidence: {path, sha256}` and
  `development_native_and_human_review=approved_for_held_out_execution`.

That is an operator-reviewed permission for held-out evaluation, not automatic
approval of statistical quality or a retail route. Freeze the reference, model,
prompt, rubric and analysis plan before any held-out execution. Never change a
held-out lock to incorporate knowledge from its outcomes.

Run from `backend` with its normal installed dependencies, using reviewed private
paths. These examples intentionally contain no active authorization/credential:

```sh
python scripts/run_supported_evaluation.py plan --authorization /private/review/authorization.json
python scripts/run_supported_evaluation.py dry-run --authorization /private/review/authorization.json --native /private/materialized --native-receipts /private/review/native-map.json
python scripts/run_supported_evaluation.py prepare --authorization /private/review/authorization.json --native /private/materialized --native-receipts /private/review/native-map.json
python scripts/run_supported_evaluation.py execute --authorization /private/review/authorization.json --native /private/materialized --native-receipts /private/review/native-map.json --side candidate --maximum-operations 1
python scripts/run_supported_evaluation.py export --authorization /private/review/authorization.json --destination /private/new-export
```

`plan` and `dry-run` make no provider/native/database requests. `prepare` alone
creates local synthetic rows and held expenses. Set the dedicated local URL in
`HIREWIZ_EVALUATION_DATABASE_URL` privately; never paste it into chat or commits.
`execute` requires an explicit side and accepts only 1–10 never-started operations
per invocation. It commits the claim before network work, uses SKIP LOCKED for
concurrent claimants and calls the actual production operation. The complete
tailoring route requires the exact internal scope, without invented retail
qualification/credits. Source files stay original; real source-preserving versions
are separately saved. Missing actual settled provider usage cannot become success.

Only never-started `queued` rows may be resumed. Running, failed, interrupted or
unknown rows are never redispatched, and their expenses remain. After an unknown
database acknowledgement, inspect the exact run/ledger before any new command;
do not assume failure means nothing happened. A different authorization cannot
replace the first allocation in the same experiment schema.

## Exports and verification

Exports require a fresh absolute private directory (0700; files 0600) and retain
every selected slot, including failed/absent outputs. They include actual model
liabilities, held native allocations, original source/case hashes and any successful
output's hash. Tailoring exports the real native downloadable artifact using the
production renderer; native-output review remains explicitly pending. Exports
remain available when generation is paused or the prompt subsequently changes.
The coordinator export contains side/model provenance and is **not** a blinded
reviewer package: separately blind it before independent scoring. Do not show the
coordinator mapping or another reviewer's scores to reviewers.

Tests use an owned dynamic-port disposable PostgreSQL 17 cluster, real production
operations/budget/erasure and synthetic model/native capture fixtures. They prove
durability, invariants and format behavior on test data; they cannot satisfy
genuine native/provider/human acceptance. The fixture verifies its own data
directory, PID and native server identifier before stopping only its server. It
never uses existing servers on 55433/55434. Retain local test evidence separately
from real evaluation receipts and leave public quality/checkout flags unchanged.
The TeX positive tests run the real native processor/parser over development
fixture bytes, with explicit synthetic scanner/compiler transports; the source
response stays blank and actual inspected PDF text is seeded into PostgreSQL.
They establish contract integration, not genuine AV/compilation/render/human
acceptance. The actual two-column parser refusal is retained as a negative test.
