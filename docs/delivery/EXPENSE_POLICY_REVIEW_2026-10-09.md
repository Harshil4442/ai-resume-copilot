# Finite pack expense review

The owner authorized expense estimates. The updated policy has independent offline
clearance and `provenance: estimated`; it has **not** been activated in production.
No invoice, actual merchant fee, bank balance or guaranteed profit is asserted.

| Pack | Price | Job-service credits | Analysis units | Modeled allocated margin |
| --- | ---: | ---: | ---: | ---: |
| Starter | ₹649 | 100 | 2 | 41.57% |
| Growth | ₹1,099 | 300 | 6 | 42.44% |
| Scale | ₹2,199 | 700 | 15 | 46.24% |

These results assume full permitted use, ₹15,000 monthly fixed/subsidy allocation,
100 paid packs per month, 20% expense contingency and the existing bounded search,
application and analysis costs. Search costs one service credit per delivered result;
a supported confirmed application costs twenty. Manual careers-portal handoff costs
zero. New tailoring costs two analysis units. Units are software usage allowances;
optional model calls still require separate consent and cash-budget admission.

The updated assumptions reserve 5% gateway fees plus 18% tax on that fee, ₹1 fixed
expense and ₹10 settlement/currency expense per purchase. The existing 18% inclusive
sale-tax estimate and 5% refund reserve remain. These assumptions are estimates, not
the merchant's verified tax treatment. The stronger policy raises the application
expense floor to seventeen credits; the chosen twenty still covers that floor.
Homogeneous forecast break-even is 49 Starter, 35 Growth or 20 Scale purchases per
month. Low purchase volume can still leave a business loss.

Razorpay's read-only supported-method endpoint returned cards, EMI, UPI, netbanking,
wallets and pay-later options. It does not verify negotiated merchant fees or the
complete dashboard configuration. The review includes that broad supported-method
superset in its conservative estimate. This is the meaning of
`all_enabled_payment_methods_covered` for this **estimated** policy. It is not an
assertion of instrument restrictions or an actual fee-contract audit. Published
ordinary and premium rates supply the planning basis:
[supported methods](https://razorpay.com/docs/payments/payment-methods/netbanking/),
[pricing](https://razorpay.com/pricing/), and
[premium/international/EMI fees](https://razorpay.com/solutions/e-commerce/).
Custom contracts and optional instant-settlement charges remain unknown. Known
adverse fees must pause new cost-bearing work, while accepted purchases and refunds
continue to be honored.

A read-only production ledger observation through **9 October 2026, 18:37:32 UTC**
found schema `0009`, one created/unpaid order, zero captured orders/refunds,
39 terminal analysis runs and no employer applications. Zero captures do not mean
zero cloud or provider expenses. The retained-liability table is not yet deployed;
legacy failed/provider exposure and external credential consumers remain unresolved.
The reconciliation cutoff covers the recorded aggregates only. The reviewed policy
expires **16 October 2026, 18:37:32 UTC** and must be refreshed after review.

The [reviewed JSON](../examples/expense-policy-estimated-reviewed-2026-10-09.json)
is configuration to deploy explicitly after the remaining gates. It is never loaded
as a fallback default. The [review evidence](evidence/2026-10-09-estimated-expense-policy-review.json)
preserves the input hashes, independent arithmetic/boundary checks, bounded ledger
observation, supported-method response summary and unknown facts.

- [x] Owner-authorized conservative estimates and exactly three finite packs.
- [x] Independent offline arithmetic and authority-boundary review.
- [x] All five exact `29919c2` CI lanes; 2,341 backend tests pass without skips.
- [ ] Independent clearance and integration of known fee/tax retention repair.
- [ ] Separate runtime/migration credentials, old-writer fencing and fresh backup.
- [ ] Schema `0010`–`0013`, immutable joined rollout and production verification.
- [ ] Actual invoice reconciliation and ongoing expense review.

Historical accepted prices and paid access terms remain intact. Policy approval is
an offline financial checkpoint; it does not certify the monetary cutover or enable
browser disclosure, submission, native compilation or optional generation.
