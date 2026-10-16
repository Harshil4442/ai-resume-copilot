# BI08 finite packs and expense authority — implementation handoff

Status: exact V2 source independently reviewed and integrated; full composed source `44e0d92` passes all five CI jobs. Legacy checkout and frontend offer retirement are deployed separately. Finite-pack accounting, prices and funding authority are not yet activated in production. The operator must review and approve a fresh finite expense policy before new cost-bearing quotes or checkout can become available. Existing accepted orders and quotes retain their stored prices and terms.

## Customer offer

Exactly three new prepaid bundled offers replace new Premium/unlimited sales. Both balances are granted atomically after verified capture; a checkout redirect never grants access.

| Pack | Inclusive estimated price | Job-service credits | AI analysis units |
|---|---:|---:|---:|
| Starter | ₹649 | 100 | 2 |
| Growth | ₹1,099 | 300 | 6 |
| Scale | ₹2,199 | 700 | 15 |

New reviewed service prices are one credit per newly delivered search result and twenty credits per confirmed assisted application. A manual careers-portal handoff has zero automatic service fee. No new unlimited offer or unlimited balance is advertised. New tailoring quotes reserve two analysis units; other existing analysis prices remain one/one/five/zero as defined in the server operation table. Previously accepted runs retain their original stored unit amount, including an old ten-unit tailoring quote. AI consent and analysis-unit accounting remain separate from service-credit consent.

Historical Premium orders and their unexpired accepted access terms are preserved. New provider work during that term must still pass current cash funding and ordinary rate limits. No artificial retrospective ten-unit or thirty-operation cap is imposed. Exhausted or unavailable funding pauses new work with an explicit message rather than changing an accepted price or deleting paid access.

## Expense estimates and calculation

The original candidate JSON remains historical planning input. The stronger [reviewed estimate](../examples/expense-policy-estimated-reviewed-2026-10-09.json) has independently cleared offline calculation review under the owner’s authorization to estimate costs. Its [review report](EXPENSE_POLICY_REVIEW_2026-10-09.md) records assumptions and unknowns. It expires on 16 October 2026 at 18:37:32 UTC and has not been activated in production. Actual-variance review and monthly reconciliation remain required. Missing, expired, malformed, mismatched, or unsafe authority denies new cost-bearing checkout, quotes and provider attempts.

All accounting uses integer paise and integer USD microdollars with exact Fraction arithmetic. Expense components and credit floors round upward; receipts round downward. The current planning assumptions are estimates, not invoices or legal tax determinations:

- ₹100 per USD adverse exchange bound.
- ₹13,000 monthly platform/operations plus ₹2,000 marketing/exposure allowance; 100 paid packs/month allocation yields ₹150 per pack. Cloud costs may exceed this bounded low/moderate-volume forecast; full-capacity estimates and actual account facts remain in the parent cost policy document.
- 5% payment fee plus 18% tax on that fee, ₹1 fixed processing allowance and ₹10 settlement/FX allowance per purchase, conservative 18% inclusive sale-tax reserve, and 5% gross refund/chargeback reserve. Unknown actual fees and fixed expenses are not reported as zero.
- ₹0.25 per delivered search job and ₹5 per confirmed browser-assisted application including normal preparation/failure allowance. A separately bounded paid search request may prepare up to the existing 1,000-candidate limit; its failed/empty preparation estimate is ₹0.50 per request plus 20% contingency, rather than charging the full delivered-job cost for every undelivered requested result.
- Maximum ₹26.50 per AI unit: USD0.25 at the adverse FX bound plus ₹1.50 worker overhead for up to three attempts. Tailoring's total operation provider ceiling is USD0.50 for two units; other configured operation ceilings are bounded explicitly. Runtime cash admission counts ₹0.50 for every provider attempt, including failed, tiny, unknown and detached historical attempts. This cash overhead never becomes a fabricated provider invoice amount.
- Internal 40% contribution target and 20% contingency; neither is asserted as an industry rule or guaranteed profit.

