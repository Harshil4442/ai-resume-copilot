# Recovering the prepared source owner

The first F2 cutover committed the additive `NOLOGIN` source-owner role, then
stopped before provider isolation or customer-data transfer. Native reconciliation
identified role OID `2080768`; the original database owner, schema and customer
data remain in place. A repeated full execution would try to create that role
again. The release operator now supports one explicit, narrowly bound continuation
from this first phase. This is operational recovery work; it does not close a
development or deployment checkpoint.

## Explicit selection and preserved evidence

The ordinary configuration and path remain unchanged. Recovery adds the optional
`prepared_owner_resume` field to the separately reviewed, exact-hash configuration.
It must contain all five fields below with their exact committed values:

| Field | Required value |
| --- | --- |
| `owner_oid` | `2080768` |
| `prior_nonce` | `e3246e0ad682494ea4f7168fcec8814c` |
| `prior_release` | `371b3c423c85529d00e8180bef66e7b0328c1540` |
| `prior_operator_image` | `us-central1-docker.pkg.dev/ai-resume-parser-482412/cloud-run-source-deploy/hirewiz-release-operator@sha256:ae4cc858febb8844e368e645ac8e3a736dd45c991377de3749ef6085c1204db3` |
| `prior_config_sha256` | `b81f376536eb2499cb0657b83099b6393d9f5c5f1d70a1924dee8c0ef381ff38` |

The typed instruction rejects other OIDs, previous attempts and extra fields. The
host freshly verifies the exact prior Docker volume label/identity and absence of
the old container. It mounts this existing volume read-only at `/operator-prior`;
it never erases or rewrites it. The new operation owns a distinct work volume.

Linux checks root ownership, private directory/file modes, an actual read-only
mount and the exact canonical `source-owner-create.started.json` marker. That
marker must be the volume's only entry. A transition, provider write, transfer,
resume marker or any unexpected file refuses continuation. The marker and review
instruction identify a previous operation; they do not prove current native state.

## Fresh admission before continuation

The complete existing source/CI/image/policy, empty-target, queue/scheduler/job,
private document/TeX and source financial gates still run. The operator binds the
complete retained credential census before accepting the prepared owner. It also
freshly verifies that captured old principals/keys are still in the original
pre-isolation state and that later account, model, observer and target credential
issuance has not happened.

The captured principal set contains six exact native email/UID pairs, including
the existing `hirewiz-worker` account. Both pristine recovery and ordinary
retirement require the complete set, with no duplicates or additional actors.
The GitHub builder continuation also requires all six to remain disabled before
creating or granting the replacement builder or enabling its trigger. This
corrects the five-account allowlist that caused the reconciled F3 attempts to
stop before mutation; it does not waive any native observation.

The source connection still authenticates through the fixed numeric secret and
proves its native TLS/cluster/database/actor identity. A bounded READ ONLY
transaction verifies the old database/schema ownership and schema9, the actual
owner's exact OID/name and seven false privilege/login attributes, and its sole
creator membership `(2080768, 16387, 10, true, false, false)`. Nonmembership shared
dependencies, including ownership and ACL references in other databases, role
settings, authenticated sessions and prepared transactions must be zero.

Only then is the actual role accepted and a separate durable
`source-owner-resume.started.json` marker recorded. No SQL `CREATE`, `DROP`,
`DROP OWNED`, ownership change or credential rotation occurs in this admission.
The original CREATE marker remains preserved. All subsequent existing native
isolation, source transition, backup, transfer, migration, staging and promotion
steps retain their per-mutation once markers and fresh fence checks.

An unknown subsequent result is reconciled against native state. This path does
not authorize replaying a provider request, replacing a result with a caller
boolean, accepting a role that already owns data or restarting from a later
partial phase. A different recovery state requires a separately reviewed design.

## Verification scope

Focused controls cover changed/untyped instructions, writable or mismatched prior
volumes, live prior containers, extra phase markers, symlinks/hardlinks, changed
role attributes/OIDs/membership, dependencies/sessions and unchanged normal flow.
Actual local PostgreSQL17 controls exercise unused-role acceptance and real
ownership, ACL, default-privilege, role-setting and live-session refusals inside
READ ONLY transactions. These UUID-owned disposable tests use synthetic local
credentials and cannot select the production source database.

The recovered path still needs the new exact source commit, successful CI/native
images and production execution. Local controls are not a native cutover receipt.

The Linux operator emits fixed stages and, for exact trusted control exception
types, a single literal refusal code drawn from the immutable checkout. Provider
messages, URLs and credentials are never reflected. An unknown exception keeps
the generic reconcile-only result. These diagnostics do not change mutation
order, replay rules or completion criteria.
