# Normal candidate browser pairing composition — 2026-10-15

The production browser-pairing factory now composes real account, native pairing, protected signing, KMS and protected device ports. A normal freshly registered or explicitly Google-enrolled candidate can pair a generated P-256 device with a separate current-password approval. Winning native completion activates the exact independently protected device and its SQL browser-assistance projection before returning its signed identity claim. No candidate or device row is pre-seeded, inferred from SQL, or enrolled from a browser claim/status response.

## Exact runtime inputs

All switches remain closed by default. Structural factory construction performs no ADC resolution, SQL connection acquisition, provider call or native initialization.

Set the existing account composition inputs from `CANDIDATE_ACCOUNT_PRODUCTION_FACTORY_2026-10-15.md`, then supply:

| Input | Exact meaning |
| --- | --- |
| `BROWSER_PAIRING_NATIVE_ENABLED=true` | Explicitly enables the configured factory. |
| `BROWSER_PAIRING_NATIVE_CONFIG` | Strict JSON, at most 16 KiB; duplicate/unknown fields rejected. Its four account fields (`execution_witness_pin`, `execution_witness_body`, `publication_resource`, `epoch_generation`) must exactly match `CANDIDATE_ACCOUNT_NATIVE_CONFIG`. |
| `extension_id` inside pairing config | Exact shipping Chrome extension ID, currently `pnjmegppacleipajnifjmbcopjjffklb`; 32 a–p characters. |
| `executor_revision` inside pairing config | Exact reviewed release identifier. Native prepare policy, emitted claim, installed companion configuration and submit executor pin must agree. |
| `assertion_issuer` inside pairing config | Fixed trusted native recent-password assertion issuer. |
| `assertion_key`, `claim_key` inside pairing config | Each has exactly `version_name`, `endpoint`, `protection_level`, `public_key`: a concrete Cloud KMS version path, its official global/regional TLS endpoint, SOFTWARE or HSM, and canonical public P-256 JWK. No latest-version alias or local signing key. |
| `BROWSER_PAIRING_GATEWAY_SECRET` | Same dedicated 64 lowercase hex characters used by the BFF. Never an identity provider or signing-key substitute. |
| `BROWSER_PAIRING_WEBSITE_ORIGIN` | Same exact HTTPS website origin used by the BFF. Gateway issuer remains `hirewiz_bff_v1`. |

A configured wrapper freezes the full native deployment plus gateway-key digest/origin and rejects their later changes. It revalidates before and after calls. The KMS public-key pin verifies the actual returned version, algorithm, key bytes and protection level; response CRCs and signatures are checked with retries disabled and bounded native RPC timeouts.

`RefreshingKmsSigner` uses the real trusted ADC credential boundary with explicit Cloud KMS scope. Refresh calls have a two-second request timeout. A valid token and concrete remaining expiry of at most one hour are required before creating the existing exact-version KMS boundary. There is no static token environment fallback, local signer, or automatic native resource initialization. Credential refresh and KMS calls occur outside SQL and native authority transactions.

## Resources and least privilege

Root provisioning must provide actual resource identities, IAM and immutable generations after the separately controlled F7 gate. The source factory creates none of these:

1. Execution Firestore database with actual immutable UID, authority/incarnation/epoch, OPEN control, exact `pairing_control/current` epoch generation, monotonic `pairing_clock/observed`, event head, and `pairing_extensions/<extension_id>:<executor_revision>` containing exactly extension ID, executor revision, protocol 2 and `active: true`.
2. Independently protected Firestore database with the exact protected resource pin, OPEN partitioned root, lanes and directory shards, plus pinned immutable resource-object bytes/generation/hash. It must share the exact execution target with account and submit authority.
3. Exact-generation OPEN execution witness, manifest/partition evidence and journal bucket. The runtime needs bounded reads and create-only journal/attempt objects, with no overwrite/delete/reopen permission. Retention, closure and restore policies remain operator obligations.
4. Concrete KMS signing versions and narrow GetPublicKey/AsymmetricSign permissions. The companion must pin the public claim key as its `authorityKey`; publishing the corresponding reviewed extension package is a separate required deployment step.
5. Existing trusted SQL engine/pool with parameter hiding. Pairing shares the account service's configured DBAPI pool, preserving its SSL, connection arguments, authentication and timeout policy. Credentials are reread through the existing password verifier and SQL is closed before SDK I/O.
6. Public candidate ingress/BFF secrets, body authentication and distinct replay authority described in `CANDIDATE_INGRESS_NATIVE_COMPOSITION_2026-10-15.md`. Pairing gateway MACs select a retained session; they do not prove a password or establish native account/device ownership.

For normal submit-device use, configure `BROWSER_SUBMIT_NATIVE_CONFIG` with the same four shared fields, exact `executor_revision`, and explicit `device_enrollment: "paired"`. Omit `device_grant_pin` in this mode. The old operator mode remains the default and still requires its exact manually pinned grant. The modes cannot adopt each other's registration type. Configuring paired mode never calls the operator provisioning port or reads an operator device grant.

## Original acknowledgement and denial sequence

`GcpPasswordCandidateBoundary` resolves the current protected/native account and session, verifies the current SQL password, and atomically consumes a protected `ScopedBegin` signing key for that exact native challenge/assertion/session. The existing protected Storage signing marker and original execution commit must then acknowledge before KMS signs. Dispatch rechecks that same original opaque protected acknowledgement after KMS and around native consumption. An existing/lost signing BEGIN or marker is not replay authority.

