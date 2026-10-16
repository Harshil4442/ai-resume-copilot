# Private LaTeX production compiler

The previous LaTeX implementation had a working local Docker compiler and a
private HTTP client, but no isolated production compiler was deployed or wired.
The old HTTP wrapper directly spawned the compiler on its Cloud Run host. Its
deployment comments did not enforce isolation, so it is not the production entry
point. Production now has a separate `Dockerfile.cloud` and `cloud_service.py`.

Candidate source is sent over bounded stdin to Google's Cloud Run sandbox
launcher. The child starts with a read-only image root, no network, no ambient
environment, and only a fresh request-owned `/tmp` mount. It runs pdfLaTeX with
shell escape disabled, restrictive TeX file access and CPU/memory/file limits.
The host verifies the bounded PDF receipt and deletes the owned sandbox before
returning an artifact. Unknown cleanup quarantines the instance. Candidate source,
compiler logs and credentials are not put into command arguments or logs.

The trusted TeX controller initializes and verifies Linux child-subreaper
ownership before publishing its launcher. Cleanup retains the direct parent's
unreaped PID while observing and reaping only adopted terminal children of that
exact process group, session and UID. Each adopted child has a native WNOWAIT
terminal receipt before its exact PID is reaped. The unchanged document launcher
then reaps its direct parent and still requires native process-group absence.
It does not accept zombies as absent, ignore SIGCHLD, sweep unrelated children
or send further nonzero signals after releasing the parent's PID anchor. This
additional reaping stays within the existing two-second parent cleanup budget;
the worst combined do/controller/parent/finalizer bounds remain 42 seconds.

This reuses the existing document worker's owned launcher by copying that file
unchanged into the TeX image. The accepted document image and its source bytes
are unchanged. The TeX worker uses its own identity, internal ingress, concurrency
one, 1 CPU/1 GiB, scale-to-zero and a two-instance cap. It has no database/model,
Secret Manager or object storage grants. Initial service invoker IAM is empty.
Only `hirewiz-runtime-20261014` may receive a service-level invoker grant after
the existing isolation step; no public invocation is permitted.

## Scope and candidate behavior

The existing source parser accepts its finite supported package/class set and
path-safe `.tex`/ZIP source projects with included supported assets. This engine
is pdfLaTeX with the installed base/recommended TeX packages and Latin Modern.
XeLaTeX, LuaLaTeX, arbitrary fonts, arbitrary packages, shell commands and some
custom macros remain unsupported. An unsupported source must explain the reason
and offer the unchanged original PDF or a custom PDF/DOCX. It must never replace
the resume with a generic template while claiming preservation or universal ATS
compatibility.

The API compiles the original source and proposed edits with the same immutable
worker image. Existing validation checks pages, links, typography, protected text
geometry and pixels outside edited regions. The review version seals its exact
PDF and native source; download uses the sealed bytes. This is preservation of a
supported uploaded source project, not proof of identical rendering by every TeX
engine or ATS vendor. The selected original/custom resume remains available for
each application.

## Concrete rollout sequence

1. Review/build the six nonsecret image inputs from the private preparation
   packet. Upload its source archive once to the existing Cloud Build bucket;
   bind the returned object generation into the request before one native Build.
   Preserve the native Build ID and resolve its immutable registry digest.
2. Run one secretless Cloud Run Job against that digest, using sandboxLauncher,
   one task, zero retries and the same worker identity. Its trusted synthetic
   entry point is `/usr/bin/python3 -I /opt/tex/native_acceptance.py`. The eight
   cases are clean compile, absolute-file refusal, missing-package refusal,
   shell escape disabled, network/read-only-root/environment isolation, owned
   launcher timeout/deletion, compiler CPU refusal and clean compile after
   refusals. Match the actual installed source hashes to the preparation packet.
   Keep Job/Execution IDs, then delete only those owned test resources and verify
   native absence. Local boundary tests cannot replace this execution.
3. After all eight native cases pass, create `hirewiz-native-tex` with its existing
   secretless identity and the reviewed request from
   `infra/gcp/native_tex_worker.render(image, initial_release)`, using an actual
   immutable source commit for the initial service label. Verify the returned
   native origin; never guess the service URI. Commit the actual origin/image
   bindings as the final release, then update and verify the service's final
   release label. Verify native service/revision/IAM/account/key
   metadata. `check_snapshot` checks agreement; it deliberately does not claim
   effective IAM, sandbox execution or caller HTTP proof.
