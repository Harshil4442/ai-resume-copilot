# Native final-submit authority — 2026-10-15

The public composition boundary is `backend/app/domains/recovery/browser_submit.py`. It creates a distinct, exact `submit` authority; legacy `Binding.action = fill` and `ScopedBegin` remain unchanged. It does not authenticate an employer application receipt or authorize credit settlement. No production resources were created or called for this increment.

## Production inputs owned by the operator

`production_submit_authority()` is structurally closed unless all of these deployment variables match. Construction parses configuration only, without importing or constructing SDK clients, resolving credentials, reading provider state, or provisioning a subject/device.

| Variable | Required value |
| --- | --- |
| `BROWSER_SUBMIT_NATIVE_ENABLED` | Exactly `true`; leave absent/false until actual resources and owner/device provenance are provisioned. |
| `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA` | Exactly `true`, retaining the existing bounded Storage request policy. |
| `BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION` | Exact same identifier as the native config, operator grant, and SQL submit binding. It uses `[A-Za-z0-9_-]`, at most 160 characters, for example `companion_0_6_0`. |
| `BROWSER_SUBMIT_NATIVE_CONFIG` | Strict JSON, at most 16,384 UTF-8 bytes, with the six fields below; unknown fields are rejected. |

| Configuration field | Exact required resource/data |
| --- | --- |
| `execution_witness_pin` | `WitnessPin`: `registry`, `bucket`, `path`, `generation`, `sha256`. The registry includes actual named Firestore database resource, immutable database UID, authority UUID, incarnation and epoch UUID. |
| `execution_witness_body` | `WitnessBody`: version 1, the same execution registry, `state: OPEN`, actual manifest generation/hash and partitions hash. Canonical body SHA-256 must equal the witness pin. |
| `publication_resource` | `PartitionedResource`: full `PartitionedPin`, resource object bucket/generation/hash. Its authority pins a separate protected Firestore registry, the exact execution target, and the journal bucket. Full native root, lanes and directory must already be operator initialized. |
| `device_grant_pin` | `SubmitGrantPin`: bucket, `authority-submit-device-grants/<device UUID>.json`, actual immutable generation and canonical grant SHA-256. |
| `epoch_generation` | Positive integer matching the separately protected device grant. |
| `executor_revision` | Same exact identifier as the submit executor variable. |

Database resources must pass the existing named native Firestore resource constraints. The publication registry and execution target must be independent under `PublicationPin`. Resource, witness and grant generations/hashes refer to actual provider metadata and canonical bytes, not fixture constants. The execution closure object and independent publication emergency-closure object must both be absent; any object at either closure path denies admission. No SQL row or cached epoch can replace these fresh checks.

Runtime credentials need legitimate access to the existing native Firestore RPC/metadata resources and Storage witness/resource/grant reads and create-only journal writes. The application runtime must not gain operator permission to replace deployment pins, forge enrollment grants, reopen a closed authority, or roll back the protected registry. Exact IAM, retention, restore discipline and real resource provenance remain root-owned provisioning obligations; passing intercepted tests does not establish them.

## Exact protected candidate and device enrollment

`SubmitContext` carries only the server-resolved expected credential revision: `subject_uuid`, `account_binding_id`, `candidate_id`, `principal_sha256`, `auth_generation`, and `credential_sha256`. The caller copies that revision under its SQL owner lock, then releases SQL locks before SDK I/O. A SQL password-account record alone is insufficient. `current_subject()` must find the original retained native password enrollment/activation, exact candidate/account owners, continuous current generation history and no subject/account/pending denial. This port does not manufacture or require an invented web session from a legacy Google bearer.

The operator creates canonical `SubmitDeviceGrant` bytes in its pinned Storage object. Required fields are version 1, kind `submit_device_enrollment`, exact execution `pin`, `epoch_generation`, full `context`, device UUID, strictly public P-256 JWK, JWK fingerprint, positive key generation, executor revision and `enrollment_provenance_sha256`. The operator must independently establish owner/device/public-key possession and retain the evidence committed by that provenance digest. A hash alone does not perform that verification. Copying a SQL device, accepting a browser declaration or supplying an arbitrary digest is not enrollment.

