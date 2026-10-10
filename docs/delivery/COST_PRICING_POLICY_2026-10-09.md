# Expense-based pricing and margin protection

Status: three finite packs and expense safeguards are integrated locally after independent
financial source review. Composed verification, deployment and actual-account expense
reconciliation remain open. This document does not activate new prices or
claim that current plans are profitable. GCP and Vercel remain the deployment platforms.

The owner subsequently authorized conservative estimated amounts and requested removal
of unlimited usage in favor of two or three finite credit schemes. Implement exactly
three prospective bundled packs with distinct job-service and AI-analysis balances.
Remove unlimited offers from checkout and public copy. Preserve accepted old orders,
quotes and existing paid access periods; separately budget remaining legacy exposure.

## Current baseline and immediate gap

The catalog currently defines ₹499 for 500 job-service credits and ₹999 for 30 days
of Premium. Employer configuration starts at one credit per newly delivered job and
five per confirmed complete automatic application. Premium, analysis units and
job-service credits are separate entitlements. These existing prices were introductory;
their configuration alone does not establish a margin after all expenses.

The prepared model policy permits up to USD 0.50 in estimated provider spend for a
tailoring operation, separately from infrastructure. Five service credits from the
current pack represent ₹4.99 of gross receipts. Those five credits must never silently
include that tailoring allowance. Original/custom resume use and optional, separately
quoted AI tailoring must remain visible before the candidate chooses a package.

Existing shared attempt limits, retained unknown model-cost holds, deterministic
search, prepaid ledgers and receipt-based application settlement remain required.
They bound parts of the expense; they do not establish whole-product profitability.

## Expense authority

Maintain a dated, versioned policy with reviewed inputs and finite expiry. Use actual
account invoices/settlements where available and conservative vendor rates or capacity
bounds where actual history is missing. Never assume an absent invoice means zero cost.

| Cost | Required input and allocation |
| --- | --- |
| Optional model work | Exact endpoint/model rates, input/output limits, shared attempts, failed/unknown holds and tool charges; convert using a reviewed adverse FX bound |
| Document work | Parse, antivirus/quarantine, native compile and rendering CPU/RAM/time; artifact retention and download egress |
| Employer discovery | Public-feed refresh fleet cost, licensed locators if approved, normalization/storage and deterministic search; include empty/error refreshes |
| Application assistance | Server authorization/checkpoints, form preparation, supported execution and receipt reconciliation; include unsuccessful work in the pooled allowance |
| Platform | GCP compute, database, Redis, Firestore, GCS, Tasks, logs, backups, monitoring, Vercel, domains and unavoidable minimum capacity |
| Payments | Actual merchant contract by enabled payment method, fee taxes, fixed fees, settlement/FX costs, refund fee treatment and chargebacks |
| Operations | Support allowance, abuse/free-trial budget and a contingency reserve; keep development expenditure separately identified |

