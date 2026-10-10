# Retained native credential configuration collector

This is a read-only source component, based on reviewed commit
`7fac1c4d76b8a5de4396b3f010c50941d61827db`. It does not implement the global
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
