# Fresh Commit acknowledgements for the private ingress clock

Status: implemented locally; deployed clock and enrollment acceptance pending.

Two successive staged availability requests returned provider-derived lower time
bounds that moved backwards by 19,725 ms. Both responses passed the existing
signature, current custody and bounded clock-read checks. A Firestore document's
`readTime` identifies its read snapshot; strong consistency does not establish
that the snapshot timestamp is wall-clock UTC at HTTP processing time. It cannot
anchor the five-second private transport assertion. This replaces that clock
source while retaining the synchronization protocol in ADR-008.

Keep the existing unmodified control document read, exact bytes, database UID,
custody, publication and epoch checks. After those succeed, send one direct
Firestore Commit to the pinned ingress database. Its sole write replaces a
document in the separate `candidate_ingress_clock_probes` collection with a fresh
random nonce and an `observed_at` REQUEST_TIME server transform. The document ID
is the first two lowercase hexadecimal nonce characters: exactly 256 possible
shards. This collection contains only disposable operational clock metadata;
authority records, replay rows and candidate content are never overwritten.
Concurrent shard collisions do not matter because only the caller's original
Commit acknowledgement supplies its clock observation.

Firestore defines `commitTime` as the time the commit occurred. REQUEST_TIME
identifies when the server processed the request, with millisecond precision.
The response lists one result per write and one result per transform in order.
Use the precise Commit timestamp as the UTC anchor; require exactly one write
result with update time and one timestamp transform. Validate timestamp ranges,
millisecond transform precision and their agreement within the bounded RPC,
allowing less than one millisecond for transform rounding. The changing nonce
ensures this is a new update rather than an unchanged document timestamp.
See the [Commit contract](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/projects.databases.documents/commit),
[server transform contract](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/Write)
and [write result contract](https://docs.cloud.google.com/firestore/docs/reference/rest/v1/WriteResult).

Measure request start and acknowledgement completion with the local monotonic
clock and retain the existing outward-rounded UTC interval. Preserve the 500 ms
RPC bound, two-second anchor age, five-second overall operation bound, exact
five-second assertion lifetime and all replay/physical-input guards. Snapshot
timestamps still validate the control response's internal consistency, but never
set the UTC anchor. Each custody check obtains its own original clock Commit
acknowledgement; a normal successful ingress performs two such writes. Existing
bounded definite-abort replay attempts can perform additional checks.

Disable SDK retries for the direct clock Commit. Missing, malformed, late or
unknown acknowledgements fail closed. Do not read a probe document to reconstruct
an acknowledgement, retry an uncertain Commit, or reuse an observation from a
different invocation. These metadata writes use existing database write
authority; no new service, IAM grant, TTL policy or unbounded record retention is
introduced. Signed probes can still be unavailable, and their availability is
not an authorization cache or permission to retry a business POST.

Focused intercepted-SDK tests verify the exact fixed-shard write/transform shape,
fresh nonces, bounded storage and absence of candidate input. They demonstrate
that backwards snapshot read times do not move the Commit clock, and that
missing, malformed, late, aborted and unknown Commit acknowledgements cannot
admit a replay. Real deployed acceptance remains a separate check.
