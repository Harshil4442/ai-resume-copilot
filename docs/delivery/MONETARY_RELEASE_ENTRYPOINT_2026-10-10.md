# Monetary release: bounded readiness and forward sequence

Status: independently reviewable development foundation, **not a completed usable
production cutover**. The native external-consumer/provider retirement verifier is
absent. Execution deliberately stops at that gate. No production migration, queue,
credential, backup or traffic action was performed during author verification.

The legacy `infra/gcp/release.sh` still accepts only schema0009. Its source guard now
shares migration-chain parsing; its original refusal and checks remain. The separate
`infra/gcp/monetary_release.py` never treats the existing monetary preflight's
`database_gate_passed` or unverified inventory booleans as cloud permission.

## Useful operation available now

Run from a clean, reviewed checkout with the locked backend Python runtime:

```sh
backend/.venv/bin/python infra/gcp/monetary_release.py /private/release-plan.json --plan-sha256 REVIEWED_SHA256 --observe
```

The default is read-only observation. It reports fixed nonsecret gate reasons for
source/CI/image/policy, queue/scheduler/job admission, database writers and the missing
external fence verifier. It does not issue a deployment certificate or mutate cloud
resources. Source bytes/HEAD/links/untracked files are checked before cloud access.

The strict plan contains only identity pins: release SHA, immutable image digest,
native Cloud Build ID, CI run ID, exact numeric Secret Manager references for distinct
runtime/migration credentials and reviewed expense policy, policy digest, separate
service accounts, a new protected backup URI, and private database-inventory path/hash.
Credential values and database role aliases stay in the operator environment. `latest`,
same runtime/migration credentials or accounts, duplicate input keys and unknown plan
fields are refused. No provider-fence or consumer-completeness assertion is accepted.

## Exact native Git build shape

`infra/gcp/cloudbuild-monetary.yaml` uses the existing repository-root `Dockerfile`,
which copies the locked backend dependencies and `backend/` source. Both Docker steps
run at the repository root. After source review and exact-head CI, an authorized
operator can submit a native Git build with the same full reviewed SHA in both places:

```sh
release_sha=FULL_REVIEWED_COMMIT_SHA
[[ "$release_sha" =~ ^[a-f0-9]{40}$ ]]
gcloud builds submit https://github.com/Harshil4442/ai-resume-copilot.git \
  --project=ai-resume-parser-482412 --region=us-central1 \
  --git-source-dir=. --git-source-revision="$release_sha" \
  --config=infra/gcp/cloudbuild-monetary.yaml --substitutions="_RELEASE=$release_sha"
```

