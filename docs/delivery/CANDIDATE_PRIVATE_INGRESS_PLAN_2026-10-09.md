# Candidate private ingress, separate authored stage

Base: immutable capture of reviewed root commit
69fbc22fcc7020b7dac29b3be138c525818f6975 plus frozen web V1 and BFF R2 prerequisites.
The web prerequisites were captured while their independent recheck was open.
Root subsequently verified the R2 independent freeze (report SHA256
`76a8b7d0730cf30dba83d7bdd975d43310738ccc359bf43062c5f70a452a646d`).
The original immutable capture and this separate ingress scope remain unchanged. This stage is not a production enablement and never changes candidate
identity, password lifetime, native pairing or scoped-publication schemas.

The native candidate HTTP surface is a server-to-server surface. All canonical
`/api/auth/candidate/v1/*` endpoints require the boundary before dependencies or
lifecycle calls, including availability/session/status/denial/password paths.
The legacy register (when native mode is enabled), mapped-account login, and
mapped-account deletion require the same boundary at their native branch;
missing/unknown proof never falls back to a numeric legacy grant. Existing
unenrolled password and Google account semantics remain supported. Google cannot
issue mapped native context. Canonical method/raw ASGI path and no query are
checked independently of decoded routing; redirects/encoded variants cannot
unlock an unguarded native entry point.

A website server generates a new request UUID and signs its bounded exact raw
body and server-owned Authorization header. Separate HMAC-derived keys/domain
separators bind body commitment to protocol + request UUID, Authorization to its
own protocol + UUID, and assertion MAC to a distinct assertion domain. The
assertion carries version, fixed issuer/audience, pinned key ID, request UUID,
exact configured HTTPS website origin, method, canonical backend path, issue and
expiry milliseconds (at most 5000 ms), and keyed body/header commitments. Raw
credentials, raw bearer/header, assertion header/MAC, keys and public unsalted
credential-body digests are never retained in replay rows, logs or errors.
Canonical JSON/base64url, duplicate/extra/nonfinite fields and bounded header
parsing fail closed. Native request reads preserve the existing 8-KiB/2-second
sensitive reader before FastAPI body parsing; header/body errors are fixed.

The replay constructor requires an actual injected FirestoreRpc, a dedicated
full RegistryPin/resource, pinned website origin/key ID, an explicit custody and
trusted-clock port, and the actual ordinary/protected action database pins for
separation checks. It creates no client, credentials, key or operator control.
The dedicated database has its own exact OPEN/CLOSED operator root, custody and
restore generation. Every accepting transaction reads that root and the exact
request UUID's absence and writes the full safe canonical assertion + resource
+ accepted timestamp atomically. Existing rows always deny, even exact replays.
Only an original definite one-write native Commit ACK permits that request to
proceed. Unknown/malformed ACK, expiry, root closure, UID/pin/custody mismatch or
bounded abort exhaustion permits no lifecycle call. Read-only status cannot
become an admission. No rows are inserted into existing scoped publication.

Replay rows have no TTL in this stage. Inference from the official Firestore
transaction contract: a read-root/read-absence/write-record transaction can
serialize concurrent replicas at commit time. TTL is not a replay decision and
is not instantaneous. Supporting references:
[Firestore transaction isolation](https://docs.cloud.google.com/firestore/native/docs/transaction-data-contention)
and [TTL limitations](https://firebase.google.com/docs/firestore/ttl).
A reviewed custody/restore transition and clock bound remain required before
production; an env key alone cannot enable the factory. The production boundary
stays unavailable. Synthetic operator metadata/clock custody in actual emulator
fixtures is explicitly labelled, not IAM/restore evidence.

Website ownership: a new server-only ingress signer used by backendAuth and the
fixed account handler; public generic auth forwarding stays blocked. Backend
ownership: new candidate_ingress contracts/crypto/replay modules, a small HTTP
boundary helper, opted-in SensitiveAuthRoute wrapper and explicit legacy native
branches. Actual private fixture injection uses the real independent native
replay writer and genuine existing native/V3 enrollment, never seeded identities
or a fake final-activation port. Browser-owned native2/pairing/census/scoped files
are not edited.

Checklist:
- [x] Author: strict cross-language keyed assertion contract and bounded failure behavior.
- [x] Author: actual independent Firestore replay root/pin/OPEN/Commit writer.
- [x] Author safety: definite original ACK followed by independent-process replay refusal, at-most-one concurrent admission and both unknown outcomes.
- [ ] Availability: preserve original exactly-one concurrent winner failure; both contenders were refused. Later at-most-one safety probes do not clear this contention gate.
- [x] Author: all native and conditional legacy entry points gated; no downgrade.
- [x] Author: real NextAuth/BFF/PG/native/V3 cold signup/login/logout/account-delete flows.
- [x] Author: genuine direct-backend bypass probes and no lifecycle call after UNKNOWN.
- [x] Author: 171 affected backend/auth/native/PG cases, 199 frontend cases, static/contracts and actual cold web 46/socket 51 cases.
- [ ] Freeze exact author artifacts and independent review; counts do not establish deployment.
- [ ] Independent review; reviewed browser fixture composition after freeze.
- [ ] Production custody/key separation/clock/restore/retention provisioning proof.

The sensitive route preserves SlowAPI's handled RateLimitExceeded exception.
The independently observed sixth-enrollment 500 remains preserved; actual
repaired HTTPS tests enroll five genuine native/protected accounts and confirm
the sixth returns 429 without entering credential/native enrollment. Other
private provider/SQL exceptions still produce fixed nonreflecting errors.

Production remains unavailable: no independent database/key/IAM/OPEN root,
restore witness, trusted clock bound or retention custody has been provisioned
by this patch. No production schema, account, credential or employer action was
mutated. Google pairing enrollment, legacy fresh-identity bootstrap, the reviewed
protected browser join and actual cold Chrome connection are separate gates.
