# Executable fresh HireWiz cutover preparation

This document describes the supported preserved-data migration and deployment
executor. The source now includes schema 17, a normal SDK authentication handoff
and preparation checks for both private document workers. Source integration
has not executed a customer cutover, credential rotation, customer copy or
production promotion. Synthetic sequencing controls and the previously observed
Linux/PostgreSQL fixtures establish those tested boundaries only.

**Current execution state:** the unchanged source guard verified all 1,023 files
and the 16-revision migration chain of the preceding `29f86053` cohort. The new
source batch adds schema 17 and must receive its own complete guard, CI and
immutable application/operator builds after it is frozen. No files are excluded
from the source check. The normal read-only Docker preflight was attempted; its
Linux-mounted Google/GitHub stores failed authentication. A subsequent private
RAM handoff diagnosis authenticated both providers, the empty destination and
the actual source, then stopped because three employer queues were still
running. That diagnosis used two explicitly mounted public source fixes and is
not final full-source admission evidence. The supported RAM handoff still needs
a successful preflight against its exact full-source image.
No customer cutover has run.

Root also verified the supported host credential helper against the retained
normal accounts: Google returned an actual 3,598-second validity window and the
GitHub login was available. Only those coarse results were retained. This is
host authentication evidence; it does not establish Linux execution, customer
migration, private worker connectivity or final release acceptance.

## Exact source and destination

Source: Neon `little-pond-36610622`, branch `br-fancy-sunset-ao8i4a57`, literal endpoint `ep-damp-rice-aosknaqp.c-2.ap-southeast-1.aws.neon.tech`, database `neondb` OID16391, cluster SHA `cabc69e0dc6343b8474c3e4d7fc4ca48e8a93e45c1694d1961a93ee56e4385f0`, current schema9. Old application writer `neondb_owner` OID16387 is retired through the supported local Neon manager only after native SQL proves zero owned dependencies/sessions. The new source owner `hirewiz_source_owner_20261014_r1` is NOLOGIN; its OID must come from actual creation, never a guessed value. The prepared observer OID1884182 is the sole authenticated source export route after the transition. Source runtime/migrator placeholders are explicitly absent, because no corresponding numeric production secrets exist.

Destination: the already-created empty `shiny-cell-70214683`, branch `br-square-mode-b4g6sxrz`, endpoint `ep-weathered-tooth-b43oa1n6.c-6.us-east-2.aws.neon.tech`; databaseOID16395, bootstrap/initial ownerOID16391, publicOID2200 owner6171; cluster SHA `61273750d6782a6bc688488b47d274ce81589d8066d54f389e7773a834e7ccd7`. Runtime/migration/observer role OIDs24576/24578/24580 and setup numeric versions `hirewiz-fresh-{runtime,migrator,observer}-db-20261013:1` are native facts, not final credentials. The selected new versions must be returned by actual writes and genuinely authenticate after isolation. Verify-full TLS remains required. The actual original source URI uses `channel_binding=require`; the source/transfer contract now preserves that stronger mode. Prepared destination/observer bindings retain their explicit `prefer` mode. Hostnames and transport modes are checked against their own exact identities; there is no historical hostname redirection.

## One usable operation

The Dockerfile derives from immutable tool image `a86ea8a74c3f03d2da896eae68d7daf5c69872205376084a33852fac5252ca5a` and fetches the complete exact approved Git commit, retaining `.git`. It installs the frozen backend dependencies. The old tool-only image does not contain this new coordinator/fence and cannot be used as the final executor. Final release SHA, actual all-required CI run, exact application Build/image and immutable full-source operator image are intentionally unresolved until this source batch is committed and verified.

The host entry is `infra/gcp/fresh_cutover_docker.py`. It requires a private0700 work directory, its0600 `approved-cutover.json`, and `inputs/inventory.json` in a private0700 inputs directory. It requires the approved config SHA and actual registry SHA. Config fields are exactly `release`, `image_digest` (application digest), `build_id`, `ci_run_id`, `backup_uri`, `inventory_sha256`, `model_primary`, `role_environment`; there are no passwords or predicted numeric versions. Model primary is the reviewed `gemini-3.6-flash`. Backup URI must be a new exact `gs://ai-resume-parser-482412-hirewiz-application-artifacts/releases/<reviewed-name>.dump`. `fresh_cutover_registry.expected_registry(final_release, application_digest, actual_reviewed_at)` produces the canonical expected-state bytes and SHA. `role_environment` is exactly that module's `ROLES` mapping. Reviewer completeness/desired-fenced declarations express the user's dedicated-credential scope and intended state; provider/queue/backup flags remain false. Native verifiers alone establish actual closure.

