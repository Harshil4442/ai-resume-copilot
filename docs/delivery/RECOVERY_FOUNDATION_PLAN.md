# Recovery foundation: independent epochs, action permits and tombstones

Status: proposed on 8 October 2026. No production recovery guard, independently retained
external permit store, retention lock or restore procedure in this plan is implemented,
tested, provisioned or enabled.
`EMPLOYER_AUTO_SUBMIT_ENABLED=false` remains required. Local fixture companion work and
SQL admission tests do not prove these recovery gates or authorize real employer sends.

This design addresses EX05–08, SE05–06, DP05, G05B–D and G06D in
[the requirements](../requirements/JOB_SEARCH_AND_APPLICATION.md). It complements
[transactional dispatch](../adr/003-transactional-work-dispatch.md) and
[the remaining gates](REMAINING_REQUIREMENTS.md). PostgreSQL remains the financial and
product-state authority in its existing location. GCP Cloud Run, Cloud Tasks and the
current `us-central1` region remain the deployment platform; this proposes no AWS,
Kubernetes or PostgreSQL relocation.

## The failure this must prevent

Today `EmployerApplicationAttempt.launch_token` and `submitting` are committed before
the external API request. That fences ordinary duplicate queue deliveries in the live
database. An older database backup can omit that attempt, a later cancellation, account
deletion or permission revocation. A restored queue can then appear to authorize another
disclosure or send. Restoring the same SQL database cannot restore the safety evidence
that was created after the backup.

The safety authority therefore must survive independently of SQL restores. A signed
command, cached epoch, worker lease or prior approval alone cannot grant disclosure. A
pause between a check and a network request also matters: an epoch check does not recall
an already-started request or prevent an unrestricted old process from sending later.

| Threat / failure | Required result |
| --- | --- |
| Old queue item or restored SQL approval | No new disclosure without a current external approval and action decision |
| Two API workers or a worker and companion | One action owner; duplicate begin requests return no permission to act |
| Crash before/after upload, autosave or POST | Conservatively retain possible disclosure/send; do not replay it |
| Deletion/cancel/grant revocation races begin | The external authority serializes them; the winner defines the boundary |
| Restore removes attempts or account deletion | Independent launch evidence and tombstones are reapplied before access |
| Registry, journal, auth, clock or network outage | Fail closed for disclosure and send; no SQL-only or cached fallback |
| Paused server executor resumes after recovery | Egress/credential fencing plus epoch rejection; drain is not sufficient alone |
| Companion is offline, restarted or navigated | No new fill/upload/autosave; fresh online decision and destination checks |

The trusted boundary is the recovery service, its GCP identities and deployment/restore
operators. A compromised recovery administrator, a malicious extension, or a person
independently using the employer website is outside the guarantee. Unannounced restores
outside the enforced restore procedure are not claimed to be detectable. Provider-side
receipt accuracy and provider idempotency need separate tenant evidence.

## Proposed independent GCP control plane

Use a private Cloud Run `recovery-guard` service and a Firestore Native database in
`us-central1`, preferably in a dedicated GCP recovery project with separate restore and
administration permissions. Its records are excluded from application SQL backup/restore
automation. A separate protected GCS recovery-journal bucket retains write-once intent
and action evidence. Neither store is the existing application-artifact/`releases/`
bucket. Resource names and retention periods require a reviewed infrastructure proposal
before provisioning; no executable infrastructure is supplied here.