Worst full use includes every service credit in its most expensive permitted mix, every promised AI unit, platform allocation, and all permitted maximum discount/bonus combinations. The stronger reviewed no-promotion estimate yields net receipts of 46,825 / 80,055 / 161,285 paise. Buffered allocated costs are 27,360 / 46,080 / 86,700 paise; modeled allocated margins are approximately 41.57% / 42.44% / 46.24%. The upward service floors are search1/application17; chosen application20 gives headroom. At the forecast ₹15,000 monthly fixed budget, homogeneous full-use purchases break even at approximately 49 Starter, 35 Growth or 20 Scale packs. Low sales volume can still lose money. These are forecast results, not actual profitability; the prior candidate’s lower-fee numbers remain historical input only.

`backend/scripts/audit_expense_policy.py` produces the deterministic pack audit and 10/50/100-volume forecast. Its optional explicit database mode is read-only, aggregates recorded payment/refund/provider facts, reports unknowns, and does not invent actual shared fixed expenses or a profit result.

## Immutable entitlement and funding provenance

`PaymentOrder.cost_policy_snapshot` stores the accepted policy and both bundled grants. Capture, replay, partial/full refund and account deletion keep financial history separate from browser state. Refund amounts upward-round proportionally for both entitlements. Spent new bundled grants can produce negative balance debt; replay does not create another grant. Old fulfillment does not depend on the current policy, current catalog or newly selected prices.

The shared owner lock protects reserve, capture and refund accounting. Funding uses **NEW-FIRST**: current paid bundled units are allocated before legacy/free units, and only the actual paid portion is counted against current grants. For example, legacy500 plus a new100 service bundle followed by a500-credit use consumes the paid100 first and legacy400; the next100 remaining are legacy. A later100 paid top-up is immediately current-funded and does not inherit a fictitious400 debt.

Service reservations freeze the server-owned paid allocation in `cost_policy_snapshot`. Reserved work holds it; settlement consumes `min(committed credits, allocated paid credits)` and restores the exact unused paid portion. Partially uncovered jobs are conservatively rounded upward to a full legacy job cost. Caller-supplied allocation fields are rejected or never used.

AI reserve/release receipts persist a server-authored `Paid allocation N; ...` prefix in the existing immutable usage ledger. The SQL projection consumes exactly reserveN minus releaseN. It does not mistake free use for paid debt or restore paid units for a pre-purchase operation. This receipt survives run input/model-quote telemetry purging. A private run model quote may also carry `expense_paid_units`; it is not a detached financial-quote field. A partially free/paid AI operation conservatively uses the promotional cash pool for its entire model cost. No extra customer or ledger columns were added for provenance.

## Cash admission and unsuccessful work

Two PostgreSQL transaction advisory locks serialize cross-owner model and service cash admission, after the existing owner lock. Queries use database aggregates and existing created/state indexes. They do not load the entire financial history into application memory.

The monthly ₹2,000 marketing allocation contains ₹1,000 promotional/failed AI, ₹500 legacy Premium AI, and ₹500 legacy-service/unsuccessful-service exposure. This is finite estimated subsidy, not unbounded access or debt hidden in new pack receipts.

Legacy cheap service quotes and unproven historical balances retain their full separate estimated cash hold even after their customer credits are returned. Unsettled older holds survive month boundaries and account erasure. Paid search preparation reserves at most ₹0.60 per fresh request in the same finite service exposure pool. Fully successful paid completion releases that provisional failure portion; partial completion upward-rounds its remaining proportion, and empty/failed completion retains it. Returned customer credits therefore cannot repeatedly authorize free preparation after the pool runs out. Existing idempotent search replay does not reserve another fresh request. For paid application preparation the corresponding bounded per-application estimate is retained on failure.

Every new model attempt passes current daily/monthly funding plus applicable promotional or legacy exposure funding. Unknown liabilities retain the larger held/settled amount, including old months. Product-unit restoration cannot erase failed provider work. A prepaid attempt uses `expense_pool: failed_work` provisionally until a valid result and usage commit succeed in the same transaction. Merely receiving and settling a provider response does not free that risk budget; another owner cannot race ahead before result validation. Successful commit releases this provisional pool to prepaid; unknown state still holds risk. Detached quote provenance remains exactly the optional complete triple `expense_policy_version`, `expense_funding`, `expense_pool` with the established enum values.

