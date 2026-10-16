# Fresh candidate native authority bootstrap — 2026-10-15

Status: **source and intercepted-SDK proof only; not provisioned or activated**. The source command is `backend/scripts/bootstrap_candidate_native_authority.py`. No credentials, GCP resources, application portal, F7/main deployment, IAM, retention policy, secrets or runtime flags were accessed or changed while implementing this increment.

This is a one-shot operator bootstrap for the existing candidate account, ingress replay, pairing and paired-device submit compositions. It does not enroll a candidate, import a SQL identity, grant a device, create a session or authorize an application. Normal users still enter through genuine public signup/explicit Google linking, protected original enrollment acknowledgement and activation, then password-approved pairing with the Chrome device's own key. There is no runtime initializer or baseline adoption path.

## Operator inputs and prerequisites

The operator must supply a private absolute JSON plan file, owned by the current uid, regular and not a symlink, not group/world writable, at most 32 KiB. The model rejects unknown keys, duplicate JSON keys, duplicate database/key names, another project, another extension and invalid origins. Required fields:

| Field | Required concrete input |
| --- | --- |
| `project` | Optional default, fixed `ai-resume-parser-482412` |
| `project_number` | Root-reviewed actual project number, checked against the existing bucket |
| `location` | Actual approved Firestore regional location and KMS location |
| `execution_database_id`, `protected_database_id`, `replay_database_id` | Three fresh distinct named Firestore databases; none may already exist |
| `journal_bucket`, `bucket_location` | Root-approved existing GCS bucket and exact uppercase provider location |
| `minimum_locked_retention_seconds` | Explicit reviewed minimum, at least 86,400 seconds |
| `kms_key_ring_id`, `assertion_key_id`, `claim_key_id` | Fresh ring and two distinct new asymmetric signing key names |
| `website_origin` | Exact approved HTTPS web origin |
| `ingress_key_id` | Exact candidate ingress transport key id |

Optional reviewed settings are `kms_protection_level` (`SOFTWARE` default, or `HSM`), `lanes` and `directory_shards` (2, 4, 8 or 16; default 8), `segment_size` (default 128), exact shipping `extension_id` (`pnjmegppacleipajnifjmbcopjjffklb` only), `executor_revision` (default `companion_0_8_0`) and `assertion_issuer` (default `hirewiz_candidate_password_v1`). The executor revision must also match the exact packaged companion and `BROWSER_ASSISTANCE_SUBMIT_EXECUTOR_REVISION`.

The existing bucket must belong to the supplied actual project number, match the reviewed location, have uniform bucket-level access and versioning enabled, and have a **locked** retention period meeting the explicit plan minimum. The command checks these settings and never creates a bucket, locks or changes retention, changes IAM, enables APIs or changes network policy. Root must separately review whether its actual resource policy meets these prerequisites; a policy mismatch stops before any provider write.

Execution requires the normal retained gcloud manager at `/Users/harshil/.config/gcloud`, SDK **578.0.0** at `/opt/homebrew/share/google-cloud-sdk/lib`, exact account `harshilyogeshbhai886@gmail.com`, configured project `ai-resume-parser-482412` and universe `googleapis.com`. The command reuses `SDKGoogleReads._http()` validation: disabled file/HTTP logging, normal OAuth token host, no impersonation, disabled auth, proxy, credential file override, token file override, custom CA or disabled TLS validation. Additional proxy/ADC/custom gRPC-root/debug trace environment overrides are refused. Set `DISABLE_GCS_PYTHON_CLIENT_OTEL_BUCKET_METADATA=true`, `GOOGLE_API_USE_MTLS_ENDPOINT=never` and `GOOGLE_API_USE_CLIENT_CERTIFICATE=false`. No call to `google.auth.default()` exists. Credentials are obtained only through `credentials.store.Load(account=EXACT_ACCOUNT, allow_account_impersonation=False)` and passed directly to the official SDKs; they are never serialized or printed.

Use the reviewed backend Python 3.12 environment with the pinned Firestore 2.34.0, KMS 3.18.0 and Storage **3.13.0** SDKs. The existing strict GCS journal guard rejects a different Storage version. The script itself does not install or upgrade dependencies or open a login.

