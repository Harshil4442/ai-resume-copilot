# Exact evidence review versions

Evidence reads now return `snapshot_fingerprint`, a server-generated SHA256 of the
canonical JSON representation of every public `EvidenceResponse` field except the
fingerprint itself. This includes exact text, title, category, ordered skills,
metrics, resume/source/provenance, approval/confidence and both stored timestamps.
No source file or historical row is rewritten. This is an optimistic review version,
not a signature, private secret or substitute for account ownership.

`career.schemas.evidence_snapshot_fingerprint(item)` is the shared domain helper
for an ORM row or validated response. Consumers binding approved facts must retain
the evidence ID and this exact version; later consumers still need their normal
current ownership, approval and source checks.

Every evidence PATCH sends `expected_fingerprint` from the version actually
displayed. The server selects the owned row with `FOR UPDATE` and
`populate_existing()`, compares under the lock, then applies the existing edit and
approval behavior. A foreign or absent row remains 404. A missing or stale version
returns 409 with `detail.code=evidence_refresh_required`; it discloses no current
fingerprint and makes no change. Ordinary factual edits still return to pending
unless the caller explicitly includes an approval state, as before.

The workspace freezes the version when its editor opens. A background refresh
does not upgrade an unsaved draft's authority. A conflict refreshes the saved
evidence, preserves the draft and blocks saving until the user cancels/reopens
and compares it. Approval conflicts require a new explicit click after refreshed
content is shown. Mutation requests are never automatically retried. Evidence text
and skills are fully visible during review, rather than truncated.

## Release order and compatibility

This follow-on changes application bytes and must receive its own complete app
qualification. It is not included in frozen release253 or its native attestations.
It adds no database field, entity, migration, permission, model call or provider
request. Keep generation closed until the separate source/readability and full
quality gates are satisfied.

Deploy the qualified backend first, then the matching generated-contract frontend.
Existing reads are additive and continue working. Older frontends may receive a
409 on edits/approval until refreshed; there is intentionally no unversioned write
compatibility window. The new frontend disables evidence actions if an older or
malformed response lacks a valid fingerprint. Never remove this check or silently
retry using a newly fetched version. A backend rollback that lacks CAS would remove
the protection and requires a deliberate reviewed rollback decision.

## Verification limits

`tests/test_evidence_review.py` covers exact field binding, stale approval/edit,
missing-version fail-closed behavior, foreign ownership and stale SQLAlchemy
identity refresh. Workspace tests exercise the frozen editor token, stale draft
preservation, explicit new approval and missing-version controls.

`tests/test_evidence_review_postgres.py` uses the existing strict owned disposable
PostgreSQL fixture (`HIREWIZ_TEST_POSTGRES_URL`) to race two requests which both
cached the old row before competing for its lock. Exactly one mutation may win;
an unseen edited fact must never become approved. These native row-lock controls
must run in CI; the fixture asserts the owned URL is present when `CI=true`, so a
missing CI prerequisite fails rather than silently skipping. The shared fixture
also rejects any URL outside its exact disposable host/ports/database. Local
SQLite results do not prove PostgreSQL concurrency, and an
unconfigured local PostgreSQL test is explicitly skipped rather than represented
as native evidence. No local server or provider is started by this change.
