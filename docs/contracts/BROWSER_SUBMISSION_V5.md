# Unified-credit final submission contract

This is source-only protocol 5. All submission, unified-execution and native
authority gates remain closed. The JSON fixture is synthetic format data, not a
native receipt, price qualification, approval or permission to submit.

Protocol 4 retains **20 legacy service credits** and its original accepted
obligations. Protocol 5 requires the exact `SubmissionBilling` object and
`fee_credits: 3`; it means **three unified credits**, not INR 3. Private execution
funding uses INR minor units and a reviewed allowance no greater than 135 paise.
It never creates Gemini work. Fill, attachment and upload remain separate free
reviews and cannot acquire a submission charge or final-click authority.

The existing signed companion `inspect` request for version 5 creates a fresh
draft and server-owned service quote without reserving credits. The backend
derives the input preimage from current owned records, original file/answers,
native upload and document evidence and the qualified native route. The preimage
contains `PriceCore`, but excludes generated quote IDs, the final priced package
digest and review digest. After quote creation, the package and review bind the
full billing snapshot, preventing a circular hash or price substitution.
The server-only `package_inputs_sha256` uses the existing complete original
package serializer with only `pricing_snapshot` set to null. Employer grants,
recipient hosts, original answers and consents, admission bounds and source
policy remain included. No final priced package, quote ID or review digest is
substituted into that preimage.

The new client explicitly requests `review_protocol_version: 5` in both
`application` and `inspect`, and requires version 5 in the response. It never
negotiates currency from the fee or falls back to version 4. A fresh version-5
review requires an eligible current unified runtime and the independent new
submission gate. Existing accepted version-4 checkpoints remain readable and
executable under their original contract. An older client can create a fresh
version-4 review only for a legacy owner under the existing legacy gates.
Additive availability fields are `unified_submit_available` and the exact
ordered `submit_protocol_versions: [4, 5]` when the independent gate is on.
They advertise configured support only. They do not prove account eligibility,
funding or approval, and the old `submit_fee_credits: 20` remains legacy advice.

`SubmissionBilling` has exactly thirteen keys: `schema_version`,
`billing_currency`, `operation`, `credits`, `tariff_version`, `route_id`,
`route_version`, `delivery_contract_sha256`, `financial_policy_sha256`, `quote_id`,
`input_fingerprint`, `quote_contract_sha256`, and `quote_expires_at_ms`.
`quote_contract_sha256` is the central quote's immutable contract fingerprint;
the policy and route hashes come from that same server-owned quote. These are
bindings, not client-provided authority. All digests are lowercase SHA-256 hex;
expiry is an exact safe positive integer in UTC epoch milliseconds. Amount and
confirmation accept integer `3` and boolean `true` only, without coercion.

The review payload and response both carry `review_protocol_version: 5`,
`fee_credits: 3` and the same `billing` object. The payload's existing `pricing`
field equals that billing object. Its canonical SHA-256 remains the native
`SubmitBinding.pricing_sha256`. The canonical SHA-256 of the complete payload
remains `review_digest`. The signed lease repeats the exact version, fee,
billing, review digest and existing file/document/destination/native bindings.
The fixture contains complete canonical review and unsigned lease payloads,
including synthetic reviewed form, upload and receipt-format fields. Those
fields are format examples only. A test must obtain its own controlled signing
authority; the fixture has no signature, grant, employer receipt or permission.

Approval keeps the existing candidate endpoint and CSRF/session checks:

`POST /api/browser-assistance/candidate/approve`

`{identity, review_digest, confirmed: true, accepted_service_quote_id}`

The accepted quote ID must equal `billing.quote_id`. This request cannot supply
a price, route, currency, funding budget or native proof. Approval atomically
reserves three unified credits and capacity funding through the existing central
quote/admission/hold APIs. It does not enqueue an employer submission task.
There is no new BFF action and no automatic quote acceptance.

A draft expires at the earlier existing review deadline or quote expiry. Expired
or changed drafts require a new companion inspection and review/quote; never
refresh price silently or approve against a newer displayed value. A replacement
unaccepted draft cancels the prior draft, preserving its unaccepted quote history.
An accepted hold is not repriced/replaced. Before a possible-send claim, explicit
cancellation releases the accepted hold once; a fresh attempt uses a new
application identity because the wallet enforces one operation/source identity.
After a possible-send claim, cancellation, expiry and transport uncertainty retain
the three-credit hold and cash exposure until authoritative reconciliation.

Terminal response states remain `not_launched`, `unknown`, `confirmed` and
`verified_not_submitted`. Only the existing exact native employer receipt can
settle three credits. Browser messages and generic success responses cannot.
An old protocol-4 accepted hold continues its original 20-credit settlement or
release after unified activation. A new protocol-4 approval for a unified owner
must fail closed and require a fresh protocol-5 review; there is no legacy fallback.

Existing quote/reservation/hold tables provide the durable join:
`CreditReservation(user_id, operation="verified_submission", source_id=application.id)`
is unique; the accepted `CreditServiceQuote` and unique `CreditExecutionHold` bind
to that reservation. `EmployerApplication.reservation_id` remains a legacy-only
foreign key. New unified billing never puts a unified ID into that legacy column.
Accepted submission history must retain unified currency selection when execution
is paused. Actual account deletion retains detached money obligations and does not
turn an unknown application into a refund or permit another click.

The backend adapter is `credits.submission`. Its route must be native with no
model attempts, match the actual finite delivery-source digest, fit the reviewed
artifact/answer/review limits and reserve at most 135 INR minor units through the
central money admission. The internal hold explicitly binds `funding_currency:
INR` separately from `billing_currency: credits` and the billing hash. No worker
outbox is created. The existing native one-opening claim and exact hosted receipt
validator continue to control the sole possible send and delivery settlement.
Only verified metering can settle native cost; a customer refund or receipt does
not invent a zero native bill.

`UNIFIED_BROWSER_SUBMISSION_ENABLED` defaults to false. All prior submit,
native-upload, account/device, execution audience and funding gates also apply.
There is no fallback to a new 20-credit hold for a unified owner. An unaccepted
legacy draft can move to a fresh explicit version-5 review only with no legacy
reservation or attempted send; an accepted legacy obligation never changes units.

Unaccepted expiry erases the old display without issuing another quote or
consuming the application identity. A new signed inspection refreshes admission
bounds and issues a distinct quote. Expiry of an accepted unlaunched reservation
releases it once and closes that application; a new attempt needs a new identity.
Old replaced displays cannot cancel a newer accepted quote. Application/history
responses show actual three-credit holds even though the legacy reservation FK
is null. Unquoted preparation has `billing_currency: credits` and no `billing`
object; only an actual quote produces the thirteen-key billing object.

The still-unapplied schema26 migration expands only the review protocol check
from 1–4 to 1–5 alongside its existing fence/reset changes. Historical schema19,
20, 21 and 25 readers remain exact. The distinct pending26 catalog reader checks
the new bound directly. Frozen schema26/native evidence from prior source does
not qualify this revised DDL. The migration downgrade remains refused.

Required further qualification includes the four owned PostgreSQL submission
races and revised real catalog case, the actual paired Chrome protocol-5 path,
the unchanged-client free pilot with paid gates closed, and a current reviewed
funded native delivery route. Local SQLite and synthetic format/SDK ports prove
neither production concurrency nor an employer receipt, invoice or live funding.
The existing business-canary boundary does not yet admit an application source
kind; it correctly refuses this new operation. That finite canary integration
and its real-business denominator must be reviewed before a canary rollout. The
local fixtures use explicitly synthetic `reviewed_public` review data only.
