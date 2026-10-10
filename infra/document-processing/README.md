# Private PDF/DOCX inspection worker

Reviewed 10 October 2026. This draft describes the integrated V3 worker and the current
`backend/app/services/document_ingestion.py` contract. It is a proposed deployment
and acceptance procedure. No cloud resource, IAM permission or production upload
capability is created by this document.

## Scope and source

New PDF/DOCX uploads are scanned and deterministically parsed before the API saves
an original or starts optional AI enrichment. The API retains the uploaded bytes
unchanged after successful admission. The worker receives a generic format and
original bytes; the client filename is not sent. No model, merchant, database or
employer operation belongs in this worker.

The worker now refuses inspection admission after the absolute request-read deadline, including a delayed timer. The V2 suite passes 78 tests; scanner/launcher calls in that suite are synthetic. Genuine local engine checks and native Cloud Run acceptance are recorded separately.

| Source | V2 SHA-256 |
| --- | --- |
| `infra/document-processing/processor.py` | `d20a5bb0b211cf13e3e4aed326d12260cbfd02ab454f070cb89495ebcdff37f0` |
| `infra/document-processing/service.py` | `931d6afe7799c072d2ca9976a71f72dce95df75fb42e18b9d09c1c17547fad1d` |
| `infra/document-processing/Dockerfile` | `eed6740c58d41a505f6c20d4d67f3fb87d189429173d1fdadcf8b4f2a78d5101` |
| `backend/tests/test_document_processor.py` | `6d82e37f4c93820675bd951d30e3b165beddfcce39176e4733f7e1c1bb07503b` |

## V3 outcome checkpoint

The table above retains the original V2 hashes. Current processor/service hashes are
`fdf4a00fdf92b09f0dd22bf3a8b17d3987cc505bd5b55d313dbfa44f537272a8`
and `fd65733bfb959cfa6af9283d92170a22debe4765a662be5430a2486a6b2b43c2`.
At that checkpoint the Dockerfile was unchanged. V3 passes 179 existing and 11 independent checks; root's
276 affected checks and exact CI Ruff/Mypy129 pass. The original absolute read-deadline
guards and regression bodies remain unchanged. Only test import whitespace was amended
for the actual CI working directory, with identical executable AST.

Permanent refusal requires a coherent actual scanner detection or explicit document
structure/limit refusal. Processor exit1 carries version, original hash/length/format
and fixed `document_inspection_refused`; the adapter adds image/policy and returns422.
The client accepts only the exact duplicate-free seven-field bound envelope, at most
4096 bytes, within the existing absolute HTTP deadline. Unknown, unbound or ambiguous
results remain unavailable. Processor exit2 is `document_inspection_unavailable`; stale,
missing or corrupt definitions, parser/infrastructure failures and failed sandbox cleanup
never become a candidate-file refusal. Cleanup failure overrides every otherwise valid
outcome. Generic adapter503 retains its historical fixed body for unchanged deadline
tests; status503 is always unavailable.

A fresh actual local ARM64 image `sha256:afa53be35f7f256a1440434944dcd8a9009380546261df2287643458e1c0d09b`
passes all eight V3 native corpus cases, with six runtime file bytes and restricted
container metadata checked. Both direct/archived EICAR require genuine ClamAV detection
plus bound processor refusal; missing definitions require exit2. This is local image
evidence, not AMD64 or Cloud Run isolation proof.

The V3 policy digest was `45c020e5134eeae2159cef5e1fa423249b8f76a4214e6dd7ed3b48b7bb332b33`.
The updated current policy is recorded below.

## Validated receipts and fresh definition acquisition

`inspect_resume_document_with_receipt` makes one authenticated inspection request and
returns the exact original byte hash, size, format, image/policy pins and validated
scanner metadata with the parsed result. The legacy tuple API delegates to the same
validator. Success requires exact duplicate-free top-level, scanner and parsed key sets.
The frozen receipt hides parsed content from its representation; callers must use the
inspection API because constructing a dataclass is not evidence of inspection. Existing
parsed lists/dictionaries remain mutable and must not be treated as signed content.
The two-path source repair passes 102 authored and eight independent checks. Original
TLS, identity, request deadlines, refusal handling and prior tests are preserved.

