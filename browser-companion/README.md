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
