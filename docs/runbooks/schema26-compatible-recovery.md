# Post-restoration schema26 recovery to C6

This separately typed operator is source-only until it receives independent review,
actual current-host CI and a funded native rehearsal. It does not complete R03,
qualify an RTO, enable generation/sales, or replace the original V3 operator.

The only image target is genuine application source
`c6a03af0636c7e9815159f807c9b126336d03233`, original CI `41500782361`, native Git
Build `32bc5596-a64b-4cf4-9cc0-311389d46f9d`, image
`sha256:2ebc717174904f6becc3309aedc5f487a47e31b7a5c60e4fc34b3494f6f8acb1`.
The current controller is a distinct committed successor of
`db8a25adefaf3bc2e00a61f7543f8a1862597adf`, with its own finite source fingerprint
and genuine five-job CI. C6 image provenance is checked
independently; C6's full source fingerprint cannot stand in for the controller.
The original application plan must describe a future actual db8 release and V3
fingerprint `23b1447987e1b3686646ce709c9898cc326e6a88d7d2379ccc085e0286c28b87`.
Neither d064 nor c75 N/A/M/S/C custody can be relabelled or transferred. No actual
db8 N/A custody exists yet; the restored pilot still serves e23/schema25. The new
operator host needs its own CI after these three paths are committed; base
db8 CI alone does not qualify it. No pending CI, synthetic fixture, or raw
dictionary is native proof.

All 289 application Python/JSON files have the same reviewed digest
`9eca9ce7c267fab5789b292a4937bf7eab7c12cf37140209223702636edd677e` in C6 and
db8. This supports the finite compatibility scope. It does not prove the images
are identical or fix a defect shared by their application code. The fixed current
host metadata reader remains in use. No new application Build is required solely
for this host-only operation; container-resident changes need a genuine new Build.

## Accepted state and original custody

Only the stable state after all original M/N/A/S/C owners completed is accepted:

- Original maintenance M closed the expected e23 API and paused the three schedules.
- Original N has its exact schema26 start, transaction-ready, commit ACK and checked
  records. The installed schema26 catalog, TLS/physical database, roles/ACLs and
  evidence fence are freshly verified; these local records alone grant no DDL.
- Original A promoted all three db8 services, retaining actual ACK/LRO and checked
  metadata. Current db8 HTTP health is deliberately not a recovery prerequisite.
- Original S binding and all three original schedule restores are checked ENABLED,
  with the unchanged original configurations. Partial/unknown restores refuse.
- Original C pause and restore have terminal actual operations and exact original
  source/template/traffic lineage. Its old paused fingerprint is reconstructed
  from the immutable pause revision and original plan, rather than accepting a new
  arbitrary coordinator fingerprint.

Queues, invokers, identities, numeric secret and policy versions, fresh scanner/TeX
acceptance, all resource configurations, liabilities and closed/pilot/privacy/
Stage5/metadata-read flags remain mandatory. The new plan explicitly checks V3
flags: an inherited exact-type dispatch would otherwise skip that block.
This first version stays in the original A resource epoch: a later document-worker
rotation or unrelated configuration change refuses even a new recovery capture.
Adopting a later genuine rotation needs a separately reviewed explicit handoff;
recapturing alone cannot make the original A pin say something different.

The original plan files and record bytes stay immutable. One new purpose binding
is appended to the ORIGINAL A journal, before the first recovery write, identifying
the exact new recovery plan and private journal. All four original owners are
locked in fixed order. A different nonce/directory cannot replace this binding or
evade a possible send. This version permits one recovery operation per original A;
it is not a generic chain of rollback targets.

## Private preparation and commands

Use the exact new committed checkout with the locked Python/SDK. Do not run from a
C6 checkout with a copied newer helper, change GITHUB_SHA, or execute an archive.
All paths below are absolute, owned and private. Each journal directory must
already exist with mode0700. Plans and custody files use mode0600. Keep credentials
in the existing credential manager and RAM, never in these files or shell history.

Prepare a private custody JSON with exactly these four keys; each value is the
original absolute plan path, SHA256 of its exact file bytes, and original journal
directory. S resides in M's original journal:

```json
{
  "maintenance": ["/private/original/M.json", "<64 hex>", "/private/original/M-journal"],
  "migration": ["/private/original/N.json", "<64 hex>", "/private/original/N-journal"],
  "application": ["/private/original/A.json", "<64 hex>", "/private/original/A-journal"],
  "coordinator": ["/private/original/C.json", "<64 hex>", "/private/original/C-journal"]
}
```

For EACH invocation create a fresh empty private bytecode directory outside the
checkout. The source entry requires isolated Python, no bytecode writes and that
empty cache, and rejects dirty source, linked files and ignored import origins.
It inspects names/modes/object IDs for the complete tree, but opens only verified
execution Python and the explicit existing V3 proof/config/public-delivery union.
The sole exception under `backend/evals` is six known public Courier runtime
assets: Regular/Bold/Italic/BoldItalic TTFs, OFL.txt and provenance.json. Every
other evaluation corpus/input/output/oracle/selection path is excluded before open.
The import fence admits only checked execution code, not proof or corpus data.
Inherited contract/archive helpers run inside a scoped canonical Git environment:
ambient GIT_* overrides are removed, replacement/lazy-fetch/prompt and external
global/system configuration are disabled for those reads, then the original Git
environment is restored exactly without logging values. Controls replace the
actual C6 and original-prior archive commits with a tiny owned code-only tree;
the normal raw archive changes while the guarded proof uses canonical originals.
Do not use a stale/prepopulated cache or print environment contents.

