# Private document routing diagnostic

`infra/gcp/document_connectivity.py` prepares and runs a small synthetic routing
check. Preparing its plan makes zero network calls. This check is separate from
the accepted six document-engine cases and from the production release gate.

Before executing it, verify the accepted worker image is serving under internal
ingress, the replacement `hirewiz-runtime-20261014` identity has the worker's
scoped invoker grant, and the fixed private network/subnet/private DNS contract
has been read back from GCP. The diagnostic must run inside GCP with that runtime
identity and Direct VPC routing. A locally impersonated token is insufficient.

The diagnostic checks the metadata project and runtime identity, obtains an ID
token with the exact worker origin as audience, and keeps that token in memory.
It requires the worker hostname to resolve only to Private Google Access VIPs.
It retries DNS/read-only GET readiness for up to 75 seconds; the worker's normal
GET response is its deliberate JSON 404. It also checks unauthenticated external
HTTPS to GitHub, with no worker token attached.

Next it sends one synthetic DOCX to `/inspect` and checks the byte-bound parser
receipt and known fixture text. It sends one concise synthetic edit to
`/validate-docx` and checks the byte-bound candidate, identical source/candidate
scan receipts, and accepted image, policy, LibreOffice and font manifest pins.
The receipt must describe one page, one changed paragraph and retained protected
text. Existing ordinary protocol and patch modules are loaded individually;
`app.main`, application packages and the release runner are not imported.

The processing POSTs are each attempted once. Unknown or refused results stop
the diagnostic; no POST retry occurs. Output contains only finite stages,
booleans and attempt counts. Tokens, source/candidate bytes, parsed text, response
bodies and exception details are never printed or persisted.

## Exact invocation shape

The application image copies `backend/`, so the new `infra/` source is not in the
current image. Prepare a reviewed ordinary source override in a structured Cloud
Run v2 Job request; avoid shell escaping an embedded program. Set the one
container's immutable application image to the approved backend image, command
to `/app/.venv/bin/python`, and args to `['-c', SOURCE_TEXT, '--execute']`,
where `SOURCE_TEXT` is the exact reviewed diagnostic file. Python `-c` does not
define `__file__`; the diagnostic uses the image's fixed `/app/app/services`
path. Source is ordinary software, not a credential.

Use these fixed Job settings:

| Setting | Value |
| --- | --- |
| Project / region | `ai-resume-parser-482412` / `us-central1` |
| Job | `hirewiz-document-connectivity-20261014` |
| Service account | `hirewiz-runtime-20261014@ai-resume-parser-482412.iam.gserviceaccount.com` |
| Tasks / parallelism / retries | `1` / `1` / `0` |
| Timeout | `300s` |
| CPU / memory | `1` / `512Mi` |
| Network / subnet | `hirewiz-private` / `hirewiz-callers-us-central1` |
| VPC egress | `PRIVATE_RANGES_ONLY` |
| Environment, secrets, mounts | none |

Bind the actual Job/Execution identifiers and immutable application source/image
to the invocation record. On success, independently observe terminal completion
and the sanitized result, then remove the temporary execution/job and confirm
absence. Do not replay Create/Run after an unknown acknowledgement; reconcile the
known exact native operation first. A successful result proves only this caller
reached and exercised these worker endpoints. It does not prove production API
or analysis revision routing, data preservation, payments, overall isolation or
release acceptance; those existing checks remain required.
