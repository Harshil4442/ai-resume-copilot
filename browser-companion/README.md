# HireWiz candidate-reviewed Chrome companion

The source package supports a Razorpay/Greenhouse text-assistance pilot. Production
stays closed until the backend release, migration 17, dedicated signer and explicit
feature flag are provisioned and accepted. Existing experimental native pairing
and independent-recovery factories remain disabled; this pilot uses the existing
signed-in Google candidate session and a retained nonextractable browser key.

The candidate chooses a saved application and resume, connects this browser by
reviewing its fingerprint on HireWiz, opens the exact company portal, and reviews
the file, destination, supported answers and disclosure before filling. The
backend issues a ten-second exact single-field signed lease only after approval.
Unknown acknowledgements stop the sequence without replay. Attachment, custom and
sensitive controls, CAPTCHA and final submission remain manual. Filling charges
no application credits and creates no application confirmation.

The shipping manifest has no granted hosts. It requests two optional origins,
HireWiz and Greenhouse; each action additionally enforces the exact Razorpay tenant,
selected numeric opening, current top-frame Chrome document and reviewed values.
It has no cookies, content scripts, external messages or broad host grants.

## Install the pilot

The HireWiz `/browser-companion` page provides the source-only ZIP. On Chrome
desktop, download/unzip, open `chrome://extensions`, enable Developer mode, select
Load unpacked and choose the extracted `hirewiz-browser-companion` folder. Pin
HireWiz. This is a pilot distribution, not a Chrome Web Store publication. Mobile
users complete applications directly on the company portal.

Use Node.js 24 and locked dependencies for source validation:

```sh
npm ci --ignore-scripts
npm run check
npm test
npm run test:assistance
npm run package:reviewed
```

`test:assistance` loads the actual MV3 package into owned temporary Chrome profiles,
locally fulfills every HTTPS request, and uses actual isolated SQL/crypto service
code. It verifies the Chrome-supplied extension Origin, explicit reviewed sequence,
no upload/submit/charge, lost-begin no replay, permission refusal and DOM change
boundaries. Narrow test-only granted origins avoid automating Chrome's human
permission dialog; that dialog and deployed BFF/API compatibility remain staging
acceptance work. These tests send no live employer or processor request.

The public ZIP is reproducible from source. Regenerate it whenever extension source
changes; no private key, session credential, test builder or candidate data belongs
in this artifact. See [the design decision](../docs/adr/007-reviewed-browser-assistance.md)
for expiry, cancellation and database-restore limitations. Existing local synthetic
and native experiment tests remain separate and do not constitute production proof.