Firestore server transactions provide the atomic authority for current epoch, revocation
and action ownership. Use current strongly consistent reads, never historical `readTime`
or client/offline caches. Retry only the pure transaction; employer networking must never
run inside its callback. Google documents
[strong reads and serializable writes](https://docs.cloud.google.com/firestore/native/docs/understand-reads-writes-scale)
and [server transaction contention](https://docs.cloud.google.com/firestore/native/docs/transaction-data-contention).
Contention exhaustion is a denied action, not permission to use a stale result.

The journal writer has object-create permission and uses unique deterministic event paths
with `ifGenerationMatch=0`; it cannot replace or delete prior events. Duplicate writes
must verify the existing bytes/digest rather than treating every 412 as success. The
registry stores the exact journal generation and event digest. Bucket retention and
separate audit access protect the journal during its declared restore horizon.
[Generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)
and [Bucket Lock](https://docs.cloud.google.com/storage/docs/bucket-lock) support these
contracts. Locking retention is irreversible and is a later reviewed infrastructure
operation, not a side effect of implementing the API.

Only the recovery service can update registry records. API/worker identities can invoke
specific authenticated guard operations, but cannot write Firestore, modify the epoch,
delete journal records or supply arbitrary tombstones. Restore operators can close the
guard and advance the epoch; opening requires separate reviewed restore evidence. Browser
devices never receive GCP credentials or direct Firestore access.

| Record | Binding and purpose |
| --- | --- |
| `control/current` | Opaque random epoch ID, monotonic generation, `CLOSED`/`OPEN`, minimum executor protocol, approved immutable revision set, restore evidence ID and journal replay barrier |
| `subjects/{subject_uuid}` | Stable random identity separate from reusable SQL numeric IDs; deletion/revocation state and generation |
| `approvals/{approval_id}` | Subject, epoch, immutable SQL approval revision, exact package/batch digest, artifact hash/generation, tenant/origin, form/consent/evidence versions, actions and expiry |
| `grants/{grant_id}` / `devices/{device_id}` | Current tenant permission or device ownership, origin scope, action scope, generation and revocation |
| `permits/{permit_id}` | Attempt ID, canonical opening, approval/admission IDs and digest, executor/device identity, epoch, deadline, exact action sequence and journal references |
| `actions/{permit_id}:{step}` | `PREPARED` → `BEGUN` → evidenced `CONFIRMED`, `PROVEN_NO_ACTION` or `UNKNOWN`; a unique action key cannot become unused again |
| `opening_claims/{subject}:{canonical_opening}` | Retains confirmed and possible sends across SQL restore; uncertainty is not an expired lease |
| `tombstones/{event_id}` | Monotonic account, approval, artifact, device, evidence or tenant-grant revocation; journal generation and applied state |

A release revision or device ID is a binding, not a credential. The guard verifies the
caller identity and exact protocol independently. Store opaque IDs and hashes rather
than resume bytes, answers, cookies, email addresses or screenshots. Minimal encrypted
purge locators, when needed, have a separate access path and key scope. Pseudonymous
identifiers and digests still need a retention policy and restricted access.

## Approval and admission contract

The SQL admission implementation is complementary: candidate UTC-day and rolling-window
limits, employer limits, pending counts, exact batch quantity/credit ceilings and
canonical-opening conflict rules must pass atomically across replicas. Queue acceptance
reserves the admission; queued, submitting and unknown work continues to consume it.
Only a proven prelaunch failure/cancel or independently evidenced no-send reconciliation
can release it under the policy. Confirmed or rejected possible sends remain consumed
for the applicable window. Epoch advance and permit expiry never refund credits, erase
an opening claim or reset admission consumption.

The guard binds the SQL admission ID, policy version and exact enumerated batch digest;
it does not become a second credit ledger. A canonical opening uses a verified employer
key and requisition ID, with a conservative source/external-ID fallback. Explicit reviewed
aliases can unite cross-source representations. Ambiguous aliases pause execution; fuzzy
title matching cannot justify clearing an existing possible-send claim.

The recovery gate must restore admission usage from external begun-action/opening evidence
before accepting new SQL admissions; a restored lower daily counter is not capacity. A
fresh admission in the new epoch still checks surviving possible-send claims. When an
independently verified no-action result permits a new attempt, record that resolution in
the external authority/journal and allocate a new permit; never reuse the old action key.

Register an external approval only through a fresh authenticated candidate interaction
bound to the current epoch and a one-time server challenge. A restored SQL row or replayed
outbox message cannot register approval by itself. Bind the immutable
`EmployerApplicationApproval.id`, reviewed file bytes, destination, answers, consents,
requested actions, quote and expiry. After recovery require fresh approval of the current
package; copying an old approval to the new epoch is prohibited. Changes and withdrawals
write independent revocations before another action can begin.

## Action order and the external boundary

No action that might disclose candidate information precedes approval. File selection,
DOM filling and consent changes may trigger autosave; adapters must classify them as
disclosure, not preparation. A combined API multipart POST is both upload and submit.
Public job/form reads are separate only when they send no candidate data.

1. Validate the current SQL package/admission and collect immutable snapshots in a short
   SQL transaction, then commit/rollback before guard or provider networking. Recheck
   tenant permission, exact origin/route, job/form/consent versions, required questions,
   artifact hash/generation, owner, evidence and approval expiry. Never hold SQL locks
   while waiting for the guard, storage or employer.
2. Ask the guard to prepare a short-lived action for that exact approval, subject,
   canonical opening, admission, epoch and authenticated executor. `PREPARED` grants no
   authority to disclose and cannot be cached as a bearer permit.
3. Persist the local attempt and its external permit reference before any disclosure.
   Before final submission persist `SUBMITTING` and the attempt reference. A later SQL
   failure cannot erase external action evidence or allow a replacement send.
4. Create the protected journal action-intent event. Then the guard atomically reads
   current epoch/status, subject, approval, grant/device and action state, checks every
   binding/deadline, and changes the action to `BEGUN`. Cancellation/revocation and begin
   read/write the same affected authority records. A duplicate or ambiguous begin reply
   never grants another execution; journal intent without a confirmed begin is treated
   conservatively during recovery.
5. The server disclosure gateway performs the one external operation immediately after
   it wins begin. For browser assistance, the paired device records `action_in_progress`
   durably, exchanges the action online, checks the current tab/frame origin, form and
   file again, and invokes only that approved DOM operation within a bounded freshness
   deadline. Navigation, suspension, expiry or uncertain response pauses it. Each new
   upload/fill/autosave step needs its own decision. Final submit is a distinct action
   requiring a new current check and launch mark; a fill permit cannot authorize it.
6. Record independently verified receipt evidence or `UNKNOWN` without replaying the
   operation. After `BEGUN`, a crash, timeout, lost response, 5xx or lease expiry is not
   proof that nothing was disclosed/sent. A receipt reconciliation API cannot issue a
   new permit or send. It may accept a verified late receipt bound to the original
   attempt even after cancellation; retain only permitted redacted financial/audit data
   after account deletion and do not recreate the account or application payload.

`BEGUN` is the authorization linearization point, not proof of employer completion.
Cancellation that commits first prevents begin. Begin that commits first is already in
flight and cannot be promised recalled, including portal autosaves. Provider idempotency
can improve recovery only when its exact tenant/operation scope is documented and tested;
it does not replace the independent permit or current approval check.

For API execution the gateway owns employer write credentials and the action+send boundary.
General workers must have no direct employer write credentials or unrestricted employer
egress. Route their traffic through an enforced GCP VPC path with a denied direct-provider
route; the allowed gateway validates destinations rather than becoming an open proxy.
GCP's [all-traffic VPC egress](https://docs.cloud.google.com/run/docs/securing/private-networking)
permits firewall enforcement, but configuration and bypass tests remain outstanding.
During epoch changes fence old gateway identities/egress and drain bounded requests;
requests that might already have left remain uncertain. Credential rotation alone cannot
fence public unauthenticated upload/submit endpoints.

For a user-local companion, the server cannot police all network traffic on the device.
The honest extension must never replay a persisted in-progress step after restart and
must never act offline or from a cached approval/epoch. Already-begun autosave cannot be
recalled. The initial fixture companion supports fill-only/candidate-submit behavior;
automatic browser submission remains a later permissioned and tested capability.

## Independently retained deletion and revocation

Use the same guard authority for account deletion, application/batch cancellation,
approval/evidence withdrawal, device unpairing and tenant permission removal. These are
monotonic revocations of the old revision; new approval uses a new revision, never clears
the old tombstone. A new account receives a new subject UUID even if its SQL ID or email
resembles an earlier deleted account.

1. Authenticate the request and construct a minimal deterministic event ID and subject /
   affected revision scope. Create the protected journal tombstone with generation-zero
   precondition before changing SQL. A write whose success is uncertain must be verified
   by exact object generation/digest before proceeding.
2. In the guard transaction block the subject/revision/grant, revoke unused permits and
   attach the journal reference. An event left only in the journal is a pending deny;
   replay must apply it conservatively. Never acknowledge successful revocation until
   both independent writes are verified. If the guard is unavailable, new disclosure is
   already fail-closed; report deletion/revocation as pending and retry its durable intent.
3. Only then commit SQL cancellation/deletion, revoke authentication/session capabilities
   and queue generation-bound artifact cleanup. If this SQL transaction fails, the
   independent deny persists and idempotent recovery completes local erasure. Do not
   recreate PII to finish an audit record. Acknowledgement distinguishes blocked execution
   from remaining local/backup erasure and employer-held copies.
4. Preserve upload intents until their bounded completion window has passed. Late upload
   completion must record its exact generation and rearm cleanup, including after an
   earlier 404. Never purge a hash-shared object still referenced by another active owner
   package. Account-scoped cleanup targets recorded candidate objects only; it cannot
   delete `releases/` backups or the recovery journal.

Journal writes and registry updates are not one cross-service transaction. Their safe
order permits extra conservative denials after a crash, not a false success. No rollback
of a SQL transaction undoes a tombstone. Replayers reconcile prepared journal events,
record progress and expose retry counts/oldest pending age without retaining raw PII.

The retention horizon must cover every allowed SQL/object backup age, maximum restore
delay, command/approval lifetime and bounded in-flight window, with an operational margin.
For example, a later approved 35-day backup policy plus a seven-day recovery window would
need at least that combined horizon and the action margin; these are examples, not current
production retention claims. Retain active deny/opening records until all backups that
could resurrect them and all dependent capabilities have expired. A restore older than
the declared evidence horizon is rejected. Review a policy separately for originals,
drafts, receipts, commands, audit events, tombstones and employer copies. A retention lock
must not make candidate resume bytes or answers undeletable in the journal.

## Restore and epoch procedure

1. Close the independent guard first using an authenticated operator operation and journal
   the closure. Keep public API authentication/data access and task execution disabled.
   Advance to a new random epoch in the external authority; never restore or decrement it
   from SQL. Closing/advancing the epoch denies all unbegun old-epoch actions.
2. Fence old executor/gateway egress and identities, revoke device command sessions, pause
   queues and wait the documented maximum action lifetime/drain interval. Inventory
   `BEGUN`/journal-intent actions and treat uncertain windows as possible sends. Traffic
   draining alone cannot prove a paused old process is harmless.
3. Restore into an isolated GCP/SQL recovery environment with sending and user access
   disabled. Verify backup identity, age and evidence horizon; replay the independent
   journal and guard state. Erase restored deleted subjects and affected originals,
   drafts, answers, artifacts and auth sessions before access. Reapply approval/evidence,
   grant and device revocations. Generation-bound GCS replay preserves other owners.
4. Join external opening claims and begun action records to restored SQL. Reconstruct
   uncertainty without resurrecting candidate payloads; quarantine absent/orphan attempts.
   Keep existing holds and admission consumption, then reconcile balances/holds against
   independently retained financial evidence. Never infer refund or completion from
   restored counters, missing SQL rows or an expired worker lease.
5. Discard/invalidate old-epoch tasks and browser commands by generation. Preserve them
   as audited recovery inputs rather than converting them into new send intents. Require
   current authenticated login and fresh epoch-bound candidate approval for future work.
6. Produce replay coverage, pending-purge inventory, uncertain-attempt reconciliation,
   measured RPO/RTO and old-worker/queue/device rejection evidence. Only a reviewed gate
   can open the new epoch for approved immutable executor revisions. Enable read-only
   product access after the local erasure/revocation replay barrier; enabling real sends
   additionally requires tenant permission, form/receipt and egress gates.

If the external registry also needs recovery, keep the guard closed. Rebuild from the
protected journal, rotate identities/epoch and prove complete replay before opening.
Missing or ambiguous journal coverage denies execution; restoring the registry from the
same old SQL timestamp is prohibited. Measure this separately from an ordinary SQL restore.

## Warm API performance and failure behavior

Do not add a recovery RPC to every page request, health check, public job search or read-only
dashboard. Those paths use existing auth/ownership checks and bounded SQL queries after
the restore replay barrier has enabled data access. The recovery dependency is mandatory
for candidate disclosure, final launch, fresh approval registration and revocation, not
for ordinary navigation. The BFF must not prefetch or initiate these actions on page load.
Private artifact retrieval and sharing are authorization-sensitive even when HTTP uses GET;
they must not bypass a deletion/permit check through a cached or long-lived signed URL.

Use colocated warm guard capacity, connection reuse, bounded deadlines/concurrency and
transaction sizes; measure their cost and p95/p99 action latency rather than weakening
checks. Cache immutable package bytes privately by exact hash/generation where allowed;
never cache a positive epoch, revocation, begin decision or grant as launch authority.
On timeout show a paused/needs-action state. Safe failures before independent begin can
release the SQL reservation according to admission rules. Failure after begin preserves
unknown status and holds until independent evidence supports reconciliation.

## Tests and implementation sequence

The first implementation remains fixture-only and disabled for real sends. Use pure guard
adapters, an emulator and local HTTP/portal fixtures; do not call real employers. Emulator
tests alone do not prove GCP IAM, retention or production egress.

| Test | Required evidence |
| --- | --- |
| Two workers/devices begin the same action/opening | Exactly one winner; replay/lost response never grants a second action |
| Cancel/delete/revoke versus begin in both orders | Deny when revocation wins; explicit in-flight outcome when begin wins |
| Snapshot before launch/delete, restore, deliver old task | Old epoch rejected; tombstones/claims survive; PII not served |
| Crash before/after journal, guard begin, DOM autosave and POST | No blind replay; correct unknown/hold behavior at every cut |
| Pause old executor, close/advance, resume it | Network fence prevents new provider traffic; already-sent cases remain unknown |
| Registry/journal/auth outage and transaction contention | No cached/SQL fallback or employer request; bounded paused response |
| Changed file/form/origin/consent/action/price/approval | Prepare/begin rejected; refreshed candidate review required |
| Cross-owner/device replay; grant revoked; device restart/offline | Rejected before DOM/file/HTTP disclosure; checkpoint never repeats action |
| Deleted owner with live GCS upload and initial missing probe | Late generation requeued; another active package and backups retained |
| Torn tombstone write and SQL failure | Retry converges to blocked/erased state; acknowledgement never precedes durability |
| Ledger replay gap or over-age backup | Restore remains quarantined; opening requires reviewed complete evidence |
| Read-only warm page traffic versus action traffic | No guard calls on ordinary reads; mandatory checks on every disclosure boundary |

Implementation order: (1) freeze protocol/schema, retention and IAM/egress proposal;
(2) add external guard adapter and tombstone/permit journal with deterministic fault tests;
(3) bind SQL admission/approval/attempt references and integrate fixture disclosure gateway;
(4) bind companion device/command checkpoints to the same action protocol;
(5) provision isolated GCP staging and test identity, retention, egress and outage behavior;
(6) run measured restore drills, publish evidence, then review each tenant capability.
Each step remains unproved until its evidence is recorded. None enables production sends
as part of the current safety-foundation implementation.
