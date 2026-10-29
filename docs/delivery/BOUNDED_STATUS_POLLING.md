# Finite payment and resume-upload status reads

This frontend-only candidate reduces one known metadata-quota pressure source.
It changes no API/OpenAPI response, account authority, payment terms, upload bytes,
scanner behavior or runtime activation flag. `ApiError` gains only an optional
client-side parsed `retryAfterMs` value.

## Payment status

After a hosted-checkout callback, each mounted owner/order has at most eight
automatic status GETs. The shared existing progress policy gives earliest offsets
0, 5, 15, 35, 65, 95, 125 and 155 seconds when every read remains pending. The
next delay starts after the previous response; slow requests cannot create a
catch-up burst. A duplicate callback does not repost the checkout result or reset
that budget. The existing mandatory status/terms GET after the explicit unified
Pay action remains separate and unchanged.

The explicit **Check payment status** action makes one GET. It does not restart an
automatic loop or replenish the automatic budget. Capacity errors stop automatic
checking; valid numeric/HTTP-date `Retry-After` establishes a per-order minimum
before any further automatic or manual read. Missing/malformed headers use a
conservative 60 seconds for HTTP 429 and 30 seconds for HTTP 503. A valid very long minimum
is not shortened: an unrepresentable delay keeps that flow paused rather than
using an overflowing timer. Other uncertain reads stop and retain status/support
recovery; no purchase or reconciliation POST is automatically replayed.

A hidden page stops the payment loop without an automatic visibility-resume or
catch-up. The owner can explicitly check after returning. Owner unmount aborts
the active status GET and wait; a late terminal result cannot update that owner's
state or initiate balance/history refreshes. Original terminal status handling,
quoted terms, unknown payment outcome controls and explicit reconciliation remain.

## Resume upload

An immediately released completion still returns without status polling. A queued
upload uses at most eight status GETs at earliest offsets 5, 15, 35, 65, 95, 125, 155 and 185
seconds, subject to longer Retry-After, native request latency and page visibility.
Both HTTP 429 and 503 honor their full server minimum; errors without a valid minimum
use the same conservative defaults. Once eight reads are consumed, the UI says
checks are paused and performs no more GETs before the original 240-second wait
ends. The existing bounded cancellation/cleanup and saved-result guidance remain.

A hidden page issues no status GET. On return, at most one future check resumes
only after a fresh adaptive delay, with no catch-up for missed intervals. The
original absolute deadline is rechecked before a resumed read; hiding does not
permit a later extra request. Owner cancellation still requests cleanup once and
prevents another GET, PUT or completion. The original file PUT is sent once.
Unknown completion responses reconcile through GET; only a verified owner-bound
`awaiting_upload` status can permit the existing bounded idempotent completion.
No new POST replay path was added.

The UI discloses the eight-read budget, increasing delays and hidden-tab pause.
Status is not proof of payment delivery or an employer submission.

## Capacity tradeoff and acceptance

Eight automatic reads mean at most 16 identity List RPCs for those reads, or 32
retained Get RPCs on the old validation path, over the finite flow. Under immediate
pending responses, the first 60 seconds contain four payment reads or three upload
reads. Existing creation, terms checks, callbacks, balances, native before/after
barriers, cleanup, manual actions, other tabs and other users add separate demand.
The shared 40-unit admission governor remains the enforcement boundary; this change
is not proof that a full journey or 10,000-user peaks fit it.

The tradeoff is up to 30 seconds between later automatic reads and explicit refresh
once payment checking pauses. Native processing, deliverable quality, payment
verification and authority checks are preserved. Local fake-time/component tests
cover finite counts, both payment paths, duplicate callbacks, long 429/503 minimums,
terminal/unknown cases, hidden/visible transitions and owner cancellation. Real
Upstash command metering, native metadata demand, deployed browser journeys and
end-to-end activation acceptance remain separate requirements.
