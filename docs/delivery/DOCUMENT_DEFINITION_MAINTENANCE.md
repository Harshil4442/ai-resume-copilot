# Immutable document-definition maintenance

This is part of the existing resume/deployment outcome. The dedicated maintenance
identity and accepted native successor are provisioned. Current serving versions,
observed runs and remaining limitations are recorded in
[the delivery board](AUTO_APPLY_PROGRESS.md). No source test or local Docker
report establishes Cloud Run acceptance.

## Runtime and exact acceptance

`.github/workflows/document-refresh.yml` is the unattended runtime. It runs on the
default branch only, with an environment named `document-maintenance`, serialized
runs, pinned Actions, standard Google WIF credentials and the existing SDK/Docker
checker. It never uses the retained human SDK session. Configure these environment
variables after the coordinator baseline is complete:

- `DOCUMENT_REFRESH_WIF_PROVIDER`: reviewed provider restricted to this repository,
  default branch and workflow/environment.
- `DOCUMENT_REFRESH_SERVICE_ACCOUNT`: exactly
  `hirewiz-document-refresh@ai-resume-parser-482412.iam.gserviceaccount.com`.
- `DOCUMENT_REFRESH_CONTROLLER_UID`: actual independently read 21-digit native UID
  of that account. There is no source placeholder UID that authorizes deployment.

The controller rejects a different ADC identity, disabled/replaced account, proxy,
CA override, redirect or mutation retry. Standard WIF/metadata refresh is used;
there is no downloaded service-account private key or token in artifacts.

`refresh.Dockerfile` always starts from the immutable renderer
`0a7e550623f0485945f9bbe52573091aabedfd612eb353e6a8bbb920e2ffa94f`.
Only `/var/lib/clamav` changes. The prior definition directory is replaced, rather
than merged, and the final user remains 65534. No package/font/Python/renderer
updates occur in this pipeline. Every new build has a fresh nonce, generation-bound
one-file GCS archive and native Cloud Build result using the existing repaired
builder. A genuine 25-case local check must pass with all required source
hashes, renderer/font/policy and exact pulled digest unchanged. Definitions must
be younger than 24 hours; the runtime's original three-day refusal remains.

Next, a new owned Cloud Run Job executes the accepted V2 ten-case
payload with only its image literal changed. Native Job/Execution UIDs, singleton
execution, actual logs, twelve runtime hashes, font manifest, sandbox policy,
cleanup and native identity/IAM are joined to the Build and generation-bound GCS
source. The consumed historical d2ba job/journal is never used again. Serializing
`NativeAcceptance` does not create authority: fresh native reads are required.

## Controlled maintenance window

The controller freshly checks the worker and all three consumers:
`ai-resume-parser`, `hirewiz-analysis-worker`, and `hirewiz-resume-coordinator`.
It then performs these existing deployment operations with conditional native
ETags, unique revisions, metadata-only once markers and actual LRO readbacks:

1. Capture and close direct-upload/optional-generation admission on API/analysis,
   and scan/direct-upload admission on the coordinator. Coordinator cleanup stays
   enabled exactly as it was.
2. Freshly reverify the native successor, promote the document worker to it, and
   validate its original complete configuration, IAM, Ready revision and 100%
   untagged traffic.
3. Roll the four exact document image/policy/renderer/font pins on all three
   callers, preserving application images, secrets, networking and other settings.
4. Freshly verify the complete native worker/caller join, restore each captured
   flag (including previously absent flags), and verify it again.

This is a bounded **fail-closed maintenance window**, not a claim that synchronous
callers drained. An older in-flight caller may finish against the older worker or
receive the new image and reject its receipt. A short 503/retry interval is allowed.
A mismatched upload creates no resume, analysis reservation or charge. A mismatched
DOCX tailoring result creates no version and releases its existing reservation
exactly once. Existing approved artifacts retain their historical sealed receipts.
No digest range, runtime updater, audience override or weakened receipt is used.

An unknown PATCH/Create/Run/Delete stops immediately. The journal retains the
original request hash and exact acknowledged native handle; missing ACK means
unknown. `--mode reconcile --transition-journal /private/prior/transition` makes
only GETs and never reopens flags, starts a new mutation or authorizes a resend.
If interruption leaves mixed pins, a new run refuses before any further transition.
A prior failed refresh artifact also blocks a new Build before any new operation;
that original unknown run must be reconciled explicitly.
A root-reviewed remaining-only recovery is required; deleting the journal is not
recovery. A completed native acceptance Job is retained while its image serves.

## Explicit recovery after a definite pre-transition failure

