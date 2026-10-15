# Candidate password lifetime provisioning checkpoint

This is an isolated author checkpoint based on `e6a94843ecbd4a9db43cb905b12ed6ad09bd96fe`.
It is not integrated, deployed, or production-enabled. The existing production
backend/schema and public frontend are unchanged. Full browser assistance and the
paid auto-apply goal remain open.

## Implemented seam

Fresh password registration commits a real credential/account row and a `PENDING`
immutable SQL owner projection before native enrollment. The server generates a
random subject UUID, account-binding UUID and opaque principal commitment. No
email, numeric bearer, old SQL row or fixture registration event creates a lifetime.
The typed native enrollment atomically retains a genuine `SUBJECT_REGISTERED` event,
its chained `PASSWORD_ACCOUNT_BOUND` event, full ownership maps and the initial
credential revision. Registration becomes `ACTIVE` only after the original native
and journal acknowledgement and the final independently protected activation ACK.
An uncertain enrollment leaves `PENDING`; login and repeated registration cannot
adopt an old journal/status result to activate it.

Password login re-reads current credentials, verifies the password, and checks the
exact salted-hash commitment and SQL authentication generation. A verified session
command compares both values with the retained native credential revision. Reading
the latest native generation and attaching an old SQL password to it is prohibited.
A new session is returned only after its original owned native ACK, independently
protected final activation ACK and a current protected session check. The opaque
owned-ACK object is issuer-bound and consumed once; copied objects, detached status,
replayed execute, expiry and unknown commits do not mint activation authority.

The credential session context passes through the existing NextAuth credentials
flow into `JWT.browserPairingSession`. It never enters the public NextAuth session.
The backend bearer also contains an explicit versioned session/generation binding.
Mapped or pending accounts cannot use a numeric-only bearer; pending/deleted
projections cannot use legacy password login. Google email lookup cannot link a
mapped password account. Existing unenrolled legacy login remains compatibility
behavior and is not evidence of an independently retained lifetime or restore safety.

Versioned password registration/login/logout/password routes are supplied. The
existing registration route can use the genuine enrollment seam only when the
server explicitly sets `CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED=true` and supplies the
reviewed factory. The default factory remains unavailable and the flag is absent
by default. Logout/reset/delete call the independently protected lifetime port
before acknowledging or mutating SQL. Reset requires a protected generation and
new credential commitment plus exact ordinary-native projection before SQL change.
No bounded V2 whole-cohort closer is substituted for normal per-subject operations.

## Schema and release boundary

Additive migration `20261009_0011` follows `20261009_0010`. It creates the owner
projection and an opaque detached schema-use history; it does not backfill legacy
users. PostgreSQL guards owner identity immutability and generation rollback.
Deleting SQL customer rows does not erase the schema-use marker, and downgrade
refuses after enrollment use. This SQL guard does not prove a restored database has
complete retained history.

The current monetary preflight recognizes only 0009/0010 and deliberately refuses
0011. The proposal tests that refusal without changing or relabeling the observed
schema. A reviewed schema-0011 monetary/release preflight, writer retirement,
controlled migrations, native authority configuration, exact CI, immutable staging
and rollback/restore proof are prerequisites for a production rollout. An old
backend image that cannot recognize the new migration must not be restarted with
auto-migration enabled against schema0011.

## Legacy and Google boundaries

Legacy enrollment needs an explicit operator baseline, fresh credential/identity
verification, candidate consent and original-owned registration proof. Absence of a
mapping cannot certify that an account was never deleted. Login performs no legacy
bootstrap. Recovery must quarantine restored legacy numeric sessions at the actual
recovery boundary; that procedure is not implemented by this component.

