# Original-request password reset projection — disabled author stage

This separate source stage extends independently reviewed scoped publication V2. It does
not activate a product flag, create a production factory, close a cohort, alter the two
frozen lifetime/enrollment source modules or wire signing/application transport. Root's
challenge/admit/consume join and candidate-author denial-only logout confirmation are
separate work. Independent review, integration and deployment remain pending.

## Exact construction and scope

Use the same actual original `GcpPasswordLifetimeCoordinator` and
`ScopedCandidateAuthority` instances as enrollment/session activation. Construct
`GcpPasswordCredentialProjection(lifetime, scoped)` only with the exact OPEN ordinary
execution pin matching `scoped.publication.resource.pin.authority.target`; the protected
publication database still differs by name and UID. Inject it explicitly as
`GcpProtectedCandidateLifetimes(scoped, reset=projector)`, then compose
`NativeCandidateLifetimeRetention(lifetime, protected)` and genuine `CandidateAccountService`.
Omitting `reset` still reports unavailable. Every existing production constructor remains
unavailable. No caller-supplied client, lane, projection callback, PIN, epoch, credential
value or raw model/status can substitute for an original owned ACK.

Protected grant/tombstone/BEGIN fields are unchanged. The distinct
`scoped_password_reset` command adds a server-generated request UUID, distinct operation
and event identities, original retained session and subject provenance, exact old/new
credential SHA256 commitments and complete fixed Native before/after/event bytes. It is
retained in the existing `scoped_admission` publication census, not in an unregistered
fourth intent feed. Existing ScopedDenial and lifetime V2 canonical bytes are unchanged.

## Request and acknowledgment ordering

1. The actual backend validates the retained context against the current SQL and protected
   six-field credential revision, freshly verifies the supplied current password against
   the exact salted SQL hash, and rechecks that hash commitment. Password hashing/verification
   remains deterministic, with the independently repaired candidate R2 crypto service.
2. A newly salted new hash stays only in request/SQL process memory. Its exact SHA256 and
   new generation bind the request. Plaintext passwords and encoded salted hashes are
   never added to Native, GCS, manifests, errors or logs by this adapter.
3. Actual ordinary Native reads verify the exact immutable candidate/account/subject owner,
   principal, original registered session, old credential commitment, native generation,
   OPEN control, monotonic clock and current global event cut. The coordinator privately
   retains the complete canonical plan bytes. Every IO/Commit attempt uses those private
   bytes; detached plans, mutations and changed cuts cannot retarget them.
4. The protected transaction registers that complete intent together with the old-generation
   pending denial. This is the deny linearization point. A second protected transaction
   verifies that same request and advances the protected generation/hash history, with its
   own original definite ACK. Every old session for this candidate becomes ineligible;
   unrelated candidates/sessions and the root OPEN state remain usable. The new
   reset-derived credential revision cannot authorize a new session or BEGIN until its
   exact retained completion evidence exists. Only the original opaque owning denial/
   Native ACK projection and completion checks may use the internal owner exception;
   challenge/admit/consume must use default current-subject/session checks.
5. Only the original identity-bound protected denial ACK can initiate this Native projection.
   The complete already-registered intent is create-only uploaded and exact-generation
   verified through Storage SDK before ordinary Native Commit. A paused upload cannot become
   an uncensused authority object. The projection rechecks the full captured native cut
   and atomically changes the subject generation, auth floor and exact credential commitment,
   with one fixed chained event plus full retained native effects/operation receipt.
6. Only the original definite Native Commit returns its private one-shot ACK. Status and
   repeat execution can report historical COMMITTED evidence but never reconstruct that ACK.
   Completion independently consumes it outside DB transactions, verifies the full Native/GCS
   evidence plus current exact native owner/generation/hash, then retains immutable
   `v3_reset_completions` full evidence in the protected authority with its own definite ACK.
   The full Native/GCS cut census includes and validates every such completion.
7. Only that original definite protected completion returns true to the actual service.
   The service then locks/rechecks the exact old SQL identity/hash/generation, writes the
   SAME new salted hash commitment and generation, and commits. No SDK executes inside a
   SQL transaction. Successful HTTP returns `CHANGED_REAUTHENTICATION_REQUIRED`; old JWTs
   and old passwords fail, and new-password login issues a fresh generation-bound session.

