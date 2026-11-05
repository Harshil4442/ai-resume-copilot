# Workspace pagination and current-resume match context

This is implementation evidence for existing requirements **F04** and **F05** in
Stage 5. Their statuses remain in progress. This change does not establish load,
field-performance, native-document quality or production acceptance.

## Resulting behavior

Workspace opens with a compact, authenticated, tab-specific response. Resume
versions, evidence, contacts, reminders and activity use explicit windows of 20
records, with an API maximum of 50. Older records remain accessible through
owner-bound keyset cursors. Opening one record fetches its complete body; opening
a list does not fetch every evidence claim or resume version.

The connected resume, submitted version and explicitly selected version are
resolved independently of the first page. A missing selection stays explicit;
an incomplete page is never treated as proof that a saved file is absent. The
employer application selector also uses bounded version metadata, with its exact
saved selection resolved separately. Its source-edit flag is a display filter,
not artifact or application authority.

The original file, exact evidence fingerprints, transcription assessment binding,
complete internal generation inputs and complete opportunity exports are
preserved. Saving a source snapshot asks the server to select the complete
approved evidence ID set at that instant. More than 500 approved links causes an
explicit refusal before saving; no partial snapshot is accepted. Later approvals
are not added to that immutable selection. An unchanged snapshot is not tailoring.

A current-resume match is now distinguished from a historical match. Changing the
resume does not delete historical scores or exports. Workspace labels a match
for another resume and does not present it as current. Explicit learning reads
include the selected resume ID. In-flight and reused match results retain the
owner, opportunity and resume captured before the request, so a delayed response
cannot be relabelled after a source switch. This does not start generation.

## Interfaces and boundaries

| Interface | Purpose |
| --- | --- |
| `GET /api/v1/opportunities/{id}/workspace` | Compact header, exact selections, tab-specific first pages and current-match association |
| `GET /api/v1/opportunities/{id}/workspace/{collection}` | One bounded continuation page |
| `GET /api/v1/opportunities/{id}/workspace/{collection}/{item_id}` | Exact owned record body for explicit review |
| `GET /api/v1/opportunities/{id}/workspace/body` | Preserved role or outcome text on request |
| `POST /api/v1/opportunities/{id}/workspace/source-snapshot` | Server-selected approved links and unchanged source snapshot |
| `GET /api/v1/resume-versions/page` | Bounded source-scoped selector metadata and exact saved selection |
| `GET /api/v1/opportunities/{id}/match?resume_id=...` | Current-source read; mismatch requires refresh |

The match route without `resume_id` keeps historical/export behavior. Legacy HTTP
collection endpoints refuse explicitly above their browsing cap, before hydrating
full bodies. Internal complete-input and export services keep their existing
semantics. Private workspace responses use `private, no-store`.

Cursors bind owner, opportunity or source, collection, filter and page size. A
cursor never grants access. Client queries bind owner/source/tab/collection/page,
consume abort signals, and prevent stale drafts or unverified detail responses
from approving evidence. A newly connected upload clears that opportunity's old
workspace cache and displays a non-authorizing verification state until a fresh
response arrives. The existing resume cursor format remains compatible.

Source snapshots lock the opportunity and resume but do not acquire evidence-row
locks in the opposite order to transcription review. Existing version creation
revalidates all selected approvals before commit. No authentication cache,
permission, admission governor, financial rule, schema or document guard is relaxed.

## Validation evidence

- Backend: 78 focused workspace/resume controls on the earlier functional freeze;
  11 targeted match-association controls on the final delta, including one existing
  projection control. These counts are not summed as distinct final-suite tests.
- Local PostgreSQL 16.15: 24 valid/malformed JSON projection cases against an actual
  `json` column. The production eligibility expression passed; its unchanged
  helper needed no repeat after the match delta. Owned cluster/socket resources
  were stopped and removed. No cloud or paid native QA was used.
- Frontend: 95 earlier functional cases, seven affected semantic controls and nine
  targeted final match-scope cases. Final TypeScript and scoped strict lint pass.
- Browser fixtures: eight remaining desktop/mobile case-and-viewport pairs pass on
  the final product source with accessibility checks. Twenty-eight earlier
  distinct passing pairs remain historical evidence, not a claimed final clean
  36-case suite. Initial failures are retained.
- Independent backend/frontend reviews pass. Source pins bind 294 public app
  files, digest `4c86409a4eae9bd941e2e89a3b000b972fd891f3d1c67a9ed0fe68b3effb27af`.
  The reviewed HTTP maps preserve their original eight path and 17 schema pins,
  adding seven paths and 25 schema pins. Only the three reviewed literal-pin
  leaves change; qualification logic, historical sources and runner roles remain
  unchanged.

These are scoped implementation checks. Exact committed-source CI, a verified
application image and a compatible guarded rollout are still required. Existing
indexes are unchanged; production query plans, p95 authenticated reads, sustained
capacity and field p75 Web Vitals remain separate acceptance work.

At this document's preparation, the reviewed PR107 code is merged and its normal
merge has a Vercel Production artifact in **Staged** state with custom-domain
assignment skipped. The public domains retain their prior deployment. That
artifact predates this workspace change. The known document verification expired
on 5 November 2026 at 06:24:10 UTC; the owner's maintenance and paid-QA holds remain
in effect. No renewal, guard waiver, Gemini call, payment activation or employer
submission is authorized by this evidence.
