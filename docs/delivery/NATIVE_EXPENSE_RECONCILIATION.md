# Native expense reconciliation

This is Stage 2/6 accounting tooling. It does not complete either stage, certify
an invoice, authorize production writes or open new credits, checkout or an AI
route. There is no customer API and no new role, migration or customer currency.

## Why this exists

Paid and free processing retain the complete native/worker allowance after
delivery, failure or deletion until actual expense can be reconciled. Previously
the paid settlement primitive required a live owner, so an erased account's
native allowance could never be reconciled through that primitive. Model-provider
costs are separate: reconciling native expense never settles an unknown Gemini
attempt or makes a timed-out attempt free.

`app/domains/credits/reconciliation.py` supports one explicit reviewed native
expense at a time, including detached owner records. Existing owner-facing
authorization and settlement functions are unchanged. It updates the existing
native cost fields and retains the first reviewed receipt in the existing
append-only `AdminAuditEvent` history. Exact repeats are idempotent. A different
receipt, amount or target cannot overwrite the first settlement.

## Evidence and operator authority

The existing authorized operator database credential is the authority to run
this tool. A reviewer name, digest or JSON file is not a new access capability.
The reviewer must independently inspect actual native usage or an allocated
supplier invoice and the completed worker-execution evidence for this exact
operation. The tool checks custody, identity, dates and durable terminal state;
it cannot decide whether a supplied invoice is genuine or correctly allocated.
Synthetic tests do not establish those facts.

Retain two private artifacts: the actual source expense evidence and terminal
worker evidence showing all native attempts have stopped. Do not use an HTTP
timeout, expired lease, missing user, estimated allowance, partial attempt or
token-usage report as proof of final native cost. Do not include resumes,
passwords, tokens or application answers in expense evidence. Evidence bytes
remain private files; only bounded receipt metadata and their SHA-256 hashes
enter the privileged audit record.

The receipt review JSON contains exactly these outer keys:

| Key | Meaning |
|---|---|
| `receipt` | Exact cost/operation metadata described below |
| `operator_email` | Existing operator's normalized account identifier |
| `operator_native_identity_sha256` | Digest of the retained operator identity review |
| `source_path` | Absolute path to the reviewed expense artifact |
| `completion_path` | Absolute path to the reviewed terminal-worker artifact |

The `receipt` contains `kind` (`paid_native` or `free_core`), `target_id`,
`actual_minor` (positive INR paise), `currency` (`INR`), `provenance`
(`measured_usage` or `invoice_reconciled`), `allocation`
(`direct_operation_metering` or `reviewed_invoice_allocation`), `source_sha256`,
`completion_sha256`, `work_state` (`completed` or `failed`), and timezone-aware
`window_start`, `window_end`, `terminal_at` and `reviewed_at`. The bounded window
must contain the operation creation and terminal time. Invoice allocation needs
invoice-reconciled provenance. Initial tooling refuses zero-cost settlements
rather than guess that a failure was unbilled.
The actual amount must cover the complete native and incremental worker path,
including reviewed allocation, FX and unrecoverable tax where applicable; do
not count the same supplier charge in both this row and shared overhead.

The completion artifact is a bounded JSON `TerminalWorkEvidence` record with
version `reviewed-native-terminal-v1` and provenance
`operator_reviewed_native_execution`. It contains the same `kind`, `target_id`,
`work_state` and `terminal_at`; the exact `financial_group_id` and
`reservation_id` (free-core uses its reservation ID and a null group); hashes
of one to 32 retained original execution readbacks in
`execution_readback_sha256`; and timezone-aware `observed_at`. The reviewer must
explicitly establish both `all_native_attempts_terminal: true` and
`no_future_native_work: true` from those original readbacks, including child
processes and queued retries. False, running, ambiguous or mismatched records
are refused. The typed record is an operator review, **not** a provider-signed
attestation. Its schema and hashes alone cannot establish that a worker stopped.
The CLI parses and hashes the same private bytes, then pins the reviewed record
and exact operation correlation in the plan and audit.

Paid work must have the corresponding delivered/failed outcome. Free work must
have a completed request, or an erased account with its private request removed
and independently reviewed terminal evidence. A live free request still marked
admitted is refused, even if a separate timeout suggests failure. Its expense
remains held pending stronger durable terminal evidence; this tool does not
rewrite processing status or erase outstanding work to make it settleable.

## Capture, review and apply

Use an owned canonical private directory (`0700`) and regular non-symlink files
(`0600`). The database must be the explicitly selected PostgreSQL database at
schema `20261028_0025`. Production connections require certificate-verified TLS
(`sslmode=verify-full`) and an observed TLS 1.2/1.3 channel. Unverified transport
configuration is refused before opening a database connection. The captured
identity includes the server-observed cluster identifier, database and namespace
OIDs, actual PostgreSQL version and operator role, not just URL/database names.
A same-named recreated database/schema or retargeted endpoint invalidates the
plan. Only the existing dedicated loopback synthetic test databases may omit TLS.
Supply its existing operator credential through the
normal private environment; never paste it into a plan, terminal output or chat.

```sh
python scripts/reconcile_native_expenses.py capture \
  --receipt-review /private/review/native-expense-review.json \
  --plan /private/review/native-expense-plan.json

python scripts/reconcile_native_expenses.py dry-run \
  --plan /private/review/native-expense-plan.json \
  --plan-sha256 <SHA-256 printed by capture>
```

Both phases use read-only transactions. Capture exclusively creates a new
private plan. Review its database identity, target fingerprint, exact native
expense, operator and artifact custody before authorizing the concrete write.
The plan binds the financial group/reservation, original allowance and complete
financial snapshot; erasing ownership cannot change these monetary identities.
Plans permit at most 24 hours for new writes.

Only an approved operator performs the explicit write:

```sh
python scripts/reconcile_native_expenses.py apply \
  --plan /private/review/native-expense-plan.json \
  --plan-sha256 <reviewed SHA-256> \
  --accepted-reviewed-native-expense
```

Apply verifies the same files and database/target again, uses the existing money
locks and updates cost plus audit in one transaction. An actual overrun is
recorded in full; it never increases the customer's accepted charge. Paid
overrun holds pause further work on that route identity until reviewed and
requalified under a new route identity. Existing free-core overrun protection
also remains in force. Global headroom counts the retained actual/reserved
expense, and unresolved provider obligations remain protected.

If commit acknowledgement is uncertain, use **dry-run of the same plan** to
observe whether its exact receipt was recorded. Do not create another receipt
or automatically repeat an uncertain write. A late readback may confirm an
already committed settlement after plan expiry; an unapplied expired plan
cannot authorize a new write. Conflicts stop for accounting review.

## Verification and limits

Synthetic SQLite and genuine isolated PostgreSQL tests cover duplicate/conflicting
receipts, owner erasure races, transaction rollback, private-artifact changes,
unfinished work, window/identity changes, positive overruns and preserved unknown
provider costs. The CLI tests capture, inspect and explicitly apply synthetic
receipts only to disposable schemas. CI uses its existing dedicated local test
database; local verification can use a separately owned loopback PG17 cluster.

Real supplier invoices, allocation review, representative shadow accounting and
commercial/quality gates remain separate acceptance evidence. This tool never
sets any release-evidence or invoice-qualification flag.
