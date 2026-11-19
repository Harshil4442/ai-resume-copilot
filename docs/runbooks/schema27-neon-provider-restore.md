# Isolated Neon schema27 restore rehearsal

This operator tests provider restoration of a private copy. It never restores the
production branch or changes application deployments, credentials, payments,
submissions, external recovery authority or admission flags. Full R03 and DP05
remain separate from a successful isolated provider exercise.

The fixed scope is project `divine-flower-30944770`, Free subscription, source
branch `br-small-surf-bdqqplk8` and one additional read-write endpoint with
minimum and maximum **0.25 CU**. A self-restore can add a preserved branch, so the
maximum is two additional branches and one additional endpoint. Existing and
foreign resources are excluded from cleanup.

## Prerequisites

- Complete the reviewed schema27 rollout and its normal native inspection first.
  The executor independently authenticates the unchanged production observer
  and requires the schema27 marker before creating anything.
- Verify fresh Free compute headroom, branch allowance and the actual six-hour
  history window. The operator verifies Free status, the configured history
  window and branch allowance; it does not invent a CU limit from a zero-valued
  account field. Plan a maximum of 20 minutes work and five minutes cleanup.
  At 0.25 CU this is approximately 0.10417 CU-hours if cleanup completes; provider
  delays or an unknown cleanup outcome require investigation, not a zero-cost claim.
- Keep the exact committed operator checkout and its checked dependencies
  available until cleanup is verified. Use the existing official Neon 8.4.1
  DEFAULT profile and FileVault-protected manager. No login or password rotation
  is part of this procedure.

## Prepare and execute

Run from the exact reviewed operator checkout with its existing checked Python
runtime. These commands do not require the local application server.

```sh
R03_PYTHON=/absolute/path/to/checked/backend/.venv/bin/python
R03_DIRECTORY=/absolute/private/0700/rehearsal-directory
"$R03_PYTHON" -B -m infra.gcp.schema27_provider_restore plan \
  --plan "$R03_DIRECTORY/plan.json"
```

`plan` is provider-free and refuses dirty or changed controller source. Review
the returned SHA256 and exact plan. An optional `--request-provider-expiry` on
this command asks for a 30-minute provider expiry in addition to cleanup.
It is off by default because tenant support is not assumed. A rejected or
unknown Create is never automatically retried without that field.

```sh
"$R03_PYTHON" -B -m infra.gcp.schema27_provider_restore run \
  --plan "$R03_DIRECTORY/plan.json" --plan-sha256 REVIEWED_PLAN_SHA256 \
  --journal "$R03_DIRECTORY/new-journal"
```

The journal path must not exist. The operator creates it privately, binds the
exact plan and starts an independent watchdog. It requires that child's ready
acknowledgment before any provider write. The watchdog observes the deadline
and parent-liveness pipe. At the deadline it cancels only its actual recorded
parent, allowing the owner's native subprocess/SQL cleanup blocks to unwind,
then acquires the released ownership lock and performs exact-resource cleanup.
Provider TTL is an optional extra backstop; cleanup and the watchdog are mandatory.

Create omits the configurable suspend timeout and retains the Free plan's idle
default. Readback accepts only the default representation `0` or explicit
300 seconds, never `-1` or an extended timeout. Neon documents `0` as the plan
default and `-1` as disabling suspension.

The executor follows these steps:

1. Verify original manager custody, exact project/parent/production endpoint,
   Free status, branch headroom and genuine production schema27 observation.
2. Durably publish an exclusive Create intent, send one request, retain its
   original operation handles and verify the actual QA branch and endpoint.
3. Resolve the QA URI only in RAM through the original manager. Bind fresh
   provider IDs to actual TLS/channel binding, cluster/database/owner identity
   and role inventory. Production-only identity classes remain unchanged.
4. Hash the exact schema, catalogue/ACLs/roles, every public and Neon Auth table,
   sequence state and protected obligations without retaining row contents.
   Capture a QA LSN and commit one nonce-owned marker outside the application.
5. Restore only that QA branch to its own captured LSN. Preserve the later
   QA state under the nonce-owned name required for self-restore. Reobserve
   original operations, actual endpoint/branch identity and all restored hashes;
   the later marker must be absent.
6. Retain measured Create-to-Ready, restore-to-verified and checkpoint-age
   intervals, then delete the exact owned endpoint and branches child-first.
   Verify absence and unchanged critical preexisting resource metadata.

Pure SQL comparison deliberately reports no provider proof. Only the complete
coordinator path with the original native restore acknowledgment, operation
readbacks and authenticated SQL comparison marks provider restoration confirmed.
It still reports `full_R03_qualified: false` and never promotes production.

## Unknown outcomes and cleanup

```sh
"$R03_PYTHON" -B -m infra.gcp.schema27_provider_restore reconcile \
  --plan "$R03_DIRECTORY/plan.json" --plan-sha256 REVIEWED_PLAN_SHA256 \
  --journal "$R03_DIRECTORY/new-journal"
"$R03_PYTHON" -B -m infra.gcp.schema27_provider_restore cleanup \
  --plan "$R03_DIRECTORY/plan.json" --plan-sha256 REVIEWED_PLAN_SHA256 \
  --journal "$R03_DIRECTORY/new-journal"
```

`reconcile` is read-only and never backfills a provider ACK or authorizes replay.
Cleanup may identify an unacknowledged Create only through the original intent,
before-inventory, exact nonce name, new native identity, birth window and parent
lineage. That is cleanup-only ownership, never permission to restore or process
the branch. An attempted DELETE is not resent. If the resource remains present,
stop for reconciliation; if absent, cleanup can continue to other unattempted
owned resources. Keep all once markers and failure evidence.

No connection URI, password, full provider error body or customer row is written
to receipts or console output. Resource metadata and comparison hashes still
belong in the private journal. `watchdog` is an internal pipe-bound child mode,
not a manual recovery/adoption command.

## Validation and scope

The focused tests use intercepted native ports and an optional owned local PG17
cluster. They verify lost acknowledgments, no write replay, foreign/production
target refusal, deadline cancellation, atomic journals, cleanup ownership,
streaming limits, schema/ACL/data/sequence drift and marker ambiguity.

```sh
HIREWIZ_SCHEMA27_CLONE_PG_BIN=/absolute/postgresql17/bin \
  "$R03_PYTHON" -m pytest -q tests/test_schema27_provider_restore.py \
  tests/test_schema27_restore_clone.py
```

These controls do not establish provider execution, disaster detection time,
full incident RPO/RTO, encrypted independent backup recovery, independent
epoch/tombstone replay or accepted employer/payment-event reconciliation.

Provider references: [branch restoration](https://neon.com/docs/reference/api/branches/restore-project-branch),
[CLI branch controls](https://neon.com/docs/cli/branches), and
[expiry semantics](https://neon.com/docs/guides/branch-expiration).
