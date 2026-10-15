# Private candidate ingress native composition

This source is usable after legitimate operator provision. It is disabled by
default and has not been activated or exercised against Google Cloud. No replay
database, IAM policy, control root, custody evidence, restore provenance, clock
witness, signing secret or candidate identity was created by this change.

`production_candidate_ingress()` performs bounded structural validation only.
It creates no SDK client, resolves no credentials, opens no SQL connection and
creates no native resource. `authorize()` composes the installed Firestore,
Firestore Admin and Storage SDKs over their default authenticated TLS transports.
It refuses emulator environment overrides. Every invocation has its own replay
object, original acknowledgement owner and trusted-clock environment; concurrent
calls never share a mutable clock anchor. Invalid physical input or MAC is
refused before clients are constructed; a valid MAC still requires all fresh
native and time checks. Invocation-owned transports are closed on completion.

## Required exact configuration

Set `CANDIDATE_INGRESS_NATIVE_ENABLED=true` only after independent provision.
`DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true` remains required. Supply
`CANDIDATE_INGRESS_NATIVE_CONFIG` as strict JSON, at most 32 KiB, with these fields:

- `execution_witness_pin`, `execution_witness_body`, `publication_resource` and
  positive `epoch_generation`: exactly the current
  `CandidateNativeConfiguration` used by enabled candidate account lifecycle.
- `resource`: the complete `IngressResource`, including the third independent
  database's actual `RegistryPin`, two ordered action registry pins, exact HTTPS
  website origin, key ID, legitimate operator custody UUID, positive restore
  generation and clock witness UUID. The action pins must equal the execution
  witness registry and protected publication registry, respectively. All three
  database names and immutable UIDs must be distinct.

`CANDIDATE_ACCOUNT_LIFECYCLE_ENABLED=true` and an exact
`CANDIDATE_ACCOUNT_NATIVE_CONFIG` are also required. Any configured submit native
authority must agree on the shared fields through the account configuration's
existing validation. Missing, extra, duplicate, nonfinite, changed or conflicting
configuration fails closed. A previously constructed composition cannot be used
after its configuration, feature flag or transport key changes.

`CANDIDATE_AUTH_TRANSPORT_SECRET` is the dedicated 32-byte HMAC key encoded as 64
lowercase hexadecimal characters. `CANDIDATE_AUTH_TRANSPORT_KEY_ID` must exactly
match `resource.key_id`. The backend refuses reuse of its configured ordinary
auth, NextAuth, browser pairing gateway or assistance signing secret. The website
uses the same dedicated transport key and key ID in its existing server-only
signer. Never place these secrets in public frontend environment variables,
browser bundles, logs, committed configuration examples or replay records.

## Provision and runtime privileges

The operator must independently retain evidence for the third database UID,
custody UUID, no-reset/restore controls, current action pins, key separation,
clock witness, retention and exact runtime IAM. Hashes and UUIDs supplied by a
browser or copied from ordinary SQL do not establish that evidence.

The operator alone creates the exact canonical `IngressControl(resource=...,
state="OPEN")` root at the path derived by
`BufferedTransaction.path("candidate_auth_ingress_control", "root")`. The native
body envelope is `{namespace, key, value}` in the existing canonical format. The
runtime never creates, repairs, adopts or opens this root. It needs read access
to current execution controls, protected publication controls/resource witnesses,
replay root and replay rows, and create-only replay-row writes. It must not have
operator root update, replay update/delete, TTL/purge, database restore/delete or
IAM administration privileges. Existing execution/protected authority resource
and emergency closure checks are reused; ingress never adds rows to their
publication journals.

Replay records retain the existing keyed body and Authorization commitments,
complete safe assertion and exact resource only. They have no runtime TTL or
purge path. Any legitimate replay database reset or restore must use the reviewed
operator closure/restore procedure, establish a new current resource/control and
restore generation, and retain independent provenance before reactivation.
This implementation does not discover or grant those operator permissions.

## Fresh checks and provider time

Before signature freshness is evaluated, and before every replay transaction
attempt, the environment freshly checks the execution `GcsWitnessFence`, exact
execution OPEN and pairing epoch controls, protected publication resource and
emergency-close absence, exact protected OPEN root, replay database's immutable
UID, and exact replay OPEN root/resource. SDK calls happen outside replay native
transactions. The replay transaction independently reads the root and request
UUID absence before its sole create-only row write. Only its original definite
Commit acknowledgement can authorize this invocation. Unknown commits and
existing replay rows never become permission through a read or reconstructed ACK.

For time, the environment issues a fresh `BatchGetDocuments` for that exact
operator root without a consistency selector. Firestore documents that omission
as strong consistency and returns the read timestamp in the response.
See the [official Firestore RPC contract](https://docs.cloud.google.com/firestore/docs/reference/rpc/google.firestore.v1#batchgetdocumentsrequest).
The code requires exactly one found root, canonical bytes, a well-formed read
timestamp, and control update time no later than read time. The read must complete
within 500 ms. It derives outward UTC bounds from provider time and local
monotonic start/end points; issuance must precede the lower bound and expiry
must follow the upper bound. Consecutive samples must fit the previous projected
interval. This interval construction is an implementation inference from the
provider's documented read-time semantics, not an additional Google clock SLA.

Inside native transaction callbacks, `now_ms()` performs only bounded monotonic
projection. It refuses an anchor at least two seconds old, backwards monotonic
time or an invocation at least five seconds old. It never makes network calls,
consults SQL, caches wall-clock UTC across requests, accepts an old read selector,
or falls back to the host's adjustable wall clock. Clock uncertainty, slow IO or
expiry can deny a legitimate request; they never extend its five-second proof.

## Local verification and remaining evidence

The focused suite uses the actual installed generated SDK request/response and
retry wrappers with all transports intercepted. It tests structural no-IO
factory behavior, exact create-only retention, original ACK replay refusal,
unknown commits before/after persistence, changed UID/resources/OPEN/epoch/custody
and clock witness, absent/slow/expired/future clock evidence, changed sampling
bounds, stale projections, definite-abort refresh, configuration revocation and
concurrent independent anchors with one replay winner. ADC and actual cloud calls
are forbidden in these tests. Storage and metadata checks assert they never enter
their invocation's native transaction.

These checks establish source and request contracts. They do not establish
production IAM, actual durable replay behavior, restore safety, custody evidence,
operator provision, cloud latency or deployed activation. Root owns that future
provision and activation after the frozen F7 continuation.
