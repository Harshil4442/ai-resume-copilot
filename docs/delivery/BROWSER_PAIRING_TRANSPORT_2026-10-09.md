# Browser pairing HTTP transport

This change connects the reviewed identity-only native pairing/password services to
candidate and device HTTP contracts and adds the candidate connection-review page.
It does not release employer autofill, upload or submission, mint action permits,
reserve application credits or claim completion of paid automatic applications.
The production dependency deliberately returns unavailable. Development is based on
`3d2e693d3fbeb62aa821f07472bab211c6dd568b`; compatibility was checked with the protected
publication implementation at `e6a94843ecbd4a9db43cb905b12ed6ad09bd96fe`.

## Connected contracts

All backend routes use the separate `/api/v1/browser-pairing` router. Sensitive JSON
is streamed with an 8,192-byte/three-second bound, duplicate keys and nonfinite or
noncanonical data are rejected, and validation failures return a fixed public error
without supplied inputs. Candidate and device paths reject general Authorization
headers and cookies. Results have private/no-store/nosniff headers and a 16,384-byte
bound. The router never logs passwords, request bodies, headers or assertions.

| Backend POST | Purpose | Required proof |
| --- | --- | --- |
| `device/prepare` | Allocate an unproved request | Public P-256 key, exact approved extension/revision, signed transport request and fresh one-use request ID |
| `device/create` | Prove possession of the prepared key | Exact server request signature and nonce |
| `candidate/challenge` | Prepare candidate review | Signed gateway context and current independently registered account/session |
| `candidate/confirm` | Confirm this pairing | Same server context, fresh native challenge, explicit confirmation and actual current-password verification |
| `device/challenge` | Prepare device completion | Signed transport lookup with retained key and exact extension Origin |
| `device/complete` | Complete identity pairing | Exact native device-challenge signature and nonce |
| `device/status` | Read redacted lifecycle status | Signed transport lookup with the retained request key |
| `device/refresh-challenge`, `device/refresh` | Refresh identity-only claim | Current retained device key/extension, fresh challenge and exact signature |
| `candidate/revoke-challenge`, `candidate/revoke` | Revoke a device | Current independently retained candidate session plus a fresh native password-confirmation challenge |

The device Origin must equal the extension ID retained on the request/device, including
completion and refresh. This is a binding, not a cryptographic attestation of a Chrome
binary. Each issued identity claim is signed with the existing native claim issuer and
has audience `hirewiz:pairing-only` and operation `device_identity`. No action or job
capability can be inferred from that claim. The existing generic backend BFF rejects
this namespace so a general web bearer cannot enter these routes.

## Candidate session and CSRF boundary

The dedicated Next route requires an explicit exact same Origin, rejects cross-site
fetch metadata, and reads an actual encrypted server-side NextAuth JWT. Only its
`browserPairingSession` claim is accepted. The exact context has `candidate_id`,
`account_binding_id` and `session_id`; malformed fields, extra fields, numeric bearer
subjects, `hirewizUserId`, client session data and body-supplied contexts are insufficient.
Existing login callbacks do not manufacture that claim in this change. Protected native
credential/session issuance must provide it before real users can pair; the public
Session callback must never expose it.

1. An authenticated website POST to `candidate/csrf`, with explicit same Origin and
   exactly `{}`, issues a random token and `__Host-hirewiz-pairing-csrf` cookie.
2. The cookie is Secure, HttpOnly, SameSite Strict, Path `/`, has no Domain, and expires
   after ten minutes. Its MAC binds the exact retained server-session context.
3. Mutations require both the cookie and the matching `X-Hirewiz-CSRF` token. A sibling
   domain cannot create a conforming host-prefixed cookie. Even a crafted cookie fails
   MAC verification; changing the retained session invalidates the cookie.
4. After that check, the server signs a separate gateway assertion with a dedicated
   key. It binds issuer/audience, unique request ID, exact POST path, exact raw-body
   SHA-256, website Origin, retained context, issue time and a five-second expiry.
5. The backend verifies this exact assertion and consumes the request ID in one native
   transaction with current control/fence and independently registered identity/session
   checks. It then dispatches into the real native challenge/password service.

Only request-ID expiry is stored in the replay namespaces. No HTTP body digest,
password, cookie, CSRF token, gateway assertion or gateway signature is retained there.
Concurrent replay has one winner. A losing request may receive a replay rejection or
bounded native unavailability; neither permits dispatch after failed consumption.
Lost native consumption acknowledgement does not
become a fresh invocation. Password verification and current native identity are
rechecked by the existing password signing/admission/consumption boundaries.

Gateway deployment requires a separate random 32-byte key supplied as exactly 64
lowercase hex characters in `BROWSER_PAIRING_GATEWAY_SECRET`, a fixed HTTPS
`BROWSER_PAIRING_WEBSITE_ORIGIN`, and the existing HTTPS `BACKEND_URL`. These are
server settings. They do not enable the unavailable production factory or prove IAM,
nonrestore protection, current session provisioning or release approval.

Candidate replies are projected to a review allowlist: challenge/nonce, pairing/device
IDs, public key/request fingerprints, operation and expiry. Subject/principal/server
session/account-binding data and signed assertions are withheld. The BFF validates the
reply contract and replaces backend failures with fixed public messages.
Device replies also undergo exact per-operation validation of keys, types, identity
audience, extension/revision, lifetimes and signed claim shape. Unexpected private
fields are rejected. Signed payload values are forwarded unchanged.

## Website and companion

