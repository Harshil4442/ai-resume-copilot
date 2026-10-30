# Browser pairing gateway admission candidate

This source change addresses a reproduced timing defect. It does **not** establish
the cause of the owner's observed 403, change a live deployment or restart an
existing pairing. CSRF/origin rejection, changed sessions and an expired pairing
remain independent possible causes and continue to fail closed.

The BFF creates an exact signed gateway assertion lasting five seconds. Previously
the configured backend verified it before native identity observations, verified
it again afterward, then checked the same five-second expiry inside nonce
consumption after further current-account/native reads. A valid request admitted
at entry could therefore be rejected solely because those reads took 5,001 ms.
Pure source reproductions confirmed both failure points without owner data or a
provider request. The actual code's pending pairing is two minutes; the
five-minute constant belongs to a completed device claim.

## Explicit freshness and processing semantics

Public `GatewayVerifier.verify` and the signed BFF envelope remain unchanged.
Every new HTTP invocation must pass its exact signature/audience/origin/
method/path/body/session binding inside the original five-second admission window.
Expired-at-entry or future-issued assertions are not admitted.

The verifier then issues an opaque, nonserializable local receipt. Its private
registry binds the original verified envelope to this native service instance,
thread/task and exact request body/path. A public constructor, unregistered
object, boolean, another verifier/native instance or another thread/task cannot
create authority. Starting a receipt is single use, and normal completion/error
removes it. No receipt is returned to a browser, persisted in a checkpoint,
transferred in a header, cached for another request or used to bypass HMAC.

The already-admitted request has the existing **30-second** processing bound,
checked using both fixed wall and monotonic deadlines and nondecreasing clocks.
Native identity/control/fence reads, transaction retries and post-operation
barriers still run. Current session/account checks are repeated after their
reads when time advanced; a disabled account, changed identity or expired session
cannot be substituted with an earlier admission snapshot. A later deadline,
rollback, failure or uncertain outcome never authorizes a retry.

After the current identity resolver finishes, the nonce transaction binds its
exact validated `RetainedWebSession` snapshot to the opaque admission as a
restriction. Session/account IDs, authentication method, subject, principal and
generation must match the resolved identity. The original issued/expiry window
then applies at every remaining pure admission barrier, including after native
Commit/fence, command/reply and observation exit. A different subsequent snapshot
refuses; this bound is never extended or used as an allow decision. The resolver
has already read this row, so the existing buffered transaction supplies the same
snapshot without another physical read or an unbounded identity reread loop.
If later IO crosses expiry, output is withheld and a consumed nonce stays consumed.

Gateway nonce consumption remains atomic and immutable. Its retained expiration
is the original signed expiration plus at most 30 seconds, covering the latest
possible request admitted inside that original signed window. This is replay
record retention, **not** an extension of public assertion validity. Concurrent
invocations with the same signed nonce still have one native winner.

The separate [signed-device follow-on](SIGNED_DEVICE_ADMISSION_CANDIDATE.md)
retains five-second incoming device freshness with its own verified bounded
processing admission. The challenge-based proof paths remain unchanged. The actual
pending-pairing two-minute limit, candidate review/password-reauth sixty-second
limits, device proof ten-second limit, current native account state and all
existing approval/signing/activation boundaries remain. The gateway is still
only a retained-session selector; it grants no employer action.

## Existing clock and cold-path limits

Pairing uses the existing `lifetime.now_ms` production callback, defined as
`time.time_ns() // 1_000_000` in candidate-account composition. The new processing
lease uses that **existing local wall clock plus monotonic elapsed time**. It is
not provider-clock attestation. The separate private account-ingress clock in
ADR008 uses an original provider `REQUEST_TIME`/commit acknowledgement with a
bounded interval; this candidate neither replaces nor borrows that authority.
Its uncertainty, freshness and no-fallback requirements remain unchanged where
that ingress is already used.

Configured pairing checks pure configuration/key/origin pins and admits the
signed request before constructing its native service. A private per-wrapper
verifier/construction-owner slot binds that exact receipt once to the resulting
native instance sharing that same verifier, only after rechecking current pins.
The same fixed 30-second lease includes cold Firestore/Admin/Storage SDK/credential
composition, observations and the operation; it is never reset after construction.
Synthetic 4,999/5,001ms cold-composition controls succeed within the lease, while
over-30-second composition or changed configuration/key/origin/verifier fails
before native authority is used.

The BFF's earlier eligibility GET uses a separate account factory and CSRF is
local to the BFF; neither guarantees this exact pairing instance is already warm.
Platform request queuing/startup **before the pure admission code runs** can still
expire the five-second signed input, as can clock disagreement. This change does
not admit such input or introduce a warm retry, wider public TTL, unsigned time
hint or alternate clock protocol. Actual cold-journey and clock behavior still
require production qualification.

## Qualification limits

This changes gateway freshness from a final-consumption deadline to a verified
time-of-admission boundary with bounded processing. It is an explicit contract
change requiring review, not removal of an expiry check or a longer public TTL.
Synthetic clocks/registry/current-identity fixtures and the real local pairing
core exercise the timing, replay and review-expiry invariants. They do not
establish production latency, explain the owner's actual 403, qualify native
provider custody or replace a genuine paired-device journey.

No live flag, CSRF/password rule, identity provider, account/device record,
original recovery evidence or current owner pairing is changed by preparing this
candidate. Production deployment and owner verification remain separate work.

## Source and qualification impact

- Application source changes: `browser_pairing.py` (opaque admission/processing/
  nonce retention) and `browser_pairing_production.py` (configured orchestration).
  The public gateway verifier, BFF signed fields/CSRF format and pairing/password/
  device wire schemas keep their existing contracts.
- The new temporal/binding tests and existing cheap-preflight fixture require
  source qualification. The connected native HTTP test's nonce-record expiry
  expectation changes from five to thirty-five seconds for an assertion issued
  at entry; its public replay rejection remains unchanged. That native-emulator
  case needs the normal owned CI environment, not a fabricated receipt.
- A prospective application source SHA/image, CI evidence and deployment-plan
  source fingerprints must bind these changed sources before promotion. No
  current main/candidate, historical application/native proof, accepted worker
  image, source-policy hash or release pin is updated by this worktree.
- The real owner pairing/reauth/device-proof/revocation journey, cold-path
  latency and clock behavior remain unaccepted. Source reproductions and local
  core tests do not establish the actual cause of the observed owner 403.
