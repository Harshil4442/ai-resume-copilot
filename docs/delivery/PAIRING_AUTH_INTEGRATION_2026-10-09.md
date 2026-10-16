# Pairing admission, recent password authentication and KMS integration

The reviewed source is integrated after `49abdeb06a987e23b9484cb3fd6437a2ac6d5b73`.
Production continues to serve `e7d8f2b` and schema `20261008_0009`; this stage does
not enable browser pairing, disclose a resume or submit an application.

## Integrated behavior

Pairing execution now requires a fresh acknowledged original intent, a fresh
protected attempt marker and its own acknowledged native claim before calling
the existing pairing lifecycle. Existing or uncertain admissions provide status
only. Native retries require the same exact claim and fresh checks. An unknown
first intent write that truly retained nothing has made no marker, native mutation
or signing call; the source does not claim that absent history permanently
consumes an operation.

The recent-password service reads current credentials through an owned native
SQLite/psycopg cursor, avoiding SQLAlchemy credential-row logging even when DEBUG
changes during the query. It checks independently registered account/session
lifetimes and returns redacted confirmation/revocation results. Passwords,
password hashes and signed assertions are not browser responses or journal data.
Its local dispatch adapter remains a development adapter; the native dispatch,
protected lifetime writers and challenge-scoped admission before signing are
the next integration work.

The exact-version KMS signing boundary uses the reviewed native protobuf RPC
bindings rather than the generated logging interceptor. It retains explicit
credentials, native deadlines, disabled retries, public-key and CRC checks,
canonical pairing domains and post-sign lifetime checks. The runtime dependency
is now `google-cloud-kms==3.18.0` in the canonical lock/export. No private signing
key or automatic credential fallback was added.

## Verification

The combined default backend suite passes **1,459 cases with no failures, errors
or skips**. The actual CI type selection found one password-hash narrowing error
missed by the isolated import context. A narrow qualification preserves the
strict usable-hash and dummy-hash behavior; all **71 affected cases**, including
three disposable PostgreSQL cases, pass again after that repair. Full-CI Mypy
passes on **80 source files**. Scoped Ruff, compilation, six prompt evaluations
and the OpenAPI drift check pass.

The earlier exact `49abdeb` release passed all four remote CI jobs in run
`37856297492`; those checks do not cover this subsequent integration. The
[source proof](evidence/2026-10-09-pairing-auth-integration.json) records frozen
patches, repaired source identities and the separate test checkpoints. CI now
provides the dedicated localhost password database URL and requires its tests
instead of silently omitting them when configuration is missing.

Independent review reran 213 KMS cases and reproduced the original DEBUG
disclosure conditions without disclosure after the repair. A separate native
reader review found no remaining issue in the repaired credential IO boundary.
Admission review added 22 adversarial actual-emulator cases for malformed fresh
acknowledgements and same-ID epoch/incarnation replay. These are local proofs;
actual cloud identity, IAM, retention and restore behavior remain unproved.

## Remaining release work

Connect the native candidate read/dispatch and protected account/session lifecycle,
then wire authenticated API/BFF/browser transport. Establish actual cloud resource
identity, KMS permissions and independently protected restore checks before
enabling pairing. The complete browser journey and Razorpay portal acceptance
remain required.

A separate actual deletion review confirmed that erasing an account currently
erases retained unknown model-cost holds. The monetary rollout must preserve
minimal independent financial facts while deleting customer inputs and support
late trusted settlement without resurrecting an account. That repair is in
development; the monetary schema is not promoted. Neither this integration nor
the existing zero-traffic compatibility candidates retires legacy writers.
