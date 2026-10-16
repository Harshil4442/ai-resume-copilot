# Candidate-reviewed Phone Country and employment controls

This is local, disabled, undeployed partial application development on
`codex/application-form-completion`, based on local commit
`fa12e999b863123adfe70c99901b89cfaf5733fe`. It does not establish full paid apply.
F7 and its schema18 deployment consumers are unchanged; schema19 requires a later,
separately admitted release.

## Actual public observation and limits

The saved read-only hosted observation and public questions response at
`/Users/harshil/.codex/hirewiz-evidence/20261015/current-razorpay-hosted-form-readonly.json`
and `current-razorpay-public-questions.json` establish that Country and employment
are required hosted controls absent from the public questions feed. A further
blank-page read-only inspection of opening `4740333005` confirmed:

- Country is inside Phone, with 244 country/calling-code labels and country flag
  codes, including `in` / `India +91`. It is not citizenship or work authorization.
- Company name and Title are text inputs, maxlength255, required.
- Start/end month controls are React Select with January through December labels.
- Start/end years are text inputs, maxlength4, required.
- `current-role-0_1` is an optional checkbox, name `current-role-0`, value `1`.
- Add another is `button.add-another-button`, type button, directly under the
  employment container.

Only dropdowns were opened and closed to read options; no option was selected,
no employment row/checkbox was changed, and no candidate data/file was supplied.
The country code is the observed flag-code identity; month values are canonical
visible-label tokens. Neither is represented as an unobserved provider API ID.
Actual conditional callbacks and a live added row have not been exercised. Those
behaviors are modeled only in owned local fixtures and remain a rollout gap.

## Review and action contract

The existing disabled-by-default `BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED=false`
flag gates this work. V1 remains seven authorized text fields; v2 can bind the
known profile values, Phone Country and up to five exact employment rows in one
review. Only undeployed migration19's bounded cursor increases from0–12 to0–64;
no new migration or protocol is introduced. V3 remains a separate, explicit,
one-action exact-file attachment review.

The public question feed cannot accept hosted answers as native application
answers. The signed-in candidate therefore enters hosted values on the same
review page and saves an account-authorized draft revision. That revision checks
owner, active device, source/application authority, flags, original expiry and
current review digest. It binds the exact known control descriptors, observed
values and explicit candidate answers into a new digest for subsequent approval.
It neither changes the sealed application answers/package nor extends expiry.
Stale application answer keys cannot supply hosted values. A cleared/no-op draft
can be saved but has no approve/run capability while it contains zero actions.

The companion first inspects known profile controls, discovers the bounded,
contiguous employment row identities/current-role state read-only, and obtains
the exact known hosted descriptors from the backend before the final inspection.
No new DOM action occurs during discovery. No employment, citizenship or work
authorization is inferred. Dates and choice IDs/labels are validated exactly.
Unsupported, ambiguous, reordered, hidden/disabled or changed controls stop.

Each fill uses the existing durable one-shot field claim, exact review/source/
DOM/document/value bindings and original short lease, now additionally capped to
review expiry. Country selection validates both the exact approved label and
observed flag identity. Year fields require their exact four-character control
bound. All callback drift, including a last text field's callback, becomes unknown
and requires inspection. A current-role toggle or Add another is one approved
click which always ends the current attempt for fresh inspection and review,
even if the callback does not visibly change the form. New rows acquire no old
approval and no previous draft answers are automatically reused. Lost or unknown
outcomes are never silently resent. Durable erased-payload v1/v2/v3 response
counts retain their respective7/64/1 bounds.

Filling and clicking can disclose/autosave values before final submission. Every
review states this and cancellation's existing short-lease limitation. This flow
creates no employer submission receipt, reservation, settlement or application
credit charge. Employer application status remains the original ready/manual
state in the owned fixtures.

## Verification

The final machine-readable evidence is
`evidence/2026-10-15-browser-hosted-controls-local-acceptance.json`.
Final results:283 backend tests,665 frontend tests in37 files,94 assistance checks
(33 installed MV3,47 adapter,14 runtime),76 companion unit tests,5 fixture tests
and14 legacy browser tests passed. Full lint/typecheck, Mypy121 files, Ruff,
OpenAPI regeneration and the43-page frontend production build passed.

The deterministic companion0.5.0 ZIP was built twice identically and checked
against exact runtime bytes. SHA-256:
`6a2cdd049d4049dc95a01219bba8c304881860dd45590e179877058b7370c97f`.
Old0.2/0.3/0.4 archives remain unchanged. Tests use synthetic owned SQL rows and locally fulfilled portal HTTPS
fixtures only, with actual installed MV3 request origins, signatures, service,
review revisions, action claims and Chrome document mutations.

Coverage includes two employment rows and Phone Country in one25-action review;
five rows in one real PostgreSQL38-action review; exact country flag/label tamper;
manual employment/year/Add-target drift before dispatch; current-role/Add clicks
stopping before another action; fresh reinspection with new empty row/changed
conditional controls; erased durable protocol metadata; stale digest, owner,
revocation, cancellation, expiry, feature flag and source failures; no-op/cleared
draft handling; source package preservation; zero charge and no receipt. Actual
full schema18→19 upgrade/downgrade/guards run on disposable local PostgreSQL only.
The F7/schema18 deployment rejection tests remain intact.

## Full journey still open

This step supports known controls locally. A later separately admitted schema19
release and native/browser rollout remain necessary. Actual employer conditional
compatibility, allowed authentication/permissions, upload acknowledgement and
recipient acceptance remain unproven. Unknown/unsupported controls, CAPTCHA and
login remain manual. Exact final-submit approval, one-shot submission, genuine
employer receipt/status reconciliation and paid-application settlement remain
open. No real candidate application was filled, uploaded or submitted, and no
provider writes, production changes, push, merge or deployment occurred.
