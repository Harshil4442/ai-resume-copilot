# App-only release after the patched scanner and prior app promotion

This is the existing Stage 7 app deployment path. It does not complete a launch
gate, approve spending or activate checkout. Preserve all original identity,
application, restore and scanner journals and their frozen source checkouts.

Use `infra.gcp.schema27_patched_app_successor` after the actual prior application
has been promoted and the patched document worker has current native acceptance.
It explicitly selects the existing patched document observers for the current
and prior app source. The exact mixed V1 plan, current expense/model policy
references, dedicated API identity and shared worker identities remain in use.
Its native catalog/ACL/liability, source, CI, Git Build, document, routing, IAM,
resource, template, flag and reachable-revision checks remain mandatory.

The controller and target must have identical qualified app bytes. The prior
checkout remains independently qualified with its real source, CI, Git Build and
image. A source descriptor grants no scanner or native processing acceptance.
The R02 app changes require their explicit current-source qualification; this
entrypoint does not make an unqualified source deployable.

Use the same private capture configuration and identity owner profile described
in [the mixed identity runbook](SCHEMA27_MIXED_IDENTITY_SUCCESSOR.md). The only
capture options remain `controller_ci_run_id`, a fresh `nonce` and the actual
document `workers_release`, plus the exact candidate/prior SourcePins. There is
no candidate policy, secret, identity, transport or feature activation option.

```sh
python -B -m infra.gcp.schema27_patched_app_successor capture \
  --root "$controller_root" --target-root "$qualified_app_root" --prior-root "$prior_app_root" \
  --identity-profile /absolute/private/api-owner-profile.json --identity-profile-sha256 OWNER_PROFILE_SHA256 \
  --capture-config /absolute/private/app-config.json --capture-config-sha256 CONFIG_SHA256 \
  --plan /absolute/private/app-plan.json --journal /absolute/private/app-original
python -B -m infra.gcp.schema27_patched_app_successor stage \
  --root "$controller_root" --target-root "$qualified_app_root" --prior-root "$prior_app_root" \
  --identity-profile /absolute/private/api-owner-profile.json --identity-profile-sha256 OWNER_PROFILE_SHA256 \
  --plan /absolute/private/app-plan.json --plan-sha256 PLAN_SHA256 \
  --journal /absolute/private/app-original
```

Use the same pinned arguments with `advance`, `observe`, `reconcile` or `rollback`.
Staging and worker-first/API-last promotion retain the existing once-only PATCH,
native ACK and fresh metadata/health checks. Unknown writes are reconciled using
their original journal; they are never replayed. Rollback restores the qualified
prior app traffic while retaining the dedicated API identity. All other template
values and paused processing flags remain unchanged; no policy pair, schema,
coordinator, document worker, database row or accepted financial obligation is
rewritten. Traffic generation and comparisons are the existing stock protocol.

No application image Build is included or started here. A new native Git-source
app Build, its registry/log/network costs and staged serving costs need a separate
bounded cost proposal and spending authorization. The existing total ₹225 scanner
allowance does not authorize that new Build. No future image digest or unobserved
promotion is assumed by this runbook.
