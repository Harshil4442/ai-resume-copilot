# Native TeX document worker

The application keeps uploaded `.tex` or `.zip` bytes, patches supported literal
source spans, compiles the original and edited projects, validates their PDFs,
and seals the native project and PDF hashes in a draft version. Approval does
not regenerate a file. Preview, PDF download and application attachment use the
same sealed PDF; native source download returns the patched project. A version with no edits is labeled
`unchanged_source_snapshot`; it is not presented as successful tailoring. Original and custom choices remain
independent for every application.

Only UTF-8 projects with one root entry file and up to 64 regular source/assets
files, totaling 5 MB, are supported. Includes must be root-relative and present.
The package allowlist is in `native_tex.py`; the compiler never installs packages
while handling a document. Native fonts supported by this image include Computer
Modern and Latin Modern. `.sty`/`.cls` project files are retained. Generated TeX
files, encrypted/linked/unsafe ZIP entries and unsupported dependencies are
refused with an editable-source/original-PDF alternative. Supported editable
spans are plain paragraphs, `\\item` text and `\\resumeItem`/`\\resumeBullet`
arguments. Nested macros, encoded text and nonunique PDF spans require manual
source editing or the original/custom PDF. PDFs with unreadable CID/unknown font
mappings are refused; for example, some legacy Computer Modern symbol bullets
need a Unicode-mapped font/source or a readable original/custom PDF. Typography and protected glyph
geometry are checked conservatively; edits that reflow protected content fail.

The v2 admission policy refuses dynamically constructed commands such as
`\\csname`, TeX character-code constructions and computed date/time commands.
`\\today`, date/time primitives and implicit `\\maketitle` dates cannot retain
an unknown original rendered date under reproducible compilation. Use the
intended literal date in the source, or upload the original/custom PDF. This
refusal never starts a compiler and never changes or discards uploaded bytes.
The fixed compiler epoch is limited to documents admitted by that policy; it
does not authorize silently replacing factual dates with January 1, 2000.

Fact admission keeps word order and clause punctuation. Narrow creation-verb
synonyms are allowed; a larger rewrite must match a complete cited approved
statement. Qualifiers and negations retain their ordered claim scope; uncertain
recombinations require manual source editing. Candidate review remains required.
PDF admission traverses reachable direct and indirect objects with bounded
depth/work. It refuses active actions, attachments and action chains while
retaining passive URI links, internal opening destinations and outline sibling
links. Malformed compiler responses/PDFs produce a guided refusal. Seals created
under the earlier v1 validator require fresh safe preparation and review.

## Local isolated evidence

For required local/CI compiler tests, run the checked-in builder with the local
architecture (CI explicitly uses `linux/amd64`):

```sh
backend/.venv/bin/python backend/scripts/native_tex_test_image.py --platform linux/arm64 --output /tmp/native-tex-test-image.json
export HIREWIZ_NATIVE_TEX_TEST_IMAGE=sha256:<image_id_from_report>
cd backend
.venv/bin/pytest -q tests/test_native_tex.py tests/test_native_tex_r2.py tests/test_native_tex_application.py tests/test_native_tex_ci.py
```

The builder copies only Dockerfile/processor/service into a fresh build context,
records the immutable ID/platform and source hashes, and checks the actual worker
bytes inside a network-disabled read-only container. Test fixtures recheck the
ID and source binding before real compilation. Missing Docker, missing/malformed
IDs, mutable tags and changed sources fail the required tests; they never skip or
pull a fallback image. `NATIVE_TEX_TEST_IMAGE` is not a supported fallback. CI
exports the verified ID only after building and checking it. The Docker base is
pinned, but package resolution during a fresh build can change; each run records
its actual image ID. This is local/CI evidence, not GCP production isolation.

For an explicitly enabled local application driver, inspect its image and set
`APP_ENV=test` and `NATIVE_TEX_IMAGE=sha256:<image-id>`. Mutable tags are refused.
The driver uses network-none, readonly root, no host mounts, nonroot 65534,
no capabilities/new privileges, 1 CPU, 256 MB memory/no swap, 32 PIDs, 64 MB disposable
work and 16 MB temporary filesystems, CPU/file limits and a 30-second deadline.
The daemon-side container is forcibly removed after completion or timeout. TeX
uses `-no-shell-escape`, restricted input/output paths and a clean process
environment. The processor additionally limits each process to 256 MB address
space. The processor bounds output/log size, runs at most two passes,
rejects overflow/missing glyphs, and returns only a PDF/hash contract. Untrusted
TeX is never executed by a host compiler or shell.

## GCP production integration and open gates

Deploy this immutable processor image as a dedicated private Cloud Run document
worker with argument `serve`, separate from API and model workers. The API uses
its private `/compile` endpoint with a workload identity token. Runtime
configuration requires `NATIVE_TEX_COMPILER_URL`, an Artifact Registry
`NATIVE_TEX_IMAGE_DIGEST`, and `NATIVE_TEX_ISOLATION_POLICY_SHA256` identifying a
separately reviewed deployment/isolation record. The worker receives the same
image/policy identifiers and checks the policy before processing. These
identifiers bind configuration; they do not themselves prove isolation.
The wrapper kills the processor's owned process group before reusing an instance,
including timeout paths. The local image is built for the local architecture;
build a separate `linux/amd64` image for Cloud Run and review its exact digest
rather than deploying an ARM64 local evidence image.

Before enabling that transport, independently prove internal ingress and narrow
invoker grant; no credentials/secret mounts or data-store roles in the worker;
egress denial including Internet and metadata; restricted disposable filesystem
and file access; nonroot execution; bounded CPU/memory/PIDs/request time; one
request per instance; malicious file/command and timeout cleanup drills; exact
image/protocol attestation; and current file quarantine/scanning controls. Cloud
Run Docker-in-Docker is not an implementation assumption. Managed Cloud Run
sandbox and egress configuration need separate deployed evidence; the local
Docker result does not close this production gate. If the selected Cloud Run
isolation cannot meet these controls, use a dedicated GCP isolated job runner
that can, retaining the same protocol and private transport contract.

No deployment/IAM changes, live compiler request, model call, employer
submission, production source-use permission or universal ATS/format claim is
part of this development evidence. The native artifact is currently stored in
existing owner-scoped version JSON; private immutable GCS application artifacts
remain the disclosure store. Large-version storage/retention and broader
representative font/macro/layout support still need rollout review.
