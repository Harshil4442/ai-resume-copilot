# ADR 002 Paid job service credits

Status: Accepted for implementation on 8 October 2026. Commercial terms updated by
[the finite-pack decision](../delivery/FINITE_PACK_IMPLEMENTATION_2026-10-09.md)
on 9 October 2026; the accounting decision below remains accepted.

Job search and automatic applications use a dedicated prepaid service balance. Premium
access and free signup analysis units do not waive these charges. The customer chooses
the number of results and sees a separate application quote for each selected job.

Current reviewed source terms are one service credit per newly delivered qualifying
posting and 20 per verified complete automatic application. New purchases offer three
finite bundles: Starter INR 649 (100 service credits + 2 analysis units), Growth INR
1,099 (300 + 6), and Scale INR 2,199 (700 + 15). See the
[finite-pack decision](../delivery/FINITE_PACK_IMPLEMENTATION_2026-10-09.md) and
[BI01–BI08 requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md#paid-search-and-auto-apply)
for expense assumptions, separate quotes and rollout evidence. Source implementation
does not prove production activation or measured profitability.

The original pilot's five-credit application price and 500-credit INR 499 pack are
historical terms, not current offers. Already accepted orders, entitlements and
reservations retain their exact purchased terms and captured unit prices. Change
future prices only with a newly reviewed pricing version; do not reprice history.

Reserve under the owner row lock, append a ledger event and create work within one SQL
transaction. Release unused search reservations. A paid delivery uniqueness constraint
prevents charging again for the same opening. Application settlement requires a complete
provider receipt; a draft upload or manual handoff is not a charged auto-application.
Unknown possible-sent outcomes retain an explicit temporary hold and require reconciliation.

Captured payment alone grants credits using the order snapshot. Refunds revoke the
proportional grant and preserve debt if refunded credits were spent. This avoids creating
unpaid credits through spending and refunding a pack. Financial records remain subject to
the existing retention policy; service credits are not transferable or redeemable money.

Optional tailoring remains separately quoted in the existing analysis-unit system.
Customers can apply with original or custom files without buying tailoring. This preserves
clear pricing while keeping legacy Premium analysis behavior compatible.

Trade-off: two balances require more explanatory UI and reconciliation. They prevent
unbounded employer execution through an existing unlimited analysis entitlement and make
costs and refunds auditable. PostgreSQL is the authority; Redis and browser state are not.

On 23 October 2026 the owner authorized a separate noncommercial testing exception:
`hirewiz-owner-prelaunch-search-20261023-v1` may grant exactly three unpaid service
credits to the reviewed owner, once globally. This does not relax captured-payment
requirements for paid entitlements. A reviewed one-shot operator command must bind the
existing authenticated operator, exact native database/schema, existing runtime write
permissions, opportunity and owned source resume. The owner lock, campaign transaction
lock and deterministic ledger identity cap the campaign even after account unlink.
The grant and actual-actor audit commit together; exact request replay is idempotent,
while changed terms conflict. Do not create a payment, receipt, invoice, income entry,
AI-unit grant or new admin permission. Existing nonpayment operating-pool guards fund
the search expense at ordinary admission; the credits are never classified as prepaid.
Checkout, sales, employer writes, prices and expense limits remain unchanged. This
records source support only; an actual grant and completed search require separate evidence.