Recorded payment fee/customer-tax above the reviewed assumption, or processed refunds above the conservative sales reserve, pause new authority pending review. Accepted order capture and refund processing remain available. Actual invoices and unreported fees still require operator reconciliation.

## Migration and rollout

Migration `20261009_0013` follows the preferences migration `20261009_0012`. It adds only two nullable JSON snapshots plus five ordinary budgeting indexes on payment paid time, processed-refund time, model created/state-created time and service state-created time. ORM metadata matches. Downgrade refuses if stored expense snapshot financial history exists, before dropping anything.

Use a guarded writer-drain maintenance window for the ordinary index build, with measured lock duration and a schema backup. This source does not claim concurrent zero-lock index installation, full-scale query performance, or a completed production migration. Review the actual plan/load before increasing fleet limits. Deploy joined native/preferences/account changes and regenerated contracts together, review the finite policy independently, then enable new checkout only after funded limits, alerts and reconciliation are configured.

## Verification checklist and remaining release gates

- [x] Finite three-pack source catalog and customer disclosures; historical offers closed in new release source. [Live legacy checkout retirement](LIVE_CHECKOUT_RETIREMENT_2026-10-10.md) is now verified separately; finite-pack purchase activation remains pending.
- [x] Worst-use/discount/bonus/adverse-FX calculator and required-expiry/review failure cases.
- [x] Atomic two-balance capture, replay, partial/full refund and spent-grant debt checks.
- [x] NEW-FIRST mixed legacy/current service and AI reserve/release/top-up tests, including spoof rejection and telemetry purge.
- [x] Empty/partial paid preparation cash holds; product-unit restoration does not restore failed cash exposure.
- [x] Settled-but-uncommitted prepaid model result retains risk; provider invoice ledger remains separate from overhead.
- [x] Frozen candidate source and preimage/hash manifests for independent review.
- [x] Independent financial review of exact V2 source: 108 baseline checks, 22 actual PostgreSQL baseline passes and five distinct actual PostgreSQL boundary checks. The root now integrates the two-file historical-fixture/head repair; 40 actual PostgreSQL checks and four composed boundary checks pass without weakening the financial-history guard.
- [x] Root final connected cold browser → BFF → backend → SQL replay passed, with 240 frontend unit checks, production build, and affected search/application UI at all five widths. First packaging and stale-selector failures remain preserved. All five exact `29919c2` CI lanes now pass; the backend suite passes 2,341 tests without skips.
- [x] Root preimage-checked 46-path composition with reviewed account, preferences and native resume changes; regenerated contracts. Published commit `92d1cc5` passed four CI lanes; its backend lane passed 2,337 tests and failed four stale integration-fixture expectations. The reviewed three-file test-only repair passes 87 focused checks; exact `29919c2` complete CI subsequently passes all five lanes, including 2,341 backend tests without skips. This is source verification; production activation remains pending.
- [x] Owner-authorized stronger estimated expense policy independently reviewed offline; [bounded review evidence](evidence/2026-10-09-estimated-expense-policy-review.json) retains actual unknowns.
- [x] Full composed `44e0d92` source CI: 2,447 backend tests, 240 frontend units, 215 browser cases and all five exact checkout proofs; [evidence](evidence/2026-10-10-exact-source-ci.json).
- [x] Live frontend unlimited/legacy-offer retirement promoted separately as `67a51f2` on both HireWiz domains; [bounded release report](FRONTEND_OFFER_RETIREMENT_2026-10-10.md).
- [ ] Current runtime authority, known variance, funded legacy exposure, alerts, monthly reconciliation and live catalog verified after deployment.
- [ ] Guarded migration, approved deployment and production verification by the root task.

No live payment, model, employer form submission, provider credential update or cloud mutation is performed by this authoring work. Independent scope clearance and complete deployment remain separate release requirements.

The [CI fixture repair evidence](evidence/2026-10-09-finite-pack-ci-fixture-repair.json) preserves the failed run and exact changed test inputs. It explicitly targets schema0011 in the scoped additive test, retains schema0013 after the transaction-wide refused downgrade, and exercises the new Starter purchase rather than reopening retired checkout. The connected journey still proves zero model calls, exact source bytes, ownership, approval invalidation and no employer submission.