4. Wire only API and analysis revisions with `NATIVE_TEX_COMPILER_URL`,
   `NATIVE_TEX_IMAGE_DIGEST` and `NATIVE_TEX_ISOLATION_POLICY_SHA256` using the
   verified native service origin/image. They already have the shared Direct VPC
   caller route, Private Google Access and private `run.app` DNS. The employer
   worker must clear all TeX variables. The legacy `NATIVE_TEX_IMAGE` local Docker
   selector must stay removed in production.
5. Extend the existing fresh release's fixed service GET/getIamPolicy/setIamPolicy
   allowlist for this one worker; add the narrow invoker grant after creation of
   the future runtime identity. Verify the TeX worker and accepted pins before
   old authority retirement, and again before staging/promotion. Do not grant
   the worker database, model or storage access.
6. From the actual staged runtime identity/VPC, run `/ready` and a synthetic
   `/compile` call with its workload identity token. Then verify a full supported
   user source upload → proposed fit-preserving edit → review → sealed PDF/native
   download. Verify unsupported-font/package/overflow guidance and original/custom
   resume selection. These complete-flow checks remain required before marking
   the original resume checkpoint complete.

The transport binds exact request SHA, returned PDF SHA/size, immutable image and
policy. It uses a five-second identity HTTP request timeout and a fifty-second
compiler HTTP timeout, with no redirect, proxy or POST retry. These limits are
bounded request behavior; this document does not claim all provider SDK calls
share one absolute deadline.

## Verification status

The repaired native image
`sha256:5feb099b9d2eb3f8bbf29ce3bc99130930fa09e194dc5a7695436c347281e291`
from Build `6876e03a-332a-468e-84a4-664940c3bc32` passed all eight actual Cloud Run
cases in 18.194 seconds on 14 October 2026 UTC. The receipt binds the real
Job/Execution IDs, immutable digest and all five installed runtime source hashes
to the six frozen image inputs. Its timeout cleanup, resource refusal and final
clean compilation passed with the shipping subreaper implementation. This is
native compiler acceptance; it does not complete service HTTP or user acceptance.

Previous native attempts failed and remain preserved as negative evidence. The
earlier controller image
`sha256:02dfde6640a7a23c832cd0f52b92667241bb11b24cca9307a8401b75aa6601ec`
was built successfully by Build `93c2ff97-c55c-47ef-8b0d-c93da2e6c2c8`.
Its eight-case execution passed the clean and absolute-file cases, then failed
at unsupported-package handling. A narrow diagnostic on that identical image
subsequently verified unsupported-package refusal and a clean compile afterward;
this does not replace or erase the failed eight-case receipt.

The same-image timeout diagnostic established the concrete custody defect:
native sandbox deletion succeeded and the direct parent was reaped, but an
orphan zombie kept the process group present. A second diagnostic proved that
zombie had been adopted by the trusted outer Python PID1; its terminal status
was waitable there, while the request subprocess was PID10. The TeX-only repair
therefore initializes subreaper ownership in the actual request controller and
reaps its exact adopted children before releasing the direct-parent anchor.
All owned diagnostic Jobs/Executions and the last synthetic build-source
generation have been deleted with independent native absence checks.

The earlier local compiler/application checks and 114-case result remain local
evidence, not native acceptance. The repaired source passes 43 focused tests and
an actual non-PID1 Linux adopted-child probe for terminal codes 0/1 and timeout,
with final native group absence. Independent source review and a fresh single
native eight-case execution have now passed. The private TeX
service, runtime IAM/VPC HTTP checks and full candidate workflow are still
pending. The production API, accepted DOCX worker and customer data were not
changed by these synthetic TeX attempts.

Google references: [Cloud Run sandboxes](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes),
[sandbox code execution](https://docs.cloud.google.com/run/docs/code-execution),
[private networking](https://docs.cloud.google.com/run/docs/securing/private-networking),
[service identity authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service).

Linux references: [child-subreaper adoption](https://man7.org/linux/man-pages/man2/PR_SET_CHILD_SUBREAPER.2const.html)
and [native wait/waitid child custody and WNOWAIT](https://man7.org/linux/man-pages/man2/waitpid.2.html).
