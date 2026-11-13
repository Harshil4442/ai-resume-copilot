# F07: weekly role updates and explicit email consent

Status: implemented and connected in the local development branch, default off.
Targeted local verification has passed. This is not a production, provider,
capacity or commercial acceptance record. The original eight delivery stages and
83 requirement IDs remain authoritative; no major checkpoint is declared complete.

## User behavior

The Profile page exposes an explicit weekly-email preference. No account is opted
in automatically. The control reads the current native state and sends the exact
expected generation with an explicit confirmation. An uncertain write requires a
manual state refresh; the browser never repeats the mutation automatically.
Disabled or malformed delivery flags refuse private preference requests before
account authentication, SQL or SDK construction. Enabled routes still require the
existing candidate authentication; public disable-only opt-out stays separate.

An eligible digest includes at most five distinct updates to roles the owner
already saved or obtained through a completed search. It does not perform another
job search, refresh employer feeds, call a model, include resume/evidence/answers,
or create per-job email alerts. There is no customer credit charge for a digest.

Only open postings from recently checked, healthy, enabled, operator-reviewed
employer-origin sources qualify. New/updated postings are bounded to seven days;
source/posting checks must be no older than 24 hours. Known passed owner deadlines,
future timestamps, owner/source mismatches and invalid origin identities refuse.
One bounded metadata query selects at most 50 candidates before deterministic
opening-key deduplication and the five-item limit. Empty digests are skipped.

Weekly means at most one admitted message per native subject and UTC ISO week.
Delivery depends on available optional-mail capacity. The UI discloses this limit.
A scheduled eligibility check is not a guaranteed delivery time. Normal dashboard
access and account/security email remain separate.

## Native consent and delivery authority

The actual configured native adapter uses the existing candidate configuration,
GCS witness and scoped authority boundaries. Four typed command kinds and four
closed namespaces cover consent heads/history, send BEGINs and outcomes. Complete
census validation checks their provenance, monotonic consent chain, stable weekly
keys, immutable message binding and outcome consistency. Unknown namespaces are
not silently adopted by older code.

Preference changes require the current native browser session, subject, SQL/native
credential revision and verified mailbox. Consent is independent of password
rotation: future sends may use a fresh credential revision with the same account,
subject, mailbox and unchanged consent. Stale requests and an in-flight retry with
changed credentials refuse.

Every send requires a fresh current subject/mailbox/consent check and an original
native BEGIN acknowledgment. The original callback runs only after configuration
and witness gates, outside SQL/native locks. The fixed message hash includes the
actual immutable provider JSON bytes, destination, sender, endpoint and stable
idempotency key; API credentials are excluded so rotation cannot buy a new slot.
The encrypted queued week is checked against the server week inside native commit
before creating a BEGIN, preventing a Sunday/Monday retargeting race.

There are at most three attempts within one live original acknowledgment chain.
A retry requires the exact acknowledged prior native known-429 outcome and the
provider's actual bounded Retry-After. No stored row, reconstructed receipt or new
worker can recreate that live callback ownership. Unknown provider/outcome results
never retry. A completely unpersisted, pre-provider ambiguous BEGIN leaves no
durable row; a fresh conditional create on the same stable key may win, while a
late competing create cannot also win. No provider callback runs in the ambiguous
invocation. Persisted BEGINs and all post-provider uncertainty block cold replay.

## SQL projection, migration and privacy

Migration `20261105_0027` adds only `notification_preferences`, linked to the owner
with cascading erasure and a bounded due-discovery index. There is no opt-in
backfill or default. The row stores account binding, native generation/hash,
recipient hash, policy, enabled state and next check time. It is scheduling/cache
metadata, never a send grant. A read of a retained native head cannot be projected
as an original write acknowledgment.

The compatible application must be qualified and promoted with optional mail off
**before** the additive migration. The older production privacy implementation
requires schema26 and cannot be treated as schema27-compatible. The bridge checks
the exact26/27 marker and current namespace: only exact26 with no projection table
may omit the cache during export/erasure. Exact27 requires its ordinary permanent,
unshadowed table; unknown markers, table drift and missing successor tables refuse.
There is no catch-and-skip fallback and no request repairs the schema.

Preference reads/changes and delivery require the successor schema before any
native consent mutation or send admission. A valid login-free opt-out can still
disable an existing native consent after an exact26 SQL-only restoration loses
the scheduling projection. Its original native acknowledgment is returned with
`projection_current=false`; no table, consent or schedule is recreated. Existing
privacy-erasure proofs and completion-audit versions remain unchanged across the
bridge, and the canonical evidence-write fence is not weakened.

