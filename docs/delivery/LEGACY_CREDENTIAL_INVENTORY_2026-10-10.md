# Retained native credential configuration collector

This is a read-only source component, initially based on reviewed commit
`7fac1c4d76b8a5de4396b3f010c50941d61827db`. The bounded v4 parent-field
compatibility amendment is based on published commit
`97eaef1afce3a415fd20539bb61469388dd56e37`. It does not implement the global
credential fence, publish a release, prove provider deletion or permit deployment.
`NativeBoundary.verify_fences` and every existing release gate remain unchanged.

## What it collects

`backend/scripts/legacy_credential_inventory.py` uses authenticated GETs against
three fixed Google REST origins. Resource Manager binds the fixed project ID
`ai-resume-parser-482412` to project number `157225724590`. Cloud Run observations
are explicitly limited to `us-central1`.

The collector lists all retained regional Services, Revisions, Jobs and Executions
with `showDeleted=true`, including deleted resources that have not expired. The
three existing API/analysis/employer services must be present. Service and job
configuration templates are included along with their retained revision and
execution templates. Executions are inspected independently: an execution may
have used overrides that are absent from today's job template.

Every list follows all pages within explicit page/resource/request/time limits;
partial regions, repeated tokens, duplicate names/UIDs, malformed identities and
bounds refuse. An independent native GET must agree on name, UUID4 UID,
generation, etag, deletion metadata, parent and full container configuration.
All containers, environment entries, commands and volumes participate in that
in-memory comparison. A second full listing refuses source additions, removals,
replacement or configuration drift. This is a bounded consistency observation,
not a transactionally atomic snapshot or a guarantee against later changes.

Native transport uses explicit certificate roots, hostname verification, no
ambient proxy, no redirects and no TLS session-key logging. It accepts only the
fixed project/region GET paths, paginated list paths and numeric Secret Manager
version metadata/access paths. Each socket operation has a five-second timeout;
the operation checks a 600-second total budget and 8,192-request bound. A caller
must also impose an external process deadline because a socket timeout is not
an absolute process timer. HTTP/network/parser failures expose fixed reason codes,
never native exceptions containing URL, bearer or secret values.

## Credential candidates and excluded settlement data

