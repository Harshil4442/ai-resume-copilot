# Native enrollment and tailoring failure diagnosis

> Historical report: this document records its dated observations, not the current
> deployment or remaining-work register. See [current pilot progress](AUTO_APPLY_PROGRESS.md)
> for the six original outcomes and latest accepted serving state. Later password
> reset, native enrollment and resume acceptance supersede their then-pending status;
> earlier failures and receipts remain preserved below.

Status: PR28 runtime repair and expense-policy-3 renewal are deployed and
canonically accepted. Model-policy secret version 2 is unchanged. Complete
customer journeys remain pending.

The accepted successor was tested at `efb35866dd850341e4f4f1f08da67323f5269d88`
and merged as `88ed491742030a8553c2419d9340a803b397e572`, with identical Git
trees. The API and analysis worker served immutable image
`sha256:c67d144ee7a02efa7df44ee78babf1ad3b562bc2f8d5c12e81a32804117eb64e`
at 100% canonical traffic, passed genuine staged/canonical verification, and
had their old runtime rollout tags cleaned. Vercel deployment
`dpl_A8eCkxaZjvK3d7rAVUKYTPodqQKV` is READY and canonical on both existing
domains, with the apex-to-www redirect retained. At **22 October, 20:42:41 UTC**,
the canonical account-availability endpoint returned HTTP 200 with exactly
`fresh_registration: true` and `legacy_enrollment: true`.

Fresh ordinary Google sign-in passed. Native Google verification reached the
owner, but human password submission, completed native enrollment and browser
pairing remain unverified. The owner reports companion installation; that is
not independent pairing acceptance. No account/password/device proof was
fabricated. See the [clock synchronization decision](../adr/008-private-ingress-clock-synchronization.md).

The owner's original PDF and previously approved tailored PDF each retain one
A4 page and nine annotations. Their existing previews/downloads succeed. A
fresh deterministic upload created a new source record with no AI unit change, and its
downloaded bytes exactly match the uploaded original. This does not establish
complete new tailoring or DOCX/LaTeX acceptance.

The earlier generic `LLMProviderError` happened before document edits and
returned its reserved units. That historical event did not retain a recoverable
numeric provider status; its exact cause remains unproved. The deployed source
distinguishes provider/budget unavailability from actual document refusals and
offers original/custom alternatives without exposing raw provider responses.
An explicit native-format reason remains authoritative over a generic exception.

One fresh post-repair owner-approved tailoring run reached Gemini and failed
with **HTTP 402** before a change plan or layout operation. AI Studio confirms
**Tier 1 Prepay / No credits** and requires a positive provider balance to resume
service. The two reserved product AI units were waived, the verified balance
remained **7**, and no new resume version was saved. No automatic retry or
provider purchase occurred. There is no provider usage receipt; retain the
unknown-cost outcome and its bounded accounting. This refusal does not prove a
formatting failure. Internal HireWiz test credits cannot fund Google's provider
balance. Successful truthful new tailoring remains unaccepted until provider
funding is available and a separately reviewed generation succeeds.

Future model-call events retain a numeric HTTP status only when obtained from a
real typed HTTP/provider exception, or a finite timeout/network/general code.
Response messages, prompts, resumes, credential values and URLs are never copied
into this error code. A failed provider response does not establish zero provider
expense: unknown-cost holds and bounded attempt accounting remain unchanged.
The historical run has no recoverable numeric provider response in its event;
do not invent a status or retry generation repeatedly to diagnose it.

The repair preserves schema, customer state, finite credit controls and the
existing five-second ingress expiry/custody/replay protections. Native upload,
attachments, typed controls, final submission and checkout remain held until
their own acceptance. Real employer writes and worldwide expansion are
follow-on work; the same six original outcomes remain open within the
owner-approved twenty-origin/Razorpay pilot.

Expense-policy secret version **3** is accepted on all three application
services; model-policy secret version **2** is unchanged. Publication, staged
verification, promotions, joined canonical acceptance and renewal-tag cleanup
all passed. The current revisions each serve 100% canonical traffic:

- `ai-resume-parser-expense-855b625c68d8`
- `hirewiz-analysis-worker-expense-855b625c68d8`
- `hirewiz-employer-worker-expense-855b625c68d8`