Scheduled runs never replay failed mutations. For the supported case—one
completed synthetic Job failed its identity preflight, all six corpus cases stayed
unattempted and sandbox cleanup completed—an operator may set
`recover_failed_native_job` to that exact Job name in a `workflow_dispatch` with
`mode=refresh`. The CLI equivalent adds `--recover-failed-native-job JOB` to the
existing `--mode refresh --publish-candidate --prior-history-directory ...` command.

Before a new build, the controller freshly rejoins the eleven original artifact
files, source generation and Build acknowledgments, successful original Build,
Create/Run acknowledgments and UIDs, singleton failed Execution with zero retries,
and the unique bounded native failure report. Missing acknowledgments, in-flight
or unknown outcomes, other failure stages, failed cleanup, acceptance/deletion
markers or any transition directory refuse recovery. Current worker configuration,
native acceptance when applicable, all three caller pins and strict serving
templates must still pass their original checks.

A metadata-only claim under the existing source prefix is keyed by the original
Job UID and created once with `ifGenerationMatch=0`. It binds the original journal
manifest and exactly one new build nonce. An existing claim or unknown claim
acknowledgment cannot start another pipeline. No new IAM permission is needed.
The original Job is never rerun or deleted; diagnostic probes are not acceptance
evidence. The new candidate must acquire fresh definitions and pass the complete
unchanged 25 local and ten native cases before any transition.

The new artifact retains every original journal byte and the immutable claim
disposition under `terminal-recoveries/`. While the original failed artifact is
among the three retrieved prior runs, normal runs freshly reverify and carry its
disposition; the failure is never relabeled as serving. Once that artifact leaves
the bounded history window, later successful maintenance drops the unneeded copy
and stops depending on expired old logs, operations or source objects. Existing
historical artifacts retain their normal retention period; the immutable consumed
claim stays and prevents another recovery of that Job. A new unknown/partial
successor still blocks maintenance and requires same-operation reconciliation.
Deleting a journal or claim is not recovery.

## Acknowledged Build stopped before native acceptance

A separate supported history shape contains exactly five files: Build intent,
source-upload acknowledgment, Build-create intent, Build acknowledgment and native
Build identity. It contains no local proof, native Job intent or transition marker.
Before allowing a fresh refresh, read-only reconciliation validates every file,
the actual successful Build, generation-bound source archive and exact recipe.
The historical V1 recipe is recognized only through its frozen source and base;
its candidate never supplies current acceptance authority.

The deterministic native Job must return an authoritative 404, and the current
accepted inspector and all caller pins must exclude the abandoned candidate.
An existing Job, unavailable read, changed acknowledgment or additional marker
still blocks the run. Original journal bytes and additive reconciliation evidence
are retained. The old Build is never replayed, resumed or promoted; a new candidate
uses a different nonce and must pass the full current acceptance pipeline.

## Schedule, proof retention and alerts

The daily 01:37 UTC run executes **the full refresh**, not candidate publication
alone. The `:17` and `:47` runs check the actual serving image and fresh native immutable-image
definition receipt (the initial baseline uses its actual image header). Successors
avoid an hourly multi-GB registry download, limiting monitoring egress and runtime.
The GitHub artifact retains all metadata-only JSON journals for fourteen days.
The next daily run retrieves up to the three newest owned refresh artifacts and
retains its byte-exact prior native journal inside the new artifact (including any
new Delete once/ACK), and uses it for UID/etag-scoped cleanup only after the new
worker and all three serving caller pins are independently verified. Missing prior
history alerts rather than inventing ACKs. Native Job cleanup does not delete the
current Job. Protect the fixed base, current accepted image and rollback image;
retain prior image/source objects under the reviewed registry/storage policy.
Image deletion is outside this controller; there is no broad registry delete right.

The monitor writes only a fixed nonsecret status/heartbeat to
`projects/ai-resume-parser-482412/logs/hirewiz-document-refresh`. At 24 hours before
the unchanged three-day deadline, or on expiry/unavailable state, it emits attention.
A failed operation makes a separate bounded best-effort unavailable notification;
it never retries the failed mutation. Missing monitor/auth/logging also triggers
absence monitoring.

`infra/gcp/document_refresh_alerts.tf` is opt-in, disabled by default, and requires
reviewed notification-channel IDs. Its alerts cover attention events and a missing
heartbeat for two hours. Root must activate those resources and exercise the
notification route; source presence is not a delivered alert.

## Identity/resource provisioning

Root provisions the dedicated keyless account and protected GitHub WIF/environment
only after the coordinator baseline. Use custom permissions/resource bindings for:

- Native account/project/worker/coordinator identity, user-key and IAM inventories.
- Build create/get and the existing repaired builder's `actAs`, without adding new
  builder grants; exact registry repository read and source-prefix create/get.
- Conditional updates on the document worker and the three consumer services;
  worker `actAs` and the corresponding exact existing consumer accounts.
