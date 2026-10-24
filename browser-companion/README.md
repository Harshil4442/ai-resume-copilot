# HireWiz candidate-reviewed Chrome companion

The 0.8.1 source package supports separately reviewed Razorpay/Greenhouse answers,
resume attachment and one final submit action. It adds bounded recovery for an
uncertain browser connection and pins the reviewed public KMS claim key with the
`companion_0_8_1` executor. Public pins and an UNCLAIMED inspection grant no account,
device or application authority. The matching backend extension record and runtime
configuration require a separate reviewed rollout; packaging alone does not
activate them. Normal candidates require genuine protected account enrollment,
current-password approval and native browser pairing.

The candidate chooses a saved application and resume, connects this browser by
reviewing its fingerprint on HireWiz, opens the exact company portal, and reviews
the file, destination, supported answers and disclosure before filling. The
backend issues a ten-second exact single-field signed lease only after approval.
Unknown acknowledgements stop the sequence without replay. Version two supports explicitly saved phone and four supported choices, plus candidate-entered Phone Country and known employment controls, behind its default-false flag. Missing personal answers remain manual.

Legacy file-selection attachment uses a separate version-three approval behind
`BROWSER_ASSISTANCE_ATTACHMENT_ENABLED=false`. Clear the answer review and inspect
the now-current form before reviewing an attachment. Approval binds the saved
original/custom/tailored artifact, exact bytes/hash/size/MIME/filename, device,
opening, document and empty Resume/CV input before any binary delivery. One signed
action authorizes one bounded binary delivery and one local file selection within
the same ten-second lease; it is never extended or silently resent. Selection can
immediately upload the bytes through employer callbacks. The result confirms only
local selection; employer upload acceptance is unconfirmed. The known hidden Resume/CV uploader and its transition are supported conservatively. Unreviewed or changed controls and CAPTCHA/login require the candidate. Neither
assistance action charges application credits or creates an employer receipt.

The shipping manifest has no granted hosts. The optional request envelope includes HireWiz, the two Greenhouse origins, native webRequest observation and AWS storage. The popup requests only the exact reviewed legitimate storage origin; each action additionally enforces the exact Razorpay tenant,
selected numeric opening, current top-frame Chrome document and reviewed values.
It has no cookies, content scripts, external messages or broad host grants.

The separately reviewed native v2 upload uses one private immutable multipart buffer and a genuine native direct HTTPS acknowledgement before invoking the known resume callback. A distinct v4 final review can bind one native direct HTTP 200 employer acknowledgement and settle exactly 20 reserved credits once. Parsed multipart events truthfully report owned-buffer provenance, not native raw-file visibility. Public thank-you DOM, 202, redirects, missing proof and lost outcomes remain unverified and never authorize resend. See [current acceptance](../docs/delivery/BROWSER_HOSTED_NETWORK_ACCEPTANCE_2026-10-15.md).

## Install the pilot

The HireWiz `/browser-companion` page provides the source-only ZIP. On Chrome
desktop, download/unzip, open `chrome://extensions`, enable Developer mode, select
Load unpacked and choose the extracted `hirewiz-browser-companion` folder. Pin
HireWiz. This is a pilot distribution, not a Chrome Web Store publication. Mobile
users complete applications directly on the company portal.

For an existing unpacked install, replace its source files and reload that same
extension. Keep the browser profile and extension storage; do not remove/reinstall
or clear its retained key to recover an uncertain connection. Choose **Inspect
uncertain connection**. A matching existing REQUESTED or CANDIDATE_CONFIRMED create
can offer **Resume this existing connection**, which reuses its exact public IDs
and never resends create. A failed prepare or strictly bound legacy 0.8.0 ID-less
unknown can offer **Archive unclaimed attempt** only after a current signed key
inspection reports UNCLAIMED in both authorities. Archiving preserves the prior
checkpoint; a new Start requires another explicit click. CLAIMED, unavailable,
unknown completion and unknown refresh stay blocked. The four-entry retained
history is never silently truncated; contact support when full. Nonces and private
keys are never copied into the connection checkpoint or archive.

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

Configured 0.8.1 ZIP SHA-256:
`174cc0b6170c1efeeb8b12ddd460734db89689f5860eca4ae4c6bdd894f9b18a`.

The committed `extension/config.js` contains the reviewed public pins, so the
ordinary package command and CI `--check` reproduce the configured native archive.
For a separately reviewed key update, `python3 scripts/package-reviewed.py
--native-config /absolute/path/to/reviewed-public-pins.json` accepts bounded JSON with
exactly `executorRevision: "companion_0_8_1"` and `authorityKey`, the actual public
P-256 claim JWK from the configured KMS version. Use the same argument with
`--check`. This replaces only public `config.js` bytes in the archive; it supplies
no account, device or action grant. Commit the same reviewed public values into
`extension/config.js` and regenerate the default archive before publishing an
updated package; the ordinary CI `--check` must also reproduce it.
Publish the digest of that configured build.
See [normal candidate activation](../docs/delivery/NORMAL_CANDIDATE_BROWSER_ACTIVATION_2026-10-15.md).

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
established by these local fixtures. The earlier F7/schema18 evidence is historical.
