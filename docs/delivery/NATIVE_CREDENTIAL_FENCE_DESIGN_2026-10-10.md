# Native credential retirement for the monetary rollout

Status: a bounded design and standalone read-only Google observer, based on
`ebb25c003e76762cffc8386b52ab8715a489d816`. No key rotation, provider generation,
database mutation, cloud deployment or monetary activation is performed here.
The existing global fence refusal in `infra/gcp/monetary_release.py` stays intact.

## Decision: credential coverage can protect unknown holders

Complete **credential authority** coverage plus authoritative revocation can deny
unknown holders of old credentials without finding every holder by name. For example,
a genuinely deleted provider key cannot be used by an unlisted laptop either. An old
database role with no login, no active sessions and no reachable write or grant authority
cannot write merely because its password remains on an unknown device.

This is sufficient only when all old authentication paths are covered, existing
sessions and requests are handled, replacement credentials are isolated, and old
holders cannot restore credentials or obtain replacement authority. Revoking one
known key or rotating one password does not establish that coverage. Inventory
completeness must come from authenticated, paginated resource and privilege
observations, not a supplied `complete`, `revoked` or `unknown_consumer_count: 0` flag.

Consumer names remain useful for service continuity: shared credential retirement
could interrupt another legitimate application. The owner needs to identify known
sharing and choose a replacement plan before that destructive action. This information
does not replace provider or database observations. No additional human-only policy
approval is imposed by this design; conservative expense estimates are already authorized.

## Latest available production observations

The private native report observed at `2026-10-09T20:26:55.710819+00:00` binds four
Google model keys to resources across three projects. All four were active, with no
`deleteTime`; three other historical provider aliases remain unresolved. This report
does not establish all-key inventory, issuer isolation or provider revocation.

The nearby runtime observation at `20:25:56.377171+00:00` shows the checkout-contained
legacy API at 100% traffic, legacy analysis revision `00021-5zd` at 100%, and employer
worker `00012-lmz`. The generation-disabled analysis candidate has a tag but no percentage
traffic. Disabling checkout does not quiesce AI generation. A revision template is not
proof of the configuration actually receiving traffic.

The last database aggregate, at `2026-10-09T18:37:26.812533+00:00`, showed schema `0009`,
an elevated legacy role, zero other sessions at that instant, one created payment order,
39 terminal analysis runs and zero employer applications. It did not prove provider
requests finished, external writers absent, or historic financial costs reconciled.
These are dated observations, not current permissions for a cutover.

## Exact authority boundaries

| Boundary | Native observations needed | What cannot prove it |
| --- | --- | --- |
| Legacy database authentication | Independently pinned cluster/database identity; all production endpoints and authentication methods; every old login role; committed NOLOGIN/revocation; fresh effective privilege/session observations | New secret version, zero Cloud Run traffic, one zero-session sample, password change alone |
| Database write and regrant authority | Existing whole-database observer for table/column/schema/sequence writes, elevated roles, ADMIN/SET/inheritance, executable SECURITY DEFINER routines, prepared work; no legacy role relabeled as operator | Role names, caller-reviewed consumer list, READ ONLY default, a harmless-looking function name |
| Database control plane | Authenticated provider project/branch/endpoint membership and applicable API-key/principal permissions; no old app can reveal/reset passwords or administer the production branch | Only querying application SQL roles, assuming every branch or admin credential is in GCP |
| Google model keys | In-memory key-to-resource native lookup; every page of `showDeleted=true` parent lists; fresh get identity/deleteTime/etag agreement; retirement operation completion when executed | Secret Manager DISABLED, key removed from latest template, 403/404 alone, a file claiming deletion |
| Model issuer and replacement access | Exact key-owning projects, auth-key service-account bindings, inherited/conditional/custom-role permissions and replacement-secret access; old principals cannot restore keys, mint usable model authority or impersonate new identities | Four deleted keys while an old app can read a replacement key or undelete one |
| Legacy cloud writers | Actual serving and tagged revisions, jobs/executions, scheduler/task admission and old identity access; queues paused/drained; generation-disabled replacements retain required settlements | Latest revision health, empty RUNNING queues, generation off only on a staged candidate |
| Financial continuity | Original merchant/mode, accepted order key identity, callback verification and current/previous webhook secrets preserved; replacement runtime can settle capture/refund exactly once | Rotating all “provider” keys together, blocking payment callbacks, deleting accepted orders |
| In-flight liabilities | Existing attempts/holds and provider evidence reconciled or conservatively retained under the existing gate; no unknown result replay | Client timeout, elapsed arbitrary grace period, terminal application label alone |

