# Candidate and browser component integration — 9 October 2026

The reviewed component source is integrated into the managed branch. Local verification
passes **1,953 backend tests without failures or skips**, 120 frontend unit tests,
configured Mypy on 108 source files, enlarged CI Ruff, frontend lint/types and the optimized
frontend build. Remote exact-commit CI and deployment are separate pending checkpoints.

This source adds bounded pairing HTTP/BFF/review transport, genuine password-account and
retained-session domain operations, nonreflecting credential-input validation, legacy bcrypt
compatibility, per-candidate protected publication and normal denial, and read-only monetary
observation for candidate schema `0011`. The default production constructors remain
unavailable until their actual dependencies and release gates are supplied. Pairing identity
never grants employer autofill, upload or submission permission.

The four independent component reviews cleared their stated disabled/bounded scopes.
Their exact report hashes, the 78 integrated source-file hashes, check logs and preserved
JUnit results are recorded in [the integration evidence](evidence/2026-10-09-candidate-browser-component-integration.json).
Shared main-route imports and generated API contracts were merged and regenerated; a
subsequent regeneration leaves both contract files byte-identical.

The initial combined run recorded 1,937 passes and 15 failures. An actual PostgreSQL
migration reproduced disabling existing timing/security loggers; Alembic now preserves
them, with a real migration regression. Resume upload itself passed: its account-deletion
cleanup fixture needed the endpoint's new bearer-token dependency. The positive release
fixture now freezes the nine reviewed migration filenames, preserving refusal of newer
or disconnected DAGs before any cloud request. All 78 ordered repair checks passed.

The next whole run recorded 1,952 passes and one actual native publication/seal concurrency
failure: the sealer exhausted three definite-abort attempts. The unchanged test passed in
isolation and in the final complete run. These results remain retained; they do not establish
contention availability or sustained-load performance. No unknown Commit was retried and
no safety assertion or retry bound was relaxed to obtain the passing run.

The final source scan reports zero findings. Its first finding was the literal synthetic
replacement password in a test fixture; an exact inline scan annotation was added after
full-suite completion. Parsed Python AST, including every literal, is unchanged. Exact
remote CI must verify the resulting committed source. An ignored TypeScript incremental
build artifact was excluded from integration; existing workspace output was preserved.

The serving backend/schema remain `e7d8f2b` / `20261008_0009`, and the frontend remains
`06cc112` on HireWiz's domains. No new backend migration, authority, signing key, runtime
factory, automatic employer application, test payment or production submission was enabled.
The earlier Firestore/Cloud KMS API enablement is recorded separately and is not authority
provisioning. Normal reset, protected challenge/dispatch consumption, actual website/MV3
connection, credential retirement and complete deployment remain open.
