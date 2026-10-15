# Exact CI image acquisition repair

Status: narrow source repair based on `ea2f801b5472b43f70d9201e9d2f2fb9557538e0`.
No CI rerun, image build/run, account credential access, provider call or cloud mutation
is performed by this authoring task. The original failure logs are retained privately.

CI run `37989653488` stopped before backend tests and secret scanning because Docker
Hub returned HTTP429 for the pinned native-compiler Debian base and pinned Gitleaks
image. Those infrastructure failures are not passing application or security results.

## Same pinned content, supported acquisition

The native public registry observation at `2026-10-09T20:55:37.107605+00:00` returned
HTTP200 for GET and HEAD on the Google cache's existing Debian index. Header digest
and SHA256 of actual response bytes both matched:

`sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587`.

The existing native Dockerfile stays byte-for-byte unchanged. Google's supported
acquisition path is Docker daemon `registry-mirrors`, which checks its public cache
before Docker Hub; cached pulls do not count against Docker Hub limits. Availability
is not guaranteed indefinitely. The workflow uses that configuration rather than
rewriting `FROM` to a cache hostname. [Google cache contract](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images).

Docker documents `registry-mirrors` as reloadable through SIGHUP without restarting
the daemon. CI preserves other daemon options, validates the merged JSON, sends HUP
to Docker's observed service PID, and verifies the activated mirror with bounded
`docker info` observations. Both backend and security/container jobs use the same
helper and reload/verification sequence before their Docker image operations. The
security job's production container build also uses the cache for its unchanged
Python base reference; replacing Gitleaks's registry alone would leave that later
Docker Hub pull exposed to the same quota. The already-running PostgreSQL service must be healthy
beforehand and retain its exact container ID, process PID, running state and health
after reload. No restart fallback or error mask is added.
[Docker reload contract](https://docs.docker.com/reference/cli/dockerd/#configuration-reload-behavior).

CI explicitly pre-pulls the identical pinned Debian base for `linux/amd64` through
that Docker daemon before the existing `--pull=false` native compiler build. Native
source hashes, immutable image verification, nonroot worker, platform contract and
required tests remain enforced. Actual next-run acquisition/build/test proof is
still necessary; metadata availability alone does not prove a successful compiler build.

The official Gitleaks upstream documents GHCR as a supported registry. Actual GHCR
`v8.24.2` GET/HEAD and canonical Docker Hub GET/HEAD returned the same existing index
digest and identical manifest bytes:

`sha256:b5918eb91b8d2473cec722f066abb4352e4ffdc4ec9f4283ec143aba9ec9ebc4`.

The only scanner change is the official registry/repository prefix to
`ghcr.io/gitleaks/gitleaks`. Version, immutable digest, full repository history,
`detect --source /repo --no-banner --redact`, configuration and required exit status
remain unchanged. [Official Gitleaks acquisition](https://github.com/gitleaks/gitleaks#installing).

Further actual platform manifest/config GETs at `20:58:15.427453+00:00` verify Debian
cache/canonical and Gitleaks GHCR/canonical identity for Linux amd64 and arm64. Every
manifest and config body hashes to its descriptor. Gitleaks configs bind release
`v8.24.2`, upstream revision `d5c275d6b2140fcb59bc3f58b8dd5350f5b19df6`, official source,
and entrypoint `gitleaks`. Matching platform/config/layer digests preserve the exact
content-addressed scanner binary and filesystem; no new scanner release or binary
is introduced. No local image execution or fresh repository scan is claimed here.

## Verification and remaining scope

- Owned file/fake-daemon tests cover preserved options, atomic configuration, malformed
  or duplicate JSON refusal, symlink refusal, bounded native activation and daemon errors.
- The helper is limited to ephemeral Linux GitHub Actions; no local daemon was modified.
- CI automatically collects the tests, checks the helper/test with Ruff and the helper with Mypy.
- The original native compiler verification and required backend/security jobs remain.
- Root must publish the reviewed composition and run full exact-head CI once. Native
  compiler completion and the real secret scan must pass; no skip/retry mask substitutes.

This repair addresses the two observed acquisition failures. Cache availability,
unrelated service-image pulls before job steps, upstream registry outages and other
future CI failures remain real operational limits. It does not certify monetary
activation, provider retirement or the full development/deployment goal.
