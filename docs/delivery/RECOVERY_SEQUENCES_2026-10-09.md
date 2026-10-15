# Disabled multi-field browser recovery core

Status: **exact-commit CI passed; source included in the verified production image, with
production execution still disabled**. Dates in supporting process evidence use UTC. This adds
no HTTP route, browser execution, production authority, upload, final submission or charge.
The serving component identities remain in [the current release report](RECOVERY_AND_CATALOG_RELEASE_2026-10-09.md).

## Behavior

The previous [one-fill foundation](RECOVERY_CORE_2026-10-08.md) permanently claims a
candidate/employer/tenant/opening when a disclosure may begin. V2 keeps that claim and
lets only its owning attempt progress through **one to eight static reviewed fields**.
A new device, epoch, resume package or approval cannot adopt or erase a prior claim.
A v1 disclosure cannot be upgraded into a v2 sequence. Longer or changing forms require
manual handoff until a separate reviewed contract supports them.

The immutable manifest binds the candidate identity, current authority/epoch, admission,
policy/price, exact resume hash/generation/descriptor, employer origin and URL, tab/main
frame/document, adapter/form version, review acknowledgement and ordered field/value/
expected-before hashes. Supported actions are text/email/textarea/select/checkbox fills.
There is no upload or submit action. Digests declared by a client do not establish real
file bytes, current DOM or reviewed content; a trusted integration must verify those.

1. Seal the exact draft, then consume a fresh candidate challenge for its complete review.
2. Prepare the exact next field without granting permission to act.
3. Atomically begin that field, preserve the permanent opening claim and journal event,
   then return a short may-act response only to the winning caller. Every begin checks
   current device, candidate approval, grant, epoch, revocation and the original deadline.
4. Record LOCAL_FILLED only with the winning response nonce and exact observation.
   A one-use continuation nonce lets the owning attempt request its next field; it never
   authorizes disclosure by itself. The next begin atomically consumes that acknowledgement.
5. Unknown outcome, cancellation, revocation, expiry or a closed epoch stops progression.
   Late observations can retain facts without unlocking another action. A lost begin or
   outcome response is never reconstructed as permission or continuation on restart.

Candidate and registered operator approval revocation use append-only tombstones. A
current owner can revoke after device/subject context stops or its candidate key rotates;
an inactive candidate cannot impersonate an operator. Cancellation cannot recall a fill
or employer autosave that already began. LOCAL_SEQUENCE_FILLED is not an employer receipt,
verified application, automatic submission or financial settlement.

## Local evidence

[Focused evidence and source hashes](evidence/2026-10-09-recovery-sequences-local.json)
record **222 passing cases with no failures/skips**: 85 preserved v1 cases, 122 new v2
unit cases and 15 spawned process/race/crash/restore cases. Five original v1 core/test
files are unchanged. The sole original fixture edit adds local event/projection checking;
new code and tests are isolated in sequence files. Ruff passed; Mypy passed nine scoped files.

Every v2 process case uses actual spawned processes. Two cases kill a process after its
durable commit and before its response. Competing permits/attempts, duplicate outcomes,
cancel/begin orderings and application-only backup/restore retain the claim and refuse
reissued decisions. Retained journal verification checks exact canonical projected
attempts, steps, permits, challenges, approvals, cancellations and claims. Selectively
restoring a cursor, continuation or approval cannot authorize progression.

[Independent Node/Python vectors](evidence/2026-10-09-recovery-sequences-canonical.json)
match the full manifest, sequence/review/context, four step digests and seven typed values.
V2 wire numbers are strict JS-safe integers; storage generations are canonical decimal
strings. Exact Unicode scalar strings are preserved without normalization. Booleans,
numeric strings, floats and lone surrogates cannot alias another typed value. Inherited
Python-only artifact tombstones still use integer revisions; those records are not v2
wire/canonical vectors and need an explicit representation at production integration.

Root verified the frozen incremental patch and all six resulting source hashes. An
independent reviewer read all changed contracts/services/fixtures/tests and found no
confirmed defect within this disabled local scope. Their review is not an additional
suite execution. Whole merged local verification passed 780 backend tests without skips,
71 frontend unit tests, Ruff, Mypy for 58 files, six prompt evaluations, compilation,
production frontend build and generated-contract drift checks. All four remote jobs passed
for `6f38020`, with 780 backend, 71 frontend unit, 190 browser, 56 companion protocol and
14 Chromium MV3 cases. Controlled GCP/Vercel rollout is separately recorded in the current
release report; inclusion in an image supplies no production action authority. See the
[catalog diagnostics](CATALOG_TIMING_2026-10-09.md)
for the independent instrumentation scope and local setup corrections.

## Remaining boundaries

`SequenceGuard()` defaults to the unavailable production store. There is no SQL/Redis/
SQLite environment fallback. The separate SQLite authority is a test fixture, whose full
journal scan is not a scalable production hot path. Local application-only restore proof
does not detect rollback of the authority file itself or establish cloud journal retention,
Firestore/GCS/IAM, authentication, pairing, physical egress fencing or compromised-device
safety. These limits remain required production gates.

- [x] Implement the disabled bounded sequence core and focused local process proofs.
- [x] Preserve v1 source/test boundaries and independent cross-runtime vectors.
- [x] Integrate the exact frozen six-file incremental patch and review its limited scope.
- [x] Pass the whole merged local backend/frontend/contract/static/build checks.
- [x] Pass all four exact-commit remote CI jobs for `6f38020`.
- [ ] Implement independently retained production authority and verified restore barriers.
- [ ] Verify candidate/device enrollment and current possession against that authority.
- [ ] Integrate exact sealed packages, actual DOM/value validation and permitted adapters.
- [ ] Verify real browser lifecycle, accessible review and honest fill-only/manual labels.
- [ ] Complete staging/security/retention acceptance before a limited assisted cohort.

See [browser-first requirements](INITIAL_BROWSER_ASSISTANCE_2026-10-08.md),
[the multi-step design](BROWSER_MULTISTEP_CORE_PLAN_2026-10-08.md), and
[the production adapter review](PRODUCTION_RECOVERY_ADAPTER_REVIEW_2026-10-08.md).
