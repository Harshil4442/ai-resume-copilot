# Disabled private document worker deployment proposal

This proposal creates a separate PDF/DOCX inspection service in the existing
GCP project `ai-resume-parser-482412`, region `us-central1`. It creates no resource
and enables no API path. The new service `hirewiz-document-inspector` and identity
`hirewiz-document-inspector@ai-resume-parser-482412.iam.gserviceaccount.com` are
proposed names, not observed deployed resources. Existing API, database, model,
payment, employer and pairing configuration is not inherited or changed.

This follows the existing `infra/gcp` separation of workload identities and
private service IAM. Its Terraform module manages employer queues, accounts and
storage; Cloud Run deployments already use separate reviewed operators. This
proposal changes neither Terraform state nor the legacy release script.

`infra/gcp/document-worker.disabled.json` starts with no image, API wiring false
and deployment proof false. `infra/gcp/document_worker.py` renders a Cloud Run
**v2 REST Service body**, not a v1 YAML export and not a deployment command. It
requires a same-project Artifact Registry digest for this separate service, a
40-character source SHA and the exact current policy file. A source label or
caller-provided digest is an expected identity, not authenticated build evidence.

## Proposed service controls

| Setting | Proposed value |
| --- | --- |
| Runtime identity | Dedicated new account; no application grants or user-managed keys |
| Ingress | Internal only; existing API reachability remains unproved |
| Authentication | IAM invoker check enabled; no service-level bindings initially |
| Intended invoker after isolation review | Only `hirewiz-api@ai-resume-parser-482412.iam.gserviceaccount.com` |
| Sandbox | `sandboxLauncher: true`, second generation, `BETA` preview |
| Capacity | Concurrency 1; minimum 0, maximum 2 instances; 2 CPU, 4 GiB |
| Request timeout | 90 seconds; application phases remain bounded independently |
| Startup probe | TCP port 8080, 1-second timeout, 10-second period, 24 failures |
| Configured environment | Only immutable worker image and policy hashes |
| Volumes/sidecars/connectors | None |
| Entrypoint | `/usr/local/bin/python3 -I /opt/document/service.py` |

