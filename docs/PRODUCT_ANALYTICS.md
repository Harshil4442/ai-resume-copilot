# Product Analytics Contract

HireWiz uses consent-gated PostHog events for product decisions and Google Analytics for aggregate acquisition reporting. Sentry is operational error reporting, not product analytics. No resume text, evidence text, job description, generated answer, password, payment credential, or provider secret may appear in an analytics property.

## Identity Rules

- Anonymous public activity stays anonymous.
- Identify a user only after authentication and only when analytics consent is active.
- Use the internal numeric user ID as the analytics distinct ID. Do not use email as the distinct ID.
- Reset analytics identity on logout and account deletion.
- Store coarse acquisition, device, route, experiment, and funnel attributes only.

## Core Funnel

```text
public_page_view
  -> account_created
  -> registration_completed
  -> resume_upload_completed
  -> opportunity_created
  -> first_useful_match
  -> evidence_approved
  -> resume_version_created
  -> opportunity_stage_changed(applied)
  -> checkout_started
  -> payment_client_confirmed
  -> entitlement_fulfilled
```

Payment fulfilment is measured from backend webhook state. A browser success callback is never counted as revenue or access delivery.

## Event Catalog

| Event | Trigger | Allowed properties |
| --- | --- | --- |
| `account_created` | Backend registration succeeds | `method` |
| `registration_completed` | New account obtains a session | `method` |
| `login_succeeded` / `login_failed` | Credential result | `method` |
| `resume_upload_selected` | Valid local file selected | `file_type`, `size_bytes` |
| `resume_upload_started` | Upload request begins | `file_type`, `size_bytes` |
| `resume_upload_completed` | Resume parsing succeeds | `resume_id`, `skill_count` |
| `resume_upload_failed` | Upload or parsing fails | `file_type`, normalized error category when added |
| `opportunity_created` | Workspace record created | `source`, `has_resume` |
| `analysis_run_created` | Durable run accepted | `operation`, `estimated_units` |
| `analysis_completed` / `analysis_failed` | Run reaches a terminal state | `run_id`, `operation`, `status`, `committed_units` |
| `first_useful_match` | First successful role match in the current flow | `opportunity_id` |
| `evidence_approved` / `evidence_rejected` | User reviews evidence | `opportunity_id` |
| `evidence_edited` | User changes imported evidence | `opportunity_id` |
| `resume_version_created` | Version persists | `opportunity_id` |
| `resume_version_reviewed` | Version approved or rejected | `approval_state` |
| `opportunity_stage_changed` | Stage event persists | `stage` |
| `opportunity_outcome_recorded` | Final outcome persists | `outcome` |
| `opportunity_exported` | Private export downloads | `opportunity_id` |
| `market_analysis_started` | Market request begins | `country_code`, `has_resume` |
| `market_analysis_completed` | Market request succeeds | `country_code`, `sample_size`, `source_provider` |
| `profile_saved` | Profile update succeeds | `completeness` |
| `checkout_product_selected` | User selects the Premium offer | `sku`, `amount_minor`, `currency` |
| `checkout_started` | Provider order is created | `sku`, `amount_minor`, `currency`, `provider` |
| `checkout_dismissed` / `checkout_failed` | Hosted checkout closes or fails | `provider`, normalized failure category |
| `payment_client_confirmed` | Signed browser callback is accepted | `provider`, `sku`; not a revenue event |
| `entitlement_fulfilled` | Signed webhook grants access | Emit server-side when a server analytics sink is configured |
| `upgrade_prompt_viewed` / `upgrade_prompt_clicked` | Contextual upgrade prompt | `surface`, `reason`, `sku` |

## Required Reports

- Activation funnel by acquisition source and device.
- Median time from account creation to first useful match.
- Resume upload and opportunity creation failure rates.
- Analysis success, latency, committed units, and estimated model cost by operation and prompt version.
- Evidence approval to tailored-version conversion.
- Saved to applied, interviewing, offer, and accepted conversion.
- Free activated cohort to paid conversion.
- Checkout started to webhook-fulfilled conversion and payment failure rate.
- Day 1, day 7, day 30, and day 60 Career Workspace retention.

## Feature Rollout

Server feature decisions are deterministic and returned from `/api/v1/features`. Every rollout must record the feature key, eligible audience, primary metric, guardrails, and rollback trigger. The standard progression is internal, invited users, 5 percent, 25 percent, and 100 percent. Environment controls are documented in `ENVIRONMENT_AND_DEPLOYMENT.md`.

## Data Quality

- Event names use `snake_case` and describe completed facts, not button labels.
- Emit success events only after the owning API mutation succeeds.
- De-duplicate terminal run events by run ID in the client and by idempotency key on the server.
- Treat PostHog events as decision-support data, never the accounting source of truth.
- Reconcile payment metrics against `payment_orders`, `payment_events`, and `entitlement_ledger`.
