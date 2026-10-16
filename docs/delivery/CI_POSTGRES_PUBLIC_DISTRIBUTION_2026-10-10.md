# PostgreSQL service acquisition before checkout

Status: narrow CI distribution repair based on
`0fb478446ad1265c32946eef84211924bf3c8c76`; actual hosted acquisition and tests remain pending.

CI run `37992358635`, attempt 1, failed both backend and cold-browser jobs during
Actions **Initialize containers**. Their three Docker Hub pulls each returned an
unauthenticated rate-limit error. Neither job reached repository checkout, cache
configuration, native compiler verification or tests. Original private logs and
hashes are retained; these failures are not passing test results. The security job's
cache reload, full-history Gitleaks scan and production container build succeeded
in that run, as separately observed by root.

The two PostgreSQL service references now use Docker's official public distribution:

```
public.ecr.aws/docker/library/postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24
```

[Docker documents this official ECR distribution](https://www.docker.com/blog/news-from-aws-reinvent-docker-official-images-on-amazon-ecr-public/).
[AWS documents anonymous public pulls and digest references](https://docs.aws.amazon.com/AmazonECR/latest/public/docker-pull-ecr-image.html).
This uses public image distribution only. It creates no AWS resources, requires no
AWS account credentials or login, and leaves production on GCP/Vercel.

Native public metadata reads at `2026-10-09T21:24:36.956093+00:00` verified:

- Canonical Docker Hub and official ECR current `17-alpine` index bodies are byte
  identical, with the SHA256 shown above. Immutable digest GETs return those same
  bytes; both registries' HEAD responses by digest return the same native digest.
- ECR's GET responses omit `Docker-Content-Digest`; this absence is recorded, not
  reported as header equality. Actual response-body hashes and the immutable HEAD
  digest establish index identity.
- Linux amd64 and arm64 platform manifests/configs are byte identical between
  registries. Each body hashes to its exact descriptor, and every layer descriptor
  matches. Both configs bind PostgreSQL major 17, version 17.11, the same entrypoint,
  command and environment. No different PostgreSQL software is substituted.
- Small config reads follow only the explicitly observed registry CDN hosts, with
  strict TLS, no ambient proxy, one redirect and no forwarded registry token. Public
  anonymous protocol tokens stay in memory. No account credentials are accessed.

The old tag was mutable. Current canonical equality does not prove historical tag
content from a previously successful run. Pinning this observed exact current image
prevents future tag drift. No image pull, build or run was performed by this task.

Only the two service image references change. PostgreSQL environment variables,
ports, health checks, required jobs/actions, exact-head checkouts, all test commands,
cache helpers and compiler/security gates retain their original bytes. Offline
checks compare the whole workflow to the baseline after only these two substitutions.
The unchanged cache contract tests also remain required.

After independent review, root must publish the reviewed composition and run full
CI on its exact new head. Both services must initialize healthy and all existing
native, database, backend and cold-browser checks must actually pass. No retry/skip
mask or substitute test result is introduced. Public ECR has anonymous quotas and
may have outages; this repair addresses the observed Docker Hub initialization
failure without promising unlimited availability or completing monetary deployment.
