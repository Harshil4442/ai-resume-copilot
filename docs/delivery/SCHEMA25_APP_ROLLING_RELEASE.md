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

## Verification checklist within Stage 7: completed v1 baseline

- [x] Distinct app-only plan and fixed native write scope; old migration guards untouched.
- [x] Bidirectional actual prior8cb/candidate PostgreSQL17 compatibility rehearsal.
- [x] Exact CI step, source/build/digest, current policy and native DB/ACL proof requirements.
- [x] Pilot flags and existing native resources preserved; mutable pilot balances permitted.
- [x] Unknown provider holds preserved, new wallet/active AI/catalog drift refused.
- [x] One-send interruption/reconciliation and traffic-only rollback tests.
- [x] Baseline candidate merged with successful exact five-job CI and immutable build proof.
- [x] Baseline private native plan captured/reviewed and source/resource/financial observations pass.
- [x] Baseline candidate staged, HTTP health checked and promoted; fresh production acceptance recorded.

On 29 October 2026, the v1 application rollout completed for source
`ab2deadd1b1cecbe9cb10c73806dbcde851bf5e2`, merged as
`7b63ba67a19c6ec00a659082d1ad8487515e037b`. Exact five-job CI `40896668321`
and Git-source Build `3da6ce3e-b592-4955-9027-f1e64663d259` qualified image
`sha256:acfb2c50c96cdcd8cc1823217e030128b92ae809c3b33eabc9b60fcc264b7645`.
Native production readback at 17:50 UTC observed 100% of each service's traffic
on that source, with schema25 and the existing pilot/closed launch flags retained:

| Service | Observed generation | Serving revision |
| --- | ---: | --- |
| API | 352 | `ai-resume-parser-r-ab2deadd1b1c-a2ef354c` |
| Analysis worker | 75 | `hirewiz-analysis-worker-r-ab2deadd1b1c-a2ef354c` |
| Employer worker | 37 | `hirewiz-employer-worker-r-ab2deadd1b1c-a2ef354c` |

The temporary Tasks-service-account health-token grant was removed and its
absence verified at 17:47:40 UTC. A subsequent signed-in synthetic PDF upload,
original preview and byte-identical original download succeeded. This is a
single functional acceptance, not a capacity or AI-quality qualification.
Retained zero-percent 8cb tags still need the separately qualified cleanup below.

These checked runtime items apply only to the completed v1 baseline. The next v2
release and the overall Stage 7, financial, quality and launch gates remain
incomplete. This document does not authorize a cloud action.

## Prepared v2 extension for the next reviewed application

The v1 contract and its original PostgreSQL test remain byte-identical. Exact
schema-contract dispatch selects v1/v2; v1 serialization, marker names, request
hashes, one-send and rollback rules remain. Shared helpers receive an explicit
prior/profile; no global source pin is reassigned.

V2 pins actual prior `ab2deadd1b1cecbe9cb10c73806dbcde851bf5e2`, Build
`3da6ce3e-b592-4955-9027-f1e64663d259`, CI `40896668321`, image
`sha256:acfb2c50c96cdcd8cc1823217e030128b92ae809c3b33eabc9b60fcc264b7645`.
Its actual 262-file app digest is
`5d09318281793a9d9f46827ca19194164579f1c45e78b36d2d48b4655b326b89`.
Capture requires fresh native observation that ab2 actually serves. No future
candidate source/Build/CI/image is invented; each needs its own reviewed proof.

The v2 fingerprint binds the complete reviewed candidate app, exactly eight
optional response additions, qualified transport sources, locked SDKs, tests and
workflow commands. Other native persisted/session/witness/replay/document/task
protocols keep their bytes. Dependency upgrades and unlisted schema changes
refuse. The reviewed owned-bootstrap GET, projection and BFF/client sources are
bound separately: exactly `/api/auth/bootstrap` and its two response components
are additive to the prior HTTP contract. It grants no paid or model rights.
Both new Stage5 flags remain absent/false. Deploying code does not enable
pacing/List. Existing pilot flags, closed money/model/free-core/checkout/submit
gates, numeric secrets, IAM and native resource/document pins remain protected.

### V2 promotion aliases and V4 worker validation

Only the qualified v2 document-resource observer can select the existing V4
worker discriminator. It selects that discriminator from the exact observed
policy, after the unchanged fresh native Build/Job acceptance check. Unknown
policies and missing native acceptance still refuse. The fixed historical base
image cannot claim V4 merely by changing its policy literal. V1 and other release
observers keep their previous policy checks. Both shared validation and traffic
helpers are included in the v2 qualification fingerprint.

V2 promotion moves every already captured prior tag to the candidate revision
in the same promotion traffic PATCH. Each tag/type is preserved at zero percent;
the candidate's reviewed tag receives 100 percent. No tag can be invented or
changed, and no revision or image is deleted. This leaves the application aliases
homogeneous for document maintenance once all three app services are promoted.
Staging keeps the exact original allocations; maintenance continues to refuse
that temporary mixed state. The actual candidate CI/Build/image tuple must also
be reviewed in the maintenance registry on main before maintenance can accept
that application source. No future tuple is predicted here.