The database observer already implements the SQL authority checks; this work does not
duplicate them or weaken a current refusal. PostgreSQL LOGIN controls initial login,
and role administration can reopen authority. Session termination also needs actual
completion: a zero-timeout `pg_terminate_backend` success only confirms signal delivery.
[PostgreSQL role attributes](https://www.postgresql.org/docs/current/role-attributes.html),
[server signalling](https://www.postgresql.org/docs/current/functions-admin.html).

The requirements record Neon as the existing database provider. That historical
record still needs authenticated binding to the actual production project, branch,
direct/pool endpoints and cluster identity. Neon roles are branch-scoped; password
retirement on one branch does not retire copies on another. Its reset-password API
keeps the old password valid until the final operation completes and drops endpoint
connections. Changing a branch password still needs the SQL authority checks and
control-plane credential isolation above. Do not reset a shared owner role blindly.
[Neon security guidance](https://github.com/neondatabase/website/blob/main/content/docs/security/security-overview.md),
[reset-password contract](https://api-docs.neon.tech/reference/resetprojectbranchrolepassword),
[paginated branches](https://api-docs.neon.tech/reference/listprojectbranches).

## Implemented Google observation, with a real positive outcome

`backend/scripts/model_key_revocation_observer.py` provides native metadata reads
only. It has no delete, update, restore, generation, payment or SQL operation.

1. Obtain a fresh observer access token through existing private authenticated tooling
   with Google API Keys lookup/get/list permissions. Its OAuth scope may be broader;
   only the operations performed by this program are asserted read-only. Keep the token and old Google key
   values in process environment only; never put them in CLI arguments, logs or files.
2. Pass opaque `HIREWIZ_FENCE_GOOGLE_KEY_*` environment aliases. Each value is bound by
   authenticated `lookupKey` to its actual numeric owning project and native resource.
3. Fully page each observed parent with `showDeleted=true`. Refuse malformed pages,
   duplicate resources, repeating cursors, missing bound keys and overbound inventory.
4. Independently get each bound resource and require its identity, etag and normalized
   deletion time to equal the list observation. Refuse reactivation/concurrent change,
   unavailable permissions, redirects and ambiguous lookup. A purged key with no
   resource name is unresolved in this bounded implementation, never guessed deleted.
5. Return sanitized resource IDs, etags and delete times. Exit 0 means **all explicitly
   inspected Google keys were natively observed deleted**. Active keys exit 65 with
   their active status. Unavailable evidence exits 65 with a fixed nonsecret reason.
   The report contains `release_permission: false` and explicit unresolved global facts.

The fixed native host is `apikeys.googleapis.com`; requests are authenticated GETs with
explicit CA trust, no ambient proxies, no ambient TLS session-key logging, no redirects and no URL/error logging. Bounds are
32 aliases, 16 owning projects, 16 pages per project, 1,024 total listed keys, 1 MiB per
response, five seconds per socket operation and a 30-second result freshness window. Crossing
any bound refuses; it does not truncate evidence. The tool never outputs key values,
key fingerprints or bearer tokens. Run it in a private process without external HTTP
tracing/audit instrumentation that records credential-bearing lookup query parameters.
The library's post-response deadline rejects stale results; urllib socket timeouts do
not guarantee a hard whole-response deadline against a slow-drip body. Invoke the CLI
with a separate 35-second process deadline and sanitize any timeout as unavailable
evidence. The release runner must retain that outer deadline if this observer is later
composed into a native provider gate.

The Google API documents that deleted keys cannot be used and may be restored during
the 30-day recovery period. Therefore deletion establishes the inspected key status;
it does not establish isolation from an old principal able to restore it. Lookup/get/list
are authenticated resource observations, not proof that supplied aliases cover every
historical credential. [Deletion lifecycle](https://docs.cloud.google.com/api-keys/docs/create-manage-api-keys),
[lookup binding](https://docs.cloud.google.com/api-keys/docs/reference/rest/v2/keys/lookupKey),
[get metadata](https://docs.cloud.google.com/api-keys/docs/reference/rest/v2/projects.locations.keys/get),
[complete list contract](https://docs.cloud.google.com/api-keys/docs/reference/rest/v2/projects.locations.keys/list).

The source supports Google, OpenAI and compatible endpoints, but the current live model
reports identify Google. Google key deletion cannot certify Groq/OpenAI aliases. Each
unresolved provider requires its exact endpoint/account/project/key identity and an
authenticated provider-specific administrative revocation observation. If that
provider exposes no authoritative administrative API through available access, retain
the precise unsupported-provider gate and obtain that access or a separately reviewed
native provider procedure. A failed ordinary request is not a substitute.

## Completing the global fence efficiently

1. **Inventory authority while reads are available.** Bind every distinct legacy LLM
   credential from retained API/analysis revisions, model-enabled jobs/executions and
   exact Secret Manager versions in memory. Fully page each owning provider project.
   Classify unbound historical aliases. Inspect applicable issuer/admin/secret-read
   grants and database control-plane authority. Missing read permission stays unknown.
2. **Prepare isolated replacements.** New runtime/migration/observer roles must be
   distinct and scoped. Replacement credentials use exact new secret versions and
   identities that legacy revisions cannot read or impersonate. Runtime versus
   migration separation alone is insufficient if old workers share the new runtime
   identity. Verify actual grants and required post-migration runtime permissions.
3. **Preserve settlements and quiesce generation.** Establish a tested schema-0009
   compatibility API/analysis worker using replacement DB credentials with optional
   generation off; keep new checkout off and existing signed settlement routes active.
   Close launch admission and drain/classify existing work. Confirm actual routed/tagged
   identity, not just template flags. Unknown obligations remain retained.
4. **Execute separately reviewed retirement.** Retire all old model keys at their actual
   provider authority, with operation identity/results and provider propagation evidence.
   Deny old database login/write/regrant paths, terminate old sessions to completion,
   and prevent old cloud/provider principals from obtaining replacements or restoring
   retired authority. This observer does none of these mutations. Uncertain mutation
   outcomes require reconciliation, never blind retry.
5. **Verify against native systems.** The new observer can now positively establish
   the inspected Google subset. Combine that with proven complete credential coverage,
   unsupported-provider adapters, issuer isolation and the existing native SQL/cloud
   checks. Record the latest stable retirement cutoff from those observations. Fresh
   pre/post checks must protect the release sequence from authority changes; a simple
   advisory lock does not fence noncooperating administrators.
6. **Continue the existing staged release.** Only then create the post-fence protected
   backup with exported verified snapshot, run guarded `0010` → `0013`, verify each
   schema and replacement runtime permissions, and stage/promote exact reviewed images.
   Optional AI, native lifecycle and employer autosubmit stay disabled until their
   independent gates pass. Preserve ledgers/old accepted terms on repair or rollback;
   never downgrade liability history or reopen an old credential.

The eventual native global implementation can replace “all consumer names known”
with proved credential/issuer closure plus reviewed continuity treatment. It must not
replace that field with an operator's `all_keys_revoked` boolean, or call this subset
observer successful and set the existing `Fence` booleans true. This packet deliberately
does not wire that incomplete composition into the release entrypoint.

## Settlement credentials are retained, not model credentials

Current source `backend/app/routers/billing.py` checks each historical checkout's
persisted `provider_key_id` and mode against current settings before verification.
`RazorpayAdapter.verify_checkout_signature` uses only the current API secret. Rotating
the API key during this fence would reject accepted historical browser callbacks.
`webhook_ready` remains independent of checkout being enabled, and signature validation
accepts current plus previous webhook secrets; the current secret must remain present.

Keep Razorpay mode, API key ID/secret and current/previous webhook secrets byte-for-byte
through this model/database cutover. Preserve capture/refund route identity and required
DB permissions on replacement runtime. Pending callbacks and delayed signed webhooks
must still settle the immutable original order snapshot, including retired SKUs.
Razorpay documents that older webhook retries require the older secret, and API-key
regeneration can deactivate the old key immediately or after 24 hours. Neither rotation
belongs to model-key retirement. [Webhook validation](https://razorpay.com/docs/webhooks/validate-test/),
[API-key lifecycle](https://razorpay.com/docs/payments/dashboard/account-settings/api-keys/).

## Concrete unresolved observations and owner information

- [x] Dated metadata resolves four Google legacy keys; all are active at that observation.
- [x] Existing source checks database sessions, effective roles and write/grant paths.
- [x] Standalone Google observer implements positive per-key deletion verification and refusal cases.
- [ ] Complete retained-revision/job/secret-version key mapping and paginated provider inventories.
- [ ] Exact account/endpoint/native administrative resources for three unresolved historical aliases.
- [ ] Native issuer/IAM and replacement-secret isolation, including inherited/custom/conditional grants.
- [ ] Actual Neon production project/branch/endpoints/control-plane principal scope bound to SQL identity.
- [ ] Distinct prepared runtime/migration/observer credentials and their observed required permissions.
- [ ] Actual generation quiescence, admission closure, classified in-flight work and liabilities.
- [ ] Provider-native deletion/revocation, DB role retirement/session termination and fresh verification.
- [ ] Verified post-fence backup/restore and the existing guarded migration/deployment sequence.

The owner's currently pending continuity answer should specify whether the legacy
database/provider credentials serve other legitimate applications and which must remain
available. Needed access information is nonsecret: which Google projects, Groq/OpenAI
accounts and Neon organization/project contain those resources, and which existing
authenticated administrative principal can observe them. Credentials should remain in
the existing private secret handling; they are not requested in chat or this document.

If those observations cannot be obtained, keep the specific monetary activation gate
closed and continue the independent compatible frontend/checkout containment already
deployed. This is a concrete remaining external prerequisite, not an estimate of source
development time or proof that the overall goal is complete.
