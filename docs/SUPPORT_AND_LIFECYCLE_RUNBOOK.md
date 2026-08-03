# Support and Lifecycle Runbook

## Protected Support Access

Set `ADMIN_EMAILS` to named HireWiz operator accounts. Support endpoints require a normal authenticated account whose email appears in that allowlist. A non-admin receives `404` so the privileged surface is not advertised.

Available endpoints:

- `GET /api/v1/admin/support-snapshot?email=<customer>`: account, entitlement, recent order, run, and usage history.
- `GET /api/v1/admin/analysis-runs/{run_id}`: normalized run and model-call telemetry without prompt or resume payloads.
- `GET /api/v1/admin/payment-orders/{reference}`: order, transaction, refund, and webhook-processing state.
- `POST /api/v1/admin/usage-adjustments`: idempotent unit adjustment requiring a reason and `Idempotency-Key`.

Every adjustment writes both an append-only usage event and an admin audit event with actor, reason, before state, after state, correlation ID, and timestamp. Never change `users.ai_credits`, entitlements, or payment rows manually unless incident recovery has an approved SQL procedure and matching audit record.

## Lifecycle Delivery

Lifecycle messages use `notification_outbox`. Enqueueing happens in the same transaction as the owning product event:

- Account created: welcome and 24-hour onboarding reminder.
- Analysis succeeded: completed-analysis message.
- Email reminder due: reminder message.
- Webhook entitlement granted: payment confirmation.

The private worker maintenance endpoint claims and sends pending rows through Resend. Provider requests include the outbox idempotency key. Failed delivery retries with bounded exponential backoff and becomes terminal after five attempts.

Before enabling:

1. Verify the sender domain with the email provider.
2. Set `LIFECYCLE_EMAILS_ENABLED=true`, `RESEND_API_KEY`, `EMAIL_FROM`, and `FRONTEND_URL` on the worker and API.
3. Invoke the private maintenance endpoint from Cloud Scheduler every 15 minutes.
4. Register a staging account and verify exactly one welcome message.
5. Replay maintenance and verify no duplicate message.
6. Confirm no resume, evidence, job-description, or payment credential appears in provider payloads.

## Retention Maintenance

The same maintenance task applies these default windows:

- Analysis input payload: 30 days after completion.
- Analysis result payload: 90 days after completion.
- Model-call telemetry: 365 days.
- Sent or terminal notification outbox rows: 90 days.

Opportunity job snapshots, evidence, and resume versions are user-owned product records and are not removed by this task. They remain available until the user deletes them or the account.

Monitor the maintenance response counts. A sudden large purge, repeated delivery retry, or growing pending outbox is an incident signal. Disable `LIFECYCLE_EMAILS_ENABLED` to stop new messages without disabling account, analysis, or payment flows.

## Common Cases

- Charged but no access: inspect payment order and webhook events. Do not ask the customer to pay again. Reconcile the webhook before changing entitlement.
- Failed run consumed units: inspect run and usage events. A terminal failure should have `release`; use an audited adjustment only after confirming the ledger is wrong.
- Duplicate message: find the outbox idempotency key and provider request. Do not enqueue a replacement with a different key until the original state is understood.
- Deleted account: active career data is deleted and payment records are unlinked. Do not recreate deleted content from logs or provider payloads.
