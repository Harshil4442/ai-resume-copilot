# Separately approved resume attachment assistance

This is disabled local development on `codex/application-form-completion` after
the phone/dropdown checkpoint. It does not complete an application, prove a real
employer upload or deploy the feature. F7 and its consumers remain frozen at
schema18; the evolved, undeployed schema19 draft needs a later additive release.

`BROWSER_ASSISTANCE_ATTACHMENT_ENABLED` defaults to false. A separate protocol-three
review authorizes only `attach`; version-one/two fill approvals never gain that
capability. After answer assistance, the candidate clears its review and inspects
the current form again. The candidate sees the selected sealed original/custom/
tailored artifact, source resume/version/artifact IDs, filename, MIME, size, SHA-256,
exact opening/destination and the single currently empty Resume/CV input. The
approval digest also binds the current document, structural context and supported
answer values. Any later change requires another review.

The attachment review has no preapproval download button or download prerequisite.
It explicitly authorizes delivery to the connected browser device and selection
into the employer input. Selection can immediately upload the full file through
page callbacks before final submission. Cancellation cannot recall prior disclosure;
an already authorized action may finish during its original ten-second lease.
The lease is never extended to accommodate a slow download.

## Binary and mutation boundary

1. A signed device `begin` requires fresh owner/device/review/application approval,
   exact package/artifact/source, flags, document/context and an unused action.
   Its durable action and signed lease bind the complete file descriptor and
   the exact empty `resume` input. Expiry is the earlier of review expiry and ten
   seconds; the companion persists running intent first.
2. A separate signed JSON `POST /browser-assistance/device/artifact` accepts only
   the review/action/digest identities. It cannot accept a URL or arbitrary blob
   identity. Existing owner/device/application/source checks and the action's
   unexpired lease authorize that exact artifact. The request is capped at 64 KiB.
3. Source metadata is checked before I/O. Inline content or the pinned GCS
   generation is read and checked against exact size/hash. The bounded GCS path
   requests bytes 0 through declared size inclusive (one overflow sentinel), a
   five-second request timeout and no transport retry. DB locks are released
   during storage I/O, then every authority/flag/package/deadline check is freshly
   repeated under lock immediately before the durable binary-delivery claim.
4. The claim is committed before the binary response. A lost response, repeated
   request ID or new request ID for that action cannot obtain another delivery.
   No binary content enters request receipts, browser storage or a general URL.
   Backend/BFF/companion enforce a nonempty maximum of 10 MiB, exact length,
   filename, allowed PDF/DOCX MIME and SHA-256. The BFF and companion rehash the
   actual bounded response and do not follow redirects or retry.
5. The isolated Chrome adapter rehashes the bytes and rechecks the exact document,
   destination, complete context/current answers and the one known empty file
   input. `File`/`DataTransfer` construction occurs before final form and deadline
   checks. Immediately before the single native `files` assignment, the original
   deadline and cancellation input are checked again. Explicit existing-upload
   IDs, remove/replace-file controls or file download links make even an empty
   input ambiguous and require manual handling. Only then are input/change
   events dispatched. Local cancellation during delivery prevents selection.
6. Selection is checked against exact file metadata/hash and unchanged context.
   Conditional changes or unknown/lost callbacks stop without another selection
   or delivery. A commit finishing after expiry retains its claim but sends no
   late bytes. Completion records `selected_upload_unconfirmed`; it does not
   claim employer upload acknowledgement, a completed application or a receipt.
   Application status/credits/reservations are not settled by this assistance.

The new route reuses the existing sealed artifact; it does not regenerate a
tailored document or fetch any employer URL. Tailored version/evidence revocation,
source closure or package/filename/size/MIME/hash drift invalidates authority.
An existing attachment is never replaced. Country, employment, unsupported
required fields, CAPTCHA/login and final submission remain manual.

## Durable history and release scope

The undeployed schema19 draft now adds non-null `review_protocol_version` with
default 1 and a 1–3 check, backfills retained version-two payloads to 2, and keeps
the 0–12 review cursor bound. New v2/v3 reviews store their marker independently
of answer payloads. Cancellation/expiry can erase sensitive payloads while
completed/cancelled/expired history retains its kind. Downgrade refuses any
durable marker above 1, an extended cursor, or a retained typed/attachment payload.
Both PostgreSQL and SQLite migration paths remain supported.

No schema18 deployment consumer or allowlist was broadened. The
[later-release assessment](BROWSER_TYPED_CONTROLS_SCHEMA19_RELEASE_2026-10-15.md)
now includes observing the protocol column/default/check/backfill as well as the
cursor check. Do not apply an edited draft to an existing deployed revision19;
this branch has no deployed schema19. The later immutable release needs its own
reviewed migration, deployment, permission/authentication and user-journey proof.

## Local evidence and remaining work

Installed MV3 tests locally fulfill every portal and HireWiz HTTPS request. Owned
synthetic files alone are selected; a synthetic page upload callback is locally
fulfilled and its posted bytes are checked against the approved hash. The tests
cover approval before delivery, exact one-shot selection, lease/binary tampering,
cancel/revoke/expiry/flag/source changes, changed inputs/answers/destination,
existing files, local cancellation, conditional callbacks and lost delivery/
outcome acknowledgements without replay. Test-only exact-origin grants avoid the
native human permission dialog; they do not prove production grants.

Actual PostgreSQL tests cover competing same/new signed binary requests with one
winner, revocation committed during unlocked storage I/O, and selection/history
on the full migrated schema19 chain. Migration tests cover v2 backfill and durable
markers after completed/cancelled/expired v2/v3 payload erasure. Separate tests
exercise actual SQL/crypto services, shipping HTTP framing, bounded storage reads,
candidate approval UI and binary BFF bounds. All data and transports are owned
local fixtures; no provider, GCP, Neon or real employer endpoint is used.

Final local validation passes 256 backend tests, 620 frontend tests, 71 assistance
Chromium cases (25 installed MV3), 14 legacy MV3 cases, 76 companion unit tests and
five fixture cases. Ruff, 120-file Mypy, frontend lint/typecheck, generated contracts
and a normal webpack production build pass. The local build used an owned copy of
installed dependencies and a synthetic build-only auth secret. The deterministic
0.4.0 source ZIP was built twice and checked against its runtime bytes; SHA-256:
`880f4510dacf4b6ea40f741322c1560067c5296209ba8f81553522aa76d9ab5d`.
The 0.2.0 and 0.3.0 archives are retained unchanged. See the
[machine-readable evidence](evidence/2026-10-15-browser-attachment-local-acceptance.json)
for exact commands, source hashes, corrections and cleanup.

The full paid apply outcome remains open: live upload acceptance/status, remaining
required fields, exact final-submit approval, a one-shot submission action,
genuine employer receipt/status reconciliation, payment settlement and production
rollout still require implementation or acceptance. Local file selection closes
none of those gates.