Google documents the launcher field and second-generation requirement. Its
sandboxes share the host's CPU and memory, so the proposed 4 GiB includes the host,
scanner and parser; native measurements must verify the budget. The preview has
limited support and remains subject to Google's pre-GA terms.
[Sandbox configuration](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes),
[v2 container fields](https://docs.cloud.google.com/run/docs/reference/rest/v2/Container).

Internal ingress needs a verified internal VPC path from another Cloud Run
service. Sharing a project and holding an identity token is insufficient. This
proposal does not alter the API's networking to invent that path.
[Ingress rules](https://docs.cloud.google.com/run/docs/securing/ingress).

The initial empty **service-level** policy is not an IAM deny: inherited grants
can still authorize requests. The later policy grants `roles/run.invoker` to the
intended API account only. Before using it, inspect effective inherited bindings,
groups/custom roles, trusted administrative roots, account key/impersonation access,
and resource-level access to secrets/storage/other services. No grants are proposed
for the worker account itself. Cloud Run still supplies its platform runtime
identity through the host metadata server; “secretless” here means no application
secrets or privileged application grants, not absence of a platform identity.
[IAM access control](https://docs.cloud.google.com/run/docs/securing/managing-access),
[service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).

## Existing child boundary and required actual checks

The existing worker uses the injected `/usr/local/gcp/bin/sandbox`, imports one
owned regular-file request tar, runs the scanner/parser inside it and explicitly
deletes the owned sandbox before releasing the response. It passes no `--env`,
`--allow-egress`, persistent bind mount, snapshot export or sync flags. Launcher
subprocesses receive only fixed PATH/HOME/LANG/TZ. The child defaults are not an
empty-environment claim: Google says host environment, secrets and metadata access
are withheld, and outbound access is blocked unless requested. Children can read
the host image filesystem, which must therefore contain only the reviewed seven
source files, dependencies and official definitions, never application credentials.
[Code execution](https://docs.cloud.google.com/run/docs/code-execution),
[sandbox CLI](https://docs.cloud.google.com/run/docs/reference/sandbox-cli).

The policy binds a 10-second absolute header/body read, launcher creation 10,
execution 45 and deletion 5 seconds. A 90-second Cloud Run timeout is a transport
ceiling, not proof that a child is killed. The API's worker HTTP budget is 75
seconds and its auth requests have a 5-second per-request timeout; no absolute
identity-acquisition deadline is claimed. Forced-delete failure remains unavailable
and overrides a clean/refused result. Inspection does not charge analysis units.

Actual deployment evidence must use synthetic noncandidate inputs to verify:

- Injected launcher availability, exact immutable Linux AMD64 source/image/runtime
  bytes, policy agreement and fresh official definition headers plus the eight
  existing native corpus cases. The current local ARM64 proof is not Cloud Run proof.
- Child inability to read a synthetic host environment canary, host metadata and
  external-network destinations; no real secrets, candidate files or outbound
  provider calls belong in these probes. Capture fixed boolean/category results only.
- Owned sandbox lifecycle cleanup on success, coherent antivirus refusal, parser
  refusal, timeout and forced-delete failure. A forced-delete failure must prevent
  release; a success envelope alone does not prove there are no surviving children.
- Unauthorized invocation rejected, the intended API identity accepted over the
  verified internal route, every retained revision/tag bound to the same reviewed
  policy, and native IAM/resource/credential isolation. A TCP startup probe proves
  listening only, not scanner freshness or child isolation.

## Bounded use and operator sequence

Run the helper from the reviewed source:

```sh
python infra/gcp/document_worker.py
python infra/gcp/document_worker.py render --image "$REVIEWED_DOCUMENT_IMAGE" --release "$REVIEWED_SOURCE_SHA"
python infra/gcp/document_worker.py check --snapshot "$PRIVATE_NATIVE_SHAPED_METADATA" --image "$REVIEWED_DOCUMENT_IMAGE" --release "$REVIEWED_SOURCE_SHA"
```

These commands perform zero cloud calls or mutations. `check` accepts at most
256 KiB of duplicate-free JSON. It checks service/revision readiness/configuration,
one configured latest target, allowed env, proposed service IAM, known public/worker project
grants, and account/key metadata. Missing/unknown credentials, mounts, sidecars,
conditional bindings or incompatible configuration fail with a fixed reason.
It accepts only documented/default false and zero omissions, preserving strict
types. Supplied output traffic status must point only to the matching latest ready
revision; missing output status is explicitly `traffic_status_checked: false` and
cannot establish routing. The result always says `untrusted_offline_snapshot`, effective IAM false,
runtime isolation false and API wiring false. It does not inspect image contents,
authenticate the capture, enumerate IAM ancestors or enable a release. Files are
not a native certificate. Keep native captures private; never include raw env in Git.

1. Build and independently review a fresh exact-source AMD64 worker image with
   official definitions; retain compiler/runtime/corpus proof and policy pin.
2. Review the operator's new-account/service creation permissions, inherited IAM,
   account-name collisions and verified API internal network route. Do not reuse
   the credential-bearing API identity as worker identity.
3. In a separately reviewed native operation, create the dedicated account without
   keys/grants and the new private service from the rendered body. Keep API worker
   URL unset. Stage only; retain resourceVersion/etag/UID and operation receipts.
4. Observe actual native configuration/IAM and run the synthetic isolation/lifecycle
   checks above through a separately bounded trusted test route. This proposal
   contains no deployer, IAM writer or runtime probe, and authoring it grants no
   permission to weaken existing gates.
5. After evidence review, apply only the intended API invoker grant and explicitly
   pin `/inspect`, canonical service audience, image and policy on the API. Verify
   the complete upload path before releasing it to users. Unknown outcomes require
   read-only reconciliation; rollback closes API wiring, never enables a fallback.
6. Rebuild with a new builder-owned definition nonce before the three-day header
   age limit, rerun freshness/corpus/isolation acceptance, and promote an immutable
   successor. Reused/cached definitions or stale headers remain unavailable.

Production setup, API reachability, inherited authority, native AMD64 image,
runtime isolation and clean shutdown are open. This proposal changes neither
monetary fencing nor the prior protected pairing/lifecycle integration hold.
