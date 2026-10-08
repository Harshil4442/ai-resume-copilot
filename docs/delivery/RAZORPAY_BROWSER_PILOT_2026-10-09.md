# Razorpay / Greenhouse browser pilot: public read-only review

Verified **9 October 2026, Asia/Kolkata**. Initial form/schema GETs were recorded at 2026-10-08 18:54 UTC (9 October 00:24 IST). This is a portal feasibility review, not employer enrollment, application execution or permission to automate.

## Recommendation and boundary

Use the exact Razorpay tenant and one current SRE opening as a **local synthetic fixture reference first**. A narrowly reviewed text-fill subset is feasible, but the complete live form exceeds the current 1–8 static-step core and includes unsupported/dynamic controls and sensitive choices. Production filling remains blocked until candidate/device pairing, independent online authority, exact tenant-use permission/source-policy review, reviewed processor/egress disclosure and a faithful adapter are established. Native file attachment and final submission remain candidate-controlled.

Only anonymous public GETs were used. No candidate values, cookies, login, file picker, autofill, selection changes, CAPTCHA interaction, POST, application action or employer submission occurred. Inspection used returned server HTML, embedded public configuration, documented GET schema and three referenced static JS assets. Browser execution was unnecessary and was not performed; observations about callbacks below are **static-code evidence, not executed network traces**.

## Exact employer-to-tenant proof

