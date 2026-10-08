# Model configuration and bounded-cost rollout candidate

Status: prepared configuration and read-only account metadata evidence. No deployment,
generation request, credential rotation or price-policy activation is established here.

## Model selection and price authority

The serving API and analysis worker name `gemini-1.5-flash`, without a monetary policy.
A credential-in-memory model-metadata check returned 404 for that exact model. The five
existing fallback identifiers returned matching metadata, including `generateContent`.
See the [runtime inventory](evidence/2026-10-09-model-runtime-drift.json) and
[account metadata](evidence/2026-10-09-model-account-metadata.json). These reads establish
metadata availability only; they do not prove generation quota, billing tier, output
quality or a provider invoice. No key is retained in either evidence file.

The candidate primary is `gemini-3.6-flash`. It is already first in the application's
fallback list, so this removes the unsupported primary's attempted request without
introducing a new model family or claiming superior quality. The existing fallback
order stays unchanged. Every possible fallback has its own exact quote in
[the candidate JSON](../../infra/gcp/model-cost-policy.candidate.json).

Published standard text rates in USD per million tokens, checked on 9 October 2026:

| Exact model | Input | Output, including thinking |
| --- | ---: | ---: |
| gemini-3.6-flash | 0.75 | 3.75 |
| gemini-3.5-flash-lite | 0.30 | 2.50 |
| gemini-3.5-flash | 1.50 | 9.00 |
| gemini-3.1-flash-lite | 0.25 | 1.50 |
| gemini-2.5-flash | 0.30 | 2.50 |

Source: [Google's pricing page](https://ai.google.dev/gemini-api/docs/pricing).
The 3.6 rates change on 1 January 2027. The candidate expires earlier, on
8 November 2026 UTC, and needs monthly review. It assumes standard synchronous text
requests, without grounding tools, audio/image generation, batch, priority, cached-input
discounts or free-tier entitlement. Rates use integer USD millionths in the JSON.

## Deliberate application limits

All five quotes cap the input estimate at 65,536 units and requested output at 8,192
tokens. The input estimator counts serialized UTF-8 bytes and framing; it is conservative
application admission metadata, not a certified tokenizer. Google includes thinking
within the output limit; a response can exhaust that budget before producing usable
structured output. See [thinking limits](https://ai.google.dev/gemini-api/docs/thinking)
and [usage fields](https://ai.google.dev/api/generate-content).

The proposed operation ceiling is USD 0.25 for enhanced matching, interview practice,
enrichment, rewriting, market analysis and learning strategy; USD 0.50 for the two
tailoring paths; and USD 0.15 for a grounded enhanced answer. These are engineering
choices for quoted maximum estimated spend across the existing shared three-attempt
limit. They are separate from candidate product units and search/application prices.
A high-priced fallback or retained unknown hold may consume the available capacity
and prevent the next attempt. Failed/ambiguous requests do not gain fresh capacity.
No invoice cap or measured savings is claimed. Quality and useful-completion cost need
independent evaluation before increasing limits or changing fallback order.

## Required cutover

1. Pass exact-commit CI and preserve the additive money history. Establish the tested
   compatibility API and worker with optional generation explicitly paused.
2. Fence all legacy provider credentials and database writers; queue pausing, zero
   traffic or a request timeout alone is insufficient. Preserve unresolved outcomes.
3. Apply schema `0010` only through a separately verified cutover. The legacy release
   script must refuse this transition. Keep original/manual workflows available.
4. Configure the exact candidate primary, native public endpoint and validated policy
   on the cost-enforcing API and analysis worker. Employer workers receive no model key.
5. Verify the account's billing/privacy tier and bounded synthetic output quality before
   enabling generation. Inspect current quote expiry and admission on both services.
6. Enable optional generation and resume its queue only after these checks. Rollback
   retains `0010` and pauses generation; it must not restore old unbounded writers.

The candidate JSON is preparation, not proof that any of these cutover steps occurred.
