# Schema19 browser and candidate release

The source now provides a separate, disabled schema19 migration and rolling
release path. It has not been used for a production schema19 migration or
deployment. F7's native backup-retention prerequisite remains pending at this
handoff, and all six original delivery outcomes remain open.

F7 and its existing rolling CLI retain their exact `20261015_0018` contracts.
Their inventory, identity and migration allowlists still reject schema19. The
new path admits the complete source chain ending in `20261015_0019` through its
own plan and native identity purpose. Keep the migration in the source and build;
do not relabel this source as schema18, widen an old allowlist, or use `upgrade
head` for F7.

## Implemented boundaries

| Source | Schema19 responsibility |
| --- | --- |
| `infra/gcp/browser_schema19_release.py` | `Schema19Plan`, native capture, exact source/CI/Cloud Build image admission, migration journal, fresh post-commit checks, staging/promotion, reconciliation and conservative traffic rollback. The plan binds `schema_contract=hirewiz_browser_schema19_v1` and an observed database phase of exactly 18 or 19. |
| `backend/scripts/browser_schema19_step.py` | `MigrationIdentity` and `upgrade_transaction`: the exact 18→19 DDL on the caller's genuine bootstrap connection and external transaction. |
| `backend/scripts/browser_schema19_contract.py` | `verify_shape`: bounded catalog-only checks for the preserved candidate/extension shape and actual schema 19 DDL. Its application-state SQL preserves the schema18 manual-report predicate. Native identity, schema marker, finance and permissions remain independent checks. |
| `backend/scripts/neon_direct_identity.py` | `collect_schema19_release_target` issues the distinct `schema19_fresh_release_capture` purpose for the fixed F7 target, numeric native secret version and exact schema 19 marker. Existing purposes keep their original schema allowlists. |
| `infra/gcp/monetary_release.py` | A shared immutable Git/CI/Cloud Build provenance helper runs after each caller's exact source-chain admission. Original F7 admission still requires its frozen schema 18 chain. |
| `infra/gcp/rolling_release.py` and `rolling_native.py` | Small plan-factory/disabled-flag hooks retain schema 18 defaults. Schema19 uses the existing durable one-PATCH journal, native LRO/readback, health and traffic machinery through its separate boundary. |

The source path pins runtime/observer numeric secrets, service UIDs, generations,
prior revisions, etags, templates, traffic and IAM. Document-worker provenance
retains its separately observed release. Capture performs native reads and health
checks; a local plan alone does not authorize migration or deployment.

## Actual schema19 DDL and privileges

Migration19 adds non-null integer `browser_assistance_reviews.review_protocol_version`
with server default 1. It backfills 2, 3 or 4 from an existing exact payload marker,
preserves the other review values, and installs validated checks:

- `ck_browser_review_protocol`: `review_protocol_version BETWEEN 1 AND 4`.
- `ck_browser_review_index`: `next_index BETWEEN 0 AND 64`.

It also creates:

| Table | Reviewed shape |
| --- | --- |
| `candidate_google_enrollment_challenges` | Primary key `id` varchar(36); `user_id` integer with indexed, cascading FK to `users.id`; four varchar(64) digests (`nonce_sha256`, `principal_sha256`, `email_sha256`, `credential_sha256`); bigint `expires_at_ms`; boolean `used`. Every column is non-null, with no server default. |
| `candidate_google_identities` | Primary key `user_id` with cascading FK to `users.id`; unique varchar(64) `principal_sha256`; varchar(64) `enrollment_proof_sha256`; varchar(24) `state`. Every column is non-null, with no server default. |
| `candidate_identity_history` | Detached, non-null varchar(36) primary key `registration_id`; no candidate FK, so candidate deletion preserves the schema-use history. |

The catalog contract checks the exact new columns, defaults, primary/foreign and
unique constraints, FK destination/delete behavior, indexes and their validity,
operator classes and collations. It checks the durable browser default and
validated bounds. It does not infer a protected candidate subject or Google
ownership from SQL rows.