## Invocation and irreversible-write boundary

A default invocation only validates/prints the public plan. It does not read credentials, construct provider clients or create a journal:

```sh
python backend/scripts/bootstrap_candidate_native_authority.py --plan-file /absolute/private-plan.json
```

Standalone plan validation requires no `DATABASE_URL`, `JWT_SECRET` or `APP_ENV`.
Its configuration-model imports do not load application database or security
initialization. The existing password verifier loads only during actual password
verification; production database and JWT configuration checks remain unchanged.

After root's F7/builder/coordinator gates and explicit operator resource approval, the operator supplies an already-created, empty, owned, non-symlink **0700** journal directory:

```sh
python backend/scripts/bootstrap_candidate_native_authority.py --plan-file /absolute/private-plan.json --journal-directory /absolute/private-empty-journal --execute
```

Do not run this command during source acceptance. The implementation and tests did not execute this real-provider mode.

Before **each** SDK write, the command creates a new 0600 intent using `O_EXCL`/`O_NOFOLLOW`, fsyncs the file and containing directory, then revalidates the normal manager. Each intent pins the public plan digest, bootstrap UUID, exact target and request. Each provider mutation is attempted once with retry disabled and a bounded RPC timeout. The gRPC channels use official TLS endpoints with gRPC retries and environment proxy use disabled. Storage uses a dedicated explicit-credential session with redirects, environmental proxy inheritance and automatic credential-challenge replay disabled.

The concrete write sequence is:

1. Create three distinct Firestore Native databases with PITR and delete protection enabled. Observe the original create LRO, poll its read-only operation with a bounded deadline, then require fresh exact database/type/location/PITR/delete-protection/create-time and actual canonical provider UID readback. No UID is fabricated or taken from an operator guess.
2. Create the execution baseline atomically: `control/meta`, `pairing_control/current`, provider-create-time `pairing_clock/observed`, empty `head/global`, and one exact active extension policy. Create the protected `v3_root`, empty lane heads and empty directory heads, and the third ingress database's exact control root. Every document uses `exists:false`. Require the original commit's write results/timestamps, another actual UID check, and an exact bounded initial authority-record census. No subject, session, password, OAuth, pairing intent, device, key owner, submit grant or application is seeded.
3. Create the canonical protected publication resource object, an immutable manifest containing actual pins and all baseline rows, and the exact witness body. Every upload uses `if_generation_match=0`, then verifies original upload metadata and fresh generation-bound exact bytes/hash with the existing journal reader. The resource object uses the existing publication serializer/path; no alternate runtime protocol is introduced.
4. Create one fresh KMS ring and two fresh P-256/SHA-256 `ASYMMETRIC_SIGN` keys. Require original create acknowledgements, exactly one actual fresh key version, enabled state, exact algorithm/protection, CRC-checked official public PEM and actual P-256 JWK. Private key material is never returned.

If a write, original acknowledgement, identity/readback check or local acknowledgement persistence is uncertain, the script records `UNKNOWN` where possible, emits no usable runtime result and stops. The preceding fsynced intent itself means uncertain even if a process dies before writing the unknown marker. The same attempt cannot execute again: `plan.json` and the nonempty journal refuse repetition. A successfully created resource with a lost acknowledgement is deliberately not recovered into authority.

## Result and separate activation

Only a completely acknowledged run produces private-journal `result.json` and public JSON stdout containing the exact existing models:

- `CANDIDATE_ACCOUNT_NATIVE_CONFIG`
- `CANDIDATE_INGRESS_NATIVE_CONFIG` (third replay database distinct from both action registries)
- `BROWSER_PAIRING_NATIVE_CONFIG` (actual assertion and claim KMS versions/public JWKs)
- `BROWSER_SUBMIT_NATIVE_CONFIG` (`device_enrollment:"paired"`, no static operator device grant)
- Companion `nativePairing` claim public key/executor revision and nonsecret environment inputs.

All enable flags in the output are **false**, including lifecycle, ingress, native pairing, assistance native precheck, native submit, assistance, typed controls and final submit. `runtime_activation_allowed` is always false. The script never writes deployment environment/config, changes a service, publishes a companion package or signs an application lease. Root must separately review the exact resource result, IAM, executor/package pins, source gates and deployment before activating the normal-user cohort.

