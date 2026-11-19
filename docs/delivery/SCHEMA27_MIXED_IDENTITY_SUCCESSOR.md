# Post-schema27 dedicated API identity and application successor

This forward profile keeps the dedicated API identity through promotion, later
Source27 application deployment, and ordinary document maintenance. Analysis and
employer workers retain the actual shared account. The résumé coordinator keeps
its own account. Both API/shared numeric UIDs are freshly checked; neither is
normalized to the other. Historical shared-account controllers and document
binary/corpus receipts retain their original scope.

These are future execution recipes. No identity promotion, provider probe, IAM
grant, payment binding, application deployment or maintenance Job was executed
while implementing this source. Finish the existing base rollout, schema27
migration and separately reviewed restore rehearsal first. The least-privilege
IAM proposal, effective worker denials, API credential probes and three managed
payment-secret bindings remain separately owned work.

## Preserve two controller roots and the original journals

**A: runtime70 identity only.** Preserve the clean frozen rehearsal checkout and
its original journal described in [the rehearsal runbook](SCHEMA27_API_IDENTITY_REHEARSAL.md).
The new `schema27_api_identity_promotion` captures that journal's checked stage
as read-only predecessor evidence into a distinct original private journal. It
pins the frozen rehearsal/reader code hashes, actual runtime70 template/source,
unchanged image, database references, flags, other workloads, exact IAM delta and
the current forward controller's complete source. It has no application target
or Build. It deliberately does not use the inherited controller/target app-byte
equivalence rule: its only writes are identity traffic promotion or functional
old-identity rollback on the unchanged runtime70 template.

**B: later application rollout.** Preserve a separate clean integrated controller
root containing this same identity/helper delta and the final application bytes.
`schema27_mixed_app_successor` reuses the existing Source27 application successor,
source qualifier, database/catalog checks and native release protocol. Its
inherited `_verify_sources` still requires the controller's backend/app manifest
to equal the target root's manifest before CI/Build acceptance. A controller root
with runtime70 app bytes cannot deploy the different final app by rewriting a
SourcePin. The separately qualified final application root and its single genuine
Build supply the target; this implementation creates no additional app Build.
The prior root remains independently qualified with its actual bytes and pins.

Keep A unchanged for its journal's observe/reconcile/rollback. B verifies the
unchanged handoff helper hashes and original A journal custody, while binding its
own full source/CI and exact application target. Never copy, retarget or reconstruct
an original journal. Native actions require the reviewed plan digest, original
path/device/inode, current preimage and ETag. Unknown started writes stop for
read-only reconciliation; they are never replayed.

## A: capture, promote, observe and roll back

Run from the clean reviewed forward controller root, after the frozen rehearsal's
zero-traffic stage and separate IAM work have been verified. Paths below are
placeholders for the actual owned private evidence, not installed defaults.

```sh
python -B -m infra.gcp.schema27_api_identity_promotion capture \
  --rehearsal-journal /absolute/private/api-identity-original \
  --rehearsal-plan-sha256 REHEARSAL_64_HEX \
  --journal /absolute/private/api-promotion-original
python -B -m infra.gcp.schema27_api_identity_promotion promote \
  --journal /absolute/private/api-promotion-original --plan-sha256 PROMOTION_64_HEX
python -B -m infra.gcp.schema27_api_identity_promotion observe \
  --journal /absolute/private/api-promotion-original --plan-sha256 PROMOTION_64_HEX
```

Capture creates an absent mode0700 journal under an already owned private parent.
Review its digest and typed metadata before using it. Promotion freshly verifies
the retained V5 native 17-case binary/corpus evidence and current caller policy.
The new forward receipt retains both actual full document IAM policies, their
latest ETags, their exact independently verified caller delta, and the separately
scoped historical binary component. Pure historical configuration validators see
only the derived original-policy component; native reader responses are never
substituted and the receipt does not claim historical V5 verified the new caller.

The API traffic PATCH retargets the captured original aliases and percentages to
the already staged dedicated-identity revision. Its optional owned candidate tag
stays at zero percent. Workers/coordinator templates, IAM, source, database and
flags remain fixed. Promotion has its own durable started/ACK/checked records.

After any pending or unknown outcome, use the same owned handle through reads:

```sh
python -B -m infra.gcp.schema27_api_identity_promotion reconcile \
  --journal /absolute/private/api-promotion-original --plan-sha256 PROMOTION_64_HEX
```

Before a later application template/source change, functional old-identity rollback
uses A and its original journal:

```sh
python -B -m infra.gcp.schema27_api_identity_promotion rollback \
  --journal /absolute/private/api-promotion-original --plan-sha256 PROMOTION_64_HEX
```