The Dockerfile now acquires official definitions in a separate build stage. Each new
build receives a builder-owned 32-character lowercase hexadecimal nonce, retaining
stable dependency caches while forcing acquisition to run again. The final runtime
contains no nonce argument/environment setting. Scanner database headers still enforce
freshness; a nonce alone proves neither successful acquisition nor fresh definitions.
The three-path repair passes 16 authored and four independent checks.

A fresh actual ARM64 image `sha256:a1a57802080a84bd584a2f119626a39e20a4420c6b7e4e8638b05b91ab3874c9`
passes all eight real scanner cases with matching source/runtime bytes and restricted
container metadata. The preceding published `ab5cb5d9` passes all five hosted CI jobs,
3,149 backend tests, genuine AMD64 scanner cases and the connected five-width journey;
those published proofs do not certify the later receipt/definition changes. Current
exact-source hosted CI and native Cloud Run isolation remain required.

[`policy.json`](policy.json) is canonical sorted compact UTF-8 JSON with a terminal
newline. Version 2 has SHA-256 `425fd4b326554d8fee8d2686f423c8e2b499eaaa1bd7858eb69f7759ac526bdf`.
It binds the seven build sources, supported PDF/DOCX limits, outcomes and required native
isolation controls. Before activation, independently match actual source, image bytes,
revision configuration and acceptance evidence to this artifact. Its requirements and
`deployment_proven:false` field do not certify the cloud. This policy excludes TeX/ZIP,
retained uninspected files, later tailoring/rendering and application artifacts; their
broader admission requirements remain open. Daily fresh-definition build/promotion
operational work is also still open.

## Build and image custody

Use a fresh allowlisted build directory with exactly these shipping source files,
retaining the paths expected by the Dockerfile:

- `infra/document-processing/Dockerfile`
- `infra/document-processing/processor.py`
- `infra/document-processing/service.py`
- `backend/app/services/parsing.py`
- `backend/app/services/market/skill_extractor.py`
- `backend/app/services/market/skill_taxonomy.py`
- `backend/app/services/market/security.py`

Copy each file from the accepted release and record its SHA-256. Do not send the
entire repository, environment files, credentials, resumes, test artifacts or a
working directory with unrelated files to a builder. Test source is retained as
review evidence outside the shipping context.

The Dockerfile pins the base manifest and Python package versions, copies only
deterministic helpers, obtains official definitions during the build, and runs
as UID/GID `65534:65534`. Its apt package and definition acquisition still need
actual resolved-version evidence. Capture the immutable output digest, target
platform, copied-file hashes, resolved scanner/package versions, definition
version/date and a bounded dependency/vulnerability report. A tag, declared base
digest or successful build alone does not bind a native tested image.

Use an actual Linux AMD64 build for Cloud Run acceptance. Run real clean PDF and
DOCX, EICAR, stale/unavailable/corrupt definitions, encrypted files, scan-limit
and expansion fixtures against the exact immutable image and processor resource
caps. A mocked scanner or a test outside those caps cannot substitute. Keep
candidate data out of this corpus. Confirm the image contains no app configuration,
model clients, database/merchant code, credential files or source-secret material.

## Private service and isolation configuration