The official [Razorpay careers page](https://razorpay.com/careers/) “SEE ALL JOBS” link resolves to the [Razorpay Software Private Limited Greenhouse board](https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited). The board listed 27 openings when inspected. Its current [Site Reliability Engineer opening, Bengaluru](https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005) and public schema agree on post ID `4723040005`, employer and canonical URL. This verifies this company-to-board relationship, not other Greenhouse tenants or write authority.

| Binding | Exact value |
| --- | --- |
| Employer | Razorpay Software Private Limited |
| Platform/region | Greenhouse / global |
| Tenant token | `razorpaysoftwareprivatelimited` |
| Portal origin | `https://job-boards.greenhouse.io` |
| Selected opening path | `/razorpaysoftwareprivatelimited/jobs/4723040005` |
| Public read API origin | `https://boards-api.greenhouse.io` |
| Hosted submission destination, observed only | `https://boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005` |

The audited [registry](../../backend/resources/employer_sources.reviewed.json) already identifies this exact tenant and explicitly sets `submission_enabled=false`, `form_parity_verified=false`. Its `allowed_hosts=["razorpay.com"]` discovery metadata is not a browser grant for the portal. The payment-company footer on Razorpay.com does not change the employer identity observed on this board.

## Observed initial form and constraints

The public [job-question schema](https://boards-api.greenhouse.io/v1/boards/razorpaysoftwareprivatelimited/jobs/4723040005?questions=true) contains 13 required questions. IDs below are this opening’s current identifiers, not a tenant-wide reusable mapping.

| Required question | Field identifier / schema kind |
| --- | --- |
| First Name; Last Name; Email; Phone | `first_name`, `last_name`, `email`, `phone` / input text |
| Resume/CV | `resume` file OR `resume_text` textarea |
| LinkedIn Profile | `question_8970454005` / text |
| Website | `question_8970455005` / text |
| Total Years of Experience | `question_8970456005` / text |
| Current Career Stage | `question_8970457005` / single choice, 3 options |
| Gender | `question_8970458005` / single choice, 3 options |
| Current Industry | `question_8970459005` / single choice, 27 options |
| Current Designation | `question_8970460005` / single choice, 3 options |
| Current Location | `question_8970461005` / text |

Current Career Stage and Current Designation both expose Experienced Professional / College Grads–Fresher / Student–College Intern options. Gender offers Male / Female / Others; no decline option appeared in this response. This custom Gender question exists despite `demographic_questions=null`: demographic classification cannot be inferred from that API key.

The hosted [initial application HTML](https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/4723040005) adds **Country** and required repeatable **Employment**: company, title, start month/year, end month/year; optional Current role checkbox and Add another. These are missing from the questions array. Text limits are 255 characters; employment years 4. Email is DOM `type=text`; Phone is `type=tel`. Country, month and custom choices are searchable React-style `role=combobox` inputs, not native selects. Accepted file extensions: pdf/doc/docx/txt/rtf. No cover-letter or education controls appeared initially. Required state uses ARIA as well as framework validation, so checking only native `required` is insufficient.

**Conditional/static-code observations:** Current role disables end-date inputs and omits the end date from serialized employment. Education is configured hidden. Actual country option loading, dropdown interaction, date validation and add/remove-row behavior were not exercised. No exact retention period or candidate-specific policy link/checkbox appeared in the inspected initial form. API compliance flags require neither processing nor retention consent for this configuration; that does not establish absence of privacy duties or employer notices elsewhere.

## Earlier-than-submit disclosure and manual checkpoints

The referenced [current application client bundle](https://job-boards.cdn.greenhouse.io/assets/entry.client-ClhIqXFJ.js) establishes these code paths:

- Email blur can send the address in a GET query to `https://email-address-validator.us.greenhouse.io/address/validate`. Candidate data may leave the page while it is still an unsent form.
- Selecting a local file invokes its uploader immediately; the UI stores a resulting file URL for later submission. The bundle names this path as upload-to-S3 and checks a 100 MiB client limit, but the actual storage hostname, signing exchange, server limits and retention were not resolved or exercised.
- Google Drive and Dropbox use picker/integration flows. “Autofill my application” uses My Greenhouse. These are separate login/permission/data-sharing paths, not necessary for our fill-only subset.
- CAPTCHA is enabled in board configuration. The client loads reCAPTCHA Enterprise and supports a security-code/email-verification checkpoint after a CAPTCHA failure. No challenge or recipient identity was tested.
- The hydrated application code submits JSON with POST to the destination above. The SSR form’s `method=get` is not evidence that submission is read-only.

No general autosave mechanism was established by this bounded inspection; **absence was not proved**. Approval must precede any mutation/events because page handlers can disclose or autosave. Cancellation stops future companion steps; it cannot recall previously fired callbacks, validator requests or uploaded files.

**Required pilot blocker:** a board-origin binding alone does not describe recipients or disclosure behavior. Known processor recipients and behavior must be bound in the reviewed policy/review payload and explained before filling. The current v2 digest declarations do not verify actual page scripts or egress. A changing bundle/form/recipient graph needs a reviewed compatibility decision, not silent acceptance. Do not add broad cookie, debugger, network interception or wildcard host permissions to compensate. File upload and final submit remain manual, with an honest warning that manual attachment itself uploads before final submission.

Pause for login/account changes, My Greenhouse autofill, CAPTCHA/security codes, Drive/Dropbox, unsupported conditional controls, sensitive answers, changed form/package/expected-before values, navigation and uncertain outcomes. A required Website must be supplied or resolved by the candidate; never invent a URL. Gender is candidate-chosen and must not be inferred from resume/name/photo. Employment and experience must be candidate-confirmed, not guessed from ambiguous dates.

## Terms, privacy and source-use uncertainty

All following official pages were inspected on 9 October 2026:

| Official document | Supported conclusion and limit |
| --- | --- |
| [Razorpay Website Terms](https://razorpay.com/terms/rsl/) | Body is Website Terms of Use, without a visible effective date. Limits Razorpay.com to lawful personal/internal informational use; external commercial copying/distribution requires prior written consent. Prohibits unauthorized access, bypass and overburdening. Its scope cannot automatically be extended to the linked Greenhouse site. |
| [Razorpay Privacy Policy](https://razorpay.com/privacy-policy/), effective 23 February 2026 | Excludes linked third-party sites. No exact tenant-specific applicant retention/rights notice was established here. Razorpay-service AI/MCP text is not an application-automation grant. |
| [Greenhouse Privacy Policy](https://www.greenhouse.com/privacy-policy), updated 28 May 2026 | Employer/customer controls employer-hosted applicant data; Greenhouse processes it under customer instructions. The Greenhouse-company applicant notice is not Razorpay’s applicant notice. |
| [Greenhouse Job Board API](https://docs.greenhouse.io/job-board.html) | Documents public unauthenticated GETs and employer Job Board API-key authentication for application POST. Public read access grants neither browser automation nor employer write authority. |
| [Greenhouse MSA](https://www.greenhouse.com/master-subscription-agreement), updated 1 February 2026; [DPA](https://www.greenhouse.com/data-processing-addendum) | Customer/signed-contract scope; actual executed Razorpay terms are unknown. Customer API/internal-use clauses and the DPA are not candidate-side grants or exact retention evidence. |
| [Portal robots.txt](https://job-boards.greenhouse.io/robots.txt) | Public GET returned comments only, without active directives. Robots silence is not action or content-reuse permission. |

No explicit candidate-side automation grant was found in examined materials. This means **unestablished**, not a claim of an express universal prohibition. Before a live pilot, resolve applicable portal/source-use permission and exact candidate privacy/controller/processor disclosure with an authorized operator review. The user’s selected portal, public GET documentation, candidate Chrome consent, candidate content approval and employer/platform-use permission are distinct gates. Never turn API read proof into a submission credential or fuzzy tenant grant.

## Proposed minimum candidate permission and adapter boundary

Chrome permits [temporary activeTab injection or host access](https://developer.chrome.com/docs/extensions/reference/api/scripting) and [optional runtime host permissions](https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions). Prefer candidate-initiated temporary access where the reviewed protocol can support its lifetime; otherwise request only `https://job-boards.greenhouse.io/*` explicitly. Chrome [ignores path components for host permissions](https://developer.chrome.com/docs/extensions/develop/concepts/match-patterns), so this grant covers more than Razorpay: the adapter/authority must enforce the exact tenant path, opening, URL, top frame, document identity and form version before every step.

Do not request `<all_urls>`, `*.greenhouse.io`, employer API hosts, cookies, Drive/Dropbox, iframe-wide access or persistent automatic content injection. The page may call its own processor without the extension holding processor host permission; review must still disclose that recipient. A separately authenticated authority origin requires its own narrow transport permission if eventually implemented. Access must be rechecked after revocation; account-switch/unpair invalidates candidate review and device authority.

The [committed manifest](../../browser-companion/extension/manifest.json) has zero required/optional host grants and [configuration](../../browser-companion/extension/config.js) is disabled. No production pairing/authority was validated here. Initial hosted application is anonymous; do not manufacture a portal-account identity from a synthetic fixture marker. If a real login becomes involved, pause rather than reading/exporting session credentials.

**Proposed first static subset:** seven plain text controls—first/last name, email, LinkedIn, website, total experience, current location—each with candidate-reviewed exact value and expected-before state. The DOM email control must be described accurately as text with email semantics. Phone is `tel`, absent from current [v2 control types](../../backend/app/domains/recovery/sequence_contracts.py); custom comboboxes likewise need real adapter proof, not a native select fixture. Keep Country/Phone, employment, custom choices/Gender and attachment manual. Finish manual condition-setting before sealing the assisted subset. Do not promise automatic completion of all required fields or new companion attempts after an ambiguous partial sequence; stable opening ownership remains permanent.

## Faithful local fixtures needed before any integration

Hand-author structural fixtures with synthetic answers and local endpoints; do not redistribute the full employer description, branding or vendor bundle. Required proofs:

1. Exact public tenant/job binding, IDs/labels/ARIA/maxLength/types; API omits required Country/Employment. Duplicate/hidden/moved controls, wrong tenant on same origin, stale bundle/form fingerprint and altered recipient metadata must fail closed.
2. Controlled input behavior and React-style comboboxes, manual employment repeats/current-role end-date changes; unsupported controls are visible manual handoffs. One fixture must have more than eight required controls without pretending the core fills them all.
3. Local email-on-blur GET recorder and immediate file-upload recorder. Zero disclosure before exact review/current per-step begin; no automatic attachment, picker, Enter/default form action or Submit. Test that cancellation after a callback cannot erase recorded disclosure.
4. Required sensitive Gender and missing Website; no guessed values or prechecked consent. Conditional fields appearing after manual edits invalidate the sealed form context.
5. CAPTCHA, security-code, optional login/account-switch, Drive/Dropbox and autofill checkpoints pause without credential reads or bypass. Known configured CAPTCHA alone should be distinguished from an actual blocking challenge.
6. Permission/document/form/owner/device/epoch/approval changes between steps, lost begin reply and process restart after possible mutation. Preserve unknown/opening ownership, never replay or reinterpret filled fields as a receipt/refund.
7. Honest completion: “reviewed answers filled only,” remaining manual fields/file/submission listed; no applied status, employer receipt or application charge settlement.

Local fixtures can prove these implementation boundaries; they cannot prove employer permission, deployed independent-authority recovery, physical egress fencing or real portal account identity.

## Evidence

Public inspection files are under `/tmp/hirewiz-razorpay-public-review-20261009/`: fetch metadata, SSR control extraction, minimal observed settings and public response snapshots. No candidate/private data is present. Exact GET response hashes:

- Job HTML (69,731 bytes): `5cd20180c8d5d5bcec552a5eb3d580132c01fe6110d18a80eebd7cce9fd5591f`.
- Public questions JSON (15,602 bytes): `c22db6820f75c646f413e23f884f2b11a7dd1bf2d2c69f71e7c30b5a2f680cf0`.
- Referenced client JS (161,137 bytes): `2d91ca7cbf9d71723d90a17f9acce597de5a788a081059e13d02cad79b4db858`.

These are time-bounded snapshots. No source enrollment, permission manifest, application state, account, payment, database or deployment was changed.
