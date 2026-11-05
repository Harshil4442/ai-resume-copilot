# F07: consent and digest admission, inert first increment

Status: local contract development only. F07 is not runtime-complete. No route,
worker, migration, native permission, provider sender or feature flag imports this
contract. Every draft result has `runtime_authority = UNAVAILABLE`, including a
logically consistent candidate. Caller-supplied facts are not native receipts.
The original eight stages and 83 requirement IDs/statuses remain unchanged.

## First code change

`backend/app/domains/notification_consent/contracts.py` models explicit consent
CAS successors and consistency checks for one weekly digest. It has only standard
library dependencies, no IO and no positive authorization output. Focused synthetic
tests exercise restored SQL consent/credentials, opt-out/re-consent, owner/account
substitution, old recipients, closed subjects, stale queues, cadence and unknown
sends, optional-category capacity and preserved security headroom. These tests do not qualify native consistency, mail delivery or load.

The cadence key is stable for one native subject and ISO delivery week. Preference,
credential, recipient, content or consent-generation changes cannot create another
weekly slot. A native send-BEGIN consumes the slot even when provider outcome is
unknown; SQL attempt-counter restoration must not reopen it. A digest is enqueued
near its due time, with at most the existing 24-hour lifecycle delivery window,
rather than reserving a provider envelope a week in advance.

## Minimal runtime integration, still pending

1. Add a dedicated `notification_preferences` SQL projection row: user PK/FK,
   subject/account binding, native consent generation, recipient hash, explicit
   weekly-digest flag, policy version and next due time. Index due discovery. Native
   consent remains authoritative; no default opt-in or adoption of a restored row.
   Using audit/outbox rows as preferences avoids DDL but mixes retention, accounting
   and discovery semantics and does not solve rollback. The dedicated row is preferred.
2. Add purpose-bound native consent head/history plus closed operation kinds
   `notification_preferences_change` and `notification_digest_send_begin`. These
   names are proposed, not existing permissions. Include their provenance in native
   census/closure validation; do not add untracked authority namespaces or reuse
   browser pairing/signing commands. Native acknowledgement precedes SQL projection.
   Missing/ambiguous native outcomes remain unavailable and are not reconstructed.
3. Authenticated preference writes must reuse
   `ConfiguredCandidateAccounts._invoke` and fresh `current_session` checks. A
   worker send uses fresh `current_subject`, current SQL/native credential revision
   equality, mailbox binding, consent head/history and exact queued generation.
   Reuse the existing publication/configuration/witness closure fences through
   `ScopedCandidateAuthority._run`; no positive authority cache or stored login
   session may stand in for these checks. Credential rotation may preserve email
   consent only when both current native and SQL revisions agree. Also retain a
   signed, purpose-isolated, disable-only unsubscribe POST as pending: GET only
   displays confirmation; the signed proof is submitted in the POST body and can
   only advance consent to disabled, never enable mail or sign in. No credentials,
   recipient address or proof belongs in the request URL. The reviewed email-to-
   confirmation proof handoff remains pending; this increment implements no link.
4. Extend `run_maintenance` with an isolated digest selector/delivery path. Follow
   `notifications/privacy_erasure.py`'s dedicated type/status pattern; the generic
   lifecycle loop otherwise claims unfamiliar pending types. Outbox selection is
   scheduling only. Recompute current bounded digest content at delivery, bind its
   hash and recipient to one original native send-BEGIN, then invoke
   `mail_budget.guarded_send`. Every actual attempt needs fresh current consent and
   account checks. Known-retryable recovery must retain the same original message,
   provider key and admission; it must not allocate another cadence slot. Unknown
   outcomes require reconciliation and no automatic resend.
5. Add the SQL row to `erase_account_sql` and `erased_owner_rows_absent`. Native
   subject/account tombstones must continue to defeat restored SQL owners and
   outboxes. Store only recipient hashes in consent history; resolve and compare
   the current recipient immediately before sending. There is no verified mailbox
   change flow in the inspected source: any SQL/native mailbox mismatch must stop
   delivery, not fall back to an older queued address. A future verified address
   change needs an explicit native consent/recipient revision.
6. Add migration/catalog/role and source-contract successors before integrating
   runtime callers. The current schema26 contract does not cover a new table or
   new native effects. An application source named Source27 is not itself database
   migration authority. Preserve existing denied/unknown failure behavior.

## Required acceptance boundaries

Opt-out linearizes at native consent-change acknowledgement: every subsequently
admitted send must fail for the old generation. An already admitted provider request
can remain in flight and cannot be recalled. Session/device logout is distinct from
email consent; native account closure and explicit email revocation deny future sends.

Reuse the existing detached mail ledger and provider idempotency. Its current
three-unit envelope and 100 daily / 1,000 monthly units allow at most 33 daily /
333 monthly messages before security and other traffic. Running security first
is not a reserved security budget. Digest activation therefore also needs an
explicit optional-mail ceiling and reserved security headroom in admission, plus
bounded fair scheduling. No new spend, throughput promise or runtime budget
allocation is implemented by this increment. `MailCapacityFacts` makes missing
headroom, exhausted optional allowance and insufficient three-unit envelopes
fail the pure check. Its values remain untrusted observations. Runtime must
calculate the optional ceiling/security headroom and reserve the envelope under
the existing mail-budget transaction lock; a precomputed boolean cannot replace
that atomic admission.

Runtime acceptance still requires native CAS/closure/restore tests, SQL migration
and erasure checks, outbox/revocation races, reserved-budget tests, original-send
unknown-outcome recovery and reviewed real delivery. No claim of F07 completion,
provider authority or successful deployment follows from the pure contract.