Enable the official sandbox launcher on a dedicated Cloud Run inspection service.
Google documents `--sandbox-launcher` / `sandboxLauncher: true`; enabling it uses
the second-generation environment. The feature is Preview, and sandbox resources
share the host container's CPU/memory allocation. Record the exact revision's
native configuration, rather than inferring enablement from an environment value.
[Google sandbox configuration](https://docs.cloud.google.com/run/docs/configuring/services/sandboxes)

The controller requires `/usr/local/gcp/bin/sandbox` and calls its named
`run`, `exec` and forced `delete` lifecycle. It imports only one internally
constructed regular JSON file into a writable ephemeral overlay. It requests no
outbound networking, environment passthrough, bind mount, export or persisted
snapshot. Google documents default network, environment and metadata isolation;
these are properties to verify in the actual deployment with harmless probes,
not conclusions established by the worker's image/policy echoes. Sandboxes can
read the host root filesystem, so the host must contain no candidate-independent
secrets or secret mounts. The documented sandbox nonroot user has sudo privileges;
do not claim the Docker UID alone proves privilege or process isolation.
[Google sandbox execution model](https://docs.cloud.google.com/run/docs/code-execution)

Use a dedicated worker service identity with no database, Secret Manager, model,
merchant, application-submission, artifact bucket or broad project permissions.
No environment secret, service-account key, user credential or secret/file volume
is needed. Review inherited project/folder IAM as well as direct bindings. The
runtime image needs only its nonsecret image/policy pins and Cloud Run's port.
Keep build/publish authority separate from the runtime identity.

Require Cloud Run IAM authentication; do not grant `allUsers` or
`allAuthenticatedUsers`, and do not disable the invoker check. Permit only the
intended API workload identity to invoke this particular worker service. The
client obtains a short-lived Google-signed ID token and sends it in the
Authorization header. Do not persist, print or pass that token into the sandbox.
[Google service-to-service authentication](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)

Prefer internal ingress after the API-to-worker path is proved. Cloud Run-to-Cloud
Run calls require appropriate VPC routing to be recognized as internal, even in
the same project; selecting internal ingress alone is insufficient. Validate the
Direct VPC egress/connector and Private Google Access or equivalent chosen path.
The current client accepts only HTTPS `run.app` endpoints, so a custom internal
load-balancer hostname is not compatible without a separately reviewed transport
change. Keep IAM private while this routing gate remains unresolved.
[Google private networking](https://docs.cloud.google.com/run/docs/securing/private-networking)

## Resource and admission limits

These initial service settings are proposals to measure, not accepted capacity:

| Control | Proposed setting / current enforced bound |
| --- | --- |
| Service CPU / memory | Start evaluation at 1 CPU / 4 GiB; accept only measured viability and cost |
| Service concurrency | 1, matching the single-request controller |
| Scale | Minimum 0; small reviewed maximum, initially 2 instances; no unbounded autoscaling |
| Cloud Run request timeout | Initially 90 seconds; verify handler/phase budgets fit the 75-second client window |
| API network deadline | 75 seconds total for the inspection HTTP transaction; identity requests have separate 5-second request timeouts |
| Worker header/body deadline | 10 seconds total from handler setup; deadline is cancelled before scanning |
| Launcher phases | Create 10 seconds; inspection 45 seconds; explicit delete 5 seconds |
| Processor limits | CPU 40 seconds; address space 2 GiB; regular file size 32 MiB; 64 open descriptors; core dumps disabled |
| Original / request / response | Original ≤5 MiB; JSON request ≤7 MiB; response ≤1 MiB |
| PDF structure | 1–20 pages, no encryption/active actions/embedded content; bounded reachable-object walk |
| DOCX structure | ≤500 entries, ≤20 MiB total and ≤10 MiB per expanded entry; no unsafe paths, links, duplicates, encrypted entries, active embeds or external templates |
| Word fields | Only PAGE/NUMPAGES with supported presentation switches; external/dynamic fields refused |
| Extracted output | Raw UTF-8 ≤256 KiB; total section values ≤256 KiB; ≤30 section keys of ≤80 bytes; ≤500 skills of ≤200 bytes; experience 0–40 years; contact values ≤1000 bytes |

ClamAV recommends roughly 3–4 GiB RAM for its standard signature database. The
processor's 2 GiB address-space cap is therefore a real measurement gate, not an
assumed sufficient budget. Test under the exact cap and fail closed if the engine
cannot load; any cap/resource change needs updated source, policy and tests.
[ClamAV system requirements](https://docs.clamav.net/Introduction.html)

The genuine local ARM64 checker passed all eight cases on 10 October: clean PDF/DOCX, a passive page counter, active PDF/Word field refusal, exact EICAR test-file detection directly and inside a ZIP member, and unavailable-definition refusal. It verified the six actual runtime files and container constraints against immutable image `sha256:e4b1caa30b40f794be1c6ce6fcbbc2486732d1b6ded1181498b1928e9ed4a531`, with ClamAV 1.4.3 and official definitions 28149 issued 2026-10-10T06:24:04Z. Clean processing succeeded under the 2 GiB processor address-space cap. This establishes viability for these ARM64 fixtures only; it does not replace the AMD64, native sandbox, broader hostile-file or capacity gates. Run `backend/scripts/document_test_image.py --platform linux/amd64 --output <new-proof-file>` for the repeatable native check.

The integrated V2 guard prevents post-expiry inspection admission. Its two scheduling regressions and the independently paused HTTP probe require zero scanner calls.
The deployment must also prove process limits/cleanup under the native launcher;
RLIMIT_AS and Docker tests do not prove a native Cloud Run cgroup/PID policy.

The website BFF gives only exact POST `/resume/parse` a 120-second upstream deadline; other requests retain 65 seconds. Its Vercel handler declares `maxDuration = 150`. Verify the actual deployment duration/Fluid configuration before promotion. This headroom does not prove an absolute identity-token acquisition deadline or cover optional AI enrichment after parsing; generation remains paused. The current multipart path also meets Vercel's 4.5 MB function body ceiling before the API's advertised 5 MiB file limit. Direct signed GCS quarantine upload is required for the full file allowance; the timeout fix does not remove this gap. [Vercel duration](https://vercel.com/docs/functions/configuring-functions/duration), [payload limits](https://vercel.com/docs/functions/limitations#request-body-size).

## Definitions and scanner acceptance

The worker loads `/var/lib/clamav`, scans the exact original before document
parsing, and refuses nonzero status, stderr, incomplete/skipped/error summaries,
wrong target/count or changed source bytes. It requires a clean one-file result
and a database date no more than three days old, taken from engine version
metadata. Scan/encryption/expansion alerts are enabled. ClamAV documents that
ordinary oversize limits may otherwise skip a file as clean; retain the
exceeds-limit alerts and strict summary checks. Scanner limitations mean a clean
result is one safety control, not proof that every possible threat is absent.
[ClamAV scanning controls](https://docs.clamav.net/manual/Usage/Scanning.html),
[official clamscan reference](https://raw.githubusercontent.com/Cisco-Talos/clamav/main/docs/man/clamscan.1.in)

FreshClam obtains official definitions; it must use the same database directory
that the scanner reads. Proposed operations rebuild a fresh immutable image daily
and alert well before the three-day cutoff. Explicitly invalidate the definition
acquisition cache: a rebuild using a cached FreshClam layer, retagging an old image
or changing a timestamp does not refresh signatures. Verify engine/database
metadata in each new image, repeat real positive/negative acceptance, then promote
that digest and update both worker/client pins together. Runtime has no updater or
network fallback while handling candidate data. Failed refreshes leave admission
unavailable; never relax the age check to restore availability.
[ClamAV definition management](https://docs.clamav.net/manual/Usage/SignatureManagement.html)

## Client configuration and result binding

Configure exactly these three nonsecret values on the API's accepted revision:

| Variable | Required value |
| --- | --- |
| `DOCUMENT_WORKER_URL` | Canonical receiving service URL plus `/inspect`, e.g. `https://<canonical-service>.run.app/inspect`; no userinfo, port, query, fragment or redirect |
| `DOCUMENT_WORKER_IMAGE_DIGEST` | Accepted worker Artifact Registry image with immutable `@sha256:<64 lowercase hex>` digest |
| `DOCUMENT_WORKER_POLICY_SHA256` | SHA-256 of a reviewed, retained canonical policy artifact defining supported formats, limits, scanner freshness and required isolation controls |

The worker has the same image and policy environment pins. The current client
derives token audience from the URL's HTTPS origin; use the canonical service URL,
not a traffic-tag hostname. Google expects the receiving service audience unless
a separately configured custom audience is used. The current code does not supply
a distinct audience variable. Record the policy artifact and its meaning before
activation; a random 64-character value is not a reviewed policy.
[Google audience requirements](https://docs.cloud.google.com/run/docs/authenticating/service-to-service)

A response must bind version, original SHA-256, original byte length, format, image,
policy and scanner metadata and pass bounded parsed-value validation. The client
rejects redirects, compressed responses and oversized/unbound results, uses an
explicit CA-validated TLS context, and closes its async stream on the absolute
deadline. There is no local parser, model or unsafe transport fallback. Image and
policy echoes bind configuration only; validate actual revision/image identity
through native deployment evidence.

## Privacy, cleanup and errors

The controller creates a private request tar and imports one JSON file. The
sandbox also has the documented read-only view of the host filesystem; keep that
view free of secrets and unrelated candidate artifacts. Candidate contents are transient in the
controller and sandbox. Use no-store responses. Do not log original text, files,
parser/scanner stdout or stderr, headers, tokens, client filenames or response
bodies. Record only bounded operational counts, latency, refusal categories and
scanner age/version under the application's retention policy. Verify Cloud
Logging/access-log configuration separately; application logging suppression is
not a platform logging audit.

Host temporary-directory cleanup runs on exit; a named sandbox is force-deleted
before a result can return. Failed create, execution or deletion refuses release.
Native timeout, disconnect, worker crash, restart and forced deletion drills must
prove no cross-request source/file retention and owned process cleanup. A cleanup
exception alone does not prove that residual native resources are gone.

The API rejects invalid format/size locally, and all worker/network/binding errors
occur before resume persistence or enrichment charging. The worker currently returns
fixed 503 for permanent refusals and temporary unavailability alike. The client has
a 422 refusal branch, but the current worker does not use it remotely. This is a documented UX
limitation: repeated retries cannot repair an encrypted or active-content file.
Do not expose scanner findings or underlying provider exceptions to candidates.

## Deployment order and remaining acceptance

1. Freeze the accepted source and policy with their exact hashes.
2. Build from the minimal context, bind the immutable platform image, and complete
   real scanner/malicious-file tests under exact limits.
3. Review the isolated worker proposal: dedicated identity, IAM, ingress route,
   launcher, resource caps, secret/mount absence, bounded scaling and cost budget.
4. Deploy only that reviewed worker candidate through the normal authorized release
   path. Prove native IAM denial, intended workload invocation, actual image/launcher
   binding, no sandbox internet/metadata/parent secret access and cleanup behavior
   using harmless synthetic data. Keep candidate traffic closed until this passes.
5. Set the three API pins on an accepted API candidate, then verify real PDF/DOCX
   upload → scan/parser worker → exact original persistence → owner-scoped source
   download. Verify refusals leave no resume, analysis hold or debit. Measure cold
   and warm latency, saturation, cancellation and stale-definition failure. Check
   that frontend/BFF request limits accommodate worker plus identity-token time;
   the client's 75-second HTTP bound is not the entire browser upload deadline.
6. Promote the accepted immutable API/worker revisions together under the existing
   deployment gates, monitor privacy-safe errors and scanner freshness, and retain
   a previously accepted, still-fresh worker rollback. If no safe rollback exists,
   leave admission unavailable rather than restore host parsing.

This does not authorize a monetary cutover, change billing, resume the paused
analysis queue or integrate the separately held protected browser paths.

- [ ] Actual immutable image and genuine scanner acceptance under exact limits.
- [ ] Native private IAM, launcher, egress/metadata/secret isolation and cleanup proof.
- [ ] Durable private GCS quarantine/release, owner/hash/generation identity,
      deletion outbox and abandoned-object cleanup required by SD03.
- [ ] Admission and custody for legacy retained files and application artifacts.
- [ ] Native TeX/ZIP scan/compiler path and later PDF/DOCX rendering isolation.
- [ ] Broader visual/extraction fidelity, supported-format guidance and usability.
- [ ] Field latency, throughput, scanner freshness, bounded cost and resilience.

A private worker with successful synthetic fixtures does not close these full
SD03/SE02–04 requirements or establish all-format resume safety.

## Native registry candidate

The later minimal seven-file source archive was built by native Cloud Build
`417bc603-00e7-4237-b5cf-1ac60a870cb3`; native source generation and SHA256/MD5
match the owned archive exactly. Registry image
`hirewiz-document-inspector@sha256:edc8f5fc3000a40237ed3888c563c5f5ef175008e5ef3c4fb5007976119934e1`
passes the actual eight-case engine corpus, all six runtime-file byte checks and
restricted container checks through local Linux AMD64 execution on the ARM host.
The original system-Python dependency refusal is preserved and only its interpreter
was corrected for replay; no source or image was rebuilt. The registry candidate
requires a published-source join and native Cloud Run sandbox/IAM/network/cleanup
acceptance before API activation. No candidate resume was processed in this build.
See [sanitized registry evidence](../../docs/delivery/evidence/2026-10-10-document-registry-image.json).
