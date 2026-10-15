# Persisted model-cost admission foundation

Status: source implementation and local verification, independently reviewed, pending an explicit operator rollout. This work makes **quoted maximum authorized estimated spend** enforceable at admission. It does not establish a provider invoice cap, validate access to a live model, or change a deployed model, credential, price policy, or cloud service.

## Scope and guarantees

`AnalysisRun` owns one immutable monetary quote and ceiling in integer USD millionths, independently of product analysis units and employer search/application service credits. `ModelCallEvent` owns an exact provider/model/endpoint price quote, the admitted output limit, token-estimate and returned-usage provenance, its reservation, and any usage-based cost estimate. A logical result with no admitted provider attempt has unavailable monetary telemetry (`null`), rather than an inferred free call.

Every external writing attempt follows this sequence:

1. Lock the run in a short, independent database transaction. Reject cancellation/terminal state, spent attempts, previous usage overrun, missing/revoked/expired price authority, oversized input estimate, or insufficient remaining estimated-spend capacity.
2. Freeze the exact event quote and increment both the shared attempt count and monetary reservation. Commit before SDK construction or network access.
3. Send the request with that event's output bound. Google uses native `max_output_tokens`, one candidate, the pinned public Gemini endpoint, explicit `vertexai=False`, disabled redirects and SDK retry attempts of one. OpenAI uses `max_completion_tokens`; a compatible chat endpoint requires its explicitly reviewed supported parameter.
4. With run → event lock ordering, settle a complete returned usage estimate at most once. Google accounting includes thoughts; OpenAI completion totals include reasoning, without adding reasoning a second time. Missing/inconsistent usage, transport failures, and a process crash leave the **entire** hold in place. Returned overrun is recorded without clipping and blocks further attempts.

Fallback models, output repair and redelivered workers share the same persisted run. A new process/context cannot reset monetary or attempt counters. The established three-attempt writing limit remains in effect. An unscoped `_chat` cannot silently switch to an in-memory spend budget. Cost metadata stores numeric amounts, validated quote metadata and fixed error codes; it stores no prompt, response, API key, provider error body or raw error message.

The input estimator is explicitly `utf8-json-bytes-framing-v1`: UTF-8 serialized-message bytes plus 64 units per message and 128 framing units. This is a conservative estimate with bounded payloads, not a certified tokenizer. Returned usage gives a better cost estimate at the admitted prices, not proof of an invoice. Provider-side billing rules, hidden charges, endpoint contract violations, or a tokenizer underestimate can exceed an estimate; the recorded overrun prevents additional calls but cannot undo a request already sent. No cache discount or free-tier entitlement is assumed.

## Price authority and expiry

`LLM_MODEL_COST_POLICY_JSON` is strict operator configuration. Missing rates/operation ceilings are unavailable, never zero. Prices must be positive integers, exact model endpoints must be unique, URLs must be public HTTPS without credentials/query strings, and each quote needs a finite, ordered, timezone-aware validity interval. The event also records the frozen operation policy version, current admission policy version and effective ceiling.

For a new explicit enhanced run, the operation quote is saved before execution and analysis-unit reservation. A future attempt checks both frozen and current price validity and permissions. A tighter current operation ceiling or input/output bound can deny or constrain a future attempt; it cannot increase the frozen authorization. A removed model or altered price/version/evidence requires a new reviewed operation. Historical quote, holds and settled estimates are never rewritten by policy changes. Expiry rejects calls even if the process was queued before the deadline. Published price boundaries require a new reviewed quote; they cannot silently continue under stale rates.

Use the generated [JSON Schema](MODEL_COST_POLICY.schema.json) as the authoritative configuration shape. This illustrative template deliberately contains placeholders instead of invented prices and is **not deployable JSON configuration** until every placeholder is replaced with a reviewed value:

```json
{
  "version": "<reviewed-policy-version>",
  "currency": "USD",
  "operation_limits_micros": {
    "job_match": "<positive integer USD micros ceiling>"
  },
  "pricing_quotes": [{
    "provider": "google",
    "model": "<exact approved requested model>",
    "api_base": "https://generativelanguage.googleapis.com",
    "version": "<reviewed-rate-version>",
    "price_source_url": "https://ai.google.dev/gemini-api/docs/pricing",
    "output_limit_source_url": "https://ai.google.dev/gemini-api/docs/generate-content/thinking",
    "usage_source_url": "https://ai.google.dev/api/generate-content",
    "valid_from": "<RFC3339 timestamp with UTC offset>",
    "expires_at": "<finite RFC3339 expiry with UTC offset>",
    "input_rate_micros_per_million": "<reviewed positive integer USD micros per million input tokens>",
    "output_rate_micros_per_million": "<reviewed positive integer USD micros per million output tokens>",
    "max_input_tokens": "<positive integer estimate bound>",
    "max_output_tokens": "<positive integer enforced output bound>",
    "output_limit_parameter": "max_output_tokens",
    "token_estimator": "utf8-json-bytes-framing-v1"
  }]
}
```

A $1 per million token rate is represented as 1,000,000 USD micros per million tokens. This is a units conversion, not a recommendation or asserted model price. Cost rounding uses ceiling division of the combined quoted input/output amount. Every model that may actually be attempted—including fallbacks—needs its own reviewed quote. A missing fallback quote stops admission before calling that model. Operators must review vendor output/usage contracts for compatible endpoints; the code does not prove that an arbitrary vendor honors a claimed parameter.

