# Paid-search history runtime checkpoint — 11 October 2026

The private Stage C runtime/schema packet and independent packaging review pass.
Eight reviewed source paths are integrated in the managed worktree;
`EMPLOYER_PAID_HISTORY_MODE` defaults to `off`. Publication and committed-source CI
remain pending.
No production migration, environment flag change, paid activation or promotion is
established by this checkpoint.

Durable history binds each paid fact to the authoritative delivery ID, owner,
search, posting, original positive charge and timestamp. The owner/opening registry
references proven history. Duplicate historical paid rows remain represented;
missing/conflicting evidence remains unresolved instead of authorizing a new paid
canonical opening. Original accepted price/version, free prior physical matches,
owner locks, refund and settlement outcomes are preserved in the scoped controls.
New completed-search proof and registry writes share the financial transaction;
proof failure rolls the settlement back, and concurrent same-key preparation settles
only once. Source edits and late positive deliveries invalidate readiness.

Resumable backfill has no arbitrary total-history cutoff. A default call claims at
most eight deliveries, returns at most 16,384 source bytes in aggregate and spends
at most 32,768 parser/reduction work units. Parsing commits outside financial locks;
final proof writes recheck source revision, claim generation and cursor boundaries.
Duplicate JSON keys, incompatible identities and review-required revisions remain
explicit unresolved evidence. The final generation guard prevents a stale worker
from declaring ready after a late backdated delivery and an intervening claimant.

## Scoped evidence

- The native PostgreSQL bounded-index run passes 29 cases before the final one-field
  generation correction. The preserved original generation counterexample fails
  against its old source and passes unchanged on final source (one pass, 29 deselected).
  Final exact-source bounded-admission, malformed-leaf and plan controls pass three
  cases (27 deselected). This is not a full 30-case final-source suite claim.
- The unchanged off-mode baseline passes 23 cases. Isolated lint covers all eight
  shipping files; independent schema review passes 76 offline controls. Original
  Unicode, valid identity-capacity, parser-FK lock and generation failures remain
  retained, along with setup/instrumentation failures.
- Ready indexed admission decodes zero historical snapshots, versus 24 in the
  measured original path, with two finite membership queries for the current cohort.
  The candidate cap remains 1,000. Known fields now filter fixed semantic key hashes
  on the full object-index prefix; arrays use the indexed `member_index` keyset.
  Native plans with 5,000 irrelevant siblings use the matching object/array indexes,
  returning one/65 rows with 10/6 shared hit blocks and no temporary blocks.
- The actual PostgreSQL 17.11/aarch64 instance used a uniquely owned, pinned local
  image, localhost-only dynamic port, dummy database and 256 MiB tmpfs without volumes.
  Container `hw-stagec-pg-3w230pvw`, ID
  `90556e6fb3b25cb76ef36294f8763aaaabe7fe5a7c2568d4e24ae80bcda0559f`,
  is stopped and removed; absence is verified by ID and name. Root independently
  repeats only those two metadata inspections with zero mutations. Its receipt SHA256
  is `4be8ec03c45ca1e9be3c88332ace75b7837de6dc6e7d60c3180575f908eb4a97`.

[Sanitized evidence](evidence/2026-10-11-paid-search-history.json) records exact
source/evidence pins. Source-body scope excludes recovery, protected pairing and
aggregate main/global conftest/router bodies. The inherited 150/156-entry metadata
inventory is retained separately; effective base/runtime body counts are 148/154.
The earlier broad-search incident remains recorded; no blanket historical no-held-read
claim is made. No managed edits or native/provider actions were performed to author
this documentation.

## Exact reviewed source

Migration `20261011_0015` follows `20261010_0014`. It adds six history tables and
indexes without historical financial DML and refuses a populated downgrade before
mutation. Existing archived result contracts include `charged_credits`.

| Path | SHA256 |
| --- | --- |
| `backend/app/domains/employer/config.py` | `6cfc0d07207a1ba8d11a3c5ed1f2e40b91bb1f5c11ade15e7c540e3e2ac30635` |
| `backend/app/domains/employer/service.py` | `a91d168783d0f330c27aa7e26378625b028b388caf0f406019abd81457d74146` |
| `backend/app/domains/employer/models.py` | `9b926e48e09a8f48d34b24fbe024b122d2369fd6d960b065bccb89b0ac831841` |
| `backend/app/domains/employer/delivery_history_projector.py` | `20980fa1d33e175088b78b071469af60bfdc1d8e746f898b4b1aa8347e8540de` |
| `backend/app/domains/employer/history_stream.py` | `62113a5887ee5f14e10315316418b5513b7c5d891b4234c71597478406b2b69b` |
| `backend/app/domains/employer/delivery_history.py` | `652f7e37c972c4bb1d02a53f906707495591cbb600756783b405f11599b75e74` |
| `backend/alembic/versions/20261011_0015_employer_paid_history.py` | `216a579c0539017028978c66f969262b28d6523cb08aa82859901187b2fd3f1c` |
| `backend/tests/test_employer_delivery_history.py` | `a4b862813a77879d22310efd55ea0979219fba85750f130646d9ae33339a23f9` |

## Remaining acceptance

- [x] Review runtime/schema row proof and atomic financial preservation.
- [x] Complete the finite local PostgreSQL controls and exact owned cleanup.
- [x] Verify the final ordinary artifact and shipping manifests.
- [x] Integrate the exact eight reviewed source paths.
- [ ] Publish and pass CI for the exact new committed source.
- [ ] Bound PostgreSQL JSON-to-text/TOAST work during legacy backfill; transport/parser
  budgets alone do not prove native database evaluation bounded.
- [ ] Implement complete indexed catalog traversal beyond the unchanged 1,000 cap.
- [ ] Complete global auth/entitlement, composed API/browser/provider and production
  migration/rollout checks. SQLite fallback uses a defensive anti-join; its performance
  and migration compatibility are separate. Non-default non-expiring sessions are not
  covered by the native revision-refresh proof.
- [ ] Complete authorized deployment/promotion and observe production.

The terminal five-job CI run 38085861350 proves exact preceding source
`9dd7689e9e8e05bae67a151ab39830668abf01ba`, not this new Stage C source. The separate
native archive writer/export command and delete return 0. The new refusal-only
diagnostic is terminal and identifies the local `member_path` gate with an
unclassified member role; no actual member name or unique provider cause is
proved, and imports are unattempted. [Sanitized refusal evidence](evidence/2026-10-11-document-archive-refusal.json)
retains the outcome. Neither archive repair nor scanner/private HTTP acceptance
is closed by paid-history controls.
