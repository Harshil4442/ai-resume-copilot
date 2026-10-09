# Finite billing rollout preparation — 10 October 2026

Unlimited offers have been retired from the serving website and new legacy checkout
is closed. The three finite packs remain implemented source, pending the controlled
backend cutover; this report does not authorize activation.

| Pack | Price | Service credits | Analysis units |
| --- | ---: | ---: | ---: |
| Starter | ₹649 | 100 | 2 |
| Growth | ₹1,099 | 300 | 6 |
| Scale | ₹2,199 | 700 | 15 |

The independently reviewed estimate models margins of 41.57%, 42.44% and 46.24%
respectively at 100 monthly purchases with ₹15,000 monthly fixed expenses. These are
forecasts, not guaranteed profits or observed invoices. The
[expense review](EXPENSE_POLICY_REVIEW_2026-10-09.md) defines fees, reserves,
tax estimates, workload limits, expiry and the consequences of lower purchase volume.

## Verified source changes

- [x] Explicitly pin `RAZORPAY_CHECKOUT_ENABLED=false` in staging and revision
  verification. An inherited true or missing setting refuses verification. Accepted
  order callbacks and refund configuration remain preserved.
- [x] Add a fixed repository-root Docker build/push configuration for an exact native
  Git revision. The existing root Dockerfile is valid; the earlier suggestion that
  it was missing was withdrawn and its failed probe retained.
- [x] Pin the native build observation to `us-central1`, independent of ambient CLI
  region. Three original unset/global/other-region failures were reproduced before
  the one-line repair. Source, image, CI, financial and fencing checks remain intact.
- [x] Add the independently reviewed, metadata-only Google credential observer.
  It binds supplied credentials to native resources, completes paginated parent
  listings and compares a fresh metadata read. It cannot grant release permission.
- [x] Root combined verification: 145 affected checks pass, including the original
  unchanged checkout probes. Ruff and Mypy pass. No tests were skipped.

The regional packet independently passed 113 checks, and the standalone observer
independently passed 162 checks plus a nanosecond mismatch refusal probe. These are
separate bounded reviews, not additional unique tests of the whole application.

## Actual native observation

On 9 October at 20:45 UTC the exact reviewed observer completed in 5.293 seconds,
under a 35-second process deadline. The current Google credential bound to an active
native key; no deletion time was present. Exit 65 expresses that active status.
There were zero generation requests and zero credential mutations. Only sanitized
metadata was retained. The OAuth token authorizes metadata reads here; no claim is
made that its wider scope is read-only.

This verifies one supplied credential, not the complete historical inventory. Three
older OpenAI/Groq aliases still lack native administrative identities. Issuer/recreation
authority, replacement isolation, database retirement and shared-service continuity
remain unresolved. The [closure design](NATIVE_CREDENTIAL_FENCE_DESIGN_2026-10-10.md)
explains why a positive subset observation cannot close the global fence.

## Remaining deployment gates

The exact `ea2f801b` native Git build completed successfully at 20:55 UTC. Root and
independent native reads agree on its source, two Docker steps and immutable image;
[build evidence](evidence/2026-10-10-native-git-backend-build.json) records the digest.
Its [exact CI run](evidence/2026-10-10-exact-ci-registry-throttling.json) passed all
three browser/frontend lanes, but Docker Hub throttled the required security and
backend dependency pulls before their checks ran. Those checks are not waived.
The [registry acquisition repair](CI_PUBLIC_REGISTRY_ACQUISITION_2026-10-10.md)
preserves pinned image content and requires a fresh full-source CI result. The
earlier native build cannot be joined to a different source revision's CI result.

The next exact `0fb4784` native Git build also completed successfully at 21:22 UTC;
[its independent source/image evidence](evidence/2026-10-10-native-git-registry-repair-build.json)
records the distinct immutable digest. Its [full CI attempt](evidence/2026-10-10-precheckout-postgres-throttling.json)
verified security, frontend and companion lanes, including the actual cache reload,
193-commit secret scan and production container build. Both PostgreSQL-backed jobs
failed before checkout when Actions exhausted Docker Hub service-image pulls.
Backend/native compiler and full cold-browser checks did not run.
The [same-image PostgreSQL distribution repair](CI_POSTGRES_PUBLIC_DISTRIBUTION_2026-10-10.md)
requires its own exact new-head CI/image proof. Neither previous build is a deployment
or a substitute for CI on the new source.

The exact `c26ef1a` Git build completed successfully at 21:36 UTC;
[its native proof](evidence/2026-10-10-native-git-postgres-repair-build.json) binds the
separate source and image. Its [full CI attempt](evidence/2026-10-10-backend-registry-auth-timeout.json)
verified all five source checkouts and both healthy PostgreSQL services. Security,
frontend, companion and the connected no-AI browser/SQL journey passed. Backend
passed cache reload and PostgreSQL continuity, then stopped at a Docker Hub
authentication timeout during the immutable Debian pre-pull. Compiler/backend tests
never ran; historical cache absence is not established.
The [exact Debian/Redis distribution repair](CI_NATIVE_BASE_PUBLIC_DISTRIBUTION_2026-10-10.md)
preserves both digests and all compiler guards. Its changed Dockerfile hash requires
fresh compiler attestation and full new-head CI. Original failures remain recorded.
Root [actual local ARM64 proof](evidence/2026-10-10-native-registry-repair-arm64.json)
now verifies the new source hash and 41 scoped tests, including both actual compiler
cases, with zero skips. It cannot replace hosted AMD64/full-backend verification.

- [ ] Establish whether old credentials are shared with other applications.
- [ ] Complete native provider/issuer and database writer retirement, with new
  restricted identities and exact secret versions.
- [ ] Verify a fresh fenced backup and restoration, then migrate through schema 0013.
- [ ] Verify the exact published CI source and returned native image provenance.
- [ ] Stage and promote the joined API/workers with admission closed, then independently
  verify funded runtime configuration and intentionally activate useful workflows.
- [ ] Activate finite checkout after catalog/payment observation verification.
- [ ] Complete permitted, candidate-reviewed Razorpay browser assistance and the wider
  coverage, performance and recovery acceptance gates.

Native build submission, successful compilation and production deployment are distinct
checkpoints. Building an image cannot bypass the entrypoint's missing global fence or
enable checkout, generation, automatic submission or candidate lifecycle.