The reviewed runtime obtains its database DSN from `DATABASE_URL`
(`backend/app/database.py`) and its explicit model key from `LLM_API_KEY`
(`backend/app/services/llm_client.py`). The collector also conservatively gathers
actually configured `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `OPENAI_API_KEY` and
`GROQ_API_KEY` as explicit model candidates. This does **not** assert that the
current runtime uses those SDK/legacy aliases, or infer a provider from an opaque
value. Provider identity/revocation still needs a separately reviewed native join.

Only those exact names become retirement candidates. A credible unknown model/DB
alias, unsupported model payload or unsupported database authentication/URL shape
remains an explicit unresolved source. Empty configured credentials add no value.
Whitespace trimming follows the current runtime's initial environment normalization;
values are deduplicated by candidate kind and normalized string in memory, without
persistent fingerprints. Different DSNs are not assumed to represent the same
native database authority. Source/issuer binding and database authentication remain
separate work.

Razorpay API key ID/secret and current/previous webhook secret, JWT secrets,
analysis/employer task tokens, job-feed credentials and unrelated application
configuration are never model/DB retirement handles. Their environment fields
still participate in exact native configuration comparison; they are not changed,
separately accessed or returned as credential values. A transient redaction check
protects the returned provenance against reflected native environment secrets.
It includes every nonempty known, excluded or credible credential literal, without
a minimum-length threshold. URL parsing also protects decoded password components
and literal password/SSL-password/SCRAM-key/OAuth-client-secret query values, including
repeated query values. Reflection refuses the report; these fragments are never
returned, persisted or hashed. No excluded/unknown secret reference is accessed
to perform this check, and candidate classification is unchanged.

## Exact Secret Manager versions

A same-project reference must identify an exact positive numeric version. `latest`,
version aliases, malformed references and foreign-project references remain
explicit unresolved sources; they are not resolved to today's version and
attributed to a historical process. Only enabled exact versions are accessed.
Returned native payload name, base64 bytes and CRC32C must agree; a fresh metadata
GET must remain identical after payload access.

Disabled/destroyed versions produce typed handles with native state, source origins
and `value=None`, plus explicit unresolved entries. They are never treated as
provider revocation. Disabling future distribution cannot revoke a credential
already loaded into an old process or copied elsewhere. Missing permissions or
unavailable native metadata/access refuse the collection rather than producing
an empty inventory.

## In-memory boundary and unsupported scope

The collection budget remains 600 seconds. The pre-request, post-payload and final
collection checks compare the monotonic clock directly with the absolute start
time plus that budget. A reading exactly at or beyond the deadline is refused;
there is no precision allowance or extended time limit. Denied request attempts
still count toward the existing request bound. Boundary tests use fake openers
and metadata responses, including the original cutoff test, so an assertion
failure cannot trigger an actual native request.

`Credential`, `SecretHandle` and `RetainedInventory` hide values from `repr`.
Use only `sanitized_provenance()` for output. Never pass private return objects
through `dataclasses.asdict`, pickle, logging, artifact serialization or crash
capture: `repr=False` is not general-purpose memory isolation. No production
credential value or value hash is written by this module.

Sanitized provenance identifies the native resource names/UIDs, exact referenced
secret versions/states, candidate counts and unresolved sources. Its status is
`collected_with_unresolved_sources` whenever any referenced value or credible
configured source is unresolved; it never emits a global completeness certificate,
`Fence`, provider-revoked assertion or true `cutover_ready`.

Mounted configuration is unresolved. Image-embedded secrets, arbitrary process/file
configuration, expired/pruned revisions or executions, other regions/projects,
external consumers, issuer restoration/minting/replacement-secret privileges,
provider revocation and database retirement remain explicitly outside this
component's proof. An environment-source success cannot erase these scope gaps.
A future global verifier must compose reviewed native authority closure instead
of trusting this component's counts or caller-provided approval booleans.

## Verification and development checklist

- [x] Standalone native-shaped positive cases exercise resource/project/list/GET,
  retained execution overrides, all containers, literal deduplication and pinned
  secret payload identity/CRC.
- [x] Refusal cases cover partial/repeating pagination, UID/config/parent mismatch,
  collection drift, unsupported aliases/unions/value shapes, foreign/latest
  secret references, unavailable payloads and metadata changes.
- [x] Secrecy/transport tests cover repr/provenance, merchant exclusion, fixed GET
  origins, absence of model/key-string/mutation endpoints, TLS keylog/proxy and
  response exception/redirect/bearer reflection suppression.
- [ ] Independent frozen-source review and final replay.
- [ ] Actual authenticated production retained-metadata observation, if authorized,
  with an external deadline and sanitized output only.
- [ ] Retained identity provenance joined to legacy source/image history and issuer
  authorities; pruned/history/external scope remains unresolved until proved.
- [ ] Global native verifier, issuer/DB retirement and monetary deployment.

V1 passed its original 97 focused cases and two unchanged release refusal/observation
cases, but independent adversarial review proved nine real provenance reflection
failures. That packet and the original failures are retained. V2 adds the bounded
transient redaction repair above. Its final author replay passed 109 focused cases,
the same two unchanged release cases and all nine unchanged independent reflection
probes, with zero skips. Configured Ruff and Mypy checks passed. Initial new-test
style/type diagnostics are retained; no production or real credential observation
is claimed by those tests. Independent v2 review then proved one remaining malformed-URI
reflection case: a nonnumeric port prevented URL parsing while the password could
still appear in a resource name. V2 and that original failure remain preserved.

V3 changes that fallback to a fixed `credential_uri_redaction_parse_unavailable`
refusal. A sensitive credential-bearing URI that cannot be parsed never produces
an inventory or diagnostic report object. No fallback parser, candidate classification,
secret access, namespace or native gate is added. Final author checks passed 110
focused cases, two unchanged release cases and all ten unchanged independent probes
(122 tests, zero skips), plus Ruff/Mypy. Independent v3 review remains pending.

A dry local check confirms that the locked psycopg client recognizes the literal
authentication query fields used by the redaction helper, and that SQLAlchemy
forwards query password/SSL-password parameters. No connection is opened. File-based
authentication sources remain outside this component's proof and are not read.
[PostgreSQL libpq connection parameter contract](https://www.postgresql.org/docs/18/libpq-connect.html)
documents these authentication fields; their inclusion is privacy protection,
not a claim that a configured authentication mode succeeds.

No production observations, cloud writes, database connections, provider operations,
model calls, payment calls, employer submissions or CI/build/publication/deployment
were performed to develop the component. Tests use synthetic native-shaped data.

Primary native contracts: [Cloud Run service listing](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services/list),
[revision listing](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services.revisions/list),
[job listing](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/list),
[execution listing](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions/list),
[Revision](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.services.revisions),
[Execution](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs.executions),
[Container/EnvVar/SecretKeySelector](https://docs.cloud.google.com/run/docs/reference/rest/v2/Container),
[secret-version metadata](https://docs.cloud.google.com/secret-manager/docs/reference/rest/v1/projects.secrets.versions/get),
[exact-version payload access](https://docs.cloud.google.com/secret-manager/docs/reference/rest/v1/projects.secrets.versions/access).

## V4 native parent-field compatibility

After independently cleared v3 was published, the operator's bounded authenticated
read-only observation refused with `native_parent_binding_unavailable` after 11
GETs. That refusal did not establish that a parent had been pruned. A separate
nine-GET diagnostic found a short `Revision.service` and a short `Execution.job`
in the sampled children. In each case the exact full parent derived from the
child's full resource name existed in its completed native parent list. The
diagnostic covered the first failure of each kind within a bounded first-20-child
inspection; it did not certify all children or a complete credential inventory.
Both observations made zero mutations, model calls or database connections; the
parent diagnostic made zero secret-payload requests. The original refusal and
sanitized diagnostic are preserved separately from this source amendment.

V4 derives the canonical parent only from the independently validated full child
resource name. It accepts the native parent field only when it is a string equal
to that exact full parent or its exact basename. The derived full parent must
still be present in the completed parent list. Project ID/number spellings are
not normalized or conflated. A foreign project, region, parent type, arbitrary
short name, missing parent or pruned parent still refuses. The native list and
independent GET must still agree on the original parent field, UID, generation,
etag and every configuration binding before this compatibility check runs.

The two observed short-parent cases genuinely failed against unchanged published
`97eaef1`, while the full-parent controls passed; that original red log is retained.
Final author checks on this amendment passed 142 focused cases, two unchanged
release refusal cases and all ten unchanged independent privacy probes (154
tests, zero skips), plus Ruff/Mypy. Existing test definitions, privacy helpers,
credential classification, transport and global `NativeBoundary` remain unchanged.
Independent v4 review and actual native invocation of v4 remain pending. The author
made no new native requests, database connections, source publication or deployment.
This correction does not implement a global credential fence or permit cutover.

## Root actual v4 observation

Independent final v4 review passes 154 unique cases with zero skips, plus Ruff/Mypy.
One bounded native invocation at 00:36–00:40 UTC on 10 October makes 145 GETs
over 242.37 seconds, with zero model, database or mutation calls. It advances past
the repaired parent field comparison, then refuses
`native_environment_identity_or_duplicate`. This fixed code distinguishes neither
the failing condition nor the affected entry publicly; no duplicate or unsupported
identity is ignored. [Root checkpoint evidence](evidence/2026-10-10-preflight-checkpoint.json)
preserves both actual refusals and the sampled parent diagnostic. Complete retained
inventory, provider retirement, global fencing and deployment remain unproved.

## Root deadline repair checkpoint

Independent review of the exact v5 source passes 220 unique checks with zero skips,
plus Ruff/Mypy. The three absolute cutoff comparisons preserve the 600-second
budget, request limits, privacy and all release refusals. The original native CI
failure (one failed deadline case, 2,707 passes) and the unchanged independent
exact-cutoff counterexamples remain preserved. This source proof does not establish
actual inventory success; the environment-name refusal above remains unchanged.
Fresh published-source full CI and native image verification remain pending.


## V6 opaque environment-name compatibility

This source-only amendment is based exactly on published commit
`1c2519bc3989c60405dce5154d8e6bd73067d826`. The earlier native observation's
`native_environment_identity_or_duplicate` refusal remains preserved. Separate
sanitized metadata reported 18 non-POSIX names across nine records and zero
exact duplicates; no actual names or values were exposed to this author. That
observation establishes the repair's scope, not successful inventory or retirement.

Cloud Run's [EnvVar contract](https://docs.cloud.google.com/run/docs/reference/rest/v2/Container#EnvVar)
limits names to 32,768 characters. Its [configuration guide](https://docs.cloud.google.com/run/docs/configuring/services/environment-variables)
includes hyphenated names and rejects empty names and `=`. The collector now
accepts nonempty UTF-8-encodable names within that character bound, excluding `=`
and NUL. It retains raw identity exactly: no case, punctuation, whitespace or
Unicode normalization, and exact duplicate names still refuse. Unicode and
leading digits do not imply credential usage. The guide's `X_GOOGLE_` reservation
is a creation rule; this read-only component preserves observed reserved names
without creating variables or claiming their runtime validity. Existing payload,
value-byte, environment-count, resource, page, request and absolute time limits
remain unchanged.

Only the existing exact model/database name sets select credential candidates.
The existing credible-alias rule is retained. A separate case-insensitive,
separator-tolerant plausibility check marks unfamiliar model/database spellings
unresolved, including surrounding ASCII whitespace; it never normalizes identity,
promotes those spellings to candidates or accesses their secret references.
Other unclassified names are not proof that arbitrary configuration contains no
credentials. The global scope limitations above remain in force.

Raw environment names in `Origin` are private and hidden from `repr`. Sanitized
unresolved provenance uses zero-based container/environment indexes and fixed
reasons. Mounted configuration has no environment entry and uses a null index.
Unknown raw names participate in a transient reflection check, so a name copied
into a public resource, UID or secret-version field refuses output. Literal value
redaction searches the same existing generic privacy markers case-insensitively;
non-ASCII names also receive conservative literal-value redaction, protecting
confusable spellings without changing candidate classification. Reflected values,
decoded URL authentication fragments or raw names are never persisted or hashed.
An incidental match can conservatively refuse output; these privacy heuristics
are not exhaustive credential discovery. Unknown/excluded references remain
unaccessed and settlement/JWT/task credentials remain excluded from retirement.

The unchanged-script red run reproduced 30 failures with 13 passing controls.
The first repair passed those 43 cases; an unchanged independent probe then found
one trailing-space alias omission (36 passes/one failure). Four additional
independent lowercase-marker reflection cases and two authored Unicode-confusable
reflection cases also failed before their privacy-only correction; all original
failures are retained outside Git. A first test-launch path error occurred before
pytest and is preserved separately. Final-byte verification passes 209 collector
cases, 41 unchanged independent name/privacy probes and two unchanged global
release-refusal/observation cases: 252 passed, zero skipped. Ruff and Mypy pass.
The original 160 collector cases, native transport, candidate/exclusion sets,
source/secret identity checks and every global release gate retain their behavior.

No native credential inventory, cloud/SQL/provider call, mutation, CI/build,
publication or deployment was performed by this author. Independent frozen-source
review and any authorized native observation are separate next steps. This fix
never produces a global fence or true `cutover_ready`.
