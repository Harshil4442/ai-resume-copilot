# Browser companion: disabled MV3 safety foundation

Date: 2026-10-08. Scope: local synthetic, fill-only development evidence for
EX02 and parts of EX05–08 / G04A–D in
[`JOB_SEARCH_AND_APPLICATION.md`](../requirements/JOB_SEARCH_AND_APPLICATION.md).
This is not a production employer adapter or a completed Stage 4 gate. Stage 5 automatic
submission remains outside this implementation.

## Delivery boundary

`browser-companion/extension` is a standalone Manifest V3 extension. Its committed
configuration is `disabled`; it has **zero required or optional host permissions**,
no content scripts, external-message entry point, web-accessible resources, cookie
permission or network authority. Its popup explains the unavailable production
authorization and disables the actions. A manifest/configuration check and a real
Chromium test verify that disabled package.

The fixture builder creates a separate temporary extension for exact
`http://127.0.0.1:<ephemeral-port>` origins. Only this build can communicate with the local
synthetic authority and inspect `/fixture/apply`. It is never a production installation
artifact. The test profile receives those exact local grants; it does not receive a
wildcard host grant. Host permissions alone do not enforce employer tenants or URL paths,
so the protocol and adapter independently check tenant, employer, opening, origin, exact
URL and reviewed document identity before each field mutation.

No real employer was contacted. No candidate account, password, browser session, payment,
application upload or final submission was used. The four-field form and file bytes are
synthetic. The small PDF-labelled fixture tests byte identity; it is not a resume rendering
or ATS-readability test.

## Local user flow

1. The fixture popup allows only the configured local origins and connects a device using
   the explicit synthetic owner grant. A non-extractable P-256 private key stays in the
   extension's IndexedDB; only its public key and fingerprint reach the fixture authority.
2. Inspect requests a short-lived signed command and the exact sealed file. The extension
   independently checks the package digest, file size and SHA-256, then the current
   top-level form schema, owner, tenant, employer and opening. Review is ephemeral.
3. The candidate sees the destination, owner/device, native filename/type/size/hash,
   every answer and every explicit consent. The exact bytes can be downloaded for review.
   Nothing is filled during inspection. Approval is disabled until the candidate checks
   the disclosure acknowledgement: input/change events can autosave before final submit.
4. Approve binds the exact review digest and the only allowed action, `fill`. Each field
   requires fresh online authorization and a single-use begin decision. The worker stores
   `action_in_progress` before begin or DOM mutation. The isolated adapter receives only
   the current approved field value, rechecks the same live document/form and a short
   deadline, changes that field, and dispatches input/change events.
5. Completion says **answers filled only**, and explicitly says no application was
   submitted. File upload and the final employer button remain manual; neither operation
   exists in this adapter. A human final click cannot be reported as verified automatic
   completion by this foundation.

Login, CAPTCHA, MFA and assessment markers pause the local fixture without reading
credentials or bypassing those steps. A changed or newly conditional question stops later
fields and requires a new exact review; previously filled answers may already have
autosaved. This marker-based fixture detection is not a general employer checkpoint
detector.

## Authorization and state contract

The fixture transport uses fixed local operations, `credentials: omit`, no redirects,
no positive authorization cache, a fresh single-use server challenge, device-signed
requests and a pinned ephemeral issuer key. It has no general URL proxy and exports no
cookies or passwords. Commands are valid for at most two minutes, device claims for five
minutes, action permits for ten seconds and fixture begin proofs for two seconds.
Device possession is a software-key proof, not hardware attestation.

Commands and every signed action proof bind:

| Binding | Checked value |
| --- | --- |
| Candidate/device | Owner ID, device ID and public-key fingerprint |
| Destination | Exact origin, employer tenant, employer key, canonical opening and application |
| Review | Sealed package digest, exact artifact SHA-256, approval ID/revision and review digest |
| Action | `fill` only, exact field ID, command ID and one-use action ID |
| Freshness | Current recovery epoch, issue/expiry deadlines and online current permission |

`review_digest` additionally includes the exact target and form schema. A valid signature
does not bypass semantic binding, schema, package-digest or exact-file checks. Cached
commands, device claims and approval alone never authorize a field. The synthetic
authority checks owner, device, tenant permission, cancellation, epoch, approval revision
and package again when authorizing and consuming begin.

| Local checkpoint | Meaning and recovery |
| --- | --- |
| `action_in_progress` | Begin or a possible field write is in progress; persisted before disclosure |
| `field_filled` | The one field has a completion acknowledgement; further fields still need fresh authorization |
| `filled` | All reviewed fixture answers filled; no upload or submission |
| `paused` / `cancelled` | Further work stopped; earlier autosave may remain |
| `unknown` | Begin response, mutation or completion outcome is uncertain; no automatic retry |

