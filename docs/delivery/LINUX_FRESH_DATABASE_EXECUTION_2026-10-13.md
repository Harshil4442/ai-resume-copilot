# Fixed Linux inputs for fresh database release

The portable profile is an explicit `LinuxExecutionFiles` value carried by `FreshNativeBoundary` and the existing native source/fresh credential collectors. It replaces the local Homebrew CA and absolute macOS receipt paths for this selected release route, while leaving direct legacy capture and the legacy in-place release route unchanged. It does not authorize a final transfer, grant provider permissions, assert a native fence or certify deployment.

## Package inputs

The private `linux-public-inputs.tar.gz` contains exactly four regular files, with root-owned metadata and mode0444. Install them at the fixed `/opt/hirewiz-release` root in the reviewed Linux execution image; keep all parent directories root-owned and not writable by group/other. The profile allows no alternate path or environment selector, symlink, extra trust input or unobserved replacement receipt.

| Fixed path | SHA256 | Purpose |
| --- | --- | --- |
| `ca/cert.pem` | `426806ef8b5e5711c484e53522ca10e2f103c0d514a6699bdf615d45134477a1` | Exact286583-byte/190-certificate public CA bundle used by the successful current native authentications. Preserve verify-full TLS. |
| `provenance/source-control.json` | `875747de81988a2504b1d75b78813b96f274f2e338005bc1f69c7b4bb730ad0a` | The genuine retained old source provider projection; old cluster/database/actors remain separate. |
| `provenance/fresh-role-creation.json` | `44af0a94b0912c3649e4fc757e972c863dca2f03c423d9c1e9a14266c7c46c39` | Genuine fresh role creation, fixed database16395/owner16391 and roles24576/24578/24580. |
| `provenance/fresh-credentials.json` | `88cc06834b6f7d0f237020bb2fea83c2afd4c1f380e6b6cb64324ee8cabe305f` | Genuine root-operated credential setup, exact three fresh names at numeric version1, initial native role authentication and empty target. No password/DSN payload is packaged. |

The reader checks exact file size, SHA256, regular-file ownership/mode/link count and complete bounded EOF reads using O_NOFOLLOW. It compares descriptor identity/metadata before and after the read. Each collector still performs genuine numeric Secret Manager reads/rechecks, parses the fixed actor/host, registers the native binding and verifies effective libpq TLS/connection settings before SQL. The Linux branch accepts only the exact observed secret name for each fixed role, with an explicit positive numeric version. `latest`, arbitrary names, mismatched aliases and actor/host overrides remain refused. The pinned setup receipt88cc and its initial version1 references are historical preparation provenance only; they do not authorize the current selected credential. Existing genuine Describe/Access/recheck and native authentication/phase checks verify the supplied version, so a legitimate credential rotation does not require a code or image rebuild. Historical setup data does not replace current native authentication, the post-restore schema check, phase checks or the writer/provider fence.

## Integration

A reviewed Linux operator imports the ordinary modules with the verified checkout's `backend` and `infra/gcp` paths and constructs `FreshNativeBoundary(verified_root, linux_execution_files=LinuxExecutionFiles())`, or the genuine fresh-only fenced subclass when that separately reviewed implementation is available. The original inherited `verify_fences` remains refusing until authentic native fencing exists. No accepting adapter or CLI bypass is supplied here.

The retained source backup continues to use its source-native collector and unchanged schema-nine snapshot/verification logic. Its actual dump environment now carries the selected Linux CA. The final transfer obtains the CA from each genuinely registered source/target dump environment, rather than a local Homebrew constant. The exact database/actor/cluster checks, fresh empty database/public-owner check, tools SHA256 checks, real owned-transfer deadline/group cleanup, once marker and mandatory NativeCutBoundary are unchanged. No destination marker is required before restore.

## Exact source, tool and image custody still required

- Keep the complete reviewed application checkout at its exact release SHA and existing image/CI/build provenance. Package/input hashes do not establish application source or image provenance. No protected application source is copied or read by this preparation.
- Supply actual SHA256-pinned native PostgreSQL17 `pg_dump` and `pg_restore` binaries through the existing TransferPlan/PostgresTools checks. The currently verified CI service reference is `mirror.gcr.io/library/postgres:17-alpine@sha256:b0f9560a2de083e2cc7382e75f808c7381a32852a7ec49117deedb300e552b24`. A reference or image digest alone is not a binary hash, nor does copying its musl-linked clients into a different Linux distribution establish executability. No client hash or new operator image digest is invented here.
- The selected Linux execution image/environment must genuinely attest its immutable digest, exact source, native client bytes/architecture/dependencies and installed four input hashes before use. This preparation builds no image and invokes no Docker/provider/native client.
- Preserve the existing normal native cloud credential owner, approved scoped access and output/deadline/privacy controls. No credential manager contents, application credentials, customer dump or password values belong in the package.

## Verification and next sequence

The local149-case isolated run includes new synthetic filesystem/collector controls and unchanged source/fresh release checks; it passes with configured Ruff5 and Linux mypy3. Synthetic Linux metadata in unit tests is explicitly not a real Linux runtime, PostgreSQL, cloud or transfer receipt.

Use disposable owned databases only for the next genuine Linux rehearsal and run all eleven existing real PostgreSQL transfer cases without skips. Never use the fresh reserved production destination for rehearsal. Keep the genuine final fresh transfer separate until the native fence, old writer retirement, protected backup, exact numeric credentials/scoped authority, tool/image proof and critical operator approval are complete. Finalization, GCP traffic promotion, Vercel deployment and opening admission remain independently observed later steps. No production or goal completion is implied by this profile.