`GcpPairingCoordinator` issues process-owned `PairingOwnedAck` only for its original winning completion/revocation result after native commit and fresh fences. `take_owned_ack()` requires the exact original result object, and `consume_owned_ack()` burns that original ownership before further I/O. Reconstructed JSON, a signed device claim, public status, restart or a cloned acknowledgement cannot obtain the handoff.

`ProtectedPairingDevices.activate()` consumes that acknowledgement, retains the full native intent, journal receipt, invocation claim, projection, exact public device/key and candidate confirmation, and checks the original protected password signing BEGIN. Its new protected activation writes the device/key owners and native-completion consumption in one transaction with current subject/session/generation checks. A lost protected activation acknowledgement withholds output even if its rows persisted. There is no status-based adoption or automatic reactivation.

Only after definite protected activation and another current-scope check does the trusted SQL `activation_port(engine).activated(grant, registration, extension_id)` run. It is injected by production composition; HTTP callers supply none of these ports. SQL stores the existing plain canonical JWK digest; native device authority stores the domain-separated `key_fingerprint`. Final-submit bindings derive the latter from the exact verified public key. SQL failure withholds claim output and does not recreate the consumed native acknowledgement.

Connected-device revocation requires another explicit current-password approval. After verifying its exact original protected signing BEGIN and device ownership, the SQL denial callback runs first; then protected device denial is retained before KMS/native revocation. Unknown protected/native revocation cannot leave a SQL-active fill device. A winning original native revocation acknowledgement retains full completion evidence against that protected denial. Claims and paired submit admission refresh protected ownership/denial; SQL or signed identity alone is insufficient.

The production bridge currently handles revocation of a fully activated device. Cancellation of a candidate-confirmed request before device completion remains closed; the older core's optional pending-request cancellation is not treated as protected device activation. Uncompleted requests can expire and cannot act or submit.

## Evidence and limits

`backend/tests/test_browser_pairing_production.py` uses the actual Firestore GAPIC/Admin, Storage and KMS protobuf/request paths over fully intercepted local transports, with a fresh private SQLite database. Only synthetic operator resource baselines and the exact extension/revision policy are seeded. Genuine account enrollment/session issuance and both candidate/device proofs create all user/device authority.

The focused cases cover structural laziness/shared-pin mismatch, current-password rejection without signing, original protected BEGIN/ACK loss, native completion and protected activation acknowledgement loss, SQL activation failure, gateway pin rotation, independent protected UID change, denial during KMS, full paired activation/census, SQL projection and digest representation, paired-mode real submit reserve/one-use proof, and SQL/protected denial across unknown native revocations. All provider and credential calls are intercepted; no GCP, Neon, employer portal or real submit operation is performed.

An earlier local regression run passed 291 tests with three optional PostgreSQL driver skips. The later owned PostgreSQL 17 and Firestore emulator run passed 117 account/scoped/native lifecycle tests with explicit local URLs. Mypy subsequently passed all 133 configured domain/router source files. The current pairing file contains 29 cases, including a genuine distinct Google native session followed by separate current-password pairing approval. No replacement cloud resource was contacted.

These checks prove source composition and local transport behavior. Actual runtime identity/IAM/retention/restore behavior and installed-extension end-to-end activation require separately authorized resource provisioning and package configuration. Native submit admission is not employer receipt: receipt and credit settlement remain false until the independent employer-receipt contract is satisfied.

## Installed MV3 and independent assistance precheck

`backend/tests/browser_paired_assistance_chrome_bridge.py` and `browser-companion/tests/paired-assistance-browser.test.js` passed three installed-Chromium tests. The shipping extension identity uses an actual non-extractable IndexedDB key and original Chrome P-256 signatures through the intercepted native production factory. Genuine candidate enrollment/session and separate synthetic BFF password approval precede native/protected device completion and SQL active projection. Assistance uses the native-assigned device UUID with that same Chrome key. Restart requires a fresh verified native claim. Wrong password enables neither a device nor application UI; lost native completion stays unknown and cannot be adopted from a COMPLETED status or replayed after restart. Every HTTPS request is locally fulfilled with the actual Chrome extension Origin; fixed test clocks and exact manifest host grants are test-only. No provider or employer call occurs.

The paired production cohort must additionally set `BROWSER_ASSISTANCE_NATIVE_PAIRING_ENABLED=true`. Each signed assistance request then calls the configured `assistance_context(device_id, public_key)` port after SQL signature/context reads are rolled back and before fresh SQL owner/credential rechecks. This port returns a strict `SubmitContext` only after independently refreshing protected full activation/key/completion/signing provenance, current credential subject and denial state, then the actual execution witness/UID/control/pairing-generation/current device/key owner/extension policy/revocation, and a second protected current check. A SQL-active restored device, cached claim or status row cannot replace these checks. The flag remains false by default; legacy SQL prepare/confirmation must not provide an alternate enrollment path in this cohort.

Focused actual-SDK cases reject independent device denial, epoch generation change, execution closure, wrong public key and missing protected activation. They exercise SQL rollback before native I/O; the shared intercepted fixture asserts no SDK calls occur while SQL/native transactions are open. The combined pairing suite currently passes 29 tests, including three paired-submit execution-control cases added by the integration owner.
