# Explicit Neon direct identity contract

The reviewed source baseline is `88d410e51939b818858b2cef6019f33714bd7e57`.
The private NOLOGIN preparation overlay retains its original scoped schema9
policy baseline `1c2519bc3989c60405dce5154d8e6bd73067d826`; that constant is
not the self-hash of this candidate or an execution/deployment approval.

The original read-only metadata capture refused `preparation_authenticated_actor_mismatch`.
A subsequent independently reviewed one-query diagnostic found the protocol PID
positive-shape predicate false, while user/current/session/version, CREATEROLE,
read-only transaction and native control/cluster/database/public identity checks
passed. Its `protocol_pid_matches_sql` flag was gated by the failed shape check;
it does not independently establish numeric inequality, sign or the raw PID.
No roles or credentials were changed. Both observations remain retained.

Primary Neon proxy source at `fa504217c61bbcaf5c512d75830564541f917f8f`
replaces BackendKeyData with a random cancellation-routing key; libpq records
its first four bytes as a signed C `int`. This is cancellation routing data,
not authenticated compute PID proof. That upstream commit has not been proven
to be the deployed proxy commit. The ordinary PostgreSQL contract continues to
require a positive protocol PID equal to the native `pg_backend_pid()`.

Only the explicitly selected `collect_neon_direct_proxy()` route can issue the
frozen, registered in-process binding. It checks the retained console receipt's
organization/project/branch/endpoint pins, then performs one bounded native
GET of the exact e7 revision in the fixed GCP project and region. The full image
digest and single literal `DATABASE_URL` slot must match. No arbitrary JSON,
secret alias, suffix-only inference, mismatch fallback or caller Boolean can
issue the binding. The historical console receipt establishes the target;
it is not fresh owner/admin or provider-retirement authority.

The credential remains transient and hidden from representations. A distinct
private target creates a genuine psycopg direct connection with `verify-full`,
the scoped operator CA, fixed timeouts and no alternate service/hostaddr/role
options. A genuine DBAPI `do_connect` hook checks effective libpq parameters and
SSL-in-use before returning the driver to SQLAlchemy, so dialect initialization
reads cannot precede that proof. Failure closes that connection and refuses
without retry. Adapters recheck the same transport before protected SQL identity
observation. Current scope is the pinned production endpoint, existing
`neondb_owner` actor and public schema9. Unknown/pooled/unverified routes refuse.
The macOS CA path applies only to this explicit operator route.

Both preflight observation and private preparation accept an optional typed
binding; omitted binding keeps the ordinary native contract. The Neon route
retains exact genuine protocol user and PG17 version checks, current/session
user agreement, native control/function/view identities and a bounded complete
activity projection. Its SQL own PID must appear exactly once with the expected
native role and database OIDs before exclusion. Other old/replacement/orphan,
NULL/foreign-database and prepared-work counts retain their original semantics.
Preflight additionally binds the selected inventory to public/schema9 and the
retained cluster/database identities. Preparation still enforces all original
object, ACL, grant, collision/drift and transaction-outcome checks.

Capture and preparation are separate explicit purposes. A capture-purpose
binding sets startup read-only and is refused by `prepare()`. A separately
collected preparation-purpose binding is an identity input only; it is not
permission to apply a manifest. The included operator capture runner only calls
`review_manifest()` in a read-only transaction and never calls `prepare()`.
It verifies the sealed source manifest first and writes credential-free private
metadata and fixed refusal receipts. Its external supervisor bounds execution.

Offline native-shaped transport tests prove request/receipt/source/TLS/settings
and privacy refusals. Actual disposable PG17 tests prove native SQL session
identity, complete own-row and foreign-session handling and unchanged default
preparation behavior. Their provider transport policy is explicitly isolated:
the loopback fixture cannot prove acceptance of the actual Neon hostname/TLS
route. The eventual reviewed read-only production capture is still required.
No production native/SQL call, role creation, credential/ownership transfer,
provider retirement, checkout activation or global fence certification was
performed by this source implementation. NativeBoundary's refusal is unchanged.

The retained v1 source packet was placed on HOLD before any native execution:
its adapter-first check did not precede SQLAlchemy dialect initialization. V2
adds the genuine pre-initialization guard and a real disposable ordering proof.
The supervisor owns a new process group and uses bounded TERM/KILL cleanup on
deadline so the native GET child does not outlive a timed-out capture.

The retained v2 packet also remains on HOLD: real psycopg `get_parameters()`
omits compiled-default values, including explicit port5432. V3 accepts that
documented omission while still requiring actual `ConnectionInfo.port ==5432`
and refusing any present conflicting parameter. Channel-binding's documented
prefer default was already handled. The other expected fields are intentionally
nondefault. The regression uses the real psycopg ConnectionInfo method and native
libpq Conninfo parser/defaults with synthetic metadata; it is API-semantics
evidence, not a connected port5432 or Neon authentication claim. No forwarding
listener, native call, credentials or driver-field change was introduced.

V3 remains on HOLD for a separate SDK logging issue: removing the inherited
HTTP-logging environment setting leaves persisted `core/log_http=true` effective,
and console verbosity does not disable body logging to SDK files. V4 explicitly
passes `CLOUDSDK_CORE_LOG_HTTP=false` to the native GET child; the SDK resolves
that override before stored configuration and only installs its HTTP logging
hooks when the resulting property is true. One fake-child regression preserves
the original assertion failure and proves the false override even when inherited
logging settings are true. This environment-only change does not repeat PG tests
or establish any native acceptance, logging-history or provider-retirement fact.


The independently reviewed V4 production capture subsequently refused at genuine
connection acquisition after one native GET, without a metadata manifest or SQL
mutations. Its separately reviewed one-connect diagnostic returned only fixed
categories: all expected settings matched, the supported-key-set check refused,
and the derived-host-address and extra-SSL-policy groups were present. Those group
flags do not identify every raw field/value or prove a unique cause. Both original
receipts remain retained; no preparation or capture retry was performed.

The V5 compatibility correction follows the locked psycopg3.3.4 implementation:
its connection-attempt resolver preserves the input hostname and supplies one
numeric hostaddr before genuine libpq connection. The controlled hook refuses
caller hostaddr/service/positional conninfo and ambient PG overrides. The effective
single bounded numeric address must equal genuine ConnectionInfo.hostaddr; exact
hostname, verify-full/root CA, port, channel/user and native SQL target/session
proofs remain required. No second DNS observation, socket override or arbitrary
address/unknown-setting fallback is added. The successfully guarded genuine driver
is registered to its issued binding before SQLAlchemy initialization; later adapter
checks require that same origin as well as the effective proof.

The clean route explicitly sets sslcertmode=disable and requires that exact effective
value, preventing ambient client-certificate loading instead of accepting a blanket
SSL-policy group. Real psycopg ConnectionInfo/libpq parser/default filtering controls
exercise API semantics with synthetic metadata only; a genuine uninitialized Connection
object proves weak-key support without opening or claiming an authenticated connection.
Prior disposable PG17 SQL/ordering evidence is retained because this correction does
not change that SQL contract. The current V5 source has not yet established actual
Neon transport acceptance, positive metadata capture, preparation, new-role isolation,
retirement, checkout activation or global-fence completion.
