# Root integration and compatibility staging

Status: reviewed source integrated and locally verified; the monetary migration and production pairing are not enabled. Production continues to serve `e7d8f2b` with schema `20261008_0009`.

## Integrated development

The root combines persisted model-cost admission, exact finite price policies, optional-generation quiescence, native GCP journal/registry adapters, the independent coverage-audit harness, a fresh-user backend journey with AI disabled, and a release guard. These changes keep search/application service credits, product analysis units and estimated provider spending separate.

The default whole-project backend command passes **1,261 tests, zero failures, zero errors and zero skips** in 70.35 seconds. This includes the existing three root marketplace tests, all 25 new release-guard tests, six actual Firestore emulator cases and the actual PostgreSQL cost/concurrency/migration cases. Ruff, Mypy (75 source files), compilation, shell syntax and generated API contract checks pass. The [root proof](evidence/2026-10-09-root-component-integration.json) records source hashes and the JUnit digest. It is local integration evidence, not a claim of live browser acceptance.

The first expanded run's **1,235 passes and one failure** remain historical evidence. The corrected contention tests permit safe double-abort schedules, require fresh explicit transactions and prove exactly one immutable final owner. They retain finite native retries and reject ambiguity; application retry limits were not increased. See [the correction](FIRESTORE_EMULATOR_CONTENTION_TEST_2026-10-09.md).

## Release guard

`infra/gcp/release_preflight.py` runs before any release-script cloud command. It requires the exact clean Git source, checks tracked bytes even when index flags hide a change, and statically validates the approved migration chain. It refuses revision `0010` and other unreviewed migration heads before cloud access. The 25 guard cases include 23 refusal cases with zero fake cloud calls and two approved-chain cases that reach only the first sentinel command.

Cloud Build now builds and pushes images only. Migration, staging and promotion require a separate reviewed operator rollout. This guard prevents using the old release sequence for the model-money migration; it does not prove that old AI writers have stopped.

## Zero-traffic compatibility stage

Compatibility commit `b306f135fcd5d8f10336c8cfb4174c71ac028649` passed all four exact CI jobs. Build `d1f4ddea-45f0-4ec5-a4eb-818620333cad` succeeded with immutable image digest `sha256:de8dcc2d778d04f5afc87f4d1513ce6495fca4713676031635d55a0486a20fce`. API candidate `ai-resume-parser-00270-veb` is ready with **zero production traffic**, direct ASGI startup, optional generation disabled and automatic migrations disabled. A direct health read returned HTTP 200 and the exact compatibility release. Existing API `ai-resume-parser-00267-xot` retains 100% of production traffic. The [staging proof](evidence/2026-10-09-generation-compatibility-api-stage.json) records only permitted nonsecret fields.

No worker replacement, queue pause, provider generation, database migration or traffic promotion occurred in this stage. Zero traffic alone does not retire old requests or workers. The monetary cutover still needs a complete legacy-writer inventory, enforceable credential/database fencing, preserved unknown outcomes, a post-fence backup and a verified rollout with generation paused. Pricing is an [offline candidate](MODEL_COST_ROLLOUT_2026-10-09.md), not an active runtime policy.

The two accidentally removed historical secret-scan exclusions were restored exactly. Compatibility full-history local scanning and exact-commit CI now pass. This restores the prior narrow scan scope; it does not demonstrate historical credential revocation.

## Remaining delivery

The reviewed connected pairing bridge is now integrated as a separate source stage. Production recent authentication/account lifetimes, durable invocation across replicas and restarts, protected projection restoration, real cloud IAM/KMS/witness proofs, BFF/MV3 transport and tenant form/permission acceptance remain open. No fixture, emulator result, backup or health response closes those broader gates.

## Connected pairing follow-up

After integrating the reviewed bridge and a narrow None-safe typing correction, the
complete default suite passes **1,279 tests without failures, errors or skips**. This
adds 18 actual-emulator cases through the real PairingService, with synthetic Storage
and independent-fence boundaries. Mypy passes the full CI selection on 77 source files.
See [the bridge evidence](GCP_PAIRING_BRIDGE_STAGE_2026-10-09.md). The 1,261-case proof
above remains the prior component checkpoint; neither checkpoint proves production pairing.

The final shared-history scan initially found two old synthetic idempotency-label
occurrences on compatibility commit `bfc3c448`, which is outside this branch's ancestry
but remains among fetched references. The same two exact commit/file/rule/line
exclusions already reviewed on the compatibility branch are now retained here, alongside
the unchanged two baseline entries. Current test labels are short and the added-source
scan found no leaks. No rule, path or value-pattern exception was added. CI now pins the
reviewed scanner image digest. These synthetic exclusions do not prove revocation of
any credential represented by the older baseline exclusions.

## Zero-traffic analysis-worker stage

The same immutable compatibility image is staged as `hirewiz-analysis-worker-00022-qut`
with generation/migration disabled, direct worker ASGI startup and zero production traffic.
Existing analysis revision `00021-5zd` retains 100%. Private invoker policy is unchanged;
the candidate health route returns 403 anonymously and 200 with the existing authorized
operator's genuine identity token, reporting the exact compatibility release and analysis
role. [The worker staging proof](evidence/2026-10-09-generation-compatibility-analysis-stage.json)
contains no tokens or credential values. This is health/identity/traffic evidence only.
Queues and old writers remain active; neither candidate stage proves their retirement.
