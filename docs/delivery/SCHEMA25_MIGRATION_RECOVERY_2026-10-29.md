# One corrected schema25 migration attempt after metadata import failure

The original schema21 upgrade recorded its one-shot start, then Alembic imported
`app.database` before using the supplied native transaction. The standalone
operator had no `DATABASE_URL`, so the production configuration guard raised.
Official reconciliation and an independent observer found schema21 with none
of the fourteen new tables. The original upgrade must never be repeated.

The application and Alembic sources remain the reviewed candidate
`8cbdfaeb9bb6cf6674c18ca8d59bc123fbf73a2a`. The correction changes only operator
initialization and the separately linked recovery path.

## Metadata initialization

`schema25_migration_metadata.initialized_metadata` uses the existing fixed native
collector to validate the numeric runtime secret and its canonical TLS URL. It
holds the URL only in memory and a scoped production environment while importing
the real candidate metadata. The unused runtime Engine cannot connect, including
after disposal. Alembic executes exclusively on its supplied bootstrap
transaction. The runtime Engine is disposed and environment/path values are
restored even if cleanup fails. A preloaded application database is refused.

Fresh subprocess tests run outside pytest's development fallback. They reproduce
the missing production configuration, perform a local Alembic21→25 upgrade with
one supplied connection, and assert that no second Engine connection occurs.

## Exact recovery link

`schema25_migration_recovery.py` pins the original plan file SHA-256
`22b838a59c4cb1f00aa917590a9022be4032886df22d95415895ad1e09761b5e`,
original started-marker hash, and original private journal path hash. Preserve
all original files. Capture a fresh schema21 plan with a new nonce and use a
separate empty private journal. Candidate, services, physical preimages,
policies, prior-source proofs and financial state must match the original plan.

Use its `prove` phase first with both exact plan paths/hashes/journals and the
reviewed metadata-helper SHA. It rechecks native release gates, then uses a
bound native read-only transaction to prove schema21, zero new tables, the
original retained-history digest and ACL, drained work, and an available
migration advisory lock. Its local proof binds both plans, the fresh journal
path, and hashes of the three recovery/operator source files.

After review, its explicit `upgrade` phase repeats the proof and spends a
create-only correction marker in the locked original journal **before** the
fresh migration journal can start one corrected transaction. This does not
authorize replay of either attempt. An uncertain outcome permits only its
`reconcile` phase with the same plans, journals and source hashes. It delegates
to the existing read-only migration observer and records success only after
fresh schema25 gates pass. A second `upgrade` is refused.

Keep API/worker/coordinator admission closed and queues/schedules paused during
proof, correction and reconciliation. Do not create a second recovery link or
edit a retained journal to bypass a failed or unknown outcome. No recovery
phase relabels an image, revives the retired migrator credential, opens product
admission, changes prices, or enables checkout.