Google candidate enrollment/recent authentication remains unsupported. The official
[Google OpenID Connect documentation](https://developers.google.com/identity/openid-connect/openid-connect)
identifies `sub` as the stable account identity and warns against email identity
linking. `iat` records token issuance; `auth_time` represents authentication time and
requires the applicable enabled claim request. A future Google adapter needs signed
issuer/subject/audience/nonce checks, an explicit binding policy and independently
retained enrollment plus verified recent authentication. No token issuance time is
substituted for recent password/provider authentication here.

## Evidence scope and remaining work

- [x] Author: actual credential SQL, actual native enrollment and verified
  session commands; original-owned acknowledgement fault cases.
- [x] Author: legacy numeric/pending/Google-email refusal and hidden JWT projection.
- [x] Author: actual PostgreSQL additive migration/immutable ownership/downgrade
  refusal and explicit schema0011 monetary-preflight refusal.
- [x] Author: complete protected inventory parser accepts the exact two-event
  enrollment intent.
- [ ] Independent review of the entire frozen patch and its current-source results.
- [ ] Actual V3 activation, normal scoped denials and credential projection jointly
  tested with this real issuer. Current service tests use an explicitly synthetic
  final activation/denial adapter; they are not V3 production availability evidence.
- [ ] Actual cold processes: frontend signup -> genuine SQL/native registration ->
  real NextAuth JWT -> browser-pairing BFF -> actual native pairing consumption.
  The factory seam supports this injection without seeding a candidate context.
- [ ] Frontend logout connected to retained revocation/reconciliation before claiming
  server sign-out. Clearing a local NextAuth cookie alone does not prove revocation.
- [ ] Actual initial-denial and effect-commit unknown/non-persisted cases, normal
  unrelated-user availability, full restore closure and cloud permission proofs.
  An initial denial registration that did not persist is not achieved revocation.
- [ ] Explicit reviewed legacy bootstrap/recovery baseline; Google identity support.
- [ ] Retention policy, load/scale acceptance, credential retirement, schema0011
  preflight, CI/staging, configuration and production promotion/monitoring.

No production credentials, cloud settings, candidates, payments, applications or
employer endpoints were touched. Synthetic SDK Storage transports and localhost
native emulators do not establish actual GCP IAM, retention or restore guarantees.

## Frozen author validation

The final current-source affected selection passed **108 tests with zero skips**
in 29.93 seconds. It exercised actual localhost native transactions, genuine SQL
password registration/session issuance, strict legacy access, the actual PostgreSQL
migration and the three affected schema-head assertions. Python source hashes were
identical before and after that run. Final evidence is retained at
`/tmp/hirewiz-candidate-author-final-v3-20261009.xml`; service final activation and
normal denial are explicitly synthetic ports in these tests.

Frontend lint, type checking and **9 actual NextAuth callback unit tests** passed;
their report is `/tmp/hirewiz-candidate-nextauth-author-final-20261009.json`.
The actual optimized Next build passed using an explicit synthetic build-only
secret and localhost NextAuth URL. Configured backend CI-domain mypy passed for
82 source files; the focused new domain/router/lifetime selection passed for
8 source files, and Ruff passed for the changed domain/native/router/test scope.

Earlier failed evidence is preserved. One initial emulator run omitted a fixture;
one PostgreSQL run deliberately refused the wrongly selected `postgres` database.
The final-v2 selection and isolated V2 race rerun each retained one native winner
but failed an assertion requiring both journal uploads: independent bounded
publication contention can withhold one upload owner. The final race test serializes
only journal admission/create, requires both receipts, then keeps the barrier and
actual competing ordinary-native commits. Its scope is ordinary-native cut safety;
it is not concurrent publication availability evidence. No production behavior or
assertion about the single winning session was weakened.

These checkpoints establish author validation only. Independent review, combined
V3 integration, actual cold browser processes, exact release/monetary schema0011
support and production deployment are separate pending checkpoints. The issuer's
process-local owned-plan/ACK retention also needs bounded lifetime/load acceptance
before production configuration can be supplied.

## R2: sensitive HTTP repair after independent review

The V1 independent review reproduced the native/core checks but held integration
because FastAPI's validation JSON reflected sensitive inputs. Pydantic's
`hide_input_in_errors` controls exception formatting and does not remove the
`input` values from FastAPI's default HTTP 422 serialization. V1 source, frozen
manifests, privacy probes and the failing independent results remain preserved.
The review also demonstrated a pre-existing unchanged-e6 bcrypt verification
defect with the installed bcrypt 5.0.0/Passlib 1.7.4 combination; that defect was
not introduced by candidate lifecycle provisioning.

Sensitive auth routes now use a route-scoped reader before FastAPI reads or parses
their body: registration/login on both existing and versioned routes, Google token
sign-in and the versioned password change. It retains at most **8,192 bytes** and
uses **one total 2-second receive deadline**. Declared oversize bodies are rejected
before receiving data; unknown-size streams stop at the first overflowing chunk.
JSON must be a UTF-8 object with only declared fields. Duplicate keys, nonfinite
numbers, malformed JSON and compressed/non-JSON inputs are rejected. Validation
and unexpected server failures produce a fixed envelope without serializing or
logging body values, validation errors, raw provider/SQL exceptions or headers.
Existing fixed authentication and rate-limit responses keep their behavior.
Unrelated auth/profile and other API routes keep their existing parsers. The API
schema documents the actual fixed error shape and generated frontend types follow it.
The ASGI server remains responsible for incoming HTTP framing/header/chunk limits;
the application budget does not claim to replace those server controls.

Legacy verification uses the maintained PyCA `bcrypt.checkpw` implementation without
entering Passlib's incompatible bcrypt backend probe. [PyCA's official documentation](https://github.com/pyca/bcrypt)
describes bcrypt 5's length rejection and prior truncation behavior. [Passlib's
raw bcrypt documentation](https://passlib.readthedocs.io/en/stable/lib/passlib.hash.bcrypt.html)
defines UTF-8 encoding and the historic 72-byte limit. Existing raw bcrypt keeps
that behavior, including a UTF-8 character crossing the byte boundary; NULL input
is refused. This is verification compatibility, and new PBKDF2 passwords continue
to use their entire value. The [official bcrypt-sha256 specification](https://passlib.readthedocs.io/en/stable/lib/passlib.hash.bcrypt_sha256.html)
defines version 1 SHA256 and version 2 salt-keyed HMAC-SHA256 followed by base64 and
bcrypt; both are verified against their existing format. Parsing is bounded to
4,096 password bytes, 1,024 hash bytes and bcrypt costs 4–16. Unsupported formats,
invalid input and costs above 16 fail closed; higher-cost historical accounts
require an explicit reviewed compatibility/reset policy rather than an unbounded
request. Verification never rewrites a mapped salted hash or independently retained
credential commitment. Official documentation was checked on October 9, 2026.

The final R2 author selection passed **177 tests with zero skips**, including actual
FastAPI/ASGI privacy and total-deadline probes, actual bcrypt short/wrong/Unicode/
72-byte/malformed cases, actual legacy HTTP login, genuine bcrypt SQL/native
enrollment and session issuance with unchanged credential commitment, and all prior
affected native/auth/actual PostgreSQL checks. Source hashes remained unchanged
through `/tmp/hirewiz-candidate-r2-author-final-20261009.xml`. The earlier affected
run's one OpenAPI test-introspection error is preserved; it did not indicate a
runtime/privacy failure. The exact final transport R2 prerequisites are external,
excluded from this patch and pinned in the R2 manifest. Independent re-review and
every production/joint-composition gate listed above remain open.
