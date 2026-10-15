# Candidate account production composition — 2026-10-15

`backend/app/domains/candidate_accounts/production.py` composes the existing credential lifecycle with real native SDK ports. The accompanying normal-user increment adds explicit enrollment and distinct Google sessions through this composition; its public journey is described in `NORMAL_CANDIDATE_BROWSER_ACTIVATION_2026-10-15.md`. No SQL row is silently adopted and no provider resource was created.

## Exact deployment contract

The factory remains closed unless `CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED=true`, `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true`, and `CANDIDATE_ACCOUNT_NATIVE_CONFIG` contains strict JSON no larger than 16 KiB. Duplicate keys and unknown fields are rejected. The configuration has exactly four fields:

| Field | Required value |
| --- | --- |
| `execution_witness_pin` | Existing `WitnessPin`, including the actual execution database resource/immutable UID, authority/incarnation/epoch, Storage bucket/path/generation and canonical body hash. |
| `execution_witness_body` | Existing `WitnessBody`, with the same exact registry, OPEN state and actual manifest/partition evidence. Its canonical hash must equal the witness pin. |
| `publication_resource` | Existing full `PartitionedResource`, including the independently pinned protected registry, same execution target, journal bucket, exact resource-object generation/hash and lane/directory configuration. |
| `epoch_generation` | Positive integer matching the existing native pairing/lifetime operator control. |

If `BROWSER_SUBMIT_NATIVE_CONFIG` is present, these four fields must exactly match that configuration. The submit enabled flag is not required to activate account lifecycle; mismatched configured resources are rejected even when submit is disabled. Candidate account and submit authority therefore cannot silently enroll against different protected owners or epochs.

`production_candidate_accounts()` validates configuration only. It acquires no SQL connection, constructs no SDK clients, resolves no ambient credentials and performs no provider I/O. Structural availability is not a provider-health or successful-enrollment claim. SDK clients are created on the first lifecycle operation or trusted composition-property use; callers must resolve those ports before entering their own SQL transaction.

## Reused concrete composition

The builder uses `app.database.engine`. If it already hides parameters, it is reused. Otherwise a `hide_parameters=True` Engine shares its exact existing DBAPI pool, preserving configured SSL, connect arguments, credentials, timeouts, connection creation and pool limits; it does not reconstruct a weaker connection policy from a candidate-supplied URL. The wrapper engine does not acquire a connection during construction or mutate the configured source engine.

Actual `FirestoreClient`, `FirestoreAdminClient` and Storage `Client` feed explicit execution/protected `FirestoreRpc` instances. The execution instance is fenced by `GcsWitnessFence`. The protected instance feeds `PartitionedPublicationCoordinator`; `GcsPasswordLifetimeJournal` retains each full original intent there before its create-only Storage write. The composed lifecycle is:

`GcpPasswordLifetimeCoordinator` → `NativeCandidateLifetimeRetention` → existing `GcpProtectedCandidateLifetimes` → `ScopedCandidateAuthority`.

`GcpPasswordCredentialProjection` is injected into that existing protected adapter for command-bound password changes and original protected completion. `GcpCandidateCookieLogout` supplies denial-only logout/cookie recovery. These adapters already existed; the earlier read-only gap assessment had overlooked `gcp_candidate_lifetimes.py`. No replacement retention adapter was needed.

The configured wrapper exposes the existing registration, registration-status, login, validate, logout, cookie-logout, password-change and prepare-delete methods, plus trusted server-side `sessions` and `retention` ports for separately implemented enrollment orchestration. Every public lifecycle method revalidates its original deployment configuration and execution witness before/after the service call. Existing scoped operations independently refresh protected identity/resource/closure state. No provider SDK I/O is placed inside a candidate SQL transaction.

Fresh registration still follows the existing service: commit a new credential/account as PENDING; require genuine original native enrollment and original protected activation acknowledgements; only then mark the same exact SQL account ACTIVE. Login re-verifies current SQL credentials and native ownership/generation, creates and activates an original retained session, and returns its real context. An existing SQL row cannot bootstrap native identity at login. Unknown enrollment/activation remains pending; a status read or repeated registration cannot adopt an acknowledgement.

Password changes retain the protected denial/high-water before command-bound native credential projection and protected completion, then update the exact SQL credential revision. Lost native/protected acknowledgements deny old scopes while withholding the SQL password update. Logout and account deletion use retained native denials. Cookie logout may observe only an exact already-retained denial fact, including its deletion-cookie path; this cannot create action authority or a session.

## Distinct Google sessions

`CreateVerifiedOAuthWebSession` creates an explicitly Google-authenticated native session from the server-verified stable Google issuer/audience/subject principal and the SHA-256 of the fresh signed identity token. The native principal must equal the original enrollment principal. An immutable proof-digest claim is committed atomically with the session, so replaying a token after SQL challenge rollback cannot mint another session. Genuine original native and protected activation acknowledgements remain required. No raw identity token is retained.

`CandidateWebSession` identifies Google sessions with `authentication_method="google"`; old password contexts retain their exact three-key canonical serialization. Native/protected retained records and census validation preserve each method and proof digest through reset, logout and deletion. Google sign-in after explicit linking is supported through this distinct command. Google verification is not a password proof: sensitive action approval still requires its separate current-password challenge.

## Resource and rollout obligations

The runtime creates no native baseline. Root/operator provisioning must supply the existing execution OPEN control, exact pairing control/epoch generation, monotonic clock/event head, independent protected OPEN root, lanes/directories, pinned resource and witness objects, create-only journal access, closure protections and appropriate IAM/retention/restore discipline. Missing/corrupt/stale resources fail closed. The exact resource evidence and credentials remain external deployment inputs; no GCP or Neon calls were used to implement or validate this increment.

This factory closes the account-lifecycle composition gap. The separately implemented explicit Google-owner linking flow uses this same service. Native public pairing, protected signing/dispatch and the original-completion-to-submit-device bridge are now separately composed in `BROWSER_PAIRING_PRODUCTION_COMPOSITION_2026-10-15.md`. The old operator submit-device grant is not a normal-user enrollment path. Do not mark the complete normal-user submit journey ready solely because this factory is configured.

## Focused evidence

`backend/tests/test_candidate_production_factory.py` passed 29 tests using actual Firestore GAPIC/Admin and Storage SDK request construction over intercepted local transports and a private synthetic SQLite database. Only operator baselines were seeded: no candidate subject, credential binding, session or protected account was pre-created. Tests establish fresh native/protected enrollment and session provenance, ordinary/cookie logout, real reset/protected completion, deletion, unknown enrollment/activation/reset/denial outcomes with both persisted and unpersisted commits, fresh execution/protected UID/resource/closure rejection, strict lazy configuration, shared submit pin matching and SQL-pool parameter hiding. OAuth cases additionally cover distinct native/protected provenance, immutable proof replay rejection, mismatched principal/method rejection, current-password requirements, reset invalidation, Google logout/deletion, lost native/protected acknowledgements, and unchanged legacy password serialization. Event assertions reject SDK I/O during SQL/native transactions, and explicit GAPIC retries/timeouts are checked. These tests establish source composition and local transport behavior, not production IAM, GCP durability, public Google authentication, device pairing or rollout availability.