Google publishes model-specific input/output prices and separate tool charges on its
[Gemini pricing page](https://ai.google.dev/gemini-api/docs/pricing). Use standard paid
rates for capacity planning rather than relying on temporary free quotas or cache hits.
The current prepared model identifiers must each retain their own valid quote.

[Cloud Run pricing](https://cloud.google.com/run/pricing) varies by region and billing
mode. Use the actual deployed resources and include the other GCP products above.
[Vercel Pro](https://vercel.com/docs/plans/pro-plan) has a USD 20 monthly platform fee
with an included deploying seat and usage allowance; additional seats and usage can
cost more. This is a vendor rate, not proof of this account's plan or invoice.

[Razorpay's published payment FAQ](https://razorpay.com/solutions/e-commerce/) describes
2% platform fees plus fee GST for ordinary domestic instruments and 3% plus fee GST
for listed higher-cost instruments. Other current pricing pages or a negotiated
merchant contract may differ. Validate the actual enabled-method contract, additional
settlement charges and refund terms before choosing the production expense bound.
Fee GST and any tax on HireWiz's sale are separate inputs; do not infer the business's
tax treatment from a gateway marketing page.

## Calculation and policy decisions

Use integer minor currency units and Decimal/rational arithmetic, never floating point.
Round expense/floor requirements upward and available net revenue downward.

1. Calculate each pack's net revenue after sale-tax liability, payment costs, allowed
   discounts, bonus credits and financial-risk reserves. Divide by all credits promised.
2. Select the lowest net revenue per usable credit across every allowed pack/promotion.
   Do not rely on unused credits, expired balances or an average customer buying a
   more expensive pack to make another pack viable.
3. For each operation, bound variable expense plus allocated fixed costs and contingency.
   Required net receipts are `bounded_cost / (1 - target_margin)`.
4. Required credits are `ceil(required_net_receipts / lowest_net_receipts_per_credit)`.
   Search, supported confirmed application and optional tailoring have separate prices.
5. Validate mixed consumption and worst-case use: the same prepaid credit must remain
   solvent whether spent on any allowed operation. Validate Premium's included workload
   separately; never use a time entitlement as unlimited generation permission.

Use a **40% contribution-margin target after allocated operating costs** and a
**20% expense contingency** as initial internal planning choices, subject to actual
measurement. They are not an industry claim or a promise of profit. Low-volume and
high-volume scenarios must both pass the approved expense forecast; a zero/unknown
volume forecast cannot erase fixed costs. A monthly break-even report must show the
paying volume needed to cover fixed expenses and any remaining business loss.

For example, ₹10 of already-buffered cost at a 40% margin requires at least ₹16.67
in net receipts. Customer-facing gross receipts must be higher when payment fees or
sale tax reduce that amount. This is an arithmetic example, not a final service price.

## Implementation and rollout stages

- [x] Add BI08 to the requirements and retain separate search/application/tailoring quotes.
- [ ] Inventory actual GCP/Vercel/model/payment contracts and recent expenses, with secrets excluded.
- [x] Implement a strict versioned expense policy and deterministic price-floor calculator.
- [x] Independently validate the candidate packs, bonus/promotion bounds, model ceilings and operation rates; production authority activation remains pending.
- [x] Persist the applicable cost-policy version with new immutable quotes and payment orders.
- [ ] Test rounding, expensive payment methods, adverse FX, full credit consumption, retries,
  unknown costs, free/error work, low volume, changed policy and stale/invalid configuration.
- [x] Implement clear prospective rates and optional tailoring costs; preserve accepted old terms.
- [ ] Configure bounded provider/document/fleet spending and restrict new work when budgets fail.
- [ ] Reconcile actual invoices and net receipts; deploy only the validated catalog/policy version.
- [ ] Monitor net contribution, fixed-cost coverage, liability and budget variance monthly.

## Estimated launch proposal, pending runtime verification

On 9 October the actual serving Cloud Run revisions were read without exposing their
environment: API and both workers use one vCPU/1 GiB; maximum instances are 1/2/2,
with concurrency 10/4/4. No minimum-instance annotation was observed. Their full five
instances running for all 30 days would cost USD 343.44 in active request-based CPU/
RAM at the published Iowa rates, before other costs and without free-tier discounts.
At the adverse planning conversion below that is ₹34,344. This is a capacity scenario,
not a measured invoice. Tagged and future compiler/authority resources require their
own allocation; maximum instances alone are not a monthly spending cap.

GCP billing is enabled, but no billing-export dataset was visible in the project.
Budget, Cloud SQL and Memorystore inventory APIs report disabled, which does not prove
those expenses are zero or identify external providers. Vercel's connected API refused
the actual team; its separately authenticated CLI verifies the linked project but
reports cost data unavailable for 9 September–9 October. No invoice was substituted
with a zero. Database/cache/provider contracts remain unmeasured.

Use these owner-authorized planning assumptions for the candidate policy:

- ₹13,000/month shared platform/operations plus ₹2,000/month marketing/free/failed work,
  with model paid usage accounted separately per consumed unit. ₹15,000 divided by
  100 paid packs gives ₹150 fixed allocation per pack; this volume is a forecast.
- ₹100/USD adverse conversion, 3.54% gross payment expense, 18% inclusive sale-tax
  reserve and 5% gross refund/chargeback reserve. These are conservative assumptions,
  not actual merchant fees or a determination of HireWiz's tax obligations.
- ₹0.25 per job-service credit: search one credit; confirmed supported application
  twenty credits for a ₹5 pooled expense bound. Manual handoff still has no application
  charge; bounded preparation and unsuccessful work are pooled expenses.
- ₹26.50 expense per AI-analysis unit, including ₹1.50 for up to three worker/document attempts. Proposed
  prospective tailoring uses two units for the existing USD 0.50 total provider cap;
  other writing caps and unit prices must each pass the same bound. Existing accepted
  unit quotes remain intact. No model quality change follows from repricing units.

| Candidate finite pack | INR gross | Job-service credits | AI-analysis units | Estimated margin after allocation/contingency |
| --- | ---: | ---: | ---: | ---: |
| Starter | 649 | 100 | 2 | 44.68% |
| Growth | 1,099 | 300 | 6 | 44.98% |
| Scale | 2,199 | 700 | 15 | 48.26% |

All promised credits and units are assumed fully consumed. These candidate prices
pass the documented integer/rational arithmetic and bounded independent local
order, quote and cash-admission checks. Exact composed production verification remains
pending. No bonus or promotion can reduce net receipt below the floor.
See the [arithmetic evidence](evidence/2026-10-09-estimated-credit-pack-proposal.json).

With only Starter purchases and those assumptions, monthly cash break-even requires
45 packs. At ten packs the model still loses approximately ₹13,990; at fifty it has
approximately ₹2,049 left after the buffered costs; at one hundred approximately
₹22,097. Thus profitable operation pricing does not promise a profitable business at
arbitrary volume. Scaling, paid/employer licensing, provider tax, legacy exposure and
support growth can exceed these estimates: update allocations and prospective prices,
and restrict new cost-bearing work before the available spending allowance is exceeded.

Manual handoff, draft saving and unverified responses still earn no automatic-application
fee. Margin protection must not invent a successful receipt or charge for failed discovery.
Rate limits and the pooled expense allowance cover those costs. A pricing change never
permits resubmitting an uncertain application or silently increasing an accepted quote.

The overhead refinement retains the [first arithmetic snapshot](evidence/2026-10-09-estimated-credit-pack-proposal-initial.json). It now reserves ₹0.50 for every permitted provider attempt, including failed or unknown calls whose analysis units may be restored. The ₹2,000 shared allowance is split into ₹1,000 promotional/failed AI, ₹500 remaining legacy Premium AI and ₹500 legacy-service and unsuccessful-service exposure. Each fresh search first reserves up to ₹0.60 of that pool before reading/scoring postings; empty or failed work retains its cash exposure, while a completed idempotent replay adds none. These are finite planning allocations, not measured bills or retroactive prices. Refunds and old quotes do not erase their expense exposure. Under the refined assumptions, an all-Growth mix requires 33 packs per month to cover buffered fixed expenses; an all-Scale mix requires 19.

The [finite-pack integration evidence](evidence/2026-10-09-finite-pack-root-integration.json) records the exact independent review and preimage-checked source composition. Source clearance is separate from deployment and actual profitability.
