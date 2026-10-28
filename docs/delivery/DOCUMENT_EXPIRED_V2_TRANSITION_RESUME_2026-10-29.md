# Exact V2 scanner transition continuation

The manual rescue in GitHub Actions run `40771705772` completed a new native
ten-case Job and acknowledged all three caller `close` operations. The API and
analysis caller closes were verified. The coordinator close completed after the
controller's five-minute per-operation deadline, so the workflow stopped before
replacing the worker. The old worker is still at 100% traffic and direct upload
remains closed. This is an incomplete transition, not a new candidate request.

`document-refresh.yml` has a single manual `resume-expired-v2-transition` mode.
It downloads only artifact `12852818465` from that failed run, checking its
name, main-branch source commit and GitHub SHA-256 digest before use. The
artifact contains the Build identity, original native Job receipt and the
caller-close journal. Its private copy is rechecked against the actual Build,
Job, Execution, logs, current service configuration and all three exact LROs.
No Build, source upload, native Job or old caller PATCH is repeated.

The continuation requires the same expired V2 worker and the exact three
closed callers at 100% Ready traffic. A completed LRO, generation, UID, revision,
template hash or closed admission flag that differs from the original journal
causes refusal before a new write. Before the worker update, the maintenance
identity consumes one create-only GCS claim keyed to source run `40771705772`
and artifact `12852818465`. The claim binds the original 16-file journal hash,
native acceptance, live service preimages, intended worker template and
maintenance source hashes. It must be absent, created with
`ifGenerationMatch=0`, acknowledged and read back byte-for-byte. An existing
claim, another runner winning the create race, or an uncertain acknowledgment
is read-only reconciliation. The claim remains consumed even when a later
worker or caller update fails; the same workflow mode cannot be dispatched to
replay a mutation. Only then can the dedicated maintenance identity send one
etag-fenced worker update, three pin updates and three flag
restorations. Every action is journaled before send and checked afterward.
The per-PATCH wait is now bounded at ten minutes. If any new action becomes
unknown, retain the new artifact, inspect its exact operation and stop; neither
this mode nor a fresh refresh authorizes replay.

The successful result must be checked against the retained native acceptance
and the live worker plus all three serving caller revisions. The API must retain
the guarded hotfix image, point to the new worker digest, and have
`RESUME_DIRECT_UPLOAD_ENABLED=true`. The old synthetic Job is left in place for
separate exact-owner retirement; it consumes no running compute after completion.
Do not use a workflow summary or a passing CI check as proof of production
restoration. Unified-credit and payment checkout flags are unaffected.
