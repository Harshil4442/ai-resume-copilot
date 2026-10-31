# Development native acquisition Job entry

Source preparation only. No Job, identity, grant, funding allocation, native
capture, model output or quality acceptance is created by this document or CLI.
The existing forty-development /160-held-out split and all200 identities remain
unchanged. This entry consumes only the40 selected source files; the existing
packet reader still hashes its full frozen stream without deserializing held-out
case records. A native service capture is not a renderer/page or human review.

`backend/scripts/native_acquisition_job.py` supplies the missing container entry
around the existing reviewed durable acquisition channel. It does not deploy or
run itself. Build and qualify an immutable image containing this exact entry,
the channel, original clients, locked runtime and frozen packet before use.

## External authority and native preflight

The operator must independently review and retain:

- A real current funding allocation covering the full80-request development
  split, plus an explicit separate runner/storage/egress/tax envelope. Gemini
  prepaid credit is not GCP compute funding. Synthetic test artifacts fund nothing.
- The original acquisition authorization with state `authorized`, a window of
  at most24hours and an exact absolute journal path. Use the same journal path
  across ephemeral executions, e.g. `/tmp/hirewiz-native-acquisition/journal`.
- Real current private worker URLs, source/image/policy/native acceptance and
  pre/post-batch serving pins; routing, metadata identity and least privilege.
- An immutable source/image and exact Job template, with one task,
  parallelism1, retries0, finite timeout, no execution overrides or mounted
  credentials, database/model secrets or inherited application environment.
- The actual Job create acknowledgment/UID/generation/etag and actual Run
  operation/Execution acknowledgment. Preserve lost acknowledgments and reconcile
  those exact resources; no new name or second Run as a timeout workaround.

Cloud Run supplies Job/execution/task environment variables. The entry checks
their single-task shape but explicitly labels them **environment observations**;
the external operator must join them to native resource GETs and terminal status.
See [Cloud Run container contract](https://docs.cloud.google.com/run/docs/container-contract)
and [execution overrides](https://docs.cloud.google.com/run/docs/execute/jobs).

## Immutable inputs

Use one reviewed private `quality-development/<experiment-id>/` prefix. A
`development-native-job-input-v1` bundle embeds the original authorization,
durable channel binding and externally reviewed dispatch. It also contains:

- Exact generation, size and SHA256 for the materialization manifest and the
 40 selected sources; no extra native files, parent traversal, aliases, path
 collisions, overwrite or mutable latest pointers.
- Exact bounded native-request funding, runner funding and dispatch review files.
- The entry's SHA256, expected Job name, and previous dispatch for a later batch.

Every input object name starts `input.`. The bundle and authority metadata are
at most256KiB, each source at most5MiB, and all input bytes at most200MiB. Inputs
are exact-generation GETs only. The workspace must be a new private directory;
files are exclusive creates with0600 mode inside0700 directories. An existing
workspace cannot be reused implicitly.

Example container arguments, after those external gates pass:

```text
python -m scripts.native_acquisition_job
  --namespace gs://<reviewed-bucket>/quality-development/<experiment-id>/
  --bundle-name input.bundle.json
  --bundle-generation <actual-generation>
  --bundle-sha256 <actual-reviewed-sha256>
```

The entry uses explicitly constructed metadata-server credentials, not an owner
login, ADC key file or a new credential. Unexpected app credentials, alternate
project settings, proxies and storage-emulator routing stop before client setup.
See [google-auth Compute Engine credentials](https://google-auth.readthedocs.io/en/latest/reference/google.auth.compute_engine.html).

## One-send and recovery behavior

The first batch initializes the original journal and can send only1–10 requests.
Before a processing HTTP call, the existing local fsynced intent and remote
create-only phase intent must both acknowledge. Source hashes, active funding
window, full-split reserve, original transports and decoders remain unchanged.

A subsequent ephemeral execution requires a separately reviewed new dispatch.
It restores the prior acknowledged checkpoint through the original channel, then
sends only phases that are globally never sent. An intent without its terminal
result remains UNKNOWN and prevents processing; a new container, restored file,
expired permit or new dispatch cannot clear it. The runner never repeats a POST
or releases a reserved expense. Create-only context objects cannot substitute
for provider execution/result receipts.

The external operator must retain terminal Execution status, exact output object
generations/hashes and original bytes before cleanup or review. Output remains
`completed_unreviewed`, native approval `pending`, unknown actual native cost and
`authorizes_retail=false`. A nonzero exit remains refused/unknown with no replay
authorization. Actual funding, IAM, input publication, immutable build, native
dispatch, PNG/page custody, human review and full quality evaluation are pending.
