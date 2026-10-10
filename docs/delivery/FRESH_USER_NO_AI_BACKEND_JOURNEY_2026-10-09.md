# Fresh-user backend journey without AI

Status: integrated in the parent working tree with a passing focused test. This is not a production or browser acceptance claim.

## Scope and isolation

`backend/tests/test_fresh_user_no_ai_journey.py` runs the full FastAPI application and its real JWT authentication, ownership checks, service implementations, normalization, package approvals, payment verification and ledgers. It uses a newly created, foreign-key-enabled SQLite database, with no users, resumes, analyses, employer postings, skill-coverage cache or credit events at the start. It neither uses nor changes a shared PostgreSQL schema or Redis namespace.

The snapshot was copied from checkout `772634d9ce8445acd559564f9b33a503ee7f0943` plus its current uncommitted source, including the model-cost budget and optional-generation gate work. Only this new test and this delivery note were authored in the isolated snapshot. Parent integration and the merged suite remain separate gates.

The fixture removes the supported model-key variables and `LLM_MODEL_COST_POLICY_JSON`, disables optional generation and automatic submission, and sets inline dispatch for this local test. Calls to the model adapter, Google SDK constructor or monetary-attempt admission fail immediately. Real socket connections also fail. Only HTTP transport at three external boundaries is synthetic: public employer-feed GETs, payment order creation/capture and welcome email delivery. API responses and service success results are not mocked.

Password registration currently creates an active account; no verification/activation success is invented. Welcome email is processed through the actual durable notification dispatcher with a synthetic Resend response. The operator account also uses signup/login and receives authority from a test-only configured admin email.

## Connected assertions

1. Unauthenticated access is rejected. A new candidate registers and logs in, and their actual welcome outbox item is delivered once through the synthetic mail boundary.
2. The candidate updates their profile and uploads two real DOCX files, representing the original resume and their own custom resume. Deterministic extraction succeeds without optional enrichment. The original bytes remain downloadable by their owner; another authenticated account cannot download them.
3. The candidate creates and explicitly approves evidence through the evidence API. Another account cannot change it.
4. An authenticated operator registers a fictional employer and exact Greenhouse tenant through the source API. A durable refresh imports four synthetic postings using the public-feed adapter and GET-only HTTP. The source remains manual-only and the catalog reports automatic submission disabled and worldwide recall unverified.
5. A real checkout order is verified against a synthetic supplier response. A correctly signed synthetic `payment.captured` webhook grants 500 service credits. Replaying the same capture does not grant them twice; checkout creation alone does not grant credits.
6. The candidate requests three jobs published within seven days. The actual search service excludes the old and unrelated fixtures, delivers two recent matching postings, reserves six credits, charges four and refunds two. The same idempotency key returns the existing search; a second equivalent query does not charge again for already delivered openings.
7. An opportunity is created from one discovered posting. Real analysis-run APIs dispatch and complete Basic matching and eight Curated interview questions. A direct answer cites the candidate's approved evidence. All three operations have zero generation attempts and no model-cost quote or model-call event.
8. The original application package is previewed and approved. Selecting the candidate's custom resume changes its digest and revokes the previous approval. The custom file is previewed byte-for-byte and separately approved. Unapproved or stale-package execution is rejected.
9. Execution returns a manual handoff to the exact employer-origin application URL. It creates no employer submission attempt, automatic-apply outbox event, application credit reservation or receipt. Reserved and charged application credits are both zero.

## Separate accounting

Under the explicit test pricing, search charges two service credits per newly delivered job: 500 granted minus four charged leaves 496. Manual handoff leaves that balance unchanged. Basic matching, Curated interview practice and the direct answer each consume one existing product analysis unit: 50 minus three leaves 47. Zero model calls does not mean every product operation is free; product units, search/application service credits and provider-cost accounting remain separate.

## Verification

- The connected test passed: **1 passed**, no skips (final local run, 1.34 seconds).
- Ruff check passed; Mypy passed for the new test; the file is Ruff-formatted.
- Every original/custom artifact byte and SHA-256 binding is checked. Approval revocation and ownership denials are checked through the actual APIs and persisted rows.
- Persisted rows confirm zero model events, zero generation attempts, an empty skill-coverage cache, one payment grant, zero automatic-application reservations/attempts/outbox entries and no employer POST.

## Parent integration

The parent copied the frozen test byte-for-byte (SHA-256 `0077d3207de8860c1aa7af9405689ceeb67d069d72cb0429e78abee0a299a0bf`) and added it to the CI lint scope. The connected test passed again in the parent checkout: **1 passed**, no skips, 1.32 seconds. Ruff and Mypy passed for that file. Full merged-suite verification and deployment remain separate gates. The earlier isolated-source snapshot and verification above remain historical evidence.

## Remaining acceptance limits

The first parent full-suite run after integration finished with **1,235 passed and one failure**, no skips, in 60.72 seconds. The journey test passed. The failing existing Firestore-emulator contention case expected exactly one aborted transaction and one committed transaction, but observed two definite aborts. That concurrency assumption is under investigation; this run does not count as a passing merged release gate.

The employer, candidate, credentials, mail response and payment capture are synthetic. This does not verify actual email delivery, payment capture, official employer attribution, live feed freshness, worldwide job recall, a provider invoice, GCP routing or concurrent PostgreSQL behavior. It does not exercise the Next.js BFF, a real browser, device pairing, the companion, an employer form or actual application completion. The receipt is intentionally absent. Those remain independent acceptance gates; automatic submission stays disabled.

## Final root component integration

The corrected default root suite passed **1,261 tests with no failures, errors or skips**.
[The integration report](ROOT_COMPONENT_INTEGRATION_2026-10-09.md) retains the earlier
failed stage and distinguishes local proof from zero-traffic compatibility staging.
Production money cutover and browser authority remain unenabled.
