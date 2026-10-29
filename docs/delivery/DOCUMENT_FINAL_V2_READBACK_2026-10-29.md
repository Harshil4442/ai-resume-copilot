# Read-only reconciliation of the acknowledged final scanner transition

Manual run `40805300453` at `ebee72761f433c43d2e91c9ca61d736631fea8cd`
consumed the third create-only claim and acknowledged **one** coordinator `open`
PATCH. It stopped during service readback with the fixed code
`document_transition_readback_changed`. Its private artifact `12868086986`
has digest
`sha256:2704e3657731002c1ba008a18cd995d14c227d1a89dbc1f13fb4f03af7df02f6`.
It contains exactly 46 JSON files, tree SHA-256
`7e200c774ae0148965d836a3ad5ff184670b05670bb3f1340f17ff1b81b047a3`:
the prior 40 source files, three third-claim markers, two coordinator-open
markers, and the fixed failure receipt. There is no `open.verified` or
completion marker. **Never dispatch `resume-final-v2-transition` again.**

The third claim is present at generation `1793240192529106`. The acknowledged
Cloud Run operation
`e62b0d5e-4326-4317-954c-aa0bf5f78e50` is done without error. A fresh
Cloud Run v2 read showed the coordinator at generation 28, Ready 100% on
`hirewiz-resume-coordinator-d-5436da554bc5-open`, with direct upload enabled
and optional AI disabled. The API remains on the original schema25 emergency
close, generation 344, with both flags disabled. The worker and analysis
service remain on their expected revisions and exact V3 document pins.

The failed controller compared `trafficStatuses[0].revision` only with a fully
qualified revision path. Cloud Run returned the exact short revision name.
The shared controller now recognizes only the short or fully qualified form
of its two known revisions during bounded readback. It still refuses a third
revision, changed template, changed generation or unrelated configuration.

## Exact read-only proof

The manual `reconcile-final-v2-transition` workflow option downloads only
artifact `12868086986` after checking its run, branch, head and digest. The
CLI validates all 46 private files and their exact tree hash **before native
HTTP**. It verifies the original ten-case native acceptance, all three
consumed GCS claims and their immutable media, the original pin and analysis
operations, the coordinator's acknowledged operation and exact request
hashes, and the four current service generations, Ready revisions, flags and
pins. The direct controller transport permits only reviewed GETs; the native
proof routines issue read requests only. This mode has no claim, Build, Job,
PATCH, or restore call. Its failure handler also skips the usual Cloud Logging
heartbeat; it writes only the private fixed-code artifact and CI output.

A successful run retains `readback/complete.json` and `result.json`, both
reporting `document_refresh_final_caller_restoration_observed`, exact source
run/artifact/digest, verified worker and caller pins, restored analysis and
coordinator admission, `read_only_reconciliation=true`, and
`api_maintenance_restore_required=true`. Inspect this artifact and live
readbacks before using the **original** schema25 emergency maintenance plan
and journal to restore API service and schedules. The reconciliation never
restores the API itself. A mismatch or unavailable read leaves the API closed;
there is no mutation to replay.
