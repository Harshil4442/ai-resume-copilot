# ADR 002 Paid job service credits

Status: Accepted for implementation on 8 October 2026

Job search and automatic applications use a dedicated prepaid service balance. Premium
access and free signup analysis units do not waive these charges. The customer chooses
the number of results and sees a separate application quote for each selected job.

Initial configuration is one service credit per newly delivered qualifying posting and
five per verified complete automatic application. The checkout catalog contains a
500-credit INR 499 pack. These are introductory prices, not a claim of measured margins.
Change future prices using a new pricing version after observing ingestion, execution,
provider and support costs. Existing reservations retain their captured unit prices.

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
