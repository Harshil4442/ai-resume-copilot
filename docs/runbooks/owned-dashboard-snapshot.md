# Bounded owned dashboard snapshot

This source-only follow-on builds on evidence CAS320. It changes no active
release, quota, metadata governor, runtime flag, financial terms or model route.
It must receive its own complete application/CI/native qualification before a
reviewed rollout. Generation and sales gates remain unchanged.

The dashboard previously made four authenticated reads: `/auth/bootstrap`,
`/analytics/summary`, `/v1/opportunities?limit=8` and
`/v1/reminders?status=scheduled`. The new exact empty GET `/auth/dashboard` returns
one fixed advisory snapshot. Its matching dedicated account BFF accepts GET only,
uses the existing signed account bearer, bounds the response at two MiB and
rejects credentials/capabilities, malformed fields and client-selected scope.
Both server and BFF respond with private, no-store cache policy.

The backend retains original profile, feature, credit-availability and wallet
readers/validators. It adds owned SQL counts/average, the existing latest-resume
evidenced-skills/verification calculation, eight role previews and five scheduled
reminders. Previews contain only the fields actually displayed; they omit resume
raw text, job descriptions, match history, source files and action credentials.
Each query filters the authenticated owner. An attached User identity and current
credential/account subject are checked before and after the complete operation.
There is no flush, commit, new wallet, enrollment or action authorization.

Only recognized SQL connection/pool availability errors and explicit fixed-size
budget failures may mark a component unavailable. Unknown, malformed-state,
ownership, current-subject and authorization errors fail the whole snapshot.
Unavailable values are null with an exact unavailable list, never invented zeros
or empty results. The dashboard displays missing counts/progress as unavailable;
it withholds previous displayed data after a current snapshot refusal. Financial
offers require successfully read credit availability and wallet data.

Summary output is capped at one KiB; role previews at 32 KiB; reminder previews at
eight KiB. The latest resume's selected skills/sections have a two-MiB work budget;
no unbounded resume/match history is loaded. Existing profile/feature/credit
component budgets, including the 5,000-lot wallet bound, remain in effect.

Nav and Dashboard use the same snapshot query only on exact `/dashboard`. Its key
is a child of the existing owner-scoped bootstrap prefix, preserving credit/profile
invalidations. The advisory cache keeps the existing 30-second semantics; it is
never backend authorization. Actual requests still perform fresh native account,
session and revocation validation. Existing owner/logout QueryClient remounts
isolate this snapshot; late aborted responses cannot populate another owner.
Other pages retain their original bootstrap/read paths.

All existing endpoints stay available. Only an exact missing-route 404 permits the
new frontend to use the old reads. The BFF recognizes the backend's exact missing
route envelope; owned-reader 404s are not compatibility. Authentication/capacity
refusals, malformed snapshots and other dependency failures never trigger fallback
or automatic retry. The older-server fallback may make one unsuccessful route
probe plus the original four reads; it does not have the aggregation saving.

The optional owned credit-offer catalog remains separate: a compatible healthy
dashboard uses one read, or two when the offer catalog is needed. This is a
request-count reduction for the same displayed journey, not proof of production
capacity, latency, provider cost or the one-million-request monthly target.
At the unchanged 40 metadata units/minute and two Lists per ordinary validation,
the ideal 30-day ceiling remains 864,000 validations before other work.

Focused tests cover the actual Nav+Dashboard request count, no fallback on
auth/capacity/malformed responses, partial-error presentation, old-server
compatibility, original metrics, owner switching/logout with late responses,
foreign-row exclusion, bounded previews and zero writes. Backend tests traverse
the real account factory with intercepted SDK transports and scratch SQLite;
Get/List counters are protocol evidence, not native provider/IAM/capacity proof.
Real PostgreSQL, production ingress, latency and whole-journey peaks remain
future qualification requirements. No production capacity gate is activated.
