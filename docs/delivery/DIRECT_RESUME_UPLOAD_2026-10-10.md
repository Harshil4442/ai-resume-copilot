# Direct resume quarantine backend candidate

The twelve-path V2 backend is integrated after independent review; the API remains disabled by default and nothing is deployed. Existing protected lifecycle/monetary source files are unchanged. The receipt API and fresh-definition repair are integrated separately. Exact source and bounded evidence are recorded in [the integration evidence](evidence/2026-10-10-direct-resume-upload.json).

## Delivered flow

1. An authenticated candidate creates a small JSON upload intent containing filename, exact byte count, SHA-256 and optional enrichment preference, with a bounded idempotency key. Only PDF/DOCX are admitted in this first direct-upload slice. The API derives MIME type, reduces filename to its basename, restricts size to 1..5,242,880 bytes, and serializes owner admissions (three active intents, twenty per rolling day).
2. A durable row records opaque owner-bound ID and both exact private object targets before any signing request. Replays return the same row and original grant deadline; changed metadata under the same key returns409. Signing failure leaves a discoverable cleanup obligation. The PUT grant lasts300s and admission lasts900s.
3. The browser will PUT original bytes directly to the quarantine bucket, with signed exact content-type, generation-match0, and inclusive byte-size range N,N. File bytes do not traverse Vercel. Native CORS/IAM and actual signed XML upload behavior remain deployment gates.
4. The owner marks the intent complete. This durably queues it; the request does not run a scanner or charge units. The coordinator claims a300s SQL lease, limited to three attempts. Duplicate workers and stale workers cannot release a saved resume.
5. The coordinator checks native metadata, pins the immutable quarantine generation, rejects content encoding, reads raw bounded bytes, verifies length and SHA-256, and calls only the approved private document worker receipt API. Worker image/policy, source byte hash/size/format, ClamAV engine and bounded parsed fields must all agree. There is no local PDF/DOCX parser, model fallback, reconstructed success receipt or model charge.
6. Exact original bytes are copied to the fixed clean target using generation-match0. Final owner/lease/expiry/generation checks precede a single transaction saving existing Resume fields and source bytes, plus its clean generation/scan receipt. This deliberately retains existing inline source consumers. New source access returns a60s signed generation-pinned GET URL; all candidate control responses are private/no-store.
7. Cancellation invalidates the lease and preserves cleanup work. Owner or saved-resume FK unlinking atomically clears candidate filename/enrichment/scan receipt, revokes the attempt and records detached cleanup work via a trigger on the new ordinary table. No protected account-lifecycle code is changed.
8. Bounded cleanup batches replace exact quarantine/clean payloads with zero-byte live generation markers. The markers prevent still-valid generation0 upload grants and delayed clean writes from recreating sensitive bytes. A currently saved resume retains its clean source. Provider failure keeps retry work; cleanup does not depend on a deleted user's credentials or a browser session.

## Root integration dependencies

- Install the independently frozen real `InspectedResume`/`inspect_resume_document_with_receipt` implementation before enabling these routes. The new scanner fails closed when that contract is absent. An offline bridge against its exact frozen source verifies one synthetic transport call and the existing five-tuple contract; this is not a scanner/cloud proof.
- Integrate the ordinary0014 migration after actual0013. Production is still at its separate known runtime/schema generation; this candidate does not authorize bypassing protected account/monetary migrations or deploying with mismatched schema.
- The separately reviewed coordinator now invokes `tasks.process_due`, `privacy.sweep`, and `privacy.cleanup_due` on a default-disabled runtime. It passes57 independent checks, including8 actual PostgreSQL cases; no native scheduler is deployed. Enabling the product without functioning native scan and cleanup is not accepted.
- Metadata/hash/PUT/complete/status polling/cancel UI is now integrated after74 independent checks, with291 whole frontend units/lint/types passing. Original-source download/CSP integration separately passes72 independent checks and an author's five-width synthetic5MiB byte/hash browser proof. Native GCS/CORS acceptance is pending; legacy/application/version artifact transports remain subject to their existing response ceiling.

## Native service configuration and acceptance

API/coordinator environment: `GOOGLE_CLOUD_PROJECT`, `RESUME_UPLOAD_QUARANTINE_BUCKET`, `RESUME_UPLOAD_CLEAN_BUCKET`, distinct `RESUME_UPLOAD_SIGNER` and `RESUME_UPLOAD_READ_SIGNER`. The existing document client additionally needs its URL, immutable image digest and policy SHA pins. `RESUME_DIRECT_UPLOAD_ENABLED` defaultsfalse and should remain false until integration/deployment gates pass.

Use private same-project buckets, uniform bucket-level access, public access prevention, Standard storage, versioning disabled and soft-delete retention0 when immediate payload retirement is required. Exact native settings and read/delete outcomes must be verified. Lifecycle policies are backstops; their asynchronous operation is not a timely deletion receipt. Upload signer needs narrowly scoped quarantine object creation; read signer needs narrowly scoped clean object reading. The API only needs keyless signing authority. The coordinator needs narrowly scoped native metadata/read/create/conditional-retirement capabilities on these exact prefixes and its SQL permissions. The document scan worker receives only bytes and its fixed configuration; no GCS, model, payment or SQL credentials.

