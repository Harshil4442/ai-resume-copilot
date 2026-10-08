# Employer execution admission foundation

Implemented in the isolated `employer-safety-foundation` worktree and integrated into the
release worktree on 8 October 2026. Exact release `7cc49c2` is now promoted on GCP and
Vercel with actual production schema `20261008_0009`; see [the release record](PRODUCTION_ADMISSION_RELEASE_2026-10-08.md).
This stage builds
backend controls for BI06, FR07 and parts of NF01/G05D–E; it does not close the full
acceptance gates or authorize employer submissions. Automatic submission still defaults
to disabled. The four-source discovery catalog is unchanged.

## Behavior delivered

An exact, approved application reserves both a durable safety slot and prepaid credits
when accepted into the execution queue. The application status and dispatch outbox event
are committed in that same transaction. PostgreSQL locks the candidate row to serialize
competing quota decisions across processes. Creation and account deletion first acquire
a transaction-scoped PostgreSQL advisory gate for that candidate, preventing a new
application from appearing after deletion has scanned its application set. Executors and
batches retain application-row-before-candidate ordering; batches and deletion scan rows
in sorted order before locking the candidate and subordinate records. The lifecycle gate
adds no network work and releases with the transaction.

The default candidate limits are 10 attempts per UTC day, 50 over 30 rolling days,
10 pending attempts, a daily attempt-credit budget of 100 and a rolling budget of 500.
The default batch is at most 10 applications and 100 credits, with a 24-hour quote.
Unresolved work does not disappear from these limits at midnight: every reserved,
submitting or unknown attempt counts regardless of age. Completed possible sends count
within the appropriate time window. These are service safety ceilings, separate from
the actual prepaid balance. A refunded provider rejection still consumes an attempt
because a request may have reached the employer.

Employer caps default to five daily attempts and ten over 30 days. A reviewed source may
carry an explicit positive policy with its version and HTTPS evidence. Sources can share
an **operator-verified** employer key; their policies all apply to that shared employer's
usage. Figma's reviewed public five-applications-per-30-days restriction is additionally
enforced for its Greenhouse source. A supplied policy may tighten that known restriction
but cannot silently loosen it. Its hiring-rejection/reapplication policy and applications
made outside HireWiz are not reconciled by this stage.

Every application quote freezes candidate policy, employer policy/evidence, exact opening
and employer keys, unit price, pricing version, quote time and expiry. The reviewed package
digest includes those snapshots and the current employer policy/opening identity. Execution
and final launch both check current restrictions as well as the saved quote, so a stricter
configuration, revoked source, changed requisition or expired approval cannot inherit an
older permission. Price configuration changes do not silently increase a reviewed quote.
Limit errors include a stable reason code and usage/limit. A midnight retry time is omitted
when pending or uncertain work would still prevent admission then.

