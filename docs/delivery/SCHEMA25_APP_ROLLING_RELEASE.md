# Application-only release on the running schema25 pilot

This is the existing Stage 7 deployment work. Local implementation and synthetic
compatibility evidence do not mark the financial, quality or launch gates done.
No cloud action is performed by tests or by importing the operator.

## Bounded authority

Use `infra/gcp/schema25_app_rolling_release.py` for the first compatible application
release **after** the completed schema25 migration and pilot restoration.
Its distinct `App25Plan` cannot be used as a migration plan. The schema21→25
operators, guards and create-only migration journals remain unchanged.

Only the API, analysis worker and employer worker app image, `APP_RELEASE`,
source label, revision name and their traffic may change. The candidate must
retain all other template bytes, numeric secret references, runtime identity,
IAM, resource limits, command, environment and database binding. Existing launch
flags remain exactly as observed: the seven API pilot flags are true, and only
employer discovery is true on the workers. Optional AI, unified credits, funded
free core, payment checkout and final submission stay closed. No new financial
policy or secret is installed by this operator.

Every previously reachable app revision is independently pinned, up to the fixed
60-revision bound across three services. Each must retain the actual prior8cb
image/source, runtime identity, closed financial/AI flags and original immutable
configuration. A historical tag may retain fewer enabled pilot flags, but cannot
introduce a new capability or alternate secrets/command. Unknown sources and
new tags are refused; this operator does not clean up historical tags. The prior
API's account-lifecycle restoration generation is captured as observed rather
than hardcoded to a pre-restoration service generation.

The four queues, three schedules, document inspector, TeX service and resume
coordinator are read and pinned, including state, configuration, native service
identity, reachable revision templates, physical traffic and IAM. Their state is
preserved. Normal scheduler attempt timestamps may change. An unserved stale
`latestReadyRevision` after coordinator restoration is not substituted for the
actually serving and latest-created revisions. No pause/resume, secret, grant,
database DDL, model request, employer action or resource-access operation exists
in this application release flow.

## Required source and compatibility evidence

The actual prior application is commit
`8cbdfaeb9bb6cf6674c18ca8d59bc123fbf73a2a`, image
`sha256:956735cd476c1159397ef21143b497f0b4d40df4d47f6b5d54cd8818a544eafa`,
Cloud Build `45473be6-d177-49e1-a8a3-7970df8271db`, CI `40784200833`.
Native source, five-job CI and Git-build provenance for **both** the prior and
candidate are mandatory; labels alone do not qualify either image. The separately
pinned native document source remains its actual source, rather than being
relabeled as the candidate app.

The candidate backend CI must contain the exact named **Qualify schema25 app
compatibility** step and complete it successfully. Checkout retains Git history
so the rehearsal loads the pinned prior's actual application archive and checks
its byte digest. It imports old and new apps in separate isolated processes,
against real disposable PostgreSQL17, and exchanges work in both directions:

- Synchronous synthetic PDF parse, exact original source bytes and owned reads;
  a different owner cannot read the resume or its source.
- The existing saved official-source search payload and ownership boundaries.
- Actual `analysis.run` outbox payload and deterministic skill-ROI consumer;
  duplicate consumption does not rerun or charge the work.
- Existing balances remain unchanged through these free operations, no model
  call or liability is created, and new paid/free-core/AI routes remain closed.

The external document/feed response fixtures are synthetic. They prove wire and
storage compatibility, **not** real provider retrieval, document isolation,
model quality or financial invoice reconciliation. The native boundary separately
retains the existing fresh document image/policy/runtime acceptance proof.
The initially qualified API, analysis, employer, dispatch, native candidate
ingress/account/pairing and document/scanner wire source files must also remain
byte-identical to the prior. A protocol change
requires a separately reviewed compatibility qualification.

## Database and financial proof

The normal private credential manager supplies the existing exact numeric
runtime/observer secrets in memory. Native TLS transport, PostgreSQL cluster,
database/schema/role identities, exact schema25 catalogs and ACLs are rechecked
read-only. The retired migration credential remains disabled. New wallet/free
core tables must still be empty, and no queued/running AI-capable analysis or
active model call may exist in this initial scope. Deterministic pilot work may
continue.

