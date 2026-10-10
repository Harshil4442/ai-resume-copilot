# Direct-upload publication and native scanner checkpoint

Review date: 10 October 2026. Full paid service delivery remains active.

## Current published source, CI and native backend build

Current source `a162890ff50a606f9c23493ea11266d32ad416d6`, tree
`7d9608d221f8cdc1704f802171b1968d7ed36f6e`, passes all five jobs in
[CI run 38057690476, attempt 1](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/38057690476).
The backend passes 3,389 cases with zero failures/skips, including disposable
upgrade 0014/downgrade 0001/reupgrade 0014, OpenAPI generation and drift checks.
Frontend passes 353 units across 27 files, 215 responsive cases and the normal
Next 16.4 production build. Compiler/scanner AMD64 checks, companion, full-history
security/container checks and the connected cold journey also pass. That journey
uses actual Chromium/Next/authentication/API/SQL, finite synthetic external
providers, zero model calls and no employer contact or automatic submission.

Independent review checks the actual source checkout in each job and joins 41
source inputs; root verifies 61 retained artifacts. The sealed
[CI verdict](/tmp/hirewiz-ci-a162-independent-20261010-5930ybyc/independent-verdict.json)
has SHA256 `a1ba2d67aedd6979d9d2da4a0972031dbb6390b4bc6805336dc2036f783fa105`.
This closes corrected-source CI; it does not close production rollout.