Known generation operation identifiers include `job_match`, `interview_questions`, `resume_tailor`, `resume_enrichment`, `resume_tailor_legacy`, `rewrite_bullets`, `interview_questions_legacy`, `job_match_legacy`, `match_question`, `market_analysis_legacy`, and `learning_strategy`. Include only deliberately authorized operations. Do not grant a wildcard ceiling.

## Deterministic and legacy compatibility

Basic matching, curated interview practice, direct-data answers, default resume parsing and other no-generation paths do not require a price policy. Mixed legacy wrappers may remain unquoted when their work is deterministic; their first actual provider admission freezes and persists the quote before network access. Explicit generation freezes upfront. A missing policy produces actionable optional-generation unavailability. Optional upload enrichment uses a narrowly typed configuration refusal to return the already-saved original, deterministic extraction, `enrichment_state="unavailable"`, an explicit warning and zero enrichment charge. Unrelated HTTP 503 exceptions retain their existing error behavior.

Migration `20261009_0010`, after `20261008_0009`, adds nullable quotes, monetary state/counters and per-attempt fields. It retains earlier telemetry and attempt counts without pretending that old zero-cost telemetry is a verified price quote. An unquoted legacy run with previous attempts or model-call history cannot acquire a fresh monetary budget; it must fail closed and be replaced through an explicit new operation. Refund of product analysis units does **not** release provider-cost holds.

Parent read-only runtime inventory on 2026-10-09 reported production API `00267-xot` and analysis worker `00021-5zd` using `gemini-1.5-flash`, with no model-cost policy configured. This differs from earlier documentation that assumed a newer model. This patch does not change that configuration and does not establish that the older model is available. Optional generation requires an exact current operator-approved model/price quote. Default deterministic paths remain usable.

## Rollout and recovery obligations

- Quiesce and explicitly retire **all** old AI writers, including synchronous API generation and workers, before allowing this schema/code to authorize calls. A request timeout or elapsed sleep does not prove process termination. Drain/inspect queued and running legacy operations and preserve uncertain outcomes.
- Apply the additive migration with verified backups and the established GCP/Vercel release process. Model-cost enforcement must never be claimed while an old writer can still run without it.
- Validate the trusted pricing JSON offline against `ModelCostPolicy`, including exact deployed model, endpoint, validity and every permitted fallback. Choose monetary/output bounds deliberately; small output caps can exhaust tokens before visible structured output, causing safe validation failure/partial guidance.
- Enable only after independent source/PG/migration review and a bounded operator-approved rollout. No live model request, model access check, billing-tier proof, invoice reconciliation, deployment or pricing change was performed for this implementation.
- Downgrade is schema compatibility evidence only, not authorization to resume old AI writers. It removes monetary enforcement metadata and maps unavailable telemetry to the legacy non-null representation. A precheck refuses conversion when any historical amount exceeds the old Integer range, without truncating it or mutating the schema/history. Keep the new revision and writers paused if refused.
- An unknown hold remains until complete trusted usage can be reconciled idempotently. This patch has no public override/refund endpoint for model spend and no provider-invoice ingestion. Never reset reservations as a worker recovery shortcut.

## Verification

Cost-focused checks passed locally: 47 tests, comprising 40 synthetic policy/service/provider invariants, 6 real PostgreSQL cases and clean SQLite migration. PostgreSQL evidence uses only the dedicated localhost database and a separate schema per test, with no customer data/model calls: concurrent last-affordable admission, shared concurrent attempt numbering, an actual child-process exit after committed admission, duplicate concurrent settlement, populated upgrade/downgrade/re-upgrade preserving unquoted history, and high-value downgrade refusal with unchanged history/revision. SDK/client tests cover native/compatible output parameters, pinned endpoint and one SDK attempt, cleanup on success/fallback/permanent error, missing/expired/revoked prices, overrun, unknown/inconsistent usage, immutable tightening and upload degradation without swallowing unrelated 503s.

Agent whole-backend verification with disposable PostgreSQL and Redis passed **1011 tests, zero skips** before the separate GCP SDK integration finished and before the final event-to-operation settlement binding check. After those last scoped changes, the focused AI/LLM/privacy/money/migration suite passed **208 tests**, including all 47 cost-focused checks. Scoped Ruff and Mypy passed (68 source files at the final Mypy run), `git diff --check` passed, OpenAPI export introduced no drift, and the delivered JSON Schema matches `ModelCostPolicy`. Parent must run the final combined suite after all agents freeze; these are agent-stage proofs, not a claim of final merged release validation. Synthetic fixture prices are isolated to pytest and explicitly named synthetic; they are not deployment defaults.

Official semantics reviewed: [Gemini thinking/output limits](https://ai.google.dev/gemini-api/docs/generate-content/thinking), [Gemini usage metadata](https://ai.google.dev/api/generate-content), [OpenAI chat completion parameters](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create), and [OpenAI token accounting](https://developers.openai.com/api/docs/guides/token-counting). These references support parameter/usage interpretation; they do not establish access, current operator rates or invoice ceilings.

## Final root component integration

The corrected default root suite passed **1,261 tests with no failures, errors or skips**.
[The integration report](ROOT_COMPONENT_INTEGRATION_2026-10-09.md) retains the earlier
failed stage and distinguishes local proof from zero-traffic compatibility staging.
Production money cutover and browser authority remain unenabled.

The pinned Google client now also uses a native 90,000 ms request timeout. This is not an absolute end-to-end or invoice deadline. Account metadata checks and the offline price candidate are recorded separately in [the rollout plan](MODEL_COST_ROLLOUT_2026-10-09.md); no generation request or runtime price activation was performed.
