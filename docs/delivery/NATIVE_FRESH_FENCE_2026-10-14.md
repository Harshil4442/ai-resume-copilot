# Fresh database release: native fence and critical cutover

This is an additive preparation for the existing billing/deployment work. It creates no new checkpoint and has executed no provider, SQL or customer-data operation. It does not replace the original in-place release denial. `NativeFreshFenceBoundary` is usable only by the fresh-target orchestration; its inherited in-place `migrate` entry point explicitly refuses.

## What the implementation observes

The operator first calls `bind_authorities_once(source)` before any retirement. The existing complete retained-config collector runs once, corroborating current native resource UIDs/configuration and actual Secret Manager sources. Only actual credential-producing automated principals and actual Google API-key resources enter the retirement scope. Selected-source DSN usernames come from actual retained values and must be in the SQL role registry, outside the trusted human-operator class. Historical databases on other hosts are neither redirected nor asserted retired. An unresolved relevant model source or unsupported issuer refuses specifically.

During this same pre-cut acquisition, the three genuine initial target `:1` payloads are accessed through the existing numeric Secret Manager Describe/Access/recheck collector. Their parsed passwords remain only in the boundary object's RAM. There is no saved password, credential fingerprint or user-supplied evidence flag.

`establish_retirement_cut(source)` rereads actual source SQL closure, exact retired API-key identities/deletion state, exact old service-account identities/disabled state, current application service templates and all routed/tagged revisions, paused empty task queues, paused schedules and completed job executions. It records the completion as the conservative cut and returns a time, not a Fence. A new complete observation is required for a Fence. Cloud Asset analysis and old audit JSON do not establish authority closure.

After actual isolated credential rotation, `bind_replacements_after_isolation(source, target)` requires each selected Secret Manager version and the replacement model key to have native issuance times at or after the cut. It additionally compares each selected target password against all three actual pre-cut passwords using constant-time comparisons. A new version containing any old role password refuses, including reuse across roles; every comparison must pass before any replacement authenticates. Every distinct replacement then authenticates once over the existing genuine verify-full Neon transport. Read-only catalog queries corroborate the actual control function, cluster, database/owner/actor OIDs and public namespace. The target must still contain no user relations/routines and no other sessions for the three target roles. This pre-restore step does not require an Alembic marker. All three authentications must succeed before the selected refs become bound; baseline passwords are then cleared from the object.

Repeated `verify_fences` calls perform bounded parallel fresh reads and preserve the oldest observation timestamp and 30-second window. They reread selected runtime/observer numeric version state, keys, disabled principals, source SQL and cloud admission. The migrator is authenticated only at replacement registration, because final serving intentionally disables its LOGIN and selected version. Existing migration/serving permission, exact-version and financial checks remain in the fresh orchestration. Drift invalidates the established cut rather than rejuvenating its timestamp. Authentication, key/source mapping and partial native inventories refuse with fixed nonsecret codes.

The stock SDK uses its retained normal credential manager; it can perform its normal refresh/authentication-challenge behavior. No token/config body is read, no API is enabled, and no custom authentication origin is allowed. Canonical refresh origin, account/project/universe, TLS, logging and proxy guards are checked. The module performs only native GETs and SQL SELECT/transaction-local settings. It neither disables identities nor deletes keys nor rotates passwords. A complete retained bind may exceed 30 seconds; it is outside the repeated fence. The repeated observation fails rather than relaxing the existing 30-second admission window.

This fence does not claim that a provider administrator has ceased to be an administrator. The expressly retained human control-plane route is trusted; captured old automated service accounts and the old application SQL owner are not granted that exception. Native service-account disable rejects existing tokens and new token issuance, while key deletion retires the bound keys. Cached pre-cut target passwords are made unusable by the separately approved actual rotation. Newly approved automated identities must be isolated from the retired identities. Existing job executions are observed, but no broad claim of worldwide historical credential retirement is made.

## Exact existing resources

Fresh target: organization `org-ancient-pond-42861142`, project `shiny-cell-70214683`, branch `br-square-mode-b4g6sxrz`, endpoint `ep-weathered-tooth-b43oa1n6`, direct host `ep-weathered-tooth-b43oa1n6.c-6.us-east-2.aws.neon.tech`, database `neondb` OID **16395**, owner/bootstrap **16391**, public namespace **2200** owned by **6171**, cluster SHA **61273750d6782a6bc688488b47d274ce81589d8066d54f389e7773a834e7ccd7**.

| Role | Native OID | Fixed secret name; initial version |
| --- | --- | --- |
| `hirewiz_runtime_20261013_r1` | 24576 | `hirewiz-fresh-runtime-db-20261013:1` |
| `hirewiz_migrator_20261013_r1` | 24578 | `hirewiz-fresh-migrator-db-20261013:1` |
| `hirewiz_observer_20261013_r1` | 24580 | `hirewiz-fresh-observer-db-20261013:1` |

Root's actual setup receipt `88cc06834b6f7d0f237020bb2fea83c2afd4c1f380e6b6cb64324ee8cabe305f` proves the additive setup and initial three role authentications; it is historical provenance, not proof that the initial passwords were never exposed. Replacement versions are explicit positive numeric selections established by the actual future rotation, not guessed `:2` identifiers.

Selected source remains project `little-pond-36610622`, branch `br-fancy-sunset-ao8i4a57`, endpoint `ep-damp-rice-aosknaqp`, direct host `ep-damp-rice-aosknaqp.c-2.ap-southeast-1.aws.neon.tech`, database OID **16391**, cluster SHA **cabc69e0dc6343b8474c3e4d7fc4ca48e8a93e45c1694d1961a93ee56e4385f0**. The captured old application owner `neondb_owner` OID **16387** remains a retirement subject. The existing prepared source observer `hirewiz_observer_20261010_r1` OID **1884182** still needs an actual least-privilege authenticated observer credential before final source checks can pass. The old source secret is `projects/157225724590/secrets/hirewiz-database-url/versions/1`.

