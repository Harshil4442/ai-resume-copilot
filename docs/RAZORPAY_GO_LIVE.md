# Razorpay go-live checklist

The launch product is the unified credit pass in the [current requirements](requirements/CREDIT_COST_QUALITY_V1_1.md) and [release runbook](delivery/UNIFIED_CREDIT_ROLLOUT.md). Keep production checkout closed until the exact product, serving release, financial evidence and commercial hosting are approved. Completing this checklist does not constitute provider approval or activate payments.

## 1. Verify the business and purchase terms

- [ ] Use the existing merchant account and choose the true KYC form. Verify that the legal operator, PAN identity, settlement account holder and website operator wording agree. Confirm a legitimate correspondence address, working contact channels and support hours.
- [ ] Confirm Indian tax and invoicing treatment with a qualified adviser. Verify the monitored support inbox and its domain authentication. Review the signed-out home, pricing, contact, legal, privacy, digital-delivery and refund pages against the approved terms.
- [ ] Obtain written Razorpay approval for the actual self-service software and credit-pass model. Verify the merchant category, enabled domestic INR payment methods, statement descriptor, settlement account and website branding. Resolve any existing activation restriction in the original account.
- [ ] Present these proposed introductory packs for review. Each purchase is a one-time 30-day pass for a finite number of HireWiz service credits, without automatic renewal. Credits cannot be transferred, resold or withdrawn as cash. Show the exact payable amount, validity, supported services and refund terms before accepting payment.

| Pass | Introductory price | Credits |
| --- | ---: | ---: |
| Starter (`starter_pass`) | ₹59 | 40 |
| Growth (`growth_pass`) | ₹179 | 130 |
| Scale (`scale_pass`) | ₹449 | 360 |

- [ ] Approve actual start and end dates for the introductory campaign separately from each purchased pass's validity. Use an introductory-price label until same-pack reference-price evidence is approved. The catalog and immutable quote control the payable price, services and expiry terms.
- [ ] Preserve the original Premium, job-service, analysis-unit and dual-balance orders and entitlements. Historical ₹999 Premium and ₹499 service SKUs remain available for original-order fulfillment. New legacy bundle sales are disabled while `UNIFIED_CREDITS_ENABLED=true`. Leave the existing balances on their recorded terms.

## 2. Verify the checkout and webhook configuration

The 13 November serving revision has live checkout disabled. Its Razorpay
credential settings are present as environment values. Before activation,
move those bindings into managed secrets. Preserve the key ID used by
outstanding orders and the signing configuration used by their webhooks.
If rotation is needed, reconcile the original orders first and verify the
callback overlap before retiring any earlier credential.