`/browser-companion?pairing=<canonical UUID>` loads nothing until the candidate asks
to review. It displays key/request fingerprints, requires an explicit matching-device
checkbox and password, clears secrets after confirmation, and never retries uncertain
confirmation automatically. Success says candidate confirmation was saved and asks
the companion to finish proving its key; it does not report an application submitted.

The new companion pairing client uses WebCrypto P-256 domain-separated signatures,
credentials omitted, no redirects, bounded responses and current identity-only claim
verification. Its private key remains nonextractable. It cannot perform employer
actions. The shipped MV3 manifest/configuration remain disabled with zero host grants;
the new client is not wired into the shipped popup/background yet.

## Verification and limits

- Actual ASGI/native Firestore/password/crypto tests cover prepare, create, candidate
  confirmation, completion, refresh and revocation, changed gateway Origin/body/session,
  replay and concurrent consumption, Commit reply loss, independent fence closure,
  wrong device key/extension, stale authentication and malformed secret bodies.
- Actual native HTTP outputs for ten lifecycle reply shapes also enter the actual
  TypeScript BFF validators through an in-memory pipe. Signed claim values remain
  unchanged; malformed generation types and unexpected private fields are rejected.
- BFF tests use real encrypted NextAuth JWTs, host-only CSRF issuance, changed context,
  sibling/opaque/missing Origin and malformed/untrusted backend replies. Their fetch
  boundary is synthetic; they are not a full hosted BFF/native integration trial.
- UI tests verify explicit fingerprint/password approval and no automatic retry.
- Companion tests use actual nonextractable WebCrypto keys and signatures against a
  synthetic response provider, independently from the native HTTP fixtures.
- Native positive fixtures use a unique owned localhost Firestore database, a real
  password verifier with an owned SQLite credential file, and actual Storage SDK calls
  to a synthetic generation-zero object provider. They use no ADC, live GCP, actual KMS,
  real candidates or employers. Final counts/hashes are in the accompanying report.
- CI provisions Node 24 in the backend test job for that cross-runtime contract check;
  the deployed Python runtime does not gain a Node dependency. Production Next build
  verification uses explicitly synthetic fixture configuration and does not deploy.
- V1 combined author verification: 28 native HTTP cases, including a controlled
  definite-abort loser and the actual one-consumption/one-challenge invariant; 100
  frontend unit cases; 61 companion cases; an actual production Next build; and a
  zero-finding scan of the authored files. These are author checks, not independent
  clearance, full connected MV3 acceptance, integration or deployment.
- An earlier combined run had 26 passes and one race failure: `[200, 503]` when the
  original test required `[200, 403]`. Its report is retained. That run did not retain
  the private exception, so its specific cause is unknown and is not inferred from
  later passes. Four bounded diagnostics observed real native Commit ABORTED followed
  by replay rejection. The final race test accepts 503 only for an explicitly
  classified native consumption-unavailable exception and proves a single retained
  request, one challenge event, a subsequent 403 replay and no device/action authority.
  Unexpected exceptions remain test failures.
- Independent V1 review held release because `boundedPairingBytes` awaited an
  underlying stream's cancellation acknowledgement, which could remain pending
  beyond its three-second deadline. Revision 2 makes cancellation best effort,
  handles rejection and releases the reader without delaying or replacing the
  original result. The same fix covers companion oversized and HTTP-error bodies;
  companion successful response reads also have an explicit three-second deadline.
  Actual WHATWG stream regressions cover cancellation that never acknowledges and
  cancellation that rejects. R2 author verification has 103 frontend cases and 65
  companion cases passing, with relevant lint, TypeScript and disabled-manifest
  checks. Unchanged native tests were not repeated. Original V1 source, the review
  hold and the unclassified native race evidence remain preserved. These changes
  do not bind the standalone companion client into the shipped MV3 popup/background.
- Independent R2 review confirmed the helper/client repair and held the two BFF
  HTTP-error branches, which still awaited backend-body cancellation. Revision 3
  applies nonawaited, rejection-handled best-effort cancellation to both branches
  while preserving their exact redacted 403/503 results. A complete pass over the
  new pairing sources found four cancellation call sites; all now avoid awaiting
  cancellation acknowledgement. Eight new actual NextRequest cases cover both
  routes, both statuses and never-acknowledged/rejected cancellation. Candidate
  cases use real encrypted NextAuth JWT and genuine route-issued CSRF inputs.
  R3 author verification passes 111 frontend cases, 65 companion cases, relevant
  lint and TypeScript. All original sources, review holds and race evidence remain
  preserved; unchanged native authority and signatures did not require a rerun.

## Remaining release checklist

- [ ] Protected native account/session issuance with genuine credential login, revocation
  and independent registration/lifetime acknowledgement; map its result to the private
  NextAuth claim without a legacy bearer bootstrap.
- [ ] Compose the production factory from explicitly pinned native/KMS/credential
  adapters after independent IAM, nonrestore/import policy and recovery review.
- [ ] Review native replay-record writer permissions and retention/expiry cleanup;
  current in-memory plans and publication-cohort limits are not a scalable deployment.
- [ ] Add distributed ingress/admission limits for anonymous device request preparation.
  A supplied Chrome Origin is not binary attestation or abuse prevention.
- [ ] Bind the claim to the actual MV3 popup/background and approved host permissions,
  add device checkpoint/restart handling and verify a connected browser/BFF/native flow.
- [ ] Support secure provider reauthentication for candidates with no local password.
- [ ] Connect separately reviewed packages, files, answers and per-field/action authority;
  independently verify employer permission and permitted portal behavior.
- [ ] Complete unknown-outcome reconciliation, customer unpairing UI, restore/load drills,
  mobile/browser acceptance and staged immutable deployment/monitoring.

None of those gates are closed by identity transport tests or an unavailable factory.