The runtime receives SELECT/INSERT/UPDATE/DELETE on the two Google projection
tables and SELECT/INSERT on `candidate_identity_history`. The history policy
refuses runtime UPDATE, DELETE and TRUNCATE. The observer receives SELECT on all
three new tables. Each new table must have the exact existing migrator owner.
Legacy table/sequence requirements and the global host-pacing DELETE prohibition
remain enforced.

The production migration authenticates the existing database bootstrap session
through the normal Neon credential manager and verified TLS/native database
identity. It verifies the bootstrap session identity, existing NOLOGIN migrator's
exact identity and SET authority. One external SERIALIZABLE READ WRITE
transaction takes advisory transaction lock `721994372`, applies bounded
statement/lock timeouts, verifies drained work and retained row digests,
temporarily grants schema CREATE, and runs only `alembic upgrade 20261015_0019`
as the migrator. It grants the new table permissions, removes temporary CREATE,
and requires the original schema ACL before commit. The migrator stays NOLOGIN;
its retired numeric secret remains DISABLED. No ownership transfer, credential
revival, data transfer or financial rewrite is part of this step.

The migration journal persists its intent before DDL. A separate connection
checks the committed marker, shape, retained data, restored ACL and permissions.
A fresh schema19 native boundary then checks global admission, source/image,
policies, identities and financial state before recording a successful check.
SDK/provider work runs outside the SQL transaction.

The global Cloud Run execution inventory projects only `metadata.name` and
`status.completionTime`. Its 257-result probe, 256-execution ceiling and 128 KiB
response bound remain enforced; pending or malformed rows stop the operation.
Retained execution templates do not need deletion to fit that observation.

The preservation snapshot admits at most 256 ordinary tables and 250,000 retained
rows within a 30-second hashing window. Exceeding those bounds stops the operation;
it does not skip rows or relax the preservation proof.

## Operator sequence

Run this sequence after the preserved-data F7/schema18 cutover is observed
complete and the later source/image/package and maintenance operation are
approved. Measure the existing review-table size and lock window first;
replacing its check and backfilling the protocol column require actual production
lock and scan capacity. These commands are operator instructions, not executed
evidence.

Use the reviewed Python3.12 environment with required SDK/SQL dependencies and
normal manager access. Supply actual observed metadata for every placeholder:

```sh
SCHEMA19_PYTHON="<absolute reviewed Python3.12 executable>"
SCHEMA19_ROOT="<absolute clean checkout at the reviewed candidate commit>"
SCHEMA19_WORK="<absolute new private operator directory>"
SCHEMA19_RELEASE="<reviewed full candidate Git commit>"
SCHEMA19_IMAGE="<actual candidate sha256:image-digest>"
SCHEMA19_BUILD="<actual candidate Cloud Build ID>"
SCHEMA19_CI_RUN="<actual successful candidate CI run ID>"
SCHEMA19_WORKERS_RELEASE="<observed document-worker source commit>"
SCHEMA19_OBSERVER_SECRET="<observed observer secret-name:numeric-version>"
SCHEMA19_RETIRED_MIGRATOR_SECRET="<observed DISABLED migrator secret-name:numeric-version>"
SCHEMA19_MIGRATION_NONCE="<new 32-character lowercase hex nonce>"
SCHEMA19_ROLLING_NONCE="<different new 32-character lowercase hex nonce>"

umask 077
mkdir -m 700 "$SCHEMA19_WORK"
mkdir -m 700 "$SCHEMA19_WORK/migration-journal" "$SCHEMA19_WORK/rolling-journal"
```

1. Capture the actual schema18 preimage using schema19 source admission. Record
   the returned `plan_sha256`; capture refuses an existing output file.

```sh
"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" capture \
  --root "$SCHEMA19_ROOT" --schema 20261015_0018 \
  --plan "$SCHEMA19_WORK/schema18-migration-plan.json" \
  --commit "$SCHEMA19_RELEASE" --image-digest "$SCHEMA19_IMAGE" \
  --build-id "$SCHEMA19_BUILD" --ci-run-id "$SCHEMA19_CI_RUN" \
  --nonce "$SCHEMA19_MIGRATION_NONCE" \
  --observer-secret "$SCHEMA19_OBSERVER_SECRET" \
  --retired-migration-secret "$SCHEMA19_RETIRED_MIGRATOR_SECRET" \
  --workers-release "$SCHEMA19_WORKERS_RELEASE"

SCHEMA19_MIGRATION_PLAN_SHA="<plan_sha256 returned by that capture>"
```

