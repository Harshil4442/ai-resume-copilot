# Current monetary release chain compatibility

The monetary deployment contracts now admit exactly the existing 0009→0016 forward
chain. They previously stopped at 0013, so current source was refused even before the
still-missing native credential fence. This corrects an ordinary deployment dependency
within the original billing and deployment checkpoints; it completes neither checkpoint.

The forward sequencer starts at 0009 rather than selecting the last four revisions.
The step command permits only the next observed revision, keeps its actual advisory
lock, short timeouts, transaction rollback and explicit no-retry outcome handling.
The 256 MiB drained index-window check remains attached specifically to 0013.

Migration 0016 uses two module-level SQL lists. The shared metadata parser accepts
those exact names only when the monetary caller opts in, and requires bounded nonempty
literal string lists without executable expressions or shadowed names. Legacy release
behavior stays strict. Source, exact CI/image, native fence, backup, current privilege,
paused admission and joined revision checks remain required.

The database observer recognizes 0014 upload tables, 0015 paid/search history and 0016
catalogue/preparation tables, plus 0015's nonnullable history revision. It checks metadata
presence and refuses missing, wrong-kind or newer-than-marker extension tables. It does
not claim complete column/constraint equality or inspect candidate values.

Runtime SELECT/INSERT/UPDATE and sequence checks already cover newly created tables.
DELETE verification now also covers the six paid/search history tables, search
preparations, catalogue snapshots/items/winners and source/posting revisions. Existing
erasure and ordinary maintenance delete those rows. A deployment needs explicit grants
or suitable migration-owner default grants followed by fresh verification; this change
adds no production grant. Financial liabilities, service reservations and payment
orders remain outside the required DELETE set.

Verification uses standalone file/AST loading, with no app initializer, routers,
protected pairing body or provider invocation. 77 selected offline checks and all
25 unchanged legacy-release checks pass. One authorized fresh disposable PostgreSQL
session passes nine selected scenarios in one standalone test, with zero failures,
errors or skips. These cover seven real forward transitions, advisory-lock refusal,
DDL rollback, explicit resume after observing the old marker, rejection of a stale
step, the 0013 index-size refusal, current metadata and missing DELETE grants, plus
wrong database identity and revision jumps. Exact migration functions execute through
Alembic Operations; the app-importing Alembic environment is excluded from this bounded
proof. Owned schemas/roles, the container and temporary credentials are removed.
The session's own absence receipt is not an independent root cleanup observation.
Ruff passes seven selected files and Mypy passes four source files. Full composed
CI, native production migration and actual runtime grants remain separate evidence.

Production activation still needs genuine external provider/writer retirement,
replacement identities, a protected backup and reviewed forward deployment. A valid
expense estimate can authorize prospective finite packs without a new invoice-approval
gate. Monthly invoice/settlement reconciliation remains ongoing work. Automatic
submission, optional AI and checkout remain disabled by the release sequence.
