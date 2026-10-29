# Bounded, explicitly opted-in credit canary

This implements the admission controls for existing R02/V04. It does not start a
canary, qualify a model, manufacture consent, purchase credits or complete Stage 7.
Human quality review, representative shadow accounting and the existing release,
funding, provider and commercial gates remain separate. Internal synthetic smoke
runs do not qualify a public route or contribute to these denominators.

## Reviewed audience and actual eligibility

`CanaryReview` is a frozen typed contract recorded in existing `AdminAuditEvent`
rows. No schema change is required. A review binds an exact runtime digest
(including financial policy, budgets, model quotes, output-contract digests,
qualified routes and expiry), complete notice hash, retained operator evidence,
actual account-identity census, explicit exclusions, selected owner IDs, service
families and a finite traffic window of at most 31 days. It expires no later than
its funding/policy review.

The operator reviews that accounts represent real business candidates and excludes
internal, synthetic and testing accounts. Terms acceptance alone cannot prove that
an account is a real customer. This is an explicit operator trust boundary; an AI
assertion or a forecast of 10,000 users supplies no evidence. The CLI checks custody
of the private review artifact, not its truth. Reserved test domains are excluded
in code as additional protection, not as a real-person classifier.

The census is read from retained `User` identity, email, Terms/Privacy versions and
age/Terms timestamps. It uses a captured cutoff: subsequent signups cannot silently
expand the selected audience. Every new quote and admission compares the complete
frozen census against current SQL values. Identity changes or deletion invalidate
new admission and need a new reviewed audience. No cross-request authority cache
is introduced. The bounded first implementation supports at most 2,000 eligible
accounts; larger or ambiguous reads fail closed instead of accepting a partial
census. The selected cohort must satisfy `20 × selected_count <= eligible_count`.

`UNIFIED_CREDIT_CANARY_OWNER_IDS` no longer authorizes any owner. Canary execution
requires both `UNIFIED_CREDIT_CANARY_REVIEW_ID` and the exact recorded
`UNIFIED_CREDIT_CANARY_REVIEW_SHA256`, alongside the existing execution flag and
runtime. A missing, stale, oversized or mismatched review denies new work. This
change supplies no environment values and opens no flag. Retained SQL review
state keeps the new-model fence closed if volatile pointers/flags are cleared;
prior explicit owner participation remains an owned currency binding. Missing
configuration never means an automatic legacy rollback. Only an explicit valid
`reviewed_public` configuration ends that unresolved fence. The existing exact
public release gates remain required and do not invent an owner ID for a public
catalog request.

## Owner choice and minimal UI contract

Authenticated `GET /v1/credits/canary-enrollment` returns only the current owner's
notice, digest, expiration, recorded participation status, `can_opt_in` and execution
flag status. It does not return the census or other selected IDs. Responses are
private and `no-store`. An owner may select an exact previously recorded review
using the bounded `review_id` and `review_sha256` query pair; these selectors confer
no authority unless the immutable review actually selected that authenticated
owner. If the current pointer is missing or no longer selects the owner, GET can
fall back to that owner's latest retained choice. Paused, expired or rotated
participation remains readable and revocable by its owner without restoring the
current runtime or environment pointer; it does not authorize execution.

Authenticated `POST /v1/credits/canary-enrollment` takes `review_id`,
`review_sha256`, `notice_sha256`, `choice` (`opt_in` or `revoke`) and an
`idempotency_key`. Opt-in additionally requires explicit `confirmed_notice: true`.
The owner comes only from authentication; extra owner IDs are rejected. A new
opt-in must match the current exact live review/runtime. A revocation may select
an older immutable review without using the current environment pointer. Each
choice appends a monotonic hash-linked audit record under the existing owner and
financial locks. Operator review, a testing allowance, accepting Terms or a saved
password never grants participation. Revocation does not require an enabled
runtime or fresh funding. Replaying an old opt-in request returns that old receipt
and cannot overwrite a later revocation.

The Billing participation panel displays the complete notice with an initially
unchecked choice and a separate explicit keyboard-accessible confirmation. It
shows Stop participating for current/retained opt-ins, including paused reviews,
and reads the exact owned review after a choice before reporting current status.
An unknown response permits read-only status checking instead of automatic POST
retries. Changed notices reset confirmation. The panel appears only for a valid
owned invitation or retained choice and uses the existing owner-scoped query
boundary; unmounting stops further reads/callbacks from the old surface.

The exact BFF allowlist permits only GET/POST, a bounded paired review selector,
and the exact choice fields. It preserves existing same-origin mutation and
session/bearer policy and limits streamed POST bodies to 2 KiB. No other owner IDs
are sent. A verified choice refreshes availability and pass eligibility once;
wallet/history reads and monetary rights are unchanged. Late earlier catalog
reads cannot overwrite refreshed eligibility. An acknowledgment, `can_opt_in`,
a displayed execution flag or a quote never reserves a traffic slot. This code
does not enable a rollout flag. Quotes and normal price review remain separate
and never grant credits.

## Literal accepted-traffic limit

The canary numerator is append-only accepted canary operations, not model calls,
HTTP requests, polls or completed delivery. The denominator is actual accepted
business `AnalysisRun`/`EmployerSearch` rows for reviewed account identities,
service families and time window. Genuine basic/curated or already accepted legacy
operations within that same reviewed scope can supply baseline activity. Thus a
canary does not need previously enabled canary traffic to bootstrap. A new canary
acceptance is counted inside the same transaction as its wallet reservation.