`financial_sha256` in this **distinct** plan hashes the validated catalog and
invariant contract; it is not a frozen digest of every mutable financial row.
Nonnegative balances, valid reservation arithmetic and no prepared transactions
are verified live. Existing detached provider liabilities retain their exact
identity, pricing quote and positive reserved amount. Removing, re-quoting or
reducing a liability, or introducing new AI liabilities, stops deployment.
A legitimate late usage callback may settle an existing liability only when the
existing provider-usage provenance and quote arithmetic validate; changing a
state label to `settled` or inventing zero expense cannot release a hold.
SQL terminal state alone does not prove an external provider or native child
has stopped. Unknown outcomes remain held; this app rollout neither claims such
termination nor settles those expenses from SQL status.
Actual provider invoice qualification remains a separate external gate.

## Operator sequence and interruption handling

1. Merge/review the candidate and wait for its exact five-job CI, including the
   explicit compatibility step. Build its immutable image from the verified
   Git source. Do not deploy a local archive or a different source's image.
2. Create a private owned directory with mode `0700`. Run `capture` with a fresh
   nonce, exact candidate source/build/CI/digest, unchanged document source and
   numeric observer/retired secret references. Capture is read-only except for
   its exclusive local mode `0600` plan file. Retain its printed file SHA-256.
3. Review the finite plan and pins. Run `observe` with the original plan path and
   file SHA. Make a separate private journal for this exact app plan; retain all
   original migration, restoration and pilot journals unchanged.
4. `stage` creates at most one new revision per service at zero traffic, with
   fresh native readback and exact tagged HTTP health. `advance` can perform
   staging as well, then promotes the two workers before the API. The mixed old
   and new binaries are covered by the bidirectional compatibility rehearsal.
5. Each physical PATCH has one create-only started marker, request hash,
   preimage/etag, acknowledgement and native operation/readback/health proof.
   If a send or acknowledgement is uncertain, stop and use **only `reconcile`**
   with the same plan and journal. Reconciliation never starts a new action or
   repeats a PATCH. A still-pending operation is reported as pending.
6. If rollback is reviewed, `rollback` changes only traffic to the retained
   prior8cb revisions, checks the same identity/funding/native/database proofs
   and exact prior tagged health, and preserves schema25 and all customer work.
   A create-only app25 rollback intent fences all forward staging/promotion
   under the same journal lock. A broken candidate HTTP endpoint cannot block
   recovery: the exact preceding mutation's native metadata/LRO is reconciled
   independently, without inventing a successful candidate-health receipt.
   Staged-only failure can recover changed services while leaving untouched
   original services untouched. The physical service/revision metadata must
   still be settled and match its exact expected shape. Uncertain rollback
   acknowledgements require read-only `reconcile` before further rollback work;
   the traffic PATCH is never repeated.
   Partial rollback remains pending until all exact actions are observed.
   Forward promotion cannot resume once rollback has started.

Common options are `--root`, `--plan`, `--plan-sha256`, and `--journal`.
`capture` additionally requires `--release`, `--image-digest`, `--build-id`,
`--ci-run-id`, `--nonce`, `--observer-secret`, `--retired-migration-secret` and
`--workers-release`. Use `--help` for argument syntax. Never place plaintext
secrets or credential-bearing URLs in commands, plans, logs or review artifacts.

Every new or unresolved action rechecks the full proof. Only exact original,
staged, promoted and traffic-rollback template/traffic/generation shapes are
accepted; unrelated concurrent changes stop the flow. The plan does not adopt
already populated wallets, alternate prior apps or a release after this initial
prior8cb scope. Those require renewed explicit compatibility review.

Traffic rollback is application recovery evidence only. It does not qualify a
database restore, regional disaster recovery, a general RTO/RPO, commercial
hosting, the seven-day shadow-accounting gate or human AI-quality approval.

## Verification checklist within Stage 7

- [x] Distinct app-only plan and fixed native write scope; old migration guards untouched.
- [x] Bidirectional actual prior8cb/candidate PostgreSQL17 compatibility rehearsal.
- [x] Exact CI step, source/build/digest, current policy and native DB/ACL proof requirements.
- [x] Pilot flags and existing native resources preserved; mutable pilot balances permitted.
- [x] Unknown provider holds preserved, new wallet/active AI/catalog drift refused.
- [x] One-send interruption/reconciliation and traffic-only rollback tests.
- [ ] Candidate merged with successful exact five-job CI and immutable build proof.
- [ ] Private native plan captured/reviewed and source/resource/financial observations pass.
- [ ] Candidate staged, HTTP health checked and promoted; fresh production acceptance recorded.

The checked items describe tested implementation. The unchecked items need
their own retained runtime evidence; this document does not authorize a cloud action.