Invoke with the supported local Python 3.14 using `--image <actual-full-source-immutable-image> --config <workdir>/approved-cutover.json --config-sha256 <actual-sha> --inputs <workdir>/inputs --workdir <workdir> --preflight`. This performs purpose-bound normal-manager reads and native source/empty-target identity checks, with no provider writes and no full-release claim. Final full-source financial/admission verification remains required.

The host uses the stock Google SDK `LoadFreshCredential` against the retained
account, with a one-hour freshness window and an actual expiry check. It reads
the existing GitHub login through `gh auth token` into a private process pipe.
Only the short-lived Google access token, its expiry and GitHub access token
reach the joined Linux container through its owned stdin. The full-source
`fresh_cutover_auth.py` wrapper installs them in process memory for the stock
clients, checks at least 3,545 seconds of remaining Google validity before the
3,540-second executor begins, and clears those process variables afterward.
Refresh tokens and credential-manager bodies are not inspected or transferred.
Credential values never enter operator files, logs, command arguments, Docker
configuration or receipts. Ordinary SDK credential-manager renewal remains
allowed under the user's existing authorization.

`--execute` is the one customer cutover attempt under the existing development
and deployment authorization; it uses a distinct durable marker and named
persistent reconciliation volume. It remains dependent on actual readiness, not
on another blanket approval request. Both modes use Docker `--init --platform
linux/amd64`, readonly root, dropped capabilities, no host network/socket,
bounded resources and a 3,660-second total including cleanup. Neon credentials
remain in its local normal manager; the bootstrap password reaches the Linux
process only through an owned pipe/stdin and is not written to an operator file,
argument, Docker configuration or receipt.

The same Linux process captures the retained source/model credential scope once, preserves the RAM password bank and stable isolation timestamps across the local barrier, and runs these exact forward steps:

1. Recheck full Git/CI/application-image/cost authority using existing live identities; require closed/drained admission, native source identities, schema9, financial obligations and an empty exact destination. Require both private document workers to have the final source labels, accepted immutable images, isolated identities and empty invoker policies before any old writer is retired. Create the NOLOGIN source owner with an actual returned OID. These checks precede old-principal disable.
2. Require the fixed GitHub trigger `42c65704-7868-4af4-96b9-60879bb2d655` to be natively disabled with its exact `^main$`/repo/cloudbuild.yaml/oldCompute binding. Require native nonterminal Builds empty and the last document Build `bc25069a-8137-4d73-a0e5-8fa1fcae7f54` terminal. Recheck trigger/Builds immediately before Compute retirement. The separate scoped replacement builder/trigger package must complete before the overall deployment goal; no future GitHub workflow is silently discarded.
3. Disable the exact five captured old service-account UIDs and delete only the exact four captured Google model-key UIDs after fresh issuer/etag checks. Require actual native readback. New runtime/migration/observer SAs are created only after this cloud isolation, preventing an old holder from minting a replacement identity beforehand. Grant only their own DB/model/policy secrets, the existing Redis/task-token/JWT secrets to runtime, enqueuer on the four fixed paused queues and actAs only the actual tasks SA UID111393701271010610534. Customer object rights are conditional on the known bucket and exclude `/releases/` backup objects; there is no blanket backup deletion/update grant or project Owner/Editor grant.
4. Publish a temporary source-observer numeric version, perform the bounded reviewed source ownership/role transition, and require native zero old dependencies/sessions. Emit only the fixed source-ref/new-owner-OID barrier. The Mac normal Neon CLI updates the one source database owner metadata if needed and deletes only `neondb_owner` from the exact source branch. The host then independently verifies native old OID/name/member/grantor/session absence. Its fixed wakeup is not a certificate; the still-running Linux coordinator reobserves native absence.
5. Rotate the source observer again after provider deletion, using a genuinely new RAM password different from every captured old/source/setup value. Publish/verify its actual numeric version before the one password transaction, then authenticate it against the fixed original cluster/schema/new owner. Preserve all captured IDs/OIDs/scope; do not reset the isolation clock or treat the new owner as an authenticated human exception. The source owner must stay NOLOGIN/non-elevated with no membership path or sessions.
6. Establish the original complete native retirement cut. Rotate the three empty-target passwords after this cut, create the exact new Gemini-only model key after isolation, publish actual numeric versions and genuinely authenticate all three role identities. Copied or cross-alias reused old passwords refuse. Runtime/observer stay selected/current; migrator is authenticated only before its intentional final retirement.
7. Use the unchanged genuine NativeCutBoundary plus owned transfer to capture a retained, protected source backup and consistent snapshot, copy every financial/customer row, artifact reference and sequence state into the verified empty target atomically, and prove source/target multiset/catalog/collation/financial continuity. Apply schema9 through17, renew strict runtime grants/financial checks, stage three joined zero-traffic services, then retire migrator LOGIN/CREATE/sessions and disable the exact selected migration secret version. Staging and serving recheck both document workers, each allowing only the exact new runtime invoker. Reobserve serving-phase native gates before GCP traffic promotion.