Semantic duplicates are deduplicated by `(owner, service_family, input_fingerprint)`
in the baseline. `dev_` runs, internal evaluation schemas, declared shadow runs,
other owners, out-of-window work and reads are excluded. Review explicitly bounds
the eligible service families; unrelated free actions are not a denominator.
Traffic inspection is limited to 10,000 retained accepted rows; exceeding the
bound closes admission and requires an independently reviewed extension rather
than extrapolation. Prior canary acceptance proofs in the reviewed window are
retained across audience IDs so a new review cannot reset the numerator.

Exact integer arithmetic requires
`20 × (prior_canary_acceptances + 1) <= eligible_business_acceptances_including_new`.
For example, 19 genuine distinct baseline acceptances permit one canary acceptance;
a second requires 19 more. A quote does not reserve a slot. Zero baseline means no
new canary work and no charge. A refund or provider failure does not erase the
original canary acceptance or increase future allowance. New canary audit times
and their traffic-window bounds use explicit naive UTC, matching the existing
`AdminAuditEvent` timestamp column without depending on PG session timezone. The shared existing
financial transaction locks serialize competitors; rollback removes both an
uncommitted acceptance and its wallet effects. During PostgreSQL acceptance only,
`FOR SHARE NOWAIT` protects the already-reviewed finite census identities through
commit before the fresh comparison. A concurrent identity update/deletion causes
a no-charge refusal and rollback rather than waiting under the financial locks.
Quotes and saved reads do not acquire these census locks. This conservative
contention tradeoff protects the 5% boundary without a deadlock-prone owner-lock
wait, a new population gate or authority cached across requests.

## No legacy AI escape; accepted work survives

A declared canary fences new legacy AI admission and, defensively, checks the
persisted provider operation before both Gemini token counting and generation.
This includes mixed deterministic/AI paths whose generation branch was not known
at initial admission. Nonselected or opted-out users cannot spend the other
currency to reach new model calls. Basic/curated results stay available. Existing
accepted legacy work predating the reviewed cutover retains its frozen authority;
explicitly authorized synthetic development runners remain separate and excluded
from real business traffic. A `dev_` label and schema tag alone grant nothing:
only the exact in-process scope established by `development_runner.execute`
after its full native/funding preflight can authorize an internal request. Each
provider boundary rechecks the dedicated loopback database/private schema,
current authorization and funding artifact hashes, exact synthetic owner/run
inputs and current implementation contract. Production and label-only runs are
rejected; old authorization JSON remains readable without being requalified.

Each new unified execution stores its immutable acceptance proof in the existing
hold. Provider preflight verifies that proof against its audit row and actual
owned reservation/source/fingerprint/family. Subsequent opt-out, expiration or
canary pause does not strand previously accepted work. Existing global emergency
AI pauses, ownership, quality and financial checks still apply. Settlement,
customer refunds, unknown supplier liabilities and saved reads do not depend on
current canary enrollment. New explicitly reviewed public admissions also freeze
a small server-verified public proof in the existing hold snapshot, bound to its
runtime, owned source/fingerprint and immutable route/policy/budget hashes. This
preserves accepted public work during a later unresolved canary pause; it neither
backfills old records nor opens new public work.

## Private operator workflow

Use the existing retained database credentials via the normal private credential
manager; never paste a URL or secret into a plan, command output or repository.
`scripts/review_canary_audience.py` requires PostgreSQL, verified TLS and observed
native server/catalog/role/schema identity, using the existing schema25 identity
helper. Only its explicit isolated localhost test transport is exempt from TLS.

1. Prepare a private `0700` directory, `0600` reviewed evidence artifact and `0600`
   JSON spec. The spec fields are `id`, `notice`, `reviewed_by`,
   `operator_native_identity_sha256`, `review_evidence_sha256`,
   `real_business_accounts_and_exclusions_reviewed: true`, `expires_at`,
   `traffic_started_at`, sorted `selected_owner_ids`, sorted `excluded_owner_ids`
   and sorted `families`.
2. Run `capture --spec <absolute-private-spec> --review-evidence <absolute-private-evidence>
   --plan <new-absolute-private-plan>`. Capture reads a repeatable SQL snapshot and
   creates an immutable private plan; it performs no database mutations.
3. Review that exact plan and evidence. Run `approve --plan <same-plan>
   --plan-sha256 <captured-sha> --review-evidence <same-evidence>
   --accept-reviewed-audience` only under separately authorized operator scope.
   This inserts only the immutable review audit. It grants no consent, credits or
   runtime activation.
4. If commit acknowledgment is uncertain, use `observe` with the same plan, SHA
   and evidence. Do not create a new review or retry with another audience ID to
   resolve uncertainty. Exact replay inserts no duplicate review.

Tests use synthetic accounts and fake provider transport, never real customer
consent, genuine invoices, provider charges or production evidence. The separate
PostgreSQL tests exercise racing slots/choices and actual settlement/refund in
the established guarded per-test CI schema when `HIREWIZ_TEST_POSTGRES_URL` is set.
The optional `HIREWIZ_CANARY_TEST_ROOT` instead starts a new private local PG17
cluster and additionally exercises the private CLI. Neither path targets a
production database. Native document-worker integration needs its existing
independently generated document-test report and was not replaced by this suite.
