# Disabled recovery-authority core: local proof

Status: **integrated and locally verified; production authority is unavailable**.
This is a Python-only foundation for the
[candidate-reviewed browser-first path](INITIAL_BROWSER_ASSISTANCE_2026-10-08.md), not
production pairing or permission to disclose information. No HTTP route, browser wiring,
file upload, final submit or financial operation is added.

## Implemented boundary

`backend/app/domains/recovery/` separates immutable contracts, authority storage and
transactional decisions. `RecoveryGuard()` uses an unavailable production store by
default. No environment flag, application SQL, Redis or test-file fallback can enable it.
The only functional store is a test fixture under `backend/tests/fixtures/`, using a
separate absolute SQLite file, FULL/WAL durability and bounded writer contention.

The authority begins CLOSED. A registered synthetic operator explicitly opens it against
the complete current local journal barrier. Approval consumes a fresh, single-use,
subject/actor/key/epoch-bound candidate challenge. A restored SQL row alone cannot
register an approval. Device possession/authentication is assumed from a trusted adapter;
constructing an actor object does not authenticate a real user.

Every decision binds subject UUID, authority/epoch/generation, employer/tenant/opening,
application/approval/admission, policy/price, exact artifact hash/generation, sealed
package/review, current grant, device key/executor, HTTPS origin, field/value hash and
deadline. The first slice supports **one fill action per stable opening**. Its opening
claim excludes epoch, device, package and approval namespaces, so replacing those cannot
grant another disclosure.

Preparing grants no authority to act. Atomic begin retains the stable claim, BEGUN state
and append-only event before returning the sole short-lived may-act response. Other
processes and a restart receive status only; a lost response is never reconstructed as
permission. Cancellation and begin serialize at that boundary. A cancellation that wins
first prevents begin; cancellation after begin cannot promise to recall possible autosave.
UNKNOWN and LOCAL_FILLED remain non-authorizing observations, not employer receipts.
They cannot refund, erase the claim, reopen it or change a terminal outcome.

## Evidence

The [frozen source hashes and local evidence](evidence/2026-10-08-recovery-core-local.json)
record **85 focused cases, zero failures/skips**: 70 contract/state cases and 15
process/restore cases. Ten actual spawned cases used 18 independent executor processes.
Proof includes competing begin and approval consumption, stable claims across devices and
packages, cancel-first/begin-first/competing order, process exit after durable commit before
reply, bounded writer timeout, restart and application-only logical backup/restore.
Retained local journal checks reject missing claim, permit, approval, consumed-challenge,
tombstone and epoch projections. Approver key/executor changes invalidate old approvals.

The complete merged root backend suite passed **630 tests with no skips**, including
26 actual PostgreSQL cases and eight actual Redis cases. Exact CI-scoped Ruff passed;
Mypy passed across 53 source files. The task-owned Redis container was removed after the
suite. Exact-commit [CI 37821078441](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/37821078441)
also passed the complete 630-test suite, scoped static checks and generated contracts.
Only the accompanying frontend layout fix was promoted; the new recovery modules are
not part of the serving `ec52279` GCP image and have no production adapter or route.

## Limits and next work

This proves only the declared local protocol with a separately retained authority file
while restoring the application database. It does not detect rollback of that authority
file itself, prove production authentication/signing, Firestore/GCS retention/IAM, cloud
restore RPO/RTO, real device pairing, employer permission or physical old-process egress
fencing. The local journal is not an independently protected production journal.

The browser flow needs multiple fields, so the next core change must allocate an
attempt-bound ordered step manifest while preserving the stable opening claim. Each step
needs a fresh exact begin, and possible/unknown steps must never be replayed to advance
the form. Then implement the reviewed production storage/authentication boundary and
actual companion pairing/integration. Keep the committed companion disabled and all
automatic-submission flags false until their relevant acceptance evidence exists.

The [production recovery architecture](RECOVERY_FOUNDATION_PLAN.md) remains proposed.
The [current component release report](BROWSER_FIRST_FOUNDATION_2026-10-08.md) separates
the promoted frontend from this local, inactive core and the unchanged serving backend.