2. Perform the bounded upgrade with that exact schema 18 plan and its owned journal.

```sh
"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" upgrade \
  --root "$SCHEMA19_ROOT" --plan "$SCHEMA19_WORK/schema18-migration-plan.json" \
  --plan-sha256 "$SCHEMA19_MIGRATION_PLAN_SHA" \
  --journal "$SCHEMA19_WORK/migration-journal"
```

If execution or commit acknowledgement is unknown, use **the same schema 18 plan, hash
and journal** with `migration-reconcile`:

```sh
"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" migration-reconcile \
  --root "$SCHEMA19_ROOT" --plan "$SCHEMA19_WORK/schema18-migration-plan.json" \
  --plan-sha256 "$SCHEMA19_MIGRATION_PLAN_SHA" \
  --journal "$SCHEMA19_WORK/migration-journal"
```

Reconciliation never invokes DDL. Observing schema18 returns reconcile-only
status and does not authorize another upgrade. Preserve a started/unknown
journal; do not remove its marker or reinterpret a new invocation as permission
to replay it.

3. After committed schema 19 is independently observed, capture a **new schema 19 plan with the
   different nonce**. Keep the migration plan and journal intact.

```sh
"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" capture \
  --root "$SCHEMA19_ROOT" --schema 20261015_0019 \
  --plan "$SCHEMA19_WORK/schema19-rolling-plan.json" \
  --commit "$SCHEMA19_RELEASE" --image-digest "$SCHEMA19_IMAGE" \
  --build-id "$SCHEMA19_BUILD" --ci-run-id "$SCHEMA19_CI_RUN" \
  --nonce "$SCHEMA19_ROLLING_NONCE" \
  --observer-secret "$SCHEMA19_OBSERVER_SECRET" \
  --retired-migration-secret "$SCHEMA19_RETIRED_MIGRATOR_SECRET" \
  --workers-release "$SCHEMA19_WORKERS_RELEASE"

SCHEMA19_ROLLING_PLAN_SHA="<plan_sha256 returned by that fresh capture>"
```

4. Advance the immutable API/worker revisions with the new plan/journal. Stage
   and joined health/readbacks precede traffic changes; each PATCH has a durable
   intent and at most one send.

```sh
"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" advance \
  --root "$SCHEMA19_ROOT" --plan "$SCHEMA19_WORK/schema19-rolling-plan.json" \
  --plan-sha256 "$SCHEMA19_ROLLING_PLAN_SHA" \
  --journal "$SCHEMA19_WORK/rolling-journal"
```

Use `reconcile` with the same rolling plan/hash/journal to inspect existing
actions after an unknown PATCH or pending LRO; it never starts the next action.
Use `observe` for fresh global read-only checks without changing the journal:

```sh
"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" reconcile \
  --root "$SCHEMA19_ROOT" --plan "$SCHEMA19_WORK/schema19-rolling-plan.json" \
  --plan-sha256 "$SCHEMA19_ROLLING_PLAN_SHA" \
  --journal "$SCHEMA19_WORK/rolling-journal"

"$SCHEMA19_PYTHON" "$SCHEMA19_ROOT/infra/gcp/browser_schema19_release.py" observe \
  --root "$SCHEMA19_ROOT" --plan "$SCHEMA19_WORK/schema19-rolling-plan.json" \
  --plan-sha256 "$SCHEMA19_ROLLING_PLAN_SHA"
```

All admission stays closed throughout this sequence. The new plan explicitly
stages false values for typed controls, attachment, submit, native upload,
native assistance pairing, native browser pairing, native submit authority and
native candidate ingress. Existing false requirements include candidate account
lifecycle, browser assistance, optional AI, employer discovery/automatic submit,
checkout and direct resume upload. Product activation and candidate native
resource provisioning require their separate reviewed operations and actual
normal-user acceptance.

## Conservative rollback and retained history