The positive terminal explicitly leaves queue admission closed, checkout disabled, frontend deployment unverified and goal_complete=false. Root still completes the existing paid-runtime/feature checks, Vercel deployment, connected GitHub builder restoration and appropriate reopening in the established checkpoints. This packet does not enable AI generation or billing from the estimated policy alone.

## Cutover impact and recovery

The five exact old SAs are `hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com` UID101596337064821972716; `ais-gemini-key-43d738d3dfb9485@226979001779.iam.gserviceaccount.com` UID100592881791239522151; `ais-gemini-key-41af2f49a3fb4b6@157225724590.iam.gserviceaccount.com` UID117611807420650799123; `ais-gemini-key-7aeb7c91129947c@157225724590.iam.gserviceaccount.com` UID103651370799427077361; old Compute `157225724590-compute@developer.gserviceaccount.com` UID115689643755778240830. Exact key resource paths are the four `OLD_KEYS` literals in the reviewed writer. Human provider-admin accounts, retained normal Neon management, unrelated apps and `bulk_action_platform` are preserved. Existing Redis value, payment keys/webhook secrets and customer financial rows are not rotated or discarded.

This interrupts old API/model/database write access after admission is frozen; downtime spans the consistent export/restore, migrations, native validation and promotion. No trustworthy wall-clock estimate exists yet; the operation has a fixed one-hour containment ceiling, not a promised completion time. Billing/application submissions remain closed throughout. A failure before writer isolation leaves original data current; after retirement, the old credentials are not automatically reenabled and there is no automated rollback that restores unsafe authority. Unknown API/SQL/COMMIT outcomes retain once markers, native identifiers, protected backup and persistent volume; root reconciles the exact affected resource before another reviewed action. No DELETE/DROP of either database is included. Restoring or promoting after uncertainty requires a new concrete review. Reaping a local process/container is not proof that server-side SQL rolled back.

## Remaining prerequisites, without invented acceptance

- Preserve and run the complete local source guard for the final exact release.
  The preceding cohort passed; the new source batch requires its own exact check.
- Adopt this exact ordinary source batch, commit/push and complete the genuine required CI/native tests; build the exact application image and full-source Linux operator image. Current tool image and earlier passing CI are not substitute pins.
- The trigger is already natively held with its old source/account configuration preserved. Do not repeat its disable. The adopted `infra/gcp/github_builder.py` still needs actual old-identity isolation, replacement builder creation, three scoped grants, retarget, enable and a genuine GitHub build. Trigger updates read the full current record, preserve optional settings and use the required `projectId`, `triggerId` and single-field `updateMask`; partial PATCH bodies previously failed `INVALID_ARGUMENT`.
- Actual successful normal-manager Docker preflight, actual approved registry/config and current source/target/catalog/financial/backup retention readiness. Missing native IAM, backup custody, source emptiness/obligation, or runtime signing prerequisites remain fixed refusals rather than broad permission additions.
- Execute the named cutover under the user's existing full-access deployment authorization only after the concrete native readiness gates pass. No additional blanket approval dependency is established, and no such cutover has executed.
- Vercel/website/paid-runtime/application acceptance and safe reopening stay within the existing goal; no new checklist labels or completion claim are created.

The published estimated expense policy expires on October16 and is not renewed here. Provider/SQL freshness stays30seconds, original settlement/admission/backup gates and unsupported-historical-source refusals remain intact. Stock normal SDK/CLI refresh or401 handler replay is disclosed; one operator invocation/once marker is not asserted to equal one HTTP request. Vendor bodies remain RAM-only and sanitized results do not contain credentials.
