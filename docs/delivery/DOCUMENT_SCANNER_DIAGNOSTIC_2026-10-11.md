# Separate sandbox-command and cleanup observations

Recorded 11 October 2026, Asia/Kolkata. This is a synthetic diagnostic checkpoint,
not scanner, private HTTP, upload or customer-release acceptance.

The earlier strict execution `hirewiz-document-synchronous-do-acceptance-r724t`
remains failed. Its raw SDK stderr was discarded, and the finally cleanup could
replace the first failure. It was not restarted.

A separately reviewed diagnostic preserves that isolation command, dynamic host
IPv4 check, single-file import and strict child-output contract. It reduces SDK
streams in memory to fixed observations and reports `help`, `do` and `delete`
separately. Independent review clears 61 offline controls; root separately replays
56 diagnostic controls and 17 create-wrapper controls. None proves native runtime.

Seven fresh native identity/IAM GETs precede one create-only request for Job
`hirewiz-document-do-failure-diagnostic`, UID
`ff67dfd4-9131-4d21-bcdd-c5fbf63ca58d`. Ready configuration readback matches immutable
scanner image `1fd697ba1ad7870c75068876aff5b9d151e151e8943418bb86072975f762ef98`,
its `eeb53d6` source, sandbox launcher, keyless scanner identity, one task, no retries,
600 seconds, two CPUs and 4 GiB. No application grants or service IAM are added.

One execution request produces `hirewiz-document-do-failure-diagnostic-4wnfk`, UID
`d0c8f28d-4919-46de-90bd-f815e0acd1e3`. Read-only reconciliation of the same handle
finds terminal `NonZeroExitCode`, one failed task and zero successful tasks. The
execution is not restarted. Its creation and completion timestamps are
`2026-10-10T19:03:40.742941Z` and `2026-10-10T19:06:55.724055Z`.

The frozen stdout reader passes 43 author controls and 43 root replays, with no
failures, errors or skips. Ruff passes, and default invocation makes no SDK calls.
One actual Logging read selects this exact execution's fixed JSON stdout only,
between creation and completion plus 30 seconds, with page limit 30, no retries,
20-second SDK and 30-second owned-child budgets. It returns ten validated events
without a next page. Raw stderr, provider text and credentials are not returned.

| Command | Exit | Stdout bytes | Stderr bytes | Fixed hint | Owned launcher stopped |
| --- | ---: | ---: | ---: | --- | --- |
| `help` | 0 | 1,959 | 0 | empty | true |
| `do` | 1 | 0 | 133 | unclassified | true |
| `delete` | 0 | 0 | 0 | empty | true |

The fixed events complete runtime-byte, host-metadata, DNS and host-egress phases.
The isolation phase starts but does not complete. No child isolation booleans are
observed; independent Python entry remains false. This distinguishes the failing
`do` invocation from its successful cleanup command. It does not identify a unique
underlying SDK cause, prove child execution or prove native sandbox absence.
Actual forced cleanup-failure injection has not occurred.

The command and import flags match the documented
[sandbox CLI](https://docs.cloud.google.com/run/docs/reference/sandbox-cli).
The provider also documents automatic sandbox lifecycle logging in
[code execution](https://docs.cloud.google.com/run/docs/code-execution). A later
separately reviewed system-log reader performs one read-only request for this same
terminal execution, exact project/region/Job, and the same timestamp window. It
selects only `run.googleapis.com/varlog/system`, limits the page to 30, uses zero
retries and never follows pagination. Independent review passes 59 synthetic
controls; root replays those controls and the 57 author controls, with zero
failures/skips and no native calls during replay. These overlapping suites are not
claimed as 116 distinct cases.

The one native read returns one unclassified entry, no known public error family
and no next page. Only fixed counts/flags are saved; native messages, unknown values
and provider tokens are discarded. The result is explicitly inconclusive and does
not establish the underlying cause. The SDK can materialize its response before the
256 KiB serialized-size guard; this is not a wire-byte cap. The selected log route
does not establish exhaustive lifecycle coverage or native sandbox absence.

Repeated broader log queries are not the next action. The next private diagnostic
proposal separates baseline absolute-command launch, writable overlay, the existing
single-file archive import and exact Python entry. It remains unexecuted pending
source/limit review and does not replace full scanner acceptance.

The [sanitized evidence](evidence/2026-10-11-document-scanner-diagnostic.json)
retains source, execution and fixed-result hashes. All original failed probes remain
historical evidence. The private service still uses its preceding EDC image; no
promotion follows this diagnostic.

- [x] Separate command and cleanup results in a reviewed synthetic diagnostic.
- [x] Reconcile its one execution to terminal state and read bounded fixed results.
- [x] Read the exact execution's bounded system-log projection; retain its
  inconclusive one-entry result without restarting the execution.
- [ ] Identify and repair the underlying SDK failure.
- [ ] Prove the full real document corpus, isolation, deadlines and cleanup.
- [ ] Prove positive private HTTP and integrated storage/browser acceptance.
- [ ] Promote a matching tested release and enable customer uploads.

Protected browser-pairing/lifecycle integration, monetary migration and customer
activation remain held. Checkout stays closed and analysis dispatch stays paused.