Rollback changes traffic and retains schema19. It requires an optional
`prior_sources` set captured into the plan: each `PriorSource` contains the
actual prior `release`, `image_digest`, `build_id` and `ci_run_id`. To supply it at
capture, add `--prior-sources "<reviewed metadata JSON path>"` and
`--prior-sources-sha256 "<actual SHA256 of those bytes>"`. The JSON is an array
of those four-field objects. The set is optional for advance and mandatory for
rollback for every distinct prior release.

Before rollback, the guard binds each proof to the actual old native revision's
image and independently verifies its immutable five-job CI/Cloud Build
provenance. It requires identical tracked `backend/app` contents, the schema19
catalog contract and complete migration19 bytes between prior and current
releases. Schema18 sources, earlier unsupported schema 19 bodies, changed backend files
and missing native provenance refuse. Broader binary compatibility is not
inferred from matching Alembic revision names. The first 18→19 transition therefore
has no automatic traffic rollback to its schema18 binary.

When those conservative prior-source requirements are satisfied, invoke
`rollback` with the same schema 19 rolling plan/hash/journal used by `advance`. It also
requires joined staged revisions, verifies the retained old configuration and
health, and preserves both old and new revisions. No SQL downgrade is invoked.

The migration's downgrade guard independently refuses when detached Google
identity history exists, a durable review protocol exceeds 1, a cursor exceeds 7,
or a retained payload identifies protocol 2/3/4. Do not delete or cancel retained
history to force a downgrade.

## Verified local evidence and remaining gates

| Scope | Verified result |
| --- | --- |
| Native identity/transport admission | 233 focused tests passed, including the distinct schema 19 purpose and preserved old-purpose refusals. |
| Schema19 catalog contract | 47 tests passed, including actual full-chain PostgreSQL 17 catalog admission, read-only observation and rejected metadata changes. |
| New release protocol/Git boundaries | 26 tests passed with owned local protocol/Git fixtures; these are not native deployment evidence. |
| Shared existing release/rolling behavior | 177 tests passed. |
| Bootstrap session 18→19 transaction | 15 genuine PostgreSQL17 tests passed through a non-superuser bootstrap login and NOLOGIN owner: committed migration, exact schema-ACL restoration, retained browser/payment/credit rows, effective runtime/observer grants, partial-DDL error rollback, caller rollback, advisory-lock conflict, wrong identities/owners and replay refusal. |
| Existing schema19 migration/history behavior | 19 PostgreSQL17 tests passed for v1 preservation, extended cursor bounds, durable v2/v3/v4 metadata after payload erasure, downgrade refusal and detached Google identity history retention. |

Reproduce the database cases only against the dedicated disposable loopback
PostgreSQL17 database accepted by the fixtures. From `backend`, with its actual
`HIREWIZ_TEST_POSTGRES_URL` set and the reviewed interpreter selected, run:

```sh
"$SCHEMA19_PYTHON" -m pytest -q tests/test_browser_schema19_step_postgres.py
"$SCHEMA19_PYTHON" -m pytest -q tests/test_browser_schema19_contract.py
"$SCHEMA19_PYTHON" -m pytest -q tests/test_browser_typed_migration_postgres.py tests/test_candidate_google_migration_postgres.py
"$SCHEMA19_PYTHON" -m pytest -q tests/test_browser_schema19_release.py tests/test_neon_schema19_release_transport.py
```

The new release protocol and identity subset was rerun together: 49 passed.
Mypy passed on the four changed/new source modules. Ruff passed on new source and
test files, with critical-error checks on the three shared release hooks. These
are local source checks, not native release evidence.

The local tests above use synthetic fixtures and the owned loopback PostgreSQL
database. They do not establish current GCP/Neon authority, production lock
timing, native resource provisioning, a successful production backup, deployment,
human browser permissions, a real employer upload/submission or employer receipt.
Fill and resume-upload assistance retain their zero-credit policy. Final submit's
separate 20-credit reservation and verified-receipt settlement remain governed by
its own contract. See [normal candidate activation](NORMAL_CANDIDATE_BROWSER_ACTIVATION_2026-10-15.md)
and [hosted network acceptance](BROWSER_HOSTED_NETWORK_ACCEPTANCE_2026-10-15.md)
for related local source and acceptance limits. All six original outcomes remain
open until their complete deployed journeys are observed.