The captured prior traffic and serialized plan are unchanged. Ordinary v2
rollback restores those original ab allocations with its existing single PATCH,
generation and journal rules; no separate post-promotion alias retirement or
recovery plan is needed. Default/v1 promotion continues to retain its prior
tagged revisions exactly as before. The separately qualified zero-percent 8cb
cleanup is still required before the first V4 maintenance transition from the
current mixed 8cb/ab state. These are source rules, not deployment acceptance.

### Actual qualification contexts

CI runs the unchanged v1 test in an owned detached actual-ab2 checkout. Current
default collection excludes only that retained-scope file; its original isolated
`--noconftest` command actually runs there. No rewritten test presents this new
application as byte-identical. Mandatory v2 steps instead prove actual
ab2↔candidate PostgreSQL17 resume/search/outbox/ownership/liability exchange,
plus exact installed SDKs, real GAPIC message construction, an owned Firestore
data emulator and corrected Redis wire tests. Missing dependencies, required
services, failures or skips refuse qualification. These tests cannot prove
production UID/IAM/retention/durability, invoice/quality gates or 10,000-user capacity.

The wire test starts its own loopback TLS/RESP2 Redis process with fresh data,
port, PID and short-lived localhost certificate; it cannot adopt a shared Redis
URI. CI prepares official Redis 7.2.7 source at archive SHA256
`fdbdee0a19a12e61c2eca46f4162f6d3ede3a01e0baa8a12cee1f35f3391f1e9`,
verifies the download before extraction, and bounds the TLS build to 300 seconds
under a private `RUNNER_TEMP` directory. Missing OpenSSL headers or binary stops
the mandatory qualification. Cleanup removes only that exact owned source;
the fixture terminates only its verified owned child, never another listener.

The separately qualified payment status poll now permits eight adaptive automatic
reads plus one explicit manual status read; the historical 24-at-2.5-second
estimate is superseded. Real aggregate journey/load acceptance against the
40-unit pacing budget remains open. `STAGE5_OWNED_HTTP_CAPACITY.md` describes the
bounded owned-local experiment and remaining native evidence. No compatibility
receipt converts local measurements into qualified production capacity.

### Finite old-tag retirement

After the actual rollout observation, zero-percent 8cb tags can prevent a
homogeneous next prior and document maintenance. The separate finite alias plan
requires 100% active ab2 and an existing ab2 health tag. It freshly verifies both
source/Build/CI tuples, operator source/five-job CI, native identity,
funding/catalog/liabilities, secrets, IAM, resources/documents, revision UIDs,
templates and traffic. Its only write removes exactly captured zero-percent 8cb
tag allocations by etag-fenced traffic PATCH. All ab2 allocations, revisions,
images, templates and resources remain. No future CI ID is prefilled.

The existing CLI adds `aliases-capture`, `aliases-observe`, `aliases-send` and
`aliases-reconcile`. Capture takes actual operator source/CI, a fresh nonce,
existing numeric observer/retired-secret references and document source. Keep
the private plan SHA and separate owned journal. Intent/etag/request hashes
precede one send. Unknown outcomes require read-only exact reconciliation,
which never resends or starts the next action. Native drift or failed ab2
health stops further writes.

Before any settled-service proof or further action, reconciliation validates
all retained intents, acknowledgements and completion records for the three
services, then reads only their exact retained operations. A pending operation
on any service is reported without health or completion authority, including
when an earlier service already completed. Missing acknowledgements never
authorize operation discovery, adoption or a repeated PATCH; terminal results
still require the full fresh settled/native/health proof.

Any cleanup generation change makes the completed original app plan historical;
never replay it. Capture fresh homogeneous ab2 metadata for the next release:
`capture --contract hirewiz_schema25_app_rolling_v2`, then existing
observe/stage/advance/reconcile/rollback on its exact new plan. Production
cleanup/promotion, Stage5 activation and native acceptance remain pending.

### Paired frontend dependency

The new frontend needs the additive bootstrap GET. Deploy and verify the API
first, then deploy the reviewed frontend. Existing frontend calls remain
supported by the new API. A new frontend against old ab2/8cb fails closed with
404; there is no silent HTTP fanout fallback or shared authority cache.

Before API rollback after frontend publication, first promote the actual
retained compatible frontend deployment and verify it. Then restore API traffic
using the exact app plan. Record real frontend source/deployment artifacts when
they exist; none are fabricated or prefilled here. V2 operator receipts cover
only API/worker traffic and explicitly leave frontend/end-to-end recovery
unqualified. This is an ordinary paired runbook dependency, with no new
cross-provider orchestration framework.
