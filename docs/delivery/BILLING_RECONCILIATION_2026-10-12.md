# Cash-period billing reconciliation

This ordinary BI08 correction improves the existing read-only expense report. It
does not activate checkout, paid execution, optional AI, credentials or production.
The original billing costs/failure recovery/load checkpoint remains open.

The old report summed each current-month purchase's cumulative refunded total.
That omitted this month's refunds for older purchases and could include later
refunds in the purchase period. The corrected report keeps captured INR gross by
`paid_at` and counts only processed refunds by their own `processed_at`. The cutoff
is an inclusive UTC month start and exclusive fixed as-of instant. Unplaced processed
refunds and other-currency captures/refunds are explicit counts, never silently
mixed into INR. Actual provider fees and customer tax remain unknown when absent.

Search and application usage now have separate settled/released credit totals,
charged quantities and pending-or-unknown held totals. Settled usage follows its
settlement month. A reservation created before the cutoff retains its frozen
reserved amount as held if still reserved or if settled at or after the cutoff;
settlement exactly at the cutoff belongs to the future held bucket. Reservations
created at or after the cutoff are excluded. Quantities use
each stored unit price, preserving older 1/5 and new 1/20 quotes. A reserved row
cannot prove whether work is pending or possibly sent, so the report retains that
combined label. It neither releases credits nor claims a confirmed application.

Model figures retain their attempt-created-month/current-snapshot basis with all
earlier unresolved holds. They do not reconstruct historical as-of model costs and
remain estimates rather than provider invoices. Fixed costs stay unknown;
the report makes no whole-business profit claim. This patch does not alter packs,
customer balances, accepted prices, settlement logic or funding admission.

The function executes fixed SQLAlchemy Core aggregates without aggregate model
imports. It requires a fresh session; PostgreSQL uses a read-only repeatable-read
snapshot with transaction-local UTC and a five-second statement timeout. Missing
current payment/refund, model-liability or service-reservation tables are an explicit
unavailable report, not zero costs. No candidate IDs, resumes or application answers
are read or returned.

Seven exact-function cases pass on owned SQLite/Core tables using standalone AST
loading, without app initialization or global conftest. Ruff passes the script and
test; Mypy passes the script. The fresh PostgreSQL packet with manifest
`c21919d929f950ace71ea71817df2f6b6373bae9c7267ef0ba51143effe14919`
ran once against the corrected source. Its seven report cases and actual
repeatable-read snapshot, UTC and write-denial (`SQLSTATE 25006`) proof passed with
zero failures, errors or skips and no application imports or provider calls.
The [native receipt](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-billing-report-cutoff-native-20261012-mm1g3ldi/state/native-sustained-result.json)
records acceptance. The four owned lifecycle helpers remained byte-identical to
the earlier reviewed packet.

Mandatory cleanup completed, and the [independent root absence observation](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-cutoff-report-root-absence-6pzoyvnz/result.json)
verified both the exact container ID and name absent, credentials removed and
owned processes reaped. That proof has SHA256
`925064c89415c6de8413663cf9349d50feed9e2595c9c1491ed2892e78c88ccf`.
These checks validate the corrected report on owned synthetic PostgreSQL/Core
tables. Full composed CI, load/failure recovery and actual production
reconciliation remain separate; the original billing checkpoint stays open.

Genuine remaining launch dependencies include the native old-writer/provider fence,
protected backup and forward deployment, current expense authority and live finite
checkout verification. Owner-authorized conservative estimates do not require an
additional invoice-approval gate; actual invoice and net-settlement calibration
continues monthly. Protected browser/lifecycle integration is outside this patch.