## Fault and race semantics

- Two resets race on the same old-generation pending key, so only one can retain its owning
  denial and advance/project generation +1. A fresh explicit request never adopts an old
  pending operation. Actual simultaneous SQLite/PostgreSQL requests prove one winning new
  hash and generation. Other candidates remain usable and no global cohort closure occurs.
- A persisted or unpersisted UNKNOWN final protected high-water/native projection/completion
  leaves the already definite pending denial effective. No detached ACK, SQL update or
  reset-success response is produced. Reconciliation/repair after lost ACK remains separate
  status-only recovery work; this adapter never replays a detached COMMITTED receipt as grant.
- If INITIAL protected registration did not persist, there is no retained pending fence,
  Native/SQL change or successful revocation. That request is honestly unachieved/unavailable;
  existing eligibility is not falsely described as revoked. If it persisted without ACK,
  the pending fence denies old scope but no projection owner is adopted.
- Storage retention/witness failure after definite denial withholds Native projection.
  A changed Native global head refuses the original fixed plan instead of recapturing an
  event cut. This can sacrifice availability for the requesting candidate; bounded status-only
  recovery remains needed. It never disables an unrelated cohort.
- Restoring old SQL hashes/generations cannot activate fresh old-password login or old
  sessions because actual protected credential history is newer. Native-current rollback
  with retained event/effects permits historical status only and fails new completion.
  UID is still an identity pin, not rollback evidence; destructive full-history rollback
  remains excluded until actual custody/checkpoint controls are proved.
- SQL commit failure after both authority effects does not undo revocation or create a
  recovery grant. No success is inferred from absent SQL projection. Existing sensitive
  authentication boundaries suppress raw credentials/provider/SQL exception disclosures.

## Author evidence and exact remaining work

The affected local run has 33 new reset cases plus 41 existing scoped publication/authority/
SQL regressions. Tests use actual anonymous Firestore Native SDK and real SQLite/PostgreSQL
service state, actual Storage SDK on one synthetic generation-aware HTTP object store,
actual authenticated FastAPI/JWT flows, and thread-local instrumentation around actual
Native begin/Storage IO to prove they occur outside SQL transactions. Positive tests use
no seeded candidate subject, fake reader result, fake final activation or fake reset flag.
Initial author failure (18/19 passed) was a probe calling a challenge reader without its
required proof; source, patch and JUnit are preserved. The corrected probe calls the actual
Native verified-session allocator and confirms a stale generation cannot issue a session.

- [x] Actual happy-path protected + Native + SQL password update and generation-2 login.
- [x] Exact salted-hash SHA256 equality in SQL, protected subject/history and Native revision.
- [x] Persisted/unpersisted UNKNOWN phase refusal; original ACK identity/one-shot replay.
- [x] Concurrent real SQL requests single winner and unrelated-candidate availability.
- [x] Old-hash SQL restore refusal, fixed-cut and mutable/detached-plan refusal.
- [x] Actual authenticated FastAPI password change, old JWT/password rejection, new login.
- [x] Complete census/export includes full reset intent and protected completion evidence.
- [ ] Independent exact review and current root source integration.
- [ ] Actual browser/NextAuth password-change UX and cold deployed process proof.
- [ ] Root-owned genuine challenge/admit/consume guards in SAME protected authority transaction.
- [ ] Actual production resource constructor, mediated writer/old-writer exclusion, restore/
  import/delete/overwrite custody, retention lock and independently retained rollback witness.
- [ ] Status-only lost-ACK/SQL projection recovery, independent full restore/replay/reopen,
  cold owner/ACK recovery and retained operation/ACK map capacity.
- [ ] Partitioned execution V3: ordinary Native lifetime still uses its original global
  execution head. This reset stage avoids whole-cohort closure but does not solve that
  head's contention, or provide production load/recovery-time guarantees.
- [ ] Exact combined Linux CI, schema acceptance, staging, promotion and deployment.

The tests run only on owned local emulator `58881` (pinned image, heap1536m/container2GiB)
and uniquely owned SQL schemas on `127.0.0.1:55433/hirewiz_admission_test`. Prior58880/58879/
58877 evidence and all frozen V1/V2 sources remain unchanged. No cloud, root, credentials,
real KMS, employer action, shared-schema cleanup or production mutation occurred.
