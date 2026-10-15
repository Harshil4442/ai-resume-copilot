# IAM disable readback and rollback

The F5 migration attempt reached old-principal isolation after full retained
credential binding, prepared-owner validation and source quiescence. It stopped
with `write_old_principal_disable_unconfirmed`. No backup, customer-data copy,
source ownership transition, target rotation or production traffic switch ran.

Fresh native IAM reads joined the six expected email/UID pairs: five disabled,
one worker still enabled. The stopped owned Docker volume contained exactly
five disable attempt markers plus `source-owner-resume`; the original container
was absent. This establishes that those five operations took effect, although
the immediate GET had not confirmed one of them. It does not establish the
cause of the earlier, separate metadata-read failure.

The five exact accounts were restored to their pre-attempt enabled state using
separate durable rollback attempts and fresh native UID/state verification.
All five enabled readbacks were confirmed on 15 October 2026 at 11:50 UTC.
The worker was unchanged. Keys, SQL, customer rows, queues, trigger and traffic
were not modified by rollback. Retain the original stopped volume and journals;
never replay their disable operations or remove attempt markers.

The source correction sends each disable once, then polls only the same native
GET up to 16 times. Every observation joins the exact email and UID. A changed
identity, malformed state, unavailable native response or unconfirmed state
refuses. Waiting and all observations consume the existing shared 55-second
isolation deadline; polling does not refresh it. The original physical-write
transport and durable once markers remain unchanged. Unknown writes still
require reconciliation rather than another POST.

Google's [official disable/enable guide](https://docs.cloud.google.com/iam/docs/service-accounts-disable-enable)
includes a Python example waiting before its GET state check. Our implementation
uses bounded polling instead of assuming the first metadata response reflects
the completed change. Focused controls cover delayed state, bounded failure,
UID drift, unchanged deadline and absence of repeated mutation requests.

A new immutable source release, exact CI, native builds and fresh admission are
required before another migration attempt. The prior successful F5 build and
read-only diagnostic are historical evidence, not authorization to deploy
changed source. All six product/deployment outcomes remain open.