Only IDs, status and counts enter persistent checkpoints; answer text and file bytes do
not. Lost begin responses never return a cached may-act token. A restart reading
`action_in_progress` or `unknown` refuses a new prepare. Cancel after restart preserves
that uncertainty instead of overwriting it. Re-enrollment does not erase the checkpoint.
There is no automatic reconciliation or “clear uncertainty” control in this foundation.

Cancellation stops subsequent fields when it wins before the current begin/write boundary.
The fixture atomically consumes each action once; it cannot recall a field that has already
begun or erase a portal's autosave. The popup reports this limitation. A paused field
already authorized to begin has only its short remaining deadline; future production
authority must preserve the same bounded, explicit race semantics.

## Evidence and repeatable checks

From `browser-companion/`, using Node 24:

```sh
npm ci --ignore-scripts
npx playwright install chromium
npm run check
npm test
npm run test:browser
```

The browser suite launches a fresh persistent Chromium profile with a temporary unpacked
extension and localhost fixture per case. It deletes the profile/build afterwards and
never connects to an employer. The protocol suite covers 55 passing cases: exact approval,
signature and binding mismatches, signed but inconsistent package data, file hashes,
250 KB/5 MB byte round trips, online revocations, offline refusal, permission/document/form
races, conditional questions, duplicate approval, one-use replay, cancellation, lost
begin/completion and restart checkpoint preservation.

The actual MV3 Chromium suite has 14 passing cases covering exact answer review before autosave, non-extractable
device signing, absence of exported portal cookies, zero submit events, four human
checkpoints, six post-review owner/form/authority races, pending-begin cancellation,
full browser restart with the same persisted device and unknown checkpoint, and the
committed disabled package. Generated review/completion screenshots are local ignored
test artifacts under `browser-companion/test-results/` (or `COMPANION_SCREENSHOT_DIR`).

These are local safety proofs, not production independence, real employer permission,
provider receipt, upload, broad checkpoint detection, recovery or throughput evidence.
Chrome origin revocation is modeled in protocol tests; the actual browser fixture has
explicit local test-profile grants. Production optional permission revocation still needs
its own release test with the final enrollment/adapter design.

## Required production work before enabling

- Authenticated owner/device enrollment, expiry, unpairing and account-switch handling;
  secure release issuer/key rotation and TLS authority endpoints. The synthetic grant is
  not an authentication mechanism for customers.
- Durable current authorization and independent recovery epoch/action authority,
  cancel/begin serialization, deletion/revocation tombstones, action journals and unknown
  reconciliation. The in-memory local authority demonstrates contracts only and must not
  be substituted for that service. Follow
  [`RECOVERY_FOUNDATION_PLAN.md`](RECOVERY_FOUNDATION_PLAN.md).
- Integrate immutable application/admission reservation, price/policy snapshot, artifact
  generation, approval version and any batch binding with the backend admissions domain.
  This standalone fixture has no production admissions or credit-ledger integration.
- Tenant-specific authenticated operator grants and authorized adapters, minimum optional
  origin permissions, exact page/job/account identity, real conditional-field and human
  checkpoint handling, file support, accessibility and browser lifecycle evidence.
  Unavailable grants must keep the adapter disabled.
- Independent browser security review: malicious pages, hostile frames, sensitive-field
  refusal, data minimization, browser-profile compromise limits, ownership/revocation,
  races/restarts and permission cancellation. No generic form filling or host-wide access.
- Any future automatic final submit requires a distinct action, fresh final launch permit,
  cancellation and unknown-outcome protocol, tested provider confirmation evidence and
  all applicable G05/recovery gates. This extension has no such submit action.

The extension is intentionally not connected to production or enabled by changing a flag;
the missing authority, grants and adapters require implemented and verified integration.

## Primary implementation references

- [Chrome activeTab](https://developer.chrome.com/docs/extensions/develop/concepts/activeTab):
  temporary user-invoked access is distinct from persistent host grants; production must
  choose the narrow permission model matching its verified adapter flow.
- [Chrome match patterns](https://developer.chrome.com/docs/extensions/develop/concepts/match-patterns):
  narrow origin patterns and permission paths do not replace application-level tenant checks.
- [Chrome extension security](https://developer.chrome.com/docs/extensions/develop/security-privacy/stay-secure):
  restrict privileges, treat page/content-script input as untrusted and protect sensitive data.
- [Playwright Chrome extensions](https://playwright.dev/docs/chrome-extensions):
  persistent Chromium contexts with unpacked extension loading for local tests.
