# Razorpay Go-Live Checklist

Checkout must remain disabled until the account is activated for the exact
HireWiz business model. Completing this checklist improves review readiness but
does not guarantee approval by Razorpay or its banking partners.

## 1. Identity and website facts

- [ ] Select the true KYC form: unregistered individual or sole proprietor.
- [ ] Legal operator name, PAN identity, bank-account holder and website
      operator wording match.
- [ ] Publish a complete legitimate correspondence address matching acceptable
      KYC evidence.
- [ ] Confirm the published phone number works and the stated support hours are
      realistic.
- [ ] Confirm GST status and invoice/tax treatment with an Indian CA; do not
      publish a GSTIN or non-registration claim without evidence.
- [ ] Verify `work@hirewizhq.com` receives mail and configure SPF, DKIM and
      DMARC for `hirewizhq.com`.
- [ ] Check all public pages in a signed-out/incognito session: `/`, `/pricing`,
      `/about`, `/digital-delivery`, `/terms`, `/privacy`, `/refund`, `/cookies`,
      `/contact`, and `/subprocessors`.
- [ ] Confirm the deployed site—not only the repository—contains no unsupported
      testimonials, outcome guarantees, recruitment/placement claims, virtual
      currency framing, legacy brand, or inactive provider claims.

## 2. Razorpay manual review

- [ ] Use the existing merchant account; do not create a duplicate account to
      bypass a prior decision.
- [ ] Request written confirmation of the exact accepted category and
      sub-category for self-service AI-assisted resume-analysis SaaS.
- [ ] Ask whether `it_and_software / saas` is the correct classification.
- [ ] Ask Razorpay to unlock or correct the original activation form.
- [ ] Supply public policy/pricing URLs and a sanitised reviewer account or
      product walkthrough.
- [ ] State clearly that the initial product is a ₹999 one-time 30-day software
      pass with no automatic renewal, wallet, stored value, transfer, resale, or
      cash withdrawal.
- [ ] Obtain separate written approval before enabling standalone analysis-unit
      packs or automatic recurring payments.

## 3. Dashboard configuration

- [ ] Generate separate Test and Live API keys. Use Live keys only after
      activation.
- [ ] Configure automatic capture for the intended payment flow.
- [ ] Keep the initial lane domestic: enable only the approved INR payment
      methods and keep international payments disabled until tax/FEMA review.
- [ ] Create the webhook URL:
      `https://<BACKEND_HOST>/api/billing/webhooks/razorpay`.
- [ ] Set a strong, unique webhook secret. It is different from the API key
      secret.
- [ ] Subscribe at minimum to `payment.captured`, `payment.failed`, and
      `refund.processed` events supported by the implementation.
- [ ] Set `work@hirewizhq.com` or another monitored mailbox as the webhook alert
      address.
- [ ] Configure the approved `https://www.hirewizhq.com` website and checkout
      branding. Do not display an approval badge unless Razorpay authorises it.
- [ ] Ensure settlement bank details and customer-facing statement descriptor
      match the approved legal/trade identity.

## 4. Backend configuration

Store these in the production platform's secret/environment configuration. Use
the exact names implemented by the backend and verify them against the current
deployment documentation before enabling checkout:

```text
RAZORPAY_MODE=live
RAZORPAY_KEY_ID=rzp_live_...
RAZORPAY_KEY_SECRET=...
RAZORPAY_WEBHOOK_SECRET=...
RAZORPAY_WEBHOOK_SECRET_PREVIOUS=...  # only during a controlled rotation
RAZORPAY_ACCOUNT_APPROVED=true
PAYMENTS_GO_LIVE_REVIEW_COMPLETE=true
RAZORPAY_CHECKOUT_ENABLED=true
```

Set `PAYMENTS_GO_LIVE_REVIEW_COMPLETE=true` only after every applicable item
in sections 1–5 is evidenced and signed off. It prevents provider approval
alone from bypassing unresolved identity, tax, support, or staging work.

During webhook-secret rotation, place the old secret in
`RAZORPAY_WEBHOOK_SECRET_PREVIOUS` until Razorpay's retry window has elapsed,
then remove it. Never reuse either secret elsewhere.

Also verify `APP_ENV=production`, production `DATABASE_URL`, a strong stable
`JWT_SECRET`, the matching Google client ID if Google login is enabled,
restricted `FRONTEND_ORIGINS`, and the corresponding NextAuth/Vercel secrets.
Never use a default authentication secret in production.

## 5. Test-mode verification

- [ ] Checkout is unavailable when any required secret or enable flag is absent.
- [ ] The frontend submits only the SKU; changing browser amount/currency has no
      effect.
- [ ] A successful browser return does not grant Premium before the webhook.
- [ ] A correctly signed captured-payment webhook grants exactly 30 days once.
- [ ] Replaying the same event id does not extend access again.
- [ ] A bad signature, wrong amount, wrong currency, wrong order id, missing
      event id, or payment for another user grants nothing.
- [ ] Failed payments grant nothing.
- [ ] A full refund revokes only the refunded order's entitlement.
- [ ] A customer can see `pending`, `paid`, `failed`, or `refunded` status only
      for their own order.
- [ ] Logs contain correlation/order ids but no API secrets, raw resumes, card
      data, UPI PINs, or full webhook bodies.

## 6. Controlled live launch

- [ ] Deploy with checkout disabled and verify public pages first.
- [ ] Enable checkout only after written activation and Live-key verification.
- [ ] Make one low-risk real purchase using the published SKU and verify the
      Razorpay dashboard, webhook event, local order, entitlement, receipt and
      settlement record.
- [ ] Test the documented refund process.
- [ ] Monitor webhook failures, unmatched payments, duplicate events, disputes,
      refund failures and settlement reconciliation daily during the initial
      launch.
- [ ] Keep a rollback switch: setting `RAZORPAY_CHECKOUT_ENABLED=false` must
      immediately stop new checkouts without damaging existing records.
