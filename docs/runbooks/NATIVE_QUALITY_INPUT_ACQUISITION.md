# Native quality input acquisition: first usable scope

This local operator tool captures actual responses from the **existing private
Cloud Run services**. It creates no Cloud Run Job, database or model
request, IAM binding, key, account or customer credit. The optional `execute`
command sends bounded HTTP document-inspection/compiler requests; it therefore
incurs native processing expense. Plan and dry-run perform local checks only.

The initial scope is all **40 frozen development cases**, with original source
bytes and case identities preserved. The complete plan retains all 200 case
IDs/hashes and the 40/160 split. Held-out acquisition is deliberately unavailable
in this first version; the final 200-case requirement remains pending. Do not
inspect held-out content to repair this tool or the parser. Do not rebuild the
sources, change the oracle, exclude difficult cases or call 34/40 qualified.

## Local acquisition permit

Use a separately reviewed private JSON instance of
`native_input_acquisition.Authorization`, with `schema_version` equal to
`development-native-service-acquisition-v1` and purpose
`unreviewed_native_input_capture_no_model_no_retail`. The closed Pydantic model
declares the exact fields; it grants no backend/provider identity or IAM access.
The existing model-evaluation permit depends on already-created capture hashes
and cannot honestly serve as this earlier input-acquisition permit.

The local permit binds:

- A draft/authorized state, named operator, experiment ID and timezone-aware
  window of no more than 24 hours.
- The exact absolute journal path. The same permit cannot choose another
  journal to bypass an existing intent. Keep its entire directory and any
  unknown outcomes; never delete/reset it to restart.
- The exact frozen packet manifest and materialization-manifest hashes.
- The private `/inspect` and `/compile` URLs, immutable image digests and
  corresponding policy hashes, reviewed against the deployed native custody.
- A hash-bound real funding-evidence artifact, positive INR reserve per request,
  request count and total native allocation covering the entire selected split.

These are reserved allocations, not a measured bill or a guarantee of maximum
infrastructure cost. Actual native cost stays unknown until separate invoice
reconciliation. No synthetic funding document from a test is live authorization.
The permit supplies no credentials. Original production clients obtain their
existing private service identity in memory; no token is printed or retained.

## Commands and one-send behavior

Run from `backend`, using its installed dependencies and private reviewed paths.
The example paths below are descriptive, not existing permits or receipts.

```sh
python scripts/acquire_native_quality_inputs.py plan --authorization /private/review/acquisition.json --native /private/original-sources
python scripts/acquire_native_quality_inputs.py dry-run --authorization /private/review/acquisition.json --native /private/original-sources
python scripts/acquire_native_quality_inputs.py execute --authorization /private/review/acquisition.json --native /private/original-sources --journal /private/exact-journal --initialize --maximum-requests 1
python scripts/acquire_native_quality_inputs.py status --authorization /private/review/acquisition.json --native /private/original-sources --journal /private/exact-journal
python scripts/acquire_native_quality_inputs.py export --authorization /private/review/acquisition.json --native /private/original-sources --journal /private/exact-journal --destination /private/new-unreviewed-export
```

Initialize once. Subsequent execute commands use the same journal without
`--initialize`. Each command permits 1–10 never-sent HTTP requests, default one.
An exclusive local lock prevents simultaneous senders. An exclusive fsynced
intent is written **before** acquiring a service token or invoking the transport.
The intent binds source/original-source/request/permit hashes and its held native
reserve. Exact raw successful response bytes and their hash are retained before
recording a validated, unreviewed result. Original response bytes are not
reconstructed from a parsed dataclass or expected fixture text.

PDF and DOCX use original inspection. TeX and TeX ZIP use original blank source
admission, original compilation and a separate inspection of the exact compiled
PDF. Requests use the existing production serialization, transports and pure
response/import decoders. All original size/format/image/policy/scanner and
compilation request/hash checks remain. No local parser or compiler fallback
replaces the service. The TeX transport's raw-capture wrapper shares its existing
single request/validation path; ordinary production callers still receive the
same decoded dictionary.

Any lost response, interruption or storage failure after intent creation leaves
that step **UNKNOWN** with its native reserve held. All new sends then stop.
Status inspects retained local evidence only. There is no original result lookup
or idempotency endpoint in either service, so no automatic reconciliation,
reconstruction, retry or new journal is a valid recovery. Even a response saved
before an interrupted acknowledgement remains unknown in this conservative
version. A verified service refusal and invalid response remain failed and
cannot be retried; other untouched cases may proceed. Compiler unavailability is
unknown, and is never mistaken for a verified refusal.

Resume/status/export recheck the exact original plan, sources, intent and
retained response bindings. A result label without its intent/raw response
cannot unlock a later phase. Changed sources, policy bindings, code, journal
path, raw bytes or result metadata refuse further work. Local files/directories
are private and owner-bound; symbolic/hard-linked journal artifacts are refused.
This is operator custody, not protection against an administrator replacing an
entire local evidence tree.

No Job/Execution ID, remote cleanup or successful deletion is claimed. Local
intent IDs are explicitly **local correlation**, not provider operation IDs.
The tool has no cleanup/reset command; preserve original sources and the journal.

## Unreviewed export and remaining acceptance

Export requires a fresh private directory and copies actual hash-bound source
responses, compiler responses/PDFs and PDF inspection responses. The unchanged
native importer validates the composite. It records the existing strict
development reading-order result without editing expected text or accepting
visible hyphens as equivalent. The six retained development failures remain
failures until actual independently verified remediation resolves them.

The registry custody is intentionally `unreviewed_service_http_capture_v1`, not
the existing trusted `operator_reviewed_native_capture_v1`. It contains no
reviewer identity or fabricated native approvals. Partial/failed/unknown cases
remain in the accompanying status, and no denominator is reduced.

An operator must separately verify the capture provenance and supply the
existing native validation map: actual extracted text, genuine rendered PNGs,
all structure/render/reading-order/link/layout/source-preservation checks, and
reviewer identity/time bound to **every page hash**. This tool does not fabricate
or auto-approve those artifacts. Only that real review may produce the existing
trusted registry and native map for `run_supported_evaluation.py dry-run`.

Complete native development input approval is required before funded model
execution. Two independent blinded reviewers plus a distinct adjudicator,
complete development exports and an immutable reference/model/prompt/rubric
lock are still required before held-out work. Actual held-out acquisition,
model quality, cost reconciliation, retail qualification and public launch
remain separate pending requirements; source-exact captures alone satisfy none
of them. Tests use explicitly synthetic transports and prove only local custody,
one-send behavior and protocol integration.
