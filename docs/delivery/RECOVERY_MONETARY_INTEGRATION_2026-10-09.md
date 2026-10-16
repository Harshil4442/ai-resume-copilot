# Recovery publication and monetary preflight integration

Source checkpoint, 9 October 2026. These changes are integrated locally after independent
review; they do not promote a backend, migrate production or enable browser actions.
The full paid employer-service goal remains active.

## What changed

All three authoritative journal writers now retain the complete canonical intent in
an independently pinned transactional publication resource before starting their GCS
upload. Emergency closure freezes that admission boundary and exports every captured
record before acknowledging a complete inventory. This closes the reproduced late-upload
and final-manifest acknowledgement races for the tested, protected-resource model.

The monetary helper now checks callable privileged functions and procedures as possible
writers, including PUBLIC, inherited and transitive SET ROLE access. A seemingly harmless
body or an owner without known table privileges cannot certify that a callable privileged
routine is safe. It also checks direct/column/elevated permissions, active sessions,
financial provenance, orphan liabilities and shared retry ceilings in one bounded
read-only repeatable-read database snapshot.

The helper never authorizes global cutover: `cutover_ready` remains false. Its caller's
cloud, provider, queue, source, image and backup declarations are not independently
verified by a database query. See [the helper runbook](MONETARY_PREFLIGHT_2026-10-09.md)
and [cutover preparation](MONETARY_CUTOVER_PREPARATION_2026-10-09.md).

## Exact review and root verification

The publication source applies the frozen prerequisite denial patch to `aab73da`, then
the separately reviewed publication incremental. Integration onto root `3d2e693` verified
every prerequisite and final source hash before mutation. The monetary V4 patch verifies
ten unchanged prerequisite files and all three added file hashes.

- Publication independent review: 353 affected tests, 12 independent race/fault cases
  and three actual PostgreSQL credential cases pass without skips. All three delayed
  ordinary uploads and the owning denial are included before complete-cut acknowledgement.
- Root publication check: all 353 affected cases pass without skips on the separately
  owned loopback native emulator. Root Ruff and the 91-file CI type selection pass.
- Monetary V4 independent review: 82 author cases and 20 independently selected actual
  PostgreSQL cases pass without skips. Nested apparently nonwriter owners still caused
  actual synthetic writes and were correctly refused. Snapshot isolation, bounded output
  privacy and owned-resource cleanup were checked.
- Combined whole-backend verification passes **1,760 tests without failures, errors or
  skips**, exact CI Ruff, and typing on 92 source files. The verification archive matches
  all 25 integrated source overlays and carries no private environment files. Owned
  admission schemas, cutover roles/routines/observer sessions and credential-test databases
  are absent after teardown. Exact remote CI for this combined source is still required.

CI explicitly includes the new closure/publication fixtures and tests, the financial
tests, and the monetary script in its relevant lint/type checks. The frontend release
documentation commit `3d2e693` separately passed all five remote CI jobs; that result
does not certify this newer combined source.

[Hash-bound integration evidence](evidence/2026-10-09-recovery-monetary-root-integration.json)
records the exact sources, independent reviews, whole-suite result and cleanup scope.

## Open activation gates

The publication foundation is disabled and limited to 63 records per authority
incarnation. Closing it affects the entire cohort permanently. That cannot provide
normal candidate logout or scaled product availability.

Independent review also demonstrated that a privileged same-UID rollback of just the
gate can hide a retained pending upload while its full row survives. Runtime resource
UID checks cannot prove protection against such a rollback. Actual protected-resource
permissions, retained history, import/delete/restore restrictions, old-writer exclusion
and complete-cut recovery remain required. The next protocol must add full-row census,
partitioned complete cuts and per-subject/session denials that serialize with consuming
admissions, leaving unrelated candidates available.

Production account/session provisioning, authenticated browser transport, current action
authority, exact package/form approval and permitted real portal completion remain open.
The database helper does not retire credentials, kill old sessions, disable model keys,
drain queues, perform a restore drill or promote schema `0010`.

GCP remains the serving `e7d8f2b` release and last verified schema `0009`; the tested
frontend remains `06cc112`. No real candidate application, payment, model generation,
KMS signing, production credential mutation or cloud traffic change was performed for
this integration. See [remaining requirements](REMAINING_REQUIREMENTS.md) for the full
development and deployment scope.
