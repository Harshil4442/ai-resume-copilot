# Exact partial V2 scanner continuation

The first continuation, GitHub Actions run `40781707554`, stopped after its
API `open` PATCH was acknowledged. Its retained artifact is `12858021573`,
`sha256:e67577ef8f59495554fba9c27c59748d40fb38e80c9c52d61af69fc3b839cde4`.
The worker and all three caller pin updates were verified. The API `open`
operation `d83f0c50-f719-45cf-9dff-056c913e0b51` completed and became Ready,
but the two worker caller opens were not started. The original create-only
claim is consumed. Repeating `resume-expired-v2-transition` is forbidden.

To avoid offering a partly restored upload route, the reviewed schema25
maintenance operator closed the API on the same image. Its original plan has
SHA-256 `7e68d55d76e3da6bf5bbea33721d34a4385a3ab8482e5a4d54b809c5fbe67d50`
and nonce `5c8888070c0e8ff8e3ca93bf7784fbe0`. The API is Ready at generation
344 on `ai-resume-parser-m25-5c8888070c0e-close`, with optional AI and direct
upload disabled. The close operation
`392cebba-48f1-48ce-b623-f09b5274b252` is complete. The maintenance plan
and journal must remain private and unchanged; its `close-api` reconciliation
must show `service.ai-resume-parser.close.checked.json` before any restoration.

The new manual `resume-partial-v2-transition` mode accepts only the exact
failed artifact. It checks the GitHub artifact identity, branch, head and
digest, then checks the 35 retained JSON files against the pinned tree hash.
It revalidates native ten-case acceptance, reads back the first claim, worker,
three caller pins, API-open operation and revision, and exact emergency API
close operation and template. Each live service must still match the captured
preimage. A **separate create-only GCS claim**, tied to this failed artifact,
maintenance close and source hashes, is consumed before any new PATCH. Only
the analysis worker and resume coordinator may receive one etag-fenced `open`
PATCH each. The API stays closed in this mode. The workflow result must report
`document_refresh_partial_restoration_observed`, and its private artifact must
contain verified claim and both caller-open markers.

After the two caller opens and live readback, run
`schema25_maintenance.py restore-services` and then `restore-schedules` using the **original** emergency
plan, SHA and journal. Only the API was closed by that maintenance journal, so
its restore does not change the two newly opened callers. Read back API,
analysis worker, coordinator and document worker, their exact image/pins,
Ready 100% revisions and admission flags. Confirm the API restored the
captured prior flags, including direct upload. Then move promptly into the
separate schema25 maintenance and release, which closes optional AI and all
credit/checkout routes before migration.

An absent, changed or uncertain claim, operation, template, traffic allocation,
proof or acknowledgement stops the continuation. A claim stays consumed even
if a later PATCH fails. Never redispatch this mode or replay an uncertain
PATCH. Retain the artifact and reconcile the exact operation with a separately
reviewed recovery. Neither this mode nor the API restore enables checkout or
employer final submission.
