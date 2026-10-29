# CI PostgreSQL lock diagnostics

The backend pytest step opts into a CI-only diagnostic plugin. Application code,
financial assertions, the fixture's four-second lock deadline and ten-second
statement deadline remain unchanged. Diagnostics do not authorize a CI rerun or
release. The repeated lock failures remain unexplained until the actual holder
evidence identifies their cause.

The plugin requires the Linux GitHub Actions backend job, an exact service
container ID, the pinned PostgreSQL 17 service image, its published loopback test
port and the disposable admission database. The socket's PostgreSQL cluster
identifier must match the identifier read inside that exact container before any
configuration changes. It never accepts a production hostname or database.

Only that disposable server enables lock-wait logging and a 100 ms deadlock-report
threshold. SQL statement logging and parameter logging are suppressed. Original
diagnostic settings are restored and the observer is closed at session teardown.
The observer alone has a 250 ms statement budget and 100 ms lock budget; it does not
change test or application connection budgets.

At most four samples per second retain hashed test identities, PostgreSQL PIDs,
blocker PIDs, wait events, transaction ages and hashed lock identities. A bounded
ring also includes CPU counters, load, memory and process resource summaries.
No SQL text, query parameters, connection URLs, credentials, process command
lines, process names or environment values are retained. Numeric server-side
holder messages preserve evidence even if Python scheduling stalls the observer.
The first failing test freezes its recent sampling window so later tests cannot
evict the relevant lock state before session teardown.

On failure only, the filtered payload is capped at 128 KiB and encrypted using an
ephemeral AES-256-GCM key wrapped by the committed RSA public key. The envelope is
capped at 192 KiB. Its first filesystem write is encrypted, mode 0600 in an owned
mode 0700 runner directory. Only the encrypted file is uploaded, for three days.
This matters because the repository is public. The decryption key is held only
in the operator's private evidence directory; it is never committed, sent to CI
or used by an application. This is diagnostic encryption, not production
credential rotation.

Encryption or diagnostic failure cannot change pytest's financial result and
cannot fall back to uploading plaintext. A fixed diagnostic status message makes
unavailable evidence explicit. The immutable public-key fingerprint is included
in each envelope so the operator can select and verify the matching local key.