Signed capabilities are bearer URLs. Do not log them. Quarantine bytes must never be publicly served. CORS must name the exact production frontend origins and signed upload headers; CORS is not an authorization boundary. Verify an actual5MiB upload, one byte over limit, changed size/type/hash, generation0 replay, expired grant, wrong owner, bound scanner refusal, delayed writes, cancellation, and account/resume deletion. Verify source generation-specific downloads become inaccessible after retirement, including noncurrent/soft-deleted state where relevant.

Deploy scanner and prove isolation/fresh ClamAV signatures first; establish buckets/IAM and exact runtime roles; apply reviewed compatible ordinary migrations; deploy API/coordinator with routes disabled; run native acceptance; ship frontend; enable gradually only after durable processing/cleanup and operational alerts are working. None of these native/cloud operations were performed by this author.

## Evidence and exact limits

V1 author tests:63 new controls, zero skips,34.54s, actual uniquely named disposable PostgreSQL schemas plus synthetic GCS/scanner only. Six existing legacy source/migration tests pass using SQLite and explicitly synthetic files/mocked worker. Ruff and Mypy pass for new domain/routes/tests. The original worker/receipt source files remain unchanged in this candidate.

Retained zero-byte closure markers and associated cleanup metadata currently have no finite disposal policy. Removing them requires verified retirement of all upload grants and in-flight generation0 writes; deleting them immediately would permit payload resurrection. This is a deliberate safety/retention trade-off, not a claim of complete retention closure. Physical erasure also depends on actual bucket versioning/soft-delete settings. Cleanup removes the new GCS payloads; existing inline Resume deletion remains owned by the existing account/resume lifecycle.

Optional skill enrichment is unavailable on this coordinator path and is reported explicitly, with zero units charged; it is not silently run. The preserved original PDF/DOCX format is the exact uploaded byte sequence. This slice does not implement or close legacy TeX/ZIP projects, old unscanned stored-source admission, tailoring/export rendering, browser auto-apply, global job coverage, or monetary cutover.

Requests use fixed GCS/IAM endpoints, explicit trusted CA/TLS context, no environment proxy, bounded per-operation/auth timeouts and bounded IAM response output. Native performance, cost and platform deadline acceptance have not been measured here. The document client independently supplies its absolute75s network deadline; this packet does not invent native scanner or sandbox evidence.

Official references: [GCS XML request headers](https://docs.cloud.google.com/storage/docs/xml-api/reference-headers), [signed URLs](https://docs.cloud.google.com/storage/docs/access-control/signed-urls), [CORS](https://docs.cloud.google.com/storage/docs/cross-origin), [soft delete](https://docs.cloud.google.com/storage/docs/soft-delete), [asynchronous lifecycle](https://docs.cloud.google.com/storage/docs/lifecycle), [IAM service-account permissions](https://docs.cloud.google.com/iam/docs/service-account-permissions), [Vercel function limits](https://vercel.com/docs/functions/limitations).

## Reviewed integration checkpoint

Both V1 independent failures are retained: parsed content appeared in a wrapper representation, and a deletion during released quarantine cleanup could postpone clean retirement by24 hours. V2 hides the parsed representation and locks Upload then Cleanup, rechecks the current saved-resume binding and preserves immediate retirement after deletion. The original probes are unchanged. Independent V2 review passes 69 controls with zero skips.

Root composed upload/migration/source/release checks cover 197 distinct passing cases across the initial 196-pass run and an 83-case affected-file replay. The initial schema-chain assertion failure remains preserved; it was updated to assert the ordinary0014 suffix without expanding either production release authority. Root receipt/related checks cover 333 distinct passing cases across an initial327-pass/six-setup-error run and the same six cases replayed with the locked psycopg driver. These are composed counts, not single full-backend results. Full CI typing passes 138 files; Ruff passes the unchanged full CI targets with gitignore discovery disabled after a local discovery timeout.

Production schema remains 0009; monetary publication still unconditionally refuses the missing native global consumer/provider fence. Native 0014 grants/migration review, scan and cleanup scheduling, private storage/scanner acceptance, measured operating expense and frontend integration remain required. Retained zero-byte markers and cleanup metadata also need a finite verified disposal policy before full retention acceptance.

## Disabled scanner deployment configuration checkpoint

The separately reviewed four-path deployment proposal is integrated with no cloud
transport or activation. Independent replay passes 76 distinct cases, root passes
all 56 authored cases, and full CI typing covers 139 source files. The proposal
uses a dedicated private scanner identity and the existing exact policy digest.
Its snapshot checker expressly retains untrusted provenance: image labels and
offline configuration agreement do not prove effective IAM or runtime isolation.
The upload UI and scan/cleanup runtime have since passed separate source reviews.
Native registry/image provenance, internal connectivity, sandbox cleanup, storage
permissions and real upload/deletion acceptance still precede production activation.

## Original-source consistency repair

The source-access route now checks current Resume ownership before interpreting
any direct-upload binding. An owned legacy resume with no binding returns the
exact404 `direct_source_not_found` contract. A retained binding whose upload owner
is NULL/foreign, state is nonreleased, or receipt/generation is invalid returns409;
signing unavailability remains503. Those outcomes cannot authorize a legacy fallback.
Root's77 affected cases and18 independent PostgreSQL/HTTP cases pass, zero skips,
with exact disposable-schema inventories before/after. Both original consistency
failures and their unchanged independent probes remain preserved. No native storage,
browser upload or production activation is implied.
