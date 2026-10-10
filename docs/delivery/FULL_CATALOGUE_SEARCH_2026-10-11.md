# Full catalogue search — inactive integration, 11 October 2026

The reviewed source removes the arbitrary 1,000-candidate prefix from the new optional
search path. All eligible indexed postings at the accepted watermark participate in
canonical ranking before unseen-first result selection. This expands traversal of our
own catalogue; it does not establish worldwide discovery coverage or 95% recall.

Preparation runs in persisted bounded chunks through the existing outbox. The candidate
can poll status or cancel without a charge. A preparation holds a zero reservation;
Search, Delivery and financial writes occur only after complete scan and proof checks.
Current source/posting, resume/history revisions, freshness and the accepted quote are
checked through settlement. Saved results retain exact item bodies and their digests;
free paging uses item rows. The original accepted terms remain unchanged.

The full-catalogue flag remains false by default. Production schema, checkout, optional
AI and automatic submission have not been enabled by this integration. Existing release
and protected integration refusals remain in force.

## Corrections and verification

The first reviewed integration was not cleared. Actual `autoflush=False` revealed that
two aliases in one chunk could insert the same canonical winner twice. A chunk-local
map now reuses pending winners. Original failure evidence is retained. Configured Ruff
and Mypy identified one strict-zip warning and 16 typing errors; the narrow correction
passes both actual managed checks on 11 source files.

The hybrid descriptor correction has seven independent metadata/SQLite reader checks;
48 streaming-parser checkpoint comparisons preserve behavior. These counts overlap
component work and are not a unique full-suite total. The earlier eight PostgreSQL schema
checks belong to the old model bytes; two native item-builder checks belong to their
original immutable source. Neither proves native end-to-end settlement or concurrency.

Frontend lint, type checks and a production build pass locally. The initial build lacked
a local authentication secret; its failure is retained. The successful build used a
fresh temporary synthetic secret in the child environment, without reading a production
secret or writing an environment file. The contract adds two status/cancel routes and
the asynchronous 202 response. Final generated-client type checking passes. The actual
whole-application OpenAPI exporter and new full CI remain remote verification gates.

[Sanitized integration evidence](evidence/2026-10-11-full-catalogue-integration.json)
records source pins and proof limits. The previous committed-source CI belongs to
`d5c5184`, not these new changes.

## Published-source CI and corrections

The reviewed Stage D source was published as `91bf10b0bd9a095fb499d9eced5b508cb311dbfc`.
[CI run 38095509980](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/38095509980)
is terminal: frontend, companion and cold-browser jobs pass. Backend lint, types,
compilation and the PostgreSQL migration chain pass, but runtime tests and the actual
OpenAPI exporter are skipped after public ECR rate-limits the disposable Redis pull.
Security scanning detects three reviewed false positives: a public SQL identifier
and two source-file hashes. The corrections retain the exact Redis image digest,
use the existing verified Docker Hub cache, and structurally reformat SQL/hash evidence
without changing scanner rules. The revised migration and full runtime/exporter checks
still require new-source CI. See [retained failure and correction evidence](evidence/2026-10-11-catalogue-ci-correction.json).
No production promotion or flag activation has occurred.

## Checkpoints

- [x] Review and integrate the 15 runtime/UI/migration bodies, including three narrow corrections.
- [x] Preserve the original failures and exact prior native evidence.
- [x] Pass configured backend lint/type checks and frontend lint/types/build locally.
- [x] Add status, cancellation and asynchronous acceptance to the generated API contract.
- [x] Integrate the three ordinary test bodies and pass configured test lint.
- [x] Publish the reviewed source and obtain terminal exact-source CI metadata.
- [x] Identify both CI failures and retain their evidence.
- [ ] Pass new corrected-source CI, including shipping tests, scanner and actual exporter.
- [ ] Verify the structurally revised migration on the new source.
- [ ] Verify actual full-chain migration and exporter drift in CI.
- [ ] Prove native source/resume/history concurrency and financial outcomes end to end.
- [ ] Measure cold/warm catalogue work, conversion/allocation cost and pack margins.
- [ ] Establish shared-content retention/cleanup and compatible reader rollout/rollback.
- [ ] Complete document worker native isolation, archive, scanner and private HTTP acceptance.
- [ ] Complete permission, protected integration and guarded GCP/Vercel rollout gates.

No hard eight-second native wall-time bound is claimed. SQL timeout callbacks do not
bound Python/native conversion, network stalls or full legacy GET allocation. User
status is resumable, but field latency/cost and real provider scheduling remain unproved.
