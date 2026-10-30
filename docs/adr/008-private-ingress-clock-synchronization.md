# Private candidate transport clock synchronization

Status: implemented; deployed enrollment acceptance remains pending.

## Dedicated browser-pairing gateway distinction

The separate browser-pairing gateway currently receives its coordinator clock
from candidate-account composition's local `time.time_ns()` callback. It does
not use the provider-bounded ingress environment described below. The
[pairing admission candidate](../delivery/BROWSER_GATEWAY_ADMISSION_CANDIDATE.md)
keeps that existing clock and signed five-second incoming-admission window,
while allowing an opaque, single-request processing lease bounded by both local
wall and monotonic elapsed time to at most thirty seconds. Final gateway nonce
consumption uses that lease rather than reusing the public five-second expiry
after native reads; nonce retention covers only the latest bounded lease.

This is an explicit pairing freshness-semantics change, not provider-clock
attestation or an authorization cache. All current account/session/password/
review checks remain required. Pure pinned gateway admission now precedes native
composition, which shares the same bounded processing lease and exact one-use
construction-owner/native binding. The BFF account eligibility preflight does
not guarantee this exact pairing service is warm; platform delay before admission
can still expire incoming input. Actual cold-path/clock qualification remains
open. This candidate does not change the provider-time/uncertainty/no-fallback
requirements of the private account-ingress protocol below or introduce a new
clock authority.

The [signed-device follow-on](../delivery/SIGNED_DEVICE_ADMISSION_CANDIDATE.md)
uses the same existing pairing clock distinction. Its pre-composition timing
capture is not authority; fresh native origin/key lookup and actual ECDSA must
complete before a verified admission can consume a nonce. The public five-second
incoming window remains; the same thirty-second processing lease is never reset.
Challenge-based create/complete/refresh retain their original core deadlines.

The website BFF signs native account requests for exactly five seconds. The
backend independently verifies the signature, physical request, current custody
and unique nonce against its bounded provider-clock observation. On 22 October,
a real HTTPS request was rejected because its issue timestamp was 11,747 ms
ahead of the backend's lower clock bound. Structural configuration and signature
binding had passed. An existing Google session was valid independently of this
native transport failure.

Keep the five-second lifetime and all backend freshness, custody, database UID,
operation deadline and replay checks unchanged. After signature/input binding and
fresh custody checks, a clock rejection may carry a numeric interval only on the
exact read-only availability GET. It remains HTTP 503, private/no-store, and
creates no replay admission or candidate lifecycle operation. Other account
operations expose no clock hint.

The server-side BFF may repeat that GET once using the observed lower bound and
a new nonce. A successful probe permits a process-local time estimate for at
most thirty seconds, advancing with monotonic elapsed time. Scope it to backend
origin, website and key ID. This is an estimate, never an authorization cache:
each later request has a fresh signature and nonce, and the backend performs all
current checks again. Clock movement or unavailable custody can still refuse a
request. No credentials, bearer, body or capability are cached or sent to the
probe. Credential, enrollment, logout and deletion POSTs are never retried.

An unavailable native provision must preserve the separate ordinary legacy
authentication branch. Its existing backend account policy still decides whether
that account may use the branch. Native operations fail closed before forwarding
their credential body when the preflight fails.

Trade-off: a cold server process may need two additional read-only requests. The
short estimate reduces repeated probes while avoiding assumptions about matching
host and provider clocks. This change does not reinterpret Firestore snapshot
timestamps as a guarantee of UTC synchronization between machines. Real deployed
availability, enrollment and device pairing remain separate acceptance work.

Validation covers a fifteen-second caller clock difference with real HMAC/native
guard definitions, strict five-second assertions, replay refusal, bounded numeric
hints, private-body isolation, monotonic estimates and no business-POST replay.