It first creates the fixed absent rollback revision from the current unchanged70
template using the actual old identity. A separate once-only traffic phase moves
all original aliases/percentages to that equivalent old-identity revision and
removes only the owned candidate tag. Original/candidate/rollback revisions and
evidence remain retained. Each phase has fresh ETag, started, ACK and same-LRO
readback. Pending phases need reconciliation before another unsent phase starts.
Rollback remains possible when the fresh promotion document proof is unavailable.
The effective baseline is restored under a new immutable revision, not the old
resource ID. A is intentionally refused after B changes the app template/source;
do not overload the old identity plan with a later application's configuration.

## B: deploy the actual qualified application with the API identity retained

After verified A promotion, export its private owner profile without copying the
journal. The absent output is created mode0600 and its SHA-256 is printed:

```sh
python -B -m infra.gcp.schema27_api_identity_promotion owner-profile \
  --journal /absolute/private/api-promotion-original --plan-sha256 PROMOTION_64_HEX \
  --profile-output /absolute/private/api-owner-profile.json
```

Use the existing Source27 capture config shape: exact `source` and `prior`
SourcePins (`release`, `image_digest`, `build_id`, `ci_run_id`), and only
`controller_ci_run_id`, fresh `nonce`, and actual document `workers_release`
options. It has no IAM, candidate payment-policy, cache or secret-binding option.
The B journal must be a fresh, owned empty mode0700 directory. With the actual
integrated/target/prior roots and their reviewed hashes:

```sh
python -B -m infra.gcp.schema27_mixed_app_successor capture \
  --root "$mixed_controller_root" --target-root "$final_app_root" --prior-root "$prior_app_root" \
  --identity-profile /absolute/private/api-owner-profile.json --identity-profile-sha256 OWNER_PROFILE_64_HEX \
  --capture-config /absolute/private/mixed-app-config.json --capture-config-sha256 CONFIG_64_HEX \
  --plan /absolute/private/mixed-app-plan.json --journal /absolute/private/mixed-app-original
python -B -m infra.gcp.schema27_mixed_app_successor stage \
  --root "$mixed_controller_root" --target-root "$final_app_root" --prior-root "$prior_app_root" \
  --identity-profile /absolute/private/api-owner-profile.json --identity-profile-sha256 OWNER_PROFILE_64_HEX \
  --plan /absolute/private/mixed-app-plan.json --plan-sha256 APP_PLAN_64_HEX \
  --journal /absolute/private/mixed-app-original
```

The same arguments with `advance`, `observe`, `reconcile` or `rollback` select the
existing staged worker-first/API-last rollout or its qualified traffic rollback.
API stage templates retain the dedicated account; worker stage templates remain
stock. Every action rechecks actual schema27, retained ACLs and liabilities,
numeric runtime database binding, actual old/new UID, native scanner evidence,
full caller policy, resource pins, source/CI/Build and reachable revisions. B's
application rollback retains the dedicated API identity and qualified prior app
traffic; it is distinct from A's functional old-identity rollback.

## Publish maintenance metadata only after promotion

The separate public export contains only allowed identity/resource/source IDs,
policy-content digests and the explicit unconditional API invoker additions:

```sh
python -B -m infra.gcp.schema27_api_identity_promotion profile \
  --journal /absolute/private/api-promotion-original --plan-sha256 PROMOTION_64_HEX \
  --profile-output /absolute/private/reviewed-api-identity.json
```

Review and commit that exact metadata as
`infra/document-processing/reviewed-api-identity.json` in the actual maintenance
source. It contains no complete policy/member list or private journal path. Full
actual policies and ETags stay in the original private receipts. No publication is
created by this source change; the shared default remains unchanged. The reader
requires the exact committed blob and unchanged promotion code, and freshly
verifies both real UIDs and both full native policy digests each time.

Opt in to ordinary recurring refresh from that reviewed source:

```sh
python -B -m infra.gcp.schema27_mixed_maintenance refresh \
  --output-directory /absolute/private/document-refresh
```

Add only the existing reviewed publication/history arguments when applicable.
The equivalent existing entrypoint is `backend/scripts/document_refresh.py --mode refresh`
with `--reviewed-application-context --published-api-identity`. Existing native
schedules, funding budgets, expiry behavior, execution holds, maxRetries=0 Jobs
and close/worker/pins/open transition remain in force. An unfinished identity/app
transition is refused before a new Build/Job. Current serving/created/retained
aliases must match the published per-service identity and qualified app source.
Fresh UID verification precedes each caller PATCH. Full forward authorization
receipts accompany the independently scoped V5 binary receipts privately.

New entrypoints print only a fixed denial code, sanitized exception class, typed
HTTP status/known uppercase provider reason and safe operation label. They never
print SDK messages, URLs, headers or payloads. New exact-resource read proof uses
one deadline and at most three attempts only for typed 429/502/503/504 failures.
No mutation retry, session substitution, credential export or global allowlist
change is introduced. A source-only qualification or controlled fixture result
does not claim native identity, IAM effectiveness or paid-launch approval.