The output names these required secret inputs but never supplies values: `CANDIDATE_AUTH_TRANSPORT_SECRET`, `BROWSER_PAIRING_GATEWAY_SECRET`, `BROWSER_ASSISTANCE_SIGNING_SECRET`. They must be separately generated/provisioned with the existing exact validators and isolation rules; ingress transport, pairing gateway, assistance signer, NextAuth and backend secrets must not be conflated. Public pins do not replace these secrets or workload credentials.

## Unknown/reconciliation procedure

Preserve the journal after any failure or interrupted execution. Do not delete it to retry the same target names, adopt a preexisting database/key/object, complete a missing phase manually and claim success, or turn on flags from partial metadata. Current source provides this read-only diagnostic:

```sh
python backend/scripts/bootstrap_candidate_native_authority.py --plan-file /absolute/private-plan.json --journal-directory /absolute/original-journal --reconcile
```

Reconciliation requires the exact original plan digest/body, reads current database identities and metadata for attempted object targets, and lists original journal filenames. Its explicit observation scope excludes object-content authentication, KMS recovery and original-acknowledgement reconstruction. It performs no writes, does not rewrite the journal, does not return runtime configs and cannot activate anything. A current resource/status lookup is not an original owned acknowledgement. Root must review the unknown attempt and choose a separately approved recovery/disposal plan; there is no automatic repair/resume command.

## Permission boundary

The operator's bootstrap grant needs only the concrete creation/read paths above: database create/get and operation get; authority document create/get/list via Firestore commits/query; existing bucket metadata get and object create/get for exact bootstrap/publication prefixes; KMS ring create/get, key create and version list/get/public-key get in the exact new ring. No signing, decrypt, secret read, IAM mutation, retention mutation, database delete, object overwrite/delete or per-user/device management is performed by this script. Provider IAM policy syntax and resource granularity must be reviewed against the actual chosen resource names; the tool does not grant its own permissions.

Runtime identity/IAM is a separate reviewed composition contract. Its Firestore action/protected/replay authority access and immutable GCS journal/publication access must be scoped to the actual generated resources, with only the two exact KMS key versions available for signing/public-key verification. Runtime must not have database creation, KMS key creation/rotation, bucket/IAM/retention changes, or an operator bootstrap role. Existing publication fencing and protected owner/device/session/epoch checks remain mandatory. The script does not establish that IAM has already been applied or that a runtime/receipt/settlement gate is ready.

## Local validation

`backend/tests/test_bootstrap_candidate_native_authority.py` drives the actual official Firestore Admin/Firestore, Storage and KMS SDK request serializers through intercepted in-memory transports; ADC and the normal manager are stubbed or explicitly forbidden. Cases cover the complete original-acknowledged 12-write composition, exact fresh actual UIDs and disabled public config models, durable intent before every provider write, unknowns at each write kind, a commit persisting rows before losing its acknowledgement, no re-execution/adoption, stale/malformed UID and unexpected initial census records, existing database refusal, locked-retention refusal before any write, read-only reconciliation without config/ack recovery, exact manager account/path/version/no-impersonation and environment denials, private journal file protections, default plan without credentials/SDK/file writes, and explicit actual SDK client/channel construction without ADC.

Final focused count and lint/type results are recorded by the implementation handoff. These tests do not establish actual IAM, SDK credential refresh against Google's token endpoint, GCP service availability, Firestore emulator behavior, live provisioning or production activation.

Local result at handoff: **27 passed** in 1.24 seconds (one existing Passlib/Python `crypt` deprecation warning). Ruff check and format check passed for both new Python files; focused mypy passed for the new script using `backend/pyproject.toml`. No Firestore emulator, normal-manager credential call, ADC fallback, live provider read/write, deployment or Git index operation was used.

The later standalone CLI regression runs a clean subprocess with database/JWT/app
settings absent and network, SDK credential-manager, ADC and SQL connection paths
forbidden. It verifies `PLAN_ONLY`, no database/security aggregate import and no
new local file. The combined bootstrap/password verification subset passes 96
cases; three optional disposable-PostgreSQL cases skip without their local URL.