After those legitimate checks and resource provisioning, the operator explicitly calls `production_submit_authority().provision_device()` outside SQL locks. This reads fresh pinned grant bytes, checks the current native subject, publishes the full enrollment intent, verifies the journal, and durably creates immutable `v3_submit_devices` and globally keyed `v3_submit_key_owners` records. Normal factory/reserve paths never call it. Existing/denied device or key ownership cannot be adopted. Unknown enrollment commit yields no acknowledgement; investigate retained native evidence rather than repeating enrollment automatically. There is no new browser/HTTP provisioning endpoint or automatic initializer.

The grant binds one exact credential revision and execution epoch. A password/generation change, subject denial, native device tombstone, grant replacement, database UID change, closed witness or closed publication root denies use. This increment reads native device tombstones but exposes no generic revocation writer; emergency closure and operator grant-generation invalidation remain available through existing protected operational controls. A new revision/device needs deliberate, independently verified provisioning.

## SQL approval and one-use native export composition

`SubmitBinding` binds candidate, device/key/generation/executor, review UUID and digest, application, exact supported HTTPS employer opening, employer/tenant/opening identity, artifact ID/hash/generation, package/policy/pricing hashes, admission ID/digest, upload evidence hash, document/context, approval time, original review expiry and a maximum ten-second begin deadline. The native port trusts the application service to have verified the candidate's exact review approval and admission under SQL locks; the values are never accepted directly as native browser authority.

1. The service durably consumes its one SQL submit attempt and records the pending request before calling the native port without SQL locks.
2. On one factory instance, `ack = gate.reserve(context, binding)` reads the fresh execution witness and independent protected registry, validates current native owner/device, then atomically retains the full submit intent and an opening reservation before journal I/O. The reservation key excludes review, device, package, operation and epoch, preventing those changes from evading a retained attempt. The same exact owner/device state is rechecked before a durable native BEGIN.
3. Only a confirmed original invocation returns the opaque, process-owned ACK. Status rows, retained plans, copied ACKs, process restarts and unknown commits cannot reconstruct it. A persisted opening reservation blocks a fresh native operation. If an initial uncertain commit persisted nothing, the caller's prior durable SQL attempt still blocks automatic retry.
4. `gate.consume_ack(ack, binding)` burns original in-memory ACK ownership before I/O, verifies the original journal and exact protected context again, and durably consumes a single native export. It returns minimal binding/intent/pin/journal proof metadata. Unknown export commit or deadline expiry yields no proof and no re-export.
5. The service reacquires its SQL owner/device/review/application/admission locks and verifies current source flags, cancellation, credential revision, artifact, deadline and lease-export state before committing a single lease export ahead of returning bytes. This native proof must never replace those SQL checks.

Proof metadata explicitly contains `employer_receipt: false` and `settlement_authorized: false`. Lost POST outcomes require reconciliation. Generic HTTP success, an editable confirmation page, a device signature or a native begin proof must not convert a held admission into a verified employer receipt.

## Local evidence and limits

`backend/tests/test_browser_submit_native.py` uses actual Firestore GAPIC/Admin and Storage SDK request construction over fully intercepted local transports, including the production factory. The retained subject provenance and deployment pins are explicitly synthetic. Tests cover original one-use export, clone/restart rejection, independent UID/epoch and native device/owner denial, publication/journal/BEGIN/export uncertainty, revocation between stages and transactional conflict retry, strict default-closed construction and unchanged fill binding. They assert provider I/O is outside native transactions and that explicit RPC calls disable SDK retries with two-second timeouts. They do not prove production IAM, retention, real candidate enrollment, real GCP durability, or employer application acceptance.

