# Bounded native metadata GET retries

This is a resilience improvement for the release operator's existing
`SDKGoogleReads.get` reader. It does not establish the cause of the F5
`fence_native_read_unavailable` refusal or constitute a successful native
preflight, credential retirement, customer migration or deployment.

The fixed route allowlist, canonical URL construction, retained normal SDK
manager, TLS configuration, no-proxy policy and response validation remain in
place. The reader calls the same authenticated SDK session used by its stock
requests-backed apitools adapter. Each attempt still sends only the original
GET URL, no body and the no-cache header, with redirects explicitly disabled.
The shared `_http()` transport, write clients, once markers and unknown-write
reconciliation rules are unchanged. No retry adapter or session wrapper is
installed.

The reader permits at most three operator GET attempts within one three-second
monotonic observation budget. Each attempt receives only the remaining socket
timeout. A late response is refused; the budget is never restarted. Temporary
HTTP 429/502/503/504 may retry only after the existing bounded JSON-object checks
succeed. Explicit requests connection/connect-timeout/read-timeout/timeout
types may retry; SSL/proxy subclasses and nested TLS failures do not. Redirects,
permanent statuses, authentication/refresh failures, configuration errors,
untyped exceptions, oversized bodies, malformed JSON and duplicate JSON keys
stop without an added operator attempt. Exception text, credential-bearing
lookup URLs and server bodies are not logged or returned.

Backoff starts at 0.1 seconds and then 0.2 seconds. A valid Retry-After delta or
HTTP date can extend that wait within the original budget; it is never capped
to a shorter wait to force another attempt. Invalid Retry-After or a requested
delay that exhausts the budget refuses. The final result always comes from the
successful server response, never an earlier error payload or cached receipt.

The original 600-second retained-census acceptance bound, 30-second fresh closure
window, original observation timestamp and logical read limits are unchanged.
These limits are not asserted to count every physical wire request: the stock
SDK can perform its existing credential refresh/authentication challenge replay,
and the new retry limit counts only operator attempts. A socket timeout and
post-response deadline check are also not a hard process-return timer. Existing
SDK authentication work and executor shutdown can take longer; no result is
admitted by extending the original freshness window.

Synthetic focused controls cover transient success, exact attempts and residual
deadlines, Retry-After, unsafe/permanent refusals, unchanged query encoding and
GET arguments, shared transport identity, final-response provenance, and a
successful retry crossing the original closure window. Existing write replay,
cutover sequencing and rolling-deployment controls remain part of validation.
No cloud, customer database, credential-manager or provider calls are needed for
these tests. Actual native admission remains a separate release requirement.
