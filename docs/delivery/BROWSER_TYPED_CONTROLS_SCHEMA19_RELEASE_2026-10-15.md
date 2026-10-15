# Schema19 typed-control release boundary

F7 cutover and its existing rolling path remain frozen at `20261015_0018`.
This typed-control branch adds migration `20261015_0019`; it is not a deployable
schema18 source snapshot. The existing source guard deliberately rejects this
longer chain before CI, provider or cloud calls. Do not remove the migration from
the build, relabel the schema, broaden the old allowlists or enable typed controls
as a workaround. A later additive schema19 release is required.

The feature flag `BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED` defaults to false.
Fill-only assistance continues to charge zero credits. The separately disabled
attachment action is described in [its local implementation report](BROWSER_ATTACHMENT_ASSISTANCE_2026-10-15.md).
Country, employment, login/CAPTCHA, exact final-submit approval, one-shot submission and
genuine employer receipt/reconciliation remain incomplete.

## Schema-specific consumer assessment

| Consumer | Current boundary | Later schema19 work |
| --- | --- | --- |
| `infra/gcp/monetary_release.py`: `CUTOVER_CHAIN`, `CHAIN`, `verify_source_image` and entrypoint source admission | Exact chain through schema18; longer source refused | Review a separate additive release sequence and exact source/image admission. Preserve the existing F7 chain. |
| `backend/scripts/monetary_schema_step.py`: `CHAIN`, `upgrade_step` | No schema18→19 step; a request is rejected before a DB connection | Add a reviewed bounded step for the later release with observed identity/schema, exclusive migration authority, advisory lock and post-commit verification. |
| `backend/scripts/monetary_cutover_preflight.py`: `SCHEMA_ORDER`, `SCHEMAS`, `Inventory` literals, extension shape and application-state reader | Schema19 unsupported | Review schema19 inventory/readers. Observe the durable `review_protocol_version` column/default/1–3 check and validated 0–12 cursor check, rather than merely accepting the old table list. Preserve financial/manual-report predicates. |
| `backend/scripts/neon_direct_identity.py`: `RELEASE_SCHEMAS` and bound transport admission | Schema19 outside supported release schemas | Review schema19 identity/transport admission separately; no new credential or production connectivity is established by local tests. |
| `infra/gcp/fresh_cutover_registry.py`: `expected_registry` | Candidate schema18 | Keep the F7 registry frozen. A later registry must bind its own reviewed schema19 release identity. |
| `infra/gcp/rolling_release.py`: `SCHEMA`, plan validation and readbacks; `rolling_native.py` native adapter | Existing target must already be schema18; no migration login revival | Review a later schema19 rolling/rollback contract. The current schema18 capture/advance path must continue rejecting unsupported state. |
| `backend/tests/test_monetary_current_chain.py` | Source extends the frozen schema18 chain by exactly 19; explicit offline rejection assertions | Keep successful schema18 contract tests and explicit 19 rejection checks until a separately reviewed release replaces them. |
| `backend/alembic/env.py` and ordinary Alembic `head` discovery | `head` is now19 for local/source verification | Use the exact18 target for F7, never an unreviewed `upgrade head` in that deployment. Local full-chain verification legitimately executes19. |

No production consumer above was widened. Offline tests prove source-image
admission refuses this branch before an external command, inventory schemas
refuse 19, the native identity allowlist omits 19, the application-state reader
refuses 19 and the bounded schema18 migration step cannot open a connection for 19.

## Procedure required for the later additive release

1. Finish and observe the preserved-data F7/schema18 cutover. Capture the exact
   current database/namespace identity, schema marker, financial invariants,
   runtime/observer/migration identities and retained revisions through its
   existing approved path. Do not reuse invented or local fixture values.
2. Review the schema19 consumers above and an immutable source/image/package
   set. Bind `BROWSER_ASSISTANCE_TYPED_CONTROLS_ENABLED=false` explicitly during
   migration and staging; keep new browser assistance disabled or drained for the
   bounded schema operation. Confirm no in-flight field action is being treated
   as safely retryable. Grant only the migration authority needed by this later
   release; the current rolling path does not reactivate a retired credential.
3. Rehearse the exact upgrade on a disposable PostgreSQL copy at 18. Inspect the
   existing `ck_browser_review_index`, the schema marker and retained reviews.
   The local six-case evidence uses PostgreSQL 17.11 and a UUID-owned schema in
   an ephemeral loopback-only Docker database. It proves behavior, not production
   lock timing, DB identity, cloud authority or backup/recovery.
4. In the reviewed maintenance transaction, bind observed identity and exact 18,
   obtain the migration advisory lock (`721994372`) and apply bounded lock and
   statement timeouts. Run only `alembic upgrade 20261015_0019` using that owned
   connection. Replacing the check takes a table lock; measure the production
   table/lock window before admitting that operation. No data backfill, new
   grants, external form action or financial rewrite is required by this DDL.
5. Observe commit, exact marker 19 and a validated `ck_browser_review_index` of
   `next_index BETWEEN 0 AND 12`, durable protocol marker/default/check and v2
   backfill; check retained rows, other constraints and
   financial invariants. A lost acknowledgement requires observing the same
   database state before any decision. Do not replay an unknown migration or
   remove retained history to make a guard pass.
6. Stage the immutable API/workers/frontend/companion set with the flag off.
   Complete exact source/package checks, DB and native identity admission,
   actual permission/authentication acceptance and the permitted staged user
   journey. Enable typed controls only after those later-release gates pass.
7. Keep the schema during rollback unless the reviewed guard and compatibility
   evidence explicitly permit a downgrade. Migration19 refuses downgrade when
   a retained durable protocol marker exceeds one or a cursor exceeds seven;
   failed downgrade leaves marker, rows and the 12 bound intact on PostgreSQL.
   Do not delete/cancel history solely to force a downgrade. Old binary/readback
   compatibility with 19 requires separate proof because current release readers
   intentionally reject it.

## Local evidence and its limits

The installed test runs the shipped stable-identity MV3 runtime with synthetic
exact-origin grants and locally fulfilled HTTPS. It traverses the actual device
signature verifier, candidate approval service, isolated SQL persistence, signed
one-shot field leases and actual Chromium DOM adapter. Twelve fields include
distinct saved labels/values for all four dropdowns. Unknown option/conditional
outcomes, changed DOM, tampered signed label and lost begin acknowledgements
produce no later lease or replay. No file is attached and no submit event occurs.

The PostgreSQL fixture executes the full Alembic chain to 18 before copying owned
synthetic package rows into the actual migrated tables. It verifies18→19, v1
row/column/table preservation, 0/7/8/12 acceptance, −1/13 refusal, twelve signed
service actions and zero fee/receipt, v1 downgrade round-trip, and downgrade
guards at typed payload cursors 0/7 and payload-erased cursors 8/12. The existing
clean SQLite Alembic regression also passes after the batch-operation repair.

No provider, GCP, Neon or employer endpoint was contacted. These checks do not
close live employer upload acceptance, final-submit approval, automatic submission, employer receipt,
paid-application settlement or production deployment gates.
