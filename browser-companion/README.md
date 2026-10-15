# HireWiz candidate-reviewed Chrome companion

The 0.6.0 source package supports separately reviewed Razorpay/Greenhouse answer
and resume-attachment assistance. Production stays closed until a later schema19
release, dedicated signer and explicit feature flags are accepted. The frozen F7
release remains schema18. Existing experimental native pairing
and independent-recovery factories remain disabled; this pilot uses the existing
signed-in Google candidate session and a retained nonextractable browser key.

The candidate chooses a saved application and resume, connects this browser by
reviewing its fingerprint on HireWiz, opens the exact company portal, and reviews
the file, destination, supported answers and disclosure before filling. The
backend issues a ten-second exact single-field signed lease only after approval.
Unknown acknowledgements stop the sequence without replay. Version two supports explicitly saved phone and four supported choices, plus candidate-entered Phone Country and known employment controls, behind its default-false flag. Missing personal answers remain manual.

Attachment uses a separate version-three approval behind
`BROWSER_ASSISTANCE_ATTACHMENT_ENABLED=false`. Clear the answer review and inspect
the now-current form before reviewing an attachment. Approval binds the saved
original/custom/tailored artifact, exact bytes/hash/size/MIME/filename, device,
opening, document and empty Resume/CV input before any binary delivery. One signed
action authorizes one bounded binary delivery and one local file selection within
the same ten-second lease; it is never extended or silently resent. Selection can
immediately upload the bytes through employer callbacks. The result confirms only
local selection; employer upload acceptance is unconfirmed. The known hidden Resume/CV uploader and its transition are supported conservatively. Unreviewed or changed controls and CAPTCHA/login require the candidate. Neither
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
one approved file selection and exact-byte synthetic upload callback, a separately approved one-shot final click, no fabricated receipt/charge, lost responses without replay, permission refusal and DOM
change boundaries. Narrow test-only granted origins avoid automating Chrome's human
permission dialog; that dialog and deployed BFF/API compatibility remain staging
acceptance work. These tests send no live employer or processor request.

The public ZIP is reproducible from source. Regenerate it whenever extension source
changes; no private key, session credential, test builder or candidate data belongs
in this artifact. See [the design decision](../docs/adr/007-reviewed-browser-assistance.md)
for expiry, cancellation and database-restore limitations. Existing local synthetic
and native experiment tests remain separate and do not constitute production proof.

The current disabled v2 draft can bind the known Phone Country selector and up to
five employment rows in one review (bounded cursor 0–64 in undeployed schema19).
The candidate saves exact hosted values on the same review page, then explicitly
approves its updated digest; no citizenship, work authorization or employment is
inferred. Add another and current-role toggles receive one action lease and end
the sequence for inspection; neither is replayed automatically. See
`docs/delivery/BROWSER_HOSTED_CONTROLS_2026-10-15.md` for local-only evidence.


## Separately approved final submission (disabled)

Version four is separate from every fill/attachment approval and requires
`BROWSER_ASSISTANCE_SUBMIT_ENABLED=false` to be deliberately enabled only with the
independently protected native production inputs in
[the native handoff](../docs/delivery/BROWSER_SUBMIT_NATIVE_AUTHORITY_2026-10-15.md).
A genuine retained candidate credential lifetime and explicitly provisioned exact
device are required; SQL rows are never automatically adopted.

After the separately approved file selection, wait for the portal to report the
matching uploaded filename in the same Chrome document. Clear the earlier review,
then choose Review final submission. The final review binds every supported current
answer, candidate, company, exact job/destination, original/custom/tailored source,
artifact bytes/hash/size/MIME/name, upload provenance, current portal context and
20-credit policy. Approval reserves 20 credits; a durable possible-send SQL attempt
precedes independent native authority and a single ten-second submit lease. The
companion invokes only the known native Submit application button, once. It never
constructs a raw employer POST or bypasses candidate CAPTCHA/login/terms.

Missing/lost outcomes never resend. Read-only inspection can report confirmation,
validation and candidate checkpoints, but those observations cannot establish an
employer receipt. A public confirmation URL is reachable without submitting.
A complete authenticated employer acknowledgement under an accepted receipt
contract is required for settlement; otherwise the 20-credit hold remains for
independent reconciliation. Before any send claim, cancellation/expiry refunds the
hold. After a claim it cannot promise an undo or refund without proof. See
[official receipt research](../docs/delivery/GREENHOUSE_CANDIDATE_RECEIPT_RESEARCH_2026-10-15.md).
No production activation, live application, provider receipt or paid completion is
established by these local fixtures. F7/schema18 stays frozen.