The initial all-off SQL release unit observes the exact projection columns,
constraints, index, FK triggers and runtime/observer permissions while preserving
the protected schema26 catalog. Traditional inheritance, RLS, extra grants and an
observer switching into the runtime writer role refuse. Its supplied-transaction
step uses standard Alembic, bounded migration/FK locks, and grants only new-table
runtime CRUD and observer SELECT. It restores the original schema permissions and
checks retained table ACLs, evidence fences and financial invariants.

The SQL unit does not obtain credentials, attest deployed flags, commit, retry,
reconcile an uncertain commit or promote services. Its local native controller
provides the separate orchestration described below. Initial all-off catalog
evidence does not qualify future populated-wallet rollout or commercial activation.

The orchestration captures SQL facts, releases owner/row locks for native IO, then
reacquires the existing account-erasure owner fence before projecting an original
change acknowledgment. It rechecks account revision and mailbox and refuses
backward generations or same-generation content changes. Owner export labels the
preference as a projection; account erasure and absence auditing cover the table.
Downgrade refuses until native history and unresolved work have a separately
reviewed retirement procedure; empty SQL alone cannot prove native non-use.

Dedicated outbox types/statuses isolate digests from generic lifecycle workers,
even if an old row is mislabelled pending. Subject/text, including the opt-out
capability, are encrypted with a separate purpose-derived AEAD key and bound to
the exact outbox ID, owner, recipient, key and metadata. Every physical request
rechecks current SQL owner/mailbox/projection after funding, then releases locks
before HTTP. Expired snapshots are cleared after 24 hours even while paused;
stale processing becomes unknown, never pending. Native cadence and detached
funding/unknown metadata remain retained independently of personal message bodies.

## Unsubscribe

The email link opens a public confirmation page without signing in. Its proof is
carried in the fragment, captured in memory and removed before telemetry. A GET
changes nothing. One explicit POST carries the proof in its body. Same-document
link replacement invalidates the old context and requires another explicit click;
old responses cannot update the new context. Proofs never enter DOM, persistent
storage, query keys, request URLs or telemetry.

The purpose-isolated MAC binds exact account identity, recipient hash, consent
generation, policy, audience and bounded expiry. Verified Python-port objects also
retain their original claims; ordinary copies cannot change them while retaining
a verified seal. The codec creates no login/session/send authority. Native current
state and CAS make the disable request single-use.

A separate disable-only native port remains available while optional delivery is
paused. It still requires current subject, credentials, mailbox, exact consent and
configuration/witness checks. It cannot enable mail, send, create missing consent
or fabricate a browser session. Both private and public HTTP bodies are bounded;
the public frontend transport needs no auth-cookie parser. Exact-route telemetry,
no-store, no-referrer, noindex and CSP protections cover the confirmation page/API.

## Cost and provider controls

Existing global funding remains 100 daily / 1,000 monthly attempt units, with
three units reserved per admitted message. Optional lifecycle funding is further
limited to 60 daily / 600 monthly units, and each new optional reservation must
leave 30 daily / 300 monthly units currently unreserved for security traffic.
These are source admission ceilings, not provider entitlements or a throughput
promise. Existing funded envelopes retain their original period, deadline and
unknown/no-refund accounting. Batch admission remains atomic under the real
PostgreSQL advisory transaction lock.

Digest queue funding is committed with the outbox before a native send BEGIN.
The callback reuses the exact envelope. A known provider failure result is exposed
to the native adapter only after the original SQL outcome commit acknowledges;
ambiguous SQL commits do not supply a reconstructed provider receipt.

The Resend adapter performs one fixed-endpoint HTTPS POST at a time, without
proxies, redirects, automatic retries, environment CA overrides or TLS key logs.
A total HTTP deadline covers streaming and the response has a 16 KiB bound.
Success requires the exact valid JSON receipt; malformed, truncated, timed-out or
uncertain responses remain unknown. No model calls or cloud QA were needed for
this source work. The native metadata governor remains unchanged at 40/minute;
representative capacity qualification is still required.

## Native deployment and recovery controller

The local controller captures a bridge plan through the existing native successor
factory. Its distinct source observer admits exactly the reviewed migration chain
through27 and pins the application Dockerfile before the existing real Git,
Cloud Build, image-digest and CI checks. The pinned image explicitly sets both
optional-mail flags false. Only after proving that same image may an absent
Cloud Run flag inherit the baked false value; true, malformed and secret-reference
overrides refuse.

