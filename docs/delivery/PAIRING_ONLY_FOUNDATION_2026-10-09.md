# Disabled pairing-only foundation — 9 October 2026

This additive Python core models a two-party device identity enrollment. It is **not
production pairing**. Production storage, recent candidate authentication and claim
issuance default to `GuardUnavailable`; there is no environment, SQL, Redis or local-file
fallback. No HTTP route, shipping extension/MV3 protocol, v1/v2 action core, migration,
cloud configuration, employer request or financial operation changes.

## Frozen identity contract

A server allocates pairing/device/operation/challenge UUIDs after checking an explicitly
approved extension identity and release. `prepare_request` creates only an unproved
request; a valid P-256 proof over its exact domain-separated request is needed to reach
`REQUESTED`. Public JWKs have exactly `{kty,crv,x,y}`, canonical unpadded 32-byte coordinates
and a valid curve point; detached signatures are raw 64-byte ES256. Valid high-S signatures
are supported for WebCrypto interoperability; replay identities are IDs, never signatures.

Canonical bytes are `hirewiz.pairing.<kind>.v2\n` plus sorted compact UTF-8 JSON. They retain
exact Unicode without normalization, reject lone surrogates/floats/unsafe integers and
non-ASCII schema keys, and distinguish booleans from integers. Wire UUID strings are
canonical, protocol version is the exact integer 2, extra model fields are forbidden.
Request ≤120s, initial/device/refresh possession challenge ≤10s, candidate challenge and
recent-auth assertion ≤60s, identity claim ≤300s. Each interval uses one captured time;
expiry equality is closed. Raw nonce UUIDs are returned once and only their hashes retained.

Candidate confirmation requires an explicitly pinned trusted issuer's real signature,
fixed audience/operation, exact request/challenge binding, explicit confirmation, fresh
authentication time and an independently active subject/principal/auth-generation/session.
This verifier is an injected trust boundary, **not a password/provider auth adapter**.
A numeric SQL owner, generic bearer, caller role or device signature cannot substitute.
Independent subject registration is a retained `SUBJECT_REGISTERED` event for the exact
lifetime UUID; missing provenance is denied. Pairing never creates or repairs that mapping.

## Ordering, ownership and denial

The state order is `UNPROVEN → REQUESTED → CANDIDATE_CONFIRMED → COMPLETED`.
Confirmation consumes the candidate challenge and stores immutable exact confirmation
and assertion evidence. Device completion verifies that challenge was consumed by that
same confirmation; it consumes a **distinct** device challenge and the pairing request.
This resolves the earlier design's conflicting request to consume a candidate challenge
at confirmation yet require it unconsumed at completion.

Completion atomically rechecks subject/session/auth generation, extension release,
current authority incarnation/epoch and monotonic tombstones. Key ownership and server
device ownership are permanent across revocation: a previously owned key cannot move to
a new subject/device ID. A cancelled request that never enrolled its key did not claim
ownership. Explicit current-owner revocation retains a device tombstone and the shared
recovery `revocations` scope; it stops pending completion/refresh. Fresh-owner cancellation
uses historical confirmation **ownership evidence**, so an expired original request or
stopped original session/epoch does not prevent a deny after fresh current authentication.
Rotation here means revoke and explicitly pair a fresh key; no rotation transport exists.

Claims are signed only after a confirmed durable transaction/journal result. Ambiguous
commit, signer/reply loss or process death cannot reconstruct a claim on duplicate
completion/refresh: redacted `status` gives only pairing ID, device ID and state. A distinct
fresh single-use key proof may refresh identity after current registry checks. A revoked
claim remains previously issued signed bytes; it has no offline authority and is not a
recall promise. Claim refresh is not an employer action retry.

Enrollment writes the same subject/device/tombstone namespaces used by recovery guards.
It creates **no** recovery actor, employer grant, package/review/sequence approval, artifact
read binding, opening claim, may-act decision, callback, submission receipt or credit event.
A paired key alone fails existing action guard entry points. No sequence bridge is wired.

## Bounded local evidence

The separate test-only authority uses SQLite FULL/WAL with real spawned processes. Its
append-only hash chain retains record changes and checks exact journal/projection equality
before and after transactions; canonical comparison prevents `True == 1` from concealing
projection corruption. Missing files/partitions, replaced authority identity, journal-only
cuts, projection-only changes and a closed authority deny. It does not silently bootstrap.

- 128 new cases: 51 crypto/canonical/assertion cases, 57 lifecycle cases and 20 spawned
  process/race/crash/application-only restore cases, all passing without skips.
- Independent Node/WebCrypto computes canonical/hash vectors and verifies Python signatures;
  Python independently verifies Node-generated signatures. The vector is not browser pairing.
- Process evidence covers duplicate completion, competing owners/keys, completion versus
  revoke/delete/auth-generation+device deny in both ordered and concurrent cases, refresh
  versus revoke, and death after commit before a claim reply.
- Independent device/subject/auth-generation denials survive restoration of a separate
  synthetic application database. Account recreation cannot inherit an old key ownership.
- 222 unchanged v1/v2/recovery authority cases pass. Ruff passes on seven new Python files;
  Mypy passes on the three new core modules. All 446 tracked baseline archive files retain
  their original byte hashes. This is not a whole backend-suite or deployed runtime proof.

## Outstanding enablement gates

The local authority's whole-file rollback, production protected journal retention/IAM,
partition replay/closure and cross-service ambiguous commits remain unproved. No production
store adapter, issuer signing/key rotation or genuinely authenticated session assertion
adapter exists. Legacy subject bootstrap and principal/account linking, deleted-subject
authentication denial, privacy/retention/residency, rate limits/challenge abuse and account
recreation repeat-application policy require separate decisions and implementation.

One key has one permanent owner; a software key does not attest hardware, a physical
installation or one human. This slice does not prove a single account per physical browser
installation, safe local buffers/account switch, extension distribution/release attestation,
Chrome transport/permissions or the same-owner sealed-package/v2 bridge. Fixed extension
registry entries are trusted operator inputs, not attestation. Candidate-only revoke is
implemented; device-signed unpair and operator/deletion orchestration remain separate.
No production gate is marked complete by this fixture and no employer permission is implied.

## Root integration

The frozen additive patch was integrated into the current root. Whole-domain Mypy
resolved the store protocol's optional challenge result and found one unpacking error
that the archive's three-file check did not cover. An explicit assertion immediately
after the existing creation-proof denial check narrows that value without changing
denial or ownership behavior. The archived patch and evidence remain unchanged;
[root integration evidence](evidence/2026-10-09-pairing-only-local.json) records the
new source hash separately.

The merged root passes 930 backend cases without skips, 72 frontend unit cases,
Ruff, compilation, six prompt evaluations and Mypy across 61 source files. Focused
catalog/pairing/PostgreSQL finance verification passes 194 cases. Generated API
contracts have no drift. Exact `e7d8f2b` passed all four remote CI jobs and was
included in the pinned GCP image; the matching Vercel deployment is promoted.
The [release report](CATALOG_SNAPSHOT_RELEASE_2026-10-09.md) records exact
identities and bounded checks. Production pairing remains unavailable regardless
of source image inclusion; health and image inclusion do not prove its enablement.