Native repository-root Cloud Build
[`46109105-22e2-4800-975a-a86fbd4abf30`](https://console.cloud.google.com/cloud-build/builds;region=us-central1/46109105-22e2-4800-975a-a86fbd4abf30?project=ai-resume-parser-482412)
is SUCCESS, finishing 10 October 2026 at 14:45:08.287113 UTC. Both build/push steps
and resolved native Git source match a162. Registry tag and immutable reference
agree on `us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz@sha256:3d4d7f7a9ada1873949998d91c89b1a0109f4997e89300ecd5398232ec4d3052`.
The independent
[source/build/registry/CI verdict](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-native-build-a162-independent-20261010-e_nh6ww0/independent-native-build-verdict.json)
has SHA256 `f3c99bab8579a0a68fe12abeabd98cf964aef449fc56ea9d56ebdaa1c617ef88`.
No deployment, migration, traffic shift, provider retirement, customer activation,
SLSA level or reproducibility is established. This full backend image is separate
from the seven-input scanner image below; the private diagnostic/Workflow repairs
are not part of a162 or its CI.

## Historical source failure and four-test correction

Source `e353e83dbd86700c979262f6680fb7913d205ac8` contains the direct-upload domain,
default-disabled frontend, scan/cleanup coordinator and original-source downloads.
[Its terminal CI run](https://github.com/Harshil4442/ai-resume-copilot/actions/runs/38054665085)
has four successful jobs and one failed backend job:3385 passing cases and four
current-head assertions expecting 0013 after the source head advances to 0014.
Frontend units 353, responsive cases 215, the normal production build, native AMD64
document engine and cold browser/API/PostgreSQL journey pass. External payment,
feed and employer transports in that journey are synthetic; model calls are zero.

Only four current-head test literals are corrected to `20261010_0014`. Full reverse
byte/AST comparison restores the exact preceding source; historical downgrade
targets, monetary retention assertions and all implementation files are unchanged.
One actual PostgreSQL17 replay of the four affected files passes 51 cases, zero skips,
in 42.96 seconds. Before/after native catalog snapshots are identical, including the
pre-existing unrelated schema. Independent review is clear for source integration.
The preceding run's OpenAPI export was skipped after test failure and remains
unverified in that run. The later a162 full CI independently passes those checks;
the original e353 result remains failure and is not relabeled.

## Actual private scanner stage

The exact seven-file native archive builds image digest
`edc8f5fc3000a40237ed3888c563c5f5ef175008e5ef3c4fb5007976119934e1`.
All seven inputs match published source e353, remain byte-identical in a162,
and match canonical policy
`425fd4b326554d8fee8d2686f423c8e2b499eaaa1bd7858eb69f7759ac526bdf`.
This is an archive/source join, not a native Git-checkout build claim.

Cloud Run service `hirewiz-document-inspector` is Ready, UID
`7014a5ba-faee-4fbf-8272-a437b1de49ad`, revision
`hirewiz-document-inspector-00001-xcf`. Independent native configuration review
verifies internal ingress, IAM invocation checks, fixed image/policy pins, gen2
sandbox launcher, no mounts/secrets/connectors/sidecars, 2 CPU/4 Gi, concurrency1,
90-second request timeout, minimum 0 and maximum 2 revision instances. The separate
provider service-wide maximum is 10. Its new runtime identity has zero user-managed
keys, application project grants or resource invoker bindings. Configuration
readback does not prove sandbox runtime behavior or customer reachability.

## Native failures retained

A single synthetic Job execution, `hirewiz-document-isolation-probe-grvwv`, uses
the same immutable image and identity, one task, no application retry, 600-second
platform timeout and 540-second program deadline. Exact command, eight saved
synthetic fixtures, six runtime source hashes, image/policy and ownership are
bound in native Job/execution receipts. Execution UID
`f5a787c4-640c-431a-9d40-57a802fbcb58` terminates with exit 1 and one fixed failed
JSON result. The generic result cannot identify the failing gate; runtime
acceptance is HOLD. A diagnostic successor must retain the original assertions,
limits and failure instead of treating a retry as evidence.

The first phase-only diagnostic Job `hirewiz-document-isolation-probe-bbk2d`
and the later independently reviewed SDK V2 diagnostic
`hirewiz-document-isolation-probe-r8nc7` preserve the same image, assertions,
fixtures, limits and failure semantics. r8nc7, execution UID
`97d996e2-7b34-4715-a47e-6eb0df9aff32`, terminates FAILED with exit 1. Its fixed
diagnostic identifies `isolation_sandbox_exec`: SDK return code 1, stdout 0 bytes,
timeout=false. Earlier host positive controls and owned cleanup phases complete;
child result checks, child isolation, scanner corpus and actual timeout gates are
not reached. No raw SDK stderr was collected, so the underlying SDK error remains
unknown. The
[terminal independent verdict](/tmp/hirewiz-native-sdk-v2-terminal-independent-20261010-_uzaqfmj/terminal-verdict-hold.json)
has SHA256 `3c05f76ee32f7363932aa107d53cbe90ab2560c34d16c6f8ab541a6a2270ce34`.
The original grvwv/bbk2d evidence is retained; this is still HOLD, not service-instance
inspection or forced-delete-failure coverage.

The later synthetic SDK smoke `hirewiz-document-sdk-smoke-c9zll`, execution UID
`fc169525-9955-4c7f-9bde-c080b339010f`, also terminates FAILED/exit1. Create/delete
return 0; echo, Python and post-delete exec each return 1 with stdout0/stderr78,
classified only as `unclassified`. `followup_missing_hint=false` supplies no native
sandbox-absence proof, and no underlying cause is established. Its exact Job/execution
source, image and runtime identity match the independently sealed terminal verdict
SHA256 `91b003bd84e4a9b4fb2d3f9ec63821fbba026163d2d06051f3e47af63f105c32`.
The [sanitized durable checkpoint](evidence/2026-10-10-native-storage-checkpoint.json)
retains its full probe/service/receipt hashes alongside r8nc7; original failures remain.

The private HTTP probe first discovers three distinct failures: initial API
propagation denial, a local SDK578 converter crash for explicit deployment
`--execution-history-level=none`, and an actual Workflows compiler rejection of
an inline map expression in `list.concat`. The map must be assigned as a workflow
variable before append. For this SDK, omitting that deployment flag retains its
[documented unspecified default](https://docs.cloud.google.com/sdk/gcloud/reference/workflows/deploy).
It does not prove provider execution history is disabled. Call logging remains
`LOG_NONE`; only synthetic inputs belong in these tests.

The reviewed Workflow V4 now compiles as revision 000002-021. Its actual HTTP
execution `8a3287d6-cd62-43d9-87c8-271e538a2835` fails at status_0. After the exact
temporary service-invoker binding is removed and IAM read back empty, the deliberate
final-denial execution `03bac72b-9db0-48db-879a-c691894d506d` also fails at status_0.
The bounded native request log records403 for the first execution and 503 for the
second, not a successful inspection or proven final403 denial. All nine HTTP cases
remain unproved. The [first execution receipt](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-document-native-acceptance-20261010-9gxn6f80/workflow-v4-http-final.json)
and [final-denial receipt](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-document-native-acceptance-20261010-9gxn6f80/workflow-v4-final-denial-final.json)
retain both FAILED outcomes; request-log SHA256 is
`7eab4ba7ad10cbb50661b79f24c3fe46f38a90ed418cf4ad09150e2cf60a8464`.
Workflow compilation, IAM readback and failed HTTP execution are separate scopes.
Owned Job/Workflow resources have not been retired merely because the invoker was removed.

After elapsed propagation and a fresh exact empty service-IAM/native-UID/source join,
the deliberate post-retirement execution
`703ac66b-8ba6-4040-949e-e122981869fb` on V4 revision 000003-6cd SUCCEEDS at
15:27:14 UTC with one actual 403 denial; the bounded collector accepts that one case.
The [terminal denial receipt](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-document-native-acceptance-20261010-9gxn6f80/workflow-v4-post-retirement-denial-final.json)
has SHA256 `76e37b1887f568840666637bacc0692471c02187526f3bebebcb1a8fe7f77244`.
This later evidence proves current denied private invocation only. Both original 403/503
failures remain preserved; positive nine-case HTTP/scanner acceptance and service-instance
isolation stay HOLD.

## Partial storage staging and owned lease removal

The reviewed GCS V2 plan permits new empty-resource staging only. Storage is
partially staged: the quarantine bucket's initial restrictive IAM update removed
the selected owner's convenience metadata/IAM authority. A later exact quarantine-only
20-minute metadata lease permits one raw policy PUT, now acknowledged and followed by
a byte-exact fresh raw GET. The provider reorders the three rows, causing the retained
first-wrapper ordering assertion to refuse; no PUT is repeated. The policy contains
the exact two prior conditional object bindings plus the selected human's four-permission
metadata-only role. Root removes the later temporary project lease in finally and reads
the fresh project policy back. The [root reconciliation receipt](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-gcs-native-stage-root-20261010-syd32w41/administrative-continuity-v2-native/root-quarantine-continuity-reconciliation.json)
records policy-readback SHA256
`8496446e4c9775ea23bbeeb329719d8d87cb6bf6a3f683b030cdafeeb61cecdb`.
The [independent closure verdict](/tmp/hirewiz-q-continuity-native-independent-20261010-f0v3oli1/independent-verdict.json)
confirms the recorded policy and lease closure; its SHA256 is
`1a9f3b6a7189b1b193effc404a2842f78b572af50fc2ce6cfc2ccf0b74847906`.
The retained reconciliation source is separately hash-bound in its addendum. Four actual
selected-human metadata TestIamPermissions GETs then succeed at 15:48:45 UTC, after the
second lease expiry at 15:43:54 UTC. A fresh project policy contains no owned lease and
matches the retirement readback, preserving all 19 unrelated bindings. The
[offline post-expiry receipt audit](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-q-effective-and-clean-input-independent-20261010-qgdze7ox/independent-verdict.json)
has SHA256 `e3bdd5d2f1c62c347c666162996eea4adeeac954753d1c66a99e23c76d3aa547`;
permission-result stdout SHA256 is
`9cbf5512cbef8b77cd7c84a7408a61906348be5a527d5fabad041d2b9e37b9ea`.
This is metadata permission recognition only; no signed-upload, object/browser or
application-actor acceptance follows.

The exact temporary quarantine-only metadata project lease has now been removed
using the fresh policy etag and native ACK plus separate readback. The request
removes exactly one acknowledged lease row from 20 and preserves all 19 unrelated
bindings and other policy fields. The native ACK/readback match and the etag
changes. The offline
[independent receipt verdict](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-q-lease-retirement-independent-20261010-t0_oy0wn/independent-verdict.json)
has SHA256 `5daaf7aeec1097b41f445c25c0fe41c71f872605ecc03a0f77f38931b01a5e30`.
This proves the first retained removal receipt. The later permanent-policy ACK/readback,
second-lease removal and post-expiry permission checks above now have separate narrow
independent receipt verification. Their original failures and readbacks remain preserved.

The owner-preserving clean continuation is independently plan-clear with 62 offline
cases and unchanged original create/settings/CORS inputs. It requires fresh absence,
native birth/full settings, the exact metadata role and an etag-bound new IAM request.
Original owner-omitting step 22 and an etag-free intent template must not be submitted.
Its [sealed verdict](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-clean-continuation-independent-20261010-kax38ctd/evidence/independent-verdict.json)
has SHA256 `70239f6e6971a1884be3c5c8b78db384de0862556ca8664cd301a836b18156d0`.
Original clean creation/settings steps 20–21 now have native ACKs and exact raw birth/full
settings readbacks. Birth generation `1791646407947806117`, project and unchanged
`timeCreated` bind the snapshots; metageneration advances from 1 to 2. The configured
private access controls and exact GET-only CORS match the reviewed inputs. The same
[offline receipt audit](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-q-effective-and-clean-input-independent-20261010-qgdze7ox/independent-verdict.json)
verifies those retained metadata receipts. These are saved CLI/SDK JSON projections,
not asserted literal HTTP response bytes. The narrow UTC helper successor now accepts
exact `Z` or `+00:00` spellings while preserving full birth/current string equality;
all 93 independent offline checks pass with no skips. Its
[sealed source verdict](/var/folders/ck/cyzkgt2d1yx6hzpsdflcg83c0000gn/T/hirewiz-clean-utc-v3-independent-20261010-503c3qcc/evidence/independent-verdict.json)
has SHA256 `034df1f7ae14915a59f312ca0c4c8d7edaa22ef220e4be61ce03210dee35b6da`.
The reviewed root continuation now records one fresh-etag SDK CAS ACK and an exact
fresh native readback of the clean policy's three bindings. Request etag `CAI=` becomes
`CAM=`; bucket metageneration advances from 2 to 3 with identity and all other settings
preserved. The offline native receipt verdict has SHA256
`0c51a199b20950a0b2d08651fac732937be91ecc5e0aa458c0cee7e9925b37dd`.
All four actual selected-human metadata permission GETs then succeed, with stdout
SHA256 `838c59c63baa671fb76b2d6400d43960d5ea7c9509b473fa2090d00f76f32978`.
The [durable sanitized checkpoint](evidence/2026-10-10-native-storage-checkpoint.json)
records exact source/receipt hashes, native identities/settings and permanent binding
projections. Original failures remain preserved. Effective application actors, native
browser CORS and signed objects remain unproved. Original step 22 is not executed;
existing-API signing steps 23–24 and all customer activation remain held.

## Remaining activation gates

- [x] Publish the direct-upload source and independently join image inputs.
- [x] Stage and independently verify native private service configuration.
- [x] Review the four test corrections with actual PostgreSQL retention checks.
- [x] Pass full a162 CI for the corrected source, including OpenAPI verification.
- [x] Build and independently join the exact native a162 backend image and registry metadata.
- [x] Compile Workflow V4; preserve its failed private HTTP/final-denial outcomes.
- [x] Verify one later actual post-retirement 403 private invocation denial; positive HTTP acceptance remains open.
- [x] Remove and read back the exact owned temporary quarantine project lease.
- [ ] Diagnose and pass actual Job isolation and private HTTP receipt acceptance.
- [ ] Retire only owned temporary probes and verify permissions afterward.
- [x] Acknowledge and read back the permanent quarantine metadata-admin policy and remove the later temporary lease.
- [x] Independently verify the later quarantine policy/lease closure and post-expiry four metadata permissions.
- [x] Create clean bucket with unchanged steps 20–21 and verify raw birth/full settings.
- [x] Independently review the narrow UTC helper successor with 93 offline cases; exact identity and grants remain unchanged.
- [x] Verify reviewed clean V3 native CAS ACK/readback, unchanged identity/settings and four selected-human metadata permissions.
- [ ] Verify effective application permissions; browser CORS and signed objects remain open.
- [ ] Deploy the coordinator and required private path; verify signed objects and real browser uploads/downloads.
- [ ] Complete old-consumer/provider fencing, guarded migrations and finite paid-plan rollout.
- [ ] Resolve protected pairing, permitted portal acceptance and all broader requirements.

The storage V2 packet separately passes 79 independent/offline controls and is clear
only for staging new empty resources. Existing-API signing grants and native probes
remain denied before SDK access. Customer upload/coordinator flags, new paid plans,
automatic submission and production migrations remain disabled. Historical production
schema remains 0009. The full worldwide coverage, resume fidelity, load, restore and
performance requirements remain open.

The earlier machine-readable [checkpoint receipt](evidence/2026-10-10-source-ci-native-scanner.json)
is retained as the historical e353/configuration checkpoint. The later sealed
a162 build/CI, diagnostic, HTTP and lease-removal receipts linked above supersede
its pending-status fields; no source, runtime or held pairing files are changed here.