Prices, economic caps, service images and retained spending history are
unchanged. The finite expense-policy expiry is
**29 October 2026, 20:28:54.855717 UTC**. Retained expense-policy-2 revisions
are not usable after **23 October, 05:07:52 UTC** unless the rollback
configuration attaches a valid reviewed expense policy. Keeping an old revision
does not extend its policy authority. Estimated expense policy is not proof of
actual invoices or profit.

The local release audit also identified published Next.js and Sentry advisories.
The lockfile now includes patched Next.js 16.4.2 and a Sentry 10.x release at least
10.75.2, with matching Next.js lint configuration. The subsequent production
dependency audit has zero critical findings; seven lower-severity dependency
findings remain and are not presented as a clean security audit. These routine
dependency patches do not activate a new service or change the sales hold.
Primary advisories: [Next.js](https://github.com/vercel/next.js/security/advisories/GHSA-2464-fhr4-w9xw)
and [Sentry](https://github.com/getsentry/sentry-javascript/security/advisories/GHSA-mchm-gjx6-7h55).

Local validation: 109 focused backend tests passed; two native-layout integration
cases were excluded from this focused run because the local native image report
was not provisioned. The complete CI native-image job must cover those cases.
Backend Ruff and Mypy passed for the changed modules. Frontend lint, type checking,
all 827 tests and the optimized production build passed using a synthetic local
build secret. Independent read-only review found no actionable clock-sync issue.
These checks are supporting evidence, not completed production outcomes.

## Retained failed staging and accepted correction

The full five-job CI run for source `1ccfdc8` passed, including its native-image
checks. Both API and analysis candidates were then staged at zero traffic and
passed their real private health checks. The API also passed the HTTPS redirect
and unsigned-request refusal checks. Its signed availability handshake failed:
the first HTTP 503 supplied a bounded clock hint, but the corrected second
request also returned HTTP 503. Production traffic was not moved.

A separate bounded diagnostic retained both responses. Their lower time bounds
were `1792696561693` and `1792696541968` milliseconds: the second went backward
by **19,725 ms**. This disproves the assumption that successive Firestore
document `readTime` values provide the fresh wall clock required by the ingress
guard. The [API contract](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases.documents/batchGet)
describes document read timestamps; strong consistency establishes data
freshness and does not provide the assumed clock bound. Repeating the same
CI fixtures or applying another clock offset cannot establish that guarantee.
Keep the failed verification and diagnostic as failures, not release permission.

The replacement keeps the fresh control/identity/custody reads and anchors time
to a complete fresh server acknowledgement. It must retain the existing expiry,
monotonic bounds and durable replay checks. Regression evidence must cover
backward read timestamps and absent, malformed, late or unknown acknowledgements.
The successor retained those requirements and passed genuine signed staged
availability before canonical promotion. The earlier failed verification remains
intact; it was not relabeled as a successful release.

## Gemini request compatibility

The selected Gemini 3.6 model received `candidate_count=1`, although Google's
[Gemini 3.6 migration guide](https://ai.google.dev/gemini-api/docs/generate-content/whats-new-gemini-3.6)
requires omitting that unsupported field for Gemini 3 and later. The source fix
omits it for Gemini 3+ and aliases, retaining the single-candidate setting for
known older Gemini 1/2 models. Output-token and durable budget limits are unchanged.
Focused regression checks inspect actual SDK wire serialization for the four
configured model choices; 199 focused tests, Ruff and Mypy passed without a
provider generation request. This is a definite request-contract correction,
not proof of the exact cause of the historical generic failure or of successful
new tailoring. Both corrections are now in the accepted PR28 release; its fresh
tailoring observation is the provider HTTP 402 described above.

The subsequent release audit reported a newly indexed DOMPurify advisory,
[GHSA-cg2g-xh64-v4q7](https://github.com/cure53/DOMPurify/security/advisories/GHSA-cg2g-xh64-v4q7).
The targeted lockfile update replaces transitive DOMPurify 3.4.16 with patched
3.4.17 without changing other dependency versions. A clean installation and
parent constraints pass, along with 222 focused rendering/security tests and
TypeScript. The actual production audit again has zero critical findings;
five high and two moderate findings remain. This is a patch for the observed
release blocker, not a claim that the entire dependency audit is clean.

The exact patched PR28 head passed the complete five-job CI and immutable native
build before promotion. These checks support the deployed repair; they are not
new development checkpoints or complete enrollment, pairing or tailoring
acceptance. The [original six-outcome progress report](AUTO_APPLY_PROGRESS.md)
remains the reporting authority.
