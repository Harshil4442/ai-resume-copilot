# Signed-device transport admission candidate

This is a source-only follow-on to the candidate gateway admission fix. It does
not establish the cause of the owner's observed 403 or change an owner pairing,
extension, record, live deployment, browser permission or device approval.

The frozen device source checks a signed five-second issued-at envelope in pure
preflight, performs native identity/origin/key reads, then checks that same expiry
again during nonce consumption. It also constructs the native service before
preflight. The retained source audit has 41 synthetic-clock/local-core cases:
4,999ms observation passes, whereas 5,001ms composition/observation/control or
known-ID origin/key lookup can reject a legitimate request. No native receipt or
owner-cause inference is claimed by those controls.

## Exact scope and authority

The five affected transport operations are `prepare`, `inspect-key`, `status`,
`challenge` and `refresh-challenge`. Each new invocation must still arrive with
its original signed `issued_at_ms` inside the original five-second window. No
wire field, extension package, protocol, origin or permission is added.

Pure pinned configuration/preflight and an opaque local **timing capture** now
precede cold composition. The capture binds exact raw bytes by hash, operation,
origin, issued time, request ID, thread/task and a private construction-owner
slot. It authorizes no nonce, key ownership, command or application action.
Body-contained keys allow bad origin/signature/input refusal before native IO.
For known IDs, the real public key is available only through native reads, so
this timing capture deliberately precedes signature verification.

After composition and a fresh configuration check, the capture binds once to the
exact native instance sharing its private verifier. The native verifier performs
the existing origin and fresh current-key lookup and verifies the actual ECDSA
signature over the exact request. Only successful verification exchanges that
single-use capture for a separate opaque verified admission. Neither a caller
boolean/time, constructed handle, another native/verifier, mismatched body/origin/
operation nor another thread/task can replace those checks. Verification failure
never enables nonce consumption or a command.

The same fixed thirty-second local wall-plus-monotonic deadline starts before
composition and is never reset by binding, key lookup, verification, consumption
or output. Both clocks have nondecreasing high-water checks. This preserves the
existing pairing local clock; it does not claim ADR008 provider-clock authority.
Platform delay before pure entry or clock disagreement still rejects expired
incoming input. No retry, unsigned time hint or longer incoming TTL is added.

## Current state and replay

Nonce consumption remains atomic and immutable, with all existing native control
and fence checks. Known-ID requests reread the current key/origin in that same
nonce transaction and compare it with the key actually used for ECDSA. Missing,
changed, inactive-device or wrong-origin rows refuse rather than reusing an
earlier key lookup as an allow decision. The original core still checks current
subject/device/session, registration, generations, ownership and revocations at
the relevant command; protected key inspection and native ACK/activation rules
remain required.

Consumed transport nonces remain retained through original `issued_at_ms + 5s +
30s`, covering the last possible admitted processing lease. This is replay
retention, not permission to present the old signature at a new entry. A reused
nonce or a concurrent second invocation is refused. Temporary timing/admission
handles are removed at normal/error completion and are not persisted or exported.
The timing capture remains available only for the final local output barrier;
it cannot be exchanged for a second verified admission.

The `create`, `complete` and `refresh` proof paths do not use an issued-at transport
envelope and are outside this change. Their original challenge/nonce/signature,
actual ten-second core proof deadline, current ownership checks and native ACK
requirements remain. The pending pairing remains two minutes, human review and
password reauthentication sixty seconds, and a completed device claim five
minutes. No device/application approval is inherited or synthesized.

## Qualification and release limits

This explicitly changes signed-device freshness from a final-consume deadline
to valid-at-entry plus bounded processing, under the same constraints as the
candidate gateway fix. The local controls use exact methods, synthetic clocks/
registry/delegates and the original local SQLite core. They are not native cloud
custody, production latency, protected-device activation or owner acceptance.
Unknown native outcomes retain the existing no-retry/reconciliation rules.

Changed application source includes `browser_pairing.py`,
`browser_pairing_production.py` and the new `device_transport_admission.py`.
Prospective application SHA/image, CI and release-plan source fingerprints must
qualify all three before deployment. Existing historical/live application,
worker-image/source-policy/native proof pins remain unchanged. The ordinary owned
native HTTP suite and real owner pairing/reauth/device/revoke journey still need
acceptance. No push, deployment, funding or external call is part of this source
candidate.
