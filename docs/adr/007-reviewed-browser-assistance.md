# Candidate-reviewed Razorpay browser assistance

Status: implemented source; production remains disabled until migration, signing credential, release and real transport acceptance.

## Decision

Use the existing HireWiz Google/authenticated candidate session for approval and a
separate nonextractable P-256 Chrome device key for device requests. PostgreSQL
owns devices, revocation, exact review payloads and one-shot field outcomes. A
dedicated service signs each exact single-field lease with a separate deployment
credential. This implementation needs no employer submission API credential and
does not depend on the experimental independent native pairing/recovery factories.
Those native factories remain disabled and are not claimed as production proof.

The first adapter supports only the exact official Razorpay Greenhouse tenant at
`https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/<numeric-id>`.
Chrome requests optional permission for the exact HireWiz and Greenhouse origins;
the runtime enforces the Razorpay tenant/job path, selected application, top frame
and actual Chrome document identity on every inspection and field event. Other
Greenhouse employers and other ATS portals are not enabled by the origin grant.

## Reviewed workflow

1. The candidate saves an existing application package and chooses original,
   custom or approved tailored resume through the normal application workflow.
2. The extension proves its retained device key. The candidate signs in on HireWiz,
   compares the public fingerprint and explicitly confirms this browser within
   five minutes. No website password, session cookie or bearer token enters the extension.
3. The extension requests that application's actual saved form mapping and
   inspects its open portal tab. The backend accepts only known plain text
   semantics and job-specific IDs: first/last name, email, LinkedIn profile,
   website, years of experience and current location, up to seven fields.
4. HireWiz shows the exact package/file hash, download of the selected file,
   destination, proposed values, replaced current values, manual fields and
   autosave/disclosure warnings. Explicit approval binds the complete review hash.
   Unknown or sensitive answers are never inferred. Email blur is explicitly
   disclosed as potentially sending email to Greenhouse's validation processor.
5. The extension checks fresh server state and every local expected value before
   each action. The backend rechecks ownership, device revocation, current job
   freshness/content, package and artifact hashes under database locks, records
   a one-shot begin, then signs a ten-second exact single-field lease. The extension
   verifies the real signature and bindings; the adapter rechecks full context and
   all assisted values immediately before emitting text events.
6. The extension records filled/blocked/unknown outcome. Lost begin/outcome replies
   stop the sequence without automatic POST retry. Browser restart discards the
   in-memory context; a fresh inspection and fresh explicit review are required.
   Phone, choices, custom/sensitive fields, file attachment, CAPTCHA and final
   submission remain manual. Filling creates no employer confirmation and charges
   no application service credits.
7. After submitting manually, the candidate may explicitly record their own
   submission report in the application review. The report binds the current
   saved package for reference and retains its original UTC recording timestamp.
   It does not verify employer acceptance, submission time or which file was
   actually attached. It creates no provider receipt or application fee. The
   application becomes terminal `user_reported`; future package edits, approval,
   automatic execution and browser authorization are blocked. Existing browser
   reviews are revoked. Repeating the same report preserves its original date;
   a changed digest or any paid/unknown/API application is refused. An already
   issued ten-second field lease still has the cancellation limit described below.

## Trade-offs and limits

Five-minute approval keeps sensitive reviewed values short lived. A ten-second
single-field lease accommodates the candidate → Vercel → GCP → PostgreSQL relay;
two seconds was too tight for ordinary geographic latency. Cancellation/revocation
stops future authorization. An already issued lease and prior page callbacks
cannot be recalled; one already authorized field may finish within ten seconds.
Current DOM checks detect form changes but cannot prove every employer script's
future network destination. These limits appear in the review and popup.

This is ordinary signed API/session authorization with durable database state,
not independent native-witness restore protection or extension binary attestation.
The manifest's stable public identity does not prove binary integrity. Operators
must disable assistance, revoke/reset outstanding reviews/devices and rotate the
dedicated signer before reopening after a database restore or identity incident;
fresh candidate review is mandatory. Retained device proof does not solve restoration
of old approval state by itself. No synthetic authority or fabricated epoch is used.

Full DOM/manual-state context stays in extension memory; manual state is locally
hashed, which binds changes but does not make low-entropy values anonymous. Only
the context digest, mapped existing values and manual field descriptors reach the
service. Local durable checkpoints contain IDs/phase/count only. Cancel/revoke erase
review payloads immediately; expiry erases on owner/device reads and bounded worker
maintenance. Maintenance clears expired answer-bearing receipts too; the scheduled
cadence can delay physical erasure past expiry. Minimal outcome/digest metadata is
retained for audit and existing account deletion cascades through all three tables.

## Release and acceptance

Additive migration `20261014_0017` follows `20261011_0016`. Keep
`BROWSER_ASSISTANCE_ENABLED=false` during staging; enabled requests require a real
separate `BROWSER_ASSISTANCE_SIGNING_SECRET` of at least 32 bytes. The worker's
bounded maintenance operates even while the feature is disabled.

The exact source-only pilot ZIP is downloadable from the HireWiz installation page.
It has no granted hosts by default, cookies, content scripts, broad host grants,
external messaging, upload or submission action. The pilot uses Chrome's Developer
mode/Load unpacked; store publication remains a later distribution decision.

Actual installed Chrome tests use the shipping key/config/background/transport and
real isolated SQL/crypto definitions through locally fulfilled HTTPS responses.
They prove a supplied Chrome extension Origin (not a fabricated header), reviewed
fill-only events, missing-permission refusal and lost-begin no replay. Adapter/UI/BFF
tests cover dynamic current question IDs, exact values/digests, CSRF, ordinary Google
auth and failure handling. These tests use synthetic owned data and issue no real
employer or processor request. Staging must still verify the deployed BFF/API auth
path, human optional-permission dialog and actual current portal read-only inspection.
No real application submission is a smoke test or release proof.
