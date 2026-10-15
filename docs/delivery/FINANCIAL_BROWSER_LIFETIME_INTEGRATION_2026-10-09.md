# Financial, browser and account-lifetime integration

Three independently reviewed repairs are integrated on top of `c253038e`.
Production remains the earlier `e7d8f2b` backend and schema `20261008_0009`.
This checkpoint does not complete the paid application service or its deployment.

Provider-cost holds now survive customer erasure and telemetry pruning in a detached,
pseudonymous financial ledger. A trusted in-flight completion can settle its original
quote once without recreating the account. Unknown or invalid usage retains the full
hold. Result writers acquire the account-lifetime guard before Resume locks, mutations
or flush; provider/rendering work stays outside that transaction. This is estimated
cost accounting, not invoice reconciliation or independent database-restore protection.

Manual handoff displays the saved exact file/hash, destination, answers and consents
before review. Changed packages require fresh approval. Existing approve/execute APIs
record `manual_handoff` before exposing the employer link. Preparation performs no
fill/upload/send and charges no automatic-application credits. The real-stack browser
journey exercises UI registration, parsing, preferences, search, signed payment
verification, credits, exact original/custom bytes and ownership. Only external
feed/payment transports are synthetic. Owned POSIX process groups are verified empty.

Protected account/session writers retain coordinator-owned canonical bytes/digests;
journal ports receive detached copies. Nested mutations cannot redirect a saved event
cut, and uncertain commits return no session context. Production factories remain
unavailable. A protected denial with no native persistence can still leave old readers
authorized under the injected OPEN witness: the durable denial barrier and complete
restore/reopen protocol remain mandatory activation gates.

## Verification

- The collected pre-lifetime default backend suite passes **1,539 cases**, and the
  additional lifetime integration passes **65 cases**, with no failures/errors/skips.
  These are separate local checkpoints, not one claimed 1,604-case local default run.
- Independent financial review passes **149 cases**, including 28 actual PostgreSQL
  cases; independent lifetime review passes **630 cases**, including four independent
  probes and three actual PostgreSQL credential cases. Independent browser review passes.
- Frontend lint/types, **72 units**, production build and **190 responsive fixtures**
  pass. The connected Chromium → NextAuth/BFF → FastAPI → isolated PostgreSQL journey
  passes at five widths: zero AI calls, one credit grant, search reserve six/charge
  four/release two, zero application fees, stale approval rejection and no receipt.
  Product/runtime bytes remain identical; subsequent changes affect tests/CI and
  disabled additive lifetime modules.
- Actual CI Ruff and CI typing plus three harness files pass, **90 Mypy files** in total;
  compilation, six prompt fixtures and unchanged OpenAPI also pass.
- Prior exact `c253038` CI passes all four jobs, run `37862213523`. This new source
  requires its own five-job CI, including the connected cold-browser journey.

The initial 1,538-pass/one-failure run is preserved. Its unowned-evidence test inspected
the developer checkout before its intended assertion; it now uses an owned credential-free
source directory. Another run encountered confirmed shared-emulator Java heap exhaustion.
Only the owned pytest was interrupted; the old container was preserved. Fresh validation
uses a separately owned pinned emulator and a validated loopback-only test port. The
subprocess fixture explicitly carries that port; the affected 32-case selection passes.
CI receives a larger finite JVM heap. Production code and RPC deadlines were not weakened.

Superseded financial deadlocks, browser orphan cleanup and mutable-plan failures remain
preserved. Root also retains native CI entries, extends financial/helper/harness static
checks, pins the fifth job's actions and removes one test-file trailing blank line.
[Integration evidence](evidence/2026-10-09-financial-browser-lifetime-integration.json)
binds source hashes, separate test scopes, augmentations and preserved failures.

## Release work

[Monetary cutover preparation](MONETARY_CUTOVER_PREPARATION_2026-10-09.md) records the
current service/job/secret inventory and coordinated writer-retirement sequence.
No credentials, queues, production traffic or database were changed. `0010` requires
the separate fenced cutover; retained liabilities prevent destructive downgrade.
A compatible frontend release does not deploy these backend modules or advance SQL.

Protected denial/restore, actual cloud controls/KMS, BFF/MV3 pairing, permitted Razorpay
form assistance, LaTeX/document isolation, independent coverage/ranking evaluation,
field/load tests and monitored rollout remain in [the register](REMAINING_REQUIREMENTS.md).