Known actual old principal subjects, to be joined again by current native UID before any action:

| Principal | Actual native UID |
| --- | --- |
| `ais-gemini-key-43d738d3dfb9485@226979001779.iam.gserviceaccount.com` | 100592881791239522151 |
| `ais-gemini-key-41af2f49a3fb4b6@157225724590.iam.gserviceaccount.com` | 117611807420650799123 |
| `ais-gemini-key-7aeb7c91129947c@157225724590.iam.gserviceaccount.com` | 103651370799427077361 |
| `157225724590-compute@developer.gserviceaccount.com` | 115689643755778240830 |

Known earlier native key resource identities are the following. Current UID, etag, issuer and operation result must be reacquired for a concrete retirement action; this list itself authorizes no change:

- `projects/226979001779/locations/global/keys/3b5f42c3-94ae-4695-a476-804dee61f243`
- `projects/157225724590/locations/global/keys/122de5ba-8ec6-498f-9e97-777188d18fcf`
- `projects/157225724590/locations/global/keys/4c39725c-a68a-44c7-8f5f-2c5ddfaa9ee4`
- `projects/43171513202/locations/global/keys/1e59173c-014d-4ea6-a659-58c56d0a5507`

## Minimum critical approval package, after preparation

The final human approval must describe these concrete actions and their current native preconditions. No action in this list has been performed by this packet.

1. Verify current CI/image readiness, the existing snapshot exporter, backup destination and restore readiness for all customer/financial rows, artifact references and sequence state. The final admitted backup still follows the genuine native cut; no pre-cut backup is substituted for it. Establish the authenticated source observer and the explicitly approved operator path. Finish Builds using the old Compute service account before disabling it, or move those Builds to a separately approved isolated build identity.
2. Enter compatible admission-closed mode; pause/drain the four existing queues, schedules, generation/application jobs and old writer sessions. New checkout, optional generation and submissions stop for the final cut. Accepted payment settlement/webhook handling and payment credentials retain their existing reviewed recovery contract.
3. Disable the actual captured old automated/issuer service accounts by their exact current identity. Delete the actual bound old model keys with current UID/etag and verified native outcomes. Any unsupported model issuer remains an explicit unresolved prerequisite until its own genuine retirement mechanism is prepared. No unrelated project or historical database is deleted.
4. Retire the selected source's application SQL owner and all captured application writer paths: LOGIN, inherited/SET/ADMIN privileges, effective ownership/write/elevation and existing sessions. Preserve an independently approved operator/observer path and every source row. The actual ownership-transfer/role SQL and its postconditions require separate review; the old application owner cannot be relabeled as a human exception.
5. Establish the native retirement cut; rotate all three fresh-target passwords, create/verify selected replacement numeric secret versions, disable old target versions and drain pre-rotation target sessions. Issue the approved replacement model credential/secret only after old identity isolation. Capture of actual initial passwords occurs before disabling initial versions. Register and authenticate the distinct replacements using this implementation.
6. Run the original genuine native fence and owned consistent source-to-empty-target transfer once, preserve the entire source schema9 dataset, migrate the target through schema16, and verify row content/financial continuity/artifact references/sequences and runtime privileges. An unknown write or transfer outcome requires reconciliation; it is not automatically replayed.
7. Retire target migrator LOGIN/schema CREATE/sessions and disable its exact selected numeric version. Stage with zero traffic, verify the existing serving/financial gates and portable runtime, then separately approve final production routing/restarts in GCP and Vercel and verify actual customer flows.

Expected impact is a temporary final-cut pause of writes, checkout, generation and applications. A reliable downtime duration requires the actual source dataset size and measured transfer/runtime readiness; this preparation does not invent one. Search/read behavior during the cut must follow the verified compatibility deployment, not an assumed feature toggle.

Rollback preserves the source and backup. Before new-target writes, use only verified financially consistent routing. After accepted writes on the target, do not route back to a stale source: stop new admissions and reconcile or repair forward. Never reset/drop the target, discard records, automatically replay an uncertain restore, reenable retired service accounts/keys/passwords, or revert payment credentials as a generic rollback. Normal human provider management remains available. `bulk_action_platform` / `withered-breeze-01964779` and historical databases are outside this action.

## Verification and remaining acceptance

Synthetic tests exercise actual composition code, copied-password rejection, exact replacement native identity/empty/session checks, one-time authentication versus later migrator retirement, oldest timestamp, provider/SQL drift, partial admission lists and legacy in-place refusal. They are not native provider acceptance. The current source/test packet passes configured Ruff, Linux mypy and isolated tests. Native retirement, source observer activation, actual post-isolation rotation, full data transfer, load/billing acceptance and final deployment remain pending in the original checkpoints.

Primary provider semantics: [Google service-account disable](https://docs.cloud.google.com/iam/docs/reference/rest/v1/projects.serviceAccounts/disable) rejects existing access tokens and new token requests; reenabling restores acceptance of old tokens. [Google API key lifecycle](https://docs.cloud.google.com/api-keys/docs/reference/rest/v2/projects.locations.keys) provides native key UID/deletion fields and a separate undelete operation. [Cloud Asset IAM analysis](https://docs.cloud.google.com/policy-intelligence/docs/analyze-iam-policies) is best effort and can omit recent updates, so it is not used as the admission certificate. Existing native Neon transport and Secret Manager checksum/metadata guards are reused unchanged.
