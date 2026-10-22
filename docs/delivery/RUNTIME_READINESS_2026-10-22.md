# Native enrollment and tailoring failure diagnosis

Status: source implemented; deployment and complete customer journeys pending.

The existing owner session on the canonical website reports
`GOOGLE_ENROLLMENT_AVAILABLE`. This establishes existing Google authentication,
not a fresh OAuth callback or completed native enrollment. The zero-traffic
Cloud Run proxy repair fixes the observed HTTPS scheme while preserving the
unsigned native request refusal. Its signed availability check still failed.
Two bounded, temporary diagnostics retained the original guard implementations,
received no production traffic and were restored afterward. The second isolated
an issue timestamp 11,747 ms ahead of the native provider-clock lower bound.
See the [clock synchronization decision](../adr/008-private-ingress-clock-synchronization.md).
The original API revision remains the serving revision until genuine acceptance
and promotion. No account, password or device proof has been fabricated.

The owner's original PDF and previously approved tailored PDF each retain one
A4 page and nine annotations. Their existing previews/downloads succeed. A
fresh deterministic upload produced resume 29 with no AI unit change, and its
downloaded bytes exactly match the uploaded original. This does not establish
complete new tailoring or DOCX/LaTeX acceptance.

A single new tailoring run failed with `LLMProviderError` before applying document
edits. It returned the reserved AI units; the balance remained seven and no new
version replaced the original. The old generic UI incorrectly described that
failure as inability to preserve formatting. The new source distinguishes provider
unavailability and generation-budget unavailability from actual document refusals,
offers original/custom alternatives and exposes no raw provider response.
Legacy persisted provider failures also receive the correct guidance. An explicit
native-format reason remains authoritative over a generic exception class.

Future model-call events retain a numeric HTTP status only when obtained from a
real typed HTTP/provider exception, or a finite timeout/network/general code.
Response messages, prompts, resumes, credential values and URLs are never copied
into this error code. A failed provider response does not establish zero provider
expense: unknown-cost holds and bounded attempt accounting remain unchanged.
The historical run has no recoverable numeric provider response in its event;
do not invent a status or retry generation repeatedly to diagnose it.

No schema migration, credential change, model invocation, employer write or paid
activation is required to implement these source corrections. Roll out a tested
immutable application with existing customer state and finite credit controls
preserved. Then verify the actual deployed availability and enrollment flow.
Native upload, attachments, typed controls, final submission and checkout remain
held until their own acceptance. The same six original outcomes remain open.

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

## Subsequent staged verification

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
Actual staged signed availability remains required before traffic promotion.

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
new tailoring. Both source corrections are prepared for one release.