This configuration only builds and pushes; it cannot authorize migration or traffic.
The existing native verifier still requires the exact resolved Git source revision,
fixed root build/push steps and returned immutable image digest. Source archives and
image labels cannot substitute for those observations. No build was submitted during
author tests. [GitSource/BuildStep semantics](https://docs.cloud.google.com/build/docs/api/reference/rest/v1/projects.builds)
and [native Git submission flags](https://docs.cloud.google.com/sdk/gcloud/reference/builds/submit)
define the configuration.

## Implemented forward sequence, currently gated

1. Verify clean immutable source and the exact0009→0010→0011→0012→0013 chain. Verify
   authenticated GitHub repository/workflow/event/run identity and five unique successful
   jobs. All future CI jobs explicitly check out the PR head SHA (or push SHA); the
   native command verifies that reviewed checkout contract rather than assuming a PR
   `headSha` is the merge checkout. Earlier CI/tree-equivalence evidence is unchanged.
2. Bind the image to native Cloud Build Git-source provenance for the exact repository
   and SHA, the root Dockerfile build/push sequence and returned registry digest.
   Uploaded source archives or image labels alone are unsupported. Validate the existing
   authoritative financial guard against the pinned numeric policy secret. Owner-authorized
   estimates can be reviewed; no new human-only approval barrier is introduced. Require
   three finite packs and search1/apply20, preserving accepted historical terms.
3. Observe four paused, empty task queues, paused schedulers and terminal visible job
   executions. Require a fresh authenticated external-consumer/provider/writer fence.
   **That native verifier does not exist yet**; zero traffic, disabled Secret Manager
   versions, an empty queue or caller JSON cannot substitute for credential revocation.
4. Reuse the read-only PostgreSQL observer for cluster/database/schema, retired-role
   capabilities and sessions, unexpected writers, prepared transactions and retained
   financial obligations. Authenticate the distinct numeric runtime/migration secret
   credentials against those identities. Check runtime table SELECT/INSERT/UPDATE,
   DELETE on supported customer erasure tables, sequence USAGE and migration ownership.
   Repeat after each schema step; default grants must cover newly created tables.
5. Create a **new** native backup after the verified fence. Hold a verified repeatable-read,
   read-only exporting transaction while `pg_dump --snapshot` runs. Scrub inherited
   libpq service/hostaddr/options variables. Keep the dump private; verify its archive,
   SHA and size. Before exposing backup bytes, verify actual bucket uniform access and
   enforced public access prevention; a bucket IAM binding list alone is insufficient.
   Upload with generation-match0 and locked seven-day object retention,
   then download and verify that exact generation. Only the native process's own creation
   receipt is admitted; caller object metadata cannot prove snapshot origin. The observer
   must have sufficient read privileges and the bucket must support private retained
   objects, or creation refuses. This is not a restore drill.
6. Run one forward migration job per observed transition with a separate migration
   identity, exact image and zero retries. `backend/scripts/monetary_schema_step.py`
   verifies database/current-role/schema pins, takes a nonblocking advisory lock and
   applies only the next revision. Schema13's ordinary index creation is restricted to
   the drained window, at most256MiB observed indexed-table size, five-second lock timeout
   and300-second statement timeout. Larger tables need a separately reviewed index plan.
7. Stage the same immutable release for API, analysis and employer workers with no traffic.
   Verify explicit service/worker roles, labels, topic scopes, numeric runtime/policy
   references, source/image identity, Ready condition and exact tagged HTTP health.
   The existing worker health lacks a revision field; its exact native revision/tag URL
   plus release/worker label establishes this bounded check. Keep worker IAM checks on,
   employer tasks-only invoker and analysis tasks+scheduler invokers. Inspect bounded
   project policy; any project public principal or conditional invoker binding is
   unresolved and refused, including custom roles. This is not an organization-wide IAM audit.
8. Recheck the joined set, source/policy, admission, fences and database before promotion.
   Promote workers, then API; observe exact100% revision traffic and health. Keep task
   admission and `RAZORPAY_CHECKOUT_ENABLED` explicitly closed, optional generation,
   automatic submission and native lifecycle off. The inherited payment callback/refund
   configuration is preserved; only admission for new checkout is closed.
   Compiler/authority configuration remains absent until its independent gates pass.
   Resume and end-to-end product/field monitoring remain separate required release work.

A lost mutation result is not retried by this command. Reconcile its native execution
and observed schema first. There is no automatic downgrade, database restore, old
credential reopening or discarded liability. A new invocation requiring0009 refuses an
already advanced database; resume/repair requires a separately reviewed forward plan.

## Evidence and remaining checklist

- [x] Separate bounded sequencer, source/refusal checks and local migration command.
- [x] Legacy0009 refusal retained; relevant tests and static checks added to hosted CI.
- [ ] Independent source review and root composition replay for the final integrated head.
- [ ] Actual external database/provider consumers resolved and native fence verifier supplied.
- [ ] Replacement roles/least privileges, numeric secrets and approved expense policy deployed.
- [ ] Real protected backup creation/restore procedure and schema13 index window verified.
- [ ] Native GCP image/worker/IAM/HTTP observations, migration, promotion and queue resumption.
- [ ] Production paid journeys, rollback/restore, invoice variance and monitoring proved.

Cloud Build's output provenance and image digests are described in its
[Build API](https://docs.cloud.google.com/build/docs/api/reference/rest/v1/projects.builds).
Generation preconditions and object retention flags are documented for
[gcloud storage cp](https://docs.cloud.google.com/sdk/gcloud/reference/storage/cp).
Bucket privacy controls follow the documented
[uniform access](https://docs.cloud.google.com/storage/docs/using-uniform-bucket-level-access)
and [public access prevention](https://docs.cloud.google.com/storage/docs/using-public-access-prevention)
contracts.
Tagged health requests use the base service URL token audience, as documented in
[Cloud Run authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).
These platform contracts inform the implementation; local tests do not prove a native
resource or credential is configured in this account.
