# Candidate browser transport repair, stage R2

The base is the exact frozen web V1 source: manifest
7e74d06c34774fca3d56bba5affd095459e38285b500870028f97460a6bb5c8e.
The independent review reproduced real bearer/context disclosure through generic
browser auth login paths, including encoded/dot/trailing-slash variants. It also
proved a caller-selected generic web-logout UUID could deny a session under a
foreign request and leave the genuine private logout cookie stuck. V1 source,
its positive tests and all independent negative evidence remain preserved.

This stage prevents authentication from being a generic forwarding operation.
The whole auth namespace is denied before credentials/body/cookie forwarding,
except four exact compatibility aliases for profile/me/export/delete. Those
aliases terminate in the same dedicated safe account handler; they cannot choose
an issuer or native lifecycle path and cannot stream an arbitrary backend reply.
Canonical path segments exclude traversal/encoding/separator ambiguity. The
remaining generic backend transport cannot follow redirects into other paths.

The dedicated `/api/account/<operation>` handler has a fixed operation/method
inventory: anonymous ordinary registration; authenticated profile GET/PUT, me
GET, account export GET and account deletion POST. It enforces exact origin and
real NextAuth double-submit CSRF for mutations, bounded input/response reads,
fixed errors and a server-held bearer. Profile/me/register/deletion replies have
explicit fields; account replies reject credential/capability fields rather than
returning stripped issuer payloads. Registration returns a status only and cannot
replace an existing account cookie. Exports intentionally contain owned account
records and have a named 16-MiB bound. Credential inputs use the existing 8-KiB
budget; profile updates use 64 KiB. Backend redirects are refused.

Existing account clients select dedicated routes and obtain actual NextAuth CSRF
before mutations. Native registration/status browser handlers use that same
CSRF boundary. NextAuth remains the credential issuer's server-side caller.
Account deletion now checks retained cookie logout completion before navigating
away, so a failed cookie denial confirmation is not hidden by the NextAuth
client's unchecked signOut response.

This stage does not certify a private backend ingress. Direct public native
credential endpoints must separately require a reviewed trusted-server assertion
with a distinct key, keyed body commitment, exact method/raw path/server origin,
<=5-second freshness, independent pinned OPEN custody and atomic shared replay
refusal. No credential-bearing body or unsalted password-guessing digest may be
retained. Legacy mappings entering native mode cannot downgrade on missing
transport authorization. The default native factory remains unavailable; full
web release remains HOLD until that ingress and its actual composition are
independently reviewed. No cloud secrets, policy or production resources change
in this repair.

Author evidence for this stage: 186 frontend cases, lint/typecheck/optimized build,
46 real cold HTTPS website checks, and unchanged independent adversarial probes
with 40 + 16 + 5 checks passed. The final logout probe skips its old vulnerable
200 branch because the generic request now returns 403; that branch is not counted
as an executed success. All four cold runners verified owned PG-schema/process
cleanup and deletion of their private TLS key. Candidate subjects/sessions came
from real credential/native/protected enrollment, not fixture-seeded identities.

The exact V1 baseline's existing Alembic logger configuration causes one logging
capture assertion to fail after a migration (110 passed, 1 failed). This failure
is preserved. Privacy-first ordering also yielded 111 passes as diagnostic data;
it does not establish compatibility with the original order. Root owns the
separately reviewed `disable_existing_loggers=False` correction. A separate
owned reconstruction on committed root 69fbc22 passed all 111 tests in that same
original order. The exact reviewed logger dependency is preserved by hash in the
compatibility manifest and does not become an authored repair in this R2 delta.

The default native password reset projection and browser-pairing action ports in
this captured V1 baseline remain unavailable, and this checkpoint makes no claim
about their newer separate implementations. Private backend ingress, independent
R2 review, root integration and deployment remain separate incomplete gates.
