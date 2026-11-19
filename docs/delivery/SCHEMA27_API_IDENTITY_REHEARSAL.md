# API identity rehearsal after the schema27 rollout

This operator stages only the API's service-account change at zero percent traffic.
It has no promotion, IAM grant, payment-secret binding, HTTP probe or SQL operation.
The application release is fixed to `70a1ef77c19c7f00570bc159b3db514b38d112d0`.
The API image, source environment, database references, flags and other configuration
must stay byte-equivalent under their retained hashes. Analysis, employer and résumé
coordinator workloads, their identities and their IAM policies remain unchanged.

The dedicated account is
`hirewiz-api-20261117@ai-resume-parser-482412.iam.gserviceaccount.com`.
Capture reads its actual enabled, keyless native identity and the shared account's
actual identity separately. Their numeric UIDs must differ. Neither UID is replaced
with the other's UID to make an existing controller accept the transition.

## Review and capture

Finish the current base and schema27 rollout first. Use a clean committed, reviewed
checkout containing this operator; the journal binds its executing checkout's HEAD,
tree and source hashes. Running from another working directory does not select a
different source root. A later source change refuses this journal's actions before
native client construction. Preserve that checkout and the original journal until
rollback and reconciliation are complete.

The native entry point uses only the stock normal-manager identity. It does not log
in, accept a token, select another project, accept a transport/plugin or read secret
payloads. Native service responses can contain existing inline credentials; they
stay in RAM. The plan and receipts retain only identities, IAM metadata, traffic,
native resource IDs/ETags/generations, source and configuration hashes, and the LRO
name. Capture performs native metadata reads, with zero mutations. These commands
are a future execution recipe, not evidence that native rehearsal occurred.

From that checkout's repository root, after reviewing the exact new revision names:

```sh
python -B -m infra.gcp.schema27_api_identity_operator capture \
  --journal /absolute/private/api-identity-original \
  --candidate-revision ai-resume-parser-apiid-20261119 \
  --rollback-revision ai-resume-parser-apiid-return-20261119
```

The journal must not already exist; its parent must already be private and owned.
Capture creates a mode0700 journal and mode0600 metadata records. Both proposed
immutable revisions must be absent. The default candidate has no tag. If the
separately reviewed proposal explicitly needs a tag for subsequent probes, append
`--candidate-tag api-identity-review` during capture. That one named tag has zero
percent traffic; there is no percentage or promotion option. Its public address
does not prove effective credential access or authorize a probe.

Review the printed `plan_sha256` against the private `plan.json`, including both
actual UIDs, image/configuration hashes, original physical traffic and aliases,
candidate/rollback names, optional tag, source state and original ETag. The digest
also binds the original journal's absolute path, device and inode, so editing a
copied journal's binding cannot establish original custody. The digest
is local custody and request intent; source code, a passing test and a plan digest
do not constitute IAM or production authorization.

Capture the original document IAM **before** the separately reviewed IAM change.
The existing pure validator requires exactly the dedicated API's unconditional
invoker addition to each private document service, retaining the original shared
caller and all other bindings. A fresh least-privilege IAM proposal, effective
worker-denial checks, the native grants, credential probes and the three managed
Razorpay bindings remain separate work. This operator cannot execute them. If the
baseline, ETag or configuration changes, prepare a fresh proposal and capture;
do not reconstruct a started journal or adopt another candidate.

## Zero-traffic stage and readback

After that separate IAM plan is approved and its narrowly scoped grants are applied,
use the reviewed digest, within six hours of capture:

```sh
python -B -m infra.gcp.schema27_api_identity_operator stage \
  --journal /absolute/private/api-identity-original --plan-sha256 REVIEWED_64_HEX
python -B -m infra.gcp.schema27_api_identity_operator observe \
  --journal /absolute/private/api-identity-original --plan-sha256 REVIEWED_64_HEX
```

Stage checks fresh metadata, actual identities, document caller delta, unchanged
workloads, original ETag and exact request digest. It pins any original LATEST
traffic to its captured physical revision, preserving original percentages and
aliases. The one PATCH uses the current ETag and fixed `template,traffic` mask,
with no write retries. Durable `stage.started` precedes the send; native ACK and
readback are separate records. A known pending LRO reports
`same_native_operation_pending` and retains that same native handle.

After any unknown result or pending LRO, use only:

```sh
python -B -m infra.gcp.schema27_api_identity_operator reconcile \
  --journal /absolute/private/api-identity-original --plan-sha256 REVIEWED_64_HEX
```

Reconcile performs reads and can append the checked receipt for an already started
action. It never sends a PATCH, starts the next phase or invents a lost native ACK.
If no ACK survived but the exact owned effect is independently visible, the receipt
explicitly records `operation_ack_retained: false`. If the effect cannot be proved,
it remains held. Repeating `stage` cannot replay a started write. A partially
published journal claim is also held for inspection. A copied journal fails its
original absolute-path/device/inode custody checks.

## Functional old-identity rollback

Use the same preserved checkout, original journal and reviewed digest:

```sh
python -B -m infra.gcp.schema27_api_identity_operator rollback \
  --journal /absolute/private/api-identity-original --plan-sha256 REVIEWED_64_HEX
```

Rollback requires the stage's verified native effect and revalidates the entire
saved proof chain against the fixed plan before another write. It first creates
the fixed, absent rollback revision from the freshly read current API template,
changing only revision name and service account back to the **actual original**
identity. It keeps the staged physical traffic during this step. Once that new
revision and same LRO are verified, a separate traffic PATCH retargets every
captured original alias name and percentage to the equivalent old-identity
rollback revision and removes only the owned candidate tag.

`rollback-template.started/ack/checked` and
`rollback-traffic.started/ack/checked` are distinct once-only phases, each using a
fresh ETag and native readback. Pending/unknown operations require `reconcile`;
reconciliation never starts an unsent traffic phase. Run `rollback` again only
after reconciliation has verified the preceding phase, to start the remaining
unsent phase. Capture expiry prevents a new stage, while original-journal rollback
and read-only reconciliation remain available with the same pinned source.

The restored effective baseline uses a **new immutable rollback revision**, not
the original revision's resource ID. Original, candidate and rollback revisions
are retained and independently fingerprinted even when unreachable by traffic.
No revision, IAM grant or evidence is deleted; separately owned document-invoker
grants remain for their own later review.

## Existing shared-identity assumptions before promotion

No production promotion is implemented here. The current generic rollout and
maintenance readers still assume the API shares `hirewiz-runtime-20261014`:

* `infra/gcp/rolling_release.py:stage_body` constructs that shared-account template.
* `infra/gcp/rolling_native.py:NativeRollingBoundary._identity` verifies only the
  shared account and its plan UID.
* `infra/gcp/schema27_continuity_bridge.py:verify_global` uses that identity profile.
* `infra/gcp/document_application_context.py:ACCOUNTS` and `serving` require the
  shared API account across created/ready and reachable revisions.
* `infra/gcp/document_refresh_transition.py:ACCOUNTS` and caller checks use the
  shared API identity, including the documented maintenance caller projection.

A zero-traffic candidate changes latestCreated/latestReady and can therefore hold
those maintenance readers even while serving traffic stays on the original
revision. Functional rollback restores the old identity in the active template
and latest revision, but retained dedicated-account document grants also require
an explicit compatible caller-policy review. Mixed-identity maintenance and app
controller qualification must be implemented and reviewed before promotion; do
not forge the old UID or broaden an existing global account allowlist. Effective
worker denial of payment secret reads and dedicated-API impersonation remains a
separate native verification requirement before any payment-secret grant.

The separately versioned [mixed-identity successor](SCHEMA27_MIXED_IDENTITY_SUCCESSOR.md)
adds an explicit forward promotion, later Source27 app rollout and opt-in current
maintenance profile. It preserves this rehearsal's code pins and original journal;
it does not change the shared defaults or turn this operator into a promotion tool.

## Local verification

From `backend`, using the existing frozen backend runtime:

```sh
python -B -m pytest -q tests/test_schema27_api_identity_operator.py \
  tests/test_schema27_api_identity_readback.py
ruff check ../infra/gcp/schema27_api_identity_operator.py \
  ../infra/gcp/schema27_api_identity_readback.py \
  tests/test_schema27_api_identity_operator.py tests/test_schema27_api_identity_readback.py
mypy ../infra/gcp/schema27_api_identity_operator.py ../infra/gcp/schema27_api_identity_readback.py
```

Controlled metadata fixtures prove custody, source guards, exact configuration,
identity separation, traffic behavior, unknown-write handling and both rollback
phases. They make no native provider, auth or SQL calls and do not prove deployed
IAM effectiveness, application credentials, merchant readiness or paid launch.
