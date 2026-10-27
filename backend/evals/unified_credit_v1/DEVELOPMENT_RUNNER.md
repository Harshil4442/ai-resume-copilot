# Internal interview calibration runner

This is an executable internal development tool, not a paid route and not a qualification claim. It admits exactly the ten frozen PDF **development** cases, with one reference-labelled and one candidate-labelled run per case. These labels identify experimental sides; neither output is an approved reference until independent reviewers approve it. Held-out cases, customer inputs, custom case lists and changed corpus hashes are refused.

The frozen corpus is unchanged. This first executable slice covers interview generation only. Other routes, the other thirty development cases, deployed native-document verification, independent reviews and retail unit-economics qualification remain separate work.

## Authorization and input evidence

`development-authorization.schema.json` is generated from `development_runner.Authorization`. Author a private JSON file matching it; never put provider credentials in that file. Required fields are:

- `schema_version`: `synthetic-interview-development-v1`.
- `experiment_id`: stable lowercase identifier, 8–80 characters. Reuse it for recovery; do not invent a new ID to retry failed work.
- `purpose`: `internal_calibration_not_sales_qualification`.
- `state`: `draft` for preparation; `authorized` only after the operator allocates the bounded tranche. `authorized_by` identifies that actual operator.
- `valid_from`, `expires_at`, `provider_balance_checked_at`: timezone-aware timestamps; allocation lasts at most one day and balance observation must be at most one day old.
- `funding_evidence` and `provider_balance_evidence`: `{path, sha256}` references, relative to the authorization directory. These must describe the actual allocation and observed provider balance, without credentials. Eligible promotional provider credit is not an invoice or proof of an old prepaid balance.
- `maximum_provider_cost_micros`: USD millionths covering all twenty one-attempt envelopes. Preflight calculates the exact required maximum from the pinned input/output limits and prices; this is an estimate, not an invoice.
- `model_policy_sha256` and `expense_policy_sha256`: canonical JSON hashes of the validated current policies. The runner compares full snapshots before execution.
- `native_manifest_sha256`: actual materialization manifest file hash.
- `native_receipts_sha256`: actual native input receipt-map file hash.
- `routes.reference` and `routes.candidate`: new work uses `prompt_version: interview-evidence-v6`, `contract_sha256` from `development_runner.contract_digest()`, and `price` containing the exact current `ModelPriceQuote`. The current output validator is `interview-complete-v2`. Only the existing native Gemini contract is supported. No fabricated `QualifiedRoute` is needed. Equal pins on both sides are a repeatability calibration, not evidence for a cheaper alternative.

Policy hashes use `development_runner.digest(policy.model_dump(mode="json"))`; artifact hashes use SHA-256 of file bytes. Regenerate draft configuration after source or policy changes, then review the change. An already prepared experiment refuses changed authorization.

The schema still loads original `interview-evidence-v5` authorizations for historical export. Dry-run, prepare and execute require both the exact current prompt version and the current implementation digest before database or provider work. Replacing an old authorization's digest with the current digest does not authorize its old prompt. Keep completed v5 experiments, receipts and exports unchanged; new work requires a separately reviewed current authorization.

The native receipt map is `{case_id: {path, sha256}}`. Each referenced JSON binds `source_sha256` and `artifact_sha256` to the immutable PDF, the six genuine structure/render/reading-order/link/format/immutability checks, extracted text, all PNG render artifacts, and a named visual review bound to every PNG hash. Actual local tools and explicit agent input review can support this synthetic-input preflight. They do not prove Cloud Run scanner behavior, human output quality, or retail qualification. Do not copy the synthetic test review flags into real evidence.

## Local durable execution

Run from `backend`. The shell/process must already have privately supplied the reviewed `LLM_MODEL_COST_POLICY_JSON`, `HIREWIZ_EXPENSE_POLICY_JSON`, actual provider credential, `LLM_MODEL`, and `LLM_API_BASE`. Never echo these secrets or put them in command arguments.

Set `APP_ENV=development`, `HIREWIZ_INTERNAL_EVALUATION_ENABLED=true`, and `OPTIONAL_AI_GENERATION_ENABLED=true`. `HIREWIZ_EVALUATION_DATABASE_URL` must identify the dedicated loopback PostgreSQL database: host `127.0.0.1`, port `55433` or `55434`, database `hirewiz_quality_development` or `hirewiz_admission_test`, driver `postgresql+psycopg`, no URL options. Remote and public-schema execution is refused. The runner creates a private `hwdev_<experiment digest>` schema and one synthetic zero-credit owner. It does not change production or spend customer credits.

Example invocations use illustrative private evidence paths:

```sh
.venv/bin/python scripts/run_development_evaluation.py dry-run \
  --authorization /private/evaluation/authorization.json \
  --native /tmp/hirewiz-quality-native-20261028-v3 \
  --native-receipts /private/evaluation/native-map.json

.venv/bin/python scripts/run_development_evaluation.py prepare \
  --authorization /private/evaluation/authorization.json \
  --native /tmp/hirewiz-quality-native-20261028-v3 \
  --native-receipts /private/evaluation/native-map.json

.venv/bin/python scripts/run_development_evaluation.py execute \
  --authorization /private/evaluation/authorization.json \
  --native /tmp/hirewiz-quality-native-20261028-v3 \
  --native-receipts /private/evaluation/native-map.json \
  --side reference --maximum-operations 1

.venv/bin/python scripts/run_development_evaluation.py export \
  --authorization /private/evaluation/authorization.json \
  --destination /private/evaluation/results-first-run
```

Inspect the first run before resuming reference work or executing the candidate side. `--maximum-operations` is 1–10 per invocation, default 1. Restart with identical authorization and experiment ID; only never-started queued runs are claimed. Change the privately configured `LLM_MODEL` between sides only if the reviewed pins differ. The process model and endpoint must exactly match the requested side.

Dry-run opens no database and makes no provider call. Prepare creates twenty durable queued runs and makes no provider call. Execute commits a run claim before invoking the real `generate_interview_questions` through `persistent_run_budget`. Real `ModelCallEvent` and detached `ModelCostLiability` records reserve/settle provider expenses. One attempt per run means no corrective generation, model fallback or SDK hidden retry. Invalid output fails explicitly; unknown usage retains its reserved cost. Failed or running/unknown runs are never automatically requeued. Resume does not refund provider expense or change the source.

The isolated ledger enforces the bounded calibration tranche; it does not observe production fleet spend. The operator's allocation must account for concurrent production exposure and existing daily/monthly/promotional budgets. Do not interpret the isolated schema's empty prior spend as fresh provider money. No new independent spend ledger or provider bypass is introduced.

Export remains available after generation is paused, policies expire, or the implementation advances to v6, provided the original provenance artifacts remain intact. It does not require the historical authorization to match today's prompt or source digest. Every export requires a new directory; overwrites are refused. It includes actual outputs, all retained expenses (including failures), the original authorization/evidence, and versioned `legacy-model-liability-v1` receipts. Legacy records have no provider request ID or unified execution hold; these fields are never fabricated. The packet importer validates linked events, liability IDs, pricing, measured tokens and cost arithmetic. Calibration receipts are rejected for retail-route qualification.

`development-results.json` is deliberately not a completed evaluation `bundle.json`. Reference approval, blinded independent reviews, adjudication and full held-out evidence are still absent. Provider transport tests use explicitly synthetic fixtures, never real provider credentials or invoices.
