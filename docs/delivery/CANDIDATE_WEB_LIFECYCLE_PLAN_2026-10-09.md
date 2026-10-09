# Candidate website lifecycle integration plan

This scope starts from the captured reviewed integration baseline, manifest
SHA-256 850249d2ba715d53f2517d9e6eb1261777e0ca0cd1c9a728b88834b19b2cdc6f.
It changes only the owned source snapshot. It is not deployment authority.

1. Preserve ordinary signup and legacy login when native custody is unavailable.
   Offer fresh protected password registration explicitly after checking the
   actual server constructor. Never infer readiness from a public feature flag.
2. Add password-verified read-only registration status. PENDING remains denied;
   status does not replay an original ACK or activate a registration. A failed
   request can be checked without automatically repeating enrollment.
3. Keep retained session context in the encrypted server-only NextAuth JWT.
   Bound sensitive frontend requests/responses and return fixed error categories.
4. Guard actual NextAuth signout before the handler can clear its cookie. Require
   the exact retained backend denial first, and block account replacement until logout
   completes. Unknown outcomes retain the cookie and provide explicit retry UI.
5. Compose a local socket fixture from actual SQL credentials, native lifetime
   issuance, V3 protected activation and real PostgreSQL. No candidate identity,
   subject registration event or session is pre-seeded.
6. Verify actual cold website login, private JWT, account switching and logout;
   preserve sensitive HTTP privacy/bounds and legacy compatibility checks.
7. Freeze patch, baseline/result hashes and scoped test evidence for independent
   review. Root integration and production deployment are later checkpoints.

Legacy enrollment requires an explicit operator baseline and fresh password
verification; it is not supported by this scope. Google remains ordinary
account access and cannot produce a password pairing context.

Known separate gate: V3 scoped password read is available, but its signing
admission/consume ports remain unavailable pending the genuine same-authority
composition. A fixture must preserve that gate rather than omit scoped denial.
Normal password reset projection belongs to the separate V3 reset author.

## Denial and recovery boundary

The NextAuth handler runs only after the server-side logout guard validates its
CSRF/origin and retains or confirms the exact original cookie request. NextAuth's
signOut event catches exceptions, so an event hook alone cannot preserve a
cookie on revocation failure. The server-only encrypted JWT contains a private
random logout request UUID; public session JSON never contains it, the bearer,
or the retained context.

The separate GcpCandidateCookieLogout adapter binds that request to the original
native-issued session, immutable enrolled account owner, exact credential digest
and generation, full protected command/pin and canonical intent fingerprint.
Its actual protected transaction retains the full V3 record and immutable pending
session denial before cookie success. Every scoped session reader consults this
pending denial, so restoring an old cookie cannot recover authority. This is a
denial-only cookie completion, not a final projection/tombstone ACK or action
grant. A persisted UNKNOWN is confirmed only by rereading the same exact retained
command and denial; an unpersisted UNKNOWN creates no claimed revocation and
requires an explicit fresh retry for that same private request. No historical
ACK is reconstructed. Protected root closure, missing provenance, foreign
requests, changed generation and unknown proofs keep the cookie gate closed.
An expired session may retain denial only. Definite account deletion may clear
only its original deleting context after permanent protected tombstones, even
when its SQL row has been erased; it never recreates an account.

Auth pages keep controlled inputs disabled until hydration and the shared
SessionOwnerBoundary finishes resolving the owner. Logout also waits for that
resolution, preventing a remount from automatically retrying an UNKNOWN.
Recovery status is password-verified and read-only. PENDING registrations have
no session and require a later separately reviewed fresh trusted operation;
checking status never adopts activation or repairs them automatically.

## Evidence and deployment dependencies

Author evidence exercises actual PostgreSQL migrations through 0011, the native
writer's original ACK, actual V3 final activation and scoped denial using fresh
identities, real HTTPS production NextAuth/BFF processes and Chromium. The socket
fixture mounts explicit real constructors and unique local emulator databases.
It seeds only synthetic operator controls/extension registration; subject,
account and candidate sessions are genuinely issued from committed credentials.
Ambient credentials, secure cloud transport and external backend HTTP are
forbidden. The browser blocks every origin except the exact test loopback and
records only API method/path, never bodies, cookies, passwords or raw errors.
Synthetic Storage HTTP, operator UID witness and local signing custody remain
explicit limitations. Owned schemas/processes and TLS private keys are cleaned.

The runner accepts an optional independently authored actual MV3 journey module
plus public manifest identity and exact extension revision. It preserves the
unavailable same-authority admit/consume gate until root's reviewed composition
joins the fixture; it cannot replace that gate with a callback or omit V3.

Production dependencies still open: actual independent Firestore UID/pin and
root policy, immutable Storage/IAM writer custody, reviewed signing custody,
exact extension/release policy, genuine scoped signing/consume composition,
normal reset projection and reset-cookie cleanup, schema-0011 exact release
preflight, recovery-boundary legacy quarantine/operator baseline, and explicit
Google identity enrollment. Default factories remain unavailable. Availability
checks expose only an actual injected constructor's capability; ordinary signup
and existing work remain accessible when that capability is absent.
Independent review, root integration, full joined browser proof and deployment
are separate checkpoints; these author results do not certify production.
