# Consumed document transition: read-only historical disposition

This repair recognizes one transition that was consumed across several failed
controller runs and then independently read back. It does not retry that
transition. It creates no Build, Job, object claim, service PATCH or heartbeat.
An audit report is inert JSON; only the freshly reconstructed typed disposition
can enter the bounded ordinary-refresh history reader in the same process.

## Exact historical chain

| Run | Artifact | Role |
| --- | --- | --- |
| 40771705772 | 12852818465 | Original failed rescue, 28 exact JSON files |
| 40781707554 | 12858021573 | First continuation, 35 exact JSON files |
| 40788512340 | 12860975525 | Second continuation, 40 exact JSON files |
| 40805300453 | 12868086986 | Third acknowledged continuation, 46 exact JSON files |
| 40811330747 | Exact native run/name/digest lookup | Successful read-only readback, 48 exact JSON files |

The source SHA, artifact ZIP SHA, extracted file-tree SHA and file count for
every row are code-pinned in `infra/gcp/document_consumed_history.py`. Retrieval
independently checks native GitHub run/repository/workflow/main/source/result
metadata and the native artifact digest. The last artifact ID is intentionally
looked up by one exact run/name/digest; no unavailable ID is invented.

All original native-proof and transition bytes, including failure receipts,
must be preserved. Three immutable GCS claim generations and their exact body
hashes must still read back as spent. Their source and native-acceptance hashes
are historical; changing today's maintenance code does not rewrite them.

The final readback correctly said the API was still maintenance-closed. It
cannot alone establish API restoration. Four separately pinned metadata-only
files in `infra/document-processing/consumed-history-api-restore/` bind the
original maintenance plan and completed restore journal. The audit reconstructs
the original request from its immutable predecessor, reads the exact old close
and restore LROs, checks the restored template/traffic and Ready restore revision,
and verifies generation 345. It never requires today's API to still be that old
image, generation or revision. Original retained files/journals remain unchanged.

## Current serving authority is separate

The document source/acceptance and caller `APP_RELEASE` are separate identities.
For each actual API, analysis worker and resume coordinator, the audit requires:

- The original native service UID and one explicitly reviewed source/image/
  native Cloud Build/five-job CI tuple in
  `infra/document-processing/reviewed-application-builds.json`.
- Native Cloud Build Git provenance, exact immutable digest and finite clean
  two-step build/push recipe; exact successful CI at that source with all five
  required jobs; source ancestry in this trusted default-main checkout.
- The reviewed frozen document-ingestion/scanner/native-TeX source bytes at
  that application source. Labels and a saved success boolean are insufficient.
- Every physically active or tagged revision plus the actual latest-created
  revision is Ready and has one identical immutable template. An unserved stale
  `latestReadyRevision` is not substituted for the actual serving revision.
- Fresh native acceptance for the **current** inspector, followed by actual
  caller document URL/image/policy/renderer/font pins and a repeated application
  observation. Historical 581 acceptance stays pinned to the consumed chain;
  later successful refreshes do not depend forever on its retired probe Job.

There are at most 20 physical allocations per caller. The homogeneous bound
refuses any mixed source, image, environment, secret reference or other runtime
template **before** creating a candidate Build/Job. Each original traffic type,
tag and percentage is kept through close/pins/open, with homogeneous aliases
moved together to that phase's proven successor. No tag is silently dropped.
Unknown sources, unproved CI/builds, changed claims or drift stay closed.

The initial registry contains only actual reviewed 8cb application and 7a
coordinator provenance. A future application release needs a separately reviewed
registry entry from its actual successful CI and native Build. A future image,
Build ID or success is never predicted. WIF read denials are operational blockers;
this path does not grant permissions or fall back to a human account.

## Operator sequence

1. Merge/release this implementation only through the ordinary reviewed process.
   Do not dispatch an old rescue/resume mode to fix its consumed history.
2. Review the actual app traffic. If an obsolete private zero-percent rollback
   alias points to a different template/source, stop. Removing it is a separate
   reviewed traffic-only operation with an exact native preimage, one-send
   journal, readback and fresh rollback procedure. Keep prior images/revisions
   and the original completed release journal. This cleanup is not performed by
   the history audit, and the old release plan is not assumed replayable afterward.
3. Use the `audit-consumed-v2-history` workflow mode for a genuine read-only
   native audit. Review its private artifact and refusals. It does not emit a
   monitor heartbeat, so audit success must not be reported as refresh success.
4. The daily ordinary `refresh` workflow retrieves the exact bundle **only when**
   the original 128528 row remains in the bounded three-row history. It then
   reconstructs fresh authority before any candidate Build. When later completed
   refreshes evict that row, old artifacts/LROs/claims are no longer a permanent
   dependency. Unknown other rows remain subject to the existing strict verifier.
5. A successful daily refresh must still complete the existing fresh inspector
   Build/Job/Execution/log acceptance, ten one-send service phases, caller pin
   verification, restored captured admission and owned prior-Job retirement.
   Its existing result/transition journals are authoritative for that new work.
6. Unknown PATCH/claim outcomes remain reconcile-only. No second send is
   authorized by this historical disposition. Preserve the new started/acknowledged
   handles and use an exact reviewed readback; do not rerun a failed refresh blindly.
7. Observe the next natural scheduled refresh and monitor separately. One manual
   audit/monitor success does not establish long-term schedule reliability or a
   seven-day operational acceptance window.

Legacy strict serving checks, rescue modes, serialized claims/plans and one-shot
migration/release journals retain their original defaults and semantics. The
new application context is explicit and initially applies only to the ordinary
V2 definitions refresh, not arbitrary full-source/V3 recovery.

## Local verification and limits

Tests use synthetic provider metadata and transports: artifact/source/digest
tamper, archive traversal, inert reports, exact five-job CI/native Build checks,
physical-vs-stale serving identity, mixed-alias refusal before Build, preserved
tag/type/percent/environment, fresh current-inspector proof after old-Job
retirement, and uncertain PATCH acknowledgement with no replay.

An offline check also verifies genuine retained artifact bytes and the original
API restore files without cloud calls. Neither synthetic tests nor that offline
check qualify the current native state or prove a successful daily refresh.
Native audit, subsequent actual refresh, and natural-schedule observation remain
separate reviewed runtime work.