Credit and analysis-unit changes refresh the current balances and Premium entitlement
after acquiring the candidate row lock. A request's previously cached authenticated user
must not supply an old balance. The shared helper flushes only that owner's prior changes
under the lock before refreshing, preserving successive batch debits when ORM autoflush is
disabled. This handles both competing reservations and refunds. Application reservations
retain the exact pricing version from the approved snapshot, even after a code release
changes the current pricing version. SQLAlchemy documents why a subsequent row query alone
does not overwrite a cached identity and why refreshing must preserve pending changes:
[Populate Existing](https://docs.sqlalchemy.org/en/20/orm/queryguide/api.html#populate-existing).

Canonical opening identity uses a verified employer/tenant identity and exact requisition
ID where available; otherwise it conservatively uses the provider-scoped posting ID.
There is no fuzzy title/location merge. Discovery rankings and charge-once delivery records
deduplicate exact aliases. Preparation and durable admissions prevent a second active
possible send, including across verified grouped tenants. Confirmed and unknown opening
claims remain active. A changed requisition cannot bypass an existing claim for the same
stored posting. Future regrouping or identity remapping must preserve historical claims
through an explicit operator migration; approximate cross-provider matches are not solved.

## State and cancellation rules

| Event | Admission result | Credit result |
| --- | --- | --- |
| Exact approved queue acceptance | `reserved`; unique active opening claim | Reserve the fixed reviewed price |
| Cancel, account deletion or failed checks before launch | `released`; clear claim | Return unused reserved credits |
| Final launch transaction | `submitting`; record possible-send time | Continue holding credits |
| Unverified response, timeout or redelivered in-flight worker | `unknown`; retain claim and all-age usage | Keep hold; never automatically repeat the POST |
| Independently verified complete receipt | `consumed`; retain claim | Commit once |
| HTTP provider rejection after possible send | `consumed`; clear claim for a newly reviewed intent | Refund, while retaining safety-budget consumption |
| Admin reconciliation proving no submission | `released`; clear claim | Refund with an audit event |
| Account deletion after possible send | Retain anonymous usage/possible-send record | Retain anonymous unresolved financial reservation |

Cancellation during a possible send requests a stop without claiming that already disclosed
data can be recalled. A valid late receipt may still confirm and settle that attempt once.
No form fetch, document read, rendering or external POST occurs under the candidate or
application SQL locks. The possible-send transition commits before the only POST.

## Exact batch contract

New additive routes under `/api/v1/employer-jobs` are:

- `POST /application-batches`: enumerate application IDs, each package digest and allowed
  actions, an idempotency key and an explicit `max_total_credits`.
- `GET /application-batches/{id}`: owner-scoped immutable quote and actual per-job statuses.
- `POST /application-batches/{id}/approve`: explicitly approve the enumerated digest.
- `POST /application-batches/{id}/execute`: atomically accept all items or roll back every
  slot, credit reservation and outbox event.
- `POST /application-batches/{id}/cancel`: release unstarted bound items; retain uncertain
  sends, and report confirmed items without implying that they were recalled.

Each frozen item records its package/actions, price, employer/opening identity and policy
snapshots. Approval also creates each job's normal immutable approval record. A changed
item, another batch binding, expired quote, tighter current batch ceiling or insufficient
balance prevents acceptance. Queued batch status means accepted work, not successful
employer applications. This first batch implementation supports complete permissioned API
packages; unsupported portals retain individual manual handoff. No blanket approval of
future jobs is provided.

Application responses add `opening_key`, `employer_key`, `admission_snapshot`,
`pricing_snapshot`, `batch_id` and `admission` state/timestamps. The future browser executor
must bind its command and independent recovery permit to the immutable approval ID,
admission ID, package/artifact/actions, owner/device/origin and any exact batch digest.
These are integration inputs, not implemented production device-authority endpoints.

## Configuration

All limits reject zero, negative, non-integer and excessively large values. Defaults are
conservative policy choices pending controlled pilot measurements.

| Environment setting | Default |
| --- | ---: |
| `EMPLOYER_CANDIDATE_DAILY_LIMIT` | 10 |
| `EMPLOYER_CANDIDATE_ROLLING_LIMIT` | 50 |
| `EMPLOYER_CANDIDATE_ROLLING_DAYS` | 30 |
| `EMPLOYER_CANDIDATE_PENDING_LIMIT` | 10 |
| `EMPLOYER_CANDIDATE_DAILY_CREDIT_LIMIT` | 100 |
| `EMPLOYER_CANDIDATE_ROLLING_CREDIT_LIMIT` | 500 |
| `EMPLOYER_BATCH_LIMIT` | 10 |
| `EMPLOYER_BATCH_CREDIT_LIMIT` | 100 |
| `EMPLOYER_ADMISSION_QUOTE_HOURS` | 24 |
| `EMPLOYER_PER_EMPLOYER_DAILY_LIMIT` | 5 |
| `EMPLOYER_PER_EMPLOYER_ROLLING_LIMIT` | 10 |
| `EMPLOYER_PER_EMPLOYER_ROLLING_DAYS` | 30 |

## Migration and evidence

`20261008_0009` follows `20261008_0008`. It adds the batch/admission tables, quote and
identity columns, indexes and exact delivery uniqueness. Existing unknown/confirmed sends
and queued attempts are conservatively backfilled into durable usage/claims. Duplicate
historical deliveries retain their financial rows; only the first alias receives the
canonical delivery key. Legacy applications have no new approved safety quote and cannot
acquire a new send without saving and reviewing a fresh package.

Local verification uses synthetic resumes, forms, credentials and provider callbacks.
Twenty-six PostgreSQL cases ran against dedicated schemas inside the disposable local
`hirewiz_admission_test` database; no production database or employer submission was used.

- [x] Simultaneous daily admissions: exactly one slot, money reserve and outbox event.
- [x] Simultaneous duplicate execution: one admission and one reservation event.
- [x] Form/POST callbacks can acquire candidate and application locks independently.
- [x] Cancellation during external preflight prevents the POST and returns credits.
- [x] Cancellation during possible send retains uncertainty; late complete receipt settles.
- [x] Exact batch execution racing account deletion does not deadlock or leave launchable jobs.
- [x] Creation winning the lifecycle gate is included in deletion's subsequent application
  scan; deletion winning fences creation. Both deterministic ordering regressions reproduce
  failure when the advisory gate is removed.
- [x] Post-send account deletion retains anonymous possible-send/financial holds.
- [x] Clean PostgreSQL migration and populated downgrade/upgrade preserve unknown usage,
  possible-send times, balances and duplicate historical delivery rows.
- [x] Clean SQLite upgrade and 0009 downgrade/upgrade use compatible batch alteration.
- [x] Positive budgets, quote/identity invalidation, tighter launch caps, exact alias billing,
  batch atomic rollback, expiry/cancellation and ownership are covered by API/domain fixtures.
- [x] Same-session authenticated identities cannot spend the final service credits or
  analysis unit twice. Competing reserves/refunds, simultaneous refunds, current Premium
  decisions and successful/insufficient sequential batch debits preserve balances and ledgers.
- [x] A previously approved application retains its exact quoted pricing version at reserve.
- [x] Legacy analysis wrappers acquire the owner lock before inserting a foreign-key run;
  concurrent cached-auth calls yield to provider work once and reject the second reservation.
- [x] Explicit no-autoflush protection leaves unrelated pending runs untouched until their
  own insertion boundary, including when a caller enables ORM autoflush.
- [x] A cancellation committed before a delayed batch approval remains cancelled; the
  final locked batch refresh prevents stale status revival and preserves pending app bindings.
- [x] A cached self-admin usage adjustment uses the current post-reservation balance and
  records the correct before/after audit; replay does not add a second adjustment.

Proof lives in `backend/tests/test_employer_admissions.py`,
`backend/tests/test_employer_admissions_postgres.py`, `backend/tests/test_alembic_schema.py`
and existing employer service/artifact tests. The integrated backend suite passed 511 tests
with PostgreSQL enabled after the lifecycle, entitlement, legacy FK-lock, admin and batch
cache-race repairs: no failures or skips, including all 26 PostgreSQL cases and the SQLite
round-trip. A separate clean
PostgreSQL `head -> 0008 -> head` round-trip passed. The exact CI Ruff scope passed and
Mypy passed across 49 configured source files. Backend OpenAPI was regenerated after
integration; the entitlement and pricing-provenance repairs do not alter its contract.
Three dependency deprecation warnings remain. Evidence is retained locally in
`/tmp/hirewiz-merged-backend-final-20261008.xml` and
`/tmp/hirewiz-merged-migration-0009-20261008.json`; these are local verification results,
not themselves remote CI or deployment proof. Exact-commit CI and production rollout
subsequently passed; their independent evidence is in the release record linked above.

The opt-in PostgreSQL harness deliberately accepts only `127.0.0.1:55433` with database
`hirewiz_admission_test` and removes only its own random schemas. It is skipped in ordinary
environments without `HIREWIZ_TEST_POSTGRES_URL`. CI now provisions that explicit disposable
database using its PostgreSQL 17 service on port 55433 and sets the harness environment
for the complete backend suite. The new companion CI job separately validates the disabled
package, unit protocol, dependency audit and localhost synthetic MV3 flow.
A protected backup, migration checks and actual controlled production upgrade passed for
the promoted release. The backup has not been restored. Rolling back 0009
removes the new safeguards, so outbound execution must remain stopped throughout rollback.
The pilot-sized backfill has not been benchmarked at a large production catalog size.

## Gates still open

- [ ] Integrate the independently retained epoch, cancellation/deletion tombstones and
  begun-action usage journal in `RECOVERY_FOUNDATION_PLAN.md`; test restore quarantine.
  PostgreSQL admissions alone cannot survive restoring an older database safely.
- [ ] Implement real owner/device pairing and online single-use disclosure authority, and
  bind the standalone companion to these admissions. Its localhost fixture is separate.
- [ ] Obtain actual per-tenant write permits and scoped credentials; prove complete forms,
  consents, file constraints and receipts in authorized sandboxes before enablement.
- [ ] Reconcile off-platform applications and employer rejection/reapplication waiting
  periods before claiming complete employer-specific policy compliance.
- [ ] Implement operator-controlled canonical identity remapping preserving unknown and
  confirmed claims across tenant/group changes; do not infer equivalence from titles.
- [ ] Measure credential-wide/fleet fairness, Retry-After scheduling and sustained load,
  database/pool outages, recovery, retention and user-facing batch UI performance.
- [x] Synchronize generated contracts, pass exact-commit CI, stage and promote the immutable
  backend/frontend release, verify schema/health/private workers, and observe a real public
  feed refresh and five-width public smoke. This does not close the wider acceptance gates.

No worldwide recall, greater-than-95% coverage, real auto-apply, production browser command
or completed G05 acceptance claim is made by this foundation.
