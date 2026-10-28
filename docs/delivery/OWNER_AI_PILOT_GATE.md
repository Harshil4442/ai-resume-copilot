# Owner-only AI pilot gate (implementation branch)

This branch adds a defense-in-depth gate for one owner AI operation after the
schema25 base deployment. It is **not a production activation**. The current
base release keeps `OPTIONAL_AI_GENERATION_ENABLED=false`; this branch does not
change Cloud Run, secrets, database schema, billing or checkout.

## Runtime contract

- Production defaults to `HIREWIZ_AI_PILOT_MODE=closed` even if an older global
  generation switch is accidentally true. The `test_legacy_open` mode is
  accepted only inside pytest with `APP_ENV=test`.
- `owner_only` requires a positive existing user ID, exactly one supported
  asynchronous operation (`resume_tailor` or enhanced
  `interview_questions`), one exact Gemini model, and a UTC start/expiry window
  of no more than 24 hours. A missing, malformed or expired field closes the
  route. All other users and AI routes stay closed.
- Admission counts frozen model-cost runs for the owner in that window. The
  owner row is already locked before a new run is admitted, so two requests
  cannot both consume the one-operation quota. A failed or uncertain run keeps
  its quota; retrying that work requires a new reviewed window, not a hidden
  second operation. Existing idempotent reads/replays return the prior run.
- The accepted run has a single model-attempt limit. Both worker pre-claim
  checks and the model client recheck owner, route, expiry, exact Gemini model,
  one-attempt budget, durable quota and provider origin before `countTokens`
  or generation. Alternate credit-budget adapters cannot bypass this fence.
- The reviewed expense policy must have at most US$3 monthly AI authorization,
  at most the same daily/subpool authorization, and an adverse exchange rate
  of at least ₹100/US$. This leaves a ₹150 planning buffer beneath the current
  ₹450 AI Studio project cap; Google's cap can overrun briefly and is not a
  substitute for the durable internal hold.

## Activation still required

1. Finish scanner recovery and promote the schema25 base release. Verify API,
   analysis worker and document-worker pins, current cost-policy/model-quote
   secret references, and all old/new admission switches closed.
2. Review and publish a **new, lower** expense-policy secret. The existing
   `hirewiz-expense-policy-20261014:4` allows US$70 monthly and deliberately
   fails this pilot gate. Reconcile actual Gemini balance/spend and route cost.
3. Build and deploy this branch with a separate same-image, journaled pilot
   operator. The operator must prove the exact completed schema25 release and
   previous true AI state before opening only the owner scope and global AI
   switch on API and analysis worker; close them on timeout/failure. Never edit
   a Cloud Run flag ad hoc or open checkout, unified credits or final submit.
4. Run one synthetic, owned AI journey, verify one attempt and its retained
   model-cost liability/result, then close the pilot. Public paid optimized AI
   still requires the independent human-quality, shadow-accounting, invoice
   and commercial-hosting gates in the main rollout requirements.

The scope fields are `HIREWIZ_AI_PILOT_MODE`,
`HIREWIZ_AI_PILOT_OWNER_USER_ID`, `HIREWIZ_AI_PILOT_OPERATION`,
`HIREWIZ_AI_PILOT_MODEL`, `HIREWIZ_AI_PILOT_STARTS_AT` and
`HIREWIZ_AI_PILOT_EXPIRES_AT`. These are operator-owned Cloud Run settings,
not client input.
