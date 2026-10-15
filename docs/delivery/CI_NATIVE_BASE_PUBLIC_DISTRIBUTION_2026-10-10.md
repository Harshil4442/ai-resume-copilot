# Exact native compiler and Redis distribution repair

Status: minimal source repair based on `c26ef1a18ddfd80b2749951e4c1aa93712394516`.
Actual fresh native compiler acquisition/attestation and full CI remain required.

## Actual failure boundary

Run `37993930130` initialized both pinned PostgreSQL services successfully. The
backend validated its merged Docker daemon configuration, sent HUP, verified the
active mirror and passed exact PostgreSQL container ID/PID/running/health continuity
under `bash -e`. Its next immutable Debian pre-pull then failed at `21:31:46UTC`
while awaiting headers from Docker Hub's authentication endpoint: client timeout /
context deadline exceeded, exit 1. This is one observed authentication timeout,
not the previous HTTP429. Native compiler build/attestation and backend tests did
not run. The original log remains retained with its exact hash.

The log does not expose mirror requests or fallback reasons. It therefore does not
prove that the cache lacked this image at failure time. Current mirror metadata is
available and matches the pinned image, which cannot establish past availability.
[Google documents cache-first acquisition and its availability limits](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images).

## Supported exact-content change

The native Dockerfile `FROM` and matching CI amd64 pre-pull now name:

```
public.ecr.aws/docker/library/debian:bookworm-slim@sha256:7c7b2c966bc9ee8cedfeef67e0e279108992c77681fa595db4a9d65c06ccc587
```

The same backend lane later requires a pinned Redis image. To avoid exposing that
required acquisition to the same Hub authentication dependency, only its registry
prefix changes to:

```
public.ecr.aws/docker/library/redis:7.4-alpine@sha256:858f009f9709ce576febc734aa78b8f6d624b82571f9ddb6bda4377c833b3499
```

[Docker officially distributes these images on ECR Public](https://www.docker.com/blog/news-from-aws-reinvent-docker-official-images-on-amazon-ecr-public/).
[AWS supports anonymous pulls and immutable digest references](https://docs.aws.amazon.com/AmazonECR/latest/public/docker-pull-ecr-image.html).
No AWS account, credentials, login or infrastructure resources are introduced.
Production remains on GCP/Vercel.

Native public GET/HEAD observations beginning `2026-10-09T21:36:23.029857+00:00`
confirm both exact existing index digests on canonical Docker Hub and official ECR.
Both immutable HEAD responses return the same native digest; actual immutable GET
index bodies are byte identical. ECR GET digest headers are absent and explicitly
recorded as unavailable, not claimed equal. Linux amd64/arm64 platform manifests
and configs hash to the exact descriptor/size, are byte identical between registries,
and bind identical layer descriptors. The Redis config reports the same 7.4.11
software on both distributions. No base or Redis upgrade is introduced; compiler package commands remain unchanged.
Current Debian mirror index/platform/config metadata also matches canonical bytes.

Config metadata follows only the fixed, observed registry CDN host, one hop with
strict TLS and no forwarded registry authorization. Public anonymous protocol tokens
remain in memory; signed redirect URLs are not retained. No image pull/build/run,
Docker daemon operation, production call or CI rerun is performed by this task.

## Compiler contract and trade-offs

`native_tex_test_image.py`, processor and service remain byte identical. Source hashes
still include the checked-in Dockerfile, so the registry-prefix change requires a
fresh image build and source labels. CI still builds with `--pull=false`, exact
`linux/amd64`, an image ID output file and the same three-source secretless context.
It then verifies the exact SHA256 image ID, platform, every source label and actual
worker bytes. Nonroot, network-none, read-only, dropped capabilities and all runtime
limits remain enforced. An old compiler image cannot satisfy the new Dockerfile
source hash. No tag/RepoDigest retag claim, fallback image or old attestation reuse
is introduced.

The native Dockerfile otherwise retains every original command/package/worker byte.
Its existing apt repositories are not newly frozen by this distribution repair;
final compiler identity continues to require the actual build and verification.
The Redis port, command, pacing checks, cleanup and test requirements stay unchanged.
The working cache helper, PostgreSQL references, root production Dockerfile, all
required jobs/actions/head checkouts and tests remain intact. Offline checks compare
those bytes and exercise existing source/identity/platform/cleanup refusal contracts
using fake Docker transport; the two real native build/run tests remain required in
hosted CI and are not executed during this metadata-only task.

Explicit ECR references avoid Docker Hub authentication for these two required
acquisitions while preserving exact content. ECR still has anonymous quotas and
outages; no permanent availability guarantee is made. After independent review,
root must publish the composition and run all five preserved CI lanes on the new
head, observing actual compiler build/attestation, database/Redis/native tests and
security results. Original failures remain evidence; no blind rerun, retry mask,
skip, daemon restart or production monetary activation is authorized by this packet.
