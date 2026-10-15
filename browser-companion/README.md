# HireWiz candidate-reviewed Chrome companion

The 0.4.0 source package supports separately reviewed Razorpay/Greenhouse answer
and resume-attachment assistance. Production stays closed until a later schema19
release, dedicated signer and explicit feature flags are accepted. The frozen F7
release remains schema18. Existing experimental native pairing
and independent-recovery factories remain disabled; this pilot uses the existing
signed-in Google candidate session and a retained nonextractable browser key.

The candidate chooses a saved application and resume, connects this browser by
reviewing its fingerprint on HireWiz, opens the exact company portal, and reviews
the file, destination, supported answers and disclosure before filling. The
backend issues a ten-second exact single-field signed lease only after approval.
Unknown acknowledgements stop the sequence without replay. Version two adds only
explicitly saved phone and four supported choices behind its own default-false
flag. Missing sensitive answers remain manual.

Attachment uses a separate version-three approval behind
`BROWSER_ASSISTANCE_ATTACHMENT_ENABLED=false`. Clear the answer review and inspect
the now-current form before reviewing an attachment. Approval binds the saved
original/custom/tailored artifact, exact bytes/hash/size/MIME/filename, device,
opening, document and empty Resume/CV input before any binary delivery. One signed
action authorizes one bounded binary delivery and one local file selection within
the same ten-second lease; it is never extended or silently resent. Selection can
immediately upload the bytes through employer callbacks. The result confirms only
local selection; employer upload acceptance is unconfirmed. Country, employment,
unsupported controls, CAPTCHA/login and final submission remain manual. Neither
assistance action charges application credits or creates an employer receipt.

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
one approved file selection and exact-byte synthetic upload callback, no final
submit/receipt/charge, lost responses without replay, permission refusal and DOM
change boundaries. Narrow test-only granted origins avoid automating Chrome's human
permission dialog; that dialog and deployed BFF/API compatibility remain staging
acceptance work. These tests send no live employer or processor request.

The public ZIP is reproducible from source. Regenerate it whenever extension source
changes; no private key, session credential, test builder or candidate data belongs
in this artifact. See [the design decision](../docs/adr/007-reviewed-browser-assistance.md)
for expiry, cancellation and database-restore limitations. Existing local synthetic
and native experiment tests remain separate and do not constitute production proof.