```sh
"$PYTHON" -I -B -X "pycache_prefix=$EMPTY_PRIVATE_CACHE" \
  "$ROOT/backend/operations/schema26_compatible_recovery.py" source --root "$ROOT"
```

Native preparation requires `CLOUDSDK_CORE_DISABLE_FILE_LOGGING=true` and
`CLOUDSDK_CORE_LOG_HTTP=false`, actual successful CURRENT controller CI, legitimate
provider/SQL read access and separately reviewed mutation authority. With the exact
custody-file hash, a fresh32-hex nonce and a new private output plan:

```sh
"$PYTHON" -I -B -X "pycache_prefix=$EMPTY_PRIVATE_CACHE" \
  "$ROOT/backend/operations/schema26_compatible_recovery.py" capture --root "$ROOT" \
  --custody "$CUSTODY" --custody-sha256 "$CUSTODY_FILE_SHA" \
  --host-ci-run-id "$ACTUAL_CONTROLLER_CI" --nonce "$FRESH_NONCE" --plan "$RECOVERY_PLAN"
```

Capture reads native source/CI/Build, original custody and current resource/SQL
state; it makes no Cloud Run mutation and does not append the A binding yet.
Review the exact plan/file hash before the first stage/advance. Keep C6 APP_RELEASE
and digest distinct from the new host release/source fingerprint.

```sh
"$PYTHON" -I -B -X "pycache_prefix=$EMPTY_PRIVATE_CACHE" \
  "$ROOT/backend/operations/schema26_compatible_recovery.py" stage --root "$ROOT" \
  --plan "$RECOVERY_PLAN" --plan-sha256 "$RECOVERY_FILE_SHA" --journal "$RECOVERY_JOURNAL"
```

`stage` first appends the original-A binding, then stages all three C6 revisions at
zero traffic with actual tagged target health. `advance` uses the same arguments
and the stock order: stage all three, then promote the two workers before API.
Every staged participant must pass a fresh joined health check before promotion.
Only permitted image/source-label/APP_RELEASE/revision fields change; existing
aliases move explicitly and no extra product, model or paid gate opens.

`reconcile` uses the same arguments and original journal. It reads only started
actions, observes the same actual LRO/native effect and never sends an action or
starts the next participant. An unknown/no-effect result remains unknown; do not
retry its PATCH, replace its owner, make a new directory/nonce or reset the binding.
After a fully reconciled effect, review `advance` separately for remaining unsent
actions. `observe` is the new schedule/coordinator-aware final read-only handoff;
it rechecks C6 health and the exact new journal phases. Old V3/S verifiers remain
strict and will reject their now-stale live generation/resource expectations.

The plan hash printed by capture is a FILE SHA256 (including canonical newline).
The journals additionally bind the canonical plan-object hash internally. Neither
hash may be substituted for the other. No command downgrades the database, removes
the fence, changes IAM or credentials, changes credit charges or refunds, replays a model call,
retries an unknown application, or changes an approved file.

## Required native acceptance and remaining R03 work

Before native rehearsal, independently review the exact new three-file source and
owned controls, qualify current-host CI, and freshly reread the genuine C6 source,
CI and native Build/digest. Recheck revision census/headroom under the unchanged
existing bounds. A complete recovery creates three revisions and at most six
planned PATCHes; retries are not a capacity strategy. Retain the narrow temporary
health-token capability through final checks, then remove exactly that binding.
The operator itself creates no IAM grant or new credential.

Reserve a finite infrastructure/rehearsal budget from actual configurations and
remaining funding. Include native GETs, database read-only observations, three
revision cold starts, health calls and synthetic traffic; retain unknown expenses
and stop at the cap. There are no required Gemini calls, customer wallet debits or
external employer submissions. Never claim the test is zero-cost.

A genuine exercise must measure detection, capture, stage, promotion and final
signed-in BFF/API journeys, demonstrate C6 evidence/transcription writes on26 and
the old e23 writer's rejection, and show before/after immutable sources, accepted
quotes/work, credits/ledger history, approved file hashes and unknown liabilities
preserved without double charge/retry. Owned ports prove control flow only; they
do not establish native SDK deployment, database acceptance, funding or RTO.
Owned source children use sparse code/proof/public-asset snapshots and counted
filesystem/Git data-open denials. Earlier broad-tree hashing results are retained
with their explicit scope correction; they are not finite-scope authoring proof.

Pre-resume/partial/uncertain original-owner recovery, a source-code rollback that
actually differs from the current application, separately funded model/quality
fallback, frontend recovery, full200/canary/financial/scale acceptance and complete
R03 remain unqualified. This slice must not be reported as completing them.
