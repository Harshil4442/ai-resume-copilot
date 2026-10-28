# Expired document worker predecessor: guarded recovery design

Status: local guarded recovery patch and tests; no production change or release evidence. The V3 successor still needs its own fresh inventory, eleven-case native corpus, exact-source CI and deployment checks. Deploy the updated API gate before using this transition path.

## Observed failure boundary

- The live V2 worker image has scanner definitions beyond the current three-day freshness limit. `processor.scan_original` checks the installed ClamAV header at request time and refuses unavailable or stale definitions before accepting a clean document. The upload path therefore returns an unavailable error instead of using an unscanned document.
- `document_refresh_transition.transition` verifies the current V2 successor **before** closing the API, analysis worker and coordinator admission flags. That verification fails on `document_native_definitions_expired`, so it prevents a fresh V3 successor from replacing the stale worker.
- The previous API route ignored the `RESUME_DIRECT_UPLOAD_ENABLED` flag. The local patch makes legacy `/resume/parse` honor it before reading the upload. This API change must be deployed before invoking the recovery transition. Until then, a stale scanner or mismatched image pin must still make the old route fail closed without persisting a resume or charging an AI operation.
- A previously successful native corpus is historical evidence. Its scanner age cannot authorize current serving or a new promotion after its freshness window.

## Recovery contract

1. **Prove the successor first.** Verify the immutable V3 build identity and source archive, fresh official definitions, exact renderer and font inventory, all eleven native cases, singleton zero-retry Jobs, Cloud Run isolation/IAM, and intact caller configuration. Re-read this proof immediately before every write phase. A failed or unknown native Job is reconciled by its retained identity; it is never re-executed.
2. **Check the predecessor and caller pins without treating expiry as serving authority.** Read the V2 image and policy, run its normal verifier, and permit recovery only when its exact refusal is `document_native_definitions_expired`. Any other error stops before a write. Confirm every caller still points at the observed prior image and policy.
3. **Close admission with fresh V3 authority.** Use the existing etag-fenced `close` PATCH for each of the three callers, preserving the original flags in the exclusive journal and reading back each new revision. Recheck every flag is false before replacing the worker. If a PATCH result is unknown, stop and reconcile that exact operation; never send another PATCH or reopen admission by assumption. The deployed legacy parse route also observes this flag.
4. **Validate the expired predecessor for retirement only.** After all three callers are verified closed, match the current V2 image, policy, build labels, immutable Build, Job/execution identities, exact native receipt and worker configuration. Permit `require_fresh=False` only inside this separate read-only verifier, which returns no `NativeAcceptance` that could authorize serving. Re-read caller pins and record `serving_authority: false` in the private journal. A failure leaves admission closed and stops before the worker patch.
5. **Replace the worker and re-pin callers.** Recheck fresh V3 evidence; patch only the worker image, policy and build labels. Verify the exact new ready revision and native acceptance. Re-pin all three callers to the same image, policy, renderer and font manifest while their admission flags remain false. Read back the full caller set. Reopen only the originally enabled flags once every pin matches and the new worker passes its current proof. Existing in-flight requests are not claimed drained; an old pin must fail closed.
6. **Verify and monitor.** Confirm a real clean upload, infected-file refusal, unavailable-definition refusal, preserving-format DOCX preview, no accidental charge/persistence on scanner unavailability, and user-visible recovery. Verify the alert/refresh schedule and repair path before expanding traffic. Retain the previous revision and native evidence for rollback review, but do not route processing back to an expired image.

## Proposed code boundary

- The local `backend/scripts/document_refresh.py --mode refresh --publish-candidate --retire-expired-v2` route selects the V3 definitions-only builder even while V2 serves. The flag is rejected for other modes and, inside GitHub Actions, unless the event is `workflow_dispatch`. The normal scheduled refresh remains unchanged. The workflow exposes a separate dispatch-only boolean input and appends this CLI flag only for an explicit `refresh` dispatch. Its existing Workload Identity Federation maintenance account performs the Build/Job/PATCH operations; no local owner impersonation or download of a private proof journal is required.
- The local V3 release entrypoint also accepts explicit `--retire-expired-v2`; the normal transition remains unchanged. The recovery mode accepts only V3 successor policy and the exact observed V2 predecessor.
- The local `verify_expired_current_v2_for_retirement(...) -> None` reuses the V2 build/job/execution/config/receipt checks while allowing age solely for the historical receipt. No reusable stale `NativeAcceptance` escapes; `verify_current_successor` and the runtime scanner remain strict.
- In the transition, use the separate retirement check only after the normal predecessor verifier raises exactly `document_native_definitions_expired`. Preserve the current candidate proof requirement before each PATCH. The recovery journal must state that this check cannot authorize serving.
- The deployment command must create a new private transition journal. Any existing journal or unknown write is reconciliation-only. Do not retry a completed or uncertain Job, Build, or PATCH under a new identity simply to bypass this guard.

## Minimum tests before rollout

- Expired but otherwise exact V2 predecessor plus fresh V3 proof closes all three callers, swaps and re-pins the worker, then restores only captured flags; every write is uniquely journaled.
- Changed V2 image/build/labels/receipt, mismatched caller pin, missing V3 case, stale V3 definitions or changed renderer stops before any write.
- Expiry that occurs during the transition stops before the next write and leaves admission closed; an unknown close/worker/pin/open result is read back by the exact operation and never replayed.
- Legacy `/resume/parse` with stale definitions or an image mismatch returns unavailable, saves no resume, and records no AI charge. Direct upload coordinator scan also remains closed during the worker change.
- Release verification of the final live worker and all caller pins is read-only and independent of any saved success marker.

## Operational constraint

The unattended refresh schedule is the existing GitHub Actions `document-refresh.yml` workflow. Its daily V2 runs on 26 and 27 October failed the scanner-definition freshness gate; its monitoring runs then reported the serving image unavailable. The workflow already has single-flight concurrency and the maintenance identity, and will select the V3 path after V3 serves. Verify a successful natural V3 refresh and the configured alert before relying on continuous production availability. A partial V3 journal requires exact manual reconciliation; the next scheduled run must not blindly replay a Build, Job or PATCH.

The explicit expired-V2 route is deliberately excluded from the schedule. Its reviewed dispatch uses a new output directory and fresh nonce, then builds a new V3 definitions-only image and executes fresh native inventory and corpus Jobs once. An already uncertain Build, Job or PATCH still requires exact read-only reconciliation before any further dispatch.