- Fresh `hirewiz-doc-six-<nonce>` Job create/run/get/delete, singleton executions,
  regional operation GETs and synthetic Job log reads. Job creation is a parent
  operation: resource-level IAM limits must be tested against Google's actual IAM
  support rather than advertised as an unsupported name-prefix guarantee.
- Fixed maintenance log writes; GitHub token has read-only Actions artifact access.

No database, Secret Manager access, user/customer storage access, IAM write,
API-key creation, payment action, employer submission or registry delete is needed.
The local adapter also fixes every resource/method and denies those mutation routes.
Provisioning and an actual native full maintenance run remain root release actions.

### Exact service updater and native verification

Create the project custom role
`projects/ai-resume-parser-482412/roles/hirewizDocumentServiceMaintenance` with
exactly `run.services.get`, `run.services.getIamPolicy`, `run.services.update`
and `run.revisions.get`, at stage GA. Bind it directly to the dedicated
`hirewiz-document-refresh` account on the inspector and three listed consumers.
It grants no invocation, IAM modification, service creation/deletion, employer
worker management or secret access. Keep every existing invoker binding intact.

The source admits only this exact role/member binding. Native release and refresh
readers independently retrieve the actual enabled account, key inventory and role
definition whenever that binding is present. Missing metadata, user-managed keys,
additional role permissions/members, conditions, duplicate bindings or a changed
invoker set refuse. The unattended controller additionally matches the actual
account UID to `DOCUMENT_REFRESH_CONTROLLER_UID`; provision that value from native
IAM, never an example. Full service IAM hashes remain part of capture and rollback,
so grant changes after capture require a fresh review rather than changing a plan.

Provide `iam.roles.get` for the native role read, alongside account/key/policy
metadata reads on the controller, inspector and coordinator accounts. Grant
`iam.serviceAccounts.actAs` only on the existing builder, inspector, coordinator
and shared application runtime accounts. The controller needs image-download
permission only on the existing `cloud-run-source-deploy` repository; retain the
builder's separate registry/source/logging grants.

The existing `ai-resume-parser-482412_cloudbuild` bucket has UBLA disabled. Preserve
its settings: a project-level custom role with only `storage.objects.create` and
`storage.objects.get` may use the supported inherited condition below. This does
not grant list, overwrite or delete, and other IAM/ACL grants must be audited
separately.

```cel
resource.type == 'storage.googleapis.com/Object' &&
resource.name.startsWith('projects/_/buckets/ai-resume-parser-482412_cloudbuild/objects/hirewiz-document-refresh-source/')
```

Cloud Build creation and fresh Cloud Run Job creation are parent-authorized;
the controller's exact recipe and `hirewiz-doc-six-*` checks are application
boundaries, not IAM body/name restrictions. The existing project log query also
needs project `logging.logEntries.list`; its synthetic-job filter is not an
authorization boundary. Do not claim these grants are scoped by unsupported
Cloud Run resource-name conditions. Use a separately configured log view and
matching reader source if a narrower log-read boundary is required.

The protected GitHub environment is `document-maintenance`, restricted to `main`.
WIF admission must bind numeric repository ID `1127941014`, owner ID `118929591`,
`refs/heads/main`, exact `workflow_ref` ending in
`/.github/workflows/document-refresh.yml@refs/heads/main`, and the environment
subject. This standalone workflow does not rely on the reusable-workflow-only
`job_workflow_ref` claim. Enable the required STS API and provision the three
environment variables above, then merge the reviewed source to the default branch
before dispatching the existing workflow. Source validation and a workflow dispatch
alone do not establish native refresh or alert delivery.

Provider contracts: [inherited conditional grants](https://docs.cloud.google.com/iam/docs/conditions-overview),
[Cloud Storage IAM conditions](https://docs.cloud.google.com/storage/docs/access-control/iam),
[Cloud Run Job creation](https://docs.cloud.google.com/run/docs/reference/rest/v2/projects.locations.jobs/create),
[Cloud Run resource access](https://docs.cloud.google.com/run/docs/securing/managing-access),
and [Logging query authorization](https://docs.cloud.google.com/logging/docs/reference/v2/rest/v2/entries/list).

## Ordinary future application releases

The baseline accepted image keeps its original verification path. A successor is
admitted only through `verify_current_successor_for_release`, using the existing
normal retained SDK manager's unchanged account/project/CA/proxy/credential guards.
That explicit read-only entrypoint verifies the same native Build/GCS/Job/log and
full worker configuration proof. It does not require maintenance ADC/controller UID
and cannot Create/Run/Delete/PATCH or publish a Build. Maintenance mutations keep
the separate exact WIF identity. Labels only locate evidence; they never authorize
an image or substitute for native proof.
