# HireWiz browser companion foundation

This Manifest V3 package is disabled. It has no employer host permissions, production
authority, upload or submit action. Do not install the local fixture build as a customer
extension or enable it by changing configuration.

The standalone tests exercise exact review and per-field online authorization against a
synthetic localhost portal. They do not establish employer permission or production
integration. Production pairing, independent recovery authority and permitted adapters
remain required. See [the protocol and evidence](../docs/delivery/BROWSER_COMPANION.md)
and [the recovery design](../docs/delivery/RECOVERY_FOUNDATION_PLAN.md).

Use Node.js 24 and the locked dependencies:

```sh
npm ci --ignore-scripts
npm run check
npm test
npx playwright install chromium
npm run test:browser
```

The browser tests create isolated temporary profiles and exact ephemeral localhost
permissions. They never use real candidate accounts or employer applications. Test output
and screenshots are ignored. Backend and frontend deployment artifacts do not ship this
standalone extension.

## Browser identity connection

The shipped popup/background source now connects the pairing-only transport to
actual Chrome message handlers, IndexedDB device keys and a versioned local
checkpoint. The shipping configuration and manifest remain disabled. Activation
still needs the reviewed backend constructor, production signing/current-state
authority, release configuration and separate employer action permits.

When configured with that authority, the candidate allows the exact HireWiz
website origin, starts a connection in the popup, compares the browser-key and
request fingerprints on HireWiz, and confirms their password on the website.
Only the retained server-side session supplies candidate context; the popup
never receives the password, website cookies, session assertion or gateway key.
The candidate then explicitly completes the device proof in the popup. Identity
connection grants no fill, upload, submit or application-credit authority.

The P-256 signing key stays nonextractable in IndexedDB. Concurrent cold reads
use a single serialized retain-or-add transaction. The checkpoint contains only
public fingerprints, opaque request/device IDs and connection phases. A worker
restart retains those phases but drops in-memory claims. An interrupted or lost
mutation becomes `unknown`: restart, cancel and status inspection never permit
automatic retry. Pausing a known connection is local and does not claim remote
revocation. Resume is explicit; refreshing current identity requires a new
online device proof. A different retained device key or constructor binding
cannot resume the saved connection.

```sh
npm run test:native-runtime
```

These additional tests load the actual MV3 package into an isolated Chromium
profile and exercise keys, popup-only admission, checkpoint/restart behavior,
socket cancellation and permission checks over pinned HTTPS loopback TLS.
Their controlled server deliberately returns unavailable responses. They do
not constitute the genuine native password-approved connection proof.
Headless Chromium does not approve its native optional-permission dialog in
this harness; socket cases use an explicitly declared test-only manifest grant
for the exact loopback website. Human approval of the native permission dialog
is a separate acceptance check. `nativeFixtureIdentity` supports generating a
public manifest key/stable extension ID before backend fixture startup;
`nativeFixtureBuild` then pins the fixture's public claim JWK without copying
private signing material. Neither helper permits a non-loopback test site.
