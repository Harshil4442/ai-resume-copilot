# Exact final caller continuation after the second failed scanner run

Manual run `40788512340` at `cbad4abb39017dcf744ede77ce6bfc2826395257`
failed after it consumed its create-only claim and acknowledged the analysis
worker `open` PATCH. Its retained artifact is `12860975525`, digest
`sha256:ffe242705f3bc036de6f41e8cfa4a85f2b447804f40ad926d38edc046fafefe3`.
The artifact contains exactly 40 JSON files with tree SHA-256
`27283c86c7f723fcdb41175c448311233ddb78c478397c600b42b72d4e2b6c84`.
It has the analysis `open.started` and `open.acknowledged` markers, but no
analysis `open.verified`, coordinator `open` markers or completion marker.

The second claim is present in GCS at generation `1793230614244914` and its
media matches the retained verified SHA-256
`f5888eafc2805c681ea25e3729a36113d6d4528094527b07526d0b4c222e88e1`.
The analysis LRO `ae717552-c804-479a-aff6-60d0520bc411` is done without
error. Generation 69 serves Ready revision
`hirewiz-analysis-worker-d-5436da554bc5-open`; its request and template
hashes match the retained `started` marker. The coordinator remains at
generation 27 on its `pins` revision with both admission flags false. The API
remains on the original schema25 emergency close, generation 344, Ready
revision `ai-resume-parser-m25-5c8888070c0e-close`, with optional AI and
direct upload disabled. The V3 document worker remains at generation 16.

The original API close is owned by the separate schema25 maintenance plan
`7e68d55d76e3da6bf5bbea33721d34a4385a3ab8482e5a4d54b809c5fbe67d50`.
Its `close.checked` marker must remain valid. This continuation never patches
the API, analysis worker or document worker, and never creates or runs a Build
or Job. It is not a replay of either prior failed mode.

## One-time path

The manual `resume-final-v2-transition` option pins the exact failed GitHub
artifact ID, run, branch, head and digest before downloading. It copies the
artifact privately and checks all 40 file bytes against the tree SHA before
native HTTP. It revalidates the original native ten-case acceptance, reads the
first and second GCS claims and their media, reconciles the acknowledged
analysis LRO and its actual Ready revision, checks the original API maintenance
close, V3 worker, all caller pins, and the coordinator's closed state. The
expected coordinator `open` revision must be absent.

Only then may it create the **third**, exact GCS claim
`hirewiz-document-refresh-source/5436da554bc5437cadccd296f3489bdd/final-transition-40788512340-12860975525.json`
with `ifGenerationMatch=0`. The claim binds this artifact, both previous claim
hashes, maintenance close, native acceptance and all four live preimages. All
four services must remain unchanged after the claim. It then sends exactly one
etag-fenced `open` PATCH to the resume coordinator. The shared controller
allows a bounded 180-second *read-only* service poll after the LRO is done when
Cloud Run still reports the old or not-yet-serving expected revision. Only
the prior or expected revision and traffic statuses may appear during this
poll; an empty or split status is accepted only while reconciling. Exact
prewrite service and revision GETs, and the acknowledged LRO GET, have narrow
bounded retries for known transient transport failures or HTTP 429/502/503/504.
A different template, generation, third revision or unrelated configuration
fails immediately. Neither an uncertain POST/PATCH nor a timed-out readback
is retried.

Successful evidence must include `final-claim.started`, `acknowledged` and
`verified`; the coordinator `open.started`, `acknowledged` and `verified`;
`complete.json` with status
`document_refresh_final_caller_restoration_observed`; and live Ready 100%
readbacks for all four services and pins. The result must say
`api_maintenance_restore_required=true`. The original maintenance operator
may then run `restore-services` and `restore-schedules` with its existing plan
and journal. Do not restore API admission from a failed continuation artifact.

If any check or send is uncertain, keep the API closed and reconcile the exact
claim, operation and live revision read-only. Never dispatch this mode again
after its third claim is consumed.

## Read-only stop on 2026-10-29

The first dispatch of this final mode, run `40795893079` at merged head
`ddb4f88207fa32af5bf9086ca9cea46cf959dc19`, stopped during preclaim
verification. Its retained artifact `12863897046` has digest
`sha256:07667206955bdfcc69ef5d13a6b32f8f4225df1cd15e172e348758c00f462fcd`
and contains only the same 40 source JSON files/tree hash above. No
`final-claim.started` or coordinator `open.started` marker exists. The exact
third GCS claim still returned 404; API, analysis, coordinator and document
worker generations and serving revisions were unchanged. Independent fresh
read-only proof of the native successor, current worker and all caller pins
passed. The runner emitted only a generic unavailable code, so the precise
transient read was not observable; this is a suspected availability stop, not
a proven Cloud Run mismatch.

The repair bounds retries to recognized transport failures and HTTP
429/502/503/504 on the finite GET and read-only IAM/logging POST routes. It
never retries a Build/Job write, GCS create-only claim or service PATCH, and
permanent proof mismatches still fail immediately. A future failure writes
only a source-owned fixed code to private `failure.json` and the workflow
output; vendor text is not retained. Before any manually reviewed new attempt,
recheck the exact third claim is absent and all four service preimages match.
The source artifact remains `12860975525` from failed run `40788512340`;
the preclaim-stop artifact is diagnostic evidence only.