- [ ] Keep Test and Live keys and webhooks separate. Use `APP_ENV=staging` or `test` with `RAZORPAY_MODE=test` and a `rzp_test_` key in an isolated environment. Production requires `RAZORPAY_MODE=live` and a `rzp_live_` key. Bind the key ID, key secret and webhook secret to that environment through managed configuration.
- [ ] Configure the approved capture behavior and the direct backend HTTPS endpoint `POST /api/billing/webhooks/razorpay`. Set a separate webhook secret of at least 32 characters. During a reviewed rotation, retain `RAZORPAY_WEBHOOK_SECRET_PREVIOUS` for outstanding callbacks and remove it after the retry window.
- [ ] Subscribe to `payment.captured`, `order.paid`, `payment.failed` and `refund.processed`. Verify the signature on the raw request body and the provider event identifier. Confirm that duplicate deliveries and both capture event types for the same payment settle at most once. Unmatched events require reconciliation. See [Razorpay webhook validation and testing](https://razorpay.com/docs/webhooks/validate-test/).
- [ ] Keep `RAZORPAY_CHECKOUT_ENABLED` false until provider approval, the go-live review and the rollout are ready. The provider gate also requires `RAZORPAY_ACCOUNT_APPROVED`, `PAYMENTS_GO_LIVE_REVIEW_COMPLETE`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` and `RAZORPAY_WEBHOOK_SECRET`.
- [ ] Check the unified release review in `UNIFIED_CREDIT_RELEASE_REVIEW_JSON` and the execution review in `UNIFIED_CREDIT_RUNTIME_REVIEW_JSON`. Checkout additionally depends on `UNIFIED_CREDITS_ENABLED`, `UNIFIED_CREDIT_EXECUTION_ENABLED`, an approved `UNIFIED_CREDIT_EXECUTION_AUDIENCE`, the exact current policy, qualified services and funded payment exposure. The `canary` audience requires its stored review ID and SHA-256 through `UNIFIED_CREDIT_CANARY_REVIEW_ID` and `UNIFIED_CREDIT_CANARY_REVIEW_SHA256`. The `reviewed_public` audience requires the reviewed widening decision.
- [ ] Retain current evidence for delivery quality, legacy rights, commercial hosting, invoices, security, shadow accounting and eligible routes. Reconcile at least seven representative days of shadow accounting and at least 200 successful paid-equivalent operations before widening. Verify the adverse margin test and a funded operating month. Use the [release runbook](delivery/UNIFIED_CREDIT_ROLLOUT.md) for the complete canary and rollout sequence.

## 3. Verify Test-mode purchase and recovery

Use a disposable HireWiz stack with synthetic accounts and a separate database.
The local browser workflow uses simulated provider responses. Keep its results
as application evidence. For provider evidence, use Razorpay Test keys, a
separate Test webhook secret and an isolated HTTPS callback that forwards only
`POST /api/billing/webhooks/razorpay`. Razorpay can deliver Test events over a
public HTTPS tunnel to a local receiver. Keep the rest of the application and
the database private. Confirm that the Test integration has no connection to
the live merchant keys, customer data, production webhook or queue. [Test and
Live modes](https://razorpay.com/docs/payments/dashboard/test-live-modes/)
· [Webhook testing](https://razorpay.com/docs/webhooks/validate-test/)

Use a separate checkout without `.env` files and install the locked frontend and
backend dependencies. Start a disposable UTF-8 PostgreSQL database named
`hirewiz_admission_test` on loopback port `55433` or `55434`. The launcher uses
the same disposable PostgreSQL contract as the local browser workflow. Run it
from the repository root with the backend Python environment.

```sh
backend/.venv/bin/python -B -m backend.scripts.run_razorpay_test_checkout --allow-disposable-postgres
```

The default check starts the production Next.js build and FastAPI, registers a
disposable account, verifies the closed checkout and empty wallet, and checks
the callback receiver. It has no provider credentials and sends no payment
requests. When the Test account and webhook secret are authorized, opt in from
an interactive terminal.

```sh
backend/.venv/bin/python -B -m backend.scripts.run_razorpay_test_checkout \
  --allow-disposable-postgres --razorpay-test --minutes 30
```

Enter the Test key ID, key secret and separate webhook secret at the hidden
prompts. The disposable database has a synthetic active campaign so the lab
uses the proposed introductory totals without displaying a crossed-out price.
Its dates apply only to this test. Open the printed local URL in an isolated
browser profile, trust only the launcher's loopback certificate for this
session, and register a synthetic account. Use Razorpay's test payment methods.
Point the HTTPS tunnel only at the printed loopback
[receiver](../backend/scripts/razorpay_test_webhook_ingress.py) port and configure
the exact printed callback path in the Test dashboard. The
receiver relays the raw body and the Razorpay signature and event ID. It returns
only the HTTP status and keeps no delivery records. Verify that `GET /api/health`
and unrelated POST paths are refused at the public endpoint. The launcher
stops its local processes and drops only its own schema on exit. Capture
sanitized evidence while the launcher is running.

For one Test order, observe delivery of `order.paid` and `payment.captured` and
verify they settle one credit lot. To verify a genuine Razorpay retry, atomically
write `{"ack_loss_order":"order_<exact-Test-order-ID>"}` to the printed private
`hold.json` with mode `0600` before paying. Use this rule by itself. For the first
matching signed `order.paid` with an event ID, the receiver returns 503 only after
the local billing handler confirms that the event committed. The private
`ack-loss-<order-digest>.used` marker records only the SHA-256 digest of that
event ID and keeps the response one-shot across receiver restarts. Keep the rule
armed until the same signed event gets a successful `duplicate` response and
the companion `.replayed` marker contains the same digest. Confirm the first
failed delivery in Razorpay and verify one recorded event and one credit lot in
the isolated ledger. If either delivery observation is unavailable, keep the
retry verification pending. A legacy empty `.used` marker prevents another
deliberate failure but cannot verify which event retried. Remove the rule
before testing partial and remaining refunds, when supported.

If a previous lab runner is already active, a second receiver can target that
runner's verified loopback API port directly. Use a new loopback receiver port,
its own private control directory and the same dedicated Test webhook secret.
Point the Test tunnel at that receiver only. The previous receiver discards the
application response needed to distinguish a commit from a duplicate, so avoid
chaining the receivers.

For the reconciliation order, use the private `hold.json` path printed by the
launcher. Create the file with permissions `0600` using an atomic rename. Before
completing Test Checkout, set `order` to that exact Razorpay Test order ID. If
testing refund reconciliation, add `payment` with that exact Test payment ID
before issuing the Test refund. A refund can contain a payment ID without an
order ID. Use only the IDs from the dedicated test account and remove the hold
as soon as reconciliation finishes. The receiver returns 503 for matching
signed deliveries without storing their bodies. Razorpay can redeliver them
later, and they must leave the isolated settlement intact. If the payment
captured before the hold or redelivery is not observed, record the relevant
case as pending. [Razorpay delivery retries](https://razorpay.com/docs/webhooks/best-practices/)

Record only sanitized event status, amount, currency and ledger assertions.
Remove the Test webhook and stop the temporary receiver when the test is
complete.

- [ ] Confirm the public catalog at `GET /api/public/credits/catalog` and the eligible signed-in catalog at `GET /api/v1/credits/catalog` reflect their audience and the checkout state. Both return 404 while the unified rollout is off. The legacy `GET /api/public/billing/catalog` should show checkout closed. During a canary, the signed-out unified catalog must keep checkout closed. Create the owned quote through `POST /api/v1/credits/purchase-quotes` with its SKU and idempotency key. Create the order through `POST /api/v1/credits/orders` with its quote ID, `billing_country=IN` and idempotency key. The server fixes the INR total and eligible services. Verify a valid 15-minute quote across the campaign cutoff and refusal of an expired, foreign or changed quote.
- [ ] Use `GET /api/v1/credits/recent-order` and `GET /api/v1/credits/orders/{reference}` to recover the owned original. Replaying an interrupted order creation must keep the same attempt. Test an ambiguous or malformed provider response and confirm it blocks a second payable order until the first is reconciled.
- [ ] A successful browser return to `POST /api/billing/orders/{order_reference}/checkout-result` records confirmation. Credits are granted only after verified capture through the shared webhook or an authoritative provider read through `POST /api/billing/orders/{order_reference}/reconcile`. Verify exactly one credit lot, the quoted credits, actual expiry and matching order and wallet entries. The actual expiry must be at least the quoted expiry and at least 30 days after both verified capture and fulfillment. Retry capture and reconciliation to verify idempotency.
- [ ] Verify that invalid signatures, missing event identifiers, incorrect amounts or currency, foreign orders and failed payments cannot grant credits. Confirm another account cannot see the order or wallet. Check that diagnostic output omits credentials, raw webhook payloads and payment instrument data.
- [ ] Verify partial and full `refund.processed` events against the original credit lot. Confirm proportional revocation, debt accounting for spent refunded credits and no effect on unrelated lots. Replaying a processed refund or receiving a late capture must preserve the reconciled state. Verify original legacy callbacks and original-order recovery separately.

## 4. Activate and verify the authorized rollout

- [ ] Deploy the matched frontend, API and workers with checkout closed. Verify approved public terms, signed-in routes, retained wallet balances, old orders and callback reachability. Run the isolated Test-mode checklist before presenting the activation decision.
- [ ] Obtain separate paid-activation approval and verify the merchant approval, commercial hosting, funding, canary and current release evidence. Enable only the approved audience and services.
- [ ] With authorization for the transaction, reconcile one controlled purchase and the approved refund against Razorpay, the webhook, the owned order, the credit lot, the receipt and the settlement ledger. Monitor payment failures, unmatched events, replay, disputes and refund errors during rollout.
- [ ] To pause sales, turn off `RAZORPAY_CHECKOUT_ENABLED` before changing unified rollout flags. Confirm new orders are closed on both catalogs. Keep the signed webhook, original quote, status and reconciliation paths available for accepted payments. Reconcile outstanding orders before rotating the merchant API key, since browser confirmation and provider reconciliation bind to the original mode and key ID. Preserve funded delivery and refund obligations through rollback.
