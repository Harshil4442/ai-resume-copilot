# Private local missing-key recovery prototype

This is an isolated, reviewed prototype. The official 0.8.4 manifest, download,
package inventory and current application release are unchanged. Do not describe
an unpacked local diagnostic installation as the unchanged official bundle.

The entry condition is an actual existing popup status failure with the trusted
`MISSING-RETAINED` device phase. Key shape, signature, database or checkpoint
errors do not authorize recovery. Only the exact existing popup sender can
request its bounded review. The review lasts five minutes and is single use.

The owner must review the UI and personally click the confirmed commit: it creates
a fresh authentication credential. Local automation must stop before that click
on the owner's browser. Offline synthetic profiles may exercise the flow in tests.
Starting a connection and approving its new fingerprints remain separate actions.

The original `device` IDB slot is never replaced or deleted. Original Chrome
`pairing_checkpoint`, `assistance_checkpoint` and `native_application_evidence`
values remain untouched. Their exact bounded snapshot is retained in one IDB
journal, with a 384 KiB archive limit. Existing unknown applications and evidence
remain unresolved. No old approval, device ID, application ID or attempt is copied
into the fresh operational namespace. Saved UUID buttons only open the existing
HireWiz device review/revocation page; they never prove or perform revocation.

An atomic IDB create-only reservation checks that the legacy key is still missing
and no previous recovery exists. It saves `pending` before generating a key. Only
then does the explicit commit generate a nonextractable P-256 key, stored in a
new `device-recovery:<UUID>` slot. Atomic activation rechecks the reservation and
legacy key absence. Final IDB and Chrome snapshot checks precede success. All
three new operational stores use `recovery:<UUID>:<original-name>` keys.

Chrome storage has no native compare-and-swap. All product operations are fenced
by the recovery journal and the unchanged old snapshots; recovery excludes running
operations while ordinary Pause/cancel stays concurrent. API, field/write and
upload callback boundaries recheck the pinned context, so cached old engines
cannot cross into a recovered identity. These guards do not claim to defend
against arbitrary browser-profile tampering by an external process.

There is one recovery per retained journal. Interruption before activation stays
`pending`; detected drift latches `blocked`. Inspection may write only that
protective journal block and never creates, repairs, retries or resumes an identity.
No automatic rollback, retry, second recovery or historical-application replay is
implemented. This limitation is shown before confirmation. Keys and original
records survive a protective block. A lost success acknowledgement can be checked
through inspection and the next normal status read; it cannot generate another key.

No backend/schema/API/origin/scope, password, permission, AI or cloud changes are
part of this prototype. No connection, employer action or revocation is sent by
the recovery commit itself.

Focused commands (from `browser-companion`):

```sh
node --test tests/lost-key-recovery.test.js tests/startup-diagnostics.test.js tests/device-phase-diagnostics.test.js
node --test --test-concurrency=1 tests/lost-key-recovery-browser.test.js
npm test
```

The browser tests create disposable owned extension IDs and offline profiles.
They never load the owner's installed extension or inspect an existing profile.