Validation: 24 new native tests and 66 existing RPC tests passed together (90 total); Mypy passed all 42 recovery source files; focused Ruff and whitespace checks passed. Existing journal/scoped/publication/export suites require the root-owned Firestore emulator at `127.0.0.1:58877`; it was unavailable, so setup failed and those runs were stopped. No alternative provider endpoint was contacted and no production validation is claimed.

Three additional tests in `backend/tests/test_browser_submit_postgres.py` passed against the explicitly authorized disposable Postgres database at `127.0.0.1:55433`, using a unique per-test schema and the full synthetic ORM/crypto/credit fixture. Competing approvals reserve once; competing begins create one native invocation/attempt/lease export; a separate cancellation commits while synthetic native I/O is paused, and the final SQL recheck prevents export while retaining the unknown-outcome hold. Native authority in these Postgres tests is deliberately synthetic. The service/SQL suite passed 29 tests, including eight rejected cross-document/destination/missing selection-provenance cases. The candidate review UI suite passed 109 tests, including strict upload continuity, separate 20-credit approval and erased/independently reconciled terminal responses.

## Normal-user source composition and deployment boundary

The operator grant remains a restricted provisioning port. It is not the public enrollment path. The normal-user source now uses genuine account enrollment, a distinct retained Google session after explicit linking, native candidate/device pairing proofs, and an original opaque completion handoff into protected device activation.

| Current source | Normal-user behavior |
| --- | --- |
| `candidate_accounts/production.py` and the bottom service factory delegate | Strict lazy composition with the trusted private SQL pool, actual native lifetime coordinator, fresh witness, protected publication/activation, command-bound reset projection and cookie logout. Fresh registration follows PENDING → original native ACK → original protected activation → ACTIVE. |
| `candidate_accounts/enrollment.py`, `google.py`, service/auth/BFF enrollment routes | Existing Google users explicitly confirm enrollment using a nonce-bound fresh verified Google identity for the same stable principal, consent and a user-chosen HireWiz password. Current SQL owner/email/credential and native owner revisions are retained; ordinary login does not adopt a legacy SQL identity. |
| Distinct native `CreateVerifiedOAuthWebSession` and protected activation | Google sign-in remains available after linking. It binds the original enrolled Google principal and a fresh signed-token digest, with an immutable native replay claim. Sensitive action approval requires its separate current-password proof. Old password contexts retain their exact canonical serialization. |
| `browser_pairing_production.py`, `gcp_password.py`, exact KMS issuers | A normal candidate uses the existing BFF/CSRF/gateway, current retained session, separate password confirmation/protected signing BEGIN, and P-256 device proof. Factories remain closed until exact runtime pins and native resources are supplied. |
| `gcp_paired_devices.py` and `browser_assistance/native_pairing.py` | Only the original winning native completion ACK can activate the protected device/key and then its exact SQL projection before claim export. Native status, a browser claim, SQL insertion or a restored JSON receipt cannot adopt that acknowledgement. Unknown revocation retains SQL/protected denial before native completion. |
| Explicit submit `device_enrollment: "paired"` | Shared deployment resources and current protected paired registration replace the per-device operator grant. Operator mode remains separately available with its old exact grant requirements. |

`CANDIDATE_ACCOUNT_PRODUCTION_FACTORY_2026-10-15.md`, `BROWSER_PAIRING_PRODUCTION_COMPOSITION_2026-10-15.md`, and `CANDIDATE_INGRESS_NATIVE_COMPOSITION_2026-10-15.md` list the concrete source composition, strict configuration and least-privilege resource inputs. The pairing suite uses real SDK request paths over intercepted local transports; its normal sequence creates all candidate/session/device authority through genuine ACKs and then exercises paired native submit admission. No per-user/per-device native seed or actual provider call is used.

Actual resource identities, IAM, immutable witness/journal generations, KMS public-key configuration in the installed companion, protected baseline provisioning and public rollout remain external deployment obligations. Current defaults remain closed. The production bridge handles connected-device revocation; optional cancellation before device completion remains closed. Employer receipt authentication is a separate obligation: native begin/claim/submit admission never establishes employer receipt or credit settlement.
