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

The reviewed connected pairing bridge is a separate next integration. Production recent authentication/account lifetimes, durable invocation across replicas and restarts, protected projection restoration, real cloud IAM/KMS/witness proofs, BFF/MV3 transport and tenant form/permission acceptance remain open. No fixture, emulator result, backup or health response closes those broader gates.
