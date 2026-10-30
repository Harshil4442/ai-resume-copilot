# Owned common-read consolidation candidate

This is source preparation for the separately reviewed app25-v2 release. It does
not activate List validation, metadata admission, AI, credits, checkout or submit.
It is not a 10,000-user capacity qualification or provider/emulator acceptance.

## Contract and ownership

`GET /api/auth/bootstrap` uses the same signed bearer and `get_current_user`
native account/session validation as existing owned GETs. It accepts no query,
body or encoding. The exact BFF target is `GET /api/account/bootstrap`; it cannot
select another actor, destination or lifecycle operation. No private-ingress
replay/clock path, wildcard grant or new authority scope is added. Existing
endpoints remain available. Every actual request independently authorizes;
no successful UID, witness, session or authorization survives the request.

`owned-bootstrap-v1` returns the existing profile view, feature decisions,
credit availability and complete owned wallet, plus explicit unavailable
component names. A failed component is null and marked unavailable, never a
successful zero balance. It cannot hide a successful profile behind unavailable
pricing. Reading a wallet does not enable purchases or convert legacy balances.
All feature, canary, financial qualification, quote and mutation gates remain.
The strict BFF projects known response fields and refuses private authority.
HTTP responses are private/no-store.

Release the additive backend before the new frontend. Old clients remain
compatible with the new API. The new frontend requires this route: restoring an
older API without it also requires restoring the corresponding prior frontend,
otherwise the shared read correctly fails closed. A backend-only traffic rollback
does not prove this new frontend works against the old API. Preserve and review
the paired frontend rollback context before promotion; no silent legacy HTTP
fan-out is included in this candidate.

The pure profile projection reads the real owner and optional profile together.
Missing profile fields use the existing response defaults without inventing an
ORM row, persisted ID or approval. The existing profile GET now uses this read;
the profile PUT retains its explicit write helper. Common reads never create a
profile, wallet, quote or grant, and check actual owner/credential/account
identity before and after component reads. Concurrent erasure or generation,
state or binding drift withholds the response.

Finite component response bounds are 64 KiB profile, 16 KiB features, 64 KiB
availability and 1 MiB wallet. The complete wallet query reads at most 5,001
active lots and refuses the wallet component above the existing client-supported
5,000-lot bound. It never truncates lots or changes the balance calculation.
Other components remain readable. Existing standalone wallet reads retain their
prior default behavior. The BFF total response cap is 2 MiB.

## Client requests

Nav and common profile/features/availability/wallet hooks use one raw owner-scoped
React Query key and typed observer projections. Imperative profile/wallet callers
explicitly project the raw result because `fetchQuery` does not apply `select`.
Profile writes update only the profile field of the snapshot. Session owner
changes retain the existing QueryClient/page-draft remount boundary; a late old
reply cannot hydrate another owner. Companion pages still suppress unrelated
header reads. Advisory freshness remains 30 seconds; this caches deliverables,
not authorization. Each later HTTP read obtains fresh native checks again.

The billing wallet uses this shared query rather than a duplicate direct fetch.
Completed workspace runs refresh the common snapshot once through the existing
terminal tracker; overlapping profile/wallet invalidations no longer cancel and
restart the same physical read. Opportunity list keys include limit, offset,
stage, search and archive selection, so dashboard limit 8 data cannot substitute
for workspace limit 200 data. Prefix mutation invalidation remains supported.

Cold production-mount source counts, excluding pending work, retries, selected
panels and public reads:

| Surface | Before authenticated reads | After |
| --- | ---: | ---: |
| Dashboard | 6 (up to8 with unified offers) | 4 (5 with offer catalog) |
| Workspace list | 5 (6 with wallet) | 3 |
| Workspace detail base | 5 (plus active-tab/result reads) | 3 (same conditional reads) |
| Resume upload | 3 (4 with wallet) | 1 |
| Employer jobs | 7 (8 with wallet) | 5 |
| Billing | 12 (13 with duplicate Nav wallet) | 9 |

The installed production composition over intercepted SDKs measures four
GetDatabase calls and 18 Storage HTTP reads per ordinary validation. Reviewed
List batching separately reduces those to two Lists and the same 18 Storage
reads. Consolidating three/four independent account reads into one therefore
removes two/three complete validations. These are source/fixture call counts,
not live latency, IAM, quota, Redis or invoice observations.

## Remaining polling demand

`readQueryPolicy.ts` and `useProgressQuery.ts` cap active analysis, unified
discovery, application and batch progress at eight successful reads. Ignoring
request duration, an immediate stream reads at 0,5,15,35,65,95,125,155 seconds:
four reads in its first minute, costing eight List units or sixteen default Get
units. Three active workspace streams can consume 24 List/48 default Get units
in 35 seconds, before page, result or terminal refresh reads. Employer search
preparation uses the same bounded delays, normally starting after five seconds.
Hidden pages and capacity errors stop those automatic polls.

The separately qualified legacy and unified payment-status loops now permit
eight adaptive automatic reads at 0,5,15,35,65,95,125,155 seconds absent request
duration, plus one explicit manual status read. The historical 24-at-2.5-second
estimate is superseded. Hidden/capacity/unknown states pause automatic reads;
unknown outcomes require status/reconcile, never another payment or application
submission. Aggregate demand still needs measurement; see
`STAGE5_OWNED_HTTP_CAPACITY.md` for the bounded owned-local plan and its limits.

The current companion popup `status`/`pairing_status` path reads local runtime
state; no automatic HTTP polling timer was found there. Review-page timers only
expire UI state. Explicit connection inspection, signing and assistance actions
still use their existing independent Get barriers and need measured accounting.

At 40 metadata units/minute, even two-List validation has a no-other-traffic
ceiling of 864,000 validations per 30 days. Common-read grouping reduces actual
requests per journey; it does not redefine or qualify the million-request goal,
peak concurrency, provider quota or Redis allowance. Both activation flags remain
false until the complete reviewed scope has actual evidence.

## Verification boundary

Local tests cover genuine bearer/native composition with intercepted SDKs,
one validation per request and freshness on the next request; no SQL writes;
faithful absent-profile defaults; two-owner and mid-read erasure/credential
isolation; finite complete-wallet refusal; partial component errors; strict BFF
target/response limits; one shared HTTP call; owner changes, late replies and
unmounts; last-known balance/error state; opportunity-limit navigation; and a
delayed terminal refresh that must remain a single request. These synthetic
tests do not substitute for actual emulator/provider or app25-v2 compatibility
qualification. Existing frozen native wire and renderer sources are unchanged.
