# Bounded native metadata GET retries

The release operator uses the retained normal Google SDK for live metadata
observations. It must recover from ordinary transient reads without admitting
stale observations or replaying a mutation.

## Observed problem

The F6 cutover stopped during source quiescence with
`fence_native_read_unavailable`, before any mutation journal entry. Its exact
failed request was not captured. A separate read-only Linux diagnostic ran the
same admission check three times. Two rounds passed in 5.730 and 4.620 seconds;
the third reproduced the refusal after a Cloud Tasks task-list `ReadTimeout`
took 3.045 seconds. Successful Cloud Run execution-list responses took
2.578–2.815 seconds and contained 674,564 bytes. These observations identify
reader brittleness, rather than a missing permission or an altered database.

F6 used the same three seconds for the whole logical GET budget and its first
socket timeout. That left no practical retry opportunity after a normal socket
timeout, despite allowing three attempts in source. Retrying a complete release
without correcting this behavior is not a reliable recovery strategy.

## Correction and boundaries

The next reader separates a ten-second logical observation budget from a
three-second maximum socket timeout. At most three original GET attempts are
allowed. Neither a retry nor a new response restarts the original budget.
Caller deadlines further restrict the budget, and standalone admission checks
must also finish within their original thirty-second freshness window.

Canonical routes, query encoding, the retained normal SDK credential manager,
TLS verification, no-proxy behavior, no-cache header and disabled redirects
remain required. Responses must pass the existing size and JSON checks. A
successful result comes only from the final validated server response.
Conservative retry classification is retained: temporary 429/502/503/504 and
typed connection/timeouts may retry. SSL, proxy, authentication, configuration,
permanent HTTP and invalid response failures do not gain retries.

Backoff begins at 0.1 seconds, then 0.2 seconds. A valid Retry-After delta or HTTP
date may extend that wait, and is never shortened to force another attempt.
Invalid headers or a wait exceeding the remaining budget refuse. Fixed failure
classifications distinguish transport timeouts, HTTP failures, validation and
exhausted deadlines; credential-bearing URLs, parameters, server bodies and raw
exception messages must never leave the reader.

The shared `_http()` transport and all write clients remain unchanged. There is
no global retry adapter or session replacement. Existing mutation once markers
and unknown-outcome reconciliation remain mandatory. The original 600-second
retained-census bound, thirty-second closure, oldest observation timestamp and
complete-list predicates remain in force.

Socket timeouts and post-response deadline checks do not promise an exact
process-return timer: stock authentication and worker shutdown can take longer.
Their delay never makes an expired observation acceptable. Operator attempt
counts also do not describe the SDK's internal authentication challenge replay.

## Required verification

Focused tests must cover a full three-second timeout followed by recovery,
exhaustion of the original logical/caller deadline, Retry-After of three seconds,
no request after deadline, late-success refusal, strict response validation,
unchanged normal SDK/shared write transport, and the aggregate admission window.

Native admission, preserved customer/financial transfer, complete runtime
connectivity and production promotion remain unfinished. This fix and its
diagnostics are supporting work within the original deployment outcome.