The stock successor stages and promotes the compatible bridge while SQL remains26.
Migration capture then checks all three services and every traffic/tag/latest
revision, original stage/promote request and completion records, native operations,
HTTP health, runtime identity, IAM, resources and numeric secret versions. The
existing fresh document-acceptance gate remains required. An all-off bridge does
not bypass an expired scanner or held document qualification.

Before opening the write connection, the controller durably records one migration
intent with file and directory fsync. The fixed existing Neon manager supplies the
bootstrap connection; no caller DSN, password or transport is accepted. The DDL
transaction also inserts a detached audit receipt binding that exact intent, plan,
source and catalog. It has no customer foreign key and commits atomically with the
new table and grants.

Read-only recovery uses separately issued exact26 or exact27 database bindings.
Every earlier purpose keeps its earlier schema; the new27 purpose cannot relabel
an old binding. Recovery verifies the same physical target and the same-transaction
receipt, never just a27 marker. An uncertain commit cannot start another DDL
transaction. Known commit acknowledgments and checked observations must agree
with the fresh SQL result, including within the original invocation. Contradictory
or malformed custody refuses; the controller never drops tables, restores an old
image, enables features or grants LOGIN as recovery.

Pure native census imports now use a lightweight mail result/error contract.
They do not initialize application ORM, database setup or notification senders.
Existing funding imports re-export the same classes and validator; validation,
funding amounts, outcome acknowledgment and JSON wire values remain unchanged.

## Application and historical qualification

The new application has a separate Source27 contract. It binds the 309 application
files, the additive notification API, migration, default-off image, consent
commands, unsubscribe purpose and release checks. Its application/operator source
identity explicitly grants no native document-processing or commercial authority.
Existing producer identities and historical quality results retain their original
meaning; they cannot qualify the new application by being renamed.

The updated CI workflow retains historical checks against the exact previously
qualified schema26 source and adds mandatory current schema27 compatibility and
protocol checks. Current PostgreSQL tests use the prepared Linux CI database;
missing prerequisites fail instead of silently skipping. The protocol checks
include actual local Firestore emulator transactions. Separate current/prior
application processes must also demonstrate compatible preserved SQL data.

These runners and source guards are local implementation. Owned fixture tests and
emulator results do not constitute an exact-source CI result. The historical-source
runner and current full-source qualification still require a genuine CI run;
no previous source's passing run can be reused for this branch.

## Local evidence and remaining release work

Focused source tests cover real owned PostgreSQL locking/schema/privacy behavior,
actual configured SDK/native transactions over intercepted transports, bounded HTTP
streams and frontend owner/proof races. Connected tests exercise API consent to
native authority to SQL schedule/funding to the actual intercepted provider adapter,
including rate limits, restored queues, opt-out, erasure and mailbox changes.
Local production build and mobile confirmation hydration passed under the actual
strict CSP with mocked API traffic and no external requests. They do not establish
production IAM, provider durability, live email delivery or load capacity.

The connected local PostgreSQL/native-SDK tests additionally cover schema26
refusing enable/delivery before a native write and actual schema27 consent followed
by a SQL-only predecessor restoration: opt-out succeeds while delivery is paused,
does not recreate the projection and refuses reuse of the consumed proof. These
are owned synthetic restoration controls, not a production downgrade procedure.
The frozen canonical decoder from the last-verified DB8 source accepts genuine
earlier scoped records and refuses the valid new digest intent accepted by the
current decoder. This records the compatibility boundary; rollback still needs
its own full qualification.

Before activation, the release still needs:

- Qualification and controlled execution of the local authenticated schema27
  bridge/migration/recovery controller, including real identity and IAM checks.
- Matched source/profile/OpenAPI qualification and genuine exact-source CI.
- Full required regressions and rollback/old-reader denial for the new native rows.
- Fresh scanner verification and the already-required document/quality gates.
- Reviewed live mail delivery, representative capacity and budget/retention checks.
- Controlled API/worker/frontend promotion and explicit enablement readback.

GitHub Support cleared the account billing lock on November 6, 2026. The
November 13 CI run on the release branch started all five jobs. Release still
requires passing CI for the final source commit. Scanner maintenance, paid
document QA, model calls and commercial activation remain on hold. This
increment still needs the qualified bridge rollout and schema migration. The
native protocol and profile successors must be qualified together before
promotion, and rollback must account for rows written by the new protocol.
